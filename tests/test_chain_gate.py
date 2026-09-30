"""`order --run-gate`: the gate run on every step of the chain simulation, so a semantic conflict is named before a merge.

A fourth repo, o/g, gates with tests/fixtures/semantic_gate.py. PR 25 (t/sem-a) pins the row count,
PR 26 (t/sem-b) adds a notice row: they merge without a textual conflict, each is green alone, and
their union is red. PR 13 (t/c) is an unrelated green step in front of them.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import TESTS, TMP, git, run_wave, sandbox, wave

G = TMP / "g"
CWD_LOG = TMP / "gate-cwd.log"
GATE = f"{sys.executable} {TESTS / 'fixtures' / 'semantic_gate.py'}"


def setUpModule():
    sandbox()
    os.environ["GATE_CWD_LOG"] = str(CWD_LOG)
    # the throwaway worktrees go under TMPDIR: keep them inside the sandbox too, for `wave` runs and in-process calls
    (TMP / "tmp").mkdir(exist_ok=True)
    os.environ["TMPDIR"] = tempfile.tempdir = str(TMP / "tmp")
    if G.exists():
        return
    bare = TMP / "remote" / "o" / "g.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(G), cwd=TMP)
    (G / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true", GATE]}))
    git("add", ".", cwd=G)
    git("commit", "-q", "-m", "base", cwd=G)
    git("push", "-q", "origin", "main", cwd=G)
    for branch, path, text in (("t/c", "c.txt", "c\n"), ("t/sem-a", "sem/rows.txt", "25\n"), ("t/sem-b", "sem/notice.txt", "notice\n")):
        git("checkout", "-q", "-b", branch, "main", cwd=G)
        (G / path).parent.mkdir(exist_ok=True)
        (G / path).write_text(text)
        git("add", path, cwd=G)
        git("commit", "-q", "-m", f"{branch}: {path}", cwd=G)
        git("push", "-q", "origin", branch, cwd=G)
        git("checkout", "-q", "main", cwd=G)


def out(*args, cwd=G) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def snapshot() -> dict:
    """Everything the chain gate must leave as it found it: the checkout, its refs, its worktrees, the remote."""
    return {"status": out("status", "--porcelain", "--untracked-files=all"), "head": out("rev-parse", "HEAD"),
            "refs": out("for-each-ref"), "worktrees": out("worktree", "list", "--porcelain"),
            "remote": out("ls-remote", str(TMP / "remote" / "o" / "g.git"))}


def cwds() -> list[str]:
    return CWD_LOG.read_text().splitlines() if CWD_LOG.exists() else []


def gate_records() -> list[dict]:
    log = TMP / "config" / "log.jsonl"
    rows = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return [r for r in rows if r.get("cmd") == "gate" and r.get("repo") == "o/g"]


class RunGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (TMP / "config" / "log.jsonl").open("a") as f:  # a measured gate run: the estimate reads the newest one
            f.write(json.dumps({"at": "2026-09-30T00:00:00Z", "cmd": "gate", "repo": "o/g", "seconds": 90, "exit": 0}) + "\n")
        cls.before, cls.cwds_before, cls.records_before = snapshot(), len(cwds()), len(gate_records())
        cls.plan = TMP / "chain-plan.json"
        cls.p = run_wave("order", "13", "25", "26", "--run-gate", "--plan", str(cls.plan), cwd=G)
        cls.after = snapshot()

    def test_the_step_whose_merge_turns_the_tree_red_is_named(self):
        self.assertEqual(self.p.returncode, 0, self.p.stderr)
        cg = json.loads(self.plan.read_text())["repos"]["o/g"]["chain_gate"]
        self.assertEqual([(s["pr"], s["ok"]) for s in cg["steps"]], [(13, True), (25, True), (26, False)])
        self.assertEqual((cg["first_failure"], cg["base_red"]), (26, False))
        self.assertEqual(cg["steps"][2]["command"], GATE, "the failing command, not the whole gate")
        self.assertIn("expected 25 body rows, got 24: the union is red", cg["steps"][2]["tail"])
        self.assertIn("gate:  #13✓ #25✓ #26✗", self.p.stdout)
        self.assertIn(f"first failing step: #26 — `{GATE}`", self.p.stdout)

    def test_the_cost_is_announced_from_the_last_measured_gate_time(self):
        self.assertIn("o/g: 3 gate run(s) ahead, ~4.5 min at the last measured gate time (1.5 min per run)", self.p.stdout)
        self.assertLess(self.p.stdout.index("gate run(s) ahead"), self.p.stdout.index("gate:  #13"))
        new = gate_records()[self.records_before:]
        self.assertEqual(len(new), 3, "every gate run is logged, so the next estimate is measured")
        self.assertTrue(all(isinstance(r["seconds"], (int, float)) for r in new))

    def test_it_runs_in_a_throwaway_worktree_that_is_gone_afterwards(self):
        ran_in = cwds()[self.cwds_before:]
        self.assertEqual(len(ran_in), 3)
        for d in ran_in:
            with self.subTest(d=d):
                self.assertNotEqual(os.path.realpath(d), os.path.realpath(G), "never in the main checkout")
                self.assertTrue(os.path.basename(d).startswith(wave.CHAIN_GATE_PREFIX))
                self.assertTrue(os.path.realpath(d).startswith(os.path.realpath(TMP)), "inside the sandbox, via TMPDIR")
                self.assertFalse(os.path.exists(d), "removed, untracked build output and all")
        self.assertEqual(self.after, self.before, "checkout, refs, worktrees and remote untouched: nothing committed, nothing pushed")

    def test_merge_refuses_a_plan_whose_chain_failed_the_gate(self):
        p = run_wave("merge", "--plan", str(self.plan), cwd=G)
        self.assertEqual(p.returncode, 1)
        self.assertIn("o/g: the plan's chain failed the gate at #26", p.stderr)
        forced = run_wave("merge", "--plan", str(self.plan), "--force", cwd=G)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertIn("dry run", forced.stdout)


class Opt(unittest.TestCase):
    def test_without_the_flag_no_gate_runs(self):
        before = len(cwds())
        plan = TMP / "chain-plan-plain.json"
        p = run_wave("order", "25", "26", "--plan", str(plan), cwd=G)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("chain_gate", json.loads(plan.read_text())["repos"]["o/g"])
        self.assertEqual(len(cwds()), before)

    def test_a_red_base_blames_no_step(self):
        with mock.patch.dict(os.environ, {"GATE_RED": "1"}):
            p = run_wave("order", "25", "26", "--run-gate", "--json", cwd=G)
        self.assertEqual(p.returncode, 0, p.stderr)
        cg = json.loads(p.stdout)["o/g"]["chain_gate"]
        self.assertEqual((cg["first_failure"], cg["base_red"]), (None, True))
        self.assertEqual([(s["pr"], s["ok"]) for s in cg["steps"]], [(25, False), (26, None)])
        self.assertIn("gate run(s) ahead", p.stderr, "with --json the announcement goes to stderr, stdout stays JSON")


class Cleanup(unittest.TestCase):
    def test_the_worktree_is_removed_even_when_the_gate_is_interrupted(self):
        repo = wave.Repo("o/g", {"path": str(G)})
        real = subprocess.run

        def interrupted(cmd, *a, **kw):
            if isinstance(cmd, str):  # the gate command; git calls are lists
                raise KeyboardInterrupt
            return real(cmd, *a, **kw)

        before = out("worktree", "list", "--porcelain")
        with mock.patch.object(wave.subprocess, "run", interrupted), self.assertRaises(KeyboardInterrupt):
            wave.run_gate(repo, out("rev-parse", "origin/main").strip(), [GATE])
        self.assertEqual(out("worktree", "list", "--porcelain"), before)

    def test_doctor_names_a_leftover(self):
        left = TMP / f"{wave.CHAIN_GATE_PREFIX}left"
        git("worktree", "add", "-q", "--detach", str(left), "main", cwd=G)
        try:
            p = run_wave("doctor", cwd=G)
            self.assertIn(f"! no leftover chain-gate worktree (an interrupted `order --run-gate`): leftover {left}", p.stdout)
        finally:
            git("worktree", "remove", "--force", str(left), cwd=G)
        self.assertIn("no leftover chain-gate worktree (an interrupted `order --run-gate`): none", run_wave("doctor", cwd=G).stdout)


if __name__ == "__main__":
    unittest.main()
