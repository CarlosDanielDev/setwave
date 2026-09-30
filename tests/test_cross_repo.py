"""An epic that spans two repositories, each owned by its own `gh` account.

Two more remotes and clones, a/one and b/two, registered with accounts `a` and `b`. The fake `gh`
answers `gh auth token --user X` with `tok-X` and records, per call, the GH_TOKEN it ran with.
  a/one#1  the epic; sub-issues a/one#9 and b/two#5
  a/one#9  ready
  b/two#5  blocked by a/one#9 (a blocker in another repo); its worktree two-5 exists next to b/two's checkout
"""
import json
import os
import subprocess
import sys
import unittest

from helpers import TMP, WAVE, git, gh_env_calls, run_wave, sandbox

ONE, TWO = TMP / "one", TMP / "two"
ACCOUNTS = {"a/one": "a", "b/two": "b"}


def setUpModule():
    sandbox()
    if TWO.exists():
        return
    reg_file = TMP / "config" / "repos.json"
    reg = json.loads(reg_file.read_text())
    for slug, clone in (("a/one", ONE), ("b/two", TWO)):
        bare = TMP / "remote" / f"{slug}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "--bare", str(bare), cwd=TMP)
        git("clone", "-q", str(bare), str(clone), cwd=TMP)
        (clone / ".wave.json").write_text(json.dumps({"base": "main"}))
        git("add", ".", cwd=clone)
        git("commit", "-q", "-m", "base", cwd=clone)
        git("push", "-q", "origin", "main", cwd=clone)
        reg["repos"][slug] = {"path": str(clone), "account": ACCOUNTS[slug]}
    reg_file.write_text(json.dumps(reg))
    (TMP / "two-5").mkdir()


def calls_of(run) -> dict[str, list[dict]]:
    """The gh calls `run()` made, grouped by the repo each one addresses (`-R slug` or an api path)."""
    before = len(gh_env_calls())
    p = run()
    by: dict[str, list[dict]] = {}
    for c in gh_env_calls()[before:]:
        args = c["args"]
        slug = args[args.index("-R") + 1] if "-R" in args else next(
            ("/".join(x.split("/")[1:3]) for x in args if x.startswith("repos/")), None)
        by.setdefault(slug, []).append(c)
    return p, by


class Tokens(unittest.TestCase):
    def test_each_repo_is_called_with_its_own_account_token(self):
        p, by = calls_of(lambda: run_wave("next", "a/one#1", cwd=ONE))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(by.get("a/one") and by.get("b/two"), by.keys())
        for slug, account in ACCOUNTS.items():
            with self.subTest(slug=slug):
                self.assertEqual({c["GH_TOKEN"] for c in by[slug]}, {f"tok-{account}"}, by[slug])

    def test_the_token_is_asked_once_per_account_and_the_global_login_is_never_switched(self):
        _, by = calls_of(lambda: run_wave("status", "a/one#1", cwd=ONE))
        every = [c["args"] for cs in by.values() for c in cs]
        self.assertEqual(sorted(a for a in every if a[:2] == ["auth", "token"]),
                         [["auth", "token", "--user", "a"], ["auth", "token", "--user", "b"]])
        self.assertFalse([a for a in every if a[:2] == ["auth", "switch"]])


class CrossRepoEpic(unittest.TestCase):
    def test_next_keys_and_worktree_path(self):
        p = run_wave("next", "a/one#1", "--json", cwd=ONE)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual([c["key"] for c in out["ready"]], ["a/one#9"])
        five = {c["key"]: c for c in out["all"]}["b/two#5"]
        self.assertEqual((five["repo"], five["state"], five["blocked_by"]), ("b/two", "blocked", ["a/one#9"]))
        self.assertEqual(five["worktree"], str(TMP / "two-5"), "the worktree of b/two#5 sits next to b/two's checkout")

    def test_why_names_the_blocker_in_the_other_repo(self):
        p = run_wave("why", "b/two#5", cwd=ONE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("b/two#5 — Leaf in another repo", p.stdout)
        self.assertIn("✗ every blocker is closed: a/one#9 open", p.stdout)
        self.assertIn(f"✗ no worktree exists for it: {TMP / 'two-5'}", p.stdout)

    def test_status_lists_both_repos_and_keys_the_foreign_leaf(self):
        p = run_wave("status", "a/one#1", cwd=ONE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("- `a/one` base `main` = ", p.stdout)
        self.assertRegex(p.stdout, r"- `b/two` base `main` = `[0-9a-f]+`, account `b`")
        self.assertIn("  - [ ] #9 Leaf in the epic's repo — READY", p.stdout)
        self.assertIn("  - [ ] b/two#5 Leaf in another repo, blocked across repos — blocked by a/one#9", p.stdout)
        self.assertIn("next wave: a/one#9\n", p.stdout)


class PlanInAnotherRepo(unittest.TestCase):
    def test_plan_slug_creates_in_that_repo_with_its_token(self):
        d = TMP / "plan-b"
        d.mkdir()
        (d / "index.tsv").write_text("x\tA new leaf for b/two\t-\t-\n")
        (d / "x.md").write_text("Body.\n")
        # a separate call log: this test lets `issue create` through, and NoWrites reads the shared one
        env = {**os.environ, "FAKE_GH_CREATE": "5", "FAKE_GH_LOG": str(TMP / "gh-plan.log"),
               "FAKE_GH_ENV_LOG": str(TMP / "gh-plan-env.log")}
        p = subprocess.run([sys.executable, str(WAVE), "plan", str(d), "--slug", "b/two"], cwd=ONE,
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = [json.loads(l) for l in (TMP / "gh-plan-env.log").read_text().splitlines()]
        creates = [c for c in calls if c["args"][:2] == ["issue", "create"]]
        self.assertEqual(len(creates), 1, calls)
        self.assertEqual(creates[0]["args"][2:4], ["-R", "b/two"])
        self.assertEqual(creates[0]["GH_TOKEN"], "tok-b")
        self.assertFalse([c for c in calls if "a/one" in " ".join(c["args"])], "nothing was asked of a/one")
        self.assertEqual(json.loads((d / "numbers.json").read_text()), {"x": 5})


class Facts(unittest.TestCase):
    def test_facts_names_the_account(self):
        p = run_wave("facts", "b/two", cwd=ONE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((json.loads(p.stdout)["slug"], json.loads(p.stdout)["account"]), ("b/two", "b"))


if __name__ == "__main__":
    unittest.main()
