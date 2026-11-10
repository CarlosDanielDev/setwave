"""The seat progress bar (#116): `wave agents --line` renders one statusline line — `setwave: 2 working · 1
stalled · 1 done(#99) · 0 dead` — from local facts alone (git, the dispatch stamps, the process probe, the
PR cache the full commands' `gh pr list` refreshes): never gh, never a Repo built, never the registry
written, empty line and exit 0 outside a wave repo. The plugin ships the exact statusLine snippet
(statusline/README.md) that chains it after the user's own statusline; `dispatch` prints the segment,
`wave init --yes` and `wave hooks install` offer it; the SKILL mandates the chat seat table at every
agent event and names every spawn `wave-<N>` (the host renders its native rows for named agents only).

Fixture repo o/p carries one worktree per verdict; o/z is the subprocess fixture (no gh, no registration).
"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from helpers import CLONE, TMP, git, gh_calls, run_wave, sandbox, wave

P = TMP / "p"  # the unit fixture: one worktree per seat verdict
Z = TMP / "z"  # the subprocess fixture: a wave repo the line reads through the CLI
G = TMP / "g-plain"  # a git repo with no wave state: the line degrades to empty
I = TMP / "i-init"  # the `wave init --yes` fixture

NOW = time.time()


def ago(minutes: float) -> str:
    return datetime.fromtimestamp(NOW - minutes * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age(path: Path, minutes: float) -> None:
    t = NOW - minutes * 60
    os.utime(path, (t, t))


def seed(wt: Path, minutes: float, edit: tuple[str, float] | None = None, issue: int | None = None) -> None:
    """A dispatch stamp aged `minutes`, plus optionally one edited file — every trace's mtime laid at its age."""
    if issue is not None:
        stamp = wt / ".setwave.json"
        stamp.write_text(json.dumps({"issue": issue, "repo": "o/p", "dispatched_at": ago(minutes), "prompt": "p.md"}))
        age(stamp, minutes)
    if edit:
        f = wt / edit[0]
        f.write_text("work\n")
        age(f, edit[1])


def worktree(repo: Path, n: int, branch: str) -> Path:
    wt = TMP / f"{repo.name}-{n}"
    git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=repo)
    return wt


def fresh_repo(slug_name: str, wave_json: bool) -> Path:
    d = TMP / slug_name
    if d.exists():
        return d
    bare = TMP / "remote" / "o" / f"{slug_name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(d), cwd=TMP)
    if wave_json:
        (d / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true"]}))
    (d / "README.md").write_text("fixture\n")
    git("add", ".", cwd=d)
    git("commit", "-q", "-m", "base", cwd=d)
    git("push", "-q", "origin", "main", cwd=d)
    return d


def setUpModule():
    global NOW
    NOW = time.time()
    sandbox()
    if P.exists():
        return
    fresh_repo("p", True)
    fresh_repo("z", True)
    fresh_repo("i-init", False)
    (I / "Makefile").write_text("test:\n\techo test\nlint:\n\techo lint\n")
    git("add", ".", cwd=I)
    git("commit", "-q", "-m", "a Makefile, for the init proposal", cwd=I)
    git("push", "-q", "origin", "main", cwd=I)
    seed(worktree(P, 2, "feat/2-p"), 90, edit=("w2.txt", 2), issue=2)  # working: the newest edit is 2 min old
    seed(worktree(P, 3, "feat/3-p"), 20, edit=("w3.txt", 20), issue=3)  # stalled: quiet, no process, <= 30
    seed(worktree(P, 4, "feat/4-p"), 45, edit=("w4.txt", 45), issue=4)  # dead: nothing for 45 min
    seed(worktree(P, 5, "feat/5-p"), 90, issue=5)  # done: the PR cache says its branch carries PR 20
    worktree(P, 6, "feat/6-p")  # empty: no stamp, no commits, no dirt — nobody's seat


def line(subprocess_run: subprocess.CompletedProcess) -> str:
    return subprocess_run.stdout.rstrip("\n")


class LinePaths(unittest.TestCase):
    """The three paths the contract pins, through the CLI: in a wave repo, outside any repo, and a wave repo
    with no seats — plus a plain git repo. Exit 0 always, at most one line, and gh never called."""

    def setUp(self):
        sandbox()
        global NOW
        NOW = time.time()
        if wave.PR_CACHE.exists():
            wave.PR_CACHE.unlink()
        fresh_repo("z", True)

    def test_outside_any_repo_the_line_is_empty_and_exit_0(self):
        d = TMP / "not-a-repo"
        d.mkdir(exist_ok=True)
        p = run_wave("agents", "--line", cwd=d)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout, "\n", "an empty line, no error text")
        self.assertEqual(p.stderr, "")

    def test_a_git_repo_with_no_wave_state_degrades_to_the_empty_line(self):
        fresh_repo("g-plain", False)
        p = run_wave("agents", "--line", cwd=G)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout, "\n")
        self.assertNotIn("o/g-plain", wave.load_registry()["repos"], "a statusline render never registers the repo")

    def test_in_a_wave_repo_it_is_one_line_under_120_chars(self):
        wt = worktree(Z, 2, "feat/2-z")
        (wt / "fresh.txt").write_text("just dispatched\n")
        before = len(gh_calls())
        p = run_wave("agents", "--line", cwd=Z)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(p.stdout.strip().splitlines()), 1, p.stdout)
        self.assertTrue(line(p).startswith("setwave: "), p.stdout)
        self.assertLess(len(line(p)), 120, p.stdout)
        self.assertIn("1 working", line(p))
        self.assertEqual(len(gh_calls()), before, "the line never calls gh")

    def test_a_wave_repo_with_no_seats_prints_the_zero_line(self):
        p = run_wave("agents", "--line", cwd=Z)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(line(p), "setwave: 0 working · 0 stalled · 0 done · 0 dead")

    def test_done_comes_from_the_local_cache_not_from_gh(self):
        seed(worktree(Z, 9, "feat/9-z"), 60, issue=9)  # stale past DEAD_MIN: dead locally...
        wave.PR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        wave.PR_CACHE.write_text(json.dumps({"o/z": {"feat/9-z": {"number": 99, "state": "OPEN"}}}))
        before = len(gh_calls())
        p = run_wave("agents", "--line", cwd=Z)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("1 done(#99)", line(p))
        self.assertIn("0 dead", line(p))
        self.assertEqual(len(gh_calls()), before, "done is read from the cache, gh is never called")


class Buckets(unittest.TestCase):
    """The seat verdicts behind the counts, asked directly: PR done first, then busy or fresh is working,
    quiet is stalled, past DEAD_MIN with no PR is dead; an empty seat is nobody's agent; any failure is
    the empty line, never an exception."""

    def setUp(self):
        if wave.PR_CACHE.exists():
            wave.PR_CACHE.unlink()

    def tearDown(self):
        if wave.PR_CACHE.exists():
            wave.PR_CACHE.unlink()

    def seated(self, probe: list[str]) -> str:
        with mock.patch.object(wave, "worktree_processes", return_value=probe):
            return wave.seat_line(P, now=NOW)

    def test_every_verdict_is_counted_and_the_empty_seat_is_skipped(self):
        line = self.seated([])
        self.assertIn("1 working", line, "feat/2-p, edited 2 min ago")
        self.assertIn("1 stalled", line, "feat/3-p, quiet 20 min")
        self.assertIn("2 dead", line, "feat/4-p (45 min) and feat/5-p (90 min): silent past DEAD_MIN, no cache")
        self.assertIn("0 done", line, "no cache entry in this test")
        self.assertNotIn("5", line.split("setwave:")[1].split("working")[0], "feat/6-p is empty: nobody's agent")

    def test_a_cached_pr_makes_the_seat_done_with_its_number(self):
        wave.PR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        wave.PR_CACHE.write_text(json.dumps({"o/p": {"feat/5-p": {"number": 20, "state": "OPEN"}}}))
        line = self.seated([])
        self.assertIn("1 done(#20)", line)
        self.assertNotIn("2 dead", line, "feat/5-p (90 min silent) is done, not dead: the PR beats the verdict")
        self.assertIn("1 dead", line, "feat/4-p has no PR: it stays dead")

    def test_a_live_process_keeps_an_old_seat_working(self):
        line = self.seated(["claude --worktree p-3"])  # a probe hit: every quiet seat reads as busy -> working
        self.assertIn("4 working", line, "busy beats idle, as in worktree_state: every stamped seat joins p-2")
        self.assertIn("0 stalled", line)
        self.assertIn("0 dead", line)

    def test_several_done_prs_are_listed_and_the_line_stays_under_120(self):
        wave.PR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        wave.PR_CACHE.write_text(json.dumps({"o/p": {"feat/5-p": {"number": 20, "state": "OPEN"},
                                                     "feat/4-p": {"number": 44, "state": "MERGED"}}}))
        line = self.seated([])
        self.assertLess(len(line), 120, line)
        self.assertIn("done(#20,#44)", line)
        wave.PR_CACHE.unlink()

    def test_the_line_never_passes_120_even_with_many_long_pr_numbers(self):
        numbers = [987654321 + i for i in range(12)]  # twelve done seats with nine-digit PRs: the named form bursts 120
        wts = []
        try:
            for n in numbers:
                wt = worktree(P, n, f"feat/{n}-p")
                seed(wt, 60, issue=n)
                wts.append(wt)
            wave.PR_CACHE.parent.mkdir(parents=True, exist_ok=True)
            cache = {f"feat/{n}-p": {"number": n, "state": "OPEN"} for n in numbers}  # the comprehension is the inner dict
            wave.PR_CACHE.write_text(json.dumps({"o/p": cache}))
            line = self.seated([])
            self.assertLess(len(line), 120, line)
            self.assertIn("12 done ·", line, "the named form that would pass 120 falls back to the count")
            self.assertNotIn("done(", line)
        finally:
            if wave.PR_CACHE.exists():
                wave.PR_CACHE.unlink()
            for wt in wts:
                subprocess.run(["git", "-C", str(P), "worktree", "remove", "--force", str(wt)],
                               capture_output=True, text=True)
            for n in numbers:  # the branch goes too, or a later dispatch in the shared sandbox clone trips over it
                subprocess.run(["git", "-C", str(P), "branch", "-D", f"feat/{n}-p"],
                               capture_output=True, text=True)

    def test_any_failure_is_the_empty_line_never_a_crash(self):
        with mock.patch.object(wave, "load_registry", side_effect=RuntimeError("boom")):
            self.assertEqual(wave.seat_line(P), "")

    def test_outside_a_git_repo_the_line_is_empty(self):
        self.assertEqual(wave.seat_line(TMP / "not-a-repo"), "")

    def test_a_failed_git_common_dir_probe_is_the_empty_line_never_a_crash(self):
        """`git rev-parse --git-common-dir` failing raises SystemExit through `sh` — BaseException, past
        an `except Exception` guard: the statusline contract (exit 0 always) must survive even it."""
        def boom(cmd, cwd=None, env=None):
            if cmd == ["git", "rev-parse", "--git-common-dir"]:
                raise SystemExit("$ git rev-parse --git-common-dir\nfatal: not a git repository")
            return ""
        with mock.patch.object(wave, "sh", side_effect=boom):
            line = wave.seat_line(P)  # the probe fails open (the toplevel stands in): never a raise, whatever it renders
        self.assertIsInstance(line, str, "SystemExit escaped the catch-all: the statusline would crash")


class Cache(unittest.TestCase):
    """The one write that keeps the line's done bucket honest: every successful `pr_states()` refreshes
    the local cache --line reads; a failed read writes nothing."""

    def setUp(self):
        sandbox()
        self._registry = (TMP / "config" / "repos.json").read_text()
        if wave.PR_CACHE.exists():
            wave.PR_CACHE.unlink()

    def tearDown(self):
        (TMP / "config" / "repos.json").write_text(self._registry)
        wave.Repo._cache.pop("o/p", None)
        if wave.PR_CACHE.exists():
            wave.PR_CACHE.unlink()

    def test_pr_states_refreshes_the_cache_and_a_fresh_process_reads_it(self):
        reg = wave.load_registry()
        reg["repos"]["o/p"] = {"path": str(P)}
        wave.save_registry(reg)
        states = wave.Repo.get("o/p").pr_states()
        self.assertEqual(states.get("feat/5-p", {}).get("number"), 20)
        cache = json.loads(wave.PR_CACHE.read_text())
        self.assertEqual(cache["o/p"]["feat/5-p"], {"number": 20, "state": "OPEN"})
        with mock.patch.object(wave, "worktree_processes", return_value=[]):
            self.assertIn("1 done(#20)", wave.seat_line(P, now=time.time()))

    def test_a_failed_pr_read_writes_nothing(self):
        reg = wave.load_registry()
        reg["repos"]["o/p"] = {"path": str(P)}
        wave.save_registry(reg)
        with mock.patch.object(wave.Repo, "gh_ok", return_value=(False, "")):
            self.assertIsNone(wave.Repo.get("o/p").pr_states())
        self.assertFalse(wave.PR_CACHE.exists())


class Prints(unittest.TestCase):
    """dispatch prints the segment next to the watchdog and the wave-<N> naming; init --yes and hooks
    install offer the snippet (one consent, chained, never replacing)."""

    def setUp(self):
        sandbox()
        self._registry = (TMP / "config" / "repos.json").read_text()

    def tearDown(self):
        (TMP / "config" / "repos.json").write_text(self._registry)
        wt = TMP / "r-2"
        if wt.exists():  # the branch goes too, or a later dispatch in the shared sandbox clone trips over it
            head = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "-C", str(TMP / "r"), "worktree", "remove", "--force", str(wt)],
                           capture_output=True, text=True)
            if head and head != "HEAD":
                subprocess.run(["git", "-C", str(TMP / "r"), "branch", "-D", head], capture_output=True, text=True)
        shutil.rmtree(wt, ignore_errors=True)
        prompt = TMP / "r-handoffs" / "2.md"
        if prompt.exists():
            prompt.unlink()
        settings = CLONE / ".claude" / "settings.json"
        if settings.exists():
            settings.unlink()

    def test_dispatch_prints_the_segment_the_naming_and_the_readme(self):
        p = run_wave("dispatch", "2", "--force", "--no-prepare")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("agents --line", p.stdout, "the statusline segment, next to the watchdog")
        self.assertIn("statusline/README.md", p.stdout, "where the exact snippet ships")
        self.assertIn("wave-<N>", p.stdout, "the spawn naming mandate")
        self.assertIn("named agents", p.stdout, "why: the host renders native rows for named agents only")

    def test_init_yes_offers_the_segment(self):
        reg = wave.load_registry()
        reg["repos"]["o/i"] = {"path": str(I)}
        wave.save_registry(reg)
        p = run_wave("init", "--yes", cwd=I)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("statusline", p.stdout.lower())
        self.assertIn("statusline/README.md", p.stdout)
        self.assertIn("never", p.stdout, "the offer says it chains, never replaces")

    def test_hooks_install_offers_the_segment(self):
        p = run_wave("hooks", "install")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("statusline/README.md", p.stdout)


class Docs(unittest.TestCase):
    """The pins that fail if the mandate, the snippet or the guarantee leaves."""

    REPO = wave.HERE.parent

    def test_the_skill_mandates_the_chat_seat_table_at_every_agent_event(self):
        skill = (self.REPO / "skills" / "wave" / "SKILL.md").read_text()
        self.assertIn("seat table", skill)
        self.assertIn("every agent event", skill)
        for event in ("dispatch", "milestone", "PR open", "nudge", "stall"):
            self.assertIn(event, skill, f"the seat table is mandated at {event} too")

    def test_the_skill_mandates_naming_every_spawn_wave_n(self):
        skill = (self.REPO / "skills" / "wave" / "SKILL.md").read_text()
        self.assertIn("wave-<N>", skill)
        self.assertIn("named", skill, "the reason: the host renders native rows for named agents only")

    def test_the_statusline_readme_ships_the_chaining_snippet(self):
        readme = (self.REPO / "statusline" / "README.md").read_text()
        self.assertIn("statusLine", readme, "the exact settings key")
        self.assertIn("agents --line", readme, "the exact command the snippet runs")
        self.assertIn("never replaces", readme, "the user keeps their own statusline")
        self.assertIn("remove", readme.lower(), "the removal note")
        self.assertIn("empty", readme.lower(), "the degradation outside a wave repo")

    def test_the_statusline_readme_carries_the_per_render_cost(self):
        readme = (self.REPO / "statusline" / "README.md").read_text()
        flat = " ".join(readme.lower().split())  # the pins are content pins, not line-wrap pins
        self.assertIn("git worktree list", readme, "what one render runs")
        self.assertIn("per seat", flat, "the local probes of one render")
        self.assertIn("never spawns", flat, "no heavy spawn per render")
        cost = flat.split("what one render costs", 1)[1]
        self.assertIn("pr cache", cost, "the cost clause lists what seat_line actually reads")
        self.assertIn(".wave.json", cost, "the line reads the repo config per render")
        self.assertIn("registry", cost, "the line reads the registry entry per render")
        self.assertNotIn("tokens", cost, "seat_line never reads the tokens file: that is stats' ledger, not the line's")

    def test_the_line_help_carries_the_precedence_note(self):
        p = run_wave("agents", "--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = " ".join(p.stdout.split())  # argparse wraps at a column that moves with the parser's options: pin the words, not the line breaks
        self.assertIn("--line", out)
        self.assertIn("wins over --check/--watch", out,
                      "the statusline segment ignores --check/--watch silently: the help says so")

    def test_the_guarantees_table_pins_the_seat_progress(self):
        row = next(r for r in wave.GUARANTEES if "agents --line" in r[0])
        self.assertTrue(row[3], "the promise is pinned by a test")
        self.assertIn("never gh", row[0], "the local-only rule is part of the promise")
        self.assertIn("wave-<N>", row[0], "the naming is part of the promise")


if __name__ == "__main__":
    unittest.main()
