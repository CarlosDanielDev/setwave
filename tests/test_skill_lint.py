"""`scripts/validate_skills.py`: the frontmatter of every skill, pinned here before CI runs it.

A skill whose frontmatter is broken does not fail loudly: it silently stops being offered to
the tooling. CI runs the linter on every push on both matrix OSes; these tests pin what it
refuses, so removing a check fails here first.
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import TMP, WAVE

SCRIPTS = WAVE.parent
SKILLS = WAVE.parent.parent / "skills"


def load():
    spec = importlib.util.spec_from_file_location("validate_skills", SCRIPTS / "validate_skills.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def skill(tmp: Path, name: str, frontmatter: str, body: str = "Does the thing.\n") -> Path:
    d = tmp / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n# {name}\n\n{body}")
    return d


GOOD = "name: my-skill\ndescription: A description long enough to pass the forty-character floor.\n"


class Real(unittest.TestCase):
    def test_the_plugins_own_skills_pass_and_the_cli_exits_zero(self):
        """Exactly what CI runs: a clean repo is exit 0, and the real skills are clean."""
        p = subprocess.run([sys.executable, str(SCRIPTS / "validate_skills.py")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(load().problems(SKILLS.parent), [])

    def test_ci_runs_the_linter(self):
        """The guard lives in CI: the workflow names the step, on the matrix that runs on both OSes."""
        ci = (WAVE.parent.parent / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("run: python3 scripts/validate_skills.py", ci)
        self.assertIn("ubuntu-latest", ci)
        self.assertIn("macos-latest", ci)

    def test_a_missing_root_is_refused_not_a_traceback(self):
        d = Path(tempfile.mkdtemp(dir=TMP))
        p = subprocess.run([sys.executable, str(SCRIPTS / "validate_skills.py"), "--root", str(d / "nope")],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("not a directory", p.stderr)


class Refusals(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=TMP))
        (self.tmp / "templates").mkdir()
        (self.tmp / "templates" / "issue-contract.md").write_text("the contract\n")

    def problems(self):
        return load().problems(self.tmp)

    def test_name_must_be_kebab_and_match_its_directory(self):
        skill(self.tmp, "my-skill", "name: My-Skill\ndescription: " + "x" * 40 + "\n")
        self.assertTrue(any("kebab" in p for p in self.problems()), self.problems())

    def test_name_must_not_differ_from_the_directory(self):
        skill(self.tmp, "other-dir", "name: my-skill\ndescription: " + "x" * 40 + "\n")
        self.assertTrue(any("directory" in p for p in self.problems()), self.problems())

    def test_description_must_be_at_least_40_chars(self):
        skill(self.tmp, "my-skill", "name: my-skill\ndescription: too short\n")
        self.assertTrue(any("40" in p for p in self.problems()), self.problems())

    def test_a_missing_or_empty_description_is_refused(self):
        skill(self.tmp, "my-skill", "name: my-skill\n")
        self.assertTrue(any("description" in p for p in self.problems()), self.problems())

    def test_a_dead_template_link_is_refused(self):
        skill(self.tmp, "my-skill", GOOD, body="Read `templates/issue-contract.md` and `templates/no-such-file.md`.\n")
        dead = [p for p in self.problems() if "no-such-file" in p]
        self.assertEqual(len(dead), 1, self.problems())
        self.assertIn("templates/no-such-file.md", dead[0])

    def test_a_skill_without_frontmatter_is_refused(self):
        d = self.tmp / "skills" / "my-skill"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# no frontmatter at all\n")
        self.assertTrue(any("frontmatter" in p for p in self.problems()), self.problems())

    def test_a_directory_without_a_skill_file_is_refused(self):
        (self.tmp / "skills" / "empty-skill").mkdir(parents=True)
        self.assertTrue(any("empty-skill" in p for p in self.problems()), self.problems())

    def test_a_clean_skill_passes(self):
        skill(self.tmp, "my-skill", GOOD, body="Read `templates/issue-contract.md` first.\n")
        self.assertEqual(self.problems(), [])


if __name__ == "__main__":
    unittest.main()
