"""wave judge: the producer never judges its own PR.

The verdict is a typed artifact (`judge-<PR>.json` in the repo's handoffs dir) the kernel re-validates:
the verdict enum, a full head sha, findings that cite `file:line`. `wave judge` writes the scoped patch
and validates; `merge` refuses a PR without a pass verdict at the head it is merging: a verdict that is
absent (`JUDGE-MISSING`), negative (`JUDGE-FAIL`) or made for a head that has since moved (`JUDGE-STALE`
— any push invalidates it). PR 20 (`feat/4-x`, clean, closes #4) is the PR under judgement here.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TMP, WAVE, gh_calls, judge_pass, run_wave, sandbox, wave

HANDOFFS = TMP / "r-handoffs"
VERDICT = HANDOFFS / "judge-20.json"


def setUpModule():
    sandbox()


def head_sha(branch: str = "feat/4-x") -> str:
    return wave.Repo.get("o/r").git(["rev-parse", f"origin/{branch}"]).strip()


def writes_allowed(*args, log: str):
    """`wave.py <args>` with the fake gh accepting PR merges and issue edits, logged to their own file."""
    env = {**os.environ, "FAKE_GH_EDIT": "1", "FAKE_GH_MERGE": "1", "FAKE_GH_LOG": str(TMP / f"gh-{log}.log")}
    p = subprocess.run([sys.executable, str(WAVE), *args], cwd=sandbox(), capture_output=True, text=True, env=env)
    calls = [json.loads(l) for l in (TMP / f"gh-{log}.log").read_text().splitlines()] if (TMP / f"gh-{log}.log").exists() else []
    return p, calls


class JudgeCmd(unittest.TestCase):
    def tearDown(self):
        VERDICT.unlink(missing_ok=True)

    def test_the_patch_is_written_and_the_verdict_asked_for(self):
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)  # nothing has been judged yet
        patch = (HANDOFFS / "judge-20.patch")
        self.assertTrue(patch.exists(), p.stdout)
        self.assertEqual(patch.read_text(), wave.Repo.get("o/r").git(["diff", "origin/main...origin/feat/4-x"]),
                         "the patch is the whole scoped diff of the PR")
        self.assertIn("g.txt", p.stdout)
        self.assertIn(str(HANDOFFS / "judge-20.json"), p.stdout)
        self.assertIn(head_sha(), p.stdout, "the judge copies this sha into its verdict")

    def test_a_pass_verdict_at_the_current_head_is_fresh(self):
        judge_pass(20)
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pass", p.stdout)

    def test_a_fail_verdict_shows_its_findings(self):
        judge_pass(20, "fail")
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("JUDGE-FAIL", p.stdout)
        self.assertIn("the seeded fault", p.stdout)

    def test_a_verdict_for_an_older_head_is_stale(self):
        judge_pass(20, sha="a" * 40)
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("JUDGE-STALE", p.stdout)

    def test_the_schema_is_validated_before_anything_else(self):
        for body, why in (
            ({"verdict": "ok", "head_sha": head_sha(), "findings": []}, "verdict enum"),
            ({"verdict": "pass", "head_sha": "abc123", "findings": []}, "short sha"),
            ({"verdict": "pass", "head_sha": head_sha(), "findings": [{"file": "g.txt", "note": "no line"}]}, "finding without a line"),
            ({"verdict": "pass", "head_sha": head_sha()}, "findings missing"),
            ({"verdict": "fail", "head_sha": head_sha(), "findings": []}, "a fail with no finding"),
        ):
            with self.subTest(bad=why):
                HANDOFFS.mkdir(parents=True, exist_ok=True)
                VERDICT.write_text(json.dumps(body))
                p = run_wave("judge", "20")
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn("JUDGE-MISSING", p.stdout, f"{why}: no valid verdict, so none is on file")


class MergeRefusals(unittest.TestCase):
    """`merge` re-checks the verdict itself, where it cannot be skipped: the OK was for a judged delta."""

    def tearDown(self):
        VERDICT.unlink(missing_ok=True)

    def merge(self):
        before = len(gh_calls())
        p = run_wave("merge", "20", "--yes")
        merges = [c for c in gh_calls()[before:] if c[:2] == ["pr", "merge"]]
        return p, merges

    def test_no_verdict_is_refused(self):
        p, merges = self.merge()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("JUDGE-MISSING", p.stdout + p.stderr)
        self.assertEqual(merges, [], "nothing merged")

    def test_a_fail_verdict_is_refused(self):
        judge_pass(20, "fail")
        p, merges = self.merge()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("JUDGE-FAIL", p.stdout + p.stderr)
        self.assertEqual(merges, [])

    def test_a_stale_verdict_is_refused(self):
        judge_pass(20, sha="b" * 40)
        p, merges = self.merge()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("JUDGE-STALE", p.stdout + p.stderr)
        self.assertEqual(merges, [])

    def test_a_fresh_pass_verdict_merges(self):
        judge_pass(20)
        p, calls = writes_allowed("merge", "20", "--yes", "--ci-timeout", "5", log="judge-merge-20")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue([c for c in calls if c[:2] == ["pr", "merge"]])


class Plan(unittest.TestCase):
    def tearDown(self):
        VERDICT.unlink(missing_ok=True)

    def test_the_plan_carries_the_verdicts(self):
        judge_pass(20, "fail")
        plan_file = TMP / "plan-judge.json"
        p = run_wave("order", "20", "11", "--plan", str(plan_file))
        self.assertEqual(p.returncode, 0, p.stderr)
        judges = json.loads(plan_file.read_text())["repos"]["o/r"]["judges"]
        self.assertEqual(judges["20"]["flag"], "JUDGE-FAIL")
        self.assertEqual(judges["11"]["flag"], "JUDGE-MISSING")
        self.assertIn("JUDGE-FAIL", p.stdout, "the table names what still has to be judged")


class Schema(unittest.TestCase):
    def test_problems_of_a_good_verdict(self):
        self.assertEqual(wave.verdict_problems({"verdict": "pass", "head_sha": "0" * 40, "findings": []}), [])
        self.assertEqual(wave.verdict_problems(
            {"verdict": "concerns", "head_sha": "0" * 40, "findings": [{"file": "a.py", "line": 3, "note": "x"}]}), [])

    def test_problems_of_a_bad_verdict(self):
        for body in ({}, "pass", {"verdict": "pass"}, {"verdict": "pass", "head_sha": "0" * 40, "findings": "all"},
                     {"verdict": "pass", "head_sha": "0" * 40, "findings": [{"file": "a.py", "line": 0, "note": "x"}]},
                     {"verdict": "fail", "head_sha": "0" * 40, "findings": [{"file": "a.py", "line": 1}]}):
            with self.subTest(body=body):
                self.assertTrue(wave.verdict_problems(body))


if __name__ == "__main__":
    unittest.main()
