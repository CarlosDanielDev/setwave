"""A command that only looks never writes: a repo with no checkout is `not cloned` in the output of
`epics`, `status` and `why`, and the clone, the registry and the disk stay exactly as they were
(`stats` and `guarantees` never resolve a repo at all). The commands that do the work (`next`,
`dispatch`, `prepare`, `prompt`) keep cloning — the checkout is what they work on (#50).

o/x has no checkout here (a 2 MB fixture in the fake gh); a registry entry with an account but no
path is the shape a repo has after it was registered and its checkout went away.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import CLONE, TMP, WAVE, run_wave, sandbox

X = TMP / "x"
REG = TMP / "config" / "repos.json"
LOG = TMP / "gh-read.log"


def setUpModule():
    sandbox()
    os.environ["FAKE_GH_CLONE"] = "1"


def tearDownModule():
    os.environ.pop("FAKE_GH_CLONE", None)


def wave_logged(*args, cwd=None):
    LOG.write_text("")
    return subprocess.run([sys.executable, str(WAVE), *args], cwd=cwd or CLONE, capture_output=True, text=True,
                          env={**os.environ, "FAKE_GH_LOG": str(LOG)})


def cloned() -> bool:
    return any(json.loads(l)[:2] == ["repo", "clone"] for l in LOG.read_text().splitlines())


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(X, ignore_errors=True)
        reg = json.loads(REG.read_text())
        reg["repos"]["o/x"] = {"account": "acct"}  # registered, no path, no checkout
        REG.write_text(json.dumps(reg))
        self.before = REG.read_bytes()

    def tearDown(self):
        reg = json.loads(REG.read_text())
        reg["repos"].pop("o/x", None)
        REG.write_text(json.dumps(reg))
        shutil.rmtree(X, ignore_errors=True)


class ReadingNeverClones(Base):
    def assert_untouched(self, p):
        self.assertFalse(cloned(), "a read command ran `gh repo clone`")
        self.assertFalse(X.exists(), "a read command wrote a checkout to disk")
        self.assertEqual(REG.read_bytes(), self.before, "a read command wrote the registry")
        self.assertNotIn("cloned o/x", p.stderr)

    def test_epics_of_one_slug_says_not_cloned_and_the_command_that_resolves_it(self):
        p = wave_logged("epics", "--slug", "o/x")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("o/x: not cloned", p.stderr)
        self.assertIn(f"gh repo clone o/x {X}", p.stderr)
        self.assertIn(f"repos add {X} --slug o/x", p.stderr)
        self.assert_untouched(p)

    def test_epics_over_the_registry_skips_the_missing_repo_as_not_cloned(self):
        p = wave_logged("epics")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("skip o/x: not cloned", p.stderr)
        self.assert_untouched(p)

    def test_status_and_why_say_not_cloned(self):
        for cmd in ("status", "why"):
            with self.subTest(cmd):
                p = wave_logged(cmd, "o/x#1")
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("o/x: not cloned", p.stderr)
                self.assert_untouched(p)

    def test_stats_and_guarantees_resolve_no_repo_at_all(self):
        for cmd in ("stats", "guarantees"):
            with self.subTest(cmd):
                p = wave_logged(cmd)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assert_untouched(p)

    def test_a_checkout_found_on_disk_is_used_without_touching_the_registry(self):
        subprocess.run(["git", "init", "-q", str(X)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(X), "remote", "add", "origin", "https://github.com/o/x.git"],
                       check=True, capture_output=True)
        p = wave_logged("why", "o/x#1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("o/x#1 — ", p.stdout, "the premises were read from the checkout found on disk")
        self.assertNotIn("not cloned", p.stderr)
        self.assertFalse(cloned())
        self.assertEqual(REG.read_bytes(), self.before, "a read command repaired the registry")


class WorkingStillClones(Base):
    def test_next_dispatch_prepare_and_prompt_clone_the_missing_repo(self):
        for args in (("next", "o/x#1"), ("dispatch", "--dry-run", "o/x#1"), ("prepare", "o/x#1"), ("prompt", "o/x#1")):
            with self.subTest(args[0]):
                shutil.rmtree(X, ignore_errors=True)
                reg = json.loads(REG.read_text())
                reg["repos"]["o/x"] = {"account": "acct"}
                REG.write_text(json.dumps(reg))
                p = wave_logged(*args)
                self.assertIn(f"cloned o/x into {X}", p.stderr, p.stdout + p.stderr)
                self.assertTrue(cloned())
                self.assertEqual(json.loads(REG.read_text())["repos"]["o/x"]["path"], str(X))


if __name__ == "__main__":
    unittest.main()
