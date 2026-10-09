"""Cleanup decides by the PR, not by `git --merged`.

A fourth repo, o/k, holds one worktree per case the decision reads; the fake `gh`
answers `pr list --state all --head <branch>` from tests/fixtures/pr_list_all_o_k.json:
  k-6   PR 40 merged, pushed, clean        -> removed, branch kept, size printed
  k-7   PR 41 closed without merge         -> kept; `--include-closed` removes
  k-8   PR 42 open (another epic's issue)  -> kept, however old
  k-9   no PR, pushed, untouched 40 days   -> kept with its age; `--stale-days` lists, `--yes` removes
  k-10  no PR, no commits, no stamp        -> removed (an empty seat)
  k-11  dirty                              -> never removed
  k-12  dispatched, no commit yet          -> kept (an agent's seat)
  k-13  no PR, a commit, never pushed      -> kept with its age
  k-14  PR 43 merged, remote gone          -> removed (the squash-merge case `--merged` never saw)
  k-15  pushed, then a local commit        -> kept (unpushed)
"""
import json
import os
import subprocess
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from helpers import TMP, gh_calls, git, run_wave, sandbox

K = TMP / "k"


def ago(minutes: float) -> float:
    return time.time() - minutes * 60


def worktree(n: int, branch: str, file: str | None = None, days_old: float | None = None,
             push: bool = False, stamp_min: float | None = None) -> Path:
    """A worktree off origin/main with an optional committed (and pushed) file, its commit dated
    `days_old` days ago, and an optional dispatch stamp `stamp_min` minutes old."""
    wt = TMP / f"k-{n}"  # a sibling of the clone, like `wave dispatch` makes them
    git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=K)
    if file:
        (wt / file).write_text("work\n")
        env = dict(os.environ)
        if days_old is not None:
            at = datetime.fromtimestamp(ago(days_old * 24 * 60), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
            env.update({"GIT_COMMITTER_DATE": at, "GIT_AUTHOR_DATE": at})
        git("add", file, cwd=wt)
        subprocess.run(["git", "commit", "-q", "-m", f"{branch}: {file}"], cwd=wt, check=True,
                       capture_output=True, env=env)
        if push:
            git("push", "-q", "origin", branch, cwd=wt)
    if stamp_min is not None:
        at = datetime.fromtimestamp(ago(stamp_min), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        (wt / ".setwave.json").write_text(json.dumps({"issue": n, "repo": "o/k", "dispatched_at": at, "prompt": "p.md"}))
    return wt


def setUpModule():
    sandbox()
    if K.exists():
        return
    bare = TMP / "remote" / "o" / "k.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(K), cwd=TMP)
    (K / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true"]}))
    git("add", ".", cwd=K)
    git("commit", "-q", "-m", "base", cwd=K)
    git("push", "-q", "origin", "main", cwd=K)
    excl = K / ".git" / "info" / "exclude"  # what dispatch writes: the stamp never counts as dirt
    excl.parent.mkdir(parents=True, exist_ok=True)
    excl.write_text("/.setwave.json\n")
    worktree(6, "feat/6-merged", "six.txt", push=True)
    worktree(7, "feat/7-closed", "seven.txt", push=True)
    worktree(8, "feat/8-open", "eight.txt", days_old=60, push=True)
    worktree(9, "feat/9-abandoned", "nine.txt", days_old=40, push=True)
    worktree(10, "feat/10-empty")
    worktree(11, "feat/11-dirty", "eleven.txt", push=True)
    (TMP / "k-11" / "dirt.txt").write_text("dirty\n")
    worktree(12, "feat/12-seat", stamp_min=5)
    worktree(13, "feat/13-local", "thirteen.txt")
    worktree(14, "feat/14-gone", "fourteen.txt")  # its remote branch was deleted after the squash merge
    worktree(15, "feat/15-ahead", "fifteen.txt", push=True)
    (TMP / "k-15" / "more.txt").write_text("more\n")  # pushed, then advanced locally: unpushed
    git("add", "more.txt", cwd=TMP / "k-15")
    subprocess.run(["git", "commit", "-q", "-m", "feat/15-ahead: more"], cwd=TMP / "k-15", check=True,
                   capture_output=True)


def ran(*args: str) -> str:
    p = run_wave("cleanup", *args, cwd=K)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


class Decision(unittest.TestCase):
    def test_the_matrix_keeps_and_removes_by_pr_state(self):
        out = ran("--dry-run")
        for name in ("k-6", "k-10", "k-14"):
            self.assertRegex(out, rf"would remove {name}: .+ \d[\d.]* (KB|MB)")
        for name in ("k-7", "k-8", "k-9", "k-11", "k-12", "k-13", "k-15"):
            self.assertIn(f"KEEP {name}:", out, out)
            self.assertTrue((TMP / name).exists(), "--dry-run removes nothing")
        self.assertNotIn("reclaimed", out)

        out = ran()  # plain: the merged and the empty go, everything else stays
        self.assertIn("removed k-6: PR #40 merged", out)
        self.assertIn("removed k-14: PR #43 merged", out)
        self.assertIn("removed k-10: empty: no PR, no commits", out)
        self.assertRegex(out, r"reclaimed \d[\d.]* (KB|MB) from 3 worktree\(s\)")
        for name in ("k-7", "k-8", "k-9", "k-11", "k-12", "k-13", "k-15"):
            self.assertIn(f"KEEP {name}:", out, out)
            self.assertTrue((TMP / name).exists(), out)
        self.assertIn("KEEP k-7: closed without merge: `--include-closed` to remove", out)
        self.assertIn("KEEP k-8: PR #42 open (in progress)", out)
        self.assertRegex(out, r"KEEP k-9: no PR, newest change (39|40) days ago")
        self.assertIn("KEEP k-11: dirty", out)
        self.assertIn("KEEP k-12: dispatched, no commit yet", out)
        self.assertIn("KEEP k-15: unpushed (origin/feat/15-ahead is behind)", out)
        self.assertTrue(any(c[:8] == ["pr", "list", "-R", "o/k", "--state", "all", "--head", "feat/6-merged"]
                            for c in gh_calls()), "the decision read the PR, not only --merged")
        self.assertEqual(subprocess.run(["git", "rev-parse", "--verify", "-q", "feat/6-merged"],
                                        cwd=K, capture_output=True).returncode, 0, "the branch stays")

        out = ran("--include-closed")
        self.assertIn("removed k-7: PR #41 closed without merge (--include-closed)", out)
        self.assertFalse((TMP / "k-7").exists())

        out = ran("--stale-days", "50")  # 40 days is under 50: kept, and not even listed
        self.assertNotIn("STALE k-9", out)
        out = ran("--stale-days", "30")  # listed with its size, still kept without --yes
        self.assertRegex(out, r"STALE k-9: no PR, newest change (39|40) days ago, \d[\d.]* (KB|MB) — `--yes` to remove")
        self.assertNotIn("STALE k-8", out, "an open PR is never stale, however old")
        self.assertTrue((TMP / "k-9").exists())

        out = ran("--stale-days", "30", "--yes")
        self.assertIn("removed k-9: stale: nothing changed for", out)
        self.assertFalse((TMP / "k-9").exists())
        self.assertTrue((TMP / "k-11").exists(), "dirty survives --yes")
        self.assertTrue((TMP / "k-15").exists(), "unpushed survives --yes")
        self.assertTrue((TMP / "k-12").exists(), "an agent's seat survives --yes")


class Flags(unittest.TestCase):
    def test_a_negative_stale_days_and_a_lone_yes_are_refused(self):
        p = run_wave("cleanup", "--stale-days", "-1", cwd=K)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("--stale-days wants N >= 0", p.stderr)
        p = run_wave("cleanup", "--yes", cwd=K)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("--yes only matters with --stale-days", p.stderr)
        self.assertTrue((TMP / "k-11").exists())


class Doctor(unittest.TestCase):
    def test_leftovers_carry_their_total_size(self):
        worktree(20, "t/leftover")  # never dispatched, never committed: the leftover `cleanup` removes
        p = run_wave("doctor", cwd=K)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        line = next(l for l in p.stdout.splitlines() if "leftover worktrees" in l)
        self.assertIn("k-20", line)
        self.assertRegex(line, r"\d[\d.]* (KB|MB) total")
        self.assertIn("`wave cleanup`", line)


if __name__ == "__main__":
    unittest.main()
