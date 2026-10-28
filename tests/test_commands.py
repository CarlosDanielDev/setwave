"""The commands against a recorded epic (tests/fixtures) and a real, temporary git repo.

The epic o/r#1 has seven leaves, one per state:
  #2 ready        #3 blocked by #2 (and PR 21 closes it)   #4 in progress (PR 20)
  #5 worktree     #6 closed                                #7 ready: its only blocker, #6, is closed
  #8 done-unclosed: open, every Done-when item ticked or struck
"""
import contextlib
import io
import json
import re
import shutil
import subprocess
import unittest
from unittest import mock

from helpers import CLONE, ONE, TMP, TESTS, TWO, WAVE, cross_repos, gh_calls, run_wave, sandbox, wave


def setUpModule():
    sandbox()


def repo():
    return wave.Repo.get("o/r")


class States(unittest.TestCase):
    def test_every_open_leaf_is_in_exactly_one_state(self):
        cands = {c["number"]: c for c in wave.candidates(wave.tree(repo(), 1))}
        self.assertEqual({n: c["state"] for n, c in cands.items()},
                         {2: "ready", 3: "blocked", 4: "in-progress", 5: "worktree", 7: "ready", 8: "done-unclosed"})
        for n, c in cands.items():
            with self.subTest(n=n):
                self.assertEqual(c["ready"], c["state"] == "ready")
                self.assertEqual(c["ready"], not (c["blocked_by"] or c["pr"] or c["worktree"] or c["done"]))
        self.assertEqual(cands[3]["blocked_by"], ["o/r#2"])
        self.assertEqual(cands[3]["pr"], 21, "a blocked leaf with a PR is still blocked: blocked wins")

    def test_premises_name_the_one_that_fails(self):
        failing = {n: [t for t, ok, _ in wave.premises_for(repo(), n) if not ok] for n in (2, 3, 4, 5, 6, 7, 8)}
        self.assertEqual(failing, {
            2: [],
            3: ["every blocker is closed", "no open PR closes it"],
            4: ["no open PR closes it"],
            5: ["no worktree exists for it"],
            6: ["it is open"],
            7: [],
            8: ["an item of its Done when is still open"],
        })

    def test_a_parent_is_not_a_leaf(self):
        # PR 22 closes the epic itself: `verify` calls that a contradiction, `why` shows both premises failing
        self.assertEqual([t for t, ok, _ in wave.premises_for(repo(), 1) if not ok],
                         ["it is a leaf (no sub-issues)", "no open PR closes it"])


class Next(unittest.TestCase):
    def test_ready_leaves_in_order(self):
        p = run_wave("next", "1", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([c["key"] for c in json.loads(p.stdout)["ready"]], ["o/r#2", "o/r#7"])

    def test_batch_limits_the_wave(self):
        p = run_wave("next", "o/r#1", "--batch", "1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("next wave (batch 1): o/r#2\n", p.stdout)


class Why(unittest.TestCase):
    def test_blocked(self):
        p = run_wave("why", "3")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("✗ every blocker is closed: o/r#2 open", p.stdout)
        self.assertIn("NOT READY", p.stdout)

    def test_ready(self):
        p = run_wave("why", "#7")
        self.assertIn("✓ every blocker is closed: o/r#6 closed", p.stdout)
        self.assertIn("READY — every premise holds", p.stdout)


class Verify(unittest.TestCase):
    def verify(self, n):
        p = run_wave("verify", str(n), "--json")
        return p.returncode, json.loads(p.stdout)[0]

    def test_a_clean_pr_is_ok(self):
        rc, r = self.verify(20)
        self.assertEqual((rc, r["ok"], r["issue"]), (0, True, 4), r)

    def test_ai_attribution_in_the_body(self):
        rc, r = self.verify(13)
        self.assertEqual((rc, r["attribution"], r["ok"]), (1, True, False))

    def test_ai_attribution_in_a_commit(self):
        rc, r = self.verify(24)
        self.assertEqual((rc, r["attribution"], r["ok"]), (1, True, False))

    def test_protected_path(self):
        rc, r = self.verify(23)
        self.assertEqual((rc, r["protected_touched"]), (1, ["locked/k.txt"]))

    def test_closing_a_still_blocked_issue(self):
        rc, r = self.verify(21)
        self.assertEqual(rc, 1)
        self.assertEqual(r["contradictions"], ["#3 still blocked by #2: merging it would break the dependency order"])

    def test_closing_a_parent(self):
        rc, r = self.verify(22)
        self.assertEqual(rc, 1)
        self.assertEqual(r["contradictions"], ["#1 is a parent, not a leaf: a PR must close a leaf"])

    def test_a_version_bump_outside_a_release_pr_is_refused(self):
        rc, r = self.verify(35)
        self.assertEqual(rc, 1)
        self.assertEqual(r["ok"], False)
        self.assertEqual(r["version_bumps"], ['.claude-plugin/plugin.json: "version": "9.9.9"'])
        p = run_wave("verify", "35")
        self.assertIn("VERSION-OUTSIDE-RELEASE[.claude-plugin/plugin.json", p.stdout)

    def test_a_release_pr_may_change_the_version(self):
        self.assertIn("version_bumps", self.verify(35)[1], "the guard must exist before the release pass-through means anything")
        rc, r = self.verify(36)
        self.assertEqual((rc, r["ok"], r["version_bumps"]), (0, True, []), r)


class ClosingKeywords(unittest.TestCase):
    """GitHub closes every issue a closing keyword names, in the PR body or in any commit message."""

    def verify(self, n):
        p = run_wave("verify", str(n), "--json")
        return p.returncode, json.loads(p.stdout)[0]

    def test_a_commit_that_closes_another_issue_is_refused(self):
        rc, r = self.verify(27)
        self.assertEqual((rc, r["ok"], r["issue"]), (1, False, 50), r)
        self.assertEqual([(c["where"][:7], c["text"]) for c in r["closes_other"]], [("commit ", "resolve o/r#60")])
        p = run_wave("verify", "27")
        self.assertIn("CLOSES-OTHER[commit ", p.stdout)
        self.assertIn(": resolve o/r#60]", p.stdout)
        self.assertIn("force push", p.stdout, "the refusal names the way out, since a pushed message cannot be reworded")

    def test_another_issue_in_the_body_is_refused(self):
        rc, r = self.verify(30)
        self.assertEqual((rc, r["ok"], r["issue"]), (1, False, 52), r)
        self.assertEqual(r["closes_other"], [{"where": "body", "text": "fixes: o/r#61"}])

    def test_own_issue_only_in_a_commit_is_refused(self):
        rc, r = self.verify(28)
        self.assertEqual((rc, r["ok"], r["closes_only_in_commit"]), (1, False, 51), r)
        self.assertIn("NO-CLOSES-IN-BODY[#51", run_wave("verify", "28").stdout)

    def test_own_issue_in_the_body_and_a_commit_is_ok(self):
        rc, r = self.verify(29)
        self.assertEqual((rc, r["ok"], r["closes_other"], r["closes_only_in_commit"]), (0, True, [], None), r)

    def test_merge_refuses_what_verify_refuses(self):
        for n in (27, 28):
            with self.subTest(pr=n):
                before = len(gh_calls())
                p = run_wave("merge", str(n), "--yes")
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn("Not merging", p.stdout)
                self.assertFalse([c for c in gh_calls()[before:] if c[:2] == ["pr", "merge"]])

    def test_no_hint_or_prompt_example_closes_anything(self):
        for name in ("agent.md", "judge.md"):
            prompt = (wave.TEMPLATES / name).read_text()
            self.assertEqual(wave.CLOSES.findall(prompt), [], f"the raw template names no closing ref")
        iss = {"number": 2, "title": "t", "html_url": "https://github.com/o/r/issues/2"}
        rendered = wave.render_prompt(repo(), iss, "feat/2-t", "0" * 40, 2)
        self.assertEqual([int(n) for _, n in wave.CLOSES.findall(rendered)], [2], "the rendered prompt closes only its issue")
        src = wave.Path(wave.__file__).read_text()
        merge_src = src[src.index("def cmd_merge"):src.index("def cmd_resolve")]
        self.assertEqual(wave.CLOSES.findall(merge_src.replace("{key(repo, n)}", "o/r#9")), [],
                         "a hint printed by merge, once its ref is filled in, closes nothing")


class EscalationPolicy(unittest.TestCase):
    def test_the_prompt_is_self_sufficient_without_host_skills(self):
        src = (wave.TEMPLATES / "agent.md").read_text()
        self.assertIn("Skill ausente não bloqueia", src,
                      "the prompt names host skills as accelerators, never as requirements")
        self.assertNotIn("Invoque via Skill tool, nesta ordem", src,
                         "the old unconditional invocation ordered skills a bare host does not have")
        # each named skill's behavior is spelled out in the always-on rules
        for fragment in ("Prosa comprimida",            # caveman
                         "preguiçosa que funciona",      # ponytail
                         "Test-first",                   # superpowers:test-driven-development
                         "ADR de três frases",           # architecture-designer
                         "Nenhum critério de aceite sem dono",  # team-agent-orchestration
                         "re-audite o fluxo tocado",     # poka-yoke
                         "o disco ganha"):               # graphify/codegraph invariant tail
            self.assertIn(fragment, src, f"the embedded rule survives without the skill: {fragment}")
        self.assertIn("nada aqui depende dele", wave.Path(wave.__file__).read_text(),
                      "the CodeGraph absence policy lives in CODEGRAPH_NOTE, its single owner")
        iss = {"number": 2, "title": "t", "html_url": "https://github.com/o/r/issues/2"}
        out = wave.render_prompt(repo(), iss, "feat/2-t", "0" * 40, 2)
        for fragment in ("Skill ausente não bloqueia", "o disco ganha"):
            self.assertIn(fragment, out, f"`wave prompt` delivers it to every agent: {fragment}")
        self.assertNotIn("{{", out, "an unresolved placeholder in the prompt is the bare-host failure mode")

    def test_section_zero_lists_the_skills_the_handoff_declares(self):
        iss = {"number": 2, "title": "t", "html_url": "https://github.com/o/r/issues/2", "body": ""}
        rendered = lambda: wave.render_prompt(repo(), iss, "feat/2-t", "0" * 40, 2).split("## 1.")[0]
        for s in wave.DEFAULT_SKILLS:
            self.assertIn(f"`{s.split(' (')[0]}`", rendered(), f"section 0 names the default skill: {s}")
        self.assertIn("`setwave:caveman` (ultra)", rendered(), "the level stays outside the Skill-tool name")
        for s in wave.DEFAULT_SKILLS:
            if s.startswith("setwave:"):
                name = s.split(" (")[0].removeprefix("setwave:")
                self.assertTrue((WAVE.parent.parent / "skills" / name / "SKILL.md").exists(), f"the plugin ships {s}")
        with mock.patch.object(wave, "profile_skills", return_value=["repo-skill-74"]):
            self.assertIn("`repo-skill-74`", rendered(), "the profile's skills win in section 0")
            self.assertNotIn("`setwave:ponytail`", rendered(), "a profile replaces the defaults in section 0")
            self.assertIn("Skills: `repo-skill-74`", wave.handoff_block(repo(), iss), "the handoff reads the same source")

    def test_the_prompt_carries_the_pre_done_audit(self):
        src = (wave.TEMPLATES / "agent.md").read_text()
        self.assertIn("## 6. Antes de chamar de pronto", src, "the audit is its own section, before the report")
        self.assertIn("## 7. Relatório final", src, "the report follows it")
        iss = {"number": 2, "title": "t", "html_url": "https://github.com/o/r/issues/2",
               "body": "## Done when\n\n- [ ] two\n"}
        out = wave.render_prompt(repo(), iss, "feat/2-t", "0" * 40, 2)
        self.assertIn("## 6. Antes de chamar de pronto", out)
        p = run_wave("prompt", "2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("## 6. Antes de chamar de pronto", p.stdout, "`wave prompt` delivers the section to every agent")

    def test_the_wave_skill_mandates_respawn_on_the_second_identical_failure(self):
        skill = (WAVE.parent.parent / "skills" / "wave" / "SKILL.md").read_text()
        self.assertIn("--respawn", skill, "step 4 names the flag that marks a respawn in the stamp")
        self.assertIn("twice", skill, "the rule fires on the same failure twice, not on a hunch")

    def test_the_gate_rule_orders_foreground(self):
        src = (wave.TEMPLATES / "agent.md").read_text()
        gate = src.split("## 4. Gate")[1].split("## 5.")[0]
        self.assertIn("foreground", gate, "section 4 runs the gate in the foreground, one step per call")
        for banned in ("run_in_background", "Monitor", "sleep"):
            self.assertIn(banned, gate, f"section 4 names what is banned for the gate: {banned}")
        self.assertIn("Nunca encerre o turno", gate, "ending the turn on a waiting command is the failure itself")
        iss = {"number": 2, "title": "t", "html_url": "https://github.com/o/r/issues/2", "body": ""}
        out = wave.render_prompt(repo(), iss, "feat/2-t", "0" * 40, 2)
        self.assertIn("foreground", out, "`wave prompt` delivers the foreground rule to every agent")

    def test_the_wave_skill_reads_an_interim_report_as_a_stall(self):
        skill = (WAVE.parent.parent / "skills" / "wave" / "SKILL.md").read_text()
        self.assertNotIn("resumes by itself", skill, "an ended turn never resumes by itself: the false promise is gone")
        for fragment in ("wave agents", "SendMessage", "foreground"):
            self.assertIn(fragment, skill, f"an interim report reads as a stall, resumed by hand: {fragment}")
        self.assertIn("--watch", skill, "step 3 arms the stall watchdog")
        self.assertNotIn("`quiet`", skill, "quiet is no longer a verdict `wave agents` prints: stalled replaced it")

    def test_stats_reports_resumes_against_respawns(self):
        log = wave.CONFIG_DIR / "log.jsonl"
        runs = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
        exp_r = sum(r.get("resumes", 0) for r in runs) + 3
        exp_p = sum(r.get("respawns", 0) for r in runs) + 2
        wave.append_log({"at": wave.now_iso(), "cmd": "dispatch", "args": ["7", "--force"],
                         "repo": "o/r", "seconds": 1.0, "exit": 0, "resumes": 3, "respawns": 0})
        wave.append_log({"at": wave.now_iso(), "cmd": "dispatch", "args": ["99", "--force", "--respawn"],
                         "repo": "o/r", "seconds": 1.0, "exit": 0, "resumes": 0, "respawns": 2})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wave.cmd_stats()
        out = buf.getvalue()
        self.assertIn(f"{exp_r} plain resumes", out)
        self.assertIn(f"{exp_p} respawns", out)


class MergeTree(unittest.TestCase):
    def test_conflicting_and_clean_pairs(self):
        r = repo()
        r.fetch()
        self.assertEqual(wave.merge_tree_conflicts(r, "origin/t/a", "origin/t/b"), ["f.txt"])
        self.assertEqual(wave.merge_tree_conflicts(r, "origin/t/a", "origin/t/c"), [])
        self.assertEqual(wave.merge_tree_conflicts(r, "origin/main", "origin/t/b"), [])


class Order(unittest.TestCase):
    def test_plan_has_pairs_chain_and_serial(self):
        plan_file = TMP / "plan.json"
        p = run_wave("order", "11", "12", "13", "--plan", str(plan_file))
        self.assertEqual(p.returncode, 0, p.stderr)
        pl = json.loads(plan_file.read_text())["repos"]["o/r"]
        self.assertEqual(pl["pairs"], {"11x12": ["f.txt"]})
        self.assertEqual(pl["order"], [13, 11, 12])
        self.assertEqual(pl["chain"], [[13, []], [11, []], [12, ["f.txt"]]])
        self.assertEqual(pl["serial"], [13])
        self.assertEqual(pl["against_base"], {"11": [], "12": [], "13": []})
        self.assertEqual(set(pl["heads"]), {"11", "12", "13"})
        self.assertEqual(len(pl["base_sha"]), 40)


class Merge(unittest.TestCase):
    def test_without_yes_nothing_is_merged(self):
        before = len(gh_calls())
        p = run_wave("merge", "20")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("dry run", p.stdout)
        self.assertFalse([c for c in gh_calls()[before:] if c[:2] == ["pr", "merge"]])

    def test_an_issue_with_an_open_blocker_is_refused(self):
        p = run_wave("merge", "21", "--yes")
        self.assertEqual(p.returncode, 1)
        self.assertIn("closes #3, which is still blocked by #2", p.stderr)


class Lint(unittest.TestCase):
    def test_contradictions(self):
        p = run_wave("lint", "6")
        self.assertEqual(p.returncode, 1)
        self.assertIn("CONTRADICTION: handoff branch `feat/99-x` does not carry #6", p.stdout)
        self.assertIn("CONTRADICTION: body closes [9] but this is #6", p.stdout)

    def test_a_visible_result_promise_without_a_verifying_item_warns(self):
        """#119: the caption issue whose Done-when checked only the plumbing."""
        p = run_wave("lint", "65")
        self.assertEqual(p.returncode, 1)
        self.assertIn("WARNING:", p.stdout)
        self.assertIn("promises a visible result", p.stdout)
        self.assertIn("Done-when item verifies", p.stdout)

    def test_an_item_that_verifies_the_screen_silences_the_warning(self):
        p = run_wave("lint", "66")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("o/r#66: [no type] the common contract — ok", p.stdout)
        self.assertNotIn("WARNING:", p.stdout)

    def test_the_promise_may_come_from_the_title_alone(self):
        body = ("Parent: #1\n\nStorage moves to UserDefaults.\n\n## Done when\n\n"
                "- [ ] the flag is stored (`Home.swift:1`) — owner: implementer\n\n"
                "## Handoff\n\n```bash\ngit worktree add -b feat/67-title ../r-67 origin/main\n```\n\nCloses #67\n")
        self.assertEqual(wave.lint_body(body, 67, None, title="Store the flag"), [])
        warned = wave.lint_body(body, 67, None, title="Show a gray hero on the home screen")
        self.assertTrue(any("visible result" in m for m in warned), warned)


class TypedLint(unittest.TestCase):
    """The issue's label picks its template (`templates/issue/<type>.md`); `lint` charges what it adds."""

    def test_a_bug_without_reproduction(self):
        p = run_wave("lint", "60")
        self.assertEqual(p.returncode, 1)
        self.assertIn("o/r#60: [bug] templates/issue/bug.md — missing `## Reprodução`: the steps to reproduce and the observed output", p.stdout)

    def test_a_bug_with_reproduction_is_ok(self):
        p = run_wave("lint", "61")
        self.assertEqual((p.returncode, p.stdout), (0, "o/r#61: [bug] templates/issue/bug.md — ok\n"), p.stdout)

    def test_a_story_needs_an_owner_and_a_check_per_done_when_item(self):
        p = run_wave("lint", "62")
        self.assertEqual(p.returncode, 1)
        self.assertIn("Done when item 1 (it works) has no `— owner:`", p.stdout)
        self.assertIn("Done when item 1 (it works) is not observable: name the test or the command that proves it", p.stdout)
        self.assertIn("Done when item 2 (docs updated)", p.stdout)

    def test_a_chore_says_why_it_is_not_a_story(self):
        p = run_wave("lint", "63")
        self.assertEqual(p.returncode, 1)
        self.assertIn("missing `## Por que não é story`: one line on why this is a chore and not a story", p.stdout)

    def test_a_feature_names_the_stories_that_slice_it(self):
        p = run_wave("lint", "64")
        self.assertEqual(p.returncode, 1)
        self.assertIn("missing `## Fatia`: the stories that slice this feature, one per line", p.stdout)

    def test_no_type_label_warns_and_charges_the_common_contract(self):
        p = run_wave("lint", "2")
        self.assertEqual((p.returncode, p.stdout),
                         (0, "o/r#2: [no type] the common contract — ok (no type label — bug, story, chore, feature — "
                             "its template was not charged)\n"), p.stdout)

    def test_each_template_carries_what_the_lint_charges(self):
        required = {"bug": ["## Reprodução"], "story": ["## Done when", "## Handoff"],
                    "chore": ["## Por que não é story"], "feature": ["## Fatia"]}
        for typ, sections in required.items():
            text = (TESTS.parent / "templates" / "issue" / f"{typ}.md").read_text(encoding="utf-8")
            for s in sections:
                self.assertIn(s, text, typ)

    def test_the_e2e_plan_bodies_lint_clean(self):
        for k, n in (("a", 2), ("b", 3), ("c", 4)):
            body = wave.fill_refs((TESTS / "e2e-plan" / f"{k}.md").read_text().replace("{{run}}", "20260101000000"),
                                  {"e": 1, "a": 2, "b": 3, "c": 4})
            self.assertEqual(wave.lint_body(body, n, None), [], f"{k}.md")


class Dispatch(unittest.TestCase):
    """One seat per agent: `resources` gives every prompt of a batch its own simulator ({N} and
    {WORKTREE} substitute too), the seat is stamped on the worktree and kept across --force/--respawn,
    a short pool or a taken seat refuses with the count, and --addendum closes every prompt verbatim."""

    REG = TMP / "config" / "repos.json"

    def setUp(self):
        self._registry = self.REG.read_text()

    def tearDown(self):
        self.REG.write_text(self._registry)
        for n in (2, 7):
            wt = TMP / f"r-{n}"
            if wt.exists():
                head = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                                      capture_output=True, text=True).stdout.strip()
                subprocess.run(["git", "-C", str(CLONE), "worktree", "remove", "--force", str(wt)],
                               capture_output=True, text=True)
                if head and head != "HEAD":
                    # the branch only ever existed inside this test's throwaway sandbox
                    subprocess.run(["git", "-C", str(CLONE), "branch", "-D", head],
                                   capture_output=True, text=True)
            shutil.rmtree(wt, ignore_errors=True)
            prompt = TMP / "r-handoffs" / f"{n}.md"
            if prompt.exists():
                prompt.unlink()

    def declare(self, **entry):
        reg = json.loads(self.REG.read_text())
        reg["repos"]["o/r"] = {**reg["repos"]["o/r"], **entry}
        self.REG.write_text(json.dumps(reg))

    def stamp(self, n):
        return json.loads((TMP / f"r-{n}" / ".setwave.json").read_text())

    def test_two_prompts_of_one_dispatch_get_different_udids(self):
        self.declare(resources={"SIMULATOR": ["UDID-AAAA", "UDID-BBBB"]},
                     gate=["xcrun simctl boot {SIMULATOR} && xcodebuild -destination 'id={SIMULATOR}' "
                           "-derivedDataPath {WORKTREE}/build/DD-{N} test"])
        p = run_wave("dispatch", "2", "7", "--force", "--no-prepare")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        seats = {}
        for n in (2, 7):
            text = (TMP / "r-handoffs" / f"{n}.md").read_text()
            udid = re.findall(r"id=([A-Z-]+)'", text)
            self.assertTrue(udid, f"{n}.md carries the gate's simulator seat")
            self.assertEqual(len(set(udid)), 1, f"every seat reference in {n}.md agrees")
            seats[n] = udid[0]
            self.assertIn(f"/build/DD-{n} test", text, "{N} is the prompt's own issue")
            self.assertIn(f"{TMP}/r-{n}/build/DD-{n}", text, "{WORKTREE} is the prompt's own tree")
        self.assertNotEqual(seats[2], seats[7], "two agents of one batch never share a seat")
        self.assertEqual(self.stamp(2)["resources"]["SIMULATOR"], seats[2], "the seat is stamped")
        self.assertEqual(self.stamp(7)["resources"]["SIMULATOR"], seats[7], "the seat is stamped")

    def test_a_worktree_keeps_its_seat_over_force_and_respawn(self):
        self.declare(resources={"SIMULATOR": ["UDID-AAAA", "UDID-BBBB"]})
        p = run_wave("dispatch", "2", "--force", "--no-prepare")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        seat = self.stamp(2)["resources"]["SIMULATOR"]
        p = run_wave("dispatch", "2", "--force", "--respawn", "--no-prepare")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.stamp(2)["resources"]["SIMULATOR"], seat,
                         "same worktree, same seat: a resume or respawn does not steal another simulator")

    def test_a_short_pool_refuses_with_the_count(self):
        self.declare(resources={"SIMULATOR": ["UDID-AAAA"]})
        p = run_wave("dispatch", "2", "7", "--force", "--no-prepare")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("2 issues, 1 SIMULATOR free", p.stderr)
        self.assertFalse((TMP / "r-handoffs" / "2.md").exists(), "the refusal precedes every prompt")

    def test_a_seat_another_worktree_holds_is_not_free(self):
        self.declare(resources={"SIMULATOR": ["UDID-AAAA"]})
        p = run_wave("dispatch", "7", "--force", "--no-prepare")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = run_wave("dispatch", "2", "--force", "--no-prepare")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("1 issue, 0 SIMULATOR free", p.stderr)

    def test_the_addendum_rides_every_prompt_and_survives_a_respawn(self):
        add = TMP / "addendum-95.md"
        add.write_text("Always run `xcodegen generate` before the first build.\n")
        p = run_wave("dispatch", "2", "7", "--force", "--no-prepare", "--addendum", str(add))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        tail = "Always run `xcodegen generate` before the first build.\n"
        for n in (2, 7):
            self.assertTrue((TMP / "r-handoffs" / f"{n}.md").read_text().endswith(tail),
                            f"the addendum closes {n}.md verbatim")
            self.assertEqual(self.stamp(n)["addendum"], str(add), "the stamp names the addendum")
        p = run_wave("dispatch", "2", "--force", "--respawn", "--no-prepare")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue((TMP / "r-handoffs" / "2.md").read_text().endswith(tail),
                        "a respawn re-applies the addendum the stamp records")

    def test_an_unresolved_gate_placeholder_refuses_before_any_worktree(self):
        """#119: a gate naming {SIMULATOR} with no seat to resolve it once shipped a literal into the prompt."""
        self.declare(gate=["xcrun simctl boot {SIMULATOR} test"])
        p = run_wave("dispatch", "2", "--force", "--no-prepare")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("{SIMULATOR}", p.stderr)
        self.assertIn("resources", p.stderr)
        self.assertFalse((TMP / "r-handoffs" / "2.md").exists(), "the refusal precedes every prompt")
        self.assertFalse((TMP / "r-2").exists(), "and every worktree")

    def test_wave_prompt_refuses_the_same_placeholder(self):
        self.declare(gate=["xcrun simctl boot {SIMULATOR} test"])
        p = run_wave("prompt", "2")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("{SIMULATOR}", p.stderr)

    def test_a_declared_resource_resolves_and_the_refusal_goes_away(self):
        self.declare(gate=["xcrun simctl boot {SIMULATOR} test"], resources={"SIMULATOR": ["UDID-AAAA"]})
        p = run_wave("dispatch", "2", "--force", "--no-prepare")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("UDID-AAAA", (TMP / "r-handoffs" / "2.md").read_text())

    def test_doctor_warns_when_a_batch_is_pinned_to_one_simulator(self):
        self.declare(gate=["xcodebuild -destination 'platform=iOS Simulator,id=SIM-1' test"])
        one = run_wave("doctor", "--batch", "1")
        self.assertEqual(one.returncode, 0, one.stdout + one.stderr)
        self.assertNotIn("pins a simulator destination", one.stdout)
        two = run_wave("doctor", "--batch", "2")
        self.assertEqual(two.returncode, 0, two.stdout + two.stderr)
        self.assertIn("! batch of 2 over a gate that pins a simulator destination", two.stdout)
        self.assertIn("id={SIMULATOR}", two.stdout, "the warning names the seat way out")

    def test_seats_are_keyed_by_full_ref_across_repos(self):
        cross_repos()
        self._registry = self.REG.read_text()  # the cross-repo registration stays: test_cross_repo builds on it
        seats: dict = {}
        # Repo(...) built by hand — no account, so nothing reaches gh and no other test's log is touched
        for slug, clone, pool in (("a/one", ONE, ["UDID-A1", "UDID-A2"]), ("b/two", TWO, ["UDID-B1", "UDID-B2"])):
            repo = wave.Repo(slug, {"path": str(clone), "base": "main"})
            repo.resources = {"SIMULATOR": pool}
            seats.update(wave.seat_plan(repo, [2]))
        self.assertEqual(sorted(seats), ["a/one#2", "b/two#2"],
                         "a seat belongs to owner/name#N, not to a bare issue number")
        self.assertEqual(seats["a/one#2"]["SIMULATOR"], "UDID-A1", "each ref resolves to its own repo's pool")
        self.assertEqual(seats["b/two#2"]["SIMULATOR"], "UDID-B1", "a/one's seat never answers for b/two")

    def test_doctor_stays_silent_when_the_gate_carries_the_seat_placeholder(self):
        self.declare(gate=["xcodebuild -destination 'platform=iOS Simulator,id={SIMULATOR}' "
                           "-derivedDataPath {WORKTREE}/build/DD-{N} test"])
        two = run_wave("doctor", "--batch", "2")
        self.assertEqual(two.returncode, 0, two.stdout + two.stderr)
        self.assertNotIn("pins a simulator destination", two.stdout)

    def test_a_pool_with_duplicate_values_is_refused(self):
        self.declare(resources={"SIMULATOR": ["UDID-AAAA", "UDID-AAAA"]})
        p = run_wave("dispatch", "2", "7", "--force", "--no-prepare")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("DUPLICATE-SEAT", p.stderr)
        self.assertFalse((TMP / "r-handoffs" / "2.md").exists(), "the refusal precedes every prompt")

    def test_the_seat_guarantee_is_declared_and_pinned(self):
        row = next(r for r in wave.GUARANTEES if "mesmo recurso" in r[0])
        self.assertEqual(row[1], "cmd_dispatch -> seat_plan")
        self.assertTrue(row[3], "the tests above pin it: removing the guard must fail them")


class NoWrites(unittest.TestCase):
    def test_no_command_tried_to_write_to_github(self):
        run_wave("next", "1")  # at least one call, so the log exists
        writes = [c for c in gh_calls() if (c[:1] == ["api"] and "-X" in c and c[c.index("-X") + 1] != "GET")
                  or c[:2] in (["pr", "merge"], ["issue", "create"], ["issue", "edit"], ["issue", "close"], ["issue", "comment"])]
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
