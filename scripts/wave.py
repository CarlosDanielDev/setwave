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

Registry: ~/.config/wave/repos.json
    {"search_paths": ["~/projects"],
     "repos": {"owner/name": {"path": "/abs/checkout", "account": "ghuser",
                              "base": "main", "gate": [...], "protected": [...],
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
CONFIG_DIR = Path(os.environ.get("WAVE_CONFIG", Path.home() / ".config" / "wave"))
REGISTRY = CONFIG_DIR / "repos.json"

ATTRIBUTION = re.compile(r"co-authored-by:\s*claude|generated with \[?claude code|noreply@anthropic\.com", re.I)
CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:([\w.-]+/[\w.-]+))?#(\d+)\b", re.I)
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
            p = (self.root / prefix)
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
        ok, out = self.gh_ok(["pr", "checks", str(n), "-R", self.slug, "--json", "name,state"])
        if ok and out.strip():
            return {c["name"]: c["state"] for c in json.loads(out)}
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
        out.append({"key": nd["key"], "repo": repo.slug, "number": n, "title": iss["title"], "branch": branch_for(iss),
                    "blocked_by": blockers, "pr": pr["number"] if pr else None,
                    "worktree": str(wt) if wt.exists() else None,
                    "ready": not blockers and not pr and not wt.exists()})
    return out


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
        for k in ("account", "base"):
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
    if epic:
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


def verify_one(repo: Repo, pr: dict) -> dict:
    head = pr["headRefName"]
    repo.git(["fetch", "-q", "origin", head])
    body = pr.get("body") or ""
    commits = repo.git(["log", "--format=%B", f"origin/{repo.base}..origin/{head}"])
    attribution = bool(ATTRIBUTION.search(body + commits))
    touched = []
    if repo.protected:
        diff = repo.git(["diff", "--name-only", f"origin/{repo.base}...origin/{head}", "--"] + repo.protected)
        touched = [l for l in diff.splitlines() if l.strip()]
    checks = repo.pr_checks(pr["number"])
    bad = {k: v for k, v in checks.items() if v.lower() not in ("pass", "success", "skipping", "skipped", "neutral")}
    n_issue = next((int(num) for _, num in CLOSES.findall(body)), None)
    wt = repo.worktree(n_issue) if n_issue else None
    wt_state = None
    if wt and wt.exists():
        dirty = bool(repo.git(["status", "--porcelain"], cwd=wt).strip())
        ok, unpushed = repo.git_ok(["log", "--oneline", "@{u}.."], cwd=wt)
        wt_state = {"dirty": dirty, "unpushed": bool(unpushed.strip()) if ok else None}
    ok = (not attribution and not touched and not bad and pr.get("mergeable") != "CONFLICTING"
          and not (wt_state and (wt_state["dirty"] or wt_state["unpushed"])))
    return {"repo": repo.slug, "pr": pr["number"], "issue": n_issue, "head": head, "attribution": attribution,
            "protected_touched": touched, "checks_not_green": bad, "mergeable": pr.get("mergeable"),
            "worktree": wt_state, "ok": ok}


def cmd_verify(default: Repo | None, a):
    results = [verify_one(r, p) for r, p in select_prs(default, a.prs, a.epic)]
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
            print(f"{r['repo']}#{r['pr']:<5} issue #{r['issue'] or '?':<5} {'OK    ' if r['ok'] else 'NOT OK'} {' '.join(flags)}")
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
        result[slug] = {"order": order, "pairs": {f"{x}x{y}": v for (x, y), v in pairs.items()}, "against_base": against_base}
        if not a.json:
            print(f"== {slug} (base {base}) ==")
            for n in nums:
                if against_base[n]:
                    print(f"  #{n} conflicts with {base} NOW: {', '.join(against_base[n])}")
            for (x, y), files in pairs.items():
                print(f"  #{x} x #{y}: {', '.join(files)}")
            if not pairs:
                print("  no pairwise conflicts")
            print("  order: " + " -> ".join(f"{slug}#{n}" for n in order))
    if a.json:
        print(json.dumps(result, indent=2))
    else:
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
    if not a.yes:
        print("dry run — pass --yes only with the owner's explicit OK in the conversation. Order: " + " ".join(key(r, n) for r, n in targets))
        return
    for repo, n in targets:
        print(f"== {key(repo, n)} ==")

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
            missing.append("body says 'Depends on' but no blocked_by is wired in GitHub")
        if not re.search(r"\b[\w./-]+\.(rs|ts|tsx|js|py|go|swift|kt|rb|php|cs|java):\d+", body):
            missing.append("no `file:line` evidence")
        problems += bool(missing)
        print(f"{key(repo, n)}: " + ("ok" if not missing else "; ".join(missing)))
    sys.exit(1 if problems else 0)


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(prog="wave", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", help="path inside the default repo (default: cwd)")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("repos", help="registry: list | add <path> [--slug] [--account] [--base] [--gate ...] [--protected ...] | scan")
    x.add_argument("action", choices=["list", "add", "scan"]); x.add_argument("path", nargs="?"); x.add_argument("--slug"); x.add_argument("--account"); x.add_argument("--base"); x.add_argument("--gate", nargs="*"); x.add_argument("--protected", nargs="*")
    x = s.add_parser("facts", help="what was discovered about repos"); x.add_argument("slugs", nargs="*")
    x = s.add_parser("next", help="leaf issues ready to dispatch"); x.add_argument("epic"); x.add_argument("--batch", type=int, default=4); x.add_argument("--json", action="store_true")
    x = s.add_parser("dispatch", help="create worktrees + prompt files"); x.add_argument("issues", nargs="+"); x.add_argument("--dry-run", action="store_true")
    x = s.add_parser("prompt", help="print the agent prompt for one issue"); x.add_argument("issue"); x.add_argument("--parallel", type=int, default=4)
    x = s.add_parser("verify", help="check PRs: attribution, protected paths, CI, mergeability, worktree"); x.add_argument("prs", nargs="*"); x.add_argument("--epic"); x.add_argument("--json", action="store_true")
    x = s.add_parser("order", help="pairwise merge-tree -> merge order, per repo"); x.add_argument("prs", nargs="*"); x.add_argument("--epic"); x.add_argument("--json", action="store_true")
    x = s.add_parser("merge", help="merge PRs in order, one at a time, CI green before each"); x.add_argument("prs", nargs="+"); x.add_argument("--yes", action="store_true"); x.add_argument("--squash", action="store_true"); x.add_argument("--ci-timeout", type=int, default=1200); x.add_argument("--wait-base-ci", action="store_true")
    x = s.add_parser("close-parents", help="close parents whose sub-issues are all closed"); x.add_argument("epic"); x.add_argument("--include-epic", action="store_true"); x.add_argument("--dry-run", action="store_true")
    x = s.add_parser("cleanup", help="remove worktrees that are clean, pushed and merged"); x.add_argument("slugs", nargs="*"); x.add_argument("--dry-run", action="store_true")
    x = s.add_parser("status", help="tree with states; --post comments it on the epic"); x.add_argument("epic"); x.add_argument("--post", action="store_true")
    x = s.add_parser("lint", help="check issues carry what /wave needs"); x.add_argument("issues", nargs="+")

    a = p.parse_args(argv)
    default: Repo | None
    try:
        default = Repo.from_cwd(a.repo)
    except SystemExit:
        default = None
    {"repos": cmd_repos, "facts": cmd_facts, "next": cmd_next, "dispatch": cmd_dispatch, "prompt": cmd_prompt,
     "verify": cmd_verify, "order": cmd_order, "merge": cmd_merge, "close-parents": cmd_close_parents,
     "cleanup": cmd_cleanup, "status": cmd_status, "lint": cmd_lint}[a.cmd](default, a)


if __name__ == "__main__":
    main()
