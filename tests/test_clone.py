"""A ref to a repo with no local checkout runs anyway: the repo is size-checked, cloned into the
first search path, registered, and the command continues. The clone is never shallow.

The fake `gh` grows `repo clone` (creates a bare fixture at the destination, opt-in via
FAKE_GH_CLONE) and `repo view --json diskUsage` (per-repo fixture, KB). Repos here: o/x (2 MB,
no checkout), o/big (~879 MB, over the default 500), o/ax (registered with account `acct`, no
path) and o/two-ck with two checkouts, the registry naming the one that sorts second.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import CLONE, TMP, WAVE, run_wave, sandbox, wave

X = TMP / "x"
AX = TMP / "ax"
CK1, CK2 = TMP / "ck1", TMP / "ck2"
GONE = TMP / "gone-13"
REG = TMP / "config" / "repos.json"


def setUpModule():
    sandbox()
    os.environ["FAKE_GH_CLONE"] = "1"
    if CK1.exists():
        return
    reg = json.loads(REG.read_text())
    reg["repos"]["o/ax"] = {"account": "acct"}     # the account a clone must run as, before any path exists
    reg["repos"]["o/two-ck"] = {"path": str(CK2)}  # the registry wins over the checkout that sorts first
    REG.write_text(json.dumps(reg))
    for d in (CK1, CK2):
        subprocess.run(["git", "init", "-q", str(d)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(d), "remote", "add", "origin", "https://github.com/o/two-ck.git"],
                       check=True, capture_output=True)


def tearDownModule():
    os.environ.pop("FAKE_GH_CLONE", None)


def reg_entry(slug: str) -> dict:
    return json.loads(REG.read_text())["repos"].get(slug, {})


def set_entry(slug: str, entry: dict | None) -> None:
    reg = json.loads(REG.read_text())
    if entry is None:
        reg["repos"].pop(slug, None)
    else:
        reg["repos"][slug] = entry
    REG.write_text(json.dumps(reg))


def wave_quiet(*args, cwd=None, extra_env=None):
    """`wave.py <args>` with fresh gh call logs, so a test reads only the calls it caused."""
    (TMP / "gh-clone.log").write_text("")
    (TMP / "gh-clone-env.log").write_text("")
    env = {**os.environ, "FAKE_GH_LOG": str(TMP / "gh-clone.log"),
           "FAKE_GH_ENV_LOG": str(TMP / "gh-clone-env.log"), **(extra_env or {})}
    return subprocess.run([sys.executable, str(WAVE), *args], cwd=cwd or sandbox(),
                          capture_output=True, text=True, env=env)


def calls() -> list[list[str]]:
    log = TMP / "gh-clone.log"
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def env_calls() -> list[dict]:
    log = TMP / "gh-clone-env.log"
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def facts_of(out: str) -> dict:
    """The facts JSON, printed after the clone's own lines."""
    doc, _ = json.JSONDecoder().raw_decode(out[out.index("{"):])
    return doc


class ClonedFromAnywhere(unittest.TestCase):
    def tearDown(self):
        set_entry("o/x", None)
        set_entry("o/ax", {"account": "acct"})
        shutil.rmtree(X, ignore_errors=True)  # no checkout again, the state every test here starts from

    def test_a_ref_to_a_missing_repo_is_cloned_registered_and_the_command_continues(self):
        p = wave_quiet("facts", "o/x", cwd=CLONE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"cloned o/x into {X}", p.stderr)
        self.assertEqual(facts_of(p.stdout)["root"], str(X))
        self.assertEqual(reg_entry("o/x"), {"path": str(X)}, "the clone is registered, so the next ref finds it")
        self.assertIn(["repo", "clone", "o/x", str(X)], calls())

    def test_the_size_is_printed_before_cloning(self):
        p = wave_quiet("facts", "o/x", cwd=CLONE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("o/x is 2.0 MB on GitHub", p.stderr)

    def test_the_clone_never_passes_a_depth(self):
        wave_quiet("facts", "o/x", cwd=CLONE)
        self.assertTrue([c for c in calls() if c[:2] == ["repo", "clone"]], "the clone ran at all")
        self.assertFalse(any("--depth" in c for c in calls()), "a --depth would cut the history worktrees need")

    def test_a_shallow_clone_is_refused_instead_of_registered(self):
        p = wave_quiet("facts", "o/x", cwd=CLONE, extra_env={"FAKE_GH_SHALLOW": "1"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("shallow", p.stderr)
        self.assertIn("need history", p.stderr)
        self.assertIn(["repo", "clone", "o/x", str(X)], calls(), "the clone ran; the refusal is what registered nothing")
        self.assertIsNone(reg_entry("o/x").get("path"), "a clone without history must not be registered")

    def test_the_clone_runs_as_the_registry_entry_account(self):
        p = wave_quiet("facts", "o/ax", cwd=CLONE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        sized = [c for c in env_calls() if "diskUsage" in c["args"]]
        cloned = [c for c in env_calls() if c["args"][:2] == ["repo", "clone"]]
        self.assertTrue(sized and cloned, env_calls())
        self.assertTrue(all(c["GH_TOKEN"] == "tok-acct" for c in sized + cloned),
                        "the account the registry entry names, not the active login")
        self.assertEqual(reg_entry("o/ax"), {"account": "acct", "path": str(AX)})

    def test_over_the_default_limit_it_stops_and_prints_the_clone_command(self):
        p = wave_quiet("facts", "o/big", cwd=CLONE)
        self.assertEqual(p.returncode, 1)
        self.assertIn("o/big is 878.9 MB on GitHub", p.stderr)
        self.assertIn("over the 500 MB", p.stdout + p.stderr)
        self.assertIn("gh repo clone o/big", p.stdout + p.stderr)
        self.assertFalse([c for c in calls() if c[:2] == ["repo", "clone"]], "nothing was cloned")
        self.assertEqual(reg_entry("o/big"), {}, "nothing was registered either")

    def test_the_registry_can_lower_the_threshold(self):
        reg = json.loads(REG.read_text())
        reg["clone_ask_over_mb"] = 1
        REG.write_text(json.dumps(reg))
        try:
            p = wave_quiet("facts", "o/x", cwd=CLONE)
            self.assertEqual(p.returncode, 1)
            self.assertIn("over the 1 MB", p.stdout + p.stderr)
            self.assertFalse([c for c in calls() if c[:2] == ["repo", "clone"]])
        finally:
            reg = json.loads(REG.read_text())
            reg.pop("clone_ask_over_mb", None)
            REG.write_text(json.dumps(reg))

    def test_a_slug_without_an_owner_is_refused_not_crashed_into(self):
        p = wave_quiet("facts", "justname", cwd=CLONE)
        self.assertEqual(p.returncode, 1)
        self.assertIn("owner/name", p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stdout + p.stderr)
        self.assertFalse([c for c in calls() if c[:2] == ["repo", "view"] or c[:2] == ["repo", "clone"]])

    def test_no_search_paths_is_refused_not_crashed_into(self):
        reg = json.loads(REG.read_text())
        reg["search_paths"] = []
        REG.write_text(json.dumps(reg))
        try:
            p = wave_quiet("facts", "o/x", cwd=CLONE)
            self.assertEqual(p.returncode, 1)
            self.assertIn("search paths", p.stdout + p.stderr)
            self.assertNotIn("Traceback", p.stdout + p.stderr)
        finally:
            reg = json.loads(REG.read_text())
            reg["search_paths"] = [str(TMP)]
            REG.write_text(json.dumps(reg))


class RegistryWins(unittest.TestCase):
    def tearDown(self):
        set_entry("o/two-ck", {"path": str(CK2)})
        wave.Repo._cache.pop("o/two-ck", None)

    def test_scan_keeps_the_registered_checkout_and_reports_the_second(self):
        p = run_wave("repos", "scan", cwd=CLONE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(reg_entry("o/two-ck")["path"], str(CK2), "the registry wins over the sort order")
        self.assertIn(f"o/two-ck: another checkout at {CK1} — the registry keeps {CK2}", p.stdout)

    def test_repo_get_keeps_the_registered_checkout(self):
        wave.Repo._cache.pop("o/two-ck", None)
        self.assertEqual(wave.Repo.get("o/two-ck").root, CK2)

    def test_a_second_checkout_is_reported_not_silently_chosen(self):
        set_entry("o/two-ck", {"path": str(GONE)})  # the registered path is gone: the pick must still be said out loud
        p = wave_quiet("facts", "o/two-ck", cwd=CLONE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("2 checkouts", p.stdout)
        self.assertIn(f"also at {CK2}", p.stdout)
        self.assertEqual(reg_entry("o/two-ck")["path"], str(CK1), "the first sorted, with the second reported")
        self.assertEqual(facts_of(p.stdout)["root"], str(CK1))

    def test_scan_repairs_a_dead_registered_path_the_way_repo_get_does(self):
        set_entry("o/two-ck", {"path": str(GONE)})
        p = run_wave("repos", "scan", cwd=CLONE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(reg_entry("o/two-ck")["path"], str(CK1), "dead in the registry, alive on disk: both agree")


class DoctorOutsideAnyRepo(unittest.TestCase):
    def test_it_reports_the_registry_and_search_paths(self):
        p = run_wave("doctor", cwd=TMP)  # TMP is inside a search path but is no git repo: no default repo
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)  # still not fit to dispatch — now it says what it sees
        line = next(l for l in p.stdout.splitlines() if "inside a registered repo" in l)
        self.assertIn(str(TMP / "config" / "repos.json"), line)
        self.assertIn("search paths", line)
        self.assertIn(str(TMP), line)
        self.assertIn("o/r", line, "the repos the registry already holds")
        self.assertIn("cloned", line, "what happens to a ref whose repo is not there")


if __name__ == "__main__":
    unittest.main()
