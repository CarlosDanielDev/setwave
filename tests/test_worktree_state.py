"""One answer to "what state is this worktree in?": `doctor`, `cleanup` and `agents` read `worktree_state`.

Every test builds its own sandbox (a fresh directory with a bare remote, a clone and the worktrees it needs), so
none depends on another's removals or on the order unittest runs them in. Two repos: `ws`, whose PRs the fake gh
lists (tests/fixtures/pr_list_all_o_ws.json: 50 merged feat/1-squashed, 51 merged feat/2-dirty, 52 merged
feat/3-ahead), and `gf`, which has no fixture, so the fake gh fails like a gh without network or login.
"""
import contextlib
import io
import itertools
import json
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from helpers import TMP, gh_calls, git, run_wave, sandbox, wave

_ids = itertools.count(1)


def setUpModule():
    sandbox()


def line(out: str, text: str) -> str:
    return next(l for l in out.splitlines() if text in l)


class Case(unittest.TestCase):
    """A private sandbox per test: self.root is the clone, self.wt(...) adds worktrees beside it."""
    slug = "ws"

    def setUp(self):
        self.base = TMP / f"case-{next(_ids)}"
        bare = self.base / "remote" / "o" / f"{self.slug}.git"
        self.root = self.base / self.slug
        git("init", "-q", "--bare", str(bare), cwd=TMP)
        git("clone", "-q", str(bare), str(self.root), cwd=TMP)
        (self.root / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true"]}))
        git("add", ".", cwd=self.root)
        git("commit", "-q", "-m", "base", cwd=self.root)
        git("push", "-q", "origin", "main", cwd=self.root)
        excl = self.root / ".git" / "info" / "exclude"  # what dispatch writes: the stamp never counts as dirt
        excl.parent.mkdir(parents=True, exist_ok=True)
        excl.write_text("/.setwave.json\n")

    def wt(self, n, branch, file=None, push=False, dirt=False, ahead=False, stamp=None):
        path = self.base / f"{self.slug}-{n}"
        git("worktree", "add", "-q", "-b", branch, str(path), "origin/main", cwd=self.root)
        if file:
            (path / file).write_text("work\n")
            git("add", file, cwd=path)
            git("commit", "-q", "-m", f"{branch}: {file}", cwd=path)
            if push:
                git("push", "-q", "origin", branch, cwd=path)
        if ahead:  # pushed, then a local commit
            (path / "more.txt").write_text("more\n")
            git("add", "more.txt", cwd=path)
            git("commit", "-q", "-m", f"{branch}: more", cwd=path)
        if dirt:
            (path / "dirt.txt").write_text("not saved anywhere\n")
        if stamp:
            at = datetime.fromtimestamp(datetime.now().timestamp(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            (path / ".setwave.json").write_text(json.dumps({"issue": n, "repo": f"o/{self.slug}", "dispatched_at": at,
                                                           "prompt": "p.md", **stamp}))
        return path

    def wave(self, *args):
        p = run_wave(*args, cwd=self.root)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def state(self, path, branch, n):
        return wave.worktree_state(wave.Repo(f"o/{self.slug}", {"path": str(self.root)}), path, branch, n)


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


class Agree(Case):
    def test_doctor_and_cleanup_agree_on_a_squash_merged_worktree(self):
        self.wt(1, "feat/1-squashed", "one.txt")  # its remote branch was never pushed: gone after the squash merge
        self.wt(2, "feat/2-dirty", "two.txt", push=True, dirt=True)
        leftovers = line(self.wave("doctor"), "leftover worktrees")
        cleanup = self.wave("cleanup", "--dry-run")
        self.assertIn("would remove ws-1: PR #50 merged", cleanup)
        self.assertIn("ws-1", leftovers, "doctor must call merged what cleanup removes")
        self.assertNotIn("ws-2", leftovers)

    def test_one_pr_query_answers_every_worktree(self):
        self.wt(1, "feat/1-squashed", "one.txt")
        self.wt(3, "feat/3-ahead", "three.txt", push=True, ahead=True)
        before = len(gh_calls())
        self.wave("cleanup", "--dry-run")
        asked = [c for c in gh_calls()[before:] if c[:2] == ["pr", "list"] and "--state" in c and c[c.index("--state") + 1] == "all"]
        self.assertEqual(len(asked), 1, asked)
        self.assertNotIn("--head", asked[0])


class DirtyBeatsThePr(Case):
    def test_a_dirty_worktree_with_a_merged_pr_is_kept_and_never_called_merged(self):
        path = self.wt(2, "feat/2-dirty", "two.txt", push=True, dirt=True)
        rec = self.state(path, "feat/2-dirty", 2)
        self.assertEqual((rec["state"], rec["pr_state"], rec["dirty"]), ("dirty", "MERGED", True))
        self.assertIn("KEEP ws-2: dirty", self.wave("cleanup"))
        self.assertTrue((path / "dirt.txt").exists())
        self.assertNotIn("ws-2", line(self.wave("doctor"), "leftover worktrees"))


class UnpushedBeatsThePr(Case):
    def test_cleanup_keeps_an_unpushed_worktree_whose_pr_merged(self):
        path = self.wt(3, "feat/3-ahead", "three.txt", push=True, ahead=True)
        out = self.wave("cleanup", "--stale-days", "0", "--yes")
        self.assertIn("KEEP ws-3: unpushed (origin/feat/3-ahead is behind)", out)
        self.assertTrue((path / "more.txt").exists())

    def test_doctor_names_unsaved_work_behind_a_merged_pr_and_never_suggests_dispatch(self):
        self.wt(2, "feat/2-dirty", "two.txt", push=True, dirt=True)
        self.wt(3, "feat/3-ahead", "three.txt", push=True, ahead=True)
        out = self.wave("doctor")
        unsaved = line(out, "unsaved work behind a merged PR")
        for name in ("ws-2", "ws-3"):
            self.assertIn(name, unsaved)
        self.assertNotIn("dispatch --force", unsaved)
        self.assertNotIn("ws-3", line(out, "agents at work"))
        self.assertNotIn("ws-3", line(out, "no agent likely dead"))

    def test_unsaved_work_past_the_retry_ceiling_is_still_unsaved_not_exhausted(self):
        self.wt(2, "feat/2-dirty", "two.txt", push=True, dirt=True, stamp={"resumes": wave.RETRY_CEILING})
        self.wt(3, "feat/3-ahead", "three.txt", push=True, ahead=True, stamp={"resumes": wave.RETRY_CEILING})
        out = self.wave("doctor")
        unsaved = line(out, "unsaved work behind a merged PR")
        self.assertIn("ws-2", unsaved)
        self.assertIn("ws-3", unsaved)
        self.assertIn("none", line(out, "retry ceiling"))


class Truncated(Case):
    def test_a_list_that_fills_the_limit_is_unknown_and_nothing_is_removed(self):
        path = self.wt(4, "feat/4-nopr", "four.txt", push=True)  # no PR listed: stale for --stale-days 0
        out, here = io.StringIO(), os.getcwd()
        wave.Repo._cache.pop("o/ws", None)
        os.chdir(self.root)
        try:  # the fixture has as many PRs as the patched limit: gh may have cut the list short
            with mock.patch.object(wave, "PR_LIST_LIMIT", 3), contextlib.redirect_stdout(out):
                wave.main(["cleanup", "--stale-days", "0", "--yes"])
        finally:
            os.chdir(here)
            wave.Repo._cache.pop("o/ws", None)
        self.assertIn("KEEP ws-4: PR state unknown", out.getvalue())
        self.assertNotIn("removed ws-", out.getvalue())
        self.assertTrue(path.exists())


class GhFailure(Case):
    slug = "gf"

    def test_unknown_is_never_removed_and_never_likely_dead(self):
        self.wt(1, "feat/1-work", "one.txt")
        empty = self.wt(2, "feat/2-empty")
        rec = self.state(empty, "feat/2-empty", 2)
        self.assertEqual((rec["state"], rec["pr_state"]), ("unknown", "unknown"))
        out = self.wave("cleanup")
        for name in ("gf-1", "gf-2"):
            self.assertIn(f"KEEP {name}: PR state unknown", out)
            self.assertTrue((self.base / name).exists(), f"{name} removed while gh was failing")
        doctor = self.wave("doctor")
        unknown = line(doctor, "PR state could be read")
        self.assertIn("gf-1", unknown)
        self.assertIn("gf-2", unknown)
        self.assertNotIn("likely dead", doctor.replace("no agent likely dead", ""))
        self.assertNotIn("gf-1", line(doctor, "leftover worktrees"))

    def test_unknown_past_the_retry_ceiling_is_still_unknown_not_exhausted(self):
        self.wt(1, "feat/1-work", "one.txt", stamp={"resumes": wave.RETRY_CEILING})
        doctor = self.wave("doctor")
        self.assertIn("gf-1", line(doctor, "PR state could be read"))
        self.assertIn("none", line(doctor, "retry ceiling"))


if __name__ == "__main__":
    unittest.main()
