"""The define skill: its triggers, its five escalation sensors, its one door to GitHub,
and the plan directory a session produces (linted with the same rules `wave lint` uses)."""
import re
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
SKILL = ROOT / "skills" / "define" / "SKILL.md"
PLAN = TESTS / "define-plan"

from helpers import wave  # noqa: E402  (after the paths: helpers pins the fake gh first)


def skill_text() -> str:
    return SKILL.read_text()


def frontmatter(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else ""


class Frontmatter(unittest.TestCase):
    def test_skill_exists_with_name_and_triggers(self):
        text = skill_text()
        fm = frontmatter(text)
        self.assertTrue(fm, "the skill carries YAML frontmatter")
        self.assertRegex(fm, r"(?m)^name: define$")
        description = next((l for l in fm.splitlines() if l.startswith("description:")), "")
        # the triggers the issue names, in the words users type, plus the English ones
        for trigger in ("define a #", "refina", "deixa pronta", "make #N ready"):
            self.assertIn(trigger, description, f"trigger {trigger!r} in the description")

    def test_the_high_uncertainty_rule_is_stated(self):
        description = frontmatter(skill_text())
        self.assertIn("uncertainty", description)
        self.assertIn("epic", description)  # the rule: do not use it there, suggest an epic instead


class EscalationSensors(unittest.TestCase):
    def test_five_conditions_listed_explicitly(self):
        text = skill_text()
        m = re.search(r"^## Escalation sensors\n(.*?)(?=^## )", text, re.S | re.M)
        self.assertTrue(m, "an `## Escalation sensors` section")
        numbered = re.findall(r"^\d+\.", m.group(1), re.M)
        self.assertEqual(len(numbered), 5, "the five conditions of the original, adapted")
        # two the issue names verbatim in spirit: the unobservable result and the two don't-knows
        section = m.group(1)
        self.assertIn("observabl", section)
        self.assertRegex(section, r"(?i)don't know|do not know|não sei")


class OneDoorToGitHub(unittest.TestCase):
    FORBIDDEN = [
        r"gh issue create", r"gh issue edit", r"gh issue close", r"gh issue comment",
        r"gh pr create", r"gh pr edit", r"gh pr close", r"gh pr merge", r"gh release create",
        r"gh api [^\n]*-X POST", r"gh api [^\n]*-F ", r"gh api [^\n]*sub_issue_id",
    ]

    def test_no_command_writes_to_github_directly(self):
        text = skill_text()
        for pattern in self.FORBIDDEN:
            self.assertIsNone(re.search(pattern, text), f"the skill never runs `{pattern}`")

    def test_the_door_is_wave_plan_behind_a_dry_run_and_an_explicit_ok(self):
        text = skill_text()
        self.assertIn("wave plan", text)
        self.assertIn("--dry-run", text)
        self.assertIn("explicit OK", text)


class PlanDirectoryPassesLint(unittest.TestCase):
    """The worked example a session produced: the same checks `wave plan` runs on the
    directory, then lint with provisional numbers — fill first, exactly as `wave plan`
    fills bodies once every issue exists, so the branch and `Closes` contradictions hold."""

    def test_directory_is_a_valid_plan_input(self):
        rows = [l.split("\t") for l in (PLAN / "index.tsv").read_text().splitlines()
                if l.strip() and not l.startswith("#")]
        self.assertTrue(rows, "index.tsv has rows")
        for row in rows:
            self.assertEqual(len(row), 4, f"4 tab-separated fields: {row}")
            self.assertTrue((PLAN / f"{row[0]}.md").exists(), f"body for key {row[0]}")

    def test_every_body_lints_clean_after_fill(self):
        rows = [l.split("\t") for l in (PLAN / "index.tsv").read_text().splitlines()
                if l.strip() and not l.startswith("#")]
        numbers = {k: 900 + i for i, (k, *_rest) in enumerate(rows)}  # provisional; `wave plan` writes the real ones
        for k, _title, labels, _parent in rows:
            body = wave.fill_refs((PLAN / f"{k}.md").read_text(), numbers)
            typ = next((t for t in wave.ISSUE_TYPES if t in labels.split(",")), None)
            missing = wave.lint_body(body, numbers[k], typ)
            self.assertEqual(missing, [], f"{k}.md [{typ}]")


if __name__ == "__main__":
    unittest.main()
