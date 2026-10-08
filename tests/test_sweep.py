"""The state sweep and the retry ceiling: what an issue claims against what GitHub and the worktrees prove.

A repo of its own, o/x, carries one leaf per lie under epic #1:
  #2  false done-unclosed: every Done-when item ticked or struck, and no PR closes it at all
  #3  false closed: closed, but its only PR (#30) is open, not merged
  #4  closed by merged PR #40, whose ledger was never applied to the issue
  #5  closed by merged PR #41 with its ledger applied: clean, so sweep writes nothing there
  #6  open, its worktree stamp counts 3 resumes without a new commit: agent-exhausted
  #7  counts resumes live: dispatch without a commit climbs to the ceiling, the fourth is refused

Writes go through FAKE_GH_EDIT and a separate call log, the way test_ledger does.
"""
import json
import os
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone

from helpers import TMP, WAVE, git, run_wave, sandbox, wave

S = TMP / "x"


def setUpModule():
    sandbox()
    if S.exists():
        return
    bare = TMP / "remote" / "o" / "x.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(S), cwd=TMP)
    (S / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true"]}))
    git("add", ".", cwd=S)
    git("commit", "-q", "-m", "base", cwd=S)
    git("push", "-q", "origin", "main", cwd=S)
    reg = wave.load_registry()
    reg["repos"]["o/x"] = {"path": str(S)}
    wave.save_registry(reg)
    # x-6: an exhausted agent, by its own stamp (dispatched 90 min ago, nothing for 45)
    wt = TMP / "x-6"
    git("worktree", "add", "-q", "-b", "feat/6-slow", str(wt), "origin/main", cwd=S)
    at = datetime.fromtimestamp(time.time() - 90 * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (wt / ".setwave.json").write_text(json.dumps(
        {"issue": 6, "repo": "o/x", "dispatched_at": at, "prompt": "p.md", "resumes": 3}))
    old = time.time() - 45 * 60
    os.utime(wt / ".setwave.json", (old, old))


def repo():
    return wave.Repo.get("o/x")


def writes(*args, log: str):
    """`wave.py <args>` on o/x, with the fake gh accepting issue edits/comments/reopens, logged to their own file."""
    env = {**os.environ, "FAKE_GH_EDIT": "1", "FAKE_GH_LOG": str(TMP / f"gh-{log}.log")}
    p = subprocess.run([sys.executable, str(WAVE), *args], cwd=S, capture_output=True, text=True, env=env)
    f = TMP / f"gh-{log}.log"
    calls = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
    return p, calls


def edited(calls) -> dict[str, str]:
    return {c[2]: c[c.index("--body") + 1] for c in calls if c[:2] == ["issue", "edit"]}


def commented(calls) -> dict[str, str]:
    return {c[2]: c[c.index("--body") + 1] for c in calls if c[:2] == ["issue", "comment"]}


class StateSweep(unittest.TestCase):
    def test_after_the_fix_next_sees_the_leaf_again(self):
        body = repo().issue(2)["body"].replace("- [x] one", "- [ ] one")
        nd = {"key": "o/x#2", "repo": "o/x", "number": 2,
              "issue": {**repo().issue(2), "body": body}, "children": [], "parent": "o/x#1"}
        c = wave.candidates({"o/x#2": nd})[0]
        self.assertEqual(c["state"], "ready", "unticked and PR-less, the leaf is dispatchable again")

    def test_fix_reverts_the_lies_with_comments(self):
        p, calls = writes("sweep", "1", "--fix", log="sweep-fix")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        for done_action in ("o/x#2: unticked 1 item(s) back to open and commented",
                            "o/x#3: reopened", "o/x#6: commented the escalation",
                            "o/x#1: commented the escalation on the epic"):
            self.assertIn(done_action, p.stdout, f"--fix says what it did: {done_action}")
        edits, comments = edited(calls), commented(calls)
        self.assertEqual(edits["2"], repo().issue(2)["body"].replace("- [x] one", "- [ ] one"),
                         "the ticked item goes back to open; the strike stays")
        self.assertIn("no PR closes it at all", comments["2"])
        self.assertIn("wave sweep", comments["2"])
        reopen = [c for c in calls if c[:2] == ["issue", "reopen"]]
        self.assertEqual([c[2] for c in reopen], ["3"])
        self.assertIn("PR #30 is open, not merged", " ".join(reopen[0]))
        self.assertEqual(edits["4"], repo().issue(4)["body"].replace("- [ ] four-a", "- [x] four-a"))
        self.assertIn("ledger applied from PR #40: 1 done, 0 dropped", comments["4"])
        self.assertNotIn("5", edits, "#5 is honest: nothing written there")
        self.assertNotIn("5", comments)
        self.assertIn("agent-exhausted", comments["6"])
        self.assertIn("retry ceiling", comments["1"], "the epic carries the escalation too")
        self.assertIn("o/x#6", comments["1"])

    def test_read_only_reports_without_writing(self):
        p, calls = writes("sweep", "1", log="sweep-ro")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("o/x#2 false-done", p.stdout)
        self.assertIn("no PR closes it at all", p.stdout)
        self.assertIn("o/x#3 false-closed", p.stdout)
        self.assertIn("PR #30 is open, not merged", p.stdout)
        self.assertIn("o/x#4 ledger-not-applied", p.stdout)
        self.assertNotIn("o/x#5", p.stdout)
        self.assertIn("o/x#6 agent-exhausted", p.stdout)
        self.assertIn("read-only", p.stdout)
        self.assertEqual([c for c in calls if c[:1] == ["issue"]], [], "nothing written")


class RetryCeiling(unittest.TestCase):
    def test_dispatch_climbs_to_the_ceiling_and_refuses_the_fourth_resume(self):
        for i in range(4):  # one fresh dispatch, then three resumes, each without a new commit
            p = run_wave("dispatch", "7", "--force", cwd=S)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        stamp = json.loads((TMP / "x-7" / ".setwave.json").read_text())
        self.assertEqual(stamp["resumes"], 3, "three resumes without a new commit")
        refused = run_wave("dispatch", "7", "--force", cwd=S)
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("agent-exhausted", refused.stdout + refused.stderr)
        after = json.loads((TMP / "x-7" / ".setwave.json").read_text())
        self.assertEqual((after["resumes"], after["dispatched_at"]),
                         (stamp["resumes"], stamp["dispatched_at"]), "the refusal writes nothing")

    def test_exhausted_is_its_own_state_and_never_ready(self):
        cands = {c["number"]: c for c in wave.candidates(wave.tree(repo(), 1))}
        self.assertEqual(cands[6]["state"], "agent-exhausted")
        self.assertFalse(cands[6]["ready"])
        self.assertIn("agent-exhausted", wave.why(cands[6]))
        self.assertIn("3 resumes", wave.why(cands[6]))

    def test_a_commit_resets_the_counter(self):
        # x-8: its own worktree, so this holds whatever order the classes run in
        p = run_wave("dispatch", "8", "--force", cwd=S)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        wt = TMP / "x-8"
        (wt / "progress.txt").write_text("work\n")
        git("add", "progress.txt", cwd=wt)
        at = datetime.fromtimestamp(time.time() + 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
        subprocess.run(["git", "commit", "-q", "-m", "progress"], cwd=wt, check=True, capture_output=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": at, "GIT_AUTHOR_DATE": at})
        p = run_wave("dispatch", "8", "--force", cwd=S)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        after = json.loads((wt / ".setwave.json").read_text())
        self.assertEqual(after["resumes"], 0, "a commit newer than the last dispatch is progress")


class Doctor(unittest.TestCase):
    def test_doctor_reports_the_ceiling_and_the_state_sweep(self):
        p = run_wave("doctor", "--epic", "1", cwd=S)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)  # sweep findings are soft, never a refused dispatch
        ceiling = next(l for l in p.stdout.splitlines() if "retry ceiling" in l)
        self.assertTrue(ceiling.lstrip().startswith("!"), ceiling)
        self.assertIn("x-6", ceiling)
        self.assertIn("sweep", ceiling)
        sweep_line = next(l for l in p.stdout.splitlines() if "state sweep" in l)
        self.assertIn("o/x#2", sweep_line)
        self.assertIn("false-done", sweep_line)


class Status(unittest.TestCase):
    def test_status_shows_the_exclusive_state(self):
        p = run_wave("status", "1", cwd=S)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("- [ ] #6 Exhausted leaf — agent-exhausted", p.stdout)


if __name__ == "__main__":
    unittest.main()
