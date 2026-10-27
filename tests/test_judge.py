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

from helpers import TMP, WAVE, gh_calls, git, judge_pass, run_wave, sandbox, wave

HANDOFFS = TMP / "r-handoffs"
CLONE_PATH = TMP / "r"
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

    def test_a_concerns_verdict_carrying_nits_only_merges(self):
        judge_pass(20, "concerns", severity="nit")
        p, calls = writes_allowed("merge", "20", "--yes", "--ci-timeout", "5", log="judge-merge-nits-20")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue([c for c in calls if c[:2] == ["pr", "merge"]], "nits never block a merge")

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
            {"verdict": "concerns", "head_sha": "0" * 40,
             "findings": [{"file": "a.py", "line": 3, "severity": "nit", "note": "x"}]}), [])

    def test_problems_of_a_recheck(self):
        good = {"verdict": "pass", "head_sha": "0" * 40, "findings": []}
        for rc in ([], [{"file": "a.py", "line": 1, "status": "fixed"}], [{"file": "a.py", "line": 2, "status": "not fixed"}]):
            self.assertEqual(wave.verdict_problems({**good, "recheck": rc}), [])
        for rc in ("yes", [{"file": "a.py", "line": 1}], [{"file": "a.py", "line": 0, "status": "fixed"}],
                   [{"file": "a.py", "line": 1, "status": "maybe"}]):
            self.assertTrue(wave.verdict_problems({**good, "recheck": rc}), rc)
        self.assertTrue(wave.round_problems(dict(good)), "a round verdict without previous/recheck is refused")
        covered = {"verdict": "pass", "head_sha": "0" * 40, "findings": [],
                   "previous": [{"file": "a.py", "line": 3, "severity": "nit", "note": "x"}],
                   "recheck": [{"file": "a.py", "line": 3, "status": "fixed"}]}
        self.assertEqual(wave.round_problems(covered), [])
        self.assertTrue(wave.round_problems({**covered, "recheck": []}), "an empty recheck skips a named finding")

    def test_problems_of_a_bad_verdict(self):
        for body in ({}, "pass", {"verdict": "pass"}, {"verdict": "pass", "head_sha": "0" * 40, "findings": "all"},
                     {"verdict": "pass", "head_sha": "0" * 40, "findings": [{"file": "a.py", "line": 0, "note": "x"}]},
                     {"verdict": "fail", "head_sha": "0" * 40, "findings": [{"file": "a.py", "line": 1}]},
                     {"verdict": "pass", "head_sha": "0" * 40, "findings": [{"file": "a.py", "line": 1, "note": "x"}]},
                     {"verdict": "pass", "head_sha": "0" * 40,
                      "findings": [{"file": "a.py", "line": 1, "severity": "meh", "note": "x"}]}):
            with self.subTest(body=body):
                self.assertTrue(wave.verdict_problems(body))


def seed_round(pr: int, previous: list[dict], statuses: list[str]) -> Path:
    """Seed a round verdict at the current head: it names the findings it was asked to re-check and answers each."""
    f = judge_pass(pr)
    body = json.loads(f.read_text())
    body["previous"] = previous
    body["recheck"] = [{"file": pf["file"], "line": pf["line"], "status": s} for pf, s in zip(previous, statuses)]
    f.write_text(json.dumps(body))
    return f


ROUND_COMMITS = 0


def push_new_commit(path: str) -> str:
    """One more commit on feat/4-x: the head moves, the previous verdict goes stale, the delta is `path`.

    The content differs every call: a second identical commit would be empty and refused.
    """
    global ROUND_COMMITS
    ROUND_COMMITS += 1
    text = f"round {ROUND_COMMITS}\n"
    sandbox()
    git("checkout", "-q", "feat/4-x")
    (CLONE_PATH / path).write_text(text)
    git("add", path)
    git("commit", "-q", "-m", f"feat/4-x: {path} round {ROUND_COMMITS}")
    git("push", "-q", "origin", "feat/4-x")
    git("checkout", "-q", "main")
    return text


class Severity(unittest.TestCase):
    """Only a `fail` verdict or a `blocking` finding refuses a merge; concerns carrying nits alone do not."""

    def tearDown(self):
        VERDICT.unlink(missing_ok=True)

    def test_fail_still_blocks(self):
        judge_pass(20, "fail")
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("JUDGE-FAIL", p.stdout)

    def test_a_blocking_finding_inside_concerns_blocks(self):
        judge_pass(20, "concerns", severity="blocking")
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("JUDGE-FAIL", p.stdout)

    def test_concerns_with_only_nits_does_not_block(self):
        judge_pass(20, "concerns", severity="nit")
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("nit", p.stdout, "the owner still sees the nits")

    def test_the_severity_must_be_blocking_or_nit(self):
        for severity in ("meh", "", None):
            with self.subTest(severity=severity):
                f = judge_pass(20, "concerns", severity=severity)
                body = json.loads(f.read_text())
                if severity is None:
                    del body["findings"][0]["severity"]
                else:
                    body["findings"][0]["severity"] = severity
                f.write_text(json.dumps(body))
                p = run_wave("judge", "20")
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn("JUDGE-MISSING", p.stdout, f"{severity!r}: not a severity the kernel knows")


class Round(unittest.TestCase):
    """`wave judge 20 --since`: the previous verdict's findings come back, the patch is only the delta."""

    def setUp(self):
        self.prev_sha = head_sha()
        self.prev_verdict = HANDOFFS / "judge-20.json"

    def tearDown(self):
        VERDICT.unlink(missing_ok=True)

    def test_the_delta_patch_holds_only_the_delta_and_the_prompt_cites_every_previous_finding(self):
        # concerns carrying a nit only, and no recheck: nothing of the round contract can refuse yet, so the
        # refusal is the sha (JUDGE-STALE) and the finding text on stdout can only come from the prompt
        judge_pass(20, "concerns", severity="nit")  # the previous round's verdict, at the head it judged
        push_new_commit("g2.txt")
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)  # the verdict is stale: nothing judged this round yet
        delta = HANDOFFS / "judge-20.delta.patch"
        self.assertTrue(delta.exists(), p.stdout)
        repo = wave.Repo.get("o/r")
        self.assertEqual(delta.read_text(), repo.git(["diff", self.prev_sha, head_sha()]),
                         "the patch is the delta since the previous verdict's head, nothing else")
        self.assertNotIn("g.txt", delta.read_text(), "the first round's file is not in the delta")
        self.assertIn("the seeded fault", p.stdout, "the prompt cites each previous finding")
        self.assertIn("g.txt:1", p.stdout, "the prompt cites each previous finding by file and line")
        self.assertIn(self.prev_sha, p.stdout)

    def test_a_previous_head_that_is_not_an_ancestor_is_named_not_silent(self):
        # a rebase rewrites the head: the "delta" against the old verdict is the whole diff, and the output says so
        other = wave.Repo.get("o/r").git(["rev-parse", "origin/t/c"]).strip()
        judge_pass(20, sha=other)
        push_new_commit("g2.txt")
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("not an ancestor", p.stdout, "a silent whole-diff delta would lie about convergence")

    def test_since_without_a_previous_verdict_is_refused(self):
        push_new_commit("g2.txt")
        self.assertFalse(self.prev_verdict.exists())
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("--since", p.stdout)
        self.assertIn("whole-diff", p.stdout, "the refusal names the recovery")

    def test_a_round_verdict_at_the_current_head_is_fresh(self):
        judge_pass(20)
        push_new_commit("g2.txt")  # the seeded verdict goes stale
        seed_round(20, [{"file": "g.txt", "line": 1, "severity": "nit", "note": "the seeded fault"}], ["fixed"])
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_round_verdict_answers_every_previous_finding(self):
        judge_pass(20, "concerns", severity="nit")
        push_new_commit("g2.txt")
        seed_round(20, [{"file": "g.txt", "line": 1, "severity": "nit", "note": "the seeded fault"}], ["not fixed"])
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_recheck_that_skips_a_previous_finding_is_refused_by_name(self):
        judge_pass(20, "concerns", severity="nit")
        push_new_commit("g2.txt")
        seed_round(20, [{"file": "g.txt", "line": 1, "severity": "nit", "note": "the seeded fault"},
                        {"file": "g2.txt", "line": 1, "severity": "blocking", "note": "delta fault"}], ["fixed", "fixed"])
        f = HANDOFFS / "judge-20.json"
        body = json.loads(f.read_text())
        body["recheck"] = body["recheck"][:1]  # one answer short
        f.write_text(json.dumps(body))
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("recheck does not answer previous finding g2.txt:1", p.stdout)

    def test_a_non_object_previous_entry_is_a_typed_refusal_not_a_crash(self):
        judge_pass(20)
        push_new_commit("g2.txt")
        f = judge_pass(20)
        body = json.loads(f.read_text())
        body["previous"] = ["the delta broke nothing"]
        body["recheck"] = [{"file": "g.txt", "line": 1, "status": "fixed"}]
        f.write_text(json.dumps(body))
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr, "a malformed verdict is refused, never a crash")
        self.assertIn("previous[0] must be a finding", p.stdout)

    def test_a_round_verdict_without_previous_is_refused(self):
        judge_pass(20)
        push_new_commit("g2.txt")
        f = judge_pass(20)
        body = json.loads(f.read_text())
        body["recheck"] = [{"file": "g.txt", "line": 1, "status": "fixed"}]  # answers nobody: no `previous` named
        f.write_text(json.dumps(body))
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("previous", p.stdout)

    def test_staleness_still_wins_over_the_round_contract(self):
        judge_pass(20)  # a stale whole-diff verdict, no recheck, no previous
        push_new_commit("g2.txt")
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("JUDGE-STALE[", p.stdout)

    def test_a_round_verdict_without_the_recheck_evidence_is_refused(self):
        judge_pass(20)
        push_new_commit("g2.txt")
        judge_pass(20)  # fresh at this head, but no recheck: the round never answered the previous findings
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("recheck", p.stdout)

    def test_a_nonsense_recheck_is_refused(self):
        judge_pass(20)
        push_new_commit("g2.txt")
        f = judge_pass(20)
        body = json.loads(f.read_text())
        body["recheck"] = [{"file": "g.txt", "line": 1, "status": "maybe"}]
        f.write_text(json.dumps(body))
        p = run_wave("judge", "20", "--since")
        # prev and verdict share one file: the refusal may come from the previous-verdict check or the round
        # verdict check, but both name the same broken entry and both refuse
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("recheck[0].status must be fixed|not fixed", p.stdout)

    def test_a_round_verdict_for_an_older_head_is_stale(self):
        judge_pass(20)  # a pass at the previous head: only the sha can refuse it now
        push_new_commit("g2.txt")
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("JUDGE-STALE[", p.stdout,
                      "the flag branch, not the instruction text that names all three flags: staleness wins over a missing recheck")
        self.assertIn(self.prev_sha[:7], p.stdout, "the refusal names both shas")
        self.assertIn(head_sha()[:7], p.stdout)


if __name__ == "__main__":
    unittest.main()
