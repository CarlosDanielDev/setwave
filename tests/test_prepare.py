"""`wave prepare`: the facts of a handoff written by the tool, the judgement asked of a human.

o/r#70 is bare (no Done when, no Handoff, no evidence, no ADR stub); o/r#71 carries a complete
body with a hand-written Handoff; o/l#13 and o/l#14 are bare leaves dispatch can pick; o/r#1 is
the epic — seven sub-issues, and the one issue `prepare` refuses. Writes go through FAKE_GH_EDIT
and their own call logs, so `NoWrites` in test_commands keeps reading a log with no write in it.
"""
import json
import os
import subprocess
import sys
import unittest

from helpers import TMP, WAVE, run_wave, sandbox, wave

BARE = "Parent: #1\n\nMake the thing faster.\n"
ANSWERS = "- [ ] the suite passes — owner: implementer\n- [ ] the doc names the flag\n"
L = TMP / "l"


def setUpModule():
    sandbox()
    import test_liveness  # the o/l clone its setUpModule builds is the dispatch sandbox here too
    test_liveness.setUpModule()


def writes_allowed(*args, cwd=None, log: str):
    """`wave.py <args>` with the fake gh accepting issue edits/comments and logged to their own file."""
    env = {**os.environ, "FAKE_GH_EDIT": "1", "FAKE_GH_LOG": str(TMP / f"gh-{log}.log")}
    p = subprocess.run([sys.executable, str(WAVE), *args], cwd=cwd or sandbox(), capture_output=True, text=True, env=env)
    f = TMP / f"gh-{log}.log"
    calls = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
    return p, calls


def edited_body(calls) -> str | None:
    edits = [c for c in calls if c[:2] == ["issue", "edit"]]
    return edits[-1][edits[-1].index("--body") + 1] if edits else None


def comments(calls) -> list[str]:
    return [c[c.index("--body") + 1] for c in calls if c[:2] == ["issue", "comment"]]


class Prepare(unittest.TestCase):
    def test_a_bare_issue_gets_the_handoff_appended_and_the_questions_listed(self):
        p, calls = writes_allowed("prepare", "70", log="prepare-70")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        body = edited_body(calls)
        self.assertIsNotNone(body, p.stdout + p.stderr)
        self.assertTrue(body.startswith(BARE + "\n## Handoff\n"), body + " — nothing above the block is touched")
        self.assertIn(f"git worktree add -b feat/70-bare-leaf {TMP / 'r-70'} origin/main", body)
        self.assertIn("Closes #70", body)
        self.assertIn("AI attribution", body, "the non-negotiables are facts too")
        self.assertIn("<no gate found", body, "the gate placeholder is what facts says, not a guess")
        for gap in ("Done when", "evidence", "ADR stub"):
            self.assertIn(gap, p.stdout, f"the {gap} question is listed")
        self.assertIn("?", p.stdout, "each gap becomes the exact question to ask")
        posted = comments(calls)
        self.assertEqual(len(posted), 1, calls)
        self.assertIn("wave prepare", posted[0])
        self.assertIn("Done when", posted[0])

    def test_a_parent_is_refused_before_any_write(self):
        p, calls = writes_allowed("prepare", "1", log="prepare-1")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("PREPARE-PARENT: o/r#1 is a parent (7 sub-issues); parents are never dispatched", p.stderr)
        self.assertEqual([c for c in calls if c[:2] == ["issue", "edit"]], [], "a parent's body is never written")
        self.assertEqual([c for c in calls if c[:2] == ["issue", "comment"]], [], "and no gap comment either")

    def test_prepare_is_idempotent_byte_for_byte(self):
        repo = wave.Repo.get("o/r")
        iss = repo.issue(70)
        block = wave.handoff_block(repo, iss)
        once = wave.insert_section(BARE, wave.HANDOFF, block)
        self.assertEqual(wave.insert_section(once, wave.HANDOFF, block), once, "same facts, same block, no diff")
        self.assertEqual(once, BARE + "\n" + block, "nothing above the block is touched")

    def test_an_existing_handoff_is_replaced_in_place_byte_identical_elsewhere(self):
        before = wave.Repo.get("o/r").issue(71)["body"]
        p, calls = writes_allowed("prepare", "71", log="prepare-71")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        body = edited_body(calls)
        self.assertIsNotNone(body)
        head, _, _ = before.partition("## Handoff")
        self.assertTrue(body.startswith(head + "## Handoff\n"), body + " — everything above is byte-identical")
        self.assertIn("## Out of scope", body.split("## Handoff", 1)[1], "the sections after it survive")
        self.assertNotIn("../r-71", body, "the hand-written block is gone (the branch line it declared stays: branch_for honors it)")
        self.assertIn("written by `wave prepare`", body)
        self.assertIn("Non-negotiables:", body)
        self.assertEqual(comments(calls), [], "no gaps, no comment")
        self.assertIn("in place", p.stdout)

    def test_the_three_gaps_come_out_as_three_questions(self):
        self.assertEqual([g for g, _ in wave.prepare_questions(BARE)], ["Done when", "evidence", "ADR stub"])
        complete = wave.Repo.get("o/r").issue(71)["body"]
        self.assertEqual(wave.prepare_questions(complete), [])


class Amend(unittest.TestCase):
    def test_the_answered_criteria_are_written_once_before_the_handoff(self):
        f = TMP / "done-when-prepare-14.md"
        f.write_text(ANSWERS)
        p, calls = writes_allowed("amend", "72", "--done-when", str(f), log="amend-72")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("## Done when\n\n" + ANSWERS + "\n## Handoff", edited_body(calls), "written once, before the Handoff")
        p, calls = writes_allowed("amend", "4", "--done-when", str(f), log="amend-4-existing")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("refused", p.stderr)
        self.assertIsNone(edited_body(calls), "a Done-when that exists is never overwritten")

    def test_a_file_with_prose_is_refused(self):
        f = TMP / "prose-prepare-14.md"
        f.write_text("- [ ] fine\nnot an item\n")
        p, calls = writes_allowed("amend", "70", "--done-when", str(f), log="amend-prose")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual([c for c in calls if c[:2] == ["issue", "edit"]], [])
        p, _ = writes_allowed("amend", "70", "--done-when", str(TMP / "missing-prepare-14.md"), log="amend-missing")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_without_a_handoff_the_section_is_appended_at_the_end(self):
        f = TMP / "done-when-3-14.md"
        f.write_text("- [ ] unblocked\n")
        p, calls = writes_allowed("amend", "3", "--done-when", str(f), log="amend-3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(edited_body(calls).rstrip("\n").endswith("## Done when\n\n- [ ] unblocked"), edited_body(calls))


class Dispatch(unittest.TestCase):
    def test_a_bare_issue_is_prepared_and_its_prompt_carries_the_handoff(self):
        self.assertFalse((TMP / "l-13").exists())
        p, calls = writes_allowed("dispatch", "13", cwd=L, log="dispatch-13")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("o/l#13: no `## Handoff` in the body — prepare wrote it", p.stdout)
        body = edited_body(calls)
        self.assertIn("## Handoff", body)
        self.assertIn(f"git worktree add -b feat/13-leaf-without-a-handoff {TMP / 'l-13'} origin/main", body)
        prompt = (TMP / "l-handoffs" / "13.md").read_text()
        self.assertIn("written by `wave prepare`", prompt, "the prompt says the Handoff was tool-supplied")
        self.assertTrue((TMP / "l-13" / ".setwave.json").exists(), "the dispatch itself went on end to end")

    def test_no_prepare_leaves_the_body_and_the_prompt_alone(self):
        p, calls = writes_allowed("dispatch", "14", "--no-prepare", cwd=L, log="dispatch-14")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual([c for c in calls if c[:2] == ["issue", "edit"]], [])
        self.assertNotIn("## Handoff", (TMP / "l-handoffs" / "14.md").read_text())


class Size(unittest.TestCase):
    """#119: prepare classifies the issue small / medium / large by Done-when items and cited files;
    a small one runs as one agent with no judge/fix phase, and the SKILL's step 3 says so."""

    def test_small_medium_large_by_items_and_files(self):
        small = "## Done when\n\n- [ ] a (`f.py:1`) — owner: x\n- [ ] b (`g.py:1`) — owner: x\n"
        self.assertEqual(wave.issue_size(small), {"size": "small", "items": 2, "files": 2})
        medium = small + "- [ ] c\n- [ ] d\n- [ ] e\n"
        self.assertEqual(wave.issue_size(medium)["size"], "medium", "5 items, few files: the middle size")
        self.assertEqual(wave.issue_size(medium + "- [ ] f\n")["size"], "large", "6 items is large on items alone")
        files = " ".join(f"(`f{i}.py:1`)" for i in range(10))
        self.assertEqual(wave.issue_size(f"## Done when\n\n- [ ] a {files}\n")["size"], "large",
                         "10 cited files is large on files alone")

    def test_the_handoff_names_the_size_and_what_it_means_for_the_pipeline(self):
        repo = wave.Repo.get("o/r")
        small = wave.handoff_block(repo, {"number": 9, "title": "t", "html_url": "u",
                                          "body": "## Done when\n\n- [ ] a — owner: x\n"})
        self.assertIn("Size: small (1 Done-when items, 0 files cited)", small)
        self.assertIn("one agent, no judge/fix phase", small)
        medium = wave.handoff_block(repo, {"number": 9, "title": "t", "html_url": "u",
                                           "body": "## Done when\n\n" + "- [ ] a\n" * 4})
        self.assertIn("Size: medium (4 Done-when items, 0 files cited)", medium)
        self.assertIn("full pipeline", medium)

    def test_prepare_prints_the_classification(self):
        p, _ = writes_allowed("prepare", "70", log="prepare-70-size")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Size: small", p.stdout)

    def test_amend_refreshes_the_size_line_prepare_stamped(self):
        """#120 judge, blocking: prepare runs before the owner's answers, so the Size it stamps can predate
        the amend — when the Done when lands, the routing line must follow the final body, never stay
        `small (0 Done-when items)` for an issue that now carries four."""
        p, calls = writes_allowed("prepare", "70", log="prepare-70-size-refresh")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Size: small (0 Done-when items", edited_body(calls), "the stamp starts truthful for a bare body")
        # GitHub keeps that body; the owner's four-item amend lands on it. The fake gh serves the static
        # fixture, so the persisted composition is replayed through the same functions amend runs.
        repo = wave.Repo.get("o/r")
        prepared = wave.insert_section(BARE, wave.HANDOFF, wave.handoff_block(repo, {"number": 70, "title": "Bare leaf", "html_url": "u", "body": BARE}))
        amended = wave.with_done_when(prepared, "- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n")
        final = wave.refresh_size(amended)
        self.assertIn("Size: medium (4 Done-when items", final, "the routing line follows the final Done when")
        self.assertNotIn("Size: small", final)

    def test_amend_refreshes_a_stale_size_line_persisted_on_github(self):
        f = TMP / "done-when-amend-67.md"
        f.write_text("- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n")
        p, calls = writes_allowed("amend", "67", "--done-when", str(f), log="amend-67-size")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        body = edited_body(calls)
        self.assertIsNotNone(body)
        self.assertIn("Size: medium (4 Done-when items", body, "the stale small line prepare left is gone")
        self.assertNotIn("Size: small", body)

    def test_dispatch_prints_the_routing_from_the_final_body(self):
        p = run_wave("dispatch", "68", "--no-prepare", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Size: medium (4 Done-when items", p.stdout,
                      "the body still carries the stale `Size: small` line; dispatch computes its own")
        (TMP / "r-handoffs" / "68.md").unlink(missing_ok=True)  # --dry-run still writes the prompt file

    def test_the_skill_says_a_small_issue_runs_one_agent_without_judge_fix(self):
        skill = (WAVE.parent.parent / "skills" / "wave" / "SKILL.md").read_text()
        self.assertIn("Size:", skill)
        self.assertIn("no judge/fix phase", skill)


class PromptNote(unittest.TestCase):
    def test_the_note_names_the_tool_sections_and_the_missing_ones(self):
        repo = wave.Repo.get("o/r")
        marked = "## Handoff\n<!-- written by `wave prepare` -->\n"
        out = wave.render_prompt(repo, {"number": 9, "title": "t", "html_url": "u", "body": marked}, "feat/9-t", "abc1234", 1)
        self.assertIn("written by `wave prepare`", out)
        self.assertIn("Falta na issue", out)
        self.assertIn("Done when", out)
        out = wave.render_prompt(repo, {"number": 9, "title": "t", "html_url": "u", "body": wave.Repo.get("o/r").issue(71)["body"]},
                                 "feat/9-t", "abc1234", 1)
        self.assertNotIn("Falta na issue", out, "a complete issue asks the agent for nothing extra")
        self.assertNotIn("Escrito pela ferramenta", out, "a hand-written handoff is nobody's claim")

    def test_the_agent_template_carries_the_note(self):
        self.assertIn("{{PREPARED}}", (wave.TEMPLATES / "agent.md").read_text())


class Skill(unittest.TestCase):
    def test_the_skill_turns_the_questions_into_one_ask_and_one_amend(self):
        skill = (WAVE.parent.parent / "skills" / "wave" / "SKILL.md").read_text()
        self.assertIn("wave prepare", skill)
        self.assertIn("AskUserQuestion", skill)
        self.assertIn("--done-when", skill)
        self.assertIn("skip", skill)
        self.assertIn("--no-prepare", skill)


if __name__ == "__main__":
    unittest.main()
