#!/usr/bin/env python3
"""wave — drive an epic of GitHub issues through worktrees, PRs and merges: any repo, any account, any stack.

State lives in GitHub (sub-issues, `blocked_by`, PRs) and in git (worktrees,
branches), never in a chat. This script reads that state and performs the
deterministic steps; judgement (dispatching agents, asking the owner before a
merge) stays with the Claude session running `/wave`.

Multi-repo: an epic's sub-issues and blockers may live in other repositories;
every node carries its own repo. Multi-account: each repo may name the `gh`
account that owns it; calls for that repo run with that account's token
(`gh auth token --user`), the global `gh` state is never switched.

Registry: ~/.config/setwave/repos.json
    {"search_paths": ["~/projects"],
     "repos": {"owner/name": {"path": "/abs/checkout", "account": "ghuser",
                              "base": "main", "gate": [...], "protected": [...], "serial": ["src/store/mod.rs"],
                              "worktree_prefix": "../name-"}}}
A repo not in the registry is found by scanning search_paths for a checkout
whose origin matches, then cached. A `.wave.json` at a repo root supplies the
same per-repo keys (gate, protected, base, worktree_prefix).

Refs: issues and PRs are `owner/name#N`; a bare `#N`/`N` means the repo of the
current directory. Stdlib only. Needs `gh` (logged in), `git` >= 2.38, optional `codegraph`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = (HERE.parent / "templates") if (HERE.parent / "templates").exists() else HERE / "templates"
CONFIG_DIR = Path(os.environ.get("SETWAVE_CONFIG", Path.home() / ".config" / "setwave"))
REGISTRY = CONFIG_DIR / "repos.json"

ATTRIBUTION = re.compile(r"co-authored-by:\s*claude|generated with \[?claude code|noreply@anthropic\.com", re.I)
CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)(?::\s*|\s+)(?:([\w.-]+/[\w.-]+))?#(\d+)\b", re.I)
BRANCH_IN_BODY = re.compile(r"git worktree add -b\s+(\S+)")
CODEGRAPH_IN_BODY = re.compile(r'codegraph explore "([^"]+)"')
REF = re.compile(r"^(?:(?P<slug>[\w.-]+/[\w.-]+))?#?(?P<n>\d+)$")
ORIGIN = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")


# ---------------------------------------------------------------- plumbing

def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    return p.returncode, p.stdout, p.stderr


def sh(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    rc, out, err = run(cmd, cwd, env)
    if rc != 0:
        raise SystemExit(f"$ {' '.join(cmd)}\n{out}{err}")
    return out


def sh_ok(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> tuple[bool, str]:
    rc, out, err = run(cmd, cwd, env)
    return rc == 0, out + err


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug_of_url(url: str) -> str | None:
    m = ORIGIN.search(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def slug_of_api(url: str) -> str:
    return "/".join(url.rstrip("/").split("/")[-2:])


# ---------------------------------------------------------------- registry

def load_registry() -> dict:
    if REGISTRY.exists():
        reg = json.loads(REGISTRY.read_text())
    else:
        reg = {}
    reg.setdefault("search_paths", ["~/projects"])
    reg.setdefault("repos", {})
    return reg


def save_registry(reg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")


def scan_for_slug(reg: dict, slug: str) -> Path | None:
    for base in reg["search_paths"]:
        root = Path(base).expanduser()
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if not (d / ".git").exists():
                continue
            ok, url = sh_ok(["git", "remote", "get-url", "origin"], cwd=d)
            if ok and slug_of_url(url) == slug and (d / ".git").is_dir():
                return d.resolve()
    return None


# ---------------------------------------------------------------- repo

class Repo:
    _cache: dict[str, "Repo"] = {}
    _tokens: dict[str, str] = {}

    @classmethod
    def get(cls, slug: str) -> "Repo":
        if slug not in cls._cache:
            reg = load_registry()
            entry = reg["repos"].get(slug, {})
            if "path" not in entry or not Path(entry["path"]).exists():
                found = scan_for_slug(reg, slug)
                if not found:
                    raise SystemExit(f"no local checkout for {slug}: add it with `wave.py repos add <path> --slug {slug}` or extend search_paths in {REGISTRY}")
                entry["path"] = str(found)
                reg["repos"][slug] = entry
                save_registry(reg)
            cls._cache[slug] = cls(slug, entry)
        return cls._cache[slug]

    @classmethod
    def from_cwd(cls, path: str | None = None) -> "Repo":
        start = Path(path or os.getcwd()).resolve()
        ok, top = sh_ok(["git", "rev-parse", "--show-toplevel"], cwd=start)
        if not ok:
            raise SystemExit("not inside a git repo; pass --repo <path> or use owner/name#N refs")
        root = Path(top.strip())
        slug = slug_of_url(sh(["git", "remote", "get-url", "origin"], cwd=root))
        if not slug:
            raise SystemExit("cannot parse origin url")
        reg = load_registry()
        entry = reg["repos"].setdefault(slug, {})
        if entry.get("path") != str(root):
            entry["path"] = str(root)
            save_registry(reg)
        cls._cache[slug] = cls(slug, entry)
        return cls._cache[slug]

    def __init__(self, slug: str, entry: dict):
        self.slug = slug
        self.owner, self.name = slug.split("/")
        self.root = Path(entry["path"])
        local = self.root / ".wave.json"
        self.cfg = {**(json.loads(local.read_text()) if local.exists() else {}), **{k: v for k, v in entry.items() if k != "path"}}
        self.account = self.cfg.get("account")
        self.env = self._env()
        self.base = self.cfg.get("base") or json.loads(self.gh(["repo", "view", slug, "--json", "defaultBranchRef"]))["defaultBranchRef"]["name"]
        self.protected: list[str] = list(self.cfg.get("protected", []))
        self.handoffs = self.root.parent / f"{self.root.name}-handoffs"
        self._prs: list[dict] | None = None

    def _env(self) -> dict:
        env = dict(os.environ)
        if self.account:
            if self.account not in Repo._tokens:
                ok, tok = sh_ok(["gh", "auth", "token", "--user", self.account])
                if not ok:
                    raise SystemExit(f"{self.slug}: account {self.account} is not logged in to gh (`gh auth login`)\n{tok}")
                Repo._tokens[self.account] = tok.strip()
            env["GH_TOKEN"] = Repo._tokens[self.account]
        return env

    # -- gh / git for this repo
    def gh(self, args: list[str]) -> str:
        return sh(["gh"] + args, env=self.env)

    def gh_ok(self, args: list[str]) -> tuple[bool, str]:
        return sh_ok(["gh"] + args, env=self.env)

    def api(self, path: str, method: str = "GET", fields: dict | None = None, paginate: bool = False):
        cmd = ["api", "-X", method, path]
        for k, v in (fields or {}).items():
            cmd += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
        if paginate:
            ok, out = self.gh_ok(cmd + ["--paginate", "--slurp"])
            if ok:
                pages = json.loads(out) if out.strip() else []
                return [x for page in pages for x in (page if isinstance(page, list) else [page])]
            out = self.gh(cmd + ["--paginate"])
            items = []
            for chunk in re.findall(r"\[.*?\](?=\[|\s*$)", out, re.S):
                items += json.loads(chunk)
            return items
        out = self.gh(cmd)
        return json.loads(out) if out.strip() else None

    def git(self, args: list[str], cwd: Path | None = None) -> str:
        return sh(["git"] + args, cwd=cwd or self.root, env=self.env)

    def git_ok(self, args: list[str], cwd: Path | None = None) -> tuple[bool, str]:
        return sh_ok(["git"] + args, cwd=cwd or self.root, env=self.env)

    # -- facts
    def worktree(self, n: int) -> Path:
        prefix = self.cfg.get("worktree_prefix")
        if prefix:
            p = Path(prefix).expanduser()
            p = p if p.is_absolute() else (self.root / p)
            return p.parent.resolve() / f"{p.name}{n}"
        return self.root.parent / f"{self.root.name}-{n}"

    def gate(self) -> list[str]:
        if self.cfg.get("gate"):
            return list(self.cfg["gate"])
        cmds: list[str] = []
        wf_dir = self.root / ".github" / "workflows"
        for wf in sorted(wf_dir.glob("*.y*ml")) if wf_dir.exists() else []:
            for line in wf.read_text().splitlines():
                m = re.match(r"\s*(?:-\s*)?run:\s*(.+?)\s*$", line)
                if m and re.match(r"(cargo|npm|pnpm|yarn|bun|pytest|python -m|uv run|go |make|mix|gradle|\./gradlew|swift|xcodebuild|dotnet|flutter|dart)", m.group(1)):
                    if m.group(1) not in cmds:
                        cmds.append(m.group(1))
        if cmds:
            return cmds
        r = self.root
        if (r / "Cargo.toml").exists():
            return ["cargo fmt --check", "cargo clippy --all-targets -- -D warnings", "cargo test"]
        if (r / "package.json").exists():
            scripts = json.loads((r / "package.json").read_text()).get("scripts", {})
            pm = "pnpm" if (r / "pnpm-lock.yaml").exists() else "yarn" if (r / "yarn.lock").exists() else "bun" if (r / "bun.lockb").exists() else "npm"
            picked = [f"{pm} run {s}" for s in ("lint", "typecheck", "test", "build") if s in scripts]
            return picked or [f"{pm} test"]
        if (r / "pyproject.toml").exists():
            return ["ruff check .", "pytest"]
        if (r / "go.mod").exists():
            return ["go vet ./...", "go test ./..."]
        if (r / "Package.swift").exists():
            return ["swift build", "swift test"]
        return ["<no gate found: read the repo, decide, and write it to .wave.json>"]

    def has_codegraph(self) -> bool:
        return (self.root / ".codegraph").exists()

    def fetch(self) -> None:
        self.git(["fetch", "-q", "--prune", "origin"])

    def origin_sha(self) -> str:
        return self.git(["rev-parse", "--short", f"origin/{self.base}"]).strip()

    # -- github reads
    def issue(self, n: int) -> dict:
        return self.api(f"repos/{self.slug}/issues/{n}")

    def sub_issues(self, n: int) -> list[dict]:
        return self.api(f"repos/{self.slug}/issues/{n}/sub_issues?per_page=100", paginate=True) or []

    def blocked_by(self, n: int) -> list[dict]:
        ok, _ = self.gh_ok(["api", f"repos/{self.slug}/issues/{n}/dependencies/blocked_by"])
        if not ok:
            return []
        return self.api(f"repos/{self.slug}/issues/{n}/dependencies/blocked_by?per_page=100", paginate=True) or []

    def open_prs(self) -> list[dict]:
        if self._prs is None:
            self._prs = json.loads(self.gh(["pr", "list", "-R", self.slug, "--state", "open", "--limit", "200",
                                            "--json", "number,title,headRefName,baseRefName,body,mergeable,url"]))
        return self._prs

    def merged_prs(self, search: str) -> list[dict]:
        return json.loads(self.gh(["pr", "list", "-R", self.slug, "--state", "merged", "--limit", "100", "--search", search,
                                   "--json", "number,body,headRefName"]))

    def pr_checks(self, n: int) -> dict[str, str]:
        """name -> bucket: pass | fail | pending | skipping | cancel (gh's own normalisation)."""
        ok, out = self.gh_ok(["pr", "checks", str(n), "-R", self.slug, "--json", "name,bucket"])
        if ok and out.strip():
            return {c["name"]: c["bucket"] for c in json.loads(out)}
        _, out = self.gh_ok(["pr", "checks", str(n), "-R", self.slug])
        res = {}
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                res[parts[0]] = parts[1]
        return res


# ---------------------------------------------------------------- refs & tree

def parse_ref(text: str, default: Repo | None) -> tuple[Repo, int]:
    m = REF.match(text.strip())
    if not m:
        raise SystemExit(f"bad ref {text!r}: use owner/name#N or N")
    if m.group("slug"):
        return Repo.get(m.group("slug")), int(m.group("n"))
    if default is None:
        raise SystemExit(f"ref {text!r} needs a repo: run inside one or use owner/name#N")
    return default, int(m.group("n"))


def key(repo: Repo, n: int) -> str:
    return f"{repo.slug}#{n}"


def pr_for_issue(repo: Repo, n: int) -> dict | None:
    for pr in repo.open_prs():
        for slug, num in CLOSES.findall(pr.get("body") or ""):
            if int(num) == n and (not slug or slug == repo.slug):
                return pr
        if re.search(rf"/{n}-", pr["headRefName"]):
            return pr
    return None


def merged_pr_for_issue(repo: Repo, n: int) -> dict | None:
    for pr in repo.merged_prs(f"#{n}"):
        for slug, num in CLOSES.findall(pr.get("body") or ""):
            if int(num) == n and (not slug or slug == repo.slug):
                return pr
        if re.search(rf"/{n}-", pr["headRefName"]):
            return pr
    return None


def tree(epic_repo: Repo, root: int) -> dict[str, dict]:
    """Every issue under the epic, keyed by 'slug#n', across repositories. One API round per level, in parallel."""
    nodes: dict[str, dict] = {}
    level: list[tuple[Repo, int, str | None]] = [(epic_repo, root, None)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        while level:
            fresh = [(r, n, parent) for r, n, parent in level if key(r, n) not in nodes]
            issues = list(pool.map(lambda t: t[0].issue(t[1]), fresh))
            subs = list(pool.map(lambda t: t[0].sub_issues(t[1]), fresh))
            nxt = []
            for (r, n, parent), iss, children in zip(fresh, issues, subs):
                k = key(r, n)
                node = {"key": k, "repo": r.slug, "number": n, "issue": iss, "children": [], "parent": parent}
                nodes[k] = node
                for child in children:
                    crepo = Repo.get(slug_of_api(child["repository_url"]))
                    node["children"].append(key(crepo, child["number"]))
                    nxt.append((crepo, child["number"], k))
            level = nxt
    return nodes


def leaves(nodes: dict[str, dict]) -> list[dict]:
    return [nd for nd in nodes.values() if not nd["children"] and nd["parent"] is not None]


def branch_for(iss: dict) -> str:
    m = BRANCH_IN_BODY.search(iss.get("body") or "")
    if m:
        return m.group(1)
    kind = "fix" if any(l["name"] == "bug" for l in iss.get("labels", [])) else "feat"
    slug = re.sub(r"[^a-z0-9]+", "-", iss["title"].lower()).strip("-")[:40].rstrip("-")
    return f"{kind}/{iss['number']}-{slug}"


def codegraph_query(iss: dict) -> str:
    m = CODEGRAPH_IN_BODY.search(iss.get("body") or "")
    return m.group(1) if m else " ".join(re.findall(r"[A-Za-z_]{4,}", iss["title"])[:6])


def candidates(nodes: dict[str, dict]) -> list[dict]:
    out = []
    open_leaves = [nd for nd in sorted(leaves(nodes), key=lambda d: (d["repo"], d["number"])) if nd["issue"]["state"] == "open"]
    for nd in open_leaves:
        Repo.get(nd["repo"]).open_prs()  # warm the per-repo PR cache once, serially
    with ThreadPoolExecutor(max_workers=12) as pool:
        blocked = list(pool.map(lambda nd: Repo.get(nd["repo"]).blocked_by(nd["number"]), open_leaves))
    for nd, bl in zip(open_leaves, blocked):
        iss = nd["issue"]
        repo = Repo.get(nd["repo"])
        n = nd["number"]
        blockers = [f"{slug_of_api(b['repository_url'])}#{b['number']}" for b in bl if b["state"] == "open"]
        pr = pr_for_issue(repo, n)
        wt = repo.worktree(n)
        state = "blocked" if blockers else "in-progress" if pr else "worktree" if wt.exists() else "ready"
        out.append({"key": nd["key"], "repo": repo.slug, "number": n, "title": iss["title"], "branch": branch_for(iss),
                    "blocked_by": blockers, "pr": pr["number"] if pr else None,
                    "worktree": str(wt) if wt.exists() else None, "state": state,
                    "ready": state == "ready"})
    # every open leaf is in exactly one state: `state` is a single value, so the states cannot overlap;
    # what a later edit could break is READY, so READY must mean "nothing else holds", and nothing less
    assert all(c["ready"] == (not c["blocked_by"] and not c["pr"] and not c["worktree"]) for c in out), \
        [c["key"] for c in out if c["ready"] != (not c["blocked_by"] and not c["pr"] and not c["worktree"])]
    return out


def premises_for(repo: Repo, n: int) -> list[tuple[str, bool, str]]:
    """The syllogism behind READY: each premise with its evidence. Conclusion = all hold."""
    iss = repo.issue(n)
    subs = repo.sub_issues(n)
    bl = repo.blocked_by(n)
    open_bl = [b for b in bl if b["state"] == "open"]
    pr = pr_for_issue(repo, n)
    wt = repo.worktree(n)
    return [
        ("it is a leaf (no sub-issues)", not subs, f"{len(subs)} sub-issues" if subs else "none"),
        ("it is open", iss["state"] == "open", iss["state"]),
        ("every blocker is closed", not open_bl, ", ".join(f"{slug_of_api(b['repository_url'])}#{b['number']} {b['state']}" for b in bl) or "no blockers"),
        ("no open PR closes it", pr is None, f"PR #{pr['number']} ({pr['headRefName']})" if pr else "none"),
        ("no worktree exists for it", not wt.exists(), str(wt)),
    ]


def why(c: dict) -> str:
    if c["ready"]:
        return "READY"
    if c["blocked_by"]:
        return "blocked by " + ", ".join(c["blocked_by"])
    if c["pr"]:
        return f"in progress (PR #{c['pr']})"
    return "worktree exists"


# ---------------------------------------------------------------- prompt

def render_prompt(repo: Repo, iss: dict, branch: str, sha: str, parallel: int) -> str:
    tpl = (TEMPLATES / "agent.md").read_text()
    n = iss["number"]
    fields = {
        "N": str(n), "TITLE": iss["title"], "URL": iss["html_url"], "REPO": repo.slug,
        "ROOT": str(repo.root), "WORKTREE": str(repo.worktree(n)), "BRANCH": branch,
        "BASE": repo.base, "SHA": sha, "DATE": datetime.now().strftime("%Y-%m-%d"),
        "GATE": "\n".join(repo.gate()), "GATE_ONE_LINE": " && ".join(repo.gate()),
        "PROTECTED": ", ".join(f"`{p}`" for p in repo.protected) or "(nenhum declarado em .wave.json / registro)",
        "CODEGRAPH_QUERY": codegraph_query(iss),
        "CODEGRAPH_NOTE": "o repo tem `.codegraph/`; use `codegraph explore \"...\"` antes de grep/Read" if repo.has_codegraph()
                          else "sem índice CodeGraph aqui; se `codegraph` existir no PATH, rode `codegraph init .` na worktree primeiro",
        "ACCOUNT": f"conta gh `{repo.account}` (use `GH_TOKEN=$(gh auth token --user {repo.account})` nos comandos gh)" if repo.account else "conta gh ativa",
        "PARALLEL": str(max(parallel - 1, 0)),
    }
    for k, v in fields.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


# ---------------------------------------------------------------- commands

def cmd_repos(default: Repo | None, a):
    reg = load_registry()
    if a.action == "list":
        print(json.dumps(reg, indent=2))
        return
    if a.action == "add":
        root = Path(a.path).expanduser().resolve()
        slug = a.slug or slug_of_url(sh(["git", "remote", "get-url", "origin"], cwd=root))
        entry = reg["repos"].setdefault(slug, {})
        entry["path"] = str(root)
        for k in ("account", "base", "worktree_prefix"):
            if getattr(a, k):
                entry[k] = getattr(a, k)
        if a.gate:
            entry["gate"] = a.gate
        if a.protected:
            entry["protected"] = a.protected
        save_registry(reg)
        print(f"registered {slug} -> {root}" + (f" (account {a.account})" if a.account else ""))
        return
    if a.action == "scan":
        added = 0
        for base in reg["search_paths"]:
            for d in sorted(Path(base).expanduser().glob("*")):
                if (d / ".git").is_dir():
                    ok, url = sh_ok(["git", "remote", "get-url", "origin"], cwd=d)
                    slug = slug_of_url(url) if ok else None
                    if slug and slug not in reg["repos"]:
                        reg["repos"][slug] = {"path": str(d.resolve())}
                        added += 1
        save_registry(reg)
        print(f"scanned {reg['search_paths']}: {added} new, {len(reg['repos'])} total")


def cmd_facts(default: Repo | None, a):
    repos = [Repo.get(s) for s in a.slugs] if a.slugs else [default] if default else []
    for r in repos:
        print(json.dumps({"slug": r.slug, "root": str(r.root), "account": r.account or "(active)", "base": r.base,
                          "gate": r.gate(), "protected": r.protected, "codegraph": r.has_codegraph(),
                          "worktree_example": str(r.worktree(1)), "handoffs": str(r.handoffs)}, indent=2))


def cmd_next(default: Repo | None, a):
    repo, epic = parse_ref(a.epic, default)
    nodes = tree(repo, epic)
    cands = candidates(nodes)
    ready = [c for c in cands if c["ready"]][: a.batch]
    if a.json:
        print(json.dumps({"ready": ready, "all": cands}, indent=2))
        return
    print(f"epic {key(repo, epic)} — {len(leaves(nodes))} leaf issues across {len({nd['repo'] for nd in nodes.values()})} repo(s), {len(cands)} open")
    for c in cands:
        print(f"  {c['key']:<40} {why(c):<36} {c['title'][:60]}")
    print(f"\nnext wave (batch {a.batch}): " + (" ".join(c["key"] for c in ready) if ready else "nothing ready"))


def cmd_dispatch(default: Repo | None, a):
    targets = [parse_ref(r, default) for r in a.issues]
    if not a.dry_run and not a.force:
        class _D: batch = len(targets)
        try:
            cmd_doctor(default, _D())
        except SystemExit:
            raise SystemExit("dispatch refused: doctor found a hard failure (pass --force only if you have read it and disagree)")
        for repo, n in targets:
            bad = [t for t, ok, _ in premises_for(repo, n) if not ok]
            if bad:
                raise SystemExit(f"dispatch refused: {key(repo, n)} is not ready — {'; '.join(bad)}. `wave why {key(repo, n)}`")
    for repo in {r for r, _ in targets}:
        repo.fetch()
        repo.handoffs.mkdir(exist_ok=True)
    for repo, n in targets:
        iss = repo.issue(n)
        branch = branch_for(iss)
        wt = repo.worktree(n)
        sha = repo.origin_sha()
        if not a.dry_run:
            if wt.exists():
                print(f"{key(repo, n)}: worktree exists at {wt}, keeping it")
            else:
                ok, out = repo.git_ok(["worktree", "add", "-q", "-b", branch, str(wt), f"origin/{repo.base}"])
                if not ok:
                    print(f"{key(repo, n)}: worktree add failed:\n{out}")
                    continue
                if sh_ok(["which", "codegraph"])[0]:
                    sh_ok(["codegraph", "init", "."], cwd=wt)
        path = repo.handoffs / f"{n}.md"
        path.write_text(render_prompt(repo, iss, branch, sha, len(targets)))
        print(f"{key(repo, n)}: {branch} -> {wt}\n    prompt: {path}   (base origin/{repo.base} = {sha})")
    print("\nDispatch one Agent (general-purpose) per prompt file, all in ONE message; prompt = file content verbatim.")


def cmd_prompt(default: Repo | None, a):
    repo, n = parse_ref(a.issue, default)
    repo.fetch()
    iss = repo.issue(n)
    print(render_prompt(repo, iss, branch_for(iss), repo.origin_sha(), a.parallel))


def epic_prs(default: Repo | None, epic_ref: str) -> list[tuple[Repo, dict]]:
    repo, epic = parse_ref(epic_ref, default)
    nodes = tree(repo, epic)
    out = []
    for nd in leaves(nodes):
        r = Repo.get(nd["repo"])
        pr = pr_for_issue(r, nd["number"])
        if pr:
            out.append((r, pr))
    return out


def select_prs(default: Repo | None, refs: list[str], epic: str | None) -> list[tuple[Repo, dict]]:
    """Explicit PR refs win; --epic alone selects every open PR that closes one of the epic's leaves."""
    if epic and not refs:
        return epic_prs(default, epic)
    if refs:
        out = []
        for ref in refs:
            repo, n = parse_ref(ref, default)
            pr = next((p for p in repo.open_prs() if p["number"] == n), None)
            if pr:
                out.append((repo, pr))
            else:
                print(f"{key(repo, n)}: no open PR with that number")
        return out
    if default is None:
        raise SystemExit("give PR refs or --epic")
    return [(default, p) for p in default.open_prs()]


def verify_one(repo: Repo, pr: dict, epic_nodes: dict | None = None) -> dict:
    head = pr["headRefName"]
    repo.git(["fetch", "-q", "origin", head])
    body = pr.get("body") or ""
    commits = repo.git(["log", "--format=%B", f"origin/{repo.base}..origin/{head}"])
    attribution = bool(ATTRIBUTION.search(body + commits))
    touched = []
    if repo.protected:
        diff = repo.git(["diff", "--name-only", f"origin/{repo.base}...origin/{head}", "--"] + repo.protected)
        touched = [l for l in diff.splitlines() if l.strip()]
    n_issue = next((int(num) for _, num in CLOSES.findall(body)), None)
    checks = repo.pr_checks(pr["number"])
    bad = {k: v for k, v in checks.items() if v.lower() not in ("pass", "success", "skipping", "skipped", "neutral")}
    # sibling issues that cite a file this PR touched: the next wave inherits this change
    touched_files = repo.git(["diff", "--name-only", f"origin/{repo.base}...origin/{head}"]).split()
    siblings = []
    for nd in (epic_nodes or {}).values():
        iss = nd["issue"]
        if nd["children"] or iss["state"] != "open" or (n_issue and nd["number"] == n_issue and nd["repo"] == repo.slug):
            continue
        cited = [f for f in touched_files if f in (iss.get("body") or "")]
        if cited:
            siblings.append({"issue": nd["key"], "files": cited})
    contradictions = []
    m_branch = re.search(r"/(\d+)-", head)
    if n_issue and m_branch and int(m_branch.group(1)) != n_issue:
        contradictions.append(f"branch says #{m_branch.group(1)}, body closes #{n_issue}")
    if n_issue:
        if repo.sub_issues(n_issue):
            contradictions.append(f"#{n_issue} is a parent, not a leaf: a PR must close a leaf")
        open_bl = [b for b in repo.blocked_by(n_issue) if b["state"] == "open"]
        if open_bl:
            contradictions.append(f"#{n_issue} still blocked by " + ", ".join(f"#{b['number']}" for b in open_bl) + ": merging it would break the dependency order")
    wt = repo.worktree(n_issue) if n_issue else None
    wt_state = None
    if wt and wt.exists():
        dirty = bool(repo.git(["status", "--porcelain"], cwd=wt).strip())
        ok, unpushed = repo.git_ok(["log", "--oneline", "@{u}.."], cwd=wt)
        wt_state = {"dirty": dirty, "unpushed": bool(unpushed.strip()) if ok else None}
    ok = (not attribution and not touched and not bad and pr.get("mergeable") != "CONFLICTING" and not contradictions
          and not (wt_state and (wt_state["dirty"] or wt_state["unpushed"])))
    return {"repo": repo.slug, "pr": pr["number"], "issue": n_issue, "head": head, "attribution": attribution,
            "protected_touched": touched, "checks_not_green": bad, "mergeable": pr.get("mergeable"),
            "worktree": wt_state, "notify_issues": siblings, "contradictions": contradictions, "ok": ok}


def cmd_verify(default: Repo | None, a):
    nodes = None
    if a.epic:
        er, en = parse_ref(a.epic, default)
        nodes = tree(er, en)
    results = [verify_one(r, p, nodes) for r, p in select_prs(default, a.prs, a.epic)]
    if not results:
        print("nothing to verify: no open PR matched"); sys.exit(1)
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            flags = []
            if r["attribution"]: flags.append("AI-ATTRIBUTION")
            if r["protected_touched"]: flags.append("PROTECTED:" + ",".join(r["protected_touched"]))
            if r["checks_not_green"]: flags.append("CI:" + ",".join(f"{k}={v}" for k, v in r["checks_not_green"].items()))
            if r["mergeable"] == "CONFLICTING": flags.append("CONFLICT")
            if r["worktree"] and (r["worktree"]["dirty"] or r["worktree"]["unpushed"]): flags.append("WORKTREE-NOT-CLEAN")
            for c in r["contradictions"]: flags.append("CONTRADICTION[" + c + "]")
            print(f"{r['repo']}#{r['pr']:<5} issue #{r['issue'] or '?':<5} {'OK    ' if r['ok'] else 'NOT OK'} {' '.join(flags)}")
            for sib in r["notify_issues"]:
                print(f"        notify {sib['issue']}: cites {', '.join(sib['files'])} — comment there what this PR changed")
    sys.exit(0 if results and all(r["ok"] for r in results) else 1)


def merge_tree_conflicts(repo: Repo, a_ref: str, b_ref: str) -> list[str]:
    rc, out, err = run(["git", "merge-tree", "--write-tree", a_ref, b_ref], cwd=repo.root, env=repo.env)
    if rc == 0:
        return []
    return sorted(set(re.findall(r"Merge conflict in (.+)", out + err))) or ["<conflict>"]


def cmd_order(default: Repo | None, a):
    selected = select_prs(default, a.prs, a.epic)
    by_repo: dict[str, list[dict]] = {}
    for r, p in selected:
        by_repo.setdefault(r.slug, []).append(p)
    result = {}
    for slug, prs in by_repo.items():
        repo = Repo.get(slug)
        repo.fetch()
        heads = {p["number"]: f"origin/{p['headRefName']}" for p in prs}
        base = f"origin/{repo.base}"
        against_base = {n: merge_tree_conflicts(repo, base, h) for n, h in heads.items()}
        pairs = {}
        nums = sorted(heads)
        for i, x in enumerate(nums):
            for y in nums[i + 1:]:
                c = merge_tree_conflicts(repo, heads[x], heads[y])
                if c:
                    pairs[(x, y)] = c
        degree = {n: sum(1 for k in pairs if n in k) for n in nums}
        order = sorted(nums, key=lambda n: (degree[n], n))
        # serial paths (a migrations list, a generated index): a textual merge cannot see an index
        # collision, so PRs touching one land one after the other, in their issues' blocked_by order
        serial_paths = list(repo.cfg.get("serial", []))
        serial_prs = []
        if serial_paths:
            for n in nums:
                files = repo.git(["diff", "--name-only", f"{base}...{heads[n]}"]).split()
                if any(f.startswith(sp) for f in files for sp in serial_paths):
                    serial_prs.append(n)
        # chain simulation: merge in the suggested order and report where the accumulated tree conflicts
        chain = []
        acc = repo.git(["rev-parse", base]).strip()
        for n in order:
            rc, out, err = run(["git", "merge-tree", "--write-tree", acc, heads[n]], cwd=repo.root, env=repo.env)
            if rc == 0:
                acc = repo.git(["commit-tree", out.strip(), "-p", acc, "-p", repo.git(["rev-parse", heads[n]]).strip(), "-m", f"sim {n}"]).strip()
                chain.append((n, []))
            else:
                chain.append((n, sorted(set(re.findall(r"Merge conflict in (.+)", out + err))) or ["<conflict>"]))
        result[slug] = {"order": order, "pairs": {f"{x}x{y}": v for (x, y), v in pairs.items()}, "against_base": against_base,
                        "serial": serial_prs, "chain": chain,
                        "base_sha": repo.git(["rev-parse", base]).strip(),
                        "heads": {n: repo.git(["rev-parse", heads[n]]).strip() for n in nums}}
        if not a.json:
            print(f"== {slug} (base {base}) ==")
            for n in nums:
                if against_base[n]:
                    print(f"  #{n} conflicts with {base} NOW: {', '.join(against_base[n])}")
            for (x, y), files in pairs.items():
                print(f"  #{x} x #{y}: {', '.join(files)}")
            if not pairs:
                print("  no pairwise conflicts")
            if serial_prs:
                print(f"  SERIAL (touch {', '.join(serial_paths)}): {' '.join(f'#{n}' for n in serial_prs)} — one after the other, in their issues' blocked_by order, never in one batch")
            print("  order: " + " -> ".join(f"{slug}#{n}" for n in order))
            print("  chain: " + "  ".join(f"#{n}✓" if not c else f"#{n}✗[{','.join(c)}]" for n, c in chain) + "   (✗ = merge the base into that branch right before merging it)")
    if a.plan:
        Path(a.plan).write_text(json.dumps({"made_at": now_iso(), "repos": result}, indent=2) + "\n")
        print(f"\nplan written to {a.plan} — show it, get the OK, then `merge --plan {a.plan} --yes`")
    if a.json:
        print(json.dumps(result, indent=2))
    elif not a.plan:
        print("\n(textual only — the base branch's CI after each merge is the truth for semantic conflicts)")


def wait_for(fn, timeout: int, every: int = 10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = fn()
        if r is not None:
            return r
        time.sleep(every)
    return None


def cmd_merge(default: Repo | None, a):
    targets = [parse_ref(r, default) for r in a.prs]
    if a.plan:
        plan = json.loads(Path(a.plan).read_text())
        planned = []
        for slug, pl in plan["repos"].items():
            repo = Repo.get(slug)
            repo.fetch()
            now_base = repo.git(["rev-parse", f"origin/{repo.base}"]).strip()
            if now_base != pl["base_sha"]:
                raise SystemExit(f"{slug}: origin/{repo.base} moved since the plan ({pl['base_sha'][:7]} -> {now_base[:7]}). The OK was for that delta: re-run `order --plan` and ask again.")
            for n in pl["order"]:
                pr = next((x for x in repo.open_prs() if x["number"] == n), None)
                if not pr:
                    raise SystemExit(f"{slug}#{n}: no longer an open PR; re-run `order --plan`.")
                head_now = repo.git(["rev-parse", f"origin/{pr['headRefName']}"]).strip()
                if head_now != pl["heads"][str(n)]:
                    raise SystemExit(f"{slug}#{n}: its branch moved since the plan ({pl['heads'][str(n)][:7]} -> {head_now[:7]}). Re-run `order --plan` and ask again.")
                planned.append((repo, n))
        targets = planned or targets
    if not a.force:
        for repo, n in targets:
            pr = next((x for x in repo.open_prs() if x["number"] == n), None)
            n_issue = next((int(num) for _, num in CLOSES.findall((pr or {}).get("body") or "")), None)
            if n_issue:
                open_bl = [b for b in repo.blocked_by(n_issue) if b["state"] == "open"]
                if open_bl:
                    raise SystemExit(f"{key(repo, n)} closes #{n_issue}, which is still blocked by " + ", ".join(f"#{b['number']}" for b in open_bl) + ". Merge the blockers first (or --force, and say why in the PR).")
    if not a.yes:
        print("dry run — pass --yes only with the owner's explicit OK in the conversation. Order: " + " ".join(key(r, n) for r, n in targets))
        return
    for repo, n in targets:
        print(f"== {key(repo, n)} ==")
        # verify again, here, where it cannot be skipped: the OK was given on a verified table
        pr = next((x for x in repo.open_prs() if x["number"] == n), None)
        if pr is None:
            print(f"{key(repo, n)}: not an open PR any more. Stopping."); sys.exit(2)
        v = verify_one(repo, pr)
        if not v["ok"] and not a.force:
            print(f"{key(repo, n)}: verify says NOT OK — attribution={v['attribution']} protected={v['protected_touched']} ci={v['checks_not_green']} contradictions={v['contradictions']} worktree={v['worktree']}. Not merging.")
            sys.exit(2)

        def mergeable():
            m = json.loads(repo.gh(["pr", "view", str(n), "-R", repo.slug, "--json", "mergeable,state"]))
            if m["state"] != "OPEN":
                return "GONE"
            return None if m["mergeable"] == "UNKNOWN" else m["mergeable"]

        m = wait_for(mergeable, 180, 5)
        if m != "MERGEABLE":
            print(f"{key(repo, n)}: {m}. In its worktree: `git merge --no-edit origin/{repo.base}`, resolve, run the gate, `git commit --no-edit`, `git push` (never --force), comment on the PR, then rerun merge from here.")
            sys.exit(2)

        def ci():
            states = {v.lower() for v in repo.pr_checks(n).values()}
            if any(s in ("fail", "failure", "error", "cancelled", "timed_out") for s in states):
                return "FAILED"
            if states and states <= {"pass", "success", "skipping", "skipped", "neutral"}:
                return "PASS"
            return None

        c = wait_for(ci, a.ci_timeout, 15)
        if c != "PASS":
            print(f"{key(repo, n)}: CI {c}. Stopping.")
            sys.exit(3)
        print(repo.gh(["pr", "merge", str(n), "-R", repo.slug, "--squash" if a.squash else "--merge"]).strip() or "merged")
        repo.fetch()
        repo._prs = None
        print(f"{repo.slug} {repo.base} now {repo.origin_sha()}")
        if a.wait_base_ci:
            def base_ci():
                runs = json.loads(repo.gh(["run", "list", "-R", repo.slug, "--branch", repo.base, "--limit", "1", "--json", "status,conclusion,headSha"]))
                if runs and runs[0]["status"] == "completed":
                    return runs[0]["conclusion"]
                return None
            r = wait_for(base_ci, a.ci_timeout, 15)
            print(f"{repo.slug} {repo.base} CI: {r}")
            if r not in ("success", None):
                print("base branch CI is red after this merge: a semantic conflict. Fix forward before merging more.")
                sys.exit(4)


def cmd_close_parents(default: Repo | None, a):
    repo, epic = parse_ref(a.epic, default)
    nodes = tree(repo, epic)
    for k, nd in nodes.items():
        if not nd["children"] or nd["issue"]["state"] != "open" or (nd["parent"] is None and not a.include_epic):
            continue
        kids = [nodes[c] for c in nd["children"]]
        if all(kd["issue"]["state"] == "closed" for kd in kids):
            lines = []
            for kd in kids:
                r = Repo.get(kd["repo"])
                pr = merged_pr_for_issue(r, kd["number"])
                lines.append(f"- {kd['key'] if kd['repo'] != nd['repo'] else '#' + str(kd['number'])}" + (f" (PR #{pr['number']})" if pr else ""))
            body = "Every sub-issue is closed:\n" + "\n".join(lines)
            r = Repo.get(nd["repo"])
            if a.dry_run:
                print(f"would close {k}:\n{body}")
            else:
                r.gh(["issue", "close", str(nd["number"]), "-R", r.slug, "--comment", body])
                print(f"closed {k}")


def cmd_cleanup(default: Repo | None, a):
    repos = [Repo.get(s) for s in a.slugs] if a.slugs else [default] if default else []
    for repo in repos:
        repo.fetch()
        merged = {l.strip().replace("origin/", "") for l in repo.git(["branch", "-r", "--merged", f"origin/{repo.base}"]).splitlines()}
        for block in repo.git(["worktree", "list", "--porcelain"]).split("\n\n"):
            m = re.search(r"^worktree (.+)$", block, re.M)
            b = re.search(r"^branch refs/heads/(.+)$", block, re.M)
            if not m or not b:
                continue
            wt = Path(m.group(1))
            if wt == repo.root or not re.match(rf"{re.escape(repo.worktree(0).name[:-1])}\d+$", wt.name):
                continue
            branch = b.group(1)
            dirty = bool(repo.git(["status", "--porcelain"], cwd=wt).strip())
            ok, unpushed = repo.git_ok(["log", "--oneline", "@{u}.."], cwd=wt)
            if dirty or (ok and unpushed.strip()) or branch not in merged:
                print(f"KEEP {wt.name}: dirty={dirty} unpushed={bool(unpushed.strip()) if ok else '?'} merged={branch in merged}")
                continue
            if a.dry_run:
                print(f"would remove {wt}")
            else:
                repo.git(["worktree", "remove", str(wt)])
                print(f"removed {wt.name} (branch {branch} kept)")
        repo.git(["worktree", "prune"])


def cmd_status(default: Repo | None, a):
    repo, epic = parse_ref(a.epic, default)
    nodes = tree(repo, epic)
    cands = {c["key"]: c for c in candidates(nodes)}
    repos = sorted({nd["repo"] for nd in nodes.values()})
    lines = [f"## wave status — epic {key(repo, epic)} — {now_iso()}", ""]
    for s in repos:
        r = Repo.get(s)
        r.fetch()
        lines.append(f"- `{s}` base `{r.base}` = `{r.origin_sha()}`" + (f", account `{r.account}`" if r.account else ""))
    lines.append("")

    def line(k: str, depth: int):
        nd = nodes[k]
        iss = nd["issue"]
        label = f"#{nd['number']}" if nd["repo"] == repo.slug else k
        extra = ""
        if k in cands:
            c = cands[k]
            extra = " — " + why(c)
        lines.append(f"{'  ' * depth}- [{'x' if iss['state'] == 'closed' else ' '}] {label} {iss['title']}{extra}")
        for ch in nd["children"]:
            line(ch, depth + 1)

    line(key(repo, epic), 0)
    ready = [c["key"] for c in cands.values() if c["ready"]]
    prs = [f"{s}#{p['number']} ({p['headRefName']})" for s in repos for p in Repo.get(s).open_prs()]
    lines += ["", "next wave: " + (" ".join(ready) if ready else "nothing ready"), "", "open PRs: " + (", ".join(prs) or "none")]
    text = "\n".join(lines)
    print(text)
    if a.post:
        repo.gh(["issue", "comment", str(epic), "-R", repo.slug, "--body", text])
        print("\n(posted on the epic)")


def cmd_lint(default: Repo | None, a):
    problems = 0
    for ref in a.issues:
        repo, n = parse_ref(ref, default)
        iss = repo.issue(n)
        body = iss.get("body") or ""
        missing = [s for s in ("## Done when", "## Handoff") if s not in body]
        if not BRANCH_IN_BODY.search(body):
            missing.append("branch line `git worktree add -b <branch>` (will be derived from the title)")
        if not re.search(r"^Parent:\s*\S*#\d+", body, re.M):
            missing.append("`Parent: #N` first line")
        if "Depends on" in body and not repo.blocked_by(n):
            missing.append("CONTRADICTION: body says 'Depends on' but no blocked_by is wired in GitHub")
        mb = BRANCH_IN_BODY.search(body)
        if mb and f"/{n}-" not in mb.group(1):
            missing.append(f"CONTRADICTION: handoff branch `{mb.group(1)}` does not carry #{n}")
        closes = [int(num) for _, num in CLOSES.findall(body)]
        if closes and n not in closes:
            missing.append(f"CONTRADICTION: body closes {closes} but this is #{n}")
        if repo.sub_issues(n) and "## Handoff" in body and n != getattr(a, "epic", 0):
            missing.append("a parent with a Handoff block: parents are never dispatched; move the handoff to the leaves")
        has_line = re.search(r"\b[\w./-]+\.(rs|ts|tsx|js|py|go|swift|kt|rb|php|cs|java|yml|yaml|toml|json):\d+", body)
        has_symbol = re.search(r"`[\w./-]+\.(rs|ts|tsx|js|py|go|swift|kt|rb|php|cs|java)`[^\n]{0,80}`[A-Za-z_][\w.]*`", body)
        if not (has_line or has_symbol):
            missing.append("no evidence: neither `file:line` nor `file` + `symbol`")
        problems += bool(missing)
        print(f"{key(repo, n)}: " + ("ok" if not missing else "; ".join(missing)))
    sys.exit(1 if problems else 0)


# ---------------------------------------------------------------- why / doctor

def cmd_why(default: Repo | None, a):
    for ref in a.refs:
        repo, n = parse_ref(ref, default)
        iss = repo.issue(n)
        prem = premises_for(repo, n)
        print(f"{key(repo, n)} — {iss['title']}")
        for text, ok, ev in prem:
            print(f"  {'✓' if ok else '✗'} {text}: {ev}")
        ready = all(ok for _, ok, _ in prem)
        print("  ∴ " + ("READY — every premise holds" if ready else "NOT READY — the first ✗ is the reason") + "\n")


def cmd_doctor(default: Repo | None, a) -> None:
    """Preflight. Every ✗ is a mistake that dispatch would otherwise let happen."""
    checks: list[tuple[str, bool, str, bool]] = []  # (text, ok, evidence, hard)
    ok, out = sh_ok(["gh", "auth", "status"])
    checks.append(("gh is logged in", ok, (out.strip().splitlines() or [""])[0].strip(), True))
    ok, out = sh_ok(["git", "--version"])
    v = re.search(r"(\d+)\.(\d+)", out)
    checks.append(("git >= 2.38 (merge-tree --write-tree)", bool(v) and (int(v.group(1)), int(v.group(2))) >= (2, 38), out.strip(), True))
    checks.append(("codegraph on PATH (optional)", sh_ok(["which", "codegraph"])[0], "agents index their worktree with it", False))
    if default is None:
        checks.append(("inside a registered repo", False, "run from a checkout, or pass --repo", True))
    else:
        r = default
        dirty = r.git(["status", "--porcelain", "--untracked-files=no"]).strip()
        untracked = r.git(["status", "--porcelain", "--untracked-files=all"]).count("?? ")
        checks.append((f"main checkout `{r.root.name}` has no tracked changes (you never work there)", not dirty,
                       (f"{len(dirty.splitlines())} modified path(s)" if dirty else "clean") + (f", {untracked} untracked (fine)" if untracked else ""), True))
        gate = r.gate()
        checks.append(("gate is declared and real", bool(gate) and not gate[0].startswith("<"), " && ".join(gate), True))
        checks.append(("protected paths declared", bool(r.protected), ", ".join(r.protected) or "none: verify cannot guard anything — `repos add . --protected <paths>`", False))
        r.fetch()
        merged = {l.strip().replace("origin/", "") for l in r.git(["branch", "-r", "--merged", f"origin/{r.base}"]).splitlines()}
        orphans, stale = [], []
        for block in r.git(["worktree", "list", "--porcelain"]).split("\n\n"):
            m = re.search(r"^worktree (.+)$", block, re.M)
            b = re.search(r"^branch refs/heads/(.+)$", block, re.M)
            if not m or not b:
                continue
            wt = Path(m.group(1))
            mm = re.match(rf"{re.escape(r.worktree(0).name[:-1])}(\d+)$", wt.name)
            if not mm:
                continue
            if b.group(1) in merged or not r.git_ok(["rev-parse", "--verify", f"origin/{b.group(1)}"])[0] and not r.git(["log", "--oneline", f"origin/{r.base}..HEAD"], cwd=wt).strip():
                stale.append(wt.name)          # merged, or never pushed and no commits: leftovers, `wave cleanup`
            elif not pr_for_issue(r, int(mm.group(1))):
                orphans.append(wt.name)        # unmerged work with no PR: an agent running, or one that died
        checks.append(("no unmerged worktree without an open PR (an agent still running, or one that died)", not orphans, ", ".join(orphans) or "none", False))
        checks.append(("no leftover worktrees (merged or empty)", not stale, (", ".join(stale) + " — `wave cleanup`") if stale else "none", False))
        okdu, du = sh_ok(["du", "-sk", str(r.root / "target")]) if (r.root / "target").exists() else (False, "")
        build_kb = int(du.split()[0]) if okdu and du.split() else 0
        st = os.statvfs(r.root)
        free_kb = st.f_bavail * st.f_frsize // 1024
        need_kb = build_kb * a.batch
        checks.append((f"disk for {a.batch} parallel builds", free_kb > need_kb * 1.2 or build_kb == 0,
                       f"free {free_kb // 1024 // 1024} GB, one build ≈ {build_kb // 1024} MB, need ≈ {need_kb // 1024} MB", True))
    for text, ok, ev, hard in checks:
        print(f"  {'✓' if ok else ('✗' if hard else '!')} {text}: {ev}")
    hard_fail = [c for c in checks if not c[1] and c[3]]
    print("  ∴ " + ("fit to dispatch" if not hard_fail else f"NOT fit — {len(hard_fail)} hard failure(s) above; fix them, do not dispatch"))
    if hard_fail:
        sys.exit(1)


# ---------------------------------------------------------------- guarantees

GUARANTEES = [
    # (guard, enforced where, how you see it, tested)
    ("no merge without --yes", "cmd_merge", "`merge` without --yes prints the order and exits", True),
    ("no merge if the base or a PR head moved since the plan", "cmd_merge (--plan)", "`merge --plan` compares SHAs and refuses", False),
    ("no merge of a PR whose issue still has an open blocker", "cmd_merge", "refusal names the blockers", True),
    ("no merge of a PR that verify rejects", "cmd_merge -> verify_one", "attribution, protected, CI, contradictions re-checked at merge time", False),
    ("no merge before CI is green, one PR at a time", "cmd_merge", "waits for checks, stops on FAILED", False),
    ("base CI red after a merge stops the queue", "cmd_merge --wait-base-ci", "exit 4 with the semantic-conflict note", False),
    ("no dispatch when doctor finds a hard failure", "cmd_dispatch -> cmd_doctor", "dispatch refused", False),
    ("no dispatch of an issue that is not READY", "cmd_dispatch -> premises_for", "refusal lists the failed premises", False),
    ("an existing worktree is kept, never recreated", "cmd_dispatch", "prints 'worktree exists'", False),
    ("cleanup removes only worktrees that are clean, pushed and merged", "cmd_cleanup", "KEEP lines with the reason", False),
    ("plan refuses to run twice on the same directory or titles", "cmd_plan", "numbers.json / duplicate titles refusal", False),
    ("every open leaf is in exactly one state", "candidates (assert)", "blocked | in-progress | worktree | ready", True),
    ("verify flags AI attribution in body or commits", "verify_one", "AI-ATTRIBUTION", True),
    ("verify flags protected paths touched", "verify_one", "PROTECTED:<paths>", True),
    ("verify flags a PR closing a parent or a still-blocked issue", "verify_one", "CONTRADICTION[...]", True),
    ("verify names sibling issues that cite files the PR touched", "verify_one --epic", "notify lines", False),
    ("order predicts pairwise and chained textual conflicts", "cmd_order", "pairs + chain ✗", True),
    ("order flags serial paths", "cmd_order", "SERIAL line", True),
    ("lint flags text/data contradictions", "cmd_lint", "CONTRADICTION: ...", True),
    ("per-repo gh account token, global login untouched", "Repo._env", "GH_TOKEN per call", False),
    ("every run is logged", "log_run", "~/.config/setwave/log.jsonl", False),
    ("the script never force-pushes, resets, stashes, or deletes", "by absence", "grep the source for 'force', 'reset --hard', 'stash', 'rm -rf': zero hits", True),
]


def cmd_guarantees(default: Repo | None, a):
    """What the plugin promises, where each promise is enforced, and whether a test pins it."""
    # the one guarantee this command proves by itself: no command line in this file is a forbidden git operation.
    # Only argument lists are scanned (a token in quotes next to its verb), so prose and this table do not count.
    forbidden = [('"push"', '--force'), ('"reset"', '"--hard"'), ('"stash"',), ('"clean"', '"-f'), ('"branch"', '"-D"'), ('"rm"', '"-rf"'), ('"gc"', '--prune'), ('"reflog"', '"expire"')]
    for i, line in enumerate(Path(__file__).read_text().splitlines(), 1):
        for combo in forbidden:
            if all(tok in line for tok in combo) and "forbidden = [" not in line:
                raise SystemExit(f"line {i} builds a forbidden git command: {line.strip()}")
    rows = [(g, w, h, t) for g, w, h, t in GUARANTEES]
    if a.json:
        print(json.dumps([{"guard": g, "enforced_by": w, "visible_as": h, "tested": t} for g, w, h, t in rows], indent=2)); return
    tested = sum(1 for r in rows if r[3])
    print(f"{len(rows)} guarantees, {tested} pinned by a test, {len(rows) - tested} enforced but untested (tests: `python3 -m unittest discover -s tests -v`)\n")
    for g, w, h, t in rows:
        print(f"  {'✓' if t else '!'} {g}\n      where: {w}   seen as: {h}")
    if tested < len(rows):
        print("\n! = the guard exists in code and was exercised by hand; nothing fails yet if someone removes it.")


# ---------------------------------------------------------------- plan

def fill_refs(body: str, numbers: dict[str, int]) -> str:
    """{{key}} -> #N (a mention); {{key.n}} -> N (bare, for branch names and `Closes #`)."""
    for kk, num in numbers.items():
        body = body.replace("{{" + kk + ".n}}", str(num)).replace("{{" + kk + "}}", f"#{num}")
    return body


def cmd_plan(default: Repo | None, a):
    """Create an epic's issues from a directory: <dir>/index.tsv, <dir>/deps.tsv, <dir>/<key>.md.

    index.tsv:  key<TAB>title<TAB>labels(comma or -)<TAB>parent   (parent = a key created earlier, '#12' for an existing issue, or '-')
    deps.tsv:   blocked-key<TAB>blocker   (blocker = a key, or '#12' for an existing issue)
    Bodies may reference issues as {{key}} (-> #N) or {{key.n}} (-> N, for branch names); filled once every issue exists.
    Creates in order, links sub-issues, wires blocked_by, applies --milestone, writes numbers.json.
    """
    repo = Repo.get(a.slug) if a.slug else default
    if repo is None:
        raise SystemExit("run inside the repo or pass --slug owner/name")
    d = Path(a.dir)
    rows = [l.split("\t") for l in (d / "index.tsv").read_text().splitlines() if l.strip() and not l.startswith("#")]
    deps = [l.split("\t") for l in (d / "deps.tsv").read_text().splitlines() if l.strip() and not l.startswith("#")] if (d / "deps.tsv").exists() else []
    for r in rows:
        if len(r) != 4:
            raise SystemExit(f"index.tsv row needs 4 tab-separated fields: {r}")
        if not (d / f"{r[0]}.md").exists():
            raise SystemExit(f"missing body {d / (r[0] + '.md')}")
    if a.dry_run:
        for k, title, labels, parent in rows:
            print(f"{k:<6} parent={parent:<6} [{labels}] {title}")
        print(f"{len(rows)} issues, {len(deps)} dependencies — nothing created")
        return
    if (d / "numbers.json").exists() and not a.force:
        raise SystemExit(f"{d / 'numbers.json'} exists: this plan was already applied (issues {json.loads((d / 'numbers.json').read_text())}). Pass --force to create a second copy on purpose.")
    existing = {i["title"] for i in json.loads(repo.gh(["issue", "list", "-R", repo.slug, "--state", "all", "--limit", "500", "--json", "title"]))}
    dup = [t for _, t, _, _ in rows if t in existing]
    if dup and not a.force:
        raise SystemExit(f"{len(dup)} title(s) already exist as issues in {repo.slug} (e.g. {dup[0]!r}). Pass --force to create anyway.")
    numbers: dict[str, int] = {}
    ids: dict[str, int] = {}
    for k, title, labels, parent in rows:
        body = (d / f"{k}.md").read_text()
        body = fill_refs(body, numbers)
        cmd = ["issue", "create", "-R", repo.slug, "--title", title, "--body", body]
        for l in [x for x in labels.split(",") if x and x != "-"]:
            cmd += ["--label", l]
        if a.milestone:
            cmd += ["--milestone", a.milestone]
        url = repo.gh(cmd).strip()
        n = int(url.rstrip("/").split("/")[-1])
        numbers[k] = n
        ids[k] = repo.issue(n)["id"]
        print(f"{k} -> #{n}")
        if parent and parent != "-":
            parent_n = int(parent[1:]) if parent.startswith("#") else numbers[parent]   # "#12" = an existing issue
            repo.api(f"repos/{repo.slug}/issues/{parent_n}/sub_issues", "POST", {"sub_issue_id": ids[k]})
    for blocked, blocker in deps:
        blocker_id = repo.issue(int(blocker[1:]))["id"] if blocker.startswith("#") else ids[blocker]   # "#12" = existing
        repo.api(f"repos/{repo.slug}/issues/{numbers[blocked]}/dependencies/blocked_by", "POST", {"issue_id": blocker_id})
        print(f"#{numbers[blocked]} blocked by {blocker if blocker.startswith('#') else '#' + str(numbers[blocker])}")
    for k, title, labels, parent in rows:  # bodies that referenced later issues get their numbers now
        body = (d / f"{k}.md").read_text()
        if re.search(r"\{\{[\w.]+\}\}", body):
            body = fill_refs(body, numbers)
            repo.gh(["issue", "edit", str(numbers[k]), "-R", repo.slug, "--body", body])
            print(f"#{numbers[k]}: references filled")
    (d / "numbers.json").write_text(json.dumps(numbers, indent=2))
    print(f"\n{len(rows)} issues created; key->number map in {d / 'numbers.json'}")


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(prog="wave", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", help="path inside the default repo (default: cwd)")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("repos", help="registry: list | add <path> [--slug] [--account] [--base] [--gate ...] [--protected ...] | scan")
    x.add_argument("action", choices=["list", "add", "scan"]); x.add_argument("path", nargs="?"); x.add_argument("--slug"); x.add_argument("--account"); x.add_argument("--base"); x.add_argument("--worktree-prefix", help="where worktrees go, e.g. ~/kyte-worktrees/demeter-  (default ../<repo>-)"); x.add_argument("--gate", nargs="*"); x.add_argument("--protected", nargs="*")
    x = s.add_parser("facts", help="what was discovered about repos"); x.add_argument("slugs", nargs="*")
    x = s.add_parser("next", help="leaf issues ready to dispatch"); x.add_argument("epic"); x.add_argument("--batch", type=int, default=4); x.add_argument("--json", action="store_true")
    x = s.add_parser("dispatch", help="create worktrees + prompt files (runs doctor and refuses issues that are not READY)"); x.add_argument("issues", nargs="+"); x.add_argument("--dry-run", action="store_true"); x.add_argument("--force", action="store_true")
    x = s.add_parser("why", help="the premises behind READY / NOT READY for an issue, with evidence"); x.add_argument("refs", nargs="+")
    x = s.add_parser("doctor", help="preflight: gh, git, clean checkout, gate, protected paths, orphan worktrees, disk"); x.add_argument("--batch", type=int, default=4)
    x = s.add_parser("prompt", help="print the agent prompt for one issue"); x.add_argument("issue"); x.add_argument("--parallel", type=int, default=4)
    x = s.add_parser("verify", help="check PRs: attribution, protected paths, CI, mergeability, worktree"); x.add_argument("prs", nargs="*"); x.add_argument("--epic"); x.add_argument("--json", action="store_true")
    x = s.add_parser("order", help="pairwise merge-tree -> merge order, per repo"); x.add_argument("prs", nargs="*"); x.add_argument("--epic"); x.add_argument("--json", action="store_true"); x.add_argument("--plan", help="write the approved delta (base sha + PR heads + order) to this file")
    x = s.add_parser("merge", help="merge PRs in order, one at a time, CI green before each"); x.add_argument("prs", nargs="*"); x.add_argument("--yes", action="store_true"); x.add_argument("--plan", help="plan file from `order --plan`: refuses if the base or any PR head moved since"); x.add_argument("--force", action="store_true"); x.add_argument("--squash", action="store_true"); x.add_argument("--ci-timeout", type=int, default=1200); x.add_argument("--wait-base-ci", action="store_true")
    x = s.add_parser("close-parents", help="close parents whose sub-issues are all closed"); x.add_argument("epic"); x.add_argument("--include-epic", action="store_true"); x.add_argument("--dry-run", action="store_true")
    x = s.add_parser("cleanup", help="remove worktrees that are clean, pushed and merged"); x.add_argument("slugs", nargs="*"); x.add_argument("--dry-run", action="store_true")
    x = s.add_parser("status", help="tree with states; --post comments it on the epic"); x.add_argument("epic"); x.add_argument("--post", action="store_true")
    x = s.add_parser("lint", help="check issues carry what /wave needs"); x.add_argument("issues", nargs="+")
    x = s.add_parser("plan", help="create an epic's issues from <dir>/index.tsv + deps.tsv + <key>.md, wiring sub-issues and blocked_by"); x.add_argument("dir"); x.add_argument("--slug"); x.add_argument("--milestone"); x.add_argument("--dry-run", action="store_true"); x.add_argument("--force", action="store_true")

    x = s.add_parser("stats", help="what the log says: runs, durations, failures per command")
    x = s.add_parser("guarantees", help="every promise the plugin makes, where it is enforced, whether a test pins it"); x.add_argument("--json", action="store_true")

    a = p.parse_args(argv)
    if a.cmd == "stats":
        return cmd_stats()
    default: Repo | None
    try:
        default = Repo.from_cwd(a.repo)
    except SystemExit:
        default = None
    t0 = time.time(); exit_code = 0
    try:
        {"repos": cmd_repos, "facts": cmd_facts, "next": cmd_next, "dispatch": cmd_dispatch, "prompt": cmd_prompt,
     "verify": cmd_verify, "order": cmd_order, "merge": cmd_merge, "close-parents": cmd_close_parents,
             "cleanup": cmd_cleanup, "status": cmd_status, "lint": cmd_lint, "plan": cmd_plan, "why": cmd_why, "doctor": cmd_doctor,
         "guarantees": cmd_guarantees}[a.cmd](default, a)
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
        raise
    finally:
        log_run(a, default, time.time() - t0, exit_code)


def log_run(a, default: Repo | None, seconds: float, exit_code: int) -> None:
    """Append one line per run: the raw material for `stats`. Never fails the command."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"at": now_iso(), "cmd": a.cmd, "args": [x for x in sys.argv[1:] if x != a.cmd], "repo": default.slug if default else None,
               "seconds": round(seconds, 1), "exit": exit_code}
        with (CONFIG_DIR / "log.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def cmd_stats() -> None:
    f = CONFIG_DIR / "log.jsonl"
    if not f.exists():
        print("no runs logged yet"); return
    runs = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    by: dict[str, list] = {}
    for r in runs:
        by.setdefault(r["cmd"], []).append(r)
    print(f"{len(runs)} runs since {runs[0]['at']} — {f}")
    print(f"{'command':<14}{'runs':>5}{'failed':>8}{'median s':>10}{'max s':>8}")
    for cmd, rs in sorted(by.items()):
        secs = sorted(r["seconds"] for r in rs)
        med = secs[len(secs) // 2]
        print(f"{cmd:<14}{len(rs):>5}{sum(1 for r in rs if r['exit']):>8}{med:>10.1f}{secs[-1]:>8.1f}")
    merges = [r for r in by.get("merge", []) if "--yes" in r["args"]]
    if merges:
        print(f"\nmerge runs with --yes: {len(merges)}, stopped by conflict/CI: {sum(1 for r in merges if r['exit'])}")


if __name__ == "__main__":
    main()
