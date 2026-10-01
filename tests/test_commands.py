"""The commands against a recorded epic (tests/fixtures) and a real, temporary git repo.

The epic o/r#1 has six leaves, one per state:
  #2 ready        #3 blocked by #2 (and PR 21 closes it)   #4 in progress (PR 20)
  #5 worktree     #6 closed                                #7 ready: its only blocker, #6, is closed
"""
import json
import unittest

from helpers import TMP, gh_calls, run_wave, sandbox, wave


def setUpModule():
    sandbox()


def repo():
    return wave.Repo.get("o/r")


class States(unittest.TestCase):
    def test_every_open_leaf_is_in_exactly_one_state(self):
        cands = {c["number"]: c for c in wave.candidates(wave.tree(repo(), 1))}
        self.assertEqual({n: c["state"] for n, c in cands.items()},
                         {2: "ready", 3: "blocked", 4: "in-progress", 5: "worktree", 7: "ready"})
        for n, c in cands.items():
            with self.subTest(n=n):
                self.assertEqual(c["ready"], c["state"] == "ready")
                self.assertEqual(c["ready"], not (c["blocked_by"] or c["pr"] or c["worktree"]))
        self.assertEqual(cands[3]["blocked_by"], ["o/r#2"])
        self.assertEqual(cands[3]["pr"], 21, "a blocked leaf with a PR is still blocked: blocked wins")

    def test_premises_name_the_one_that_fails(self):
        failing = {n: [t for t, ok, _ in wave.premises_for(repo(), n) if not ok] for n in (2, 3, 4, 5, 6, 7)}
        self.assertEqual(failing, {
            2: [],
            3: ["every blocker is closed", "no open PR closes it"],
            4: ["no open PR closes it"],
            5: ["no worktree exists for it"],
            6: ["it is open"],
            7: [],
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
        prompt = (wave.TEMPLATES / "agent.md").read_text()
        self.assertEqual(wave.CLOSES.findall(prompt), [], "the raw template names no closing ref")
        iss = {"number": 2, "title": "t", "html_url": "https://github.com/o/r/issues/2"}
        rendered = wave.render_prompt(repo(), iss, "feat/2-t", "0" * 40, 2)
        self.assertEqual([int(n) for _, n in wave.CLOSES.findall(rendered)], [2], "the rendered prompt closes only its issue")
        src = wave.Path(wave.__file__).read_text()
        merge_src = src[src.index("def cmd_merge"):src.index("def cmd_resolve")]
        self.assertEqual(wave.CLOSES.findall(merge_src.replace("{key(repo, n)}", "o/r#9")), [],
                         "a hint printed by merge, once its ref is filled in, closes nothing")


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
    def test_a_complete_body(self):
        p = run_wave("lint", "2")
        self.assertEqual((p.returncode, p.stdout), (0, "o/r#2: ok\n"))

    def test_contradictions(self):
        p = run_wave("lint", "6")
        self.assertEqual(p.returncode, 1)
        self.assertIn("CONTRADICTION: handoff branch `feat/99-x` does not carry #6", p.stdout)
        self.assertIn("CONTRADICTION: body closes [9] but this is #6", p.stdout)


class NoWrites(unittest.TestCase):
    def test_no_command_tried_to_write_to_github(self):
        run_wave("next", "1")  # at least one call, so the log exists
        writes = [c for c in gh_calls() if (c[:1] == ["api"] and "-X" in c and c[c.index("-X") + 1] != "GET")
                  or c[:2] in (["pr", "merge"], ["issue", "create"], ["issue", "edit"], ["issue", "close"], ["issue", "comment"])]
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
