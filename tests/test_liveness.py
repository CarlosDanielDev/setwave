"""Agent liveness: a running agent told from a dead one by what its work leaves on disk.

A third repo, o/l, gets one worktree per verdict. Nothing is inspected but files and git, and the
process probe (`wave.worktree_processes`) is injected: every mtime and commit date is set by the test,
so no real process of the machine is looked at.
  l-9   likely dead: dispatched 90 min ago, newest edit 45 min ago, no PR
  l-10  working:     newest edit 2 min ago
  l-11  stalled:     newest edit 20 min ago, probe injected as no process
  l-12  likely dead: no stamp, one commit 50 min ago
  l-4   done:        PR 20 closes #4
"""
import contextlib
import io
import json
import os
import subprocess
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from helpers import TMP, git, run_wave, sandbox, wave

L = TMP / "l"


NOW = time.time()  # the fixtures' "now", reset by settle(); liveness is asked against it, so the wall clock cannot tick a minute over
STAMPED = {9: 90, 10: 90, 11: 60, 4: 90}  # worktree -> minutes since its dispatch stamp
EDITED = {9: ("edit.txt", 45), 10: ("busy.txt", 2), 11: ("slow.txt", 20), 12: ("c.txt", 50)}  # newest edit
COMMITTED = (12, 50)  # worktree, minutes since its one commit


def ago(minutes: float, now: float | None = None) -> float:
    return (time.time() if now is None else now) - minutes * 60


def age(path, minutes: float, now: float | None = None) -> None:
    t = ago(minutes, now)
    os.utime(path, (t, t))


def iso(minutes: float, fmt: str) -> str:
    return datetime.fromtimestamp(ago(minutes, NOW), timezone.utc).strftime(fmt)


def settle() -> None:
    """Lay every fixture's trace at its age from `NOW`, taken here."""
    global NOW
    NOW = time.time()
    for n, minutes in STAMPED.items():
        stamp = TMP / f"l-{n}" / ".setwave.json"
        stamp.write_text(json.dumps({"issue": n, "repo": "o/l", "dispatched_at": iso(minutes, "%Y-%m-%dT%H:%M:%SZ"), "prompt": "p.md"}))
        age(stamp, minutes, NOW)
    n, minutes = COMMITTED
    at = iso(minutes, "%Y-%m-%dT%H:%M:%S+0000")
    subprocess.run(["git", "commit", "-q", "--amend", "--no-edit", "--date", at], cwd=TMP / f"l-{n}", check=True,
                   capture_output=True, env={**os.environ, "GIT_COMMITTER_DATE": at})
    for n, (name, minutes) in EDITED.items():
        age(TMP / f"l-{n}" / name, minutes, NOW)


def worktree(n: int, branch: str):
    wt = TMP / f"l-{n}"
    git("worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=L)
    return wt


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
    for n, branch in ((9, "feat/9-dead"), (10, "feat/10-busy"), (11, "feat/11-quiet"), (12, "feat/12-committed"), (4, "feat/4-l")):
        wt = worktree(n, branch)
        if n in EDITED:
            (wt / EDITED[n][0]).write_text("work\n" if n != 12 else "c\n")
    git("add", "c.txt", cwd=TMP / "l-12")
    git("commit", "-q", "-m", "c", cwd=TMP / "l-12")
    settle()


def wave_at_now(*args, cwd) -> SimpleNamespace:
    """`wave <args>` run in-process with the clock pinned to NOW, so the printed minutes cannot drift with the host's
    speed. (The subprocess `run_wave` reads the wall clock; the minutes it prints are 45 + however long the run took.)"""
    out, err, code = io.StringIO(), io.StringIO(), 0
    here = os.getcwd()
    os.chdir(cwd)
    try:
        with mock.patch.object(wave.time, "time", return_value=NOW), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                wave.main(list(args))
            except SystemExit as e:
                code = 0 if e.code is None else e.code if isinstance(e.code, int) else 1
    finally:
        os.chdir(here)
    return SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())


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
        got = {n: wave.liveness(repo(), n, now=NOW) for n in (9, 10, 11, 12, 4)}
        self.assertEqual({n: lv["verdict"] for n, lv in got.items()},
                         {9: "likely dead", 10: "working", 11: "quiet", 12: "likely dead", 4: "done"})
        self.assertEqual((got[9]["idle_min"], got[9]["age_min"]), (45, 90), "idle = newest edit, age = the stamp")
        self.assertEqual((got[12]["idle_min"], got[12]["age_min"]), (50, None), "a commit on the branch counts; no stamp, no age")
        self.assertEqual(got[4]["pr"], 20)

    def test_the_clock_is_the_one_passed(self):
        later = {n: wave.liveness(repo(), n, now=NOW + 9 * 60) for n in (10, 11)}
        self.assertEqual((later[10]["idle_min"], later[10]["verdict"]), (11, "quiet"), "2 min + 9 on the passed clock: working before")
        self.assertEqual((later[11]["idle_min"], later[11]["verdict"]), (29, "quiet"), "clear of the 30 min edge")


class Agents(unittest.TestCase):
    def test_every_worktree_is_listed_with_its_verdict(self):
        with mock.patch.object(wave, "worktree_processes", return_value=[]):
            p = wave_at_now("agents", cwd=L)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rows = {int(l.split()[0].split("#")[1]): l for l in p.stdout.splitlines() if l.startswith("  o/l#")}
        self.assertTrue({4, 9, 10, 11, 12} <= set(rows), p.stdout)
        for n, v in {9: "likely dead", 10: "working", 11: "stalled", 12: "likely dead", 4: "done"}.items():
            with self.subTest(n=n):
                self.assertTrue(rows[n].rstrip().endswith(v), rows[n])
        self.assertIn("dispatched 90 min ago", rows[9])
        self.assertIn("changed 45 min ago", rows[9])
        self.assertIn("PR #20", rows[4])

    def test_inside_a_worktree_it_lists_the_siblings_too(self):
        with mock.patch.object(wave, "worktree_processes", return_value=[]):
            p = wave_at_now("agents", cwd=TMP / "l-9")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = {int(l.split()[0].split("#")[1]) for l in p.stdout.splitlines() if l.startswith("  o/l#")}
        self.assertTrue({4, 9, 10, 11, 12} <= rows, p.stdout)


class Doctor(unittest.TestCase):
    def test_likely_dead_agents_are_named_with_the_recovery(self):
        with mock.patch.object(wave, "worktree_processes", return_value=[]):
            p = wave_at_now("doctor", cwd=L)
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
        self.assertIn("l-11 stalled", alive)
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
        self.assertIn("wave agents --watch", p.stdout, "dispatch ends by printing the stall watchdog to arm")
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




class Stalled(unittest.TestCase):
    """`worktree_state` reads the process probe injected here: quiet with no process is `stalled` — a
    signal at WORKING_MIN, before DEAD_MIN — and busy is `working` however long the gate runs without
    touching a tracked file."""

    def test_no_process_is_stalled_before_dead_min(self):
        with mock.patch.object(wave, "worktree_processes", return_value=[]):
            at_25 = wave.worktree_state(repo(), TMP / "l-11", "feat/11-quiet", 11, now=NOW + 5 * 60)
            at_20 = wave.worktree_state(repo(), TMP / "l-11", "feat/11-quiet", 11, now=NOW)
        self.assertFalse(at_20["busy"])
        self.assertEqual(at_20["progress"], "stalled")
        self.assertEqual(at_25["progress"], "stalled", "idle 25 min, no process: already a signal, still before DEAD_MIN")

    def test_a_live_gate_is_working_without_file_changes(self):
        with mock.patch.object(wave, "worktree_processes", side_effect=lambda wt: [f"xcodebuild {wt}"]):
            rec = wave.worktree_state(repo(), TMP / "l-11", "feat/11-quiet", 11, now=NOW + 5 * 60)
        self.assertTrue(rec["busy"])
        self.assertEqual(rec["progress"], "working")

    def test_a_merged_pr_is_done_never_stalled(self):
        with mock.patch.object(wave, "worktree_processes", return_value=[]), \
             mock.patch.object(wave.Repo, "pr_of", return_value=(True, {"number": 21, "state": "MERGED"})):
            rec = wave.worktree_state(repo(), TMP / "l-11", "feat/11-quiet", 11, now=NOW)
        self.assertEqual(rec["pr_state"], "MERGED")
        self.assertEqual(rec["progress"], "done", "merged work is done however idle: never a stall signal")
        self.assertEqual(wave.agent_verdict(rec), "done")

    def test_a_failed_probe_falls_back_to_the_files_verdict(self):
        def boom(wt):
            raise OSError("ps exploded")
        with mock.patch.object(wave, "worktree_processes", side_effect=boom):
            rec = wave.worktree_state(repo(), TMP / "l-11", "feat/11-quiet", 11, now=NOW)
        self.assertFalse(rec["busy"])
        self.assertEqual(rec["progress"], "stalled")


class Check(unittest.TestCase):
    def test_stalled_and_likely_dead_exit_1_naming_the_recovery(self):
        with mock.patch.object(wave, "worktree_processes", return_value=[]):
            p = wave_at_now("agents", "--check", cwd=L)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("stalled", p.stdout)
        self.assertIn('SendMessage <agentId> "run the gate in foreground', p.stdout)
        self.assertIn("likely dead", p.stdout)
        self.assertIn("--force --respawn", p.stdout)

    def test_everyone_busy_exits_0(self):
        with mock.patch.object(wave, "worktree_processes", side_effect=lambda wt: [f"gate {wt}"]):
            p = wave_at_now("agents", "--check", cwd=L)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("no stalled or likely dead agent", p.stdout)


class WatchStopped(Exception):
    pass


class Watch(unittest.TestCase):
    def watch(self, *args, probe, clock, sleep) -> tuple[str, int | None]:
        """`agents --watch` in-process with the clock, the sleep and the probe injected."""
        out, code = io.StringIO(), None
        here = os.getcwd()
        os.chdir(L)
        try:
            with mock.patch.object(wave.time, "time", side_effect=lambda: clock["now"]), \
                 mock.patch.object(wave.time, "sleep", side_effect=sleep), \
                 mock.patch.object(wave, "worktree_processes", side_effect=probe), \
                 contextlib.redirect_stdout(out):
                try:
                    wave.main(["agents", "--watch", *args])
                except SystemExit as e:
                    code = 0 if e.code is None else e.code
        finally:
            os.chdir(here)
        return out.getvalue(), code

    def test_watch_blocks_until_a_worktree_stalls_then_exits_1(self):
        clock = {"now": NOW}

        def tick(seconds):
            clock["now"] += seconds

        def probe(wt):
            return [f"xcodebuild {wt}"] if wt.name in ("l-9", "l-11", "l-12") else []  # only l-10 is left to stall

        out, code = self.watch("--every", "5m", probe=probe, clock=clock, sleep=tick)
        self.assertEqual(code, 1, out)
        self.assertIn("o/l#10", out)
        self.assertIn("stalled", out)
        self.assertGreaterEqual(clock["now"] - NOW, 10 * 60, "it polled on the injected clock until the stall")

    def test_watch_with_everyone_busy_keeps_polling_on_the_default_interval(self):
        clock = {"now": NOW}
        ticks = []

        def stop(seconds):
            ticks.append(seconds)
            if len(ticks) == 3:
                raise WatchStopped  # how the harness kills an armed watchdog; the test stops it here
            clock["now"] += seconds

        with self.assertRaises(WatchStopped):
            self.watch(probe=lambda wt: [f"gate {wt}"], clock=clock, sleep=stop)
        self.assertEqual(ticks, [300, 300, 300], "the default --every is 5m")

    def test_every_zero_never_spins_the_watchdog(self):
        for bad in ("0", "0m", "0s", "0h", "00", "000", "00s"):
            with self.assertRaises(SystemExit):
                wave.every_seconds(bad)

    def test_watch_survives_a_transient_failure_and_keeps_polling(self):
        clock = {"now": NOW}
        polls = {"n": 0}

        def tick(seconds):
            clock["now"] += seconds

        def flaky(wt):
            polls["n"] += 1
            if polls["n"] == 2:
                raise SystemExit("git index.lock: unable to lock")  # what `sh()` raises on a bounced git call
            return [f"gate {wt}"] if wt.name in ("l-9", "l-11", "l-12") else []

        out, code = self.watch("--every", "5m", probe=flaky, clock=clock, sleep=tick)
        self.assertEqual(code, 1, "a transient failure keeps the watchdog alive; only a stall ends it with 1")
        self.assertIn("probe failed, still watching", out, "a transient failure is logged, never silent")
        self.assertIn("git index.lock", out)
        self.assertIn("o/l#10", out, "the watchdog still fires for the real stall")


class ProcessProbe(unittest.TestCase):
    """The probe matches the worktree path on a path boundary: a sibling whose number merely extends the
    digits (`l-9` inside `l-94`) is another agent's worktree, never this one's process."""

    def test_a_digit_prefix_sibling_is_not_this_worktrees_process(self):
        ps = (f"xcodebuild -project {TMP}/l-94 -destination x\n"
              f"/usr/bin/make -C {TMP}/l-9\n"
              f"tail -f {TMP}/l-9/build.log\n"
              f"tail -f {TMP}/l-9x/build.log\n")
        with mock.patch.object(wave, "run", return_value=(0, ps, "")):
            got = wave.worktree_processes(TMP / "l-9")
        self.assertIn(f"/usr/bin/make -C {TMP}/l-9", got, "the worktree root itself matches")
        self.assertIn(f"tail -f {TMP}/l-9/build.log", got, "a child path matches")
        self.assertNotIn(f"xcodebuild -project {TMP}/l-94 -destination x", got,
                         "l-94 is a sibling: a digit-prefix read would keep l-9 quiet forever")
        self.assertNotIn(f"tail -f {TMP}/l-9x/build.log", got)

    def test_the_lsof_cwd_respects_the_boundary_too(self):
        lsof = f"p42\0n{TMP}/l-94\0p7\0n{TMP}/l-9\0p9\0n{TMP}/l-9/build\0"
        with mock.patch.object(wave, "run", return_value=(0, lsof, "")):
            got = wave.worktree_processes(TMP / "l-9")
        self.assertIn(f"{TMP}/l-9", got)
        self.assertIn(f"{TMP}/l-9/build", got)
        self.assertNotIn(f"{TMP}/l-94", got)


if __name__ == "__main__":
    unittest.main()
