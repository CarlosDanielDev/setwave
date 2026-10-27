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

from helpers import TMP, WAVE, gh_calls, git, run_wave, sandbox, wave

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


def writes(*args, log: str):
    """`wave.py <args>` with the fake gh accepting merges and issue writes, logged aside so the
    no-writes audit of the rest of the suite never sees a test merge."""
    env = {**os.environ, "FAKE_GH_EDIT": "1", "FAKE_GH_COMMENT": "1", "FAKE_GH_MERGE": "1",
           "FAKE_GH_LOG": str(TMP / f"gh-{log}.log")}
    return subprocess.run([sys.executable, str(WAVE), *args], cwd=sandbox(), env=env, capture_output=True, text=True)


def calls(log: str) -> list[list[str]]:
    f = TMP / f"gh-{log}.log"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def judge_pass_n(pr: int) -> None:
    """Seed a pass verdict at the head of o/n's PR, the way the read-only judge agent would."""
    repo = wave.Repo.get("o/n")
    branch = next(x["headRefName"] for x in repo.open_prs() if x["number"] == pr)
    repo.handoffs.mkdir(parents=True, exist_ok=True)
    (repo.handoffs / f"judge-{pr}.json").write_text(json.dumps({
        "verdict": "pass", "head_sha": repo.git(["rev-parse", f"origin/{branch}"]).strip(), "findings": []}))


class CiVerdict(unittest.TestCase):
    """The distinction the issue asks for: no checks anywhere is NO-CI, not pending."""

    def test_no_checks_and_no_workflow_is_no_ci(self):
        class R:
            def __init__(self, checks, workflows):
                self.c, self.w = checks, workflows
            def pr_checks(self, n):
                return self.c
            def has_ci_workflows(self):
                return self.w
        self.assertEqual(wave.ci_verdict(R({}, False), 1), "NO-CI")
        self.assertIsNone(wave.ci_verdict(R({}, True), 1), "a workflow exists: the checks may still come (pending)")
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


class VerifyReports(unittest.TestCase):
    def test_verify_reports_no_ci_not_silent_green(self):
        p = run_wave("verify", "o/n#81", "--json")
        r = json.loads(p.stdout)[0]
        self.assertEqual((p.returncode, r["ok"], r["no_ci"]), (1, False, True), r)
        self.assertIn("NO-CI", run_wave("verify", "o/n#81").stdout)

    def test_a_pr_with_checks_is_not_no_ci(self):
        r = json.loads(run_wave("verify", "20", "--json").stdout)[0]
        self.assertFalse(r["no_ci"])


class NoCiEvidence(unittest.TestCase):
    def test_a_green_chain_gate_in_the_plan_is_evidence(self):
        judge_pass_n(81)
        plan = TMP / "n-plan.json"
        o = run_wave("order", "o/n#81", "--run-gate", "--plan", str(plan), cwd=N)
        self.assertEqual(o.returncode, 0, o.stderr)
        p = writes("merge", "--plan", str(plan), "--yes", "--no-ci", log="noci-plan")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--no-ci", p.stdout)
        self.assertTrue([c for c in calls("noci-plan") if c[:2] == ["pr", "merge"]])

    def test_the_gate_output_in_the_body_is_evidence(self):
        judge_pass_n(82)
        p = writes("merge", "o/n#82", "--yes", "--no-ci", log="noci-body")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--no-ci", p.stdout)
        self.assertIn(GATE, p.stdout, "the acknowledgment names the gate it accepted")
        self.assertIn("unverified", p.stdout, "the acknowledgment says the paste is unverified")
        self.assertTrue([c for c in calls("noci-body") if c[:2] == ["pr", "merge"]])

    def test_without_evidence_no_ci_is_refused(self):
        judge_pass_n(81)
        p = writes("merge", "o/n#81", "--yes", "--no-ci", log="noci-none")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("NO-CI", p.stdout)
        self.assertIn("--run-gate", p.stdout, "the refusal names what counts as evidence")
        self.assertEqual([c for c in calls("noci-none") if c[:2] == ["pr", "merge"]], [])


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
        for frag in ("never waits for CI that does not exist", "an empty plan is never success"):
            row = next(r for r in rows if frag in r["guard"])
            self.assertTrue(row["tested"], frag)


if __name__ == "__main__":
    unittest.main()
