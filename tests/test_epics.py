"""`wave epics`: every open epic across the registry's repos, readiest first.

Three epics across two repos (a/one, b/two), plus o/r holding one open issue that is not an epic:
  a/one#1   open epic (has sub-issues: a/one#9 ready, b/two#5 blocked) — last status comment 2026-10-07
  b/two#6   open epic (label `epic`; its leaf b/two#7 has an open PR)   — never posted
  a/one#10  a closed epic — must not appear whatever its leaves say
"""
import json
import unittest

from helpers import TMP, TESTS, cross_repos, run_wave

SKILL = TESTS.parent / "skills" / "wave" / "SKILL.md"


def setUpModule():
    cross_repos()
    reg_file = TMP / "config" / "repos.json"
    reg = json.loads(reg_file.read_text())
    reg["repos"]["x/dead"] = {"path": str(TMP / "no-such-checkout")}  # in the registry, no checkout on disk
    reg["repos"]["y/broken"] = {"path": str(TMP / "r")}  # a checkout whose issue list the fake gh cannot answer
    reg_file.write_text(json.dumps(reg))


class RegistryWide(unittest.TestCase):
    def test_open_epics_readiest_first_one_line_each_ending_in_the_command(self):
        p = run_wave("epics", cwd=TMP / "one")
        self.assertEqual(p.returncode, 0, p.stderr)
        one = next(l for l in p.stdout.splitlines() if l.lstrip().startswith("a/one#1"))
        self.assertTrue(one.rstrip().endswith("/setwave:wave a/one#1"), one)
        self.assertIn("ready 1", one)
        self.assertIn("blocked 1", one)
        self.assertIn("in progress 0", one)
        self.assertIn("leaves 0/2 closed", one)
        self.assertIn("last status 2026-10-07T09:00:00Z", one)
        six = next(l for l in p.stdout.splitlines() if l.lstrip().startswith("b/two#6"))
        self.assertTrue(six.rstrip().endswith("/setwave:wave b/two#6"), six)
        self.assertIn("ready 0", six)
        self.assertIn("in progress 1", six)
        self.assertIn("last status never", six)
        self.assertLess(p.stdout.index("a/one#1"), p.stdout.index("b/two#6"), "readiest first")

    def test_a_fully_closed_epic_never_appears(self):
        p = run_wave("epics", cwd=TMP / "one")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("a/one#10", p.stdout)
        self.assertNotIn("fully closed", p.stdout)

    def test_an_open_issue_that_is_not_an_epic_is_skipped(self):
        p = run_wave("epics", cwd=TMP / "one")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("o/r#60", p.stdout)
        self.assertNotIn("b/two#7", p.stdout, "a sub-issue is never an epic, even labeled")

    def test_a_repo_without_a_local_checkout_is_skipped_loudly(self):
        p = run_wave("epics", cwd=TMP / "one")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("skip x/dead", p.stderr, "named on stderr, so --json stays parseable")

    def test_a_repo_whose_issue_list_fails_is_skipped_loudly_too(self):
        p = run_wave("epics", cwd=TMP / "one")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("skip y/broken", p.stderr)
        self.assertIn("a/one#1", p.stdout, "the other repos are still listed")


class Filters(unittest.TestCase):
    def test_slug_scans_one_repo(self):
        p = run_wave("epics", "--slug", "b/two", cwd=TMP / "one")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("b/two#6", p.stdout)
        self.assertNotIn("a/one#1", p.stdout)

    def test_repo_scans_one_repo(self):
        p = run_wave("epics", "--repo", str(TMP / "two"), cwd=TMP)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("b/two#6", p.stdout)
        self.assertNotIn("a/one#1", p.stdout)

    def test_slug_and_repo_together_is_refused(self):
        p = run_wave("epics", "--slug", "b/two", "--repo", str(TMP / "two"), cwd=TMP)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("give --slug or --repo, not both", p.stderr)


class JsonMode(unittest.TestCase):
    def test_json_rows_sorted_by_ready_desc(self):
        p = run_wave("epics", "--json", cwd=TMP / "one")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = json.loads(p.stdout)
        self.assertEqual([r["epic"] for r in rows], ["a/one#1", "b/two#6"])
        top = rows[0]
        self.assertEqual((top["leaves"], top["closed"], top["ready"], top["blocked"], top["in_progress"]),
                         (2, 0, 1, 1, 0))
        self.assertEqual(top["last_status"], "2026-10-07T09:00:00Z")
        self.assertEqual(top["command"], "/setwave:wave a/one#1")
        self.assertEqual(rows[1]["last_status"], None)
        self.assertEqual((rows[1]["leaves"], rows[1]["ready"], rows[1]["in_progress"]), (1, 0, 1))


class SkillStepZero(unittest.TestCase):
    def test_the_skill_tells_the_agent_to_run_wave_epics_json_with_no_epic(self):
        text = SKILL.read_text()
        self.assertIn("wave epics --json", text)
        self.assertNotIn("is planned: #10", text, "the stale 'planned' line must go once #10 lands")


if __name__ == "__main__":
    unittest.main()
