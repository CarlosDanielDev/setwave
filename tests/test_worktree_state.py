"""One answer to "what state is this worktree in?": `doctor`, `cleanup` and `agents` read `worktree_state`.

Two repos, each with worktrees named after the case:
  o/ws  (the fake gh lists its PRs: tests/fixtures/pr_list_all_o_ws.json)
    ws-1  squash-merged: PR 50 merged, remote branch gone      -> merged: doctor lists it, cleanup removes it
    ws-2  PR 51 merged, but a file edited since                -> dirty: dirt beats the merged PR
  o/gf  (no fixture: the fake gh fails, like a gh without network or login)
    gf-1  a commit, no PR visible                              -> unknown, never "likely dead"
    gf-2  an empty seat                                        -> unknown too: cleanup removes nothing
"""
import json
import unittest

from helpers import TMP, gh_calls, git, run_wave, sandbox, wave

W, G = TMP / "ws", TMP / "gf"


def make_repo(root, slug):
    bare = TMP / "remote" / f"{slug}.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(root), cwd=TMP)
    (root / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true"]}))
    git("add", ".", cwd=root)
    git("commit", "-q", "-m", "base", cwd=root)
    git("push", "-q", "origin", "main", cwd=root)


def worktree(root, name, branch, file=None, push=False):
    wt = TMP / name
    git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=root)
    if file:
        (wt / file).write_text("work\n")
        git("add", file, cwd=wt)
        git("commit", "-q", "-m", f"{branch}: {file}", cwd=wt)
        if push:
            git("push", "-q", "origin", branch, cwd=wt)
    return wt


def setUpModule():
    sandbox()
    if W.exists():
        return
    make_repo(W, "o/ws")
    worktree(W, "ws-1", "feat/1-squashed", "one.txt")
    worktree(W, "ws-2", "feat/2-dirty", "two.txt", push=True)
    (TMP / "ws-2" / "dirt.txt").write_text("not saved anywhere\n")
    make_repo(G, "o/gf")
    worktree(G, "gf-1", "feat/1-work", "one.txt")
    worktree(G, "gf-2", "feat/2-empty")


def line(out: str, text: str) -> str:
    return next(l for l in out.splitlines() if text in l)


class Pure(unittest.TestCase):
    def facts(self, **kw):
        return {"dirty": False, "unpushed": False, "pr_state": None, "commits": 0, "stamped": False,
                "progress": "working", **kw}

    def test_the_decision_list_in_order(self):
        d = wave.derive_state
        cases = [
            (dict(dirty=True, unpushed=True, pr_state="MERGED"), "dirty"),
            (dict(unpushed=True, pr_state="MERGED"), "unpushed"),
            (dict(unpushed=True, pr_state="unknown"), "unpushed"),
            (dict(pr_state="unknown", commits=0), "unknown"),
            (dict(pr_state="MERGED", commits=2), "merged"),
            (dict(pr_state="CLOSED"), "closed"),
            (dict(pr_state="OPEN"), "open"),
            (dict(), "empty"),
            (dict(stamped=True), "working"),
            (dict(commits=1, progress="quiet"), "quiet"),
            (dict(commits=1, progress="dead"), "dead"),
        ]
        for kw, want in cases:
            with self.subTest(kw=kw):
                self.assertEqual(d(self.facts(**kw)), want)


class Agree(unittest.TestCase):
    def test_doctor_and_cleanup_agree_on_a_squash_merged_worktree(self):
        doctor = run_wave("doctor", cwd=W)
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        leftovers = line(doctor.stdout, "leftover worktrees")
        cleanup = run_wave("cleanup", "--dry-run", cwd=W)
        self.assertEqual(cleanup.returncode, 0, cleanup.stdout + cleanup.stderr)
        self.assertIn("would remove ws-1: PR #50 merged", cleanup.stdout)
        self.assertIn("ws-1", leftovers, "doctor must call merged what cleanup removes")
        self.assertNotIn("ws-2", leftovers)

    def test_one_pr_query_answers_every_worktree(self):
        before = len(gh_calls())
        run_wave("cleanup", "--dry-run", cwd=W)
        asked = [c for c in gh_calls()[before:] if c[:2] == ["pr", "list"] and "--state" in c and c[c.index("--state") + 1] == "all"]
        self.assertEqual(len(asked), 1, asked)
        self.assertNotIn("--head", asked[0])


class DirtyBeatsThePr(unittest.TestCase):
    def test_a_dirty_worktree_with_a_merged_pr_is_kept_and_never_called_merged(self):
        rec = wave.worktree_state(wave.Repo("o/ws", {"path": str(W)}), TMP / "ws-2", "feat/2-dirty", 2)
        self.assertEqual((rec["state"], rec["pr_state"], rec["dirty"]), ("dirty", "MERGED", True))
        out = run_wave("cleanup", cwd=W).stdout
        self.assertIn("KEEP ws-2: dirty", out)
        self.assertTrue((TMP / "ws-2" / "dirt.txt").exists())
        self.assertNotIn("ws-2", line(run_wave("doctor", cwd=W).stdout, "leftover worktrees"))


class GhFailure(unittest.TestCase):
    def test_unknown_is_never_removed_and_never_likely_dead(self):
        rec = wave.worktree_state(wave.Repo("o/gf", {"path": str(G)}), TMP / "gf-2", "feat/2-empty", 2)
        self.assertEqual((rec["state"], rec["pr_state"]), ("unknown", "unknown"))
        out = run_wave("cleanup", cwd=G)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        for name in ("gf-1", "gf-2"):
            self.assertIn(f"KEEP {name}: PR state unknown", out.stdout)
            self.assertTrue((TMP / name).exists(), f"{name} removed while gh was failing")
        doctor = run_wave("doctor", cwd=G)
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        unknown = line(doctor.stdout, "PR state could be read")
        self.assertIn("gf-1", unknown)
        self.assertIn("gf-2", unknown)
        self.assertNotIn("likely dead", doctor.stdout.replace("no agent likely dead", ""))
        self.assertNotIn("gf-1", line(doctor.stdout, "leftover worktrees"))


if __name__ == "__main__":
    unittest.main()
