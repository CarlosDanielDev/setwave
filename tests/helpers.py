"""Shared by every test: a sandbox that makes `wave.py` run offline.

`sandbox()` builds, once per test run, a temporary bare remote `o/r` and a clone of it,
with branches whose merges conflict (t/a x t/b) or not (t/c, feat/4-x), puts the fake `gh`
(tests/bin/gh, answering from tests/fixtures) first on PATH, and points the registry and the
run log at the temporary directory. Nothing outside that directory is read or written:
git runs with a throwaway global config, and the registry never scans ~/projects.
"""
import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
WAVE = TESTS.parent / "scripts" / "wave.py"

_tmp = tempfile.TemporaryDirectory(prefix="setwave-tests-")
atexit.register(_tmp.cleanup)
TMP = Path(_tmp.name).resolve()
CLONE = TMP / "r"

(TMP / "gitconfig").write_text("[user]\n\tname = Test\n\temail = test@example.com\n"
                               "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
os.environ.update({
    "PATH": f"{TESTS / 'bin'}{os.pathsep}{os.environ['PATH']}",
    "SETWAVE_CONFIG": str(TMP / "config"),
    "GIT_CONFIG_GLOBAL": str(TMP / "gitconfig"),
    "GIT_CONFIG_NOSYSTEM": "1",
    "FAKE_GH_LOG": str(TMP / "gh.log"),
})
os.environ.pop("GH_TOKEN", None)
# a fake gh that lost its executable bit would let the real one answer, against real GitHub: refuse to run
if shutil.which("gh") != str(TESTS / "bin" / "gh"):
    raise SystemExit(f"tests/bin/gh is not the gh on PATH ({shutil.which('gh')}): `chmod +x tests/bin/gh`")

_spec = importlib.util.spec_from_file_location("wave", WAVE)
wave = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wave)

_built = False


def git(*args, cwd=CLONE):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def commit_on(branch: str, path: str, text: str, message: str = ""):
    git("checkout", "-q", "-b", branch, "main")
    (CLONE / path).parent.mkdir(parents=True, exist_ok=True)
    (CLONE / path).write_text(text)
    git("add", path)
    git("commit", "-q", "-m", message or f"{branch}: {path}")
    git("push", "-q", "origin", branch)
    git("checkout", "-q", "main")


def sandbox() -> Path:
    """The clone of o/r, built on first use. Branches: t/a and t/b conflict on f.txt; t/c adds a serial file;
    t/locked touches a protected path; t/trailer carries an AI trailer."""
    global _built
    if _built:
        return CLONE
    bare = TMP / "remote" / "o" / "r.git"
    bare.parent.mkdir(parents=True)
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(CLONE), cwd=TMP)
    (CLONE / "f.txt").write_text("one\ntwo\n")
    (CLONE / ".wave.json").write_text(json.dumps({"base": "main", "serial": ["s/"], "protected": ["locked/"]}))
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("push", "-q", "origin", "main")
    commit_on("t/a", "f.txt", "a\ntwo\n")
    commit_on("t/b", "f.txt", "b\ntwo\n")
    commit_on("t/c", "s/x.txt", "serial\n")
    commit_on("feat/4-x", "g.txt", "g\n")
    commit_on("feat/3-y", "h.txt", "h\n")
    commit_on("t/locked", "locked/k.txt", "k\n")
    # the global config is a throwaway one, so no commit-msg hook strips this trailer: verify must see it
    commit_on("t/trailer", "t.txt", "t\n", "Add t.txt\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    (TMP / "r-5").mkdir()  # the worktree path of #5: its existence is the fact `candidates` reads
    (TMP / "config").mkdir()
    (TMP / "config" / "repos.json").write_text(json.dumps({"search_paths": [str(TMP)], "repos": {"o/r": {"path": str(CLONE)}}}))
    _built = True
    return CLONE


def run_wave(*args, cwd=None) -> subprocess.CompletedProcess:
    """`wave.py <args>` as a user runs it, from inside the clone (or `cwd`), against the fake gh."""
    return subprocess.run([sys.executable, str(WAVE), *args], cwd=cwd or sandbox(), capture_output=True, text=True)


def gh_calls() -> list[list[str]]:
    log = TMP / "gh.log"
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
