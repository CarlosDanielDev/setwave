"""No CI and empty plans (#97): `merge` never waits for CI that does not exist, and an empty plan is never success.

A sixth repo, o/n: no `.github/workflows/` and a declared gate (`echo gate-o-n`).
  PR 81 t/n1  closes #80, body names no gate — `merge` stops at once with NO-CI, `--no-ci` alone is not evidence
  PR 82 t/n2  closes #83, body pastes the gate's output — `--no-ci` accepts it without a plan
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from helpers import TMP, gh_calls, git, judge_pass, run_wave, sandbox, wave, writes_allowed

N = TMP / "n"
Z = TMP / "z"
GATE = "echo gate-o-n"


def setUpModule():
    sandbox()
    bare = TMP / "remote" / "o" / "n.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(N), cwd=TMP)
    (N / ".wave.json").write_text(json.dumps({"base": "main", "gate": [GATE]}))
    git("add", ".", cwd=N)
    git("commit", "-q", "-m", "base", cwd=N)
    git("push", "-q", "origin", "main", cwd=N)
    for branch, path, text in (("t/n1", "n.txt", "n\n"), ("t/n2", "n2.txt", "n2\n")):
        git("checkout", "-q", "-b", branch, "main", cwd=N)
        (N / path).write_text(text)
        git("add", path, cwd=N)
        git("commit", "-q", "-m", f"{branch}: {path}", cwd=N)
        git("push", "-q", "origin", branch, cwd=N)
        git("checkout", "-q", "main", cwd=N)
    reg_file = TMP / "config" / "repos.json"
    reg = json.loads(reg_file.read_text())
    reg["repos"]["o/n"] = {"path": str(N)}
    reg_file.write_text(json.dumps(reg))
    # o/z: origin/main carries a workflow, the checkout's tree does not — what has_ci_workflows reads (#97)
    bare_z = TMP / "remote" / "o" / "z.git"
    bare_z.parent.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", str(bare_z), cwd=TMP)
    git("clone", "-q", str(bare_z), str(Z), cwd=TMP)
    (Z / ".wave.json").write_text(json.dumps({"base": "main"}))
    (Z / ".github" / "workflows").mkdir(parents=True)
    (Z / ".github" / "workflows" / "ci.yml").write_text("name: ci\non: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n")
    git("add", ".", cwd=Z)
    git("commit", "-q", "-m", "base", cwd=Z)
    git("push", "-q", "origin", "main", cwd=Z)
    (Z / ".github" / "workflows" / "ci.yml").unlink()  # the stale tree: only origin knows the workflow


def judge_pass_n(pr: int) -> None:
    """Seed a pass verdict at the head of o/n's PR, the way the read-only judge agent would."""
    judge_pass(pr, slug="o/n")


class CiVerdict(unittest.TestCase):
    """The distinction the issue asks for: no checks anywhere is NO-CI, not pending."""

    def test_no_checks_and_no_expected_ci_is_no_ci(self):
        class R:
            def __init__(self, checks, ci_expected):
                self.c, self.e = checks, ci_expected
            def pr_checks(self, n):
                return self.c
            def ci_expected(self):
                return self.e
        self.assertEqual(wave.ci_verdict(R({}, False), 1), "NO-CI")
        self.assertIsNone(wave.ci_verdict(R({}, True), 1), "CI is expected: the checks may still come (pending)")
        self.assertEqual(wave.ci_verdict(R({"ci": "pass"}, False), 1), "PASS")
        self.assertEqual(wave.ci_verdict(R({"ci": "fail"}, True), 1), "FAILED")


class MergeStops(unittest.TestCase):
    def test_merge_stops_at_once_with_no_ci_naming_the_flag(self):
        out = io.StringIO()
        slept: list[float] = []
        before = len(gh_calls())
        with mock.patch.object(wave.time, "sleep", lambda s: slept.append(s)):
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    wave.cmd_merge(wave.Repo.get("o/n"), argparse.Namespace(
                        prs=["81"], yes=True, plan=None, force=True, squash=False,
                        ci_timeout=1200, wait_base_ci=False, no_ci=False))
        self.assertEqual(ctx.exception.code, 3, out.getvalue())
        self.assertIn("NO-CI", out.getvalue())
        self.assertIn("--no-ci", out.getvalue())
        self.assertEqual(slept, [], "nothing waited: the timeout must never be reached")
        self.assertFalse([c for c in gh_calls()[before:] if c[:2] == ["pr", "merge"]])

    def test_merge_names_no_ci_at_the_verify_gate_too(self):
        p = run_wave("merge", "o/n#81", "--yes")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("NO-CI", p.stdout)
        self.assertIn("--no-ci", p.stdout)


class MergeNoWait(unittest.TestCase):
    """#119: the CI wait is never armed for a repo that cannot produce checks — even when the checks
    read fails — and the NO-CI stop prints the ready `gh pr merge --squash --match-head-commit <sha>`
    so finishing by hand takes seconds instead of another hung session."""

    def test_a_repo_without_ci_is_no_ci_even_when_the_checks_read_fails(self):
        class R:
            def pr_checks(self, n):
                return None          # gh failed to read the checks
            def ci_expected(self):
                return False         # but no CI exists that could produce one
        self.assertEqual(wave.merge_ci_step(R(), 81), "NO-CI",
                         "plain ci_verdict returns None here and the merge waits the whole ci-timeout")

    def test_a_repo_with_expected_ci_still_goes_through_ci_verdict(self):
        class R:
            def pr_checks(self, n):
                return {"ci": "pass"}
            def ci_expected(self):
                return True
        self.assertEqual(wave.merge_ci_step(R(), 81), "PASS")

    def test_the_no_ci_stop_prints_the_match_head_commit_command(self):
        out = io.StringIO()
        slept: list[float] = []
        with mock.patch.object(wave.time, "sleep", lambda s: slept.append(s)):
            with contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    wave.cmd_merge(wave.Repo.get("o/n"), argparse.Namespace(
                        prs=["81"], yes=True, plan=None, force=True, squash=True,
                        ci_timeout=1200, wait_base_ci=False, no_ci=False))
        sha = wave.Repo.get("o/n").git(["rev-parse", "origin/t/n1"]).strip()
        self.assertEqual(ctx.exception.code, 3, out.getvalue())
        self.assertIn(f"gh pr merge 81 -R o/n --squash --match-head-commit {sha}", out.getvalue())
        self.assertIn("--no-ci", out.getvalue(), "the acknowledgment is still named")
        self.assertEqual(slept, [], "nothing waited: the stop is in seconds")


class VerifyReports(unittest.TestCase):
    def test_verify_reports_no_ci_not_silent_green(self):
        p = run_wave("verify", "o/n#81", "--json")
        r = json.loads(p.stdout)[0]
        self.assertEqual((p.returncode, r["ok"], r["no_ci"]), (1, False, True), r)
        out = run_wave("verify", "o/n#81").stdout
        self.assertIn("NO-CI", out)
        self.assertIn("unverified", out, "the flag carries the fenced/unverified wording, not 'the gate output'")
        self.assertIn("chain_gate", out, "the flag names the machine-verifiable alternative")

    def test_a_pr_with_checks_is_not_no_ci(self):
        r = json.loads(run_wave("verify", "20", "--json").stdout)[0]
        self.assertFalse(r["no_ci"])


class NoCiEvidence(unittest.TestCase):
    def test_a_green_chain_gate_in_the_plan_is_evidence(self):
        judge_pass_n(81)
        plan = TMP / "n-plan.json"
        o = run_wave("order", "o/n#81", "--run-gate", "--plan", str(plan), cwd=N)
        self.assertEqual(o.returncode, 0, o.stderr)
        p, mcalls = writes_allowed("merge", "--plan", str(plan), "--yes", "--no-ci", log="noci-plan", comment=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--no-ci", p.stdout)
        self.assertTrue([c for c in mcalls if c[:2] == ["pr", "merge"]])

    def test_the_gate_output_in_the_body_is_evidence(self):
        judge_pass_n(82)
        p, mcalls = writes_allowed("merge", "o/n#82", "--yes", "--no-ci", log="noci-body", comment=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--no-ci", p.stdout)
        self.assertIn(GATE, p.stdout, "the acknowledgment names the gate it accepted")
        self.assertIn("unverified", p.stdout, "the acknowledgment says the paste is unverified")
        self.assertTrue([c for c in mcalls if c[:2] == ["pr", "merge"]])

    def test_without_evidence_no_ci_is_refused(self):
        judge_pass_n(81)
        p, mcalls = writes_allowed("merge", "o/n#81", "--yes", "--no-ci", log="noci-none", comment=True)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("NO-CI", p.stdout)
        self.assertIn("--run-gate", p.stdout, "the refusal names what counts as evidence")
        self.assertEqual([c for c in mcalls if c[:2] == ["pr", "merge"]], [])


class EmptyPlans(unittest.TestCase):
    def test_order_refuses_to_write_a_plan_for_a_leaf(self):
        plan = TMP / "n-leaf-plan.json"
        p = run_wave("order", "--epic", "2", "--plan", str(plan))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("is a leaf: use `order <PR>`", p.stderr)
        self.assertFalse(plan.exists(), "nothing written")

    def test_order_refuses_a_plan_with_no_pr(self):
        plan = TMP / "n-empty-plan.json"
        p = run_wave("order", "99", "--plan", str(plan))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("nothing to order", p.stderr)
        self.assertFalse(plan.exists())

    def test_merge_refuses_an_empty_plan_and_no_target(self):
        plan = TMP / "n-no-repos.json"
        plan.write_text(json.dumps({"made_at": "now", "repos": {}}))
        p = run_wave("merge", "--plan", str(plan), "--yes")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("nothing to merge", p.stderr)
        self.assertEqual([c for c in gh_calls() if c[:2] == ["pr", "merge"]], [])
        q = run_wave("merge", "--yes")
        self.assertEqual(q.returncode, 1, q.stdout + q.stderr)
        self.assertIn("nothing to merge", q.stderr)

    def test_bare_order_refuses_a_leaf_too(self):
        p = run_wave("order", "--epic", "2")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("is a leaf: use `order <PR>`", p.stderr)

    def test_bare_order_refuses_an_empty_selection_too(self):
        p = run_wave("order", "99")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("nothing to order", p.stderr)

    def test_the_no_ci_help_carries_the_current_policy(self):
        p = run_wave("merge", "--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("--no-ci", p.stdout)
        self.assertIn("Actions", p.stdout, "the help is Actions-aware, not files-only")
        self.assertIn("unverified", p.stdout, "the help says the body paste is an unverified paste")


class GatePaste(unittest.TestCase):
    """The body-paste evidence is anchored: a fenced run, not any mention (#97 review)."""

    def r(self):
        class R:
            def gate(self_inner):
                return ["echo gate-o-n", "cargo test"]
        return R()

    def test_a_pasted_run_in_a_fenced_block_is_evidence(self):
        body = "Gate:\n\n```\n$ echo gate-o-n\nok\n```\n"
        self.assertEqual(wave.gate_output_in_body(self.r(), body), "echo gate-o-n")
        self.assertEqual(wave.gate_output_in_body(self.r(), "```\necho gate-o-n\nok\n```"), "echo gate-o-n")

    def test_a_bare_mention_outside_a_fence_is_not_evidence(self):
        self.assertIsNone(wave.gate_output_in_body(self.r(), "we should run echo gate-o-n soon"))
        self.assertIsNone(wave.gate_output_in_body(self.r(), "```\ncargo test\n```\nthen echo gate-o-n"))


class WorkflowSource(unittest.TestCase):
    def test_workflow_existence_reads_origin_not_the_checkout_tree(self):
        repo = wave.Repo("o/z", {"path": str(Z)})
        self.assertTrue(repo.has_ci_workflows(), "origin/main has the workflow; a stale tree must not flip NO-CI")

    def test_actions_disabled_is_no_ci_even_with_workflow_files(self):
        # the incident behind #97: Actions off (billing), files still on disk — the merge spun the whole clock
        repo = wave.Repo("o/z", {"path": str(Z)})
        self.assertTrue(repo.has_ci_workflows(), "premise: origin/main carries the workflow files")
        self.assertIs(repo.actions_enabled(), False, "premise: the fixture says Actions is disabled")
        with mock.patch.object(repo, "pr_checks", lambda n: {}):
            self.assertEqual(wave.ci_verdict(repo, 1), "NO-CI", "no check can ever run: files or not, this is NO-CI")

    def test_an_actions_read_failure_keeps_pending_when_files_exist(self):
        repo = wave.Repo("o/z", {"path": str(Z)})  # origin has the files; the fixture says Actions is disabled
        with mock.patch.object(repo, "actions_enabled", lambda: None):
            self.assertTrue(repo.ci_expected(), "Actions unreadable + files on origin: keep waiting")
            self.assertIsNone(wave.ci_verdict(repo, 1), "the verdict retries as pending, never NO-CI")
        with mock.patch.object(repo, "actions_enabled", lambda: None), \
             mock.patch.object(repo, "has_ci_workflows", lambda: False):
            self.assertFalse(repo.ci_expected(), "Actions unreadable but truly no workflow: the files arm decides, NO-CI stands")


class ChecksError(unittest.TestCase):
    def test_pr_checks_returns_none_when_gh_cannot_read_them(self):
        repo = wave.Repo.get("o/n")
        with mock.patch.object(repo, "gh_ok", lambda args: (False, "gh: exploded")):
            self.assertIsNone(repo.pr_checks(81), "a tool failure is never an empty checks dict")

    def test_a_gh_failure_is_pending_never_no_ci(self):
        class R:
            def pr_checks(self_inner, n):
                return None  # the tri-state's error arm: gh failed both reads
            def has_ci_workflows(self_inner):
                return False
        self.assertIsNone(wave.ci_verdict(R(), 1), "a tool failure is retried as pending, never NO-CI")

    def test_verify_reports_ci_unknown_instead_of_silent_green_or_no_ci(self):
        repo = wave.Repo.get("o/n")
        real = wave.Repo.gh_ok
        def failing(self, args):
            if args[:2] == ["pr", "checks"]:
                return False, "gh: exploded"
            return real(self, args)
        out = io.StringIO()
        with mock.patch.object(wave.Repo, "gh_ok", failing), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                wave.cmd_verify(repo, argparse.Namespace(prs=["o/n#81"], epic=None, json=False))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("CI-UNKNOWN", out.getvalue())
        v = json.loads(run_wave("verify", "o/n#81", "--json").stdout)  # sanity, unpatched: the fixture PR is clean
        self.assertFalse(v[0]["checks_error"])


class GuaranteesPinned(unittest.TestCase):
    def test_the_two_new_guarantees_are_listed_and_pinned(self):
        p = run_wave("guarantees", "--json")
        rows = json.loads(p.stdout)
        for frag in ("never waits for CI that does not exist", "an empty selection is never success"):
            row = next(r for r in rows if frag in r["guard"])
            self.assertTrue(row["tested"], frag)


if __name__ == "__main__":
    unittest.main()
