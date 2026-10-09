"""The per-account token comes from `gh auth token --user <account>` stdout only (issue #27).

gh writes update notices and keyring warnings to stderr, and `sh_ok` returns stdout + stderr —
one notice concatenated into GH_TOKEN silently 401s every later gh call for that repo. The fake
gh here always prints a warning on stderr next to `tok-<account>` on stdout, so any code path
that joins the streams fails these tests. Repos: w/one (its own checkout, account `w` — the
`Repo._env` path) and o/wx (registered with account `w`, no path — `clone_repo` reads it).
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import CLONE, TMP, WAVE, sandbox, wave

WX = TMP / "wx"
WONE = TMP / "w-one"
REG = TMP / "config" / "repos.json"


def setUpModule():
    sandbox()
    if WONE.exists():
        return
    # w/one: a checkout of its own, registered with account `w` — every Repo call reads its token first
    bare = TMP / "remote" / "w-one.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    for cmd in (["git", "init", "-q", "--bare", str(bare)],
                ["git", "clone", "-q", str(bare), str(WONE)]):
        subprocess.run(cmd, check=True, capture_output=True)
    # no .wave.json here on purpose: with a base declared, Repo.__init__ never calls gh and no
    # call would carry the token; without one, `gh repo view` runs under the account's GH_TOKEN
    for cmd in (["git", "-C", str(WONE), "add", "."],
                ["git", "-C", str(WONE), "commit", "-q", "-m", "base", "--allow-empty"],
                ["git", "-C", str(WONE), "push", "-q", "origin", "main"]):
        subprocess.run(cmd, check=True, capture_output=True)
    reg = json.loads(REG.read_text())
    reg["repos"]["w/one"] = {"path": str(WONE), "account": "w"}
    reg["repos"]["o/wx"] = {"account": "w"}  # no path: clone_repo asks for the token instead
    REG.write_text(json.dumps(reg))


def tearDownModule():
    reg = json.loads(REG.read_text())
    for slug in ("w/one", "o/wx"):
        reg["repos"].pop(slug, None)
    REG.write_text(json.dumps(reg))


def set_entry(slug: str, entry: dict | None) -> None:
    reg = json.loads(REG.read_text())
    if entry is None:
        reg["repos"].pop(slug, None)
    else:
        reg["repos"][slug] = entry
    REG.write_text(json.dumps(reg))


def reg_entry(slug: str) -> dict:
    return json.loads(REG.read_text())["repos"].get(slug, {})


def run_quiet(*args, extra_env=None):
    """`wave.py <args>` with fresh gh call logs, so a test reads only the calls it caused."""
    (TMP / "gh-token.log").write_text("")
    (TMP / "gh-token-env.log").write_text("")
    env = {**os.environ, "FAKE_GH_LOG": str(TMP / "gh-token.log"),
           "FAKE_GH_ENV_LOG": str(TMP / "gh-token-env.log"), **(extra_env or {})}
    return subprocess.run([sys.executable, str(WAVE), *args], cwd=CLONE, capture_output=True, text=True, env=env)


def env_calls() -> list[dict]:
    log = TMP / "gh-token-env.log"
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()] if log.exists() else []


class StdoutOnly(unittest.TestCase):
    """The warning sits on stderr of every successful `auth token`; GH_TOKEN must stay exactly tok-w."""

    def tearDown(self):
        set_entry("o/wx", {"account": "w"})   # a clone test may have let wave register the path
        shutil.rmtree(WX, ignore_errors=True)  # no checkout again, the state every test here starts from

    def test_a_stderr_warning_never_reaches_the_token(self):
        p = run_quiet("facts", "w/one")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        carried = [c for c in env_calls() if c["args"][:2] != ["auth", "token"]]
        self.assertTrue(carried, f"a gh call ran under the account: {env_calls()}")
        self.assertEqual({c["GH_TOKEN"] for c in carried}, {"tok-w"},
                         "the warning joined the streams into GH_TOKEN")

    def test_the_clone_reads_the_token_the_same_way(self):
        p = run_quiet("facts", "o/wx", extra_env={"FAKE_GH_CLONE": "1"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"cloned o/wx into {WX}", p.stderr)
        tokened = [c for c in env_calls() if "diskUsage" in " ".join(c["args"]) or c["args"][:2] == ["repo", "clone"]]
        self.assertTrue(tokened, env_calls())
        self.assertEqual({c["GH_TOKEN"] for c in tokened}, {"tok-w"},
                         "the warning joined the streams into GH_TOKEN")
        self.assertEqual(reg_entry("o/wx"), {"account": "w", "path": str(WX)})


class Refusals(unittest.TestCase):
    def test_a_stdout_that_is_not_one_token_line_is_refused_naming_the_account(self):
        for mode in ("two", "empty"):
            with self.subTest(mode=mode):
                p = run_quiet("facts", "w/one", extra_env={"FAKE_GH_TOKEN": mode})
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("not a token", p.stdout + p.stderr)
                self.assertIn("--user w", p.stdout + p.stderr, "the account is named")
                self.assertNotIn("Traceback", p.stdout + p.stderr)

    def test_a_failed_token_command_keeps_stderr_for_the_message_and_never_echoes_stdout(self):
        p = run_quiet("facts", "w/one", extra_env={"FAKE_GH_TOKEN": "fail"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("not logged in", p.stdout + p.stderr)
        self.assertIn("fake gh: no token for w (not logged in)", p.stdout + p.stderr,
                      "stderr is kept for the error message")
        self.assertNotIn("tok-", p.stdout + p.stderr,
                         "stdout may hold a token: it is never echoed into the message")


class NeverPrinted(unittest.TestCase):
    def test_the_token_never_appears_in_output_or_the_run_log(self):
        ok = run_quiet("facts", "w/one")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        bad = run_quiet("facts", "w/one", extra_env={"FAKE_GH_TOKEN": "fail"})
        self.assertEqual(bad.returncode, 1)
        for out in (ok.stdout, ok.stderr, bad.stdout, bad.stderr):
            self.assertNotIn("tok-", out)
        log = TMP / "config" / "log.jsonl"
        if log.exists():
            self.assertNotIn("tok-", log.read_text())


class Pinned(unittest.TestCase):
    def test_the_guarantee_row_exists_and_is_pinned_by_a_test(self):
        rows = [g for g in wave.GUARANTEES if "stdout" in g[0] and "token" in g[0]]
        self.assertTrue(rows, "the guarantees table carries the stdout-only token row")
        self.assertTrue(rows[0][3], "the row is pinned by a test")


if __name__ == "__main__":
    unittest.main()
