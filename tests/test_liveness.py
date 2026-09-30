"""Agent liveness: a running agent told from a dead one by what its work leaves on disk.

A third repo, o/l, gets one worktree per verdict. Nothing is inspected but files and git: every
mtime and commit date is set by the test, so no real process of the machine is looked at.
  l-9   likely dead: dispatched 90 min ago, newest edit 45 min ago, no PR
  l-10  working:     newest edit 2 min ago
  l-11  quiet:       newest edit 20 min ago
  l-12  likely dead: no stamp, one commit 50 min ago
  l-4   done:        PR 20 closes #4
"""
import json
import os
import subprocess
import time
import unittest
from datetime import datetime, timezone

from helpers import TMP, git, run_wave, sandbox, wave

L = TMP / "l"


def ago(minutes: float) -> float:
    return time.time() - minutes * 60


def age(path, minutes: float) -> None:
    t = ago(minutes)
    os.utime(path, (t, t))


def worktree(n: int, branch: str, stamp_minutes: float | None = None):
    wt = TMP / f"l-{n}"
    git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=L)
    if stamp_minutes is not None:
        at = datetime.fromtimestamp(ago(stamp_minutes), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        (wt / ".setwave.json").write_text(json.dumps({"issue": n, "repo": "o/l", "dispatched_at": at, "prompt": "p.md"}))
        age(wt / ".setwave.json", stamp_minutes)
    return wt


def edit(wt, name: str, minutes: float) -> None:
    (wt / name).write_text("work\n")
    age(wt / name, minutes)


def setUpModule():
    sandbox()
    if L.exists():
        return
    bare = TMP / "remote" / "o" / "l.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(L), cwd=TMP)
    (L / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true"]}))
    git("add", ".", cwd=L)
    git("commit", "-q", "-m", "base", cwd=L)
    git("push", "-q", "origin", "main", cwd=L)
    edit(worktree(9, "feat/9-dead", 90), "edit.txt", 45)
    edit(worktree(10, "feat/10-busy", 90), "busy.txt", 2)
    edit(worktree(11, "feat/11-quiet", 60), "slow.txt", 20)
    wt = worktree(12, "feat/12-committed")
    (wt / "c.txt").write_text("c\n")
    git("add", "c.txt", cwd=wt)
    at = datetime.fromtimestamp(ago(50), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=wt, check=True, capture_output=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": at, "GIT_AUTHOR_DATE": at})
    age(wt / "c.txt", 50)
    worktree(4, "feat/4-l", 90)


def repo():
    return wave.Repo("o/l", {"path": str(L)})


class Verdict(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual([wave.verdict(m, False) for m in (0, 9.9, 10, 30, 30.1, 600)],
                         ["working", "working", "quiet", "quiet", "likely dead", "likely dead"])

    def test_an_open_pr_is_done_however_old(self):
        self.assertEqual(wave.verdict(600, True), "done")


class Liveness(unittest.TestCase):
    def test_each_worktree_gets_its_verdict(self):
        got = {n: wave.liveness(repo(), n) for n in (9, 10, 11, 12, 4)}
        self.assertEqual({n: lv["verdict"] for n, lv in got.items()},
                         {9: "likely dead", 10: "working", 11: "quiet", 12: "likely dead", 4: "done"})
        self.assertEqual((got[9]["idle_min"], got[9]["age_min"]), (45, 90), "idle = newest edit, age = the stamp")
        self.assertEqual((got[12]["idle_min"], got[12]["age_min"]), (50, None), "a commit on the branch counts; no stamp, no age")
        self.assertEqual(got[4]["pr"], 20)


class Agents(unittest.TestCase):
    def test_every_worktree_is_listed_with_its_verdict(self):
        p = run_wave("agents", cwd=L)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows = {int(l.split()[0].split("#")[1]): l for l in p.stdout.splitlines() if l.startswith("  o/l#")}
        self.assertTrue({4, 9, 10, 11, 12} <= set(rows), p.stdout)
        for n, v in {9: "likely dead", 10: "working", 11: "quiet", 12: "likely dead", 4: "done"}.items():
            with self.subTest(n=n):
                self.assertTrue(rows[n].rstrip().endswith(v), rows[n])
        self.assertIn("dispatched 90 min ago", rows[9])
        self.assertIn("changed 45 min ago", rows[9])
        self.assertIn("PR #20", rows[4])

    def test_inside_a_worktree_it_lists_the_siblings_too(self):
        p = run_wave("agents", cwd=TMP / "l-9")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = {int(l.split()[0].split("#")[1]) for l in p.stdout.splitlines() if l.startswith("  o/l#")}
        self.assertTrue({4, 9, 10, 11, 12} <= rows, p.stdout)


class Doctor(unittest.TestCase):
    def test_likely_dead_agents_are_named_with_the_recovery(self):
        p = run_wave("doctor", cwd=L)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        dead = next(l for l in p.stdout.splitlines() if "likely dead" in l and l.startswith("  !"))
        for name in ("l-9", "l-12"):
            self.assertIn(name, dead)
        for name in ("l-10", "l-11", "l-4"):
            self.assertNotIn(name + " ", dead)
        self.assertIn("SendMessage", dead)
        self.assertIn("`wave dispatch --force 9`", dead)
        alive = next(l for l in p.stdout.splitlines() if "agents at work" in l)
        self.assertIn("l-10 working", alive)
        self.assertIn("l-11 quiet", alive)
        self.assertNotIn("l-10", next(l for l in p.stdout.splitlines() if "leftover" in l),
                         "an agent that has not committed yet is not a leftover")


class Cleanup(unittest.TestCase):
    def test_a_likely_dead_worktree_is_kept(self):
        p = run_wave("cleanup", cwd=L)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("KEEP l-9:", p.stdout)
        self.assertTrue((TMP / "l-9").exists())


class Dispatch(unittest.TestCase):
    def test_the_stamp_is_written_and_excluded(self):
        p = run_wave("dispatch", "8", "--force", cwd=L)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        wt = TMP / "l-8"
        stamp = json.loads((wt / ".setwave.json").read_text())
        self.assertEqual({k: stamp[k] for k in ("issue", "repo")}, {"issue": 8, "repo": "o/l"})
        self.assertEqual(stamp["prompt"], str(TMP / "l-handoffs" / "8.md"))
        self.assertLess(abs(datetime.strptime(stamp["dispatched_at"], "%Y-%m-%dT%H:%M:%SZ")
                            .replace(tzinfo=timezone.utc).timestamp() - time.time()), 120)
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=wt, capture_output=True, text=True).stdout
        self.assertNotIn(".setwave.json", status, "the stamp is excluded, never committed by accident")
        self.assertFalse((L / ".gitignore").exists(), "the repo's .gitignore is not touched")
        self.assertEqual(wave.liveness(repo(), 8)["verdict"], "working", "a fresh dispatch is working")
        again = run_wave("dispatch", "8", "--force", cwd=L)
        self.assertIn("o/l#8: worktree exists at", again.stdout)
        self.assertIn("keeping it (working, changed 0 min ago)", again.stdout,
                      "--force over a worktree says what its agent looks like before a second one starts there")


class Next(unittest.TestCase):
    def test_a_dead_worktree_stays_worktree_and_why_says_how_old(self):
        age(TMP / "r-5", 120)  # o/r#5's worktree: a bare directory, so its own mtime is the only trace
        cands = {c["number"]: c for c in wave.candidates(wave.tree(wave.Repo.get("o/r"), 1))}
        self.assertEqual(cands[5]["state"], "worktree", "never counted as in progress")
        self.assertIn("likely dead", wave.why(cands[5]))
        self.assertIn("120 min", wave.why(cands[5]))
        p = run_wave("why", "5")
        self.assertIn("likely dead, nothing changed for 120 min", p.stdout)


if __name__ == "__main__":
    unittest.main()
