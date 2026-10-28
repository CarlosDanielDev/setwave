"""Cost brakes (#119): `dispatch --budget` (or `budget` in .wave.json) stamps the ceiling per issue, the
prompt carries it with the stop-at-80% rule, and `wave stats` reads the worktree's usage file as spend
vs ceiling. The usage file (`.setwave-tokens.jsonl`) is what the prompt tells the agent to append to;
it is excluded from git next to the dispatch stamp.

o/r#2 and o/r#7 are the dispatchable leaves test_commands.Dispatch uses; same tearDown contract:
the worktrees, prompts and usage files this class creates are removed again.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import TMP, run_wave, sandbox

REG = TMP / "config" / "repos.json"
TOKENS = ".setwave-tokens.jsonl"


class Budget(unittest.TestCase):
    def setUp(self):
        sandbox()
        self._registry = REG.read_text()

    def tearDown(self):
        REG.write_text(self._registry)
        for n in (2, 7):
            wt = TMP / f"r-{n}"
            if wt.exists():
                head = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                                      capture_output=True, text=True).stdout.strip()
                subprocess.run(["git", "-C", str(TMP / "r"), "worktree", "remove", "--force", str(wt)],
                               capture_output=True, text=True)
                if head and head != "HEAD":
                    subprocess.run(["git", "-C", str(TMP / "r"), "branch", "-D", head],
                                   capture_output=True, text=True)
            shutil.rmtree(wt, ignore_errors=True)
            prompt = TMP / "r-handoffs" / f"{n}.md"
            if prompt.exists():
                prompt.unlink()

    def declare(self, **entry):
        reg = json.loads(REG.read_text())
        reg["repos"]["o/r"] = {**reg["repos"]["o/r"], **entry}
        REG.write_text(json.dumps(reg))

    def stamp(self, n):
        return json.loads((TMP / f"r-{n}" / ".setwave.json").read_text())

    def dispatch(self, *extra, n=2):
        return run_wave("dispatch", str(n), "--force", "--no-prepare", *extra)

    def test_the_prompt_carries_the_ceiling_and_the_80_rule(self):
        p = self.dispatch("--budget", "200000")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        text = (TMP / "r-handoffs" / "2.md").read_text()
        self.assertIn("200000", text, "the ceiling itself is in the prompt")
        self.assertIn("160000", text, "so is the 80% line the rule is about")
        self.assertIn("pare e relate", text, "the rule is the issue's own words: over 80% -> stop and report")
        self.assertIn(TOKENS, text, "the prompt names the usage file `wave stats` reads")
        self.assertEqual(self.stamp(2)["budget"], 200000, "the stamp carries the ceiling")
        excl = subprocess.run(["git", "-C", str(TMP / "r-2"), "rev-parse", "--git-path", "info/exclude"],
                              capture_output=True, text=True)
        self.assertIn(f"/{TOKENS}", (TMP / "r-2" / excl.stdout.strip()).read_text(),
                      "the usage file is excluded from git next to the stamp")

    def test_the_wave_json_budget_is_the_default_and_the_flag_wins(self):
        self.declare(budget=100000)
        p = self.dispatch()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("100000", (TMP / "r-handoffs" / "2.md").read_text())
        self.assertEqual(self.stamp(2)["budget"], 100000)
        p = self.dispatch("--budget", "300000")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("300000", (TMP / "r-handoffs" / "2.md").read_text(), "--budget overrides .wave.json")
        self.assertEqual(self.stamp(2)["budget"], 300000)

    def test_without_a_budget_the_prompt_carries_no_ceiling(self):
        p = self.dispatch()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("Teto de custo", (TMP / "r-handoffs" / "2.md").read_text())
        self.assertNotIn("budget", self.stamp(2))

    def test_wave_prompt_uses_the_wave_json_budget_and_the_flag_wins(self):
        self.declare(budget=100000)
        p = run_wave("prompt", "2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Teto de custo", p.stdout)
        self.assertIn("100000", p.stdout, "prompt honors the .wave.json default, like dispatch")
        p = run_wave("prompt", "2", "--budget", "300000")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("300000", p.stdout, "--budget overrides it here too")

    def test_a_non_positive_budget_flag_is_refused(self):
        for flag in ("-5", "0"):
            with self.subTest(flag=flag):
                p = self.dispatch("--budget", flag)
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("--budget", p.stderr)
                self.assertIn("positive", p.stderr)
                self.assertFalse((TMP / "r-handoffs" / "2.md").exists(), "the refusal precedes every prompt")
                p = run_wave("prompt", "2", "--budget", flag)
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("--budget", p.stderr)

    def test_prompt_refuses_an_invalid_wave_json_budget(self):
        """#120 judge round 2: the config fallback must carry the same validation on the prompt path —
        a string crashed `wave prompt` in `budget_note` (TypeError), a negative rendered a nonsense
        ceiling; dispatch already refused both, and must keep doing so."""
        for bad in (-5, "lots"):
            with self.subTest(bad=bad):
                self.declare(budget=bad)
                p = run_wave("prompt", "2")
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("positive", p.stderr)
                self.assertNotIn("Traceback", p.stderr, "a bad config value refuses, it never crashes")
                p = self.dispatch()
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("positive", p.stderr)
                self.assertFalse((TMP / "r-handoffs" / "2.md").exists(), "the refusal precedes every prompt")

    def test_stats_shows_spend_vs_ceiling_per_issue(self):
        p = self.dispatch("--budget", "200000")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        f = TMP / "r-2" / TOKENS
        f.write_text('{"at": "2026-10-28T10:00:00Z", "tokens": 90000}\n'
                     '{"at": "2026-10-28T10:30:00Z", "tokens": 154000}\n')
        p = run_wave("stats")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("o/r#2: 154000 / 200000 tokens (77%)", p.stdout,
                      "the newest cumulative total, against the ceiling")

    def test_stats_names_a_ceiling_without_usage(self):
        p = self.dispatch("--budget", "200000")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = run_wave("stats")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("o/r#2: ceiling 200000, no usage recorded", p.stdout)


if __name__ == "__main__":
    unittest.main()
