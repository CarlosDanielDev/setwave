"""`scripts/e2e.py` and `scripts/fake_agent.py`, offline: the parts that decide before anything reaches GitHub.

The run itself needs a real account and a real sandbox repository, so it is not here (and not in CI): what is
here is that it refuses any repository that is not a sandbox, that `tests/e2e-plan/` is a plan `wave plan`
accepts once per run, that the fake agent makes the change its issue asks for and closes the issue in its
commit, that leaves a and b conflict while c does not, that the fake agent settles that conflict, and that
leaf d exists to be lied about: the fake agent ticks its ledger with nothing merged, and the sweep reverts it.
"""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import TMP, WAVE, gh_calls, git, run_wave, sandbox

SCRIPTS = WAVE.parent
PLAN = Path(__file__).resolve().parent / "e2e-plan"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


e2e = load("e2e")
fake_agent = load("fake_agent")


def repo_with_origin(url: str) -> Path:
    d = Path(tempfile.mkdtemp(dir=TMP, prefix="origin-"))
    git("init", "-q", cwd=d)
    git("remote", "add", "origin", url, cwd=d)
    return d


def out(*args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Target(unittest.TestCase):
    def test_the_plugins_own_repository_is_refused_even_with_the_flag(self):
        for any_repo in (False, True):
            with self.assertRaises(SystemExit) as e:
                e2e.check_target("CarlosDanielDev/setwave", any_repo)
            self.assertIn("never runs against", str(e.exception))

    def test_a_repository_whose_name_does_not_end_in_sandbox_is_refused(self):
        with self.assertRaises(SystemExit) as e:
            e2e.check_target("o/r", False)
        self.assertIn("--any-repo", str(e.exception))

    def test_a_sandbox_passes_and_the_flag_lets_another_name_through(self):
        e2e.check_target("o/r-sandbox", False)
        e2e.check_target("o/r", True)

    def test_the_command_line_refuses_before_any_gh_call(self):
        sandbox()
        for url in ("git@github.com:CarlosDanielDev/setwave.git", "https://github.com/o/r.git"):
            before = len(gh_calls())
            p = subprocess.run([sys.executable, str(SCRIPTS / "e2e.py"), str(repo_with_origin(url))], capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0, p.stdout)
            self.assertIn("e2e refused", p.stderr)
            self.assertEqual(gh_calls()[before:], [])


class Plan(unittest.TestCase):
    def test_each_run_gets_its_own_titles_and_no_numbers_file(self):
        d1, d2 = Path(tempfile.mkdtemp(dir=TMP)), Path(tempfile.mkdtemp(dir=TMP))
        e2e.prepare_plan(PLAN, d1, "20260930000001")
        e2e.prepare_plan(PLAN, d2, "20260930000002")
        titles = lambda d: [l.split("\t")[1] for l in (d / "index.tsv").read_text().splitlines()]
        self.assertEqual(len(titles(d1)), 5)
        self.assertFalse(set(titles(d1)) & set(titles(d2)))
        for f in d1.iterdir():
            self.assertNotIn("{{run}}", f.read_text(), f.name)
        self.assertFalse((d1 / "numbers.json").exists())

    def test_wave_plan_accepts_it(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        e2e.prepare_plan(PLAN, d, "20260930000003")
        p = run_wave("plan", str(d), "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("5 issues, 0 dependencies", p.stdout)

    def test_the_lie_leaf_has_a_done_when_to_lie_about(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        e2e.prepare_plan(PLAN, d, "20260930000005")
        text = (d / "d.md").read_text()
        self.assertIn("## Done when", text)
        self.assertGreaterEqual(len(re.findall(r"(?m)^- \[ \]", text)), 1)


class FakeAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sandbox()
        cls.run_id = "20260930000004"
        d = Path(tempfile.mkdtemp(dir=TMP))
        e2e.prepare_plan(PLAN, d, cls.run_id)
        cls.bodies = {k: (d / f"{k}.md").read_text() for k in "abc"}
        bare = TMP / "remote" / "fa.git"
        cls.clone = TMP / "fa"
        git("init", "-q", "--bare", str(bare), cwd=TMP)
        git("clone", "-q", str(bare), str(cls.clone), cwd=TMP)
        (cls.clone / "app.py").write_text('"""The setwave sandbox."""\n')
        git("add", ".", cwd=cls.clone)
        git("commit", "-q", "-m", "base", cwd=cls.clone)
        git("push", "-q", "origin", "main", cwd=cls.clone)
        for n, k in ((11, "a"), (12, "b"), (13, "c")):
            git("checkout", "-q", "-b", f"e2e/{n}-{k}", "main", cwd=cls.clone)
            fake_agent.apply(cls.clone, fake_agent.change_of(cls.bodies[k]))
            fake_agent.commit(cls.clone, f"leaf {k}", n)
        git("checkout", "-q", "main", cwd=cls.clone)

    def conflicts(self, x: str, y: str) -> bool:
        return subprocess.run(["git", "merge-tree", "--write-tree", x, y], cwd=self.clone, capture_output=True).returncode != 0

    def test_the_change_is_the_one_the_issue_asks_for(self):
        text = out("show", "e2e/11-a:app.py", cwd=self.clone)
        self.assertIn(f"def greet_{self.run_id}_a():", text)
        self.assertEqual(out("show", f"e2e/13-c:runs/{self.run_id}.txt", cwd=self.clone), f"run {self.run_id}: leaf c")

    def test_the_commit_closes_its_issue_and_carries_no_attribution(self):
        msg = out("log", "-1", "--format=%B", "e2e/12-b", cwd=self.clone)
        self.assertIn("Closes #12", msg)
        self.assertNotRegex(msg.lower(), r"co-authored-by|generated with")

    def test_a_and_b_conflict_and_c_conflicts_with_neither(self):
        self.assertTrue(self.conflicts("e2e/11-a", "e2e/12-b"))
        self.assertFalse(self.conflicts("e2e/11-a", "e2e/13-c"))
        self.assertFalse(self.conflicts("e2e/12-b", "e2e/13-c"))

    def test_work_refuses_a_branch_that_is_not_the_issues(self):
        with self.assertRaises(SystemExit) as e:
            fake_agent.check_branch("main", 12)
        self.assertIn("not the branch of #12", str(e.exception))
        fake_agent.check_branch("e2e/12-x-b", 12)

    def test_a_body_without_a_change_is_refused(self):
        with self.assertRaises(SystemExit):
            fake_agent.change_of("Parent: #1\n\nno block here\n")

    def test_resolve_keeps_both_sides_stages_them_and_compiles(self):
        wt = TMP / "fa-12"
        git("worktree", "add", "-q", str(wt), "e2e/12-b", cwd=self.clone)
        subprocess.run(["git", "merge", "-q", "--no-edit", "e2e/11-a"], cwd=wt, capture_output=True)
        self.assertTrue(out("diff", "--name-only", "--diff-filter=U", cwd=wt))
        self.assertEqual(fake_agent.resolve(wt), ["app.py"])
        text = (wt / "app.py").read_text()
        self.assertIn(f"def greet_{self.run_id}_a():", text)
        self.assertIn(f"def greet_{self.run_id}_b():", text)
        self.assertNotRegex(text, r"(?m)^(<{7}|={7}|>{7})")
        self.assertEqual(out("diff", "--name-only", "--diff-filter=U", cwd=wt), "")
        compiled = subprocess.run([sys.executable, "-m", "py_compile", "app.py"], cwd=wt, capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)


class Lie(unittest.TestCase):
    def test_claim_done_ticks_only_the_done_when_section(self):
        body = "Parent: #1\n\nthe work.\n\n## Done when\n\n- [ ] one\n- [x] two\n\n## Handoff\n\n- [ ] three stays open\n"
        ticked = fake_agent.claim_done(body)
        self.assertIn("- [x] one", ticked)
        self.assertIn("- [x] two", ticked)
        self.assertIn("- [ ] three stays open", ticked)

    def test_a_body_without_a_done_when_cannot_be_lied_about(self):
        with self.assertRaises(SystemExit):
            fake_agent.claim_done("Parent: #1\n\nno ledger here\n")

    def test_nothing_here_is_ever_written_to_the_plugins_own_repository(self):
        for slug in ("CarlosDanielDev/setwave", "carlosdanieldev/setwave"):
            with self.assertRaises(SystemExit) as e:
                fake_agent.check_target(slug)
            self.assertIn("own repository", str(e.exception))
        fake_agent.check_target("o/r-sandbox")


class Steps(unittest.TestCase):
    def test_an_unexpected_exit_stops_the_run_and_names_the_step(self):
        log = []
        with self.assertRaises(SystemExit) as e:
            e2e.step(log, "boom", [sys.executable, "-c", "raise SystemExit(3)"], TMP)
        self.assertIn("boom", str(e.exception))
        self.assertEqual((log[0]["step"], log[0]["exit"], log[0]["expected"]), ("boom", 3, 0))

    def test_an_expected_nonzero_exit_is_recorded_and_the_run_goes_on(self):
        log = []
        e2e.step(log, "stops by design", [sys.executable, "-c", "raise SystemExit(2)"], TMP, expect=2)
        self.assertEqual(log[0]["exit"], 2)
        self.assertIn("seconds", log[0])
        json.dumps(log)  # the log is written as JSON


if __name__ == "__main__":
    unittest.main()
