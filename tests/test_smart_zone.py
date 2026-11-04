"""Smart zone (#121): `smart_zone` in .wave.json is validated exactly like `budget` (positive int,
default 100000), a crude documented context estimate feeds the `Size:` line's zone-fit clause,
dispatch warns `!` with split advice over the zone and never refuses, and `wave stats` / `wave
agents` show spend and state vs zone in the same ledger the budget ceiling uses. The zone is an
estimate everywhere it prints — never a live context reading.

o/r#2 is the dispatchable leaf test_commands.Dispatch uses; same tearDown contract: the worktrees,
prompts and usage files this class creates are removed again.
"""
import json
import shutil
import subprocess
import unittest

from helpers import CLONE, TMP, WAVE, run_wave, sandbox, wave

REG = TMP / "config" / "repos.json"
TOKENS = ".setwave-tokens.jsonl"


class ZoneConfig(unittest.TestCase):
    def setUp(self):
        sandbox()
        reg = json.loads(REG.read_text())  # a sibling suite's registry snapshot can lack the sandbox repo
        if "o/r" not in reg["repos"]:
            reg["repos"]["o/r"] = {"path": str(CLONE)}
            REG.write_text(json.dumps(reg))
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

    def dispatch(self, *extra, n=2):
        return run_wave("dispatch", str(n), "--force", "--no-prepare", *extra)

    def test_a_bad_smart_zone_refuses_dispatch_and_prompt(self):
        """`smart_zone` is validated exactly like `budget` (#120's standard): a value that is not a
        positive number of tokens refuses before any prompt is written, on the dispatch path and on
        `wave prompt` too."""
        for bad in ("lots", -5, 0):
            with self.subTest(bad=bad):
                self.declare(smart_zone=bad)
                p = self.dispatch()
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("positive", p.stderr)
                self.assertNotIn("Traceback", p.stderr, "a bad config value refuses, it never crashes")
                self.assertFalse((TMP / "r-handoffs" / "2.md").exists(), "the refusal precedes every prompt")
                p = run_wave("prompt", "2")
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertIn("positive", p.stderr)

    def test_the_default_zone_applies_and_the_size_line_states_the_fit(self):
        """No config, the default 100000 speaks: dispatch's Size line carries the zone fit of the
        crude estimate, with the estimate wording the zone must never print without."""
        p = self.dispatch()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        est = wave.zone_estimate(wave.issue_size("## Done when\n\n- [ ] it works (`scripts/wave.py:1`)\n"))
        self.assertIn(f"crude context estimate ~{est} tokens vs zone 100000 — fits the zone", p.stdout)
        self.assertNotIn("over the zone", p.stdout)

    def test_over_the_zone_dispatch_warns_with_split_advice_and_never_refuses(self):
        self.declare(smart_zone=5000)
        p = self.dispatch()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr + "\nthe zone warns, it never refuses dispatch")
        self.assertIn("over the zone — split", p.stdout, "the Size line states the fit")
        self.assertIn("! ", p.stdout, "the dispatch warning is a `!` line")
        self.assertIn("split this issue", p.stdout, "the warning carries the split advice")
        self.assertIn("estimate", p.stdout, "the warning says estimate, never a live reading")
        self.assertTrue((TMP / "r-handoffs" / "2.md").exists(), "the prompt is written anyway")


class ZoneLedger(unittest.TestCase):
    def setUp(self):
        sandbox()
        self._registry = REG.read_text()
        p = run_wave("dispatch", "2", "--force", "--no-prepare", "--budget", "200000")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def tearDown(self):
        REG.write_text(self._registry)
        wt = TMP / "r-2"
        if wt.exists():
            head = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "-C", str(TMP / "r"), "worktree", "remove", "--force", str(wt)],
                           capture_output=True, text=True)
            if head and head != "HEAD":
                subprocess.run(["git", "-C", str(TMP / "r"), "branch", "-D", head],
                               capture_output=True, text=True)
        shutil.rmtree(wt, ignore_errors=True)
        prompt = TMP / "r-handoffs" / "2.md"
        if prompt.exists():
            prompt.unlink()

    def usage(self, tokens: int):
        (TMP / "r-2" / TOKENS).write_text(f'{{"at": "2026-11-03T10:00:00Z", "tokens": {tokens}}}\n')

    def test_stats_shows_spend_vs_zone_per_issue(self):
        """Same ledger as the budget ceiling, one zone word per state: in-zone, near-edge at 80% of
        the zone, over past it."""
        for tokens, word in ((50000, "in-zone"), (80000, "near-edge"), (120000, "over")):
            self.usage(tokens)
            p = run_wave("stats")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn(f"— zone 100000: {word}", p.stdout, f"{tokens} tokens spent")
            self.assertIn("estimate", p.stdout, "the zone read is an estimate wherever it prints")

    def test_stats_names_the_zone_while_no_usage_is_recorded(self):
        """No usage yet: the row names the zone, and "no usage" says once — the ceiling clause
        already carries it (#127)."""
        p = run_wave("stats")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = next(l for l in p.stdout.splitlines() if l.lstrip().startswith("o/r#2"))
        self.assertIn("ceiling 200000, no usage recorded", row, "the pinned budget substring stays")
        self.assertIn("— zone 100000 (crude estimate)", row, "the zone is still named")
        self.assertEqual(row.count("no usage"), 1, "the wording is deduped: one mention, not two")

    def test_agents_shows_each_seat_zone_state(self):
        self.usage(90000)
        p = run_wave("agents")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = next(l for l in p.stdout.splitlines() if l.lstrip().startswith("o/r#2"))
        self.assertIn("zone: near-edge", row)
        self.assertIn("estimate", row)
        (TMP / "r-2" / TOKENS).unlink()
        p = run_wave("agents")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        row = next(l for l in p.stdout.splitlines() if l.lstrip().startswith("o/r#2"))
        self.assertIn("zone: no usage", row)


class Docs(unittest.TestCase):
    def test_define_carries_the_split_rule(self):
        skill = (WAVE.parent.parent / "skills" / "define" / "SKILL.md").read_text()
        self.assertIn("smart zone", skill, "the rule names the zone")
        self.assertIn("split", skill, "the rule says an issue over the zone must split")

    def test_the_agent_template_carries_the_hygiene_block(self):
        tpl = (WAVE.parent.parent / "templates" / "agent.md").read_text()
        self.assertIn("higiene de contexto", tpl.lower())
        self.assertIn("estimativa", tpl.lower(), "the caveat: the zone is an estimate")

    def test_the_guarantees_table_carries_the_zone(self):
        row = next(r for r in wave.GUARANTEES if "smart_zone" in r[0])
        self.assertTrue(row[3], "the promise is pinned by a test")


if __name__ == "__main__":
    unittest.main()
