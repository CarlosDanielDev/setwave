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
same per-repo keys (gate, protected, base, worktree_prefix). `wave init` proposes
those keys on a repo's first run and marks the accepted answer `confirmed_at`.

Refs: issues and PRs are `owner/name#N`; a bare `#N`/`N` means the repo of the
current directory. Stdlib only. Needs `gh` (logged in), `git` >= 2.38, optional `codegraph`.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = (HERE.parent / "templates") if (HERE.parent / "templates").exists() else HERE / "templates"
CONFIG_DIR = Path(os.environ.get("SETWAVE_CONFIG", Path.home() / ".config" / "setwave"))
REGISTRY = CONFIG_DIR / "repos.json"

ATTRIBUTION = re.compile(r"co-authored-by:\s*claude|generated with \[?claude code|noreply@anthropic\.com", re.I)
CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)(?::\s*|\s+)(?:([\w.-]+/[\w.-]+))?#(\d+)\b", re.I)
# the version line of the serial manifests: it only moves in a Release PR (#58)
VERSION_LINE = re.compile(r'"version"\s*:')
# textual dependencies `wave adopt` wires as real blocked_by: a verb, then a ref — a bare #N is this repo's
# issue, owner/name#N another repo's. A ref with no verb in front of it is a mention, not a dependency.
DEPENDS = re.compile(r"\b(?:depends on|blocked by|needs|after)\s+((?:[\w.-]+/[\w.-]+)?)#(\d+)", re.I)
BRANCH_IN_BODY = re.compile(r"git worktree add -b\s+(\S+)")
CODEGRAPH_IN_BODY = re.compile(r'codegraph explore "([^"]+)"')
REF = re.compile(r"^(?:(?P<slug>[\w.-]+/[\w.-]+))?#?(?P<n>\d+)$")
ORIGIN = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")
# CI steps that are actions: the ones that are gates in practice, and the command each runs. Keys are
# lowercase `owner/repo` without `@ref`. An action in neither table is named by `doctor`, never guessed.
GATE_ACTIONS = {
    "embarkstudios/cargo-deny-action": "cargo deny check",
    "golangci/golangci-lint-action": "golangci-lint run",
    "actions-rs/clippy-check": "cargo clippy --all-targets -- -D warnings",
}
# setup and plumbing, not gates (fnmatch patterns); a repo adds its own with `ignore_actions` in .wave.json
IGNORE_ACTIONS = ["actions/checkout", "actions/setup-*", "actions/cache", "actions/cache/*", "actions/upload-artifact",
                  "actions/download-artifact", "dtolnay/rust-toolchain", "swatinem/rust-cache"]
# agent liveness, inferred from the newest trace its work left in the worktree (an agent that dies stops writing):
# changed less than WORKING_MIN minutes ago = working, up to DEAD_MIN = quiet, more and no PR = likely dead
WORKING_MIN = 10
DEAD_MIN = 30
STAMP = ".setwave.json"  # written by dispatch into each worktree, excluded from git: issue, repo, dispatched_at, prompt, resumes, respawns
# dispatches over a worktree without a new commit on its branch — a plain resume and a respawn with a new strategy
# count alike: at RETRY_CEILING of them, the agent is agent-exhausted — dispatch refuses a further one and
# `wave sweep --fix` escalates to the owner; nothing is ever closed by itself
RETRY_CEILING = 3
# more parallel agents than this once hit the API rate limit and left a half-extracted crate in the shared cargo cache
MAX_BATCH = 6
# the agent skills a handoff names when the repo declares and detects none (`phases.agent_skills` in .wave.json wins)
DEFAULT_SKILLS = ["caveman (ultra)", "ponytail", "superpowers:test-driven-development"]
# a registry package in Cargo.lock: name, version, then a registry or sparse-index source (git and path packages are not cached there)
LOCK_PACKAGE = re.compile(r'\[\[package\]\]\s*\nname = "([^"]+)"\s*\nversion = "([^"]+)"\s*\nsource = "(?:registry|sparse)\+')
# `order --run-gate` checks each simulated step out into a detached worktree named so; `doctor` reports any it finds
CHAIN_GATE_PREFIX = "setwave-chain-gate-"


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


def token_of(slug: str, account: str) -> str:
    """The account's token, from `gh auth token` stdout only: gh writes update notices and keyring
    warnings to stderr, and one of those concatenated into GH_TOKEN silently 401s every later gh
    call for the repo (#27). A failing command keeps stderr for its message — never stdout, which
    may hold a token; a stdout that is not exactly one non-empty line is refused, never stored."""
    rc, out, err = run(["gh", "auth", "token", "--user", account])
    if rc != 0:
        raise SystemExit(f"{slug}: account {account} is not logged in to gh (`gh auth login`)\n{err.strip() or '(gh printed nothing to stderr)'}")
    lines = out.splitlines()
    if len(lines) != 1 or not lines[0].strip():
        raise SystemExit(f"{slug}: `gh auth token --user {account}` printed something that is not a token "
                         f"({len(lines)} line(s)) — nothing stored; check the account name and `gh auth status`")
    return lines[0].strip()


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


def find_checkouts(reg: dict, slug: str) -> list[Path]:
    """Every checkout under the search paths whose origin is slug, sorted: [0] is the one the registry takes."""
    found = []
    for base in reg["search_paths"]:
        root = Path(base).expanduser()
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if not (d / ".git").is_dir():
                continue
            ok, url = sh_ok(["git", "remote", "get-url", "origin"], cwd=d)
            if ok and slug_of_url(url) == slug:
                found.append(d.resolve())
    return found


def clone_repo(reg: dict, slug: str) -> Path:
    """The missing checkout goes into the first search path: size-checked first, never shallow.

    `gh repo clone` copies the whole history (no --depth is ever passed: worktrees, `merge-tree` and
    `--merged` read it); above `clone_ask_over_mb` (registry, default 500) the tool stops and hands
    the command back — cloning is safe but not free, and where big things go is the user's call.
    """
    if "/" not in slug:
        raise SystemExit(f"{slug}: not an owner/name slug — refs are owner/name#N")
    if not reg.get("search_paths"):
        raise SystemExit(f"no search paths in {REGISTRY}: set \"search_paths\" there — the first is where a clone goes")
    dest = Path(reg["search_paths"][0]).expanduser() / slug.split("/")[1]
    env = dict(os.environ)
    account = reg["repos"].get(slug, {}).get("account")
    if account:
        env["GH_TOKEN"] = token_of(slug, account)
    ok, out = sh_ok(["gh", "repo", "view", slug, "--json", "diskUsage"], env=env)
    if not ok:
        raise SystemExit(f"{slug}: cannot read its size (`gh repo view {slug} --json diskUsage`)\n{out}")
    mb = json.loads(out).get("diskUsage", 0) / 1024
    print(f"{slug} is {mb:.1f} MB on GitHub", file=sys.stderr)  # progress is never stdout: `--json` consumers parse stdout
    ask_over = reg.get("clone_ask_over_mb", 500)
    if mb > ask_over:
        raise SystemExit(f"{slug} is over the {ask_over} MB this tool clones without asking — cloning is safe but not free, "
                         f"and where big things go is your call:\n  gh repo clone {slug} {dest}\n"
                         f"then `wave.py repos add {dest} --slug {slug}`")
    dest.parent.mkdir(parents=True, exist_ok=True)
    ok, out = sh_ok(["gh", "repo", "clone", slug, str(dest)], env=env)
    if not ok:
        raise SystemExit(f"`gh repo clone {slug} {dest}` failed:\n{out}")
    ok, shallow = sh_ok(["git", "rev-parse", "--is-shallow-repository"], cwd=dest)
    if ok and shallow.strip() == "true":
        raise SystemExit(f"{dest} is a shallow clone: worktrees, `merge-tree` and `--merged` need history — "
                         "remove it and clone again without --depth")
    print(f"cloned {slug} into {dest}", file=sys.stderr)
    return dest


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
                found = find_checkouts(reg, slug)
                if found:
                    entry["path"] = str(found[0])
                    if len(found) > 1:
                        print(f"{slug}: {len(found)} checkouts — using {found[0]}, also at "
                              f"{', '.join(map(str, found[1:]))}; the registry keeps this one")
                else:
                    entry["path"] = str(clone_repo(reg, slug))
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
        # inside a linked worktree the toplevel is the worktree: the registry names the main checkout, the one
        # the common git dir belongs to, so a command run in a worktree never re-points it (a submodule keeps its toplevel)
        common = (root / sh(["git", "rev-parse", "--git-common-dir"], cwd=root).strip()).resolve()
        root = common.parent if common.name == ".git" else root
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
        self.local_cfg: dict | None = json.loads(local.read_text()) if local.exists() else None
        self.entry = {k: v for k, v in entry.items() if k != "path"}
        self.cfg = {**(self.local_cfg or {}), **self.entry}
        self.account = self.cfg.get("account")
        self.env = self._env()
        self.base = self.cfg.get("base") or json.loads(self.gh(["repo", "view", slug, "--json", "defaultBranchRef"]))["defaultBranchRef"]["name"]
        self._load_cfg()
        self.handoffs = self.root.parent / f"{self.root.name}-handoffs"
        self._prs: list[dict] | None = None

    def _load_cfg(self) -> None:
        """`.wave.json` as origin/<base> has it, the tree every worktree is cut from: the main checkout is
        never worked in, so it falls behind. Its working-tree copy counts only when origin has none; the registry wins over both."""
        ok, text = self.git_ok(["show", f"origin/{self.base}:.wave.json"])
        try:
            self.remote_cfg: dict | None = json.loads(text) if ok else None
        except json.JSONDecodeError as e:
            raise SystemExit(f"{self.slug}: origin/{self.base}:.wave.json is not valid JSON ({e}): fix it on {self.base}")
        self.cfg = {**(self.remote_cfg if ok else self.local_cfg or {}), **self.entry}
        where = [f"origin/{self.base}:.wave.json" if ok else "working tree .wave.json" if self.local_cfg is not None else "",
                 f"registry ({', '.join(sorted(self.entry))})" if self.entry else ""]
        self.cfg_source = " + ".join(w for w in where if w) or "none (defaults)"
        self.protected: list[str] = list(self.cfg.get("protected", []))

    def _env(self) -> dict:
        env = dict(os.environ)
        if self.account:
            if self.account not in Repo._tokens:
                Repo._tokens[self.account] = token_of(self.slug, self.account)
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

    def ci_gate(self) -> tuple[list[tuple[str, str]], list[str]]:
        """The gate commands CI runs, each with its source (`run:` or `uses:`), and the `uses:` steps that
        neither GATE_ACTIONS nor the ignore list (IGNORE_ACTIONS + `ignore_actions`) knows: only the owner can say what those run."""
        cmds: list[tuple[str, str]] = []
        unknown: list[str] = []
        own = self.cfg.get("ignore_actions", [])
        ignore = [p.lower() for p in IGNORE_ACTIONS + ([own] if isinstance(own, str) else list(own))]  # a bare string is one pattern, not its letters
        wf_dir = self.root / ".github" / "workflows"
        for wf in sorted(wf_dir.glob("*.y*ml")) if wf_dir.exists() else []:
            for line in wf.read_text().splitlines():
                m = re.match(r"\s*(?:-\s*)?(run|uses):\s*(.+?)\s*$", line)
                if not m:
                    continue
                kind, val = m.groups()
                if kind == "run":
                    if re.match(r"(cargo|npm|pnpm|yarn|bun|pytest|python -m|uv run|go |make|mix|gradle|\./gradlew|swift|xcodebuild|dotnet|flutter|dart)", val) and val not in [c for c, _ in cmds]:
                        cmds.append((val, "run:"))
                    continue
                action = re.sub(r"\s+#.*$", "", val).strip("'\"")
                name = action.split("@")[0].lower()
                if name.startswith("./.github/workflows/"):
                    continue  # a local reusable workflow: its file is in wf_dir and is read on its own
                if name in GATE_ACTIONS:
                    if GATE_ACTIONS[name] not in [c for c, _ in cmds]:
                        cmds.append((GATE_ACTIONS[name], "uses:"))
                elif not any(fnmatch(name, p) or fnmatch(action.lower(), p) for p in ignore) and action not in unknown:
                    unknown.append(action)
        return cmds, unknown

    def gate(self) -> list[str]:
        return [c for c, _ in self.gate_sources()]

    def gate_sources(self) -> list[tuple[str, str]]:
        """(command, source): source is `config` (.wave.json or the registry), `run:` or `uses:` (CI), or `manifest`."""
        if self.cfg.get("gate"):
            return [(c, "config") for c in self.cfg["gate"]]
        cmds = self.ci_gate()[0]
        if cmds:
            return cmds
        return [(c, "manifest" if not c.startswith("<") else "none") for c in self._manifest_gate()]

    def _manifest_gate(self) -> list[str]:
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
        make = [f"make {t}" for t in GATE_TARGETS if t in makefile_targets(r)]
        if make:
            return make
        return ["<no gate found: read the repo, decide, and write it to .wave.json>"]

    def gate_proposal(self) -> tuple[list[str] | None, str, str]:
        """The gate candidates in the order init offers them — CI `run:`/`uses:` (#3), then the Makefile's
        test/check/lint/ci targets, then the manifest scripts, and last the commands the rule files' code
        blocks name — with how the winner was read: detected (a file says so) or guessed (convention).
        Detection reads; it never runs a candidate to see whether it works."""
        ci = self.ci_gate()[0]
        if ci:
            return [c for c, _ in ci], "detected", ".github/workflows `run:`/`uses:`"
        mk = [f"make {t}" for t in GATE_TARGETS if t in makefile_targets(self.root)]
        if mk:
            return mk, "detected", "Makefile targets test/check/lint/ci"
        r = self.root
        if (r / "package.json").exists():
            try:
                scripts = json.loads((r / "package.json").read_text()).get("scripts", {})
            except ValueError:
                scripts = {}
            pm = "pnpm" if (r / "pnpm-lock.yaml").exists() else "yarn" if (r / "yarn.lock").exists() else "bun" if (r / "bun.lockb").exists() else "npm"
            picked = [f"{pm} run {s}" for s in ("lint", "typecheck", "test", "build") if s in scripts]
            if picked:
                return picked, "detected", "package.json scripts"
            return [f"{pm} test"], "guessed", "package.json names no scripts: the runner's default test"
        if (r / "Cargo.toml").exists():
            return ["cargo fmt --check", "cargo clippy --all-targets -- -D warnings", "cargo test"], "guessed", "Cargo.toml: the Rust convention, nothing read"
        if (r / "pyproject.toml").exists():
            return ["ruff check .", "pytest"], "guessed", "pyproject.toml: the Python convention, nothing read"
        if (r / "go.mod").exists():
            return ["go vet ./...", "go test ./..."], "guessed", "go.mod: the Go convention, nothing read"
        if (r / "Package.swift").exists():
            return ["swift build", "swift test"], "guessed", "Package.swift: the Swift convention, nothing read"
        docs = [(c, w) for c, w in doc_commands(self.root) if w != "Makefile"]
        if docs:
            return [c for c, _ in docs], "detected", "the code blocks of " + ", ".join(sorted({w for _, w in docs}))
        return None, "guessed", "no gate found anywhere — a gate is never guessed, only named"

    def has_codegraph(self) -> bool:
        return (self.root / ".codegraph").exists()

    def profile(self) -> dict:
        """The repo's own orchestration: detect_profile's phases plus the gate and the worktree location,
        which read the config and CI that only this class sees."""
        p = detect_profile(self.root, self.cfg)
        gate = [(c, s) for c, s in self.gate_sources() if not c.startswith("<")]
        p["phases"]["gate"] = {"provider": [c for c, _ in gate],
                               "source": {"config": "declared"}.get(gate[0][1], "detected") if gate else "default"}
        prefix = self.cfg.get("worktree_prefix")
        p["phases"]["worktree"] = ({"provider": prefix, "source": "declared"} if prefix
                                   else {"provider": "../<repo>-<N>", "source": "default"})
        return p

    def fetch(self) -> None:
        self.git(["fetch", "-q", "--prune", "origin"])
        self._load_cfg()  # origin/<base> may carry a newer .wave.json now

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

    def parent(self, n: int) -> dict | None:
        """The issue's parent (the sub-issue API's read end), or None when it has none — or the server has no such endpoint."""
        ok, _ = self.gh_ok(["api", f"repos/{self.slug}/issues/{n}/parent"])
        if not ok:
            return None
        return self.api(f"repos/{self.slug}/issues/{n}/parent")

    def open_prs(self) -> list[dict]:
        if self._prs is None:
            self._prs = json.loads(self.gh(["pr", "list", "-R", self.slug, "--state", "open", "--limit", "200",
                                            "--json", "number,title,headRefName,baseRefName,body,mergeable,url"]))
        return self._prs

    def merged_prs(self, search: str) -> list[dict]:
        return json.loads(self.gh(["pr", "list", "-R", self.slug, "--state", "merged", "--limit", "100", "--search", search,
                                   "--json", "number,body,headRefName"]))

    def prs_for_head(self, branch: str) -> list[dict]:
        """Every PR whose head is this branch, any state. GitHub is the truth here: a squash merge and a
        remote branch deleted after the merge are both invisible to `git branch --merged`."""
        return json.loads(self.gh(["pr", "list", "-R", self.slug, "--state", "all", "--head", branch,
                                   "--limit", "200", "--json", "number,state,headRefName"]))

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


# ---------------------------------------------------------------- profile

GATE_TARGETS = ("test", "check", "lint", "ci")  # Makefile targets offered as gate candidates, in this order
RULE_FILES = ("CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")
KNOWN_INTERPRETERS = {"python3", "python", "bash", "sh", "zsh", "node", "ruby", "perl"}
SKILLS_OF_A_PHASE = {"define", "kickoff", "issue-handoff"}  # named by their own phase, not by the skills line
# gate-shaped commands, the same family `ci_gate` reads from workflow `run:` lines (plus `ruff`): what the
# docs may promise. Anything else in a CLAUDE.md is prose, not a gate candidate.
DOC_GATE_CMD = re.compile(r"((?:cargo|npm|pnpm|yarn|bun|pytest|python3? -m|uv run|go |make|mix|gradle|\./gradlew|"
                          r"swift|xcodebuild|dotnet|flutter|dart|ruff)\b.*)$")


def makefile_targets(root: Path) -> list[str]:
    """Every runnable target the root Makefile defines (a `name:` line at column 0), in file order."""
    mk = root / "Makefile"
    if not mk.exists():
        return []
    out = []
    for line in mk.read_text(errors="replace").splitlines():
        m = re.match(r"([^\s:=][^:=]*?)\s*:{1,2}(?:\s|$)", line)
        for t in m.group(1).split() if m else []:
            if not t.startswith((".", "%", "$")) and t not in out:
                out.append(t)
    return out


def _stems(d: Path) -> list[str]:
    """Names of the files in d, extension dropped, sorted — the `.claude/commands`, `agents` and
    `issue-templates` convention is one definition per file."""
    return sorted(p.stem for p in d.iterdir() if p.is_file() and not p.name.startswith(".")) if d.is_dir() else []


def _skill_names(root: Path) -> list[str]:
    """Skill names under `.claude/skills/`: a directory is one skill, so is a bare `.md` file."""
    d = root / ".claude" / "skills"
    if not d.is_dir():
        return []
    return sorted(p.name if p.is_dir() else p.stem for p in d.iterdir() if not p.name.startswith("."))


def _src(declared: bool, detected: bool) -> str:
    return " + ".join(label for label, on in (("declared", declared), ("detected", detected)) if on) or "default"


def hook_health(root: Path) -> list[dict]:
    """Every hook the repo declares — files under `.claude/hooks/` and the commands `.claude/settings.json`
    registers — with whether it can run. A hook that is not executable, or whose interpreter is missing,
    fails silently for every agent meant to trip on it. A file a settings command runs through an
    interpreter (`python3 x.py`) needs no executable bit."""
    wired: set[str] = set()
    commands: list[tuple[str, str | None, str | None, str | None]] = []
    data, _ = load_settings(root / ".claude" / "settings.json")
    if isinstance(data, dict):
        for blocks in (data.get("hooks") or {}).values():
            for b in blocks if isinstance(blocks, list) else []:
                if not isinstance(b, dict):
                    continue
                for h in b.get("hooks") or []:
                    cmd = h.get("command") if isinstance(h, dict) else None
                    if cmd is not None and not isinstance(cmd, str):
                        commands.append((str(cmd), None, None, "command is not a string"))
                        continue
                    if not cmd:
                        continue
                    try:
                        toks = shlex.split(cmd)
                    except ValueError as e:
                        commands.append((cmd, None, None, f"cannot parse the command ({e})"))
                        continue
                    interp = Path(toks[0]).name if toks and Path(toks[0]).name in KNOWN_INTERPRETERS else None
                    script = next((t for t in (toks[1:] if interp else toks)
                                   if "/" in t or t.endswith((".py", ".sh", ".js", ".ts"))), None)
                    commands.append((cmd, interp, script, None))
                    if interp and script:
                        p = Path(script)
                        wired.add((p if p.is_absolute() else root / p).resolve().as_posix())
    out = []
    d = root / ".claude" / "hooks"
    if d.is_dir():
        for f in sorted(p for p in d.iterdir() if p.is_file()):
            why = None
            interp = None
            first = f.read_text(errors="replace").splitlines()[:1]
            if first and first[0].startswith("#!"):
                toks = first[0][2:].split()
                tok = toks[1] if toks and toks[0].endswith("env") and len(toks) > 1 else (toks[0] if toks else None)
                interp = Path(tok).name if tok else None
            if f.resolve().as_posix() not in wired and not os.access(f, os.X_OK):
                why = "not executable (chmod +x)"
            elif interp and shutil.which(interp) is None:
                why = f"interpreter `{interp}` is not on PATH"
            out.append({"path": f.relative_to(root).as_posix(), "interpreter": interp, "ok": why is None, "why": why})
    for cmd, interp, script, broken in commands:
        why = broken
        if interp and shutil.which(interp) is None:
            why = f"interpreter `{interp}` is not on PATH"
        elif script:
            sp = Path(script)
            if not sp.is_absolute():
                sp = root / sp
            if not sp.exists():
                why = f"names `{script}`, which does not exist"
        out.append({"path": script or cmd, "command": cmd, "interpreter": interp, "ok": why is None, "why": why})
    return out


def doc_commands(root: Path) -> list[tuple[str, str]]:
    """(command, where): the gate-shaped commands the repo's own docs tell people to run — the Makefile's
    gate targets and the fenced command lines of the rule files. `doctor` warns when the gate does not
    contain one: the docs promise it, the gate never runs it."""
    out = [(f"make {t}", "Makefile") for t in GATE_TARGETS if t in makefile_targets(root)]
    for doc in [f for f in RULE_FILES if (root / f).exists()]:
        fence = False
        try:
            lines = (root / doc).read_text(errors="replace").splitlines()
        except OSError:
            continue  # a rule file that is a directory or unreadable: doctor has enough to say already
        for line in lines:
            if line.strip().startswith("```"):
                fence = not fence
            elif fence:
                m = DOC_GATE_CMD.match(line.strip().lstrip("$ ").strip())
                if m and m.group(1).strip() not in [c for c, _ in out]:
                    out.append((m.group(1).strip(), doc))
    return out


def detect_profile(root: Path, cfg: dict) -> dict:
    """The repo's own orchestration, by name and location, per phase: what `.wave.json` `phases` declares
    is `declared`, a known name on disk is `detected`, what remains is the plugin's `default`. Detection
    reads the checkout's working tree; the gate and the worktree location are added by `Repo.profile`."""
    declared = cfg.get("phases")
    if declared is None:
        declared = {}
    elif not isinstance(declared, dict):
        raise SystemExit(f"{root}/.wave.json: `phases` must be an object with one key per phase "
                         f"(define, handoff, kickoff, sync, rules, agent_skills, review, enforcement), "
                         f"got {type(declared).__name__}")
    for key in ("rules", "agent_skills"):
        if key in declared and not isinstance(declared[key], list):
            raise SystemExit(f"{root}/.wave.json: `phases.{key}` must be a list of names, got {type(declared[key]).__name__}")

    def phase(name: str, detected=None, default=None):
        if declared.get(name):
            return {"provider": declared[name], "source": "declared"}
        if detected:
            return {"provider": detected, "source": "detected"}
        return {"provider": default if default is not None else [], "source": "default"}

    skills = _skill_names(root)
    commands = _stems(root / ".claude" / "commands")
    agents = _stems(root / ".claude" / "agents")
    templates = _stems(root / ".claude" / "issue-templates")
    targets = makefile_targets(root)
    hooks = hook_health(root)
    handoff = next((p for p in (root / ".claude" / "skills" / "issue-handoff" / "SKILL.md",
                                root / ".claude" / "skills" / "issue-handoff.md") if p.exists()), None)
    declared_rules = [r for r in declared.get("rules", []) if isinstance(r, str)] \
        if isinstance(declared.get("rules"), list) else []
    found_rules = [f for f in RULE_FILES if (root / f).exists()]
    declared_skills = [s for s in declared.get("agent_skills", []) if isinstance(s, str)] \
        if isinstance(declared.get("agent_skills"), list) else []
    extra_skills = declared_skills + [s for s in skills if s not in SKILLS_OF_A_PHASE and s not in declared_skills]
    kick = None
    if not declared.get("kickoff"):
        kick = "kickoff" if "kickoff" in skills else next((c for c in commands if c.startswith("start-")), None)
    phases = {
        "define": phase("define",
                        detected="define" if "define" in skills else ("issue-templates" if templates else None),
                        default="templates/issue-contract.md + `wave plan`"),
        "handoff": phase("handoff", detected="issue-handoff" if handoff else None, default="`wave prepare` (#14)"),
        "kickoff": phase("kickoff", detected=kick, default="the agent prompt's step 0"),
        "sync": phase("sync",
                      detected="make sync" if "sync" in targets else ("sync-main" if "sync-main" in commands else None),
                      default="git fetch --prune"),
        "rules": {"provider": declared_rules + [f for f in found_rules if f not in declared_rules],
                  "source": _src(bool(declared_rules), bool(found_rules))},
        "agent_skills": {"provider": extra_skills,
                         "source": _src(bool(declared_skills), bool([s for s in skills if s not in SKILLS_OF_A_PHASE]))},
        "review": phase("review", detected=[a for a in agents if fnmatch(a, "*review*") or fnmatch(a, "*-qa*")] or None,
                        default="poka-yoke re-audit before the PR"),
        "enforcement": phase("enforcement", detected=list(dict.fromkeys(h["path"] for h in hooks)) or None,
                             default="none (the gate is the enforcement)"),
    }
    if handoff and phases["handoff"]["source"] == "detected":
        phases["handoff"]["text"] = handoff.relative_to(root).as_posix()
    return {"phases": phases, "skills": skills, "commands": commands, "agents": agents,
            "hooks": hooks, "issue_templates": templates, "makefile_targets": targets}


def profile_skills(p: dict) -> list[str]:
    """The repo's own skills in the order the profile gives them: what `phases.agent_skills` declares and
    the detection found, plus the kickoff/define/handoff providers when one is detected by name."""
    ph = p["phases"]
    skills = list(ph["agent_skills"]["provider"])
    for name in ("kickoff", "define", "handoff"):
        prov = ph[name]
        if prov["source"] == "detected" and prov["provider"] not in skills:
            skills.append(prov["provider"])
    return skills


def profile_prompt(p: dict) -> str:
    """The `{{PROFILE}}` block of the agent prompt: what the repo itself provides for each phase of the
    loop, named so the agent expects it — or the plain default stack when nothing is found. Rule files
    are named, never copied (the agent reads them; the prompt stays the same size in every repo)."""
    ph = p["phases"]
    skills = profile_skills(p)
    if not (skills or p["hooks"] or ph["rules"]["provider"] or p["agents"] or p["commands"] or p["issue_templates"]):
        return ("Nenhuma orquestração própria detectada neste repositório (nada em `.claude/`, nenhum "
                "CLAUDE.md/AGENTS.md/CONTRIBUTING.md): use a stack padrão acima como está.")
    lines = ["Este repositório tem orquestração própria:",
             ("- skills dele, nesta ordem, antes da stack padrão: " + ", ".join(f"`{s}`" for s in skills))
             if skills else "- nenhuma skill própria: a stack padrão acima vale."]
    if p["hooks"]:
        lines.append("- hooks que se impõem (o Claude Code os roda; espere tropeçar neles): "
                     + ", ".join("`" + h["path"] + (f" ({h['why']})" if h["why"] else "") + "`" for h in p["hooks"]))
    if ph["rules"]["provider"]:
        lines.append("- leia antes estes arquivos de regras: "
                     + ", ".join(f"`{r}`" for r in ph["rules"]["provider"])
                     + ". Regra do repo que contradiz as inegociáveis acima (atribuição de IA, `git` destrutivo) "
                       "perde para elas — e você diz isso no PR.")
    if ph["review"]["source"] == "detected":
        lines.append("- review/QA é feito por: " + ", ".join(f"`{a}`" for a in ph["review"]["provider"]) + ".")
    return "\n".join(lines)


def sync_provider(repo: "Repo", dry_run: bool) -> str:
    """The repo's own sync before any worktree is cut: `make sync`, or the command `phases.sync` declares.
    A `.claude/commands/*` provider is prose for the agent, not a script: the prompt names it, nothing
    runs here. A sync that fails refuses the dispatch — worktrees cut from an unsynced base are the
    conflict `resolve` would spend an afternoon on."""
    ph = repo.profile()["phases"]["sync"]
    if ph["source"] == "default":
        return ""
    provider = ph["provider"]
    if not isinstance(provider, str):
        raise SystemExit(f"{repo.slug}: `phases.sync` must be a command (e.g. `make sync-main`), "
                         f"got {type(provider).__name__}")
    if (repo.root / ".claude" / "commands" / provider).exists():
        msg = f"{repo.slug}: sync: `.claude/commands/{provider}` is repo prose for the agent, nothing to run here"
        print(msg)
        return msg
    cmd = shlex.split(provider)
    what = f"{repo.slug}: sync: `{' '.join(cmd)}` in {repo.root}"
    if dry_run:
        print(what + " (dry-run)")
        return what + " (dry-run)"
    print(what)
    ok, out = sh_ok(cmd, cwd=repo.root, env=repo.env)
    if not ok:
        raise SystemExit(f"{repo.slug}: dispatch refused: the repo's sync provider `{' '.join(cmd)}` failed:\n{out}")
    return what


# ---------------------------------------------------------------- init (first run)

INIT_KEYS = ("gate", "base", "worktree_prefix", "protected", "serial")
PROTECTED_NAMES = {"safety", "security", "migrations", "auth"}
VERSION_FILES = ("VERSION", "version.txt", "Cargo.toml", "package.json", "pyproject.toml", "go.mod", "Package.swift", "Mix.exs")
WALK_SKIP = {".git", "node_modules", "target", "dist", "build", "vendor", "Pods", "DerivedData",
             ".venv", "venv", "__pycache__", ".build", ".codegraph", ".claude", ".github"}


def find_named_dirs(root: Path, names: set[str]) -> list[str]:
    """Repo-relative paths (trailing `/`) of the directories named in `names`: depth 3, heavy and hidden
    trees pruned, at most 20 hits — detection reads a repo, it never crawls one."""
    out: list[str] = []

    def walk(d: Path, rel: str, depth: int) -> None:
        if depth > 3 or len(out) >= 20:
            return
        for child in sorted(d.iterdir()):
            if not child.is_dir() or child.name in WALK_SKIP or child.name.startswith("."):
                continue
            r = f"{rel}/{child.name}" if rel else child.name
            if child.name in names:
                out.append(r + "/")
            walk(child, r, depth + 1)

    if root.is_dir():
        try:
            walk(root, "", 1)
        except OSError:
            pass
    return out


def detect_worktree_prefix(root: Path, homes: list[Path] | None = None) -> tuple[str | None, str]:
    """(prefix, evidence) from the convention sibling directories show: `<repo>-<digits>` next to the
    checkout, or in a common parent such as `~/*-worktrees/`. (None, why) when nothing is on disk."""
    name = root.name
    try:
        sibs = sorted(d.name for d in root.parent.iterdir()
                      if d.is_dir() and re.fullmatch(re.escape(name) + r"-\d+", d.name))
    except OSError:
        sibs = []
    if sibs:
        return f"../{name}-", f"siblings {', '.join(sibs)} in {root.parent}"
    for home in [Path.home()] if homes is None else homes:
        for base in sorted(p for p in home.glob("*-worktrees") if p.is_dir()):
            try:
                hits = sorted(d.name for d in base.iterdir()
                              if d.is_dir() and re.fullmatch(re.escape(name) + r"-\d+", d.name))
            except OSError:
                continue
            if hits:
                where = f"~/{base.relative_to(home).as_posix()}"
                return f"{where}/{name}-", f"{', '.join(hits)} in {where}/"
    return None, "no <repo>-<digits> sibling next to the checkout or in a ~/*-worktrees/ parent"


def init_proposal(repo: "Repo") -> dict:
    """The entry `wave init` proposes, one row per key: {key: {"value", "source", "why"}} — source is
    `detected` when a file in the repo says so, `guessed` when only convention does. The guessed rows
    are the only thing init may ask about; nothing here is answered by running anything."""
    gate, gsrc, gwhy = repo.gate_proposal()
    prefix, wwhy = detect_worktree_prefix(repo.root)
    named = find_named_dirs(repo.root, PROTECTED_NAMES)
    serial = [p for p in named if p.rstrip("/").rsplit("/", 1)[-1] == "migrations"] \
        + [f for f in VERSION_FILES if (repo.root / f).exists()]
    return {
        "gate": {"value": gate, "source": gsrc, "why": gwhy},
        "base": {"value": repo.base, "source": "detected", "why": "GitHub default branch"},
        "worktree_prefix": {"value": prefix, "source": "detected" if prefix else "guessed",
                            "why": wwhy if prefix else wwhy + ": the built-in default ../<repo>-<N> assumed"},
        "protected": {"value": named, "source": "detected",
                      "why": "directories named safety/security/migrations/auth" if named
                             else "none of safety/security/migrations/auth found"},
        "serial": {"value": serial, "source": "detected",
                   "why": "migrations directories, version files" if serial
                          else "no migrations directory, no version file"},
    }


def render_proposal(repo: "Repo", prop: dict) -> str:
    """The one proposal block: the `.wave.json`/registry entry init would write, every line marked,
    plus the profile (#15) — gathered for the human, never written (detection keeps reading the repo)."""
    lines = [f"wave init {repo.slug} — proposal, the entry it would write (each line detected or guessed):", ""]
    for k, row in prop.items():
        lines.append(f'  "{k}": {json.dumps(row["value"], ensure_ascii=False)}  # {row["source"]} — {row["why"]}')
    ph = repo.profile()["phases"]
    lines += ["", "profile (gathered, never written):"]
    for name in ("define", "handoff", "kickoff", "sync", "rules", "agent_skills", "review", "enforcement"):
        prov = ph[name]["provider"]
        if prov:
            text = prov if isinstance(prov, str) else ", ".join(str(x) for x in prov)
            lines.append(f"  {name}: {text} ({ph[name]['source']})")
    return "\n".join(lines)


def unconfirmed(repo: "Repo") -> bool:
    """Whether the repo still owes init its one answer: no `.wave.json` anywhere (a repo that has one is
    never asked anything), no `confirmed_at` on its registry entry (an answer is never re-proposed), and
    no key typed by hand — `repos add --gate ...` is an answer too."""
    if repo.local_cfg is not None or repo.remote_cfg is not None:
        return False
    if repo.entry.get("confirmed_at"):
        return False
    return not any(k in repo.entry for k in INIT_KEYS)


def write_proposal_file(repo: "Repo", prop: dict) -> Path:
    """The proposal as a file the SKILL reads and edits before `--from` writes it back: no stdin — a
    proposal file is the same artifact in every host."""
    repo.handoffs.mkdir(parents=True, exist_ok=True)
    path = repo.handoffs / "init-proposal.json"
    path.write_text(json.dumps({k: row["value"] for k, row in prop.items() if row["value"] is not None}, indent=2) + "\n")
    return path


def accept_proposal(repo: "Repo", values: dict) -> dict:
    """Write the answer: the registry entry with `confirmed_at`, and — unless the repo already has one —
    a `.wave.json` at the root, untracked, ready for the small PR that shares it. Empty values never write."""
    reg = load_registry()
    entry = reg["repos"].setdefault(repo.slug, {})
    for k, v in values.items():
        if v:
            entry[k] = v
    entry["confirmed_at"] = now_iso()
    save_registry(reg)
    local = repo.root / ".wave.json"
    if local.exists():
        note = f"; {local} already exists, not overwritten — the registry entry carries the accepted values"
    else:
        shareable = {k: v for k, v in values.items() if v}
        note = f"; {local} written (untracked) — a small PR adding it gives the next person the answer for free" if shareable else ""
        if shareable:
            local.write_text(json.dumps(shareable, indent=2) + "\n")
    print(f"{repo.slug}: accepted — registry entry confirmed at {entry['confirmed_at']}{note}")
    return entry


def cmd_init(default: Repo | None, a):
    """First run in a repo: gather everything detectable, print one proposal (each line detected or
    guessed), and write only an accepted answer — `--yes`, or `--from <file>` with the SKILL's edit.
    A repo with a `.wave.json` or a `confirmed_at` entry is never asked again; `--again` reopens."""
    repo = Repo.get(a.slug) if a.slug else default
    if repo is None:
        raise SystemExit("run inside the repo, pass --repo, or name owner/name")
    if a.from_file is None and not a.again and not unconfirmed(repo):
        when = load_registry()["repos"].get(repo.slug, {}).get("confirmed_at")
        print(f"{repo.slug}: answered already" + (f" (confirmed_at {when})" if when else " (it carries a `.wave.json` or a declared entry)")
              + " — nothing is asked twice; `wave init --again` reopens")
        return
    prop = init_proposal(repo)
    print(render_proposal(repo, prop))
    if a.yes or a.from_file is not None:
        if a.from_file is not None:
            try:
                values = json.loads(Path(a.from_file).read_text())
            except (OSError, ValueError) as e:
                raise SystemExit(f"init refused: cannot read the proposal {a.from_file} ({e})")
            if not isinstance(values, dict):
                raise SystemExit(f"init refused: {a.from_file} must be a JSON object of the proposal's keys")
        else:
            values = {k: row["value"] for k, row in prop.items()}
        bad = [k for k in values if k not in INIT_KEYS]
        if bad:
            raise SystemExit(f"init refused: {', '.join(bad)} — only {', '.join(INIT_KEYS)} are init's to write")
        accept_proposal(repo, values)
        return
    pf = write_proposal_file(repo, prop)
    guessed = [k for k, row in prop.items() if row["source"] == "guessed"]
    if guessed:
        print(f"{len(guessed)} guessed line(s) ({', '.join(guessed)}) — the one question this tool may ask: "
              f"accept, or edit a line in {pf} and run `wave init --from {pf}`; skip, and nothing is written")
    else:
        print(f"nothing guessed: every line was read from the repo — `wave init --yes` accepts; the proposal file is {pf}")


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


# ---------------------------------------------------------------- Done-when ledger

DONE_WHEN = re.compile(r"^## Done when[ \t]*$")
NEAR_DONE_WHEN = re.compile(r"^#{1,6} .*done when", re.I)
HEADING = re.compile(r"^#{1,6} ")
ITEM = re.compile(r"^([-*] \[)([ xX])(\] )(.*?)([ \t]*\r?\n?)$")
WHY = re.compile(r"\s+—\s+(?:dropped|not done):.*$")


def norm(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------- typed issue templates

ISSUE_TYPES = ("bug", "story", "chore", "feature")  # labels; each type's template: templates/issue/<type>.md


def issue_type(iss: dict) -> str | None:
    """The issue's type, from its labels (`bug` -> templates/issue/bug.md); None when no type label is set."""
    names = {l["name"] for l in iss.get("labels", [])}
    return next((t for t in ISSUE_TYPES if t in names), None)


def section(body: str, name: str) -> str | None:
    """The text of the `## name` section, up to the next heading; None when the heading is not there."""
    lines = body.splitlines()
    start = next((i for i, l in enumerate(lines) if l.rstrip() == name), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if HEADING.match(lines[i])), len(lines))
    return "\n".join(lines[start + 1:end]).strip()


def lint_type(body: str, typ: str) -> list[str]:
    """What the type's template demands beyond the common contract, each miss named by its section."""
    if typ == "bug":
        rep = section(body, "## Reprodução")
        if rep is None:
            return ["missing `## Reprodução`: the steps to reproduce and the observed output"]
        if not (re.search(r"^\s*(?:[-*]|\d+[.)]) ", rep, re.M) or "```" in rep):
            return ["`## Reprodução` has no steps: list them in order, or paste the session"]
    elif typ == "story":
        out = []
        for i, item in enumerate(done_when(body) or [], 1):
            short = item["key"][:40] + ("…" if len(item["key"]) > 40 else "")
            if not re.search(r"—\s*owner:", item["text"]):
                out.append(f"Done when item {i} ({short}) has no `— owner:`")
            if "`" not in item["text"]:
                out.append(f"Done when item {i} ({short}) is not observable: name the test or the command that proves it")
        return out
    elif typ == "chore":
        if not section(body, "## Por que não é story"):
            return ["missing `## Por que não é story`: one line on why this is a chore and not a story"]
    elif typ == "feature":
        fatia = section(body, "## Fatia")
        if fatia is None:
            return ["missing `## Fatia`: the stories that slice this feature, one per line"]
        if not re.search(r"^[-*] ", fatia, re.M):
            return ["`## Fatia` names no stories: one bullet each, they become the sub-issues"]
    return []


def done_when(body: str) -> list[dict] | None:
    """The items of the first `## Done when` section (exactly that heading, at the start of a line), up to the next
    heading: each with its line index, raw text, state (done | dropped | open) and key (the item's own words,
    whitespace normalised, without the strike or a `— dropped:` / `— not done:` reason). None when there is no such heading."""
    lines = body.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if DONE_WHEN.match(l.rstrip("\r\n"))), None)
    if start is None:
        return None
    items = []
    for i in range(start + 1, len(lines)):
        if HEADING.match(lines[i]):
            break
        m = ITEM.match(lines[i])
        if not m:
            continue  # prose, a blank line, or an indented line under an item (a why): not an item
        text = m.group(4)
        struck = re.match(r"~~(.+?)~~", text)
        state = "done" if m.group(2) in "xX" else "dropped" if struck else "open"
        items.append({"line": i, "text": text, "state": state, "key": norm(struck.group(1) if struck else WHY.sub("", text))})
    return items


def ledger_check(pr_body: str, issue_body: str) -> tuple[str, str] | None:
    """None when the PR's ledger lists the issue's Done-when items, in order; else (flag, what is wrong)."""
    want = done_when(issue_body)
    if want is None:
        return ("ISSUE-NO-DONE-WHEN", "the issue has no `## Done when` heading at the start of a line, so there is nothing to declare against: fix the issue body")
    have = done_when(pr_body)
    if have is None:
        near = next((l.strip() for l in pr_body.splitlines() if NEAR_DONE_WHEN.match(l)), None)
        return ("LEDGER-MISSING", (f"the heading must be exactly `## Done when`, found `{near}`" if near else "no `## Done when` section")
                + ": copy the issue's list, each item `- [x]`, `- [ ] item — not done: why`, or `- [ ] ~~item~~ — dropped: why`")
    w, h = [i["key"] for i in want], [i["key"] for i in have]
    if w == h:
        return None
    missing, extra = [k for k in w if k not in h], [k for k in h if k not in w]
    return ("LEDGER-MISMATCH", "; ".join([f"missing: {k}" for k in missing] + [f"extra: {k}" for k in extra]) or "same items, another order")


def tick_body(body: str, done: list[str], strike: list[tuple[str, str]]) -> str:
    """`body` with the named open items ticked (`- [x]`) or struck (`- [ ] ~~item~~ — dropped: why`); an item is named
    by its 1-based index or its text (whitespace normalised). Only those lines change, and only if still open: every
    other byte stays. Raises ValueError for a body without `## Done when`, an unknown item, or a strike without a reason."""
    items = done_when(body)
    if items is None:
        raise ValueError("the issue has no `## Done when` heading at the start of a line")

    def find(ref: str) -> dict:
        if ref.strip().isdigit():
            if not 1 <= int(ref) <= len(items):
                raise ValueError(f"no item {ref}: the list has {len(items)}")
            return items[int(ref) - 1]
        hit = next((i for i in items if i["key"] == norm(ref)), None)
        if hit is None:
            raise ValueError(f"no item {ref!r} in `## Done when`")
        return hit

    both = {find(r)["line"] for r in done} & {find(r)["line"] for r, _ in strike}
    if both:
        raise ValueError("an item cannot be both done and struck: " + ", ".join(repr(i["key"]) for i in items if i["line"] in both))
    lines = body.splitlines(keepends=True)
    for ref in done:
        it = find(ref)
        if it["state"] == "open":
            m = ITEM.match(lines[it["line"]])
            lines[it["line"]] = m.group(1) + "x" + lines[it["line"]][m.end(2):]
    for ref, why in strike:
        if not why.strip():
            raise ValueError(f"striking {ref!r} needs a reason (--why)")
        it = find(ref)
        if it["state"] == "open":
            m = ITEM.match(lines[it["line"]])
            lines[it["line"]] = f"{m.group(1)} {m.group(3)}~~{m.group(4)}~~ — dropped: {norm(why)}{m.group(5)}"
    return "".join(lines)


def ledger_ticks(pr_body: str) -> tuple[list[str], list[tuple[str, str]]]:
    """What a PR's ledger declares: the keys it ticked, and the keys it struck with their reasons."""
    items = done_when(pr_body) or []
    struck = []
    for i in items:
        if i["state"] == "dropped":
            m = re.search(r"—\s+dropped:\s*(.*)$", i["text"])
            struck.append((i["key"], m.group(1) if m and m.group(1).strip() else "dropped in the PR's ledger"))
    return [i["key"] for i in items if i["state"] == "done"], struck


def untick_body(body: str, refs: list[str]) -> str:
    """`body` with the named done items unticked back to `- [ ]`: the reverse of tick_body, for a done claim a
    sweep proved false. Only those lines change, and only while still done. Raises ValueError for a body without
    `## Done when` or an unknown item."""
    items = done_when(body)
    if items is None:
        raise ValueError("the body has no `## Done when` heading at the start of a line")

    def find(ref: str) -> dict:
        if ref.strip().isdigit():
            if not 1 <= int(ref) <= len(items):
                raise ValueError(f"no item {ref}: the list has {len(items)}")
            return items[int(ref) - 1]
        hit = next((i for i in items if i["key"] == norm(ref)), None)
        if hit is None:
            raise ValueError(f"no item {ref!r} in `## Done when`")
        return hit

    lines = body.splitlines(keepends=True)
    for ref in refs:
        it = find(ref)
        if it["state"] == "done":
            m = ITEM.match(lines[it["line"]])
            lines[it["line"]] = m.group(1) + " " + lines[it["line"]][m.end(2):]
    return "".join(lines)


def tally(items: list[dict]) -> dict[str, int]:
    return {s: sum(1 for i in items if i["state"] == s) for s in ("done", "dropped", "open")}


# ---------------------------------------------------------------- body surgery for prepare / amend

HANDOFF = re.compile(r"^## Handoff[ \t]*$")
PREPARE_MARK = "<!-- written by `wave prepare` -->"


def insert_section(body: str, heading: re.Pattern, block: str) -> str:
    """`body` with `block` (a full `## H` section, ending in one newline) replacing the first section
    whose heading matches, in place, or appended after a blank line when there is none. Every byte
    outside the section stays: an existing block is updated where it stands, never moved."""
    if not body:
        return block
    lines = body.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if heading.match(l.rstrip("\r\n"))), None)
    if start is None:
        return (body if body.endswith("\n") else body + "\n") + "\n" + block
    end = next((i for i in range(start + 1, len(lines)) if HEADING.match(lines[i])), len(lines))
    return "".join(lines[:start] + block.splitlines(keepends=True) + lines[end:])


def insert_before(body: str, heading: re.Pattern, block: str) -> str:
    """`body` with `block` inserted ahead of the first line whose heading matches — never replacing
    anything — or appended after a blank line at the end when there is none. Every byte of `body`
    keeps its place."""
    if not body:
        return block
    lines = body.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if heading.match(l.rstrip("\r\n"))), None)
    if start is None:
        return (body if body.endswith("\n") else body + "\n") + "\n" + block
    return "".join(lines[:start] + block.splitlines(keepends=True) + lines[start:])


def with_done_when(body: str, items: str) -> str:
    """`body` with a `## Done when` section carrying `items`, written once — before the Handoff when
    there is one, at the end when not. Raises ValueError when the body already has the section: the
    owner answered once, and an answer is never overwritten by a second one."""
    if done_when(body) is not None:
        raise ValueError("the body already has a `## Done when` — amend writes it once, it never overwrites")
    return insert_before(body, HANDOFF, "## Done when\n\n" + items.rstrip("\n") + "\n\n")


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
        items = done_when(iss.get("body") or "")
        done = bool(items) and all(i["state"] != "open" for i in items)  # open, yet nothing left to do: not dispatched
        lv = liveness(repo, n, wt) if wt.exists() else None
        state = ("blocked" if blockers else "in-progress" if pr else "done-unclosed" if done
                 else "agent-exhausted" if lv and no_progress(lv) >= RETRY_CEILING
                 else "worktree" if wt.exists() else "ready")
        out.append({"key": nd["key"], "repo": repo.slug, "number": n, "title": iss["title"], "branch": branch_for(iss),
                    "blocked_by": blockers, "pr": pr["number"] if pr else None,
                    "worktree": str(wt) if wt.exists() else None, "done": done, "state": state,
                    "liveness": lv,
                    "ready": state == "ready"})
    # every open leaf is in exactly one state: `state` is a single value, so the states cannot overlap;
    # what a later edit could break is READY, so READY must mean "nothing else holds", and nothing less
    assert all(c["ready"] == (not c["blocked_by"] and not c["pr"] and not c["worktree"] and not c["done"]) for c in out), \
        [c["key"] for c in out if c["ready"] != (not c["blocked_by"] and not c["pr"] and not c["worktree"] and not c["done"])]
    return out


def premises_for(repo: Repo, n: int) -> list[tuple[str, bool, str]]:
    """The syllogism behind READY: each premise with its evidence. Conclusion = all hold."""
    iss = repo.issue(n)
    subs = repo.sub_issues(n)
    bl = repo.blocked_by(n)
    open_bl = [b for b in bl if b["state"] == "open"]
    pr = pr_for_issue(repo, n)
    wt = repo.worktree(n)
    lv = liveness(repo, n, wt) if wt.exists() else None
    items = done_when(iss.get("body") or "")
    left = [i for i in items or [] if i["state"] == "open"]
    return [
        ("it is a leaf (no sub-issues)", not subs, f"{len(subs)} sub-issues" if subs else "none"),
        ("it is open", iss["state"] == "open", iss["state"]),
        ("every blocker is closed", not open_bl, ", ".join(f"{slug_of_api(b['repository_url'])}#{b['number']} {b['state']}" for b in bl) or "no blockers"),
        ("no open PR closes it", pr is None, f"PR #{pr['number']} ({pr['headRefName']})" if pr else "none"),
        ("no worktree exists for it", not wt.exists(), f"{wt} ({lv['verdict']}, nothing changed for {lv['idle_min']} min)" if lv else str(wt)),
        ("an item of its Done when is still open", not items or bool(left),
         f"{len(left)} of {len(items)} open" if items else "no `## Done when` list"),
    ]


def why(c: dict) -> str:
    if c["ready"]:
        return "READY"
    if c["blocked_by"]:
        return "blocked by " + ", ".join(c["blocked_by"])
    if c["pr"]:
        return f"in progress (PR #{c['pr']})"
    if c.get("done"):
        return "done-unclosed: every Done-when item is ticked or struck — close it or add an item"
    lv = c.get("liveness")
    if c.get("state") == "agent-exhausted":
        extra = f", {lv['respawns']} with a new strategy" if lv.get("respawns") else ""
        return (f"agent-exhausted: {lv['resumes']} resumes without a new commit{extra} — dispatch refuses; "
                "`wave sweep --fix` escalates, the owner decides")
    return f"worktree exists ({lv['verdict']}, nothing changed for {lv['idle_min']} min)" if lv else "worktree exists"


# ---------------------------------------------------------------- liveness

def verdict(idle_min: float, has_pr: bool) -> str:
    if has_pr:
        return "done"
    return "working" if idle_min < WORKING_MIN else "quiet" if idle_min <= DEAD_MIN else "likely dead"


def _epoch(iso: str | None) -> float | None:
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() if iso else None
    except (TypeError, ValueError):
        return None


def last_activity(repo: Repo, wt: Path, dispatched: float | None) -> float:
    """Epoch of the newest trace of work in a worktree: the dispatch, a commit on its branch, a file changed or
    created (ignored files aside, so build output does not count). The directory's own mtime when there is none."""
    times = [dispatched] if dispatched else []
    rc, out, _ = run(["git", "ls-files", "-z", "-m", "-o", "--exclude-standard"], cwd=wt, env=repo.env)
    for f in [f for f in out.split("\0") if f] if rc == 0 else []:
        try:
            times.append((wt / f).stat().st_mtime)
        except OSError:
            pass  # deleted: `-m` lists it, it has no mtime
    rc, out, _ = run(["git", "log", "-1", "--format=%ct", f"origin/{repo.base}..HEAD"], cwd=wt, env=repo.env)
    if rc == 0 and out.strip():
        times.append(float(out.strip()))
    return max(times) if times else wt.stat().st_mtime


def stamp_of(wt: Path) -> dict:
    """The dispatch stamp of a worktree, {} when there is none or it is not JSON."""
    try:
        return json.loads((wt / STAMP).read_text())
    except (OSError, ValueError):
        return {}


def no_progress(stamp: dict) -> int:
    """Dispatches over a worktree without a new commit: plain resumes plus respawns with a new strategy alike.
    The retry ceiling counts the sum — a strategy change is not progress."""
    return stamp.get("resumes", 0) + stamp.get("respawns", 0)


def next_resume_counts(repo: Repo, wt: Path, stamp: dict, respawn: bool) -> tuple[int, int]:
    """The stamp's (resumes, respawns) after one more dispatch in this worktree: a commit on the branch newer
    than the last dispatch is progress and resets both; a dispatch over no progress is one more plain resume,
    or one more respawn when the orchestrator returns with a new strategy after a repeated failure. No stamp
    means a first agent here, not a resume."""
    dispatched = _epoch(stamp.get("dispatched_at"))
    if dispatched is None:
        return (0, 0)
    rc, out, _ = run(["git", "log", "-1", "--format=%ct", f"origin/{repo.base}..HEAD"], cwd=wt, env=repo.env)
    last = float(out.strip()) if rc == 0 and out.strip() else None
    if last is not None and last > dispatched:
        return (0, 0)
    return (stamp.get("resumes", 0) + (0 if respawn else 1),
            stamp.get("respawns", 0) + (1 if respawn else 0))


def liveness(repo: Repo, n: int, wt: Path | None = None) -> dict:
    """What an issue's worktree says about its agent. Nothing but files and git is read: no process is looked at."""
    wt = wt or repo.worktree(n)
    stamp = stamp_of(wt)
    now = time.time()
    dispatched = _epoch(stamp.get("dispatched_at"))
    idle = (now - last_activity(repo, wt, dispatched)) / 60
    pr = pr_for_issue(repo, n)
    return {"issue": n, "worktree": str(wt), "age_min": round((now - dispatched) / 60) if dispatched else None,
            "idle_min": round(idle), "pr": pr["number"] if pr else None, "resumes": stamp.get("resumes", 0),
            "respawns": stamp.get("respawns", 0), "verdict": verdict(idle, bool(pr))}


def issue_worktrees(repo: Repo) -> list[tuple[Path, str, int]]:
    """(path, branch, issue) of every worktree named after an issue (`<prefix>N`), the main checkout aside."""
    out = []
    for block in repo.git(["worktree", "list", "--porcelain"]).split("\n\n"):
        m = re.search(r"^worktree (.+)$", block, re.M)
        b = re.search(r"^branch refs/heads/(.+)$", block, re.M)
        mm = m and re.match(rf"{re.escape(repo.worktree(0).name[:-1])}(\d+)$", Path(m.group(1)).name)
        if mm and b and Path(m.group(1)) != repo.root:
            out.append((Path(m.group(1)), b.group(1), int(mm.group(1))))
    return out


def cargo_home() -> Path:
    return Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")


def broken_crates(root: Path, home: Path) -> tuple[int, list[tuple[Path, list[str], int]]]:
    """(crates checked, [(directory, files missing, files in the archive)]) for the registry crates `Cargo.lock` names.

    `.cargo-ok` is cargo's "extraction complete" marker, and it once sat on a directory that lacked two files: so a
    marked directory is checked against its `.crate` archive, file by file. An unmarked one is skipped (cargo removes
    and re-extracts it by itself), and so is one whose archive is gone (nothing to compare with)."""
    wanted = {f"{m.group(1)}-{m.group(2)}" for m in LOCK_PACKAGE.finditer((root / "Cargo.lock").read_text())}
    checked, broken = 0, []
    for d in sorted((home / "registry" / "src").glob("*/*")):
        archive = home / "registry" / "cache" / d.parent.name / f"{d.name}.crate"
        if d.name not in wanted or not (d / ".cargo-ok").exists() or not archive.exists():
            continue
        try:
            with tarfile.open(archive) as tar:
                files = [m.name.split("/", 1)[1] for m in tar if m.isfile() and m.name.startswith(d.name + "/")]
        except (tarfile.TarError, OSError, EOFError):
            continue  # an unreadable archive: cargo verifies its checksum before it trusts it, so do not guess here
        checked += 1
        missing = [f for f in files if not (d / f).exists()]
        if missing:
            broken.append((d, missing, len(files)))
    return checked, broken


# ---------------------------------------------------------------- prompt

def remaining(body: str) -> str:
    """The prompt's Remaining section: the issue's open items only, and the rule for the others."""
    items = done_when(body)
    if items is None:
        return ("A issue não tem uma linha `## Done when` no início de linha: `verify` recusará o PR com `ISSUE-NO-DONE-WHEN`. "
                "Não edite o corpo da issue; trabalhe pelo texto dela e diga isso no relatório — o dono conserta o corpo.")
    left = [i for i in items if i["state"] == "open"]
    if not left:
        return "Nada falta: todo item está marcado ou riscado (`done-unclosed`). Não implemente nada; relate e pare."
    t = tally(items)
    return ("checked is done — do not redo, do not re-verify unless the gate fails; struck is out — do not reopen.\n"
            f"({t['done']} done, {t['dropped']} dropped, {len(left)} left of {len(items)}; o ledger do PR lista os {len(items)}, na ordem da issue.)\n\n"
            + "".join(f"- [ ] {i['text']}\n" for i in left))


def render_prompt(repo: Repo, iss: dict, branch: str, sha: str, parallel: int) -> str:
    tpl = (TEMPLATES / "agent.md").read_text()
    n = iss["number"]
    fields = {
        "WAVE": str(Path(__file__).resolve()), "REMAINING": remaining(iss.get("body") or "").rstrip("\n"),
        "N": str(n), "TITLE": iss["title"], "URL": iss["html_url"], "REPO": repo.slug,
        "ROOT": str(repo.root), "WORKTREE": str(repo.worktree(n)), "BRANCH": branch,
        "BASE": repo.base, "SHA": sha, "DATE": datetime.now().strftime("%Y-%m-%d"),
        "GATE": "\n".join(repo.gate()), "GATE_ONE_LINE": " && ".join(repo.gate()),
        "PROTECTED": ", ".join(f"`{p}`" for p in repo.protected) or "(nenhum declarado em .wave.json / registro)",
        "CODEGRAPH_QUERY": codegraph_query(iss),
        "CODEGRAPH_NOTE": "o repo tem `.codegraph/`; use `codegraph explore \"...\"` antes de grep/Read" if repo.has_codegraph()
                          else "sem índice CodeGraph aqui; se `codegraph` existir no PATH, rode `codegraph init .` na worktree primeiro",
        "ACCOUNT": f"conta gh `{repo.account}` (use `GH_TOKEN=$(gh auth token --user {repo.account})` nos comandos gh)" if repo.account else "conta gh ativa",
        "PROFILE": profile_prompt(repo.profile()),
        "PREPARED": prepared_note(iss.get("body") or ""),
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
        also: dict[str, list[Path]] = {}
        for base in reg["search_paths"]:
            for d in sorted(Path(base).expanduser().glob("*")):
                if not (d / ".git").is_dir():
                    continue
                ok, url = sh_ok(["git", "remote", "get-url", "origin"], cwd=d)
                slug = slug_of_url(url) if ok else None
                if not slug:
                    continue
                entry = reg["repos"].get(slug)
                here = d.resolve()
                if entry is None or "path" not in entry or not Path(entry["path"]).exists():
                    # a new repo, or a registered one whose path is gone — the same repair Repo.get makes
                    if entry is None:
                        reg["repos"][slug] = {"path": str(here)}
                    else:
                        entry["path"] = str(here)
                    added += 1
                elif Path(entry["path"]).resolve() != here:
                    also.setdefault(slug, []).append(here)  # the registry wins; a second checkout is reported, not chosen
        save_registry(reg)
        for slug, paths in sorted(also.items()):
            print(f"{slug}: another checkout at {', '.join(map(str, paths))} — the registry keeps {reg['repos'][slug]['path']}")
        print(f"scanned {reg['search_paths']}: {added} new, {len(reg['repos'])} total")


def cmd_facts(default: Repo | None, a):
    repos = [Repo.get(s) for s in a.slugs] if a.slugs else [default] if default else []
    for r in repos:
        print(json.dumps({"slug": r.slug, "root": str(r.root), "account": r.account or "(active)", "base": r.base,
                          "config_source": r.cfg_source, "gate": r.gate(), "gate_sources": dict(r.gate_sources()), "protected": r.protected, "codegraph": r.has_codegraph(),
                          "profile": r.profile(),
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


def epics_of(repo: Repo) -> list[dict]:
    """The repo's open epic roots: an open issue that has sub-issues or carries the `epic` label, minus
    every issue that is itself a sub-issue — only roots count. Every issue is fetched (`--state all`) so
    the open filter is enforced here, not assumed from the query: a closed epic can never slip in."""
    found = json.loads(repo.gh(["issue", "list", "-R", repo.slug, "--state", "all", "--limit", "500",
                                "--json", "number,title,labels,state"]))
    open_issues = [i for i in found if i["state"] == "open"]
    with ThreadPoolExecutor(max_workers=12) as pool:
        subs = list(pool.map(lambda i: repo.sub_issues(i["number"]), open_issues))
    child = {(slug_of_api(c["repository_url"]), int(c["number"])) for ss in subs for c in ss}
    return [iss for iss, ss in zip(open_issues, subs)
            if (ss or "epic" in {l["name"] for l in iss.get("labels", [])})
            and (repo.slug, iss["number"]) not in child]


def epic_stats(repo: Repo, iss: dict) -> dict:
    """One `wave epics` row: the epic's leaves and where they stand (the `candidates` states, summed),
    and the date of the newest `wave status --post` comment — the paper trail left on the epic."""
    nodes = tree(repo, iss["number"])
    cands = candidates(nodes)
    lvs = leaves(nodes)
    comments = repo.api(f"repos/{repo.slug}/issues/{iss['number']}/comments?per_page=100", paginate=True) or []
    last = max((c["created_at"] for c in comments if (c.get("body") or "").startswith("## wave status")), default=None)
    return {"epic": key(repo, iss["number"]), "repo": repo.slug, "number": iss["number"], "title": iss["title"],
            "leaves": len(lvs), "closed": sum(1 for nd in lvs if nd["issue"]["state"] == "closed"),
            "ready": sum(1 for c in cands if c["ready"]), "blocked": sum(1 for c in cands if c["state"] == "blocked"),
            "in_progress": sum(1 for c in cands if c["state"] == "in-progress"),
            "last_status": last, "command": f"/setwave:wave {key(repo, iss['number'])}"}


def cmd_epics(default: Repo | None, a):
    """`wave epics`: every open epic across the registry's repos (or --slug / --repo for one), readiest first."""
    if a.slug and getattr(a, "repo", None):
        raise SystemExit("give --slug or --repo, not both")
    if a.slug:
        found = [epic_stats(Repo.get(a.slug), iss) for iss in epics_of(Repo.get(a.slug))]
    elif getattr(a, "repo", None):
        r = Repo.from_cwd(a.repo)
        found = [epic_stats(r, iss) for iss in epics_of(r)]
    else:
        found = []
        for slug in sorted(load_registry()["repos"]):
            try:  # one stale checkout or unreadable repo must not blind the whole overview: name it, move on
                r = Repo.get(slug)
                found += [epic_stats(r, iss) for iss in epics_of(r)]
            except SystemExit as e:
                print(f"skip {slug}: {str(e).strip().splitlines()[-1] if str(e).strip() else e}", file=sys.stderr)
    found.sort(key=lambda s: (-s["ready"], s["repo"], s["number"]))
    if a.json:
        print(json.dumps(found, indent=2))
        return
    if not found:
        print("no open epics — an epic is an open issue with sub-issues or the `epic` label; `wave plan` creates one")
        return
    print(f"{len(found)} open epic(s) across {len({s['repo'] for s in found})} repo(s), readiest first")
    for s in found:
        print(f"  {s['epic']}  {s['title']} — ready {s['ready']} · blocked {s['blocked']} · in progress {s['in_progress']}"
              f" · leaves {s['closed']}/{s['leaves']} closed · last status {s['last_status'] or 'never'}"
              f" — {s['command']}")


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
        sync_provider(repo, a.dry_run)
        repo.handoffs.mkdir(exist_ok=True)
        if a.warm:  # archives downloaded once, here, so the worktrees' parallel builds only extract
            if not (repo.root / "Cargo.toml").exists():
                print(f"{repo.slug}: --warm: no Cargo.toml, nothing to fetch")
                continue
            if a.dry_run:
                print(f"{repo.slug}: --warm: would run `cargo fetch` in {repo.root}")
                continue
            ok, out = sh_ok(["cargo", "fetch"], cwd=repo.root) if shutil.which("cargo") else (False, "cargo is not on PATH")
            if not ok:
                raise SystemExit(f"dispatch refused: `cargo fetch` failed in {repo.root}, no worktree created:\n{out}")
            print(f"{repo.slug}: cargo fetch in {repo.root}: done")
    for repo, n in targets:
        iss = repo.issue(n)
        if not a.no_prepare and not a.dry_run and "## Handoff" not in (iss.get("body") or ""):
            try:
                iss, _, _ = prepare_issue(repo, n)
                print(f"{key(repo, n)}: no `## Handoff` in the body — prepare wrote it")
            except SystemExit as e:  # the write is an upgrade, not a precondition: the prompt carries the same facts
                print(f"{key(repo, n)}: no `## Handoff` in the body and prepare could not write it "
                      f"({str(e).strip().splitlines()[-1] if str(e).strip() else e}) — dispatching anyway", file=sys.stderr)
        elif a.dry_run and not a.no_prepare and "## Handoff" not in (iss.get("body") or ""):
            print(f"{key(repo, n)}: dry run, nothing written; a real dispatch would run `wave prepare {n}` first")
        branch = branch_for(iss)
        wt = repo.worktree(n)
        sha = repo.origin_sha()
        resumes, respawns = 0, 0
        if not a.dry_run:
            if wt.exists():
                lv = liveness(repo, n, wt)  # --force over a live agent would put a second one in the same tree: say what this one looks like
                print(f"{key(repo, n)}: worktree exists at {wt}, keeping it ({lv['verdict']}, changed {lv['idle_min']} min ago)"
                      + (" — respawn with a new strategy" if a.respawn else ""))
                resumes, respawns = next_resume_counts(repo, wt, stamp_of(wt), a.respawn)  # a dispatch over a worktree is a resume, or a respawn when --respawn says this one returns with a new strategy; a commit since the last one is progress
                if resumes + respawns > RETRY_CEILING:
                    raise SystemExit(f"{key(repo, n)}: agent-exhausted: {resumes + respawns - 1} dispatches without a new commit "
                                     f"({resumes} resumes, {respawns} respawns; nothing changed for {lv['idle_min']} min) — "
                                     f"dispatch refused; `wave sweep <epic> --fix` comments the escalation, the owner decides")
            else:
                ok, out = repo.git_ok(["worktree", "add", "-q", "-b", branch, str(wt), f"origin/{repo.base}"])
                if not ok:
                    print(f"{key(repo, n)}: worktree add failed:\n{out}")
                    continue
                if sh_ok(["which", "codegraph"])[0]:
                    sh_ok(["codegraph", "init", "."], cwd=wt)
        path = repo.handoffs / f"{n}.md"
        path.write_text(render_prompt(repo, iss, branch, sha, len(targets)))
        if not a.dry_run:  # the stamp `agents`, `doctor` and the retry ceiling read; a re-dispatch (--force) is a new agent, so a new stamp
            (wt / STAMP).write_text(json.dumps({"issue": n, "repo": repo.slug, "dispatched_at": now_iso(), "prompt": str(path),
                                                "resumes": resumes, "respawns": respawns}, indent=2) + "\n")
            totals = getattr(a, "stamp_totals", {"resumes": 0, "respawns": 0})  # logged for `wave stats`: the waves' escalation mix
            a.stamp_totals = {"resumes": totals["resumes"] + resumes, "respawns": totals["respawns"] + respawns}
            ok, excl = repo.git_ok(["rev-parse", "--git-path", "info/exclude"], cwd=wt)  # shared by every worktree
            excl_path = wt / excl.strip()  # an absolute path stays itself
            text = excl_path.read_text() if ok and excl_path.exists() else ""
            if ok and f"/{STAMP}" not in text.splitlines():
                excl_path.parent.mkdir(parents=True, exist_ok=True)
                excl_path.write_text(text + ("\n" if text and not text.endswith("\n") else "") + f"/{STAMP}\n")
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


def manifest_version_changes(repo: Repo, head: str) -> list[str]:
    """The added `"version"` lines the PR's diff carries in `.claude-plugin/*.json`, as `path: line` —
    the detail a non-Release refusal names. Only added lines count: a changed version is one change,
    and the `-` side of the same hunk is that change seen from behind."""
    out = []
    path = None
    diff = repo.git(["diff", f"origin/{repo.base}...origin/{head}", "--", ".claude-plugin/*.json"])
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/")[-1]
        elif path and line.startswith("+") and not line.startswith("+++") and VERSION_LINE.search(line):
            out.append(f"{path}: {line[1:].strip()}")
    return out


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
    # the version bump belongs to the owner's release ritual (a PR titled `Release ...`, as #49 was): any
    # other PR that moves the manifest version line is refused, however correct the number it carries
    version_bumps = [] if (pr.get("title") or "").startswith("Release") else manifest_version_changes(repo, head)
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
    # GitHub closes every ref a closing keyword names in the body *or in any commit message* once the PR merges
    log = repo.git(["log", "--format=%h%x1f%B%x1e", f"origin/{repo.base}..origin/{head}"])
    texts = [("body", body)] + [("commit " + h, msg) for h, _, msg in (c.strip().partition("\x1f") for c in log.split("\x1e")) if h]
    refs = [(where, m.group(0), m.group(1) or repo.slug, int(m.group(2))) for where, t in texts for m in CLOSES.finditer(t)]
    own = n_issue or (int(m_branch.group(1)) if m_branch else None) or next((n for _, _, s, n in refs if s == repo.slug), None)
    closes_other = [{"where": where, "text": text} for where, text, s, n in refs if (s, n) != (repo.slug, own)]
    closes_only_in_commit = own if not n_issue and any((s, n) == (repo.slug, own) for _, _, s, n in refs) else None
    # the Done-when ledger: what the PR declares done, against the issue it closes (or its branch names)
    ledger = None
    if own:
        try:
            issue_body = repo.issue(own).get("body") or ""
        except SystemExit as e:
            ledger = {"flag": "LEDGER-MISMATCH", "detail": f"cannot read #{own} to compare with: {str(e).strip().splitlines()[-1]}"}
        else:
            hit = ledger_check(body, issue_body)
            ledger = {"flag": hit[0], "detail": hit[1]} if hit else None
    wt = repo.worktree(n_issue) if n_issue else None
    wt_state = None
    if wt and wt.exists():
        dirty = bool(repo.git(["status", "--porcelain"], cwd=wt).strip())
        ok, unpushed = repo.git_ok(["log", "--oneline", "@{u}.."], cwd=wt)
        wt_state = {"dirty": dirty, "unpushed": bool(unpushed.strip()) if ok else None}
    ok = (not attribution and not touched and not bad and pr.get("mergeable") != "CONFLICTING" and not contradictions
          and not (wt_state and (wt_state["dirty"] or wt_state["unpushed"])) and not closes_other and not closes_only_in_commit
          and not ledger and not version_bumps)
    return {"repo": repo.slug, "pr": pr["number"], "issue": n_issue, "head": head, "attribution": attribution,
            "protected_touched": touched, "checks_not_green": bad, "mergeable": pr.get("mergeable"),
            "worktree": wt_state, "notify_issues": siblings, "contradictions": contradictions,
            "closes_other": closes_other, "closes_only_in_commit": closes_only_in_commit, "ledger": ledger,
            "version_bumps": version_bumps, "ledger_issue": own, "ok": ok}


VERDICTS = ("pass", "fail", "concerns")


def verdict_problems(v) -> list[str]:
    """What disqualifies `v` as a judge verdict: the enum, a full 40-hex sha, findings that cite file and a 1-based
    line; fail and concerns must name at least one finding. Extra keys are tolerated."""
    if not isinstance(v, dict):
        return ["the verdict is not a JSON object"]
    bad = []
    if v.get("verdict") not in VERDICTS:
        bad.append(f"verdict must be one of {'|'.join(VERDICTS)}")
    if not isinstance(v.get("head_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", v["head_sha"]):
        bad.append("head_sha must be the full 40-hex sha the patch was judged at")
    fs = v.get("findings")
    if not isinstance(fs, list):
        bad.append("findings must be a list")
    else:
        for i, f in enumerate(fs):
            if not isinstance(f, dict) or not isinstance(f.get("file"), str) or not f["file"]:
                bad.append(f"findings[{i}].file must be a path")
            elif not isinstance(f.get("line"), int) or isinstance(f.get("line"), bool) or f["line"] < 1:
                bad.append(f"findings[{i}].line must be a 1-based line")
            elif not isinstance(f.get("note"), str) or not f["note"]:
                bad.append(f"findings[{i}].note must say what is wrong")
        if v.get("verdict") in ("fail", "concerns") and not fs:
            bad.append(f"a {v.get('verdict')} names at least one finding")
    return bad


def judge_check(repo: Repo, n: int, head_sha: str) -> dict:
    """The judge's verdict for PR `n`, re-validated by the kernel: `flag` is None only when a schema-valid verdict
    says `pass` at exactly `head_sha`; anything else is one of the three JUDGE refusals (merge never trusts the
    file, the plan, or this command's earlier answer — the sha is re-checked where the merge happens)."""
    path = repo.handoffs / f"judge-{n}.json"
    if not path.exists():
        return {"flag": "JUDGE-MISSING", "detail": f"no verdict at {path}: `wave judge` writes the patch, a read-only agent writes the verdict"}
    try:
        v = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return {"flag": "JUDGE-MISSING", "detail": f"{path.name} is not a readable verdict ({e})"}
    bad = verdict_problems(v)
    if bad:
        return {"flag": "JUDGE-MISSING", "detail": f"{path.name}: " + "; ".join(bad)}
    if v["verdict"] != "pass":
        where = "; ".join(f"{f['file']}:{f['line']} {f['note']}" for f in v["findings"][:3])
        return {"flag": "JUDGE-FAIL", "detail": f"verdict {v['verdict']}: {where}",
                "verdict": v["verdict"], "head_sha": v["head_sha"], "findings": v["findings"]}
    if v["head_sha"] != head_sha:
        return {"flag": "JUDGE-STALE", "detail": f"the verdict is for {v['head_sha'][:7]}, the head is {head_sha[:7]}: a push invalidates it, judge again",
                "verdict": "pass", "head_sha": v["head_sha"], "findings": []}
    return {"flag": None, "detail": f"pass at {head_sha[:7]}", "verdict": "pass", "head_sha": head_sha, "findings": []}


def cmd_judge(default: Repo | None, a):
    """The producer never judges its own PR: write the scoped diff into the handoffs dir, have a read-only agent
    (templates/judge.md is its prompt) judge it, and validate the verdict it writes. Exit 0 only when every PR
    named has a fresh pass verdict; `merge` re-checks the sha at merge time whatever this said."""
    bad = 0
    for ref in a.prs:
        repo, n = parse_ref(ref, default)
        pr = next((x for x in repo.open_prs() if x["number"] == n), None)
        if not pr:
            print(f"{key(repo, n)}: no open PR with that number")
            bad += 1
            continue
        repo.fetch()
        head = f"origin/{pr['headRefName']}"
        sha = repo.git(["rev-parse", head]).strip()
        files = repo.git(["diff", "--name-only", f"origin/{repo.base}...{head}"]).split()
        repo.handoffs.mkdir(parents=True, exist_ok=True)
        patch = repo.handoffs / f"judge-{n}.patch"
        patch.write_text(repo.git(["diff", f"origin/{repo.base}...{head}"]))  # base...head is by construction the diff of the files the PR touches
        n_issue = next((int(num) for _, num in CLOSES.findall(pr.get("body") or "")), None)
        print(f"{key(repo, n)}: patch {patch} ({len(files)} file(s): {', '.join(files) or 'none'})"
              + (f"; its issue is #{n_issue}" if n_issue else "; its PR body names no issue"))
        jc = judge_check(repo, n, sha)
        if jc["flag"] is None:
            print(f"  verdict pass at {sha[:7]} — fresh; `merge` re-checks the sha before merging")
        elif jc["flag"] == "JUDGE-FAIL":
            print(f"  {jc['flag']}[{jc['detail']}]")
        else:
            print(f"  no valid verdict yet — spawn ONE read-only agent (Read/Grep/Glob only; it edits nothing, writes only the verdict;"
                  f" {TEMPLATES / 'judge.md'} is its prompt) with the patch above, head sha {sha}, and the issue body")
            print(f'  it writes {repo.handoffs / f"judge-{n}.json"}:' +
                  ' {"verdict": "pass|fail|concerns", "head_sha": "<the 40-hex sha above>", "findings": [{"file": "<path>", "line": <N>, "note": "<what is wrong>"}]}')
            print(f"  then re-run this command; `merge` refuses a PR without a pass verdict at the head it merges"
                  f" (JUDGE-MISSING / JUDGE-FAIL / JUDGE-STALE)")
        if jc["flag"]:
            bad += 1
    sys.exit(1 if bad else 0)


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
            for c in r["closes_other"]: flags.append(f"CLOSES-OTHER[{c['where']}: {c['text']}]")
            if r["closes_only_in_commit"]: flags.append(f"NO-CLOSES-IN-BODY[#{r['closes_only_in_commit']} is closed only by a commit: GitHub links it at merge, not before]")
            if r["version_bumps"]: flags.append("VERSION-OUTSIDE-RELEASE[" + "; ".join(r["version_bumps"])
                                                + " — the version moves only in a Release PR (a title starting with `Release`)]")
            if r["ledger"]: flags.append(f"{r['ledger']['flag']}[{r['ledger']['detail']}]")
            print(f"{r['repo']}#{r['pr']:<5} issue #{r['issue'] or '?':<5} {'OK    ' if r['ok'] else 'NOT OK'} {' '.join(flags)}")
            if r["closes_other"]:
                print("        merging would close those issues too. Reword the body; a pushed commit cannot be reworded without a"
                      " force push, which wave never does: open a new branch with a clean message, or reopen the issue after merge")
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
        chain, trees = [], []  # trees: the accumulated commit after each step, None where the step conflicted
        acc = base_sha = repo.git(["rev-parse", base]).strip()
        for n in order:
            rc, out, err = run(["git", "merge-tree", "--write-tree", acc, heads[n]], cwd=repo.root, env=repo.env)
            if rc == 0:
                acc = repo.git(["commit-tree", out.strip(), "-p", acc, "-p", repo.git(["rev-parse", heads[n]]).strip(), "-m", f"sim {n}"]).strip()
                chain.append((n, []))
                trees.append(acc)
            else:
                chain.append((n, sorted(set(re.findall(r"Merge conflict in (.+)", out + err))) or ["<conflict>"]))
                trees.append(None)
        head_shas = {n: repo.git(["rev-parse", heads[n]]).strip() for n in nums}
        result[slug] = {"order": order, "pairs": {f"{x}x{y}": v for (x, y), v in pairs.items()}, "against_base": against_base,
                        "serial": serial_prs, "chain": chain,
                        "base_sha": base_sha,
                        "heads": head_shas,
                        "judges": {n: judge_check(repo, n, sha) for n, sha in head_shas.items()}}
        if a.run_gate:
            result[slug]["chain_gate"] = gate_chain(repo, base_sha, order, trees, sys.stderr if a.json else sys.stdout)
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
            unjudged = {n: j for n, j in result[slug]["judges"].items() if j["flag"]}
            if unjudged:
                print("  judge: " + " ".join(f"#{n} {j['flag']}" for n, j in unjudged.items())
                      + f"  — `wave judge {slug}#<PR>` and let the read-only agent write the verdict; `merge` refuses without a pass at the head")
            cg = result[slug].get("chain_gate")
            if cg:
                skipped = sorted({st["skipped"] for st in cg["steps"] if st.get("skipped")})
                print("  gate:  " + " ".join(f"#{st['pr']}" + {True: "✓", False: "✗", None: "·"}[st["ok"]] for st in cg["steps"])
                      + (f"   (· = not run: {'; '.join(skipped)})" if skipped else ""))
                bad = next((st for st in cg["steps"] if st["ok"] is False), None)
                if cg["base_red"]:
                    print(f"  {base} itself fails the gate (`{cg['base']['command']}`): no step can be blamed, fix the base first")
                elif bad:
                    print(f"  first failing step: #{bad['pr']} — `{bad['command']}`\n" + "\n".join("      " + l for l in bad["tail"].splitlines()))
    if a.plan:
        Path(a.plan).write_text(json.dumps({"made_at": now_iso(), "repos": result}, indent=2) + "\n")
        print(f"\nplan written to {a.plan} — show it, get the OK, then `merge --plan {a.plan} --yes`")
    if a.json:
        print(json.dumps(result, indent=2))
    elif not a.plan and not a.run_gate:
        print("\n(textual only — `--run-gate` runs the gate on every step; without it the base branch's CI after each merge is the truth for semantic conflicts)")


def gate_seconds(slug: str) -> float | None:
    """The newest measured gate run of a repo, from the run log."""
    try:
        lines = (CONFIG_DIR / "log.jsonl").read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("cmd") == "gate" and rec.get("repo") == slug and rec.get("seconds"):
            return float(rec["seconds"])
    return None


def run_gate(repo: Repo, sha: str, gate: list[str]) -> dict:
    """Run the gate on `sha` in a detached throwaway worktree, never in a checkout anyone works in. The worktree is
    removed whatever happened; only a killed process leaves one behind, and `doctor` names it."""
    wt = Path(tempfile.mkdtemp(prefix=CHAIN_GATE_PREFIX))
    t0 = time.time()
    res = {"sha": sha, "ok": True, "command": None, "tail": ""}
    try:
        repo.git(["worktree", "add", "-q", "--detach", str(wt), sha])
        for cmd in gate:
            p = subprocess.run(cmd, shell=True, cwd=wt, text=True, capture_output=True)
            if p.returncode != 0:
                res.update(ok=False, command=cmd, tail="\n".join((p.stdout + p.stderr).splitlines()[-20:]))
                break
    finally:
        # --force: the tree is ours, detached, and holds only what the gate wrote (build output, caches)
        removed, why_not = repo.git_ok(["worktree", "remove", "--force", str(wt)])
        if not removed and wt.exists() and not any(wt.iterdir()):
            wt.rmdir()  # the add itself failed, so git never registered it
        elif not removed:
            print(f"could not remove {wt}: {why_not.strip()} — `wave doctor` lists it", file=sys.stderr)
    res["seconds"] = round(time.time() - t0, 1)
    append_log({"at": now_iso(), "cmd": "gate", "repo": repo.slug, "sha": sha, "seconds": res["seconds"], "exit": 0 if res["ok"] else 1})
    return res


def gate_chain(repo: Repo, base_sha: str, order: list[int], trees: list[str | None], say) -> dict:
    """The gate on the tree after each step of the chain. The first red step is the culprit, unless the base is red too."""
    gate = repo.gate()
    if not gate or gate[0].startswith("<"):
        raise SystemExit(f"{repo.slug}: --run-gate needs a declared gate, found: {gate}")
    runs = sum(1 for t in trees if t)
    last = gate_seconds(repo.slug)
    print(f"{repo.slug}: {runs} gate run(s) ahead, " + (f"~{runs * last / 60:.1f} min at the last measured gate time ({last / 60:.1f} min per run)"
          if last else "time unknown: no gate run measured yet for this repo, this one measures it"), file=say, flush=True)
    steps, culprit, base_red, base = [], None, False, None
    for n, sha in zip(order, trees):
        if sha is None:
            steps.append({"pr": n, "ok": None, "skipped": "textual conflict, not in the simulated tree"})
        elif culprit or base_red:
            steps.append({"pr": n, "sha": sha, "ok": None, "skipped": "after the first red step"})
        else:
            print(f"  gating #{n} ({sum(1 for x in steps if 'seconds' in x) + 1}/{runs}) ...", file=say, flush=True)
            st = {"pr": n, **run_gate(repo, sha, gate)}
            steps.append(st)
            if not st["ok"]:
                if not any(s["ok"] for s in steps[:-1]):  # nothing green before it: a red base would look the same
                    base = run_gate(repo, base_sha, gate)
                    base_red = not base["ok"]
                culprit = None if base_red else n
    return {"gate": gate, "steps": steps, "first_failure": culprit, "base_red": base_red, "base": base}


def wait_for(fn, timeout: int, every: int = 10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = fn()
        if r is not None:
            return r
        time.sleep(every)
    return None


def edit_issue_body(repo: Repo, n: int, old: str, new: str, dry_run: bool = False) -> bool:
    """Write an issue body on GitHub unless dry_run, printing every line that changed either way. Whether it changed."""
    for o, w in zip(old.splitlines(), new.splitlines()):
        if o != w:
            print(f"  {key(repo, n)}: {o}\n  {' ' * len(key(repo, n))}  -> {w}")
    if new != old and not dry_run:
        repo.gh(["issue", "edit", str(n), "-R", repo.slug, "--body", new])
    return new != old


def tick_issue(repo: Repo, n: int, done: list[str], strike: list[tuple[str, str]], dry_run: bool = False) -> tuple[bool, dict[str, int]]:
    """Tick and strike items of an issue's Done when on GitHub: (whether the body changed, counts after). Writes only on
    a change, and never with dry_run; prints every line it changes either way."""
    body = repo.issue(n).get("body") or ""
    new = tick_body(body, done, strike)
    return edit_issue_body(repo, n, body, new, dry_run), tally(done_when(new))


def apply_ledger(repo: Repo, pr: dict, v: dict) -> bool:
    """After a merge: the PR's ledger onto its issue, one comment saying so. The PR's `Closes #N` closed the issue;
    while an item is still open it is reopened, so `next` offers the remainder. False when the ledger could not be applied."""
    own = v.get("ledger_issue")
    if not own:
        return True
    done, strike = ledger_ticks(pr.get("body") or "")
    try:
        _, t = tick_issue(repo, own, done, strike)
    except (ValueError, SystemExit) as e:
        print(f"{key(repo, own)}: LEDGER NOT APPLIED ({str(e).strip()}). The merge stands; apply it by hand with "
              f"`wave tick {key(repo, own)} --done <item> --strike <item> --why <reason>`")
        return False
    note = f"ledger applied from PR #{pr['number']}: {t['done']} done, {t['dropped']} dropped" + (f", {t['open']} remain" if t["open"] else "")
    if t["open"] and v.get("issue") == own:
        print(f"{key(repo, own)}: {t['open']} item(s) still open; waiting up to 60 s for GitHub to close it, to reopen it")
    closed = t["open"] and v.get("issue") == own and wait_for(lambda: True if repo.issue(own)["state"] == "closed" else None, 60, 3)
    if closed:
        repo.gh(["issue", "reopen", str(own), "-R", repo.slug, "--comment", note + " — reopened: those items are still to do"])
    else:
        repo.gh(["issue", "comment", str(own), "-R", repo.slug, "--body", note])
    print(f"{key(repo, own)}: {note}" + (" (reopened)" if closed else ""))
    return True


def cmd_tick(default: Repo | None, a):
    repo, n = parse_ref(a.issue, default)
    strikes, whys = a.strike or [], a.why or []
    if not (a.done or strikes):
        print("tick refused: name at least one item with --done or --strike", file=sys.stderr)
        sys.exit(2)
    if len(strikes) != len(whys):
        print(f"tick refused: {len(strikes)} --strike and {len(whys)} --why; each struck item needs its own reason", file=sys.stderr)
        sys.exit(2)
    try:
        changed, t = tick_issue(repo, n, a.done or [], list(zip(strikes, whys)), a.dry_run)
    except ValueError as e:
        print(f"tick refused: {key(repo, n)}: {e}", file=sys.stderr)
        sys.exit(2)
    print(f"{key(repo, n)}: " + ("" if changed else "nothing to change; ") + ("dry run, nothing written; " if a.dry_run and changed else "")
          + f"{t['done']} done, {t['dropped']} dropped, {t['open']} remain")


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
            cg = pl.get("chain_gate")
            if cg and (cg["first_failure"] or cg["base_red"]) and not a.force:
                where = f"#{cg['first_failure']}" if cg["first_failure"] else f"the base, origin/{repo.base}"
                raise SystemExit(f"{slug}: the plan's chain failed the gate at {where}. Fix it there and re-run `order --run-gate --plan`, or pass --force and say why in the PR.")
            for n in pl["order"]:
                pr = next((x for x in repo.open_prs() if x["number"] == n), None)
                if not pr:
                    raise SystemExit(f"{slug}#{n}: no longer an open PR; re-run `order --plan`.")
                head_now = repo.git(["rev-parse", f"origin/{pr['headRefName']}"]).strip()
                if head_now != pl["heads"][str(n)]:
                    raise SystemExit(f"{slug}#{n}: its branch moved since the plan ({pl['heads'][str(n)][:7]} -> {head_now[:7]}). Re-run `order --plan` and ask again.")
                if not a.force:
                    jc = judge_check(repo, n, head_now)
                    if jc["flag"]:
                        raise SystemExit(f"{slug}#{n}: {jc['flag']}[{jc['detail']}] — the OK was for a judged delta: `wave judge` and ask again, or --force and say why in the PR.")
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
    unapplied: list[str] = []
    for repo, n in targets:
        print(f"== {key(repo, n)} ==")
        # verify again, here, where it cannot be skipped: the OK was given on a verified table
        pr = next((x for x in repo.open_prs() if x["number"] == n), None)
        if pr is None:
            print(f"{key(repo, n)}: not an open PR any more. Stopping."); sys.exit(2)
        v = verify_one(repo, pr)
        if not v["ok"] and not a.force:
            print(f"{key(repo, n)}: verify says NOT OK — attribution={v['attribution']} protected={v['protected_touched']} ci={v['checks_not_green']} contradictions={v['contradictions']} closes_other={v['closes_other']} closes_only_in_commit={v['closes_only_in_commit']} ledger={v['ledger']} version_bumps={v['version_bumps']} worktree={v['worktree']}. Not merging."
                  + (f" It conflicts with {repo.base}: run `wave resolve` on PR {key(repo, n)}, then rerun merge from here." if v["mergeable"] == "CONFLICTING" else ""))
            sys.exit(2)

        def mergeable():
            m = json.loads(repo.gh(["pr", "view", str(n), "-R", repo.slug, "--json", "mergeable,state"]))
            if m["state"] != "OPEN":
                return "GONE"
            return None if m["mergeable"] == "UNKNOWN" else m["mergeable"]

        m = wait_for(mergeable, 180, 5)
        if m != "MERGEABLE":
            print(f"{key(repo, n)}: {m}." + (f" `wave resolve` on PR {key(repo, n)} merges {repo.base} into its branch in its worktree, gates and pushes (never --force); then rerun merge from here." if m == "CONFLICTING" else ""))
            sys.exit(2)
        # judged here too, where it cannot be skipped: the plan's verdict may predate a push, a direct merge never had one,
        # and a PR that still has to be resolved first would only be re-judged after resolve moved its head
        if not a.force:
            jc = judge_check(repo, n, repo.git(["rev-parse", f"origin/{pr['headRefName']}"]).strip())
            if jc["flag"]:
                print(f"{key(repo, n)}: {jc['flag']}[{jc['detail']}]. Not merging — `wave judge {key(repo, n)}` and let the read-only agent write the verdict.")
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
        if not apply_ledger(repo, pr, v):
            unapplied.append(key(repo, n))
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
    if unapplied:
        print(f"merged, but the ledger of {', '.join(unapplied)} is not on its issue: `wave tick` it (see above)")
        sys.exit(5)


CONFLICT_START = re.compile(r"^<{7}(?: |$)")
CONFLICT_END = re.compile(r"^>{7}(?: |$)")


def conflict_hunks(path: Path) -> list[tuple[int, int]]:
    """The 1-based line ranges, markers included, of every conflict git left in a file."""
    hunks, start = [], None
    for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if CONFLICT_START.match(line):
            start = i
        elif CONFLICT_END.match(line) and start:
            hunks.append((start, i))
            start = None
    return hunks


def pr_worktree(repo: Repo, head: str) -> Path | None:
    """The worktree that has the PR's branch checked out, whatever its path."""
    wt = None
    for line in repo.git(["worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            wt = Path(line[len("worktree "):])
        elif line == f"branch refs/heads/{head}":
            return wt
    return None


def cmd_resolve(default: Repo | None, a):
    """Merge the base into a PR's branch in its worktree, gate the result, push without force, comment on the PR.
    A conflict is left in the worktree for a human; `--continue` picks up after the hand resolution."""
    repo, n = parse_ref(a.pr, default)
    pr = next((x for x in repo.open_prs() if x["number"] == n), None)
    if pr is None:
        raise SystemExit(f"{key(repo, n)}: no open PR with that number")
    head, base = pr["headRefName"], pr.get("baseRefName") or repo.base
    wt = pr_worktree(repo, head)
    if wt is None:
        raise SystemExit(f"{key(repo, n)}: no worktree has {head} checked out; create one (`git worktree add <path> {head}`) and rerun")
    repo.fetch()
    base_sha = repo.git(["rev-parse", f"origin/{base}"]).strip()
    merging = repo.git_ok(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=wt)[0]
    if not a.cont:
        # a merge only ever starts from a clean tree: whatever is uncommitted belongs to someone, and is never stashed
        if merging:
            raise SystemExit(f"{wt}: a merge is already in progress; finish it by hand, then `wave resolve {key(repo, n)} --continue`")
        dirty = repo.git(["status", "--porcelain"], cwd=wt).strip()
        if dirty:
            raise SystemExit(f"{wt} has uncommitted changes; commit or move them yourself, then rerun (resolve never stashes):\n{dirty}")
        if not repo.git_ok(["merge-base", "--is-ancestor", f"origin/{head}", "HEAD"], cwd=wt)[0]:
            raise SystemExit(f"{wt}: {head} lacks commits that are on origin/{head}; `git pull --ff-only` there first, so the push stays a fast-forward")
        before = repo.git(["rev-parse", "HEAD"], cwd=wt).strip()
        rc, out, err = run(["git", "merge", "--no-edit", f"origin/{base}"], cwd=wt, env=repo.env)
        if rc != 0:
            files = repo.git(["diff", "--name-only", "--diff-filter=U"], cwd=wt).splitlines()
            if not files:
                raise SystemExit(f"git merge failed in {wt}:\n{(out + err).strip()}")
            print(f"{key(repo, n)}: merging origin/{base} ({base_sha[:7]}) into {head} conflicts in {wt}:")
            for f in files:
                ranges = ", ".join(f"{s}-{e}" for s, e in conflict_hunks(wt / f))
                print(f"  {f}: lines {ranges}" if ranges else f"  {f}: no text markers (a delete, rename or binary conflict)")
            print(f"resolve by hand, then `wave resolve {key(repo, n)} --continue`")
            sys.exit(2)
        if repo.git(["rev-parse", "HEAD"], cwd=wt).strip() == before:
            print(f"{key(repo, n)}: {head} already contains origin/{base} ({base_sha[:7]}); nothing to merge or push")
            return
    else:
        unmerged = repo.git(["diff", "--name-only", "--diff-filter=U"], cwd=wt).splitlines()
        if unmerged:
            raise SystemExit(f"{wt}: still unmerged: {', '.join(unmerged)}. Resolve and `git add` them, then rerun --continue")
        # `git add` clears the unmerged state whether or not the markers are gone: check the text itself, against HEAD
        changed = repo.git(["diff", "--name-only", "HEAD"], cwd=wt).splitlines()
        marked = [f for f in changed if (wt / f).is_file() and conflict_hunks(wt / f)]
        ok, check = repo.git_ok(["diff", "--check", "HEAD"], cwd=wt)
        if marked or (not ok and "conflict marker" in check):
            where = ", ".join(marked) or check.strip()
            raise SystemExit(f"{wt}: conflict markers remain ({where}). Resolve them, `git add`, rerun --continue")
        loose = [l for l in repo.git(["status", "--porcelain"], cwd=wt).splitlines() if l[1] != " "]
        if loose:
            raise SystemExit(f"{wt}: changes not staged; `git add` what belongs to the resolution, then rerun --continue:\n" + "\n".join(loose))
        if merging:
            repo.git(["commit", "-q", "--no-edit"], cwd=wt)
        elif not repo.git_ok(["merge-base", "--is-ancestor", f"origin/{base}", "HEAD"], cwd=wt)[0]:
            raise SystemExit(f"{wt}: no merge in progress and HEAD does not contain origin/{base}; run `wave resolve {key(repo, n)}` first")
    # the gate runs on the commit about to be pushed, in a throwaway worktree: what is gated is exactly what is pushed
    gate = repo.gate()
    if not gate or gate[0].startswith("<"):
        raise SystemExit(f"{repo.slug}: resolve needs a declared gate, found: {gate}")
    sha = repo.git(["rev-parse", "HEAD"], cwd=wt).strip()
    g = run_gate(repo, sha, gate)
    if not g["ok"]:
        print(f"{key(repo, n)}: the merge of origin/{base} is committed in {wt} but fails the gate at `{g['command']}`; nothing pushed.\n"
              + "\n".join("    " + l for l in g["tail"].splitlines())
              + f"\nfix it there, commit, then `wave resolve {key(repo, n)} --continue`")
        sys.exit(3)
    ok, msg = repo.git_ok(["push", "origin", f"HEAD:refs/heads/{head}"], cwd=wt)  # plain push: a rejection stops here
    if not ok:
        raise SystemExit(f"{key(repo, n)}: push of {head} rejected (someone pushed meanwhile?); nothing forced:\n{msg.strip()}")
    body = (f"merged `{base}` at `{base_sha[:7]}` into this branch; gate: " + ", ".join(f"`{c}`" for c in gate)
            + f" green on `{sha[:7]}` in {g['seconds']}s")
    ok, msg = repo.gh_ok(["pr", "comment", str(n), "-R", repo.slug, "--body", body])
    if not ok:
        raise SystemExit(f"{key(repo, n)}: pushed {sha[:7]} to {head}, but the PR comment failed ({msg.strip()}).\n"
                         f"`wave resolve {key(repo, n)} --continue` posts it again, or post by hand: {body}")
    print(f"{key(repo, n)}: pushed {sha[:7]} to {head}; commented: {body}")


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


def du_kb(path: Path) -> int:
    """The size of a directory in KB (`du -sk`), 0 when it cannot be read."""
    ok, out = sh_ok(["du", "-sk", str(path)])
    parts = out.split() if ok else []
    return int(parts[0]) if parts else 0


def fmt_kb(kb: int) -> str:
    return f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb} KB"


def cmd_cleanup(default: Repo | None, a):
    """Worktrees whose work lives somewhere else go, with the disk they held: a merged PR's (the truth for
    a squash merge and a deleted remote, which `--merged` never sees), a closed one with --include-closed,
    an empty seat. An open PR (another epic's issue at work), a dispatch stamp, dirt or an unpushed head
    always keep it. Branches stay: a branch is cheap, a worktree is not."""
    repos = [Repo.get(s) for s in a.slugs] if a.slugs else [default] if default else []
    if a.stale_days is not None and a.stale_days < 0:
        raise SystemExit(f"--stale-days wants N >= 0 days, got {a.stale_days}")
    if a.yes and a.stale_days is None:
        raise SystemExit("--yes only matters with --stale-days: it removes the stale worktrees")
    total_kb, removed_n = 0, 0
    for repo in repos:
        repo.fetch()
        for wt, branch, _n in issue_worktrees(repo):
            def remove(why: str) -> None:
                nonlocal total_kb, removed_n
                kb = du_kb(wt)
                if a.dry_run:
                    print(f"would remove {wt.name}: {why}, {fmt_kb(kb)}")
                    return
                repo.git(["worktree", "remove", str(wt)])
                total_kb, removed_n = total_kb + kb, removed_n + 1
                print(f"removed {wt.name}: {why}, reclaimed {fmt_kb(kb)} (branch {branch} kept)")
            if repo.git(["status", "--porcelain"], cwd=wt).strip():
                print(f"KEEP {wt.name}: dirty")
                continue
            ok, remote = repo.git_ok(["rev-parse", "--verify", "--quiet", f"origin/{branch}"])
            if ok and remote.strip() != repo.git(["rev-parse", "HEAD"], cwd=wt).strip():
                print(f"KEEP {wt.name}: unpushed (origin/{branch} is behind)")
                continue
            pr = next(iter(repo.prs_for_head(branch)), None)
            state, num = (pr or {}).get("state"), (pr or {}).get("number")
            if state == "MERGED":
                remove(f"PR #{num} merged")
            elif state == "CLOSED":
                if a.include_closed:
                    remove(f"PR #{num} closed without merge (--include-closed)")
                else:
                    print(f"KEEP {wt.name}: closed without merge: `--include-closed` to remove")
            elif state == "OPEN":
                print(f"KEEP {wt.name}: PR #{num} open (in progress)")
            elif int(repo.git_ok(["rev-list", "--count", f"origin/{repo.base}..HEAD"], cwd=wt)[1] or 0):
                days = round((time.time() - last_activity(repo, wt, _epoch(stamp_of(wt).get("dispatched_at")))) / 86400)
                if a.stale_days is not None and days >= a.stale_days:
                    if a.yes:
                        remove(f"stale: nothing changed for {days} days and no PR")
                    else:
                        print(f"STALE {wt.name}: no PR, newest change {days} days ago, {fmt_kb(du_kb(wt))} — `--yes` to remove")
                else:
                    print(f"KEEP {wt.name}: no PR, newest change {days} days ago")
            elif stamp_of(wt):
                print(f"KEEP {wt.name}: dispatched, no commit yet")
            else:
                remove("empty: no PR, no commits")
        repo.git(["worktree", "prune"])
    if removed_n:
        print(f"reclaimed {fmt_kb(total_kb)} from {removed_n} worktree(s)")


# ---------------------------------------------------------------- sweep

def ledger_applied(repo: Repo, n: int, pr: dict) -> bool:
    """Whether the issue carries apply_ledger's note for this PR."""
    comments = repo.api(f"repos/{repo.slug}/issues/{n}/comments?per_page=100", paginate=True) or []
    return any(f"ledger applied from PR #{pr['number']}" in (c.get("body") or "") for c in comments)


def sweep_epic(nodes: dict[str, dict]) -> list[dict]:
    """The state sweep: every leaf that claims to be done (all Done-when items ticked or struck) or is closed,
    re-checked against GitHub — a PR closes it, that PR is merged, and its ledger was applied. Read-only."""
    findings = []
    for nd in sorted(leaves(nodes), key=lambda d: (d["repo"], d["number"])):
        r, n, iss = Repo.get(nd["repo"]), nd["number"], nd["issue"]
        items = done_when(iss.get("body") or "")
        done = bool(items) and all(i["state"] != "open" for i in items)
        closed = iss["state"] == "closed"
        if not closed and not done:
            continue  # nothing claimed here: nothing to re-verify
        merged = merged_pr_for_issue(r, n)
        if merged:
            if not ledger_applied(r, n, merged):
                findings.append({"key": key(r, n), "repo": r.slug, "number": n, "kind": "ledger-not-applied",
                                 "evidence": f"merged PR #{merged['number']} closes it, but no "
                                             f"`ledger applied from PR #{merged['number']}` comment is on the issue"})
            continue
        open_pr = pr_for_issue(r, n)
        behind = f"PR #{open_pr['number']} is open, not merged" if open_pr else "no PR closes it at all"
        claimed = "closed" if closed else "every Done-when item is ticked or struck"
        findings.append({"key": key(r, n), "repo": r.slug, "number": n,
                         "kind": "false-closed" if closed else "false-done",
                         "evidence": f"{claimed}, but {behind}"})
    return findings


def sweep_retries(repo: Repo, nums: set[int] | None = None) -> list[dict]:
    """The retry sweep: worktrees whose stamp counts RETRY_CEILING no-progress dispatches (resumes and respawns
    alike) without a new commit on the branch. Read-only; nums narrows it to the leaves of one epic."""
    out = []
    for wt, branch, n in issue_worktrees(repo):
        if nums is not None and n not in nums:
            continue
        stamp = stamp_of(wt)
        if no_progress(stamp) < RETRY_CEILING:
            continue
        lv = liveness(repo, n, wt)
        rc, log, _ = run(["git", "log", "-1", "--format=%h %ct", f"origin/{repo.base}..HEAD"], cwd=wt, env=repo.env)
        last = log.strip().split() if rc == 0 and log.strip() else None
        commit = f"last commit {last[0]} {round((time.time() - float(last[1])) / 60)} min ago" if last else "no commit on the branch"
        mix = f", {stamp['respawns']} with a new strategy" if stamp.get("respawns") else ""
        out.append({"key": key(repo, n), "repo": repo.slug, "number": n, "wt": wt, "kind": "agent-exhausted",
                    "evidence": f"{stamp['resumes']} resumes without a new commit{mix} "
                                f"({commit}; nothing changed for {lv['idle_min']} min)"})
    return sorted(out, key=lambda f: f["number"])


def apply_sweep(repo: Repo, epic: int, findings: list[dict]) -> None:
    """--fix: revert every lie a sweep proved, comment why, and escalate the exhausted ones to the owner. It never
    closes or deletes: a reverted leaf goes back to `next`, an exhausted one stays open — dispatch refuses it.
    Every finding is applied on its own: one that fails is reported, the rest still run."""
    epic_notes = []
    for f in findings:
        try:
            r, n = Repo.get(f["repo"]), f["number"]
            if f["kind"] == "false-done":
                body = r.issue(n).get("body") or ""
                done_refs = [str(i + 1) for i, it in enumerate(done_when(body) or []) if it["state"] == "done"]
                edit_issue_body(r, n, body, untick_body(body, done_refs))
                note = (f"wave sweep: {f['evidence']}. "
                        + (f"Unticked {len(done_refs)} item(s) back to open, so `wave next` sees the work again; "
                           "strikes stay, they are the owner's calls." if done_refs else
                           "Nothing unticked: every item is a strike, the owner's call."))
                r.gh(["issue", "comment", str(n), "-R", r.slug, "--body", note])
                print(f"  {f['key']}: " + (f"unticked {len(done_refs)} item(s) back to open and commented"
                                           if done_refs else "commented (nothing to untick: every item is a strike)"))
            elif f["kind"] == "false-closed":
                r.gh(["issue", "reopen", str(n), "-R", r.slug,
                      "--comment", f"wave sweep: {f['evidence']} — reopened: a closure has to stand on a merged PR"])
                print(f"  {f['key']}: reopened o/x#N and commented")
            elif f["kind"] == "ledger-not-applied":
                merged = merged_pr_for_issue(r, n)
                if merged:
                    apply_ledger(r, merged, {"ledger_issue": n, "issue": n})
            else:  # agent-exhausted
                r.gh(["issue", "comment", str(n), "-R", r.slug, "--body",
                      f"wave sweep: agent-exhausted — {f['evidence']}. Dispatch now refuses it: the wave moves on and "
                      "nothing was closed or discarded. The owner decides: resume by hand with a fresh instruction, "
                      "commit what is there, or close."])
                print(f"  {f['key']}: commented the escalation on the issue")
                epic_notes.append(f"- {f['key']}: {f['evidence']}")
        except (SystemExit, ValueError) as e:  # like apply_ledger: the finding is reported, the others still run
            print(f"  {f['key']}: could not be fixed ({str(e).strip().splitlines()[-1] if str(e).strip() else e})")
    if epic_notes:
        repo.gh(["issue", "comment", str(epic), "-R", repo.slug, "--body",
                 "wave sweep: agents past the retry ceiling:\n" + "\n".join(epic_notes)
                 + "\nDispatch refuses these; the owner decides. Nothing was closed or discarded."])
        print(f"  {key(repo, epic)}: commented the escalation on the epic")


def cmd_sweep(default: Repo | None, a):
    """Both sweeps: state (claimed done and closed leaves, against merged PRs) and the retry ceiling. Read-only unless --fix."""
    repo, epic = parse_ref(a.epic, default)
    repo.fetch()
    nodes = tree(repo, epic)
    findings = sweep_epic(nodes) + sweep_retries(repo, {nd["number"] for nd in leaves(nodes)})
    print(f"epic {key(repo, epic)} — {len(leaves(nodes))} leaf issues, {len(findings)} finding(s)" + ("" if findings else ", clean"))
    for f in findings:
        print(f"  {f['key']} {f['kind']}: {f['evidence']}")
    if findings and a.fix:
        apply_sweep(repo, epic, findings)
    if a.fix:
        print("fixes applied" + (" — run the sweep again: what was reverted is honest now" if findings else ": nothing to fix"))
    else:
        print("(read-only: pass --fix to untick, reopen, apply the ledgers and comment the escalations)")
    sys.exit(1 if findings else 0)


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

    def boxes(k: str) -> tuple[int, int]:
        """(ticked or struck, items): a leaf's own Done when; a parent's, the sum of its leaves'."""
        nd = nodes[k]
        if nd["children"]:
            sums = [boxes(ch) for ch in nd["children"]]
            return sum(d for d, _ in sums), sum(t for _, t in sums)
        items = done_when(nd["issue"].get("body") or "") or []
        return sum(1 for i in items if i["state"] != "open"), len(items)

    def line(k: str, depth: int):
        nd = nodes[k]
        iss = nd["issue"]
        label = f"#{nd['number']}" if nd["repo"] == repo.slug else k
        extra = ""
        if k in cands:
            c = cands[k]
            extra = " — " + why(c)
        d, t = boxes(k)
        extra += f" · {d}/{t}" if t else ""
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


EVIDENCE_LINE = re.compile(r"\b[\w./-]+\.(rs|ts|tsx|js|py|go|swift|kt|rb|php|cs|java|yml|yaml|toml|json):\d+")
EVIDENCE_SYMBOL = re.compile(r"`[\w./-]+\.(rs|ts|tsx|js|py|go|swift|kt|rb|php|cs|java)`[^\n]{0,80}`[A-Za-z_][\w.]*`")


def has_evidence(body: str) -> bool:
    """`file:line`, or a file named beside the symbol it defines: what lets an agent start without guessing."""
    return bool(EVIDENCE_LINE.search(body) or EVIDENCE_SYMBOL.search(body))


def lint_body(body: str, n: int, typ: str | None, *, depends_wired: bool = True, sub_issues: int = 0) -> list[str]:
    """Everything `wave lint` charges one body with: the common contract, plus the type's template when labeled.
    `depends_wired` mirrors the issue's blocked_by and `sub_issues` its count; both come from GitHub."""
    missing = [s for s in ("## Done when", "## Handoff") if s not in body]
    if not BRANCH_IN_BODY.search(body):
        missing.append("branch line `git worktree add -b <branch>` (will be derived from the title)")
    if not re.search(r"^Parent:\s*\S*#\d+", body, re.M):
        missing.append("`Parent: #N` first line")
    if "Depends on" in body and not depends_wired:
        missing.append("CONTRADICTION: body says 'Depends on' but no blocked_by is wired in GitHub")
    mb = BRANCH_IN_BODY.search(body)
    if mb and f"/{n}-" not in mb.group(1):
        missing.append(f"CONTRADICTION: handoff branch `{mb.group(1)}` does not carry #{n}")
    closes = [int(num) for _, num in CLOSES.findall(body)]
    if closes and n not in closes:
        missing.append(f"CONTRADICTION: body closes {closes} but this is #{n}")
    if sub_issues and "## Handoff" in body:
        missing.append("a parent with a Handoff block: parents are never dispatched; move the handoff to the leaves")
    if not has_evidence(body):
        missing.append("no evidence: neither `file:line` nor `file` + `symbol`")
    return missing + (lint_type(body, typ) if typ else [])


def cmd_lint(default: Repo | None, a):
    problems = 0
    for ref in a.issues:
        repo, n = parse_ref(ref, default)
        iss = repo.issue(n)
        body = iss.get("body") or ""
        typ = issue_type(iss)
        missing = lint_body(body, n, typ,
                            depends_wired=bool(repo.blocked_by(n)) if "Depends on" in body else True,
                            sub_issues=len(repo.sub_issues(n)) if "## Handoff" in body else 0)
        problems += bool(missing)
        where = f"[{typ}] templates/issue/{typ}.md" if typ else "[no type] the common contract"
        note = "" if typ else " (no type label — bug, story, chore, feature — its template was not charged)"
        print(f"{key(repo, n)}: {where} — " + ("ok" if not missing else "; ".join(missing)) + note)
    sys.exit(1 if problems else 0)


# ---------------------------------------------------------------- prepare / amend

PREPARE_COMMENT = "handoff added by `wave prepare`"


def handoff_block(repo: Repo, iss: dict) -> str:
    """The facts half of a handoff, everything from `facts` on the repo: branch, worktree, base and sha,
    gate, skills (`.wave.json`'s `phases.agent_skills`, else the default stack), the non-negotiables and
    `Closes #N`. The judgement — the Done when, the file:line, the ADR — is never written here."""
    n = iss["number"]
    gate, skills = repo.gate(), profile_skills(repo.profile()) or DEFAULT_SKILLS
    return "\n".join([
        "## Handoff",
        PREPARE_MARK, "",
        "```bash",
        f"git worktree add -b {branch_for(iss)} {repo.worktree(n)} origin/{repo.base}",
        f"cd {repo.worktree(n)}",
        "```", "",
        f"Base `origin/{repo.base}` at `{repo.origin_sha()}`. Gate:",
        "", "```bash", *gate, "```", "",
        "Skills: " + ", ".join(f"`{s}`" for s in skills), "",
        "Non-negotiables: no AI attribution in commits or PRs; never `git gc --prune`, `git reflog expire`, "
        "`git stash`, `git reset --hard`, `git clean -f`, `git push --force*`, `git branch -D`, `rm -rf`; "
        "never merge — the owner merges; never touch the main checkout or a sibling worktree; "
        "evidence before assertions.", "",
        f"Closes #{n}", "",
    ])


def prepare_questions(body: str) -> list[tuple[str, str]]:
    """The judgement `prepare` cannot write, each with the exact question the orchestrator asks: the SKILL
    turns them into one AskUserQuestion per issue, the answer comes back through
    `wave amend <issue> --done-when <file>`. The tool proposes nothing here — proposing is inventing."""
    questions = []
    if done_when(body) is None:
        questions.append(("Done when",
                          "What must be observably true when this issue is done? Name 3-6 checks a stranger "
                          "can run, in the issue's own words (the answer is written with `wave amend`)."))
    if not has_evidence(body):
        questions.append(("evidence",
                          "Where does this change land? Name the `file:line` (or the file and the symbol) in "
                          "the current code you believe it touches."))
    if section(body, "## ADR stub") is None:
        questions.append(("ADR stub",
                          "Which design fork does this issue settle? Name both options and the recommendation, "
                          "so the PR can record the decision in three sentences."))
    return questions


def prepared_note(body: str) -> str:
    """The agent prompt's note on the issue's completeness: which sections the tool wrote (the marker says
    so) and which are missing, so the agent verifies its premises harder where the issue is thin."""
    wrote, lack = [], [g for g, _ in prepare_questions(body)]
    if PREPARE_MARK in body:
        wrote.append("Handoff (branch, worktree, base, gate, skills — written by `wave prepare`)")
    if not (wrote or lack):
        return ""
    out = []
    if wrote:
        out.append("Escrito pela ferramenta: " + "; ".join(wrote) + ".")
    if lack:
        out.append("Falta na issue, e o orquestrador já perguntou: " + ", ".join(lack)
                   + " — onde a issue é fina, verifique cada premissa duas vezes antes de agir.")
    return "\n".join(out)


def prepare_issue(repo: Repo, n: int) -> tuple[dict, str, list[tuple[str, str]]]:
    """The facts of a handoff written into the issue's body (`## Handoff` appended, or an existing one
    replaced in place with every other byte kept), the judgement it cannot write returned as questions,
    and — once, when judgement is missing — a comment naming the gap. A parent is refused at the door:
    parents are never dispatched, so none ever receives a Handoff (`lint` stays as the second net).
    Returns the refreshed issue, what happened to the body (appended | updated | current) and the questions."""
    repo.fetch()
    iss = repo.issue(n)
    subs = repo.sub_issues(n)
    if subs:
        raise SystemExit(f"PREPARE-PARENT: {key(repo, n)} is a parent ({len(subs)} sub-issues); "
                         "parents are never dispatched")
    body = iss.get("body") or ""
    new = insert_section(body, HANDOFF, handoff_block(repo, iss))
    what = ("appended" if not any(HANDOFF.match(l) for l in body.splitlines())
            else "current" if new == body else "updated")
    if new != body:
        edit_issue_body(repo, n, body, new)
    gaps = prepare_questions(new)
    if gaps:
        comments = repo.api(f"repos/{repo.slug}/issues/{n}/comments?per_page=100", paginate=True) or []
        if not any(PREPARE_COMMENT in (c.get("body") or "") for c in comments):
            repo.gh(["issue", "comment", str(n), "-R", repo.slug,
                     "--body", f"{PREPARE_COMMENT}: the facts are in the body; still needing a human: "
                               + ", ".join(g for g, _ in gaps) + "."])
    iss["body"] = new
    return iss, what, gaps


def cmd_prepare(default: Repo | None, a):
    for ref in a.issues:
        repo, n = parse_ref(ref, default)
        iss, what, gaps = prepare_issue(repo, n)
        print(f"{key(repo, n)}: " + {"appended": "Handoff appended to the body",
                                     "updated": "Handoff updated in place in the body",
                                     "current": "Handoff already current — nothing written"}[what]
              + f" (branch {branch_for(iss)}, worktree {repo.worktree(n)}, base {repo.base} @ {repo.origin_sha()})")
        if not gaps:
            print("  nothing to ask: the issue carries its own judgement")
        else:
            print(f"  {len(gaps)} thing(s) the tool cannot write — one question each, for the orchestrator to ask:")
            for g, q in gaps:
                print(f"    {g}: {q}")
            print("  commented on the issue: the gap trail (once; the next run writes nothing)")


def cmd_amend(default: Repo | None, a):
    """Write the owner's answered criteria into the issue once: `wave amend <issue> --done-when <file>`."""
    repo, n = parse_ref(a.issue, default)
    try:
        text = Path(a.done_when).read_text()
    except OSError as e:
        print(f"amend refused: cannot read {a.done_when}: {e.strerror or e}", file=sys.stderr)
        sys.exit(2)
    items = [l for l in text.splitlines() if l.strip()]
    bad = [l for l in items if not ITEM.match(l)]
    if not items or bad:
        for l in bad[:3]:
            print(f"  not an item: {l}", file=sys.stderr)
        print(f"amend refused: every line of {a.done_when} must be a `- [ ] item`", file=sys.stderr)
        sys.exit(2)
    body = repo.issue(n).get("body") or ""
    try:
        new = with_done_when(body, "\n".join(items))
    except ValueError as e:
        print(f"amend refused: {key(repo, n)}: {e}", file=sys.stderr)
        sys.exit(2)
    edit_issue_body(repo, n, body, new)
    print(f"{key(repo, n)}: Done-when written ({len(items)} item(s)); verify can now compare the PR's ledger")


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


def cmd_agents(default: Repo | None, a):
    """Every issue worktree of the repo with what its files say about its agent."""
    if default is None:
        raise SystemExit("run inside a repo checkout, or pass --repo")
    rows = [liveness(default, n, wt) for wt, _, n in issue_worktrees(default)]
    print(f"{len(rows)} worktree(s) of {default.slug} — working < {WORKING_MIN} min since the newest change, quiet up to {DEAD_MIN}, then likely dead unless a PR is open")
    for lv in sorted(rows, key=lambda x: x["issue"]):
        print(f"  {key(default, lv['issue']):<24} {Path(lv['worktree']).name:<20} "
              f"dispatched {'?' if lv['age_min'] is None else lv['age_min']} min ago   changed {lv['idle_min']} min ago   "
              f"{'PR #' + str(lv['pr']) if lv['pr'] else 'no PR':<8} {lv['verdict']}")


def cmd_doctor(default: Repo | None, a) -> None:
    """Preflight. Every ✗ is a mistake that dispatch would otherwise let happen."""
    checks: list[tuple[str, bool, str, bool]] = []  # (text, ok, evidence, hard)
    ok, out = sh_ok(["gh", "auth", "status"])
    checks.append(("gh is logged in", ok, (out.strip().splitlines() or [""])[0].strip(), True))
    ok, out = sh_ok(["git", "--version"])
    v = re.search(r"(\d+)\.(\d+)", out)
    checks.append(("git >= 2.38 (merge-tree --write-tree)", bool(v) and (int(v.group(1)), int(v.group(2))) >= (2, 38), out.strip(), True))
    checks.append(("codegraph on PATH (optional)", sh_ok(["which", "codegraph"])[0], "agents index their worktree with it", False))
    if a.batch > MAX_BATCH:
        checks.append((f"batch of {a.batch} is at most {MAX_BATCH}", False,
                       "eleven parallel agents once hit the rate limit (3 of 11 killed) and one killed mid-build left a crate "
                       "half-extracted in the shared cargo cache — batch 4, and `wave dispatch --warm` to download once", False))
    if default is None:
        reg = load_registry()
        checks.append(("inside a registered repo", False,
                       f"the registry ({REGISTRY}) lists {len(reg['repos'])} repo(s): {', '.join(sorted(reg['repos'])) or 'none'}; "
                       f"search paths: {', '.join(reg['search_paths'])} — run from a checkout or pass --repo; "
                       "an owner/name#N ref to a repo you do not have is cloned into the first search path", True))
    else:
        r = default
        dirty = r.git(["status", "--porcelain", "--untracked-files=no"]).strip()
        untracked = r.git(["status", "--porcelain", "--untracked-files=all"]).count("?? ")
        checks.append((f"main checkout `{r.root.name}` has no tracked changes (you never work there)", not dirty,
                       (f"{len(dirty.splitlines())} modified path(s)" if dirty else "clean") + (f", {untracked} untracked (fine)" if untracked else ""), True))
        r.fetch()  # first: the gate, protected paths and CI checks below read origin/<base>'s .wave.json
        if r.remote_cfg is None:
            checks.append((f"main checkout's `.wave.json` matches origin/{r.base}'s", True,
                           f"no copy on origin/{r.base}, the working tree's is used" if r.local_cfg is not None else f"no `.wave.json` on origin/{r.base} or in the working tree", False))
        else:
            differs = sorted(k for k in {*(r.local_cfg or {}), *r.remote_cfg} if (r.local_cfg or {}).get(k) != r.remote_cfg.get(k))
            checks.append((f"main checkout's `.wave.json` matches origin/{r.base}'s", not differs,
                           (f"differs in {', '.join(differs)} — the main checkout is behind; prompts use origin/{r.base}'s" if r.local_cfg is not None
                            else f"the main checkout has none; prompts use origin/{r.base}'s") if differs else "same", False))
        first_run = unconfirmed(r)
        if first_run:  # the wizard, run automatically on a repo seen for the first time: the proposal is
            prop = init_proposal(r)  # printed, the answer stays the owner's — the script never asks
            print(render_proposal(r, prop))
            pf = r.handoffs / "init-proposal.json"
            if not pf.exists():  # a proposal the SKILL may have edited is never clobbered by an auto-run
                pf = write_proposal_file(r, prop)
            print(f"  (first run in this repo: the proposal above is the one question — `wave init --yes` accepts it, "
                  f"or edit {pf} and `wave init --from {pf}`; answered once, never asked again)")
        gate = r.gate()
        if gate and not gate[0].startswith("<"):
            checks.append(("gate is declared and real", True, " && ".join(gate), True))
        else:
            checks.append(("gate is declared and real", False,
                           "the proposal above is what `wave init --yes` would write for it — answer it first" if first_run else
                           "no gate found; the repo answered init without one — `wave init --again` re-proposes, "
                           "`wave repos add . --gate ...` declares one", True))
        if first_run:
            checks.append(("first run confirmed (init)", False, "nothing accepted yet — the proposal above", False))
        for action in r.ci_gate()[1]:
            checks.append((f"CI runs `{action}` and the gate does not", False, "if it is a gate step, declare its command in `.wave.json`; then add it to `ignore_actions`", False))
        for cmd, where in doc_commands(r.root):
            if cmd not in gate:
                checks.append((f"`{where}` names `{cmd}` and the gate does not", False,
                               "if it is a gate step, declare it in `.wave.json`'s gate", False))
        hooks = r.profile()["hooks"]
        if hooks:
            broken = [h for h in hooks if not h["ok"]]
            checks.append((f"the repo's own hooks can run ({len(hooks)})", not broken,
                           "; ".join(f"{h['path']}: {h['why']}" for h in broken)
                           or ", ".join(h["path"] for h in hooks), False))
        checks.append(("protected paths declared", bool(r.protected), ", ".join(r.protected) or "none: verify cannot guard anything — `repos add . --protected <paths>`", False))
        hdata, hwhy = load_settings(r.root / ".claude" / "settings.json")
        hhave = installed_guards(hdata) if hdata is not None else set()
        hbroken = sorted(n for n in hhave if ping_guard(guard_command(n)))
        hmissing = sorted(n for n, _, _ in GUARD_HOOKS if n not in hhave)
        hsum = f"{len(hhave)}/{len(GUARD_HOOKS)} active"
        if hwhy:
            hsum += f", settings.json {hwhy}"
        if hmissing:
            hsum += f", missing: {', '.join(hmissing)} — `wave hooks install`"
        if hbroken:
            hsum += f", broken: {', '.join(hbroken)}"
        checks.append(("guard hooks installed", not (hwhy or hmissing or hbroken), hsum, False))
        merged = {l.strip().replace("origin/", "") for l in r.git(["branch", "-r", "--merged", f"origin/{r.base}"]).splitlines()}
        alive, dead, stale, exhausted = [], [], [], []
        for wt, branch, n in issue_worktrees(r):
            untouched = (not r.git_ok(["rev-parse", "--verify", f"origin/{branch}"])[0] and not r.git(["log", "--oneline", f"origin/{r.base}..HEAD"], cwd=wt).strip()
                         and not (wt / STAMP).exists() and not r.git(["status", "--porcelain"], cwd=wt).strip())
            if branch in merged or untouched:
                stale.append((wt.name, du_kb(wt)))  # merged, or never dispatched, pushed, committed or edited: leftovers, `wave cleanup`
            elif no_progress(stamp_of(wt)) >= RETRY_CEILING:
                exhausted.append((wt, n))      # past the retry ceiling: dispatch refuses it, the sweep escalates
            elif not pr_for_issue(r, n):       # unmerged work with no PR: an agent at work, or one that died
                lv = liveness(r, n, wt)
                (dead if lv["verdict"] == "likely dead" else alive).append((wt.name, n, lv))
        checks.append(("agents at work (unmerged worktree, no PR yet)", True,
                       ", ".join(f"{name} {lv['verdict']} ({lv['idle_min']} min)" for name, _, lv in alive) or "none", False))
        checks.append((f"no agent likely dead (no PR, nothing changed for more than {DEAD_MIN} min)", not dead,
                       (", ".join(f"{name} ({lv['idle_min']} min)" for name, _, lv in dead)
                        + " — resume the agent with `SendMessage` to its id, or "
                        + ", ".join(f"`wave dispatch --force {n}`" for _, n, _ in dead) + " to start over on top of what is there") if dead else "none", False))
        checks.append((f"no agent past the retry ceiling ({RETRY_CEILING} no-progress dispatches)", not exhausted,
                       ", ".join(f"{wt.name} ({stamp_of(wt).get('resumes')} resumes, {stamp_of(wt).get('respawns', 0)} respawns, "
                                 f"changed {liveness(r, n, wt)['idle_min']} min ago) — "
                                 "dispatch refuses it; `wave sweep <epic> --fix` escalates" for wt, n in exhausted)
                       if exhausted else "none", False))
        if getattr(a, "epic", None):
            er, en = parse_ref(a.epic, r)
            found = sweep_epic(tree(er, en))
            checks.append((f"state sweep of {key(er, en)} (claimed done and closed leaves, against merged PRs)", not found,
                           "; ".join(f"{f['key']} {f['kind']}" for f in found) or "clean", False))
        checks.append(("no leftover worktrees (merged or empty)", not stale,
                       (", ".join(nm for nm, _ in stale) + f" — {fmt_kb(sum(kb for _, kb in stale))} total — `wave cleanup`") if stale else "none", False))
        gate_left = [w for w in re.findall(r"^worktree (.+)$", r.git(["worktree", "list", "--porcelain"]), re.M) if Path(w).name.startswith(CHAIN_GATE_PREFIX)]
        checks.append(("no leftover chain-gate worktree (an interrupted `order --run-gate`)", not gate_left,
                       "; ".join(f"leftover {w} — `git worktree remove --force {w}` (`git worktree prune` if the directory is gone)" for w in gate_left) or "none", False))
        if (r.root / "Cargo.lock").exists():
            home = cargo_home()
            checked, broken = broken_crates(r.root, home)
            moved = []
            quarantine = CONFIG_DIR / "quarantine"
            for d, _, _ in (broken if getattr(a, "fix_cache", False) else []):  # moved aside, never deleted: cargo re-extracts it
                dest = quarantine / f"{d.name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
                try:
                    quarantine.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(d), str(dest))
                    moved.append(f"moved {d.name} to {dest}")
                except OSError as e:  # the crate stays listed as broken, with why it could not be moved
                    moved.append(f"could not move {d.name} to {dest}: {e}")
            broken = [b for b in broken if b[0].exists()]
            if broken:
                ev = ("; ".join([m for m in moved if m.startswith("could not")]
                                + [f"{d.name} is missing {len(m)} of {n} files ({', '.join(m[:3])}{', …' if len(m) > 3 else ''})" for d, m, n in broken])
                      + f" — every fresh build fails inside it; `wave doctor --fix-cache` moves it to {quarantine} and cargo re-extracts it from the checksummed .crate")
            else:
                ev = "; ".join(moved + [f"{checked} extracted crate(s) match their archives in {home / 'registry'}"])
            checks.append(("cargo cache holds every crate Cargo.lock names whole", not broken, ev, True))
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


# ---------------------------------------------------------------- guard hooks

HOOKS_SCRIPT = HERE.parent / "hooks" / "guards.py"
GUARD_HOOKS = [
    # (name, PreToolUse matcher, what it denies) — the prose of templates/agent.md, made environmental
    ("attribution", "Bash", "an AI attribution line in a commit or PR-body command"),
    ("forbidden-git", "Bash", "a destructive git command (gc --prune, reflog expire, stash, reset --hard, clean -f, push --force, branch -D, rm -rf)"),
    ("secret-read", "Bash", "printing the contents of a secret file (the registry's repos.json, .env, credentials, auth.json)"),
    ("dispatch-contract", "Task", "an Agent dispatch whose prompt lacks the contract sections (where, task, gate, delivery, Done when)"),
]


def guard_command(name: str) -> str:
    return f"python3 {HOOKS_SCRIPT} {name}"


def load_settings(path: Path) -> tuple[dict | None, str | None]:
    """(settings, why not): {} when the file does not exist yet, None when it cannot be used as a base."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        return None, f"cannot be read ({e})"
    try:
        data = json.loads(raw)
    except ValueError as e:
        return None, f"does not parse ({e})"
    return (data, None) if isinstance(data, dict) else (None, "is not a JSON object")


def installed_guards(data: dict) -> set[str]:
    """Names of the plugin's guards already present in the settings, whatever else lives in there."""
    blocks = data.get("hooks") or {}
    if not isinstance(blocks, dict):
        return set()
    commands = set()
    for b in blocks.get("PreToolUse") or []:
        if isinstance(b, dict):
            commands.update(h.get("command") for h in b.get("hooks") or [] if isinstance(h, dict))
    return {name for name, _, _ in GUARD_HOOKS if guard_command(name) in commands}


def ping_guard(command: str) -> str | None:
    """None when the hook answers for itself, else why it is broken (a guard that cannot run fails open, so ask it)."""
    ok, out = sh_ok([*shlex.split(command), "--ping"])
    return None if ok else out.strip()[:200] or "exited nonzero with no output"


def cmd_hooks(default: Repo | None, a) -> None:
    """Register the guards in the project's .claude/settings.json: idempotent, backed up, --dry-run to look first."""
    if default is None:
        raise SystemExit("run inside a repo checkout, or pass --repo")
    path = default.root / ".claude" / "settings.json"
    data, why = load_settings(path)
    if a.action == "status":
        print(f"guard hooks of {default.slug} — {path}" + (f" ({why})" if why else ""))
        have = installed_guards(data) if data is not None else set()
        for name, matcher, what in GUARD_HOOKS:
            if name not in have:
                print(f"  {name:<18} missing  ({what}) — `wave hooks install`")
                continue
            broken = ping_guard(guard_command(name))
            print(f"  {name:<18} {'active' if not broken else f'broken   ping failed: {broken} — check the plugin checkout'}")
        return
    if data is None:
        raise SystemExit(f"hooks refused: {path} {why} — fix it by hand; install never overwrites a file it cannot read")
    if not isinstance(data.get("hooks", {}), dict) or not isinstance(data.get("hooks", {}).get("PreToolUse", []), list):
        raise SystemExit(f"hooks refused: {path} has a `hooks` value install cannot extend safely — fix it by hand")
    have = installed_guards(data)
    todo = [(name, matcher) for name, matcher, _ in GUARD_HOOKS if name not in have]
    if not todo:
        print(f"already installed: {len(GUARD_HOOKS)}/{len(GUARD_HOOKS)} guard hooks present in {path}")
        return
    for name, matcher in todo:
        data.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
            {"matcher": matcher, "hooks": [{"type": "command", "command": guard_command(name)}]})
    new_text = json.dumps(data, indent=2) + "\n"
    if a.dry_run:
        print("".join(difflib.unified_diff(path.read_text().splitlines(True) if path.exists() else [],
                                           new_text.splitlines(True), fromfile=str(path), tofile=f"{path} (hooks install)")))
        print(f"{len(todo)} hook(s) would be added: {', '.join(name for name, _ in todo)} — nothing written (--dry-run)")
        return
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
        while backup.exists():
            backup = path.with_name(f"{backup.name}-1")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text)
    for name, matcher in todo:
        print(f"installed {name} (PreToolUse[{matcher}])")
    print(f"wrote {path}" + (f", backup: {backup}" if backup else ""))


# ---------------------------------------------------------------- guarantees

GUARANTEES = [
    # (guard, enforced where, how you see it, tested)
    ("no merge without --yes", "cmd_merge", "`merge` without --yes prints the order and exits", True),
    ("no merge if the base or a PR head moved since the plan", "cmd_merge (--plan)", "`merge --plan` compares SHAs and refuses", False),
    ("no merge of a PR whose issue still has an open blocker", "cmd_merge", "refusal names the blockers", True),
    ("no merge of a PR that verify rejects", "cmd_merge -> verify_one", "attribution, protected, CI, contradictions re-checked at merge time", False),
    ("no merge before CI is green, one PR at a time", "cmd_merge", "waits for checks, stops on FAILED", False),
    ("base CI red after a merge stops the queue", "cmd_merge --wait-base-ci", "exit 4 with the semantic-conflict note", False),
    (".wave.json is read from origin/<base> after the fetch, not from a main checkout left behind; doctor names a stale copy", "Repo._load_cfg <- Repo.fetch, cmd_doctor", "facts config_source: origin/main:.wave.json; ! main checkout's `.wave.json` matches origin/main's: differs in gate", True),
    ("doctor names every CI action the gate does not cover", "cmd_doctor -> Repo.ci_gate", "! CI runs `x/y@v1` and the gate does not", True),
    ("the repo's own orchestration is detected per phase, by name and location, and `.wave.json` phases overrides it", "Repo.profile -> detect_profile", "facts profile.phases: provider + detected | declared | default", True),
    ("the agent prompt carries the profile: repo skills in order before the default stack, hooks enforced, rule files first; nothing found says so", "render_prompt -> profile_prompt", "the {{PROFILE}} block of templates/agent.md", True),
    ("dispatch runs the repo's sync provider before creating worktrees", "cmd_dispatch -> sync_provider", "make sync in <root>; a failure refuses, a `.claude/commands/*` provider is only named", True),
    ("Makefile targets test/check/lint/ci are gate candidates when the manifest falls through", "Repo._manifest_gate -> makefile_targets", "gate_sources: (make test, manifest)", True),
    ("doctor warns when the Makefile or the rule files name a command the gate lacks", "cmd_doctor -> doc_commands", "! `CLAUDE.md` names `make ci` and the gate does not", True),
    ("doctor lists the repo's hooks and warns when one cannot run", "cmd_doctor -> hook_health", "! the repo's own hooks can run (3): <path>: not executable (chmod +x)", True),
    ("plan refuses a body missing the repo's issue-template sections", "cmd_plan -> template_problems", "plan refused: one lacks the repo template's ## Handoff", True),
    ("doctor tells an agent at work from a likely dead one, and names the recovery", "cmd_doctor -> liveness", "! no agent likely dead: <worktrees> — SendMessage or `wave dispatch --force N`", True),
    ("a likely dead worktree stays `worktree` in next, never `in progress`", "candidates -> liveness", "worktree exists (likely dead, nothing changed for N min)", True),
    ("doctor fails on a crate cargo marked extracted but left half-written", "cmd_doctor -> broken_crates", "✗ cargo cache ...: <crate> is missing N of M files", True),
    ("doctor --fix-cache moves a broken crate to quarantine, never deletes", "cmd_doctor --fix-cache", "moved <crate> to ~/.config/setwave/quarantine/<crate>-<time>", True),
    ("doctor warns on a batch larger than MAX_BATCH", "cmd_doctor", "! batch of N is at most 6: the rate-limit and cache history", True),
    ("dispatch --warm fetches once in the main checkout before any worktree; a failed fetch refuses", "cmd_dispatch --warm", "cargo fetch in <root>: done", True),
    ("no dispatch when doctor finds a hard failure", "cmd_dispatch -> cmd_doctor", "dispatch refused", False),
    ("no dispatch of an issue that is not READY", "cmd_dispatch -> premises_for", "refusal lists the failed premises", False),
    ("an existing worktree is kept, never recreated", "cmd_dispatch", "prints 'worktree exists'", False),
    ("cleanup removes only worktrees that are clean, pushed and merged", "cmd_cleanup", "KEEP lines with the reason", True),
    ("cleanup decides by the PR, not by `--merged`: a merged PR's worktree goes (a squash merge and a deleted remote included), a closed-without-merge one stays until --include-closed, an open PR, a dispatch stamp, dirt or an unpushed head always stay; every removal prints the disk it reclaims and the total", "cmd_cleanup -> Repo.prs_for_head, du_kb", "removed k-6: PR #40 merged, reclaimed 12 KB (branch feat/6-merged kept); KEEP k-8: PR #42 open (in progress)", True),
    ("cleanup --stale-days N lists kept worktrees with no open PR and nothing newer than N days, with their size, and removes them only with --yes", "cmd_cleanup --stale-days", "STALE k-9: no PR, newest change 40 days ago, 12 KB — `--yes` to remove", True),
    ("doctor reports leftover worktrees with their total size", "cmd_doctor -> du_kb", "! no leftover worktrees (merged or empty): k-20 — 24 KB total — `wave cleanup`", True),
    ("plan refuses to run twice on the same directory or titles", "cmd_plan", "numbers.json / duplicate titles refusal", False),
    ("adopt turns a milestone, a label or explicit refs into an epic: sub-issues attached, textual dependencies (depends on / blocked by / after / needs, cross-repo) wired as blocked_by; an issue that already has a parent is reported, not moved", "cmd_adopt -> textual_deps, Repo.parent", "adopt report: attached / blocked by / already has a parent — reported, not moved", True),
    ("adopt reads before it writes: a second run attaches nothing twice, wires nothing twice, comments nothing twice", "cmd_adopt (sub_issues, blocked_by and comments read before every write)", "already a sub-issue / already blocked by; the second run makes no write call", True),
    ("adopt never edits a body: a missing `## Done when` gets a comment asking for observable criteria, and the lint output rides the adopt report", "cmd_adopt -> lint_body", "commented: no `## Done when`; lint: ...", True),
    ("adopt refuses to create an epic whose title an issue already carries: an open one is reused, a closed one refuses with ADOPT-TITLE-EXISTS and the ref (--epic N is the explicit door into it, reopening is the owner's call)", "cmd_adopt", 'ADOPT-TITLE-EXISTS: "M1" is o/r#30 (closed) — pass --epic 30 to adopt into it explicitly', True),
    ("every open leaf is in exactly one state", "candidates (assert)", "blocked | in-progress | done-unclosed | agent-exhausted | worktree | ready", True),
    ("an open issue whose every Done-when item is ticked or struck is never dispatched", "candidates + premises_for", "done-unclosed: ... close it or add an item", True),
    ("verify flags AI attribution in body or commits", "verify_one", "AI-ATTRIBUTION", True),
    ("verify flags protected paths touched", "verify_one", "PROTECTED:<paths>", True),
    ("verify flags a PR closing a parent or a still-blocked issue", "verify_one", "CONTRADICTION[...]", True),
    ("verify refuses a PR whose body or any commit message closes an issue other than its own", "verify_one (+ cmd_merge)", "CLOSES-OTHER[<body or commit>: <the keyword and its ref>]", True),
    ("verify refuses a PR whose own issue is closed only by a commit message", "verify_one (+ cmd_merge)", "NO-CLOSES-IN-BODY[#N ...]", True),
    ("verify refuses a PR that changes the version line of .claude-plugin/*.json whose title does not start with `Release` — the bump is the owner's release ritual, one PR of its own", "verify_one (+ cmd_merge)", "VERSION-OUTSIDE-RELEASE[.claude-plugin/plugin.json: \"version\": \"9.9.9\" — ...]", True),
    ("verify refuses a PR without a `## Done when` ledger, or whose items differ from its issue's", "verify_one -> ledger_check (+ cmd_merge)", "LEDGER-MISSING[...] / LEDGER-MISMATCH[...] / ISSUE-NO-DONE-WHEN[...]", True),
    ("merge applies the PR's ledger to its issue after the merge, in one comment, and reopens it while an item is open", "cmd_merge -> apply_ledger -> tick_issue", "ledger applied from PR #N: X done, Y dropped, Z remain", True),
    ("prepare writes only facts into the issue's `## Handoff` — appended, or an existing one replaced in place with every other byte kept — and asks, never answers, the judgement (Done when, evidence, ADR stub), one question per gap, commented once", "cmd_prepare -> prepare_issue, handoff_block, prepare_questions", "wave prepare <issue>: the block, the questions, one gap comment; the same facts twice write nothing", True),
    ("prepare refuses a parent: an issue with sub-issues never receives a Handoff (lint stays as the second net)", "prepare_issue <- cmd_prepare, cmd_dispatch", "exit 1: PREPARE-PARENT: owner/name#N is a parent (N sub-issues); parents are never dispatched", True),
    ("amend writes the owner's answered Done-when once, before the Handoff, and never over one that exists; every line of the file must be an item", "cmd_amend -> with_done_when", "exit 2: amend refused: the body already has a `## Done when`", True),
    ("dispatch prepares an issue whose body has no `## Handoff` itself, so a bare issue works end to end; --no-prepare opts out and a failed write dispatches anyway", "cmd_dispatch -> prepare_issue", "no `## Handoff` in the body — prepare wrote it", True),
    ("the agent prompt names the sections the tool wrote and the ones the issue still lacks, so the agent verifies its premises harder where the issue is thin", "render_prompt -> prepared_note", "the {{PREPARED}} block of templates/agent.md", True),
    ("no merge of a PR the judge never judged: the producer is never the judge of its own PR", "cmd_merge -> judge_check (cmd_judge writes the patch)", "JUDGE-MISSING[no verdict at <handoffs>/judge-<PR>.json]", True),
    ("no merge of a PR the judge failed or held concerns on", "cmd_merge -> judge_check", "JUDGE-FAIL[verdict fail: <file:line, ...>]", True),
    ("no merge on a verdict whose head_sha is not the head being merged: any push invalidates the judgement", "cmd_merge -> judge_check", "JUDGE-STALE[the verdict is for abc1234, the head is def5678]", True),
    ("tick changes only the open lines it names, every other byte kept; a body without `## Done when` is refused", "tick_body (cmd_tick, apply_ledger)", "exit 2: tick refused: ...", True),
    ("verify names sibling issues that cite files the PR touched", "verify_one --epic", "notify lines", False),
    ("order predicts pairwise and chained textual conflicts", "cmd_order", "pairs + chain ✗", True),
    ("order flags serial paths", "cmd_order", "SERIAL line", True),
    ("order --run-gate names the first chain step whose tree fails the gate, and not a step when the base is red", "cmd_order -> gate_chain", "gate: #N✗ + first failing step", True),
    ("the chain gate runs in a detached throwaway worktree, removed whatever happened; doctor names a leftover", "run_gate (finally) + cmd_doctor", "! no leftover chain-gate worktree: leftover <path>", True),
    ("no merge of a plan whose chain failed the gate, unless --force", "cmd_merge (--plan)", "the plan's chain failed the gate at #N", True),
    ("resolve refuses a dirty worktree before merging the base in; it never stashes", "cmd_resolve", "<worktree> has uncommitted changes", True),
    ("resolve leaves a conflict for a human, naming files and line ranges", "cmd_resolve -> conflict_hunks", "exit 2: f.txt: lines 1-5", True),
    ("resolve --continue refuses leftover conflict markers, even once staged", "cmd_resolve --continue", "conflict markers remain", True),
    ("resolve pushes only a gated commit, with a plain push", "cmd_resolve -> run_gate", "exit 3 and nothing pushed on a red gate", True),
    ("lint flags text/data contradictions", "cmd_lint -> lint_body", "CONTRADICTION: ...", True),
    ("lint charges the issue's type template by label, names it and each missing section; no type label warns and charges the common contract", "cmd_lint -> lint_body + lint_type", "[bug] templates/issue/bug.md — missing `## Reprodução`: ...; [no type] the common contract — ok (no type label — ...)", True),
    ("the define skill creates nothing on GitHub: its only door is `wave plan` behind `--dry-run` and an explicit OK", "skills/define/SKILL.md + tests/test_define_skill.py", "the test greps the skill for GitHub write commands (issue create/edit, PR create/merge, `gh api -X POST`) and finds none, and pins the dry-run + OK wording", True),
    ("a define session's plan directory passes lint before anything exists: fill_refs with provisional numbers, then the same lint_body `wave lint` runs on GitHub", "tests/test_define_skill.py (tests/define-plan/)", "the worked example lints clean after fill; deleting its branch line, a sensor, or adding a write command fails the suite", True),
    ("sweep re-verifies claimed done and closed leaves against merged PRs; --fix reverts the lie with a comment and the leaf returns to next", "cmd_sweep -> sweep_epic, apply_sweep", "false-done | false-closed | ledger-not-applied", True),
    ("three no-progress dispatches — plain resumes and respawns with a new strategy alike — exhaust an agent: dispatch refuses the next one, sweep --fix comments the escalation on the issue and the epic, nothing closes", "cmd_dispatch + sweep_retries", "agent-exhausted: 3 dispatches without a new commit (R resumes, P respawns)", True),
    ("the second identical failure is a respawn with the failure context attached, never a third identical resume: --respawn marks it in the stamp", "cmd_dispatch --respawn + skills/wave/SKILL.md step 4", ".setwave.json respawns field; 'respawn with a new strategy' on dispatch", True),
    ("wave stats measures the waves' escalation mix: plain resumes against respawns, from the dispatch log", "cmd_stats <- log_run <- cmd_dispatch", "dispatch escalations: R plain resumes, P respawns with a new strategy", True),
    ("an exhausted worktree is its own exclusive state, never ready", "candidates -> liveness", "agent-exhausted: N resumes without a new commit — dispatch refuses", True),
    ("doctor reports the retry ceiling, and with --epic the state sweep, both soft", "cmd_doctor -> sweep_retries, sweep_epic", "! no agent past the retry ceiling (3 no-progress dispatches ...) / ! state sweep of <epic>", True),
    ("a command run inside a worktree never re-points the registry: it names the main checkout", "Repo.from_cwd (--git-common-dir)", "repos.json path stays the main checkout; a worktree or missing path is repaired to it", True),
    ("a ref to a repo with no checkout is size-checked, cloned into the first search path, registered, and the command continues", "Repo.get -> clone_repo", "the size on GitHub, then 'cloned owner/name into …'; repos.json gains the path", True),
    ("a clone is never shallow: worktrees, merge-tree and --merged need history", "clone_repo (no --depth, is-shallow-repository refused)", "a shallow result exits with 'need history' and registers nothing", True),
    ("cloning asks above clone_ask_over_mb (registry, default 500 MB): the command is printed instead", "clone_repo's diskUsage gate", "'over the 500 MB … gh repo clone owner/name …', nothing cloned or registered", True),
    ("scan and Repo.get agree on the checkout: the registry wins, a second checkout is reported, a dead path is repaired", "find_checkouts + cmd_repos scan", "'another checkout at … — the registry keeps …'", True),
    ("first run in a repo is a wizard: everything detectable is gathered into one proposal, every line marked detected (a file says so) or guessed (convention), and nothing is executed to test a candidate", "cmd_doctor/cmd_dispatch -> unconfirmed -> init_proposal", "doctor prints `wave init <slug> — proposal …` for a repo with no `.wave.json` and no confirmed entry", True),
    ("an answer is written once and never asked again: a `.wave.json`, a `confirmed_at` entry or keys typed by hand silence the proposal; `wave init --yes` / `--from <file>` write it, `--again` is the only reopen", "cmd_init -> accept_proposal + unconfirmed", "`wave init` on an answered repo: `answered already — nothing is asked twice`", True),
    ("with no gate found and no answer, dispatch still refuses and the refusal is the proposal itself, not a pointer to documentation", "cmd_doctor's gate check <- init_proposal", "✗ gate is declared and real: the proposal above is what `wave init --yes` would write for it", True),
    ("doctor from outside any repo reports the registry and the search paths, not just a refusal", "cmd_doctor's default=None branch", "'the registry (…) lists …; search paths: …'", True),
    ("per-repo gh account token, global login untouched", "Repo._env", "GH_TOKEN per call", True),
    ("the per-account token is read from gh's stdout only: a stderr notice never reaches GH_TOKEN, and a stdout that is not one non-empty line is refused with the account named", "token_of <- Repo._env, clone_repo", "GH_TOKEN stays exactly tok-w under a stderr warning; `printed something that is not a token` names --user w", True),
    ("an epic follows sub-issues and blockers into other repos; plan --slug creates there", "tree + candidates, cmd_plan", "keys owner/name#N, gh -R owner/name", True),
    ("wave epics lists every open epic root across the registry's repos, readiest first, one line ending in the exact command to continue", "cmd_epics -> epics_of, epic_stats", "an epic is open, has sub-issues or the `epic` label, and is nobody's sub-issue; a closed epic never appears; --json feeds the SKILL's step 0", True),
    ("every run is logged", "log_run", "~/.config/setwave/log.jsonl", False),
    ("the script never force-pushes, resets, stashes, or deletes", "by absence", "grep the source for 'force', 'reset --hard', 'stash', 'rm -rf': zero hits", True),
    ("the e2e run writes only to a sandbox: never the plugin's own repo, a name without -sandbox only with --any-repo", "scripts/e2e.py check_target", "e2e refused: ...", True),
    ("the e2e fake agent pushes only its issue's own branch, never the base", "scripts/fake_agent.py check_branch", "<branch> is not the branch of #N", True),
    # the guard hooks (hooks/guards.py): the prose rules of templates/agent.md, enforced by the environment. All fail open.
    ("no AI attribution in a commit or PR-body command (fail open: a broken guard exits 0)", "hooks/guards.py attribution <- settings PreToolUse[Bash]", "exit 2: attribution-guard: this command carries AI attribution (...) — drop the trailer", True),
    ("no forbidden git command through Bash — gc --prune, reflog expire, stash, reset --hard, clean -f, push --force, branch -D, rm -rf — each refusal naming the safe alternative (fail open: a broken guard exits 0)", "hooks/guards.py forbidden-git <- settings PreToolUse[Bash]", "exit 2: forbidden-git-guard: `git stash` is refused: commit the work to a branch instead", True),
    ("no secret file's contents are printed — the registry's repos.json, .env, credentials, auth.json; ls, stat and grep -c stay open (fail open: a broken guard exits 0)", "hooks/guards.py secret-read <- settings PreToolUse[Bash]", "exit 2: secret-read-guard: use `ls -la`, `stat` or `grep -c` instead", True),
    ("no Agent dispatch whose prompt lacks the contract sections — where, task, gate, delivery, Done when; a fork is exempt (fail open: a broken guard exits 0)", "hooks/guards.py dispatch-contract <- settings PreToolUse[Task]", "exit 2: dispatch-contract-guard: it lacks the dispatch contract: the gate to run, ...", True),
    # the skill-lint (scripts/validate_skills.py): a skill whose frontmatter is broken silently stops being offered to the tooling
    ("no skill whose frontmatter is broken — name kebab matching its directory, a description of at least 40 chars, no dead template link", "ci.yml skill-lint <- validate_skills.py", "CI red: skills/<dir>/SKILL.md: name `X` differs from its directory ...", True),
]


def cmd_guarantees(default: Repo | None, a):
    """What the plugin promises, where each promise is enforced, and whether a test pins it."""
    # the one guarantee this command proves by itself: no command line in this file is a forbidden git operation.
    # Only argument lists are scanned (a token in quotes next to its verb), so prose and this table do not count.
    forbidden = [('"push"', '--force'), ('"reset"', '"--hard"'), ('"stash"',), ('"clean"', '"-f'), ('"branch"', '"-D"'), ('"rm"', '"-rf"'), ('"gc"', '--prune'), ('"reflog"', '"expire"')]
    for src in sorted(HERE.glob("*.py")):  # wave.py and the scripts beside it: e2e.py, fake_agent.py
        for i, line in enumerate(src.read_text().splitlines(), 1):
            for combo in forbidden:
                if all(tok in line for tok in combo) and "forbidden = [" not in line:
                    raise SystemExit(f"{src.name} line {i} builds a forbidden git command: {line.strip()}")
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


def template_problems(repo: "Repo", d: Path, rows: list[list[str]]) -> list[tuple[str, list[str]]]:
    """(key, missing headings) for each body that lacks a `## ` section the repo's own issue template
    requires: `.claude/issue-templates/<label>.md` for the row's first labelled type, else `default.md`.
    No template, no charge — the plugin's contract stays the one `wave lint` charges on GitHub."""
    tdir = repo.root / ".claude" / "issue-templates"
    if not tdir.is_dir():
        return []
    out = []
    for k, _, labels, _ in rows:
        tpl = next((tdir / f"{l.strip()}.md" for l in labels.split(",") if (tdir / f"{l.strip()}.md").exists()), None)
        if tpl is None and (tdir / "default.md").exists():
            tpl = tdir / "default.md"
        if tpl is None:
            continue
        need = [l.strip() for l in tpl.read_text().splitlines() if l.startswith("## ")]
        body = (d / f"{k}.md").read_text().splitlines()
        missing = [h for h in need if not any(l.strip() == h for l in body)]
        if missing:
            out.append((k, missing))
    return out


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
    problems = template_problems(repo, d, rows)
    if problems and not a.force:
        raise SystemExit("plan refused: " + "; ".join(f"{k} lacks the repo template's {', '.join(m)}"
                                                      for k, m in problems) + " (`--force` creates anyway)")
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


# ---------------------------------------------------------------- adopt

ADOPT_NOTE_TAG = "`wave adopt`"
ADOPT_NOTE = (ADOPT_NOTE_TAG + ": this issue has no `## Done when` list, so `next` cannot tell done from to-do "
              "and a PR cannot carry its ledger. Add one — every item an observable criterion: a test that "
              "passes, a command that prints what it should.")


def textual_deps(text: str, slug: str) -> set[tuple[str, int]]:
    """The (owner/name, n) of every textual dependency in `text`: a verb (`depends on`, `blocked by`, `after`,
    `needs`) followed by a ref — a bare #N is this repo's issue, `owner/name#N` another repo's."""
    return {(m.group(1) or slug, int(m.group(2))) for m in DEPENDS.finditer(text)}


def pick_by_selector(issues: list[dict], milestone: str | None, label: str | None) -> list[dict]:
    """The issues that carry the milestone title or the label, in `gh issue list` order."""
    if milestone:
        return [i for i in issues if (i.get("milestone") or {}).get("title") == milestone]
    return [i for i in issues if label in {l["name"] for l in i["labels"]}]


def cmd_adopt(default: Repo | None, a):
    """Turn an existing milestone, label or explicit issue list into an epic the plugin can run: `--epic N`
    designates the epic, or one is created titled after the milestone/label with the `epic` label — a title
    an open issue already carries is reused, one a closed issue carries refuses with `ADOPT-TITLE-EXISTS`
    (reopening is the owner's call, `--epic N` is the explicit door into it). Every
    selected open issue is attached as a sub-issue — one that already has a parent is reported, not moved —
    and the textual dependencies written in bodies and comments become real `blocked_by`. A body without a
    `## Done when` gets a comment asking for observable criteria, never an edit, and the `wave lint` output
    rides the report. Every read happens before any write, so a second run adds nothing twice."""
    repo = Repo.get(a.slug) if a.slug else default
    if repo is None:
        raise SystemExit("run inside the repo or pass --slug owner/name")
    if bool(a.milestone) + bool(a.label) + bool(a.issues) != 1:
        raise SystemExit('select the issues one way: --milestone "<title>", --label <name>, or issue refs')
    if a.issues and not a.epic:
        raise SystemExit("explicit refs need an epic to join: --epic N")
    issues = json.loads(repo.gh(["issue", "list", "-R", repo.slug, "--state", "all", "--limit", "500",
                                 "--json", "number,id,title,body,state,labels,milestone"]))
    if a.issues:
        picked = []
        for ref in a.issues:
            r, n = parse_ref(ref, repo)
            if r.slug != repo.slug:
                raise SystemExit(f"{key(r, n)}: adopt adopts one repo's issues at a time; run it again for {r.slug}")
            iss = r.issue(n)
            if iss["state"] != "open":
                print(f"{key(r, n)}: closed, not adopted")
                continue
            picked.append(iss)
    else:
        picked = [i for i in pick_by_selector(issues, a.milestone, a.label) if i["state"] == "open"]
    if not picked:
        sel = f'milestone "{a.milestone}"' if a.milestone else f'label "{a.label}"' if a.label else "the refs given"
        raise SystemExit(f"no open issue selected: nothing in {repo.slug} carries {sel}")

    epic_repo, epic_n, created = repo, None, False
    if a.epic:
        epic_repo, epic_n = parse_ref(a.epic, repo)
    else:  # read before create: a second adopt finds the epic the first one made and reuses it
        title = a.milestone or a.label
        hit = next((i for i in issues if i["title"] == title and i["state"] == "open"), None)
        closed = next((i for i in issues if i["title"] == title and i["state"] == "closed"), None)
        if hit is None and closed is not None:  # a closed epic: refusing, never reopening one alone
            raise SystemExit(f'ADOPT-TITLE-EXISTS: "{title}" is {key(repo, closed["number"])} (closed) '
                             f'— pass --epic {closed["number"]} to adopt into it explicitly')
        epic_n = hit["number"] if hit else None
    where = f'milestone "{a.milestone}"' if a.milestone else f'label "{a.label}"' if a.label else "explicit refs"
    print(f"adopt {repo.slug}: {where} — {len(picked)} open issue(s)")

    attached = set()
    if epic_n is not None:
        attached = {(slug_of_api(s["repository_url"]), s["number"]) for s in epic_repo.sub_issues(epic_n)}

    # reads: parent, existing blockers, comments — per issue, before any write below
    entries, unreadable = [], []
    for iss in sorted(picked, key=lambda i: i["number"]):
        n, body = iss["number"], iss.get("body") or ""
        bodies = [c.get("body") or "" for c in repo.api(f"repos/{repo.slug}/issues/{n}/comments")]
        body_deps = textual_deps(body, repo.slug)
        comment_deps = textual_deps("\n".join(bodies), repo.slug) - body_deps
        blockers, unread = [], []
        for slug, dn in sorted((body_deps | comment_deps) - {(repo.slug, n)}):
            try:
                blockers.append((slug, dn, Repo.get(slug).issue(dn)["id"]))
            except SystemExit:
                unread.append((slug, dn, n))
        unreadable += unread
        entries.append({"n": n, "id": iss["id"], "title": iss["title"], "parent": repo.parent(n),
                        "comment_deps": comment_deps, "blockers": blockers,
                        "wired": {(slug_of_api(b["repository_url"]), b["number"]) for b in repo.blocked_by(n)},
                        "attach": epic_n is None or (repo.slug, n) not in attached,
                        "epic_itself": epic_n is not None and (epic_repo.slug, epic_n) == (repo.slug, n),
                        "nudge": done_when(body) is None
                                 and not any(ADOPT_NOTE_TAG in t and "## Done when" in t for t in bodies),
                        "lint": lint_body(body, n, issue_type(iss))})

    if epic_n is None and not a.dry_run:
        name = a.milestone or a.label
        url = repo.gh(["issue", "create", "-R", repo.slug, "--title", name, "--label", "epic", "--body",
                       f"`wave adopt` gathered the open issues of {repo.slug} that carried the "
                       f"{'milestone' if a.milestone else 'label'} \"{name}\" under this issue and wired "
                       f"their textual dependencies as blocked_by."])
        epic_n, created = int(url.strip().rstrip("/").split("/")[-1]), True
    if epic_n is None:  # a dry run with no epic to reuse: only the promise of one
        print(f'epic: would create one titled "{a.milestone or a.label}" with the `epic` label')
    else:
        print(f"epic: {key(epic_repo, epic_n)} (" + (f"created from {where}, label `epic`)" if created else "existing)"))

    counts = {"attached": 0, "wired": 0, "commented": 0}
    for e in entries:
        n, notes = e["n"], []
        if e["parent"] is not None:
            notes.append("already has a parent: " + key(Repo.get(slug_of_api(e["parent"]["repository_url"])),
                                                        e["parent"]["number"]) + " — reported, not moved")
        elif e["epic_itself"]:
            notes.append("is the epic")
        elif not e["attach"]:
            notes.append("already a sub-issue")
        elif a.dry_run:
            notes.append("would attach as a sub-issue")
        else:
            epic_repo.api(f"repos/{epic_repo.slug}/issues/{epic_n}/sub_issues", "POST", {"sub_issue_id": e["id"]})
            counts["attached"] += 1
            notes.append("attached")
        for slug, dn, bid in e["blockers"]:
            if (slug, dn) in e["wired"]:
                notes.append(f"already blocked by {slug}#{dn}")
            elif a.dry_run:
                notes.append(f"would wire blocked by {slug}#{dn}")
            else:
                repo.api(f"repos/{repo.slug}/issues/{n}/dependencies/blocked_by", "POST", {"issue_id": bid})
                counts["wired"] += 1
                notes.append(f"blocked by {slug}#{dn}" + (" (from a comment)" if (slug, dn) in e["comment_deps"] else ""))
        if e["nudge"]:
            if a.dry_run:
                notes.append("would comment: no `## Done when`")
            else:
                repo.gh(["issue", "comment", str(n), "-R", repo.slug, "--body", ADOPT_NOTE])
                counts["commented"] += 1
                notes.append("commented: no `## Done when` (asked for observable criteria)")
        print(f"  #{n} {e['title'][:50]} — " + ("; ".join(notes) if notes else "nothing to do"))
        for m in e["lint"]:
            print(f"      lint: {m}")
    if unreadable:
        print("could not read: " + ", ".join(f"{slug}#{dn} (wanted by #{n})" for slug, dn, n in unreadable))
    print(f"{counts['attached']} sub-issue(s) attached, {counts['wired']} blocked_by wired, "
          f"{counts['commented']} Done-when ask(s) commented" + (" — nothing written (--dry-run)" if a.dry_run else ""))


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(prog="wave", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", help="path inside the default repo (default: cwd)")
    s = p.add_subparsers(dest="cmd", required=True)

    x = s.add_parser("repos", help="registry: list | add <path> [--slug] [--account] [--base] [--gate ...] [--protected ...] | scan")
    x.add_argument("action", choices=["list", "add", "scan"]); x.add_argument("path", nargs="?"); x.add_argument("--slug"); x.add_argument("--account"); x.add_argument("--base"); x.add_argument("--worktree-prefix", help="where worktrees go, e.g. ~/kyte-worktrees/demeter-  (default ../<repo>-)"); x.add_argument("--gate", nargs="*"); x.add_argument("--protected", nargs="*")
    x = s.add_parser("facts", help="what was discovered about repos"); x.add_argument("slugs", nargs="*")
    x = s.add_parser("init", help="first run in a repo: print the one proposal (every line detected or guessed); --yes accepts it, --from <file> writes an edited proposal file, --again reopens a repo already answered")
    x.add_argument("slug", nargs="?", help="owner/name, when not running inside the repo")
    x.add_argument("--yes", action="store_true", help="accept the whole proposal, guessed lines included")
    x.add_argument("--from", dest="from_file", metavar="FILE", help="write an edited init-proposal.json as the answer")
    x.add_argument("--again", action="store_true", help="re-propose for a repo already answered (a `.wave.json` or a confirmed entry)")
    x = s.add_parser("next", help="leaf issues ready to dispatch"); x.add_argument("epic"); x.add_argument("--batch", type=int, default=4); x.add_argument("--json", action="store_true")
    x = s.add_parser("epics", help="every open epic across the registry's repos (--slug / --repo for one), readiest first: leaves, closed, ready, blocked, in progress, last status comment"); x.add_argument("--slug", help="owner/name: scan that repo instead of the whole registry"); x.add_argument("--repo", default=argparse.SUPPRESS, help="path inside a repo: scan that repo instead of the whole registry"); x.add_argument("--json", action="store_true", help="one object per epic, for the SKILL's step 0")
    x = s.add_parser("dispatch", help="create worktrees + prompt files (runs doctor and refuses issues that are not READY)"); x.add_argument("issues", nargs="+"); x.add_argument("--dry-run", action="store_true"); x.add_argument("--force", action="store_true"); x.add_argument("--warm", action="store_true", help="Rust: `cargo fetch` in the main checkout first, so parallel builds only extract"); x.add_argument("--respawn", action="store_true", help="this dispatch returns with a new strategy after the same failure twice: the stamp counts a respawn, not a plain resume"); x.add_argument("--no-prepare", action="store_true", help="do not run `wave prepare` on an issue whose body has no `## Handoff`")
    x = s.add_parser("prepare", help="write the facts of a handoff (branch, worktree, base, gate, skills, non-negotiables, Closes #N) into the issue's `## Handoff` — appended, or an existing one replaced in place — and print the judgement it cannot write, one question per gap, for the orchestrator to ask"); x.add_argument("issues", nargs="+")
    x = s.add_parser("amend", help="write the owner's answered criteria into the issue once: --done-when <file>, one `- [ ] item` per line, written before the Handoff, never over one that exists"); x.add_argument("issue"); x.add_argument("--done-when", metavar="FILE", required=True)
    x = s.add_parser("why", help="the premises behind READY / NOT READY for an issue, with evidence"); x.add_argument("refs", nargs="+")
    x = s.add_parser("doctor", help="preflight: gh, git, clean checkout, gate, protected paths, dead agents, retry ceiling, leftover worktrees, cargo cache, disk, batch size"); x.add_argument("--batch", type=int, default=4); x.add_argument("--fix-cache", action="store_true", help="move each half-extracted crate to ~/.config/setwave/quarantine/ (never deletes)"); x.add_argument("--epic", help="also run the state sweep of this epic as a soft check")
    x = s.add_parser("agents", help="every issue worktree: age since dispatch, minutes since the newest change, PR, verdict (working | quiet | likely dead | done)")
    x = s.add_parser("prompt", help="print the agent prompt for one issue"); x.add_argument("issue"); x.add_argument("--parallel", type=int, default=4)
    x = s.add_parser("verify", help="check PRs: attribution, protected paths, CI, mergeability, worktree"); x.add_argument("prs", nargs="*"); x.add_argument("--epic"); x.add_argument("--json", action="store_true")
    x = s.add_parser("judge", help="write a PR's scoped patch for a read-only judge agent, validate the verdict it writes (merge refuses without a pass at the head)"); x.add_argument("prs", nargs="+")
    x = s.add_parser("order", help="pairwise merge-tree -> merge order, per repo"); x.add_argument("prs", nargs="*"); x.add_argument("--epic"); x.add_argument("--json", action="store_true"); x.add_argument("--plan", help="write the approved delta (base sha + PR heads + order) to this file"); x.add_argument("--run-gate", action="store_true", help="run the repo's gate on the tree after each step of the chain, in a throwaway worktree: names the PR that turns it red")
    x = s.add_parser("merge", help="merge PRs in order, one at a time, CI green before each"); x.add_argument("prs", nargs="*"); x.add_argument("--yes", action="store_true"); x.add_argument("--plan", help="plan file from `order --plan`: refuses if the base or any PR head moved since"); x.add_argument("--force", action="store_true"); x.add_argument("--squash", action="store_true"); x.add_argument("--ci-timeout", type=int, default=1200); x.add_argument("--wait-base-ci", action="store_true")
    x = s.add_parser("resolve", help="merge the base into a conflicting PR's branch in its worktree, gate, push (never --force), comment; a conflict is left for you, then --continue"); x.add_argument("pr"); x.add_argument("--continue", dest="cont", action="store_true")
    x = s.add_parser("close-parents", help="close parents whose sub-issues are all closed"); x.add_argument("epic"); x.add_argument("--include-epic", action="store_true"); x.add_argument("--dry-run", action="store_true")
    x = s.add_parser("cleanup", help="remove worktrees whose work lives elsewhere (a merged PR, an empty seat), printing the disk each removal reclaims; branches stay"); x.add_argument("slugs", nargs="*"); x.add_argument("--dry-run", action="store_true"); x.add_argument("--include-closed", action="store_true", help="also remove worktrees whose PR closed without merging"); x.add_argument("--stale-days", type=int, metavar="N", help="also list kept worktrees with no open PR and nothing newer than N days, with their size; with --yes, remove those too"); x.add_argument("--yes", action="store_true", help="with --stale-days: remove the stale worktrees as well")
    x = s.add_parser("status", help="tree with states; --post comments it on the epic"); x.add_argument("epic"); x.add_argument("--post", action="store_true")
    x = s.add_parser("sweep", help="re-verify claimed done and closed leaves against merged PRs, and the retry ceiling; read-only unless --fix"); x.add_argument("epic"); x.add_argument("--fix", action="store_true", help="apply the state fixes (untick, reopen, apply the ledger) and comment the escalations")
    x = s.add_parser("lint", help="check issues carry what /wave needs"); x.add_argument("issues", nargs="+")
    x = s.add_parser("tick", help="tick (--done) or strike (--strike + --why) items of an issue's `## Done when`, by index or text; what merge does with a PR's ledger"); x.add_argument("issue"); x.add_argument("--done", action="append", metavar="ITEM"); x.add_argument("--strike", action="append", metavar="ITEM"); x.add_argument("--why", action="append", metavar="REASON", help="one per --strike, in the same order"); x.add_argument("--dry-run", action="store_true", help="print the lines that would change, write nothing")
    x = s.add_parser("plan", help="create an epic's issues from <dir>/index.tsv + deps.tsv + <key>.md, wiring sub-issues and blocked_by"); x.add_argument("dir"); x.add_argument("--slug"); x.add_argument("--milestone"); x.add_argument("--dry-run", action="store_true"); x.add_argument("--force", action="store_true")
    x = s.add_parser("adopt", help="turn an existing milestone or label (or explicit refs) into an epic: attaches sub-issues, wires textual dependencies as blocked_by, comments what is missing"); x.add_argument("issues", nargs="*"); x.add_argument("--milestone"); x.add_argument("--label"); x.add_argument("--epic", help="an existing issue to designate as the epic; without it one is created titled after the milestone/label, with the `epic` label"); x.add_argument("--slug"); x.add_argument("--dry-run", action="store_true", help="print what would be wired and what could not be read; write nothing")

    x = s.add_parser("hooks", help="guard hooks: install [--dry-run] writes them into the project's .claude/settings.json (idempotent, backed up); status: active | missing | broken")
    x.add_argument("action", choices=["install", "status"]); x.add_argument("--dry-run", action="store_true")
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
        {"repos": cmd_repos, "facts": cmd_facts, "init": cmd_init, "next": cmd_next, "epics": cmd_epics, "dispatch": cmd_dispatch, "prompt": cmd_prompt,
     "verify": cmd_verify, "judge": cmd_judge, "order": cmd_order, "merge": cmd_merge, "resolve": cmd_resolve, "close-parents": cmd_close_parents,
             "cleanup": cmd_cleanup, "status": cmd_status, "lint": cmd_lint, "plan": cmd_plan, "adopt": cmd_adopt, "why": cmd_why, "doctor": cmd_doctor,
             "agents": cmd_agents, "tick": cmd_tick, "prepare": cmd_prepare, "amend": cmd_amend, "sweep": cmd_sweep, "hooks": cmd_hooks,
         "guarantees": cmd_guarantees}[a.cmd](default, a)
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
        raise
    finally:
        log_run(a, default, time.time() - t0, exit_code)


def log_run(a, default: Repo | None, seconds: float, exit_code: int) -> None:
    """Append one line per run: the raw material for `stats`. Never fails the command."""
    rec = {"at": now_iso(), "cmd": a.cmd, "args": [x for x in sys.argv[1:] if x != a.cmd], "repo": default.slug if default else None,
           "seconds": round(seconds, 1), "exit": exit_code}
    st = getattr(a, "stamp_totals", None)  # a dispatch records the escalation mix it stamped: plain resumes vs respawns
    if st:
        rec["resumes"], rec["respawns"] = st["resumes"], st["respawns"]
    append_log(rec)


def append_log(rec: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
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
    d = by.get("dispatch", [])
    resumes = sum(r.get("resumes", 0) for r in d)
    respawns = sum(r.get("respawns", 0) for r in d)
    if resumes or respawns:
        print(f"\ndispatch escalations: {resumes} plain resumes, {respawns} respawns with a new strategy")


if __name__ == "__main__":
    main()
