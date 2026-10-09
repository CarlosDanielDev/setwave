"""`wave init` — the first run in a repo is a wizard: everything detectable is detected, at most
one question is asked, and it is never asked again (#17).

Fixture repos with no `.wave.json`: o/mk (a Makefile, protected and serial candidates, a sibling
worktree directory), o/ci (a CI workflow only), o/pj (package.json scripts only), o/no (nothing
at all), o/doc (a CLAUDE.md code block only). A registered entry without those files is what
`doctor`/`dispatch` see on a first run.
"""
import json
import shutil
import unittest
from pathlib import Path

from helpers import TESTS, TMP, git, run_wave, sandbox, wave

MK, CI, PJ, NO, DOC = (TMP / n for n in ("mk", "wiz-ci", "wiz-pj", "wiz-no", "wiz-doc"))
CARGO = TMP / "wiz-cargo"


def setUpModule():
    sandbox()
    if not MK.exists():
        for d in (MK, CI, PJ, NO, DOC):
            bare = TMP / "remote" / "o" / f"{d.name}.git"
            bare.parent.mkdir(parents=True, exist_ok=True)
            git("init", "-q", "--bare", str(bare), cwd=TMP)
            git("clone", "-q", str(bare), str(d), cwd=TMP)
            (d / "README.md").write_text("fixture\n")
        (MK / "Makefile").write_text(".PHONY: unit test lint\nunit:\n\techo unit\ntest:\n\techo test\nlint:\n\techo lint\n")
        (MK / "CLAUDE.md").write_text("# rules, no code fences\n")
        (MK / "migrations").mkdir()
        (MK / "migrations" / "01.sql").write_text("select 1;\n")
        (MK / "src" / "auth").mkdir(parents=True)
        (MK / "src" / "auth" / "guard.py").write_text("")
        (MK / "VERSION").write_text("1.0.0\n")
        (MK.parent / "mk-7").mkdir(exist_ok=True)  # the sibling the worktree convention is read from
        (CI / ".github" / "workflows").mkdir(parents=True)
        shutil.copy(TESTS / "fixtures" / "workflow_actions.yml", CI / ".github" / "workflows" / "ci.yml")
        (PJ / "package.json").write_text(json.dumps({"name": "pj", "scripts": {"test": "jest", "lint": "eslint ."}}))
        (DOC / "CLAUDE.md").write_text("# docs\n\n```bash\npytest -q\n```\n")
        CARGO.mkdir(exist_ok=True)
        (CARGO / "Cargo.toml").write_text("[package]\nname = \"wiz\"\nversion = \"0.1.0\"\n")
        for d in (MK, CI, PJ, NO, DOC):
            git("add", ".", cwd=d)
            git("commit", "-q", "-m", "base", cwd=d)
            git("push", "-q", "origin", "main", cwd=d)


def repo(slug, d, **entry):
    return wave.Repo(slug, {"path": str(d), **entry})


def registry() -> dict:
    return wave.load_registry()


def set_entry(slug: str, path: Path, **fields) -> None:
    """The registry entry for slug, exactly: a bare path, plus whatever a test seeds (confirmed_at, gate)."""
    reg = registry()
    entry = {"path": str(path)}
    entry.update(fields)
    reg["repos"][slug] = entry
    wave.save_registry(reg)


class Proposal(unittest.TestCase):
    """What init gathers, each line marked detected (read from the repo) or guessed (convention)."""

    def test_a_makefile_gate_is_detected(self):
        prop = wave.init_proposal(repo("o/mk", MK))
        self.assertEqual(prop["gate"]["value"], ["make test", "make lint"])
        self.assertEqual(prop["gate"]["source"], "detected")

    def test_ci_run_and_uses_lines_are_detected(self):
        prop = wave.init_proposal(repo("o/wiz-ci", CI))
        self.assertEqual(prop["gate"]["value"], ["cargo test", "cargo deny check"])
        self.assertEqual(prop["gate"]["source"], "detected")

    def test_manifest_scripts_are_detected(self):
        prop = wave.init_proposal(repo("o/wiz-pj", PJ))
        self.assertEqual(prop["gate"]["value"], ["npm run lint", "npm run test"])
        self.assertEqual(prop["gate"]["source"], "detected")

    def test_a_doc_code_block_is_the_last_gate_candidate(self):
        prop = wave.init_proposal(repo("o/wiz-doc", DOC))
        self.assertEqual(prop["gate"]["value"], ["pytest -q"])
        self.assertEqual(prop["gate"]["source"], "detected")
        self.assertIn("CLAUDE.md", prop["gate"]["why"])

    def test_an_ecosystem_manifest_gate_is_guessed(self):
        prop = wave.init_proposal(repo("o/wiz-cargo", CARGO))
        self.assertEqual(prop["gate"]["value"], ["cargo fmt --check", "cargo clippy --all-targets -- -D warnings", "cargo test"])
        self.assertEqual(prop["gate"]["source"], "guessed")

    def test_nothing_found_leaves_the_gate_guessed_and_empty(self):
        prop = wave.init_proposal(repo("o/wiz-no", NO))
        self.assertIsNone(prop["gate"]["value"])
        self.assertEqual(prop["gate"]["source"], "guessed")

    def test_the_worktree_convention_is_read_from_sibling_directories(self):
        prop = wave.init_proposal(repo("o/mk", MK))
        self.assertEqual(prop["worktree_prefix"]["value"], "../mk-")
        self.assertEqual(prop["worktree_prefix"]["source"], "detected")
        self.assertIn("mk-7", prop["worktree_prefix"]["why"])

    def test_no_convention_falls_back_guessed_to_the_default(self):
        prop = wave.init_proposal(repo("o/wiz-no", NO))
        self.assertIsNone(prop["worktree_prefix"]["value"])
        self.assertEqual(prop["worktree_prefix"]["source"], "guessed")

    def test_protected_candidates_are_the_named_directories(self):
        prop = wave.init_proposal(repo("o/mk", MK))
        self.assertEqual(prop["protected"]["value"], ["migrations/", "src/auth/"])
        self.assertEqual(prop["protected"]["source"], "detected")

    def test_serial_candidates_are_migrations_dirs_and_version_files(self):
        prop = wave.init_proposal(repo("o/mk", MK))
        self.assertEqual(prop["serial"]["value"], ["migrations/", "VERSION"])
        self.assertEqual(prop["serial"]["source"], "detected")

    def test_base_is_the_github_default_branch(self):
        prop = wave.init_proposal(repo("o/wiz-no", NO))
        self.assertEqual(prop["base"], {"value": "main", "source": "detected", "why": "GitHub default branch"})

    def test_a_first_run_renders_every_line_marked(self):
        r = repo("o/wiz-no", NO)
        out = wave.render_proposal(r, wave.init_proposal(r))
        self.assertIn("wave init o/wiz-no", out)
        self.assertIn('"gate": null', out)
        self.assertIn("# detected", out)
        self.assertIn("# guessed", out)

    def test_the_profile_is_gathered_into_the_proposal(self):
        r = repo("o/mk", MK)
        out = wave.render_proposal(r, wave.init_proposal(r))
        self.assertIn("profile", out)
        self.assertIn("rules", out)


class Unconfirmed(unittest.TestCase):
    """The predicate that decides whether a repo is asked anything."""

    def test_bare_entry_is_unconfirmed(self):
        set_entry("o/wiz-no", NO)
        self.assertTrue(wave.unconfirmed(wave.Repo("o/wiz-no", registry()["repos"]["o/wiz-no"])))

    def test_confirmed_at_is_never_reproposed(self):
        set_entry("o/wiz-no", NO, confirmed_at="2026-10-09T00:00:00Z")
        self.assertFalse(wave.unconfirmed(wave.Repo("o/wiz-no", registry()["repos"]["o/wiz-no"])))

    def test_a_repo_with_a_wave_json_is_never_asked(self):
        local = TMP / "wiz-local"
        local.mkdir(exist_ok=True)
        (local / ".wave.json").write_text(json.dumps({"gate": ["true"]}))
        self.assertFalse(wave.unconfirmed(wave.Repo("o/wiz-local", {"path": str(local)})))

    def test_explicit_keys_count_as_an_answer(self):
        set_entry("o/wiz-no", NO, gate=["make ci"])
        self.assertFalse(wave.unconfirmed(repo("o/wiz-no", NO, gate=["make ci"])))


class FirstRun(unittest.TestCase):
    """The commands: doctor/dispatch propose on a first run, `init` asks once and never again."""

    def test_doctor_proposes_on_a_first_run(self):
        set_entry("o/wiz-no", NO)
        p = run_wave("doctor", cwd=NO)
        self.assertIn("wave init o/wiz-no", p.stdout)
        self.assertIn("# guessed", p.stdout)
        self.assertIn("`wave init --yes`", p.stdout)

    def test_doctor_never_clobbers_an_edited_proposal_file(self):
        set_entry("o/wiz-no", NO)
        run_wave("doctor", cwd=NO)
        pf = NO.parent / "wiz-no-handoffs" / "init-proposal.json"
        self.assertTrue(pf.exists())
        pf.write_text('{"gate": ["make ci"]}')
        run_wave("doctor", cwd=NO)
        self.assertEqual(json.loads(pf.read_text()), {"gate": ["make ci"]})

    def test_a_second_run_after_accept_asks_nothing(self):
        set_entry("o/mk", MK)
        self.assertEqual(run_wave("init", "--yes", cwd=MK).returncode, 0, "accepts the proposal")
        entry = registry()["repos"]["o/mk"]
        self.assertTrue(entry.get("confirmed_at"))
        self.assertEqual(entry["gate"], ["make test", "make lint"])
        self.assertEqual(entry["worktree_prefix"], "../mk-")
        written = json.loads((MK / ".wave.json").read_text())
        self.assertEqual(written["protected"], ["migrations/", "src/auth/"])
        self.assertEqual(written["serial"], ["migrations/", "VERSION"])
        p = run_wave("doctor", cwd=MK)
        self.assertNotIn("wave init o/mk", p.stdout)
        p = run_wave("init", cwd=MK)
        self.assertIn("--again", p.stdout)
        self.assertNotIn("# guessed", p.stdout)

    def test_again_reopens_and_the_edited_proposal_is_what_writes(self):
        set_entry("o/wiz-doc", DOC, confirmed_at="2026-10-09T00:00:00Z")
        p = run_wave("init", "--again", cwd=DOC)
        self.assertIn("wave init o/wiz-doc", p.stdout)
        pf = DOC.parent / "wiz-doc-handoffs" / "init-proposal.json"
        self.assertTrue(pf.exists(), "the proposal is a file the SKILL can read and edit")
        edited = json.loads(pf.read_text())
        edited["gate"] = ["pytest -q", "ruff check ."]
        pf.write_text(json.dumps(edited))
        self.assertEqual(run_wave("init", "--from", str(pf), cwd=DOC).returncode, 0)
        entry = registry()["repos"]["o/wiz-doc"]
        self.assertEqual(entry["gate"], ["pytest -q", "ruff check ."])
        self.assertEqual(json.loads((DOC / ".wave.json").read_text())["gate"], ["pytest -q", "ruff check ."])

    def test_a_from_file_that_cannot_be_an_answer_is_refused(self):
        (DOC / ".wave.json").unlink(missing_ok=True)
        handoffs = DOC.parent / "wiz-doc-handoffs"
        handoffs.mkdir(parents=True, exist_ok=True)
        bad, listy = handoffs / "not-json.json", handoffs / "a-list.json"
        bad.write_text("{oops")
        p = run_wave("init", "--from", str(bad), cwd=DOC)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("cannot read the proposal", p.stderr)
        listy.write_text('["gate"]')
        p = run_wave("init", "--from", str(listy), cwd=DOC)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("must be a JSON object", p.stderr)
        self.assertFalse((DOC / ".wave.json").exists(), "nothing was written by a refused answer")

    def test_a_confirmed_entry_is_never_reproposed_even_without_a_gate(self):
        set_entry("o/wiz-no", NO, confirmed_at="2026-10-09T00:00:00Z")
        p = run_wave("doctor", cwd=NO)
        self.assertNotIn("wave init o/wiz-no", p.stdout)
        self.assertIn("gate is declared and real", p.stdout)
        self.assertIn("--again", p.stdout)

    def test_with_no_gate_and_no_answer_the_dispatch_refusal_is_the_proposal(self):
        set_entry("o/wiz-no", NO)
        p = run_wave("dispatch", "#1", cwd=NO)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("wave init o/wiz-no", p.stdout + p.stderr, "the refusal carries the proposal itself")
        self.assertIn('"gate": null', p.stdout + p.stderr)
        self.assertNotIn("<no gate found: read the repo", p.stdout + p.stderr)


class SkillText(unittest.TestCase):
    """The SKILL carries the wizard contract: one question, the whole proposal, never twice."""

    def test_step_one_asks_one_question_over_the_whole_proposal(self):
        text = (TESTS.parent / "skills" / "wave" / "SKILL.md").read_text()
        self.assertIn("one `AskUserQuestion` containing the whole proposal", text)
        self.assertIn("`wave init --yes`", text)
        self.assertIn("`wave init --from <file>`", text)
        self.assertIn("`wave init --again`", text)
        self.assertIn("confirmed_at", text)
        self.assertIn("small PR", text)


if __name__ == "__main__":
    unittest.main()
