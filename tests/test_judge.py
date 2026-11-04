"""wave judge: the producer never judges its own PR.

The verdict is a typed artifact (`judge-<PR>.json` in the repo's handoffs dir) the kernel re-validates:
the verdict enum, a full head sha, findings that cite `file:line`. `wave judge` writes the scoped patch
and validates; `merge` refuses a PR without a pass verdict at the head it is merging: a verdict that is
absent (`JUDGE-MISSING`), negative (`JUDGE-FAIL`) or made for a head that has since moved (`JUDGE-STALE`
— any push invalidates it). PR 20 (`feat/4-x`, clean, closes #4) is the PR under judgement here.
"""
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TMP, WAVE, gh_calls, git, judge_pass, run_wave, sandbox, wave, writes_allowed

HANDOFFS = TMP / "r-handoffs"
CLONE_PATH = TMP / "r"
VERDICT = HANDOFFS / "judge-20.json"


def setUpModule():
    sandbox()


def head_sha(branch: str = "feat/4-x") -> str:
    return wave.Repo.get("o/r").git(["rev-parse", f"origin/{branch}"]).strip()


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


class Scope(unittest.TestCase):
    """#119: a `concerns` whose findings all carry `"scope": true` is the owner's decision, not a fix
    round — the implementing agent cannot un-ask a scope question. `judge_check` classifies it and
    `wave judge` prints the owner question (accept as is / split a follow-up)."""

    def tearDown(self):
        VERDICT.unlink(missing_ok=True)

    def seed(self, findings, verdict="concerns"):
        HANDOFFS.mkdir(parents=True, exist_ok=True)
        VERDICT.write_text(json.dumps({"verdict": verdict, "head_sha": head_sha(), "reality": "clean", "findings": findings}))

    def scope_concern(self, **extra):
        return {"file": "g.txt", "line": 1, "severity": "nit", "note": "the hero was never asked", "scope": True, **extra}

    def test_all_scope_concerns_print_the_owner_question(self):
        self.seed([self.scope_concern()])
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr + " — concerns never refuse")
        self.assertIn("scope-only concerns", p.stdout)
        self.assertIn("accept", p.stdout)
        self.assertIn("follow-up", p.stdout)

    def test_mixed_findings_are_not_scope_only(self):
        self.seed([self.scope_concern(),
                   {"file": "g.txt", "line": 2, "severity": "nit", "note": "a plain nit"}])
        p = run_wave("judge", "20")
        self.assertNotIn("scope-only concerns", p.stdout)

    def test_scope_does_not_soften_a_fail_or_a_blocker(self):
        self.seed([self.scope_concern(severity="blocking")], verdict="fail")
        p = run_wave("judge", "20")
        self.assertIn("JUDGE-FAIL", p.stdout)

    def test_a_non_boolean_scope_is_refused(self):
        self.seed([self.scope_concern(scope="yes")])
        p = run_wave("judge", "20")
        self.assertIn("JUDGE-MISSING", p.stdout)
        self.assertIn("scope", p.stdout)

    def test_the_judge_template_defines_the_flag(self):
        text = (WAVE.parent.parent / "templates" / "judge.md").read_text()
        self.assertIn('"scope": true', text)
        self.assertIn("follow-up", text)


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
        self.assertEqual(wave.verdict_problems({"verdict": "pass", "head_sha": "0" * 40, "reality": "clean", "findings": []}), [])
        self.assertEqual(wave.verdict_problems(
            {"verdict": "concerns", "head_sha": "0" * 40, "reality": "MAJOR",
             "findings": [{"file": "a.py", "line": 3, "severity": "nit", "note": "x"}]}), [])

    def test_problems_of_a_recheck(self):
        good = {"verdict": "pass", "head_sha": "0" * 40, "reality": "clean", "findings": []}
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


class PatchStaleness(unittest.TestCase):
    """#108: a verdict certifies a diff, not just a head sha — round 2 on PR #107 re-read a stale
    `judge-107.patch` and certified a head whose diff it never saw. The kernel anchors the patch:
    `wave judge` records the patch content's sha256 at cut time in a sidecar (`judge-<PR>.patch.sha`,
    head + patch_sha), the verdict may carry the same `patch_sha`, and `judge_check` refuses
    (`JUDGE-PATCH-STALE`) when the patch on disk is not the diff the verdict certified. Re-cutting
    the patch invalidates the previous verdict explicitly."""

    def tearDown(self):
        for f in ("judge-20.json", "judge-20.patch", "judge-20.patch.sha",
                  "judge-20.delta.patch", "judge-20.delta.patch.sha"):
            (HANDOFFS / f).unlink(missing_ok=True)

    def digest(self, name: str = "judge-20.patch") -> str:
        return hashlib.sha256((HANDOFFS / name).read_bytes()).hexdigest()

    def anchor(self) -> dict:
        return json.loads((HANDOFFS / "judge-20.patch.sha").read_text())

    def verdict_with_patch_sha(self, patch_sha):
        HANDOFFS.mkdir(parents=True, exist_ok=True)
        VERDICT.write_text(json.dumps(
            {"verdict": "pass", "head_sha": head_sha(), "reality": "clean", "findings": [], "patch_sha": patch_sha}))

    def test_the_cut_records_the_patch_sha_of_the_round_in_the_sidecar(self):
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)  # nothing has been judged yet
        a = self.anchor()
        self.assertEqual(a["head"], head_sha(), "the sidecar names the head the patch was cut at")
        self.assertEqual(a["patch_sha"], self.digest(), "the sidecar names the sha256 of the patch content")
        self.assertIn(self.digest(), p.stdout, "the printed sha256 is what the judge copies into the verdict")

    def test_a_verdict_carrying_its_own_patch_sha_is_fresh(self):
        run_wave("judge", "20")
        self.verdict_with_patch_sha(self.digest())
        p = run_wave("judge", "20")  # a same-head re-cut writes the same bytes: the sha still matches
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_verdict_whose_patch_sha_is_not_the_disk_patch_is_refused(self):
        run_wave("judge", "20")
        self.verdict_with_patch_sha("0" * 64)
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("JUDGE-PATCH-STALE", p.stdout, "the verdict names a diff it did not judge")

    def test_a_patch_sha_in_the_verdict_must_be_a_sha256(self):
        for bad in ("abc", "0" * 63, "Z" * 64):
            with self.subTest(bad=bad):
                self.verdict_with_patch_sha(bad)
                p = run_wave("judge", "20")
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn("JUDGE-MISSING", p.stdout, f"{bad!r}: not a digest the kernel knows")
                self.assertIn("patch_sha", p.stdout)

    def merge(self):
        before = len(gh_calls())
        p = run_wave("merge", "20", "--yes")
        merges = [c for c in gh_calls()[before:] if c[:2] == ["pr", "merge"]]
        return p, merges

    def test_a_patch_tampered_after_the_verdict_is_refused_at_merge(self):
        run_wave("judge", "20")
        judge_pass(20)
        (HANDOFFS / "judge-20.patch").write_text("# tampered after the verdict\n")
        p, merges = self.merge()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("JUDGE-PATCH-STALE", p.stdout + p.stderr)
        self.assertEqual(merges, [], "nothing merged")

    def test_a_verdict_for_a_head_whose_patch_was_cut_earlier_is_refused(self):
        # the #107 bug: the patch is cut at head H, a push moves the head, the judge reads the
        # stale patch and certifies the new head — head_sha matches, so only the anchor can refuse
        run_wave("judge", "20")
        push_new_commit("g2.txt")
        judge_pass(20, sha=head_sha())
        p, merges = self.merge()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("JUDGE-PATCH-STALE", p.stdout + p.stderr,
                      "the verdict certifies a head whose diff was never cut into this patch")
        self.assertEqual(merges, [], "nothing merged")

    def test_recutting_the_patch_invalidates_the_previous_verdict_explicitly(self):
        judge_pass(20)
        run_wave("judge", "20")  # a fresh pass at the current head
        push_new_commit("g2.txt")
        p = run_wave("judge", "20")  # the re-cut: the old verdict certified the old cut
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(f"previous verdict invalidated: patch re-cut at {head_sha()}", p.stdout)
        self.assertIn("JUDGE-STALE", p.stdout, "the re-cut also moves the head: the old verdict is stale")

    def test_a_same_head_recut_leaves_the_verdict_standing(self):
        judge_pass(20)
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("previous verdict invalidated", p.stdout,
                         "the same head cuts the same bytes: nothing was invalidated")
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("previous verdict invalidated", p.stdout)

    def test_the_schemas_anchor_is_optional_but_checked(self):
        good = {"verdict": "pass", "head_sha": "0" * 40, "reality": "clean", "findings": []}
        self.assertEqual(wave.verdict_problems({**good, "patch_sha": "a" * 64}), [])
        self.assertTrue(wave.verdict_problems({**good, "patch_sha": "abc"}))
        self.assertTrue(wave.verdict_problems({**good, "patch_sha": "a" * 64 + "\n"}))
        self.assertEqual(wave.verdict_problems(good), [], "a verdict without the anchor is still schema-valid")


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
        push_new_commit("g2.txt")
        judge_pass(20)  # fresh at this head, but no recheck: the round never answered the previous findings
        p = run_wave("judge", "20", "--since")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("recheck", p.stdout)

    def test_a_nonsense_recheck_is_refused(self):
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
