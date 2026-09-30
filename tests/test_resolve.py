"""`wave resolve`: merge the base into a conflicting PR's branch in its worktree, gate, push without force, comment.

A fifth repo, o/m, gates with `test ! -e red`. Every PR branch starts from the first commit of main;
main then moves on (line one of f.txt becomes "base", b.txt appears), so:
  PR 40 t/clean     adds c.txt: the base merges in cleanly
  PR 41 t/conflict  line one becomes "mine": the base merge conflicts on f.txt
  PR 42 t/dirty     its worktree holds an uncommitted change: refused before any merge
  PR 43 t/red       adds `red`: the merge is clean, the gate is not, nothing is pushed
  PR 44 t/late      listed MERGEABLE, but `pr view` says CONFLICTING when merge asks
  PR 45 t/behind    origin has a commit its worktree has not pulled: refused, the push would not fast-forward
Each PR but 44 has a worktree at <tmp>/m-<N>. Comments go to the fake gh, which logs them and sends nothing.
"""
import os
import subprocess
import tempfile
import unittest

from helpers import TMP, gh_calls, git, run_wave, sandbox

M = TMP / "m"
BARE = TMP / "remote" / "o" / "m.git"
BRANCHES = {40: ("t/clean", "c.txt", "c\n"), 41: ("t/conflict", "f.txt", "mine\ntwo\n"),
            42: ("t/dirty", "d.txt", "d\n"), 43: ("t/red", "red", "\n"), 44: ("t/late", "l.txt", "l\n"),
            45: ("t/behind", "e.txt", "e\n")}


def setUpModule():
    sandbox()
    (TMP / "tmp").mkdir(exist_ok=True)
    os.environ["TMPDIR"] = tempfile.tempdir = str(TMP / "tmp")  # the gate's throwaway worktrees stay in the sandbox
    os.environ["FAKE_GH_COMMENT"] = "1"
    if M.exists():
        return
    git("init", "-q", "--bare", str(BARE), cwd=TMP)
    git("clone", "-q", str(BARE), str(M), cwd=TMP)
    (M / "f.txt").write_text("one\ntwo\n")
    (M / ".wave.json").write_text('{"base": "main", "gate": ["test ! -e red"]}')
    git("add", ".", cwd=M)
    git("commit", "-q", "-m", "base", cwd=M)
    git("push", "-q", "origin", "main", cwd=M)
    for n, (branch, path, text) in BRANCHES.items():
        git("checkout", "-q", "-b", branch, "main", cwd=M)
        (M / path).write_text(text)
        git("add", path, cwd=M)
        git("commit", "-q", "-m", f"{branch}: {path}", cwd=M)
        git("push", "-q", "-u", "origin", branch, cwd=M)
        git("checkout", "-q", "main", cwd=M)
    (M / "f.txt").write_text("base\ntwo\n")
    (M / "b.txt").write_text("b\n")
    git("add", ".", cwd=M)
    git("commit", "-q", "-m", "main moves on", cwd=M)
    git("push", "-q", "origin", "main", cwd=M)
    for n in (40, 41, 42, 43, 45):
        git("worktree", "add", "-q", str(TMP / f"m-{n}"), BRANCHES[n][0], cwd=M)
    ahead = out("commit-tree", "t/behind^{tree}", "-p", "t/behind", "-m", "pushed from elsewhere")
    git("push", "-q", "origin", f"{ahead}:refs/heads/t/behind", cwd=M)
    (TMP / "m-42" / "d.txt").write_text("changed, not committed\n")


def out(*args, cwd=M) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def remote_head(branch: str) -> str:
    return out("rev-parse", branch, cwd=BARE)


def comments(n: int, since: int) -> list[list[str]]:
    return [c for c in gh_calls()[since:] if c[:3] == ["pr", "comment", str(n)]]


class CleanMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = remote_head("t/clean")
        cls.calls = len(gh_calls())
        cls.p = run_wave("resolve", "40", cwd=M)

    def test_the_base_is_merged_in_and_pushed_without_rewriting_the_branch(self):
        self.assertEqual(self.p.returncode, 0, self.p.stdout + self.p.stderr)
        new = remote_head("t/clean")
        self.assertNotEqual(new, self.old)
        parents = out("rev-list", "--parents", "-n", "1", new, cwd=BARE).split()[1:]
        self.assertEqual(parents, [self.old, remote_head("main")], "a merge commit on top of the old head: no rewrite")
        self.assertEqual(out("status", "--porcelain", cwd=TMP / "m-40"), "")

    def test_the_pr_gets_a_comment_naming_the_base_sha_and_the_gate(self):
        [c] = comments(40, self.calls)
        body = c[c.index("--body") + 1]
        self.assertIn(f"merged `main` at `{remote_head('main')[:7]}`", body)
        self.assertIn("test ! -e red", body)


class Conflict(unittest.TestCase):
    def test_conflict_then_markers_refused_then_continue(self):
        wt = TMP / "m-41"
        old = remote_head("t/conflict")
        calls = len(gh_calls())

        p = run_wave("resolve", "41", cwd=M)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("f.txt: lines 1-5", p.stdout)
        self.assertIn("resolve by hand, then `wave resolve o/m#41 --continue`", p.stdout)
        self.assertEqual(out("diff", "--name-only", "--diff-filter=U", cwd=wt), "f.txt", "left in the conflicted state")
        self.assertEqual(remote_head("t/conflict"), old)

        # staged with the markers still in it: git no longer calls it unmerged, the marker check still does
        git("add", "f.txt", cwd=wt)
        p = run_wave("resolve", "41", "--continue", cwd=M)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("conflict markers", p.stdout + p.stderr)
        self.assertTrue((wt / ".git").exists() and out("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=wt), "the merge is still in progress")
        self.assertEqual(remote_head("t/conflict"), old)
        self.assertEqual(comments(41, calls), [])

        (wt / "f.txt").write_text("base and mine\ntwo\n")
        git("add", "f.txt", cwd=wt)
        p = run_wave("resolve", "41", "--continue", cwd=M)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        new = remote_head("t/conflict")
        self.assertEqual(out("rev-list", "--parents", "-n", "1", new, cwd=BARE).split()[1:], [old, remote_head("main")])
        self.assertEqual(out("show", f"{new}:f.txt", cwd=BARE), "base and mine\ntwo")
        self.assertEqual(len(comments(41, calls)), 1)


class Refusals(unittest.TestCase):
    def test_a_dirty_worktree_is_refused_not_stashed(self):
        wt = TMP / "m-42"
        before = out("status", "--porcelain", cwd=wt)
        p = run_wave("resolve", "42", cwd=M)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("uncommitted", p.stdout + p.stderr)
        self.assertEqual(out("status", "--porcelain", cwd=wt), before, "the change is where it was")
        self.assertEqual(out("stash", "list", cwd=wt), "")
        self.assertEqual(out("rev-parse", "HEAD", cwd=wt), remote_head("t/dirty"), "no merge started")

    def test_a_branch_behind_its_remote_is_refused_before_merging(self):
        wt = TMP / "m-45"
        head = out("rev-parse", "HEAD", cwd=wt)
        p = run_wave("resolve", "45", cwd=M)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("git pull --ff-only", p.stdout + p.stderr)
        self.assertEqual(out("rev-parse", "HEAD", cwd=wt), head, "no merge commit left behind")

    def test_a_red_gate_pushes_nothing(self):
        old = remote_head("t/red")
        calls = len(gh_calls())
        p = run_wave("resolve", "43", cwd=M)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("test ! -e red", p.stdout)
        self.assertEqual(remote_head("t/red"), old)
        self.assertEqual(comments(43, calls), [])


class MergePointsAtResolve(unittest.TestCase):
    def test_merge_prints_the_resolve_command_where_it_stops(self):
        for n in (41, 44):  # 41: verify sees CONFLICTING in the list; 44: `pr view` says so at merge time
            with self.subTest(pr=n):
                p = run_wave("merge", str(n), "--yes", cwd=M)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn(f"wave resolve o/m#{n}", p.stdout)


if __name__ == "__main__":
    unittest.main()
