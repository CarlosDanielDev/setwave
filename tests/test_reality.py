"""#134: the reality gate — a mechanical fake-done scan that runs before the judge spawns.

Port of claud-framework `backend/superframework/audit/reality_gate.py` (stdlib regex, zero network):
the six false-completion classes fire BLOCKER on what the PR delivers (added diff lines + the PR
body) and MAJOR on pre-existing context lines only — MAJOR never refuses, it rides the judge prompt
as context. `judge_check` re-runs the scan on the certified patch at check time and refuses with
JUDGE-REALITY-BLOCKER. Every trigger string below is built through `mark(...)`, split, so this file
never trips the gate it tests on the very diff that ships it.
"""
import hashlib
import json
import unittest

from helpers import CLONE, TMP, gh_calls, git, judge_pass, run_wave, sandbox, wave

HANDOFFS = TMP / "r-handoffs"


def mark(*parts: str) -> str:
    """Join the parts: every trigger below is split so the diff that adds this file never scans BLOCKER."""
    return "".join(parts)


TRIGGERS = {
    "lorem_ipsum": mark("lo", "rem ipsum dolor sit amet"),
    "todo_fixme": mark("TO", "DO: wire the retry"),
    "placeholder_text": mark("use the place", "holder here"),
    "invented_number": mark("reaches XX", "% of users"),
    "simulated_integration": mark("a simulated integrat", "ion with the gateway"),
    "truncation": mark("the rest of", " the code goes here"),
}


def patch_with(added: str = "", context: str = "", removed: str = "") -> str:
    """A minimal one-file diff with the given added / context / removed line, in that order."""
    lines = ["diff --git a/f.txt b/f.txt", "index 0000000..1111111 100644", "--- a/f.txt", "+++ b/f.txt"]
    lines += [" " + context] if context else []
    lines += ["-" + removed] if removed else []
    lines += ["+" + added] if added else []
    return "\n".join(lines) + "\n"


def head_sha(branch: str = "feat/4-x") -> str:
    return wave.Repo.get("o/r").git(["rev-parse", f"origin/{branch}"]).strip()


_PUSHED = False


def push_fake_done() -> None:
    """One commit on feat/4-x whose added lines carry a marker, so PR 20's whole diff scans BLOCKER.

    Runs once per test run; test_judge's own flows ran before this file (alphabetical discovery
    order), and no test after it judges PR 20 again.
    """
    global _PUSHED
    if _PUSHED:
        return
    sandbox()
    git("checkout", "-q", "feat/4-x")
    (CLONE / "fd.txt").write_text(mark("TO", "DO: wire the retry\n") + "still broken\n")
    git("add", "fd.txt")
    git("commit", "-q", "-m", "feat/4-x: fd.txt")
    git("push", "-q", "origin", "feat/4-x")
    git("checkout", "-q", "main")
    _PUSHED = True


class Pure(unittest.TestCase):
    def test_every_pattern_class_fires_as_a_blocker_in_added_lines(self):
        for name, trigger in TRIGGERS.items():
            with self.subTest(pattern=name):
                r = wave.judge_reality(patch_with(added=trigger))
                self.assertEqual(r["grade"], "BLOCKER")
                self.assertEqual([f["pattern"] for f in r["blockers"]], [name])
                self.assertEqual(r["blockers"][0]["severity"], "BLOCKER")

    def test_a_clean_diff_and_a_clean_body_pass(self):
        self.assertEqual(wave.judge_reality(patch_with(added="a plain line\n")),
                         {"grade": "clean", "blockers": [], "majors": []})

    def test_a_match_in_context_lines_is_major_and_never_a_blocker(self):
        r = wave.judge_reality(patch_with(context=TRIGGERS["truncation"]))
        self.assertEqual(r["grade"], "MAJOR")
        self.assertEqual(r["blockers"], [])
        self.assertEqual(r["majors"][0]["pattern"], "truncation")
        self.assertEqual(r["majors"][0]["severity"], "MAJOR")

    def test_a_match_in_the_pr_body_is_blocker(self):
        r = wave.judge_reality(patch_with(added="ok"), body=TRIGGERS["invented_number"])
        self.assertEqual(r["grade"], "BLOCKER")
        self.assertEqual(r["blockers"][0]["pattern"], "invented_number")

    def test_a_removed_marker_is_a_fix_not_fake_done(self):
        self.assertEqual(wave.judge_reality(patch_with(removed=TRIGGERS["todo_fixme"]))["grade"], "clean")

    def test_a_blocker_swallows_its_own_major_echo(self):
        trigger = TRIGGERS["lorem_ipsum"]
        r = wave.judge_reality(patch_with(added=trigger, context=trigger))
        self.assertEqual(r["grade"], "BLOCKER")
        self.assertEqual(r["majors"], [], "the same class is not reported twice")

    def test_the_grades_are_the_enum_the_verdict_echoes(self):
        self.assertEqual(wave.REALITY_GRADES, ("clean", "MAJOR", "BLOCKER"))


class Schema(unittest.TestCase):
    good = {"verdict": "pass", "head_sha": "0" * 40, "findings": [], "reality": "clean"}

    def test_reality_is_required_and_typed(self):
        self.assertEqual(wave.verdict_problems(dict(self.good)), [])
        for bad in ("meh", "blocker", "", None):
            with self.subTest(reality=bad):
                body = {**self.good}
                if bad is None:
                    del body["reality"]
                else:
                    body["reality"] = bad
                self.assertTrue(any("reality" in p for p in wave.verdict_problems(body)), bad)


class JudgeCheck(unittest.TestCase):
    """judge_check re-runs the scan itself, on the certified patch, where it cannot be skipped."""

    def tearDown(self):
        for f in ("judge-20.json", "judge-20.patch", "judge-20.patch.sha"):
            (HANDOFFS / f).unlink(missing_ok=True)

    def seed(self, patch_text: str, reality_grade: str = "MAJOR") -> dict:
        repo = wave.Repo.get("o/r")
        sha = head_sha()
        HANDOFFS.mkdir(parents=True, exist_ok=True)
        patch = HANDOFFS / "judge-20.patch"
        patch.write_text(patch_text)
        (HANDOFFS / "judge-20.patch.sha").write_text(json.dumps(
            {"head": sha, "patch_sha": hashlib.sha256(patch.read_bytes()).hexdigest()}))
        (HANDOFFS / "judge-20.json").write_text(json.dumps(
            {"verdict": "pass", "head_sha": sha, "findings": [], "reality": reality_grade}))
        return wave.judge_check(repo, 20, sha)

    def test_a_major_grade_in_context_lines_never_refuses(self):
        jc = self.seed(patch_with(context=TRIGGERS["todo_fixme"]))
        self.assertIsNone(jc["flag"], jc)

    def test_a_blocker_in_the_certified_patch_refuses_by_name(self):
        jc = self.seed(patch_with(added=TRIGGERS["lorem_ipsum"]))
        self.assertEqual(jc["flag"], "JUDGE-REALITY-BLOCKER", jc)
        self.assertIn("lorem_ipsum", jc["detail"])

    def test_no_certified_patch_skips_the_scan(self):
        repo = wave.Repo.get("o/r")
        sha = head_sha()
        HANDOFFS.mkdir(parents=True, exist_ok=True)
        (HANDOFFS / "judge-20.json").write_text(json.dumps(
            {"verdict": "pass", "head_sha": sha, "findings": [], "reality": "clean"}))
        jc = wave.judge_check(repo, 20, sha)
        self.assertIsNone(jc["flag"], jc)


class JudgeCmd(unittest.TestCase):
    def test_a_clean_patch_prints_the_scan_and_the_verdict_must_echo_it(self):
        p = run_wave("judge", "21")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)  # nothing has been judged yet
        self.assertIn("reality scan: clean", p.stdout)
        self.assertIn('"reality"', p.stdout, "the verdict the judge is asked for carries the scan grade")
        self.assertNotIn("JUDGE-REALITY-BLOCKER[", p.stdout)


class Prompt(unittest.TestCase):
    def test_the_round_prompt_carries_the_scan_grade_and_the_context_matches(self):
        delta = TMP / "fake-delta.patch"
        delta.write_text(patch_with(context=TRIGGERS["placeholder_text"]))
        try:
            prompt = wave.render_round_prompt(wave.Repo.get("o/r"), 20, 4,
                                              {"head_sha": head_sha(), "findings": []}, head_sha(), delta)
        finally:
            delta.unlink()
        self.assertIn("MAJOR", prompt)
        self.assertIn("placeholder_text", prompt, "the MAJOR context matches ride the prompt")


class FakeDone(unittest.TestCase):
    """End to end: a diff carrying a marker is refused before any judge agent is spawned, and again
    at merge time on the certified patch — whatever a seeded pass verdict says."""

    def tearDown(self):
        (HANDOFFS / "judge-20.json").unlink(missing_ok=True)

    def test_judge_refuses_before_spawning(self):
        push_fake_done()
        p = run_wave("judge", "20")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("reality scan: BLOCKER", p.stdout)
        self.assertIn("todo_fixme", p.stdout)
        self.assertIn("JUDGE-REALITY-BLOCKER", p.stdout)
        self.assertNotIn("no valid verdict yet", p.stdout, "no judge agent is spawned on a BLOCKER scan")

    def test_merge_refuses_on_the_certified_patch_even_with_a_pass_verdict(self):
        push_fake_done()
        judge_pass(20)  # a pass at the pushed head, reality clean — the kernel's own scan still refuses
        before = len(gh_calls())
        p = run_wave("merge", "20", "--yes")
        merges = [c for c in gh_calls()[before:] if c[:2] == ["pr", "merge"]]
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("JUDGE-REALITY-BLOCKER", p.stdout + p.stderr)
        self.assertEqual(merges, [], "nothing merged")


if __name__ == "__main__":
    unittest.main()
