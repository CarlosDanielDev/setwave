"""The Done-when ledger: the PR declares what it did, `verify` refuses what is undeclared, `merge` applies it to the issue.

Pure parts first (parsing, comparing, ticking a body byte for byte), then the commands against the fake `gh`:
`verify` on PRs with a ledger present, absent and mismatched; `merge` applying a partial ledger; `tick` by text,
by index, twice; `next`, `status` and the prompt reading the boxes. Writes go through FAKE_GH_EDIT / FAKE_GH_MERGE
and a separate call log, so `NoWrites` in test_commands keeps reading a log with no write in it.
"""
import json
import os
import subprocess
import sys
import unittest

from helpers import TMP, WAVE, run_wave, sandbox, wave

ISSUE = ("Parent: #1\n\nWhy, with  two spaces and a trailing one \n\n## Done when\n\n"
         "- [ ] first thing — owner: implementer\n"
         "- [ ] second   thing — owner: implementer\n"
         "* [x] third thing\n"
         "- [ ] fourth thing\n"
         "\n## Out of scope\n\n- [ ] not an item\n")


def setUpModule():
    sandbox()


def writes_allowed(*args, log: str):
    """`wave.py <args>` with the fake gh accepting issue edits/comments/reopens and PR merges, logged to their own file."""
    env = {**os.environ, "FAKE_GH_EDIT": "1", "FAKE_GH_MERGE": "1", "FAKE_GH_LOG": str(TMP / f"gh-{log}.log")}
    p = subprocess.run([sys.executable, str(WAVE), *args], cwd=sandbox(), capture_output=True, text=True, env=env)
    calls = [json.loads(l) for l in (TMP / f"gh-{log}.log").read_text().splitlines()] if (TMP / f"gh-{log}.log").exists() else []
    return p, calls


def edited_body(calls) -> str | None:
    edits = [c for c in calls if c[:2] == ["issue", "edit"]]
    return edits[-1][edits[-1].index("--body") + 1] if edits else None


class Parse(unittest.TestCase):
    def test_items_states_and_the_section_ends_at_the_next_heading(self):
        items = wave.done_when(ISSUE)
        self.assertEqual([(i["key"], i["state"]) for i in items], [
            ("first thing — owner: implementer", "open"), ("second thing — owner: implementer", "open"),
            ("third thing", "done"), ("fourth thing", "open")])

    def test_a_struck_item_and_a_why_keep_the_items_text_as_key(self):
        items = wave.done_when("## Done when\n\n- [ ] ~~a — owner: x~~ — dropped: no\n- [ ] b — not done: later\n  more why\n- [X] c\n")
        self.assertEqual([(i["key"], i["state"]) for i in items], [("a — owner: x", "dropped"), ("b", "open"), ("c", "done")])

    def test_no_heading_at_the_start_of_a_line_is_no_list(self):
        self.assertIsNone(wave.done_when("The orchestrator reads every `## Done when\n\n- [ ] an item\n"))
        self.assertIsNone(wave.done_when("## Ledger — Done when\n\n- [x] an item\n"))


class Check(unittest.TestCase):
    def test_a_verbatim_copy_with_whitespace_changes_matches(self):
        pr = ("Closes #N\n\n## Done when\n\n- [x] first thing —  owner: implementer\n"
              "- [ ] ~~second thing — owner: implementer~~ — dropped: not needed\n- [x] third thing\n"
              "- [ ] fourth thing — not done: needs a machine\n")
        self.assertIsNone(wave.ledger_check(pr, ISSUE))

    def test_missing_mismatched_and_no_list_in_the_issue(self):
        self.assertEqual(wave.ledger_check("Closes #N", ISSUE)[0], "LEDGER-MISSING")
        flag, detail = wave.ledger_check("## Done when\n\n- [x] first thing\n", ISSUE)
        self.assertEqual(flag, "LEDGER-MISMATCH")
        self.assertIn("missing: second thing — owner: implementer", detail)
        self.assertIn("extra: first thing", detail)
        self.assertEqual(wave.ledger_check("## Done when\n\n- [x] a\n", "no list here")[0], "ISSUE-NO-DONE-WHEN")

    def test_a_near_heading_is_named(self):
        flag, detail = wave.ledger_check("## Done when — ledger\n\n- [x] a\n", "## Done when\n\n- [ ] a\n")
        self.assertEqual(flag, "LEDGER-MISSING")
        self.assertIn("exactly `## Done when`", detail)
        self.assertIn("`## Done when — ledger`", detail)


class Tick(unittest.TestCase):
    def test_only_the_ticked_lines_change_byte_for_byte(self):
        new = wave.tick_body(ISSUE, done=["1"], strike=[("fourth thing", "out of scope now")])
        old_lines, new_lines = ISSUE.splitlines(keepends=True), new.splitlines(keepends=True)
        self.assertEqual(len(old_lines), len(new_lines))
        changed = [(o, n) for o, n in zip(old_lines, new_lines) if o != n]
        self.assertEqual(changed, [
            ("- [ ] first thing — owner: implementer\n", "- [x] first thing — owner: implementer\n"),
            ("- [ ] fourth thing\n", "- [ ] ~~fourth thing~~ — dropped: out of scope now\n")])

    def test_by_text_after_whitespace_normalisation_and_twice_is_the_same(self):
        once = wave.tick_body(ISSUE, done=["second thing — owner:   implementer"], strike=[])
        self.assertIn("- [x] second   thing — owner: implementer\n", once)
        self.assertEqual(wave.tick_body(once, done=["second thing — owner: implementer", "3"], strike=[]), once)

    def test_a_ticked_or_struck_line_is_never_unticked_or_restruck(self):
        struck = wave.tick_body(ISSUE, done=[], strike=[("4", "why")])
        self.assertEqual(wave.tick_body(struck, done=["4"], strike=[]), struck)
        self.assertEqual(wave.tick_body(struck, done=[], strike=[("4", "another why")]), struck)
        ticked = wave.tick_body(ISSUE, done=["4"], strike=[])
        self.assertEqual(wave.tick_body(ticked, done=[], strike=[("4", "why")]), ticked)

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "no `## Done when`"):
            wave.tick_body("no list", done=["1"], strike=[])
        with self.assertRaisesRegex(ValueError, "no item 9"):
            wave.tick_body(ISSUE, done=["9"], strike=[])
        with self.assertRaisesRegex(ValueError, "no item 'nothing like it'"):
            wave.tick_body(ISSUE, done=["nothing like it"], strike=[])
        with self.assertRaisesRegex(ValueError, "reason"):
            wave.tick_body(ISSUE, done=[], strike=[("1", "  ")])
        with self.assertRaisesRegex(ValueError, "both done and struck"):
            wave.tick_body(ISSUE, done=["1"], strike=[("first thing — owner: implementer", "why")])


class TickCommand(unittest.TestCase):
    def test_by_index_and_by_text(self):
        for ref in ("1", "it works"):
            with self.subTest(ref=ref):
                p, calls = writes_allowed("tick", "2", "--done", ref, log=f"tick-{ref.replace(' ', '-')}")
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertEqual(edited_body(calls), wave.Repo.get("o/r").issue(2)["body"].replace("- [ ] it works", "- [x] it works"))
                self.assertIn("1 done, 0 dropped, 0 remain", p.stdout)

    def test_strike_pairs_each_item_with_its_why(self):
        p, calls = writes_allowed("tick", "4", "--strike", "2", "--why", "the gate is #2's", log="tick-strike")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("- [ ] ~~the gate passes~~ — dropped: the gate is #2's\n", edited_body(calls))
        p, _ = writes_allowed("tick", "4", "--strike", "2", log="tick-strike-nowhy")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_a_second_tick_changes_nothing_and_writes_nothing(self):
        p, calls = writes_allowed("tick", "8", "--done", "one", "--strike", "two", "--why", "again", log="tick-twice")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("nothing to change", p.stdout)
        self.assertEqual([c for c in calls if c[:1] == ["issue"]], [])

    def test_dry_run_shows_the_line_and_writes_nothing_and_naming_nothing_is_refused(self):
        p, calls = writes_allowed("tick", "2", "--done", "1", "--dry-run", log="tick-dry")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("-> - [x] it works", p.stdout)
        self.assertIn("dry run, nothing written", p.stdout)
        self.assertEqual([c for c in calls if c[:2] == ["issue", "edit"]], [])
        p, _ = writes_allowed("tick", "2", log="tick-empty")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_a_body_without_done_when_is_refused(self):
        p, calls = writes_allowed("tick", "3", "--done", "1", log="tick-none")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("no `## Done when`", p.stderr)
        self.assertEqual([c for c in calls if c[:2] == ["issue", "edit"]], [])


class Verify(unittest.TestCase):
    def verify(self, n):
        p = run_wave("verify", str(n), "--json")
        return p.returncode, json.loads(p.stdout)[0]

    def test_a_pr_with_a_matching_ledger_is_ok(self):
        rc, r = self.verify(20)
        self.assertEqual((rc, r["ok"], r["ledger"]), (0, True, None), r)

    def test_a_pr_without_a_ledger_is_refused(self):
        rc, r = self.verify(31)
        self.assertEqual((rc, r["ok"], r["ledger"]["flag"]), (1, False, "LEDGER-MISSING"), r)
        self.assertIn("LEDGER-MISSING[", run_wave("verify", "31").stdout)

    def test_a_pr_whose_ledger_differs_from_the_issue_is_refused(self):
        rc, r = self.verify(32)
        self.assertEqual((rc, r["ok"], r["ledger"]["flag"]), (1, False, "LEDGER-MISMATCH"), r)
        self.assertIn("missing: the gate passes", r["ledger"]["detail"])

    def test_an_old_heading_is_refused_and_named(self):
        rc, r = self.verify(33)
        self.assertEqual((rc, r["ledger"]["flag"]), (1, "LEDGER-MISSING"), r)
        self.assertIn("`## Ledger — Done when`", r["ledger"]["detail"])

    def test_an_issue_without_done_when_is_refused(self):
        rc, r = self.verify(21)
        self.assertEqual((rc, r["ledger"]["flag"]), (1, "ISSUE-NO-DONE-WHEN"), r)


class Merge(unittest.TestCase):
    def test_a_partial_ledger_is_applied_and_the_issue_reopened_with_one_comment(self):
        before = wave.Repo.get("o/r").issue(53)["body"]
        p, calls = writes_allowed("merge", "34", "--yes", "--ci-timeout", "5", log="merge-34")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue([c for c in calls if c[:2] == ["pr", "merge"]])
        self.assertEqual(edited_body(calls), before
                         .replace("- [ ] p53.txt exists", "- [x] p53.txt exists")
                         .replace("- [ ] docs updated — owner: implementer\n",
                                  "- [ ] ~~docs updated — owner: implementer~~ — dropped: there are no docs yet\n"))
        reopen = [c for c in calls if c[:2] == ["issue", "reopen"]]
        self.assertEqual(len(reopen), 1, calls)
        self.assertIn("ledger applied from PR #34: 1 done, 1 dropped, 1 remain", " ".join(reopen[0]))
        self.assertEqual([c for c in calls if c[:2] == ["issue", "comment"]], [], "one comment: the reopen carries it")
        merged_at = calls.index(next(c for c in calls if c[:2] == ["pr", "merge"]))
        self.assertLess(merged_at, calls.index(next(c for c in calls if c[:2] == ["issue", "edit"])), "applied after the merge")

    def test_a_full_ledger_comments_and_leaves_the_close_to_github(self):
        p, calls = writes_allowed("merge", "20", "--yes", "--ci-timeout", "5", log="merge-20")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        comments = [c for c in calls if c[:2] == ["issue", "comment"]]
        self.assertEqual(len(comments), 1, calls)
        self.assertIn("ledger applied from PR #20: 2 done, 0 dropped", " ".join(comments[0]))
        self.assertEqual([c for c in calls if c[:2] == ["issue", "reopen"]], [])

    def test_merge_refuses_a_pr_without_a_ledger(self):
        p, calls = writes_allowed("merge", "31", "--yes", log="merge-31")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("ledger=", p.stdout)
        self.assertEqual([c for c in calls if c[:2] == ["pr", "merge"]], [])


class Readers(unittest.TestCase):
    def test_next_holds_back_an_issue_whose_every_item_is_ticked_or_struck(self):
        cands = {c["number"]: c for c in wave.candidates(wave.tree(wave.Repo.get("o/r"), 1))}
        self.assertEqual(cands[8]["state"], "done-unclosed")
        self.assertFalse(cands[8]["ready"])
        self.assertIn("close it or add an item", wave.why(cands[8]))

    def test_status_counts_the_boxes_per_leaf_and_sums_them_on_the_epic(self):
        p = run_wave("status", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("- [ ] #2 Ready leaf — READY · 0/1\n", p.stdout)
        self.assertIn("- [ ] #8 Leaf whose every item is ticked or struck — done-unclosed", p.stdout)
        self.assertIn(" · 2/2\n", p.stdout)
        self.assertIn("- [ ] #1 Epic: a recorded epic · 2/5\n", p.stdout)  # leaves 2 (0/1), 4 (0/2), 8 (2/2)

    def test_the_prompt_lists_only_what_remains_and_names_wave(self):
        iss = {"number": 9, "title": "t", "html_url": "u", "body": ISSUE}
        text = wave.render_prompt(wave.Repo.get("o/r"), iss, "feat/9-t", "abc1234", 1)
        remaining = text[text.index("Remaining"):]
        self.assertIn("- [ ] first thing — owner: implementer", remaining)
        self.assertNotIn("third thing", remaining)
        self.assertIn("checked is done — do not redo, do not re-verify unless the gate fails; struck is out — do not reopen", text)
        self.assertIn(f"python3 {WAVE} verify", text)
        self.assertNotIn("{{", text)

    def test_the_fake_agents_pr_carries_a_ledger_verify_accepts(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fake_agent", WAVE.parent / "fake_agent.py")
        fa = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fa)
        body = fa.pr_body(9, ISSUE)
        self.assertIsNone(wave.ledger_check(body, ISSUE), body)


if __name__ == "__main__":
    unittest.main()
