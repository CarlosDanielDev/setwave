"""`wave adopt` against the fake gh: a milestone of five issues becomes an epic the plugin can run.

The fixtures live in tests/adopt-fixtures-11/ and are served through FAKE_GH_FIXTURES, so the
shared tests/fixtures stay untouched. The milestone M1 in o/r carries five open issues:
  #21  Done when, `depends on #22` in the body
  #22  Done when; a comment says `blocked by #23`
  #23  Done when, `needs o/y#7` — the blocker lives in another repo
  #24  Done when, already a sub-issue of o/r#9 (fixture `..._24_parent.json`)
  #25  no `## Done when`, body says `after #21`
Writes go through with FAKE_GH_API_WRITE (api POSTs), FAKE_GH_CREATE and FAKE_GH_EDIT; every
call lands in a per-run log the tests read. A scenario that needs GitHub to *already* contain
what a first run wrote mutates a copy of the fixture directory between runs.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import TMP, WAVE, git, sandbox, wave

FIXTURES = TMP / "adopt-src-11"
LOG = TMP / "adopt-log-11.json"


def setUpModule():
    sandbox()
    if not FIXTURES.exists():
        shutil.copytree(wave.Path(__file__).parent / "adopt-fixtures-11", FIXTURES)
    reg_file = TMP / "config" / "repos.json"
    reg = json.loads(reg_file.read_text())
    if "o/y" not in reg["repos"]:
        bare = TMP / "remote" / "o" / "y.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "--bare", str(bare), cwd=TMP)
        x = TMP / "y"
        git("clone", "-q", str(bare), str(x), cwd=TMP)
        (x / ".wave.json").write_text(json.dumps({"base": "main"}))
        git("add", ".", cwd=x)
        git("commit", "-q", "-m", "base", cwd=x)
        git("push", "-q", "origin", "main", cwd=x)
        reg["repos"]["o/y"] = {"path": str(x)}
        reg_file.write_text(json.dumps(reg))


def scenario(name: str) -> wave.Path:
    """A fresh copy of the fixture directory a test may mutate into any state GitHub needs to be in."""
    d = TMP / name
    shutil.copytree(FIXTURES, d, dirs_exist_ok=True)
    return d


def adopt(args, fixtures: wave.Path, log=LOG):
    """`wave adopt` as a user runs it, with writes allowed through the fake gh; returns (run, calls)."""
    for f in (LOG, TMP / "adopt-log-11-env.json"):
        f.unlink(missing_ok=True)
    env = {**os.environ, "FAKE_GH_FIXTURES": str(fixtures), "FAKE_GH_API_WRITE": "1",
           "FAKE_GH_CREATE": "30", "FAKE_GH_EDIT": "1",
           "FAKE_GH_LOG": str(LOG), "FAKE_GH_ENV_LOG": str(TMP / "adopt-log-11-env.json")}
    p = subprocess.run([sys.executable, str(WAVE), "adopt", *args], cwd=sandbox(),
                       capture_output=True, text=True, env=env)
    calls = [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []
    return p, calls


def posts(calls, path: str) -> list[list[str]]:
    return [c for c in calls if c[:1] == ["api"] and "-X" in c and c[c.index("-X") + 1] == "POST"
            and any(path in x for x in c)]


def field(call: list[str], name: str) -> str:
    """The value of `-F name=value` (repo.api passes the flag and the field as separate tokens)."""
    for i, x in enumerate(call):
        if x in ("-F", "-f") and call[i + 1].startswith(f"{name}="):
            return call[i + 1].split("=", 1)[1]
    raise AssertionError(f"no -F {name}=... in {call}")


def wired_by(calls) -> dict[str, int]:
    """{blocked issue: blocker's issue_id} over the blocked_by POSTs a run made."""
    return {c[3].split("/issues/")[1].split("/dependencies")[0]: int(field(c, "issue_id"))
            for c in posts(calls, "dependencies/blocked_by")}


class Pure(unittest.TestCase):
    def test_textual_deps_finds_the_four_verbs_and_cross_repo_refs_only(self):
        text = ("Depends on #22. BLOCKED BY o/y#7. needs #9 first; after #21 lands.\n"
                "closes #14, fixes #15, relates to #16, plain #17, needs-review #18.")
        self.assertEqual(wave.textual_deps(text, "o/r"),
                         {("o/r", 22), ("o/y", 7), ("o/r", 9), ("o/r", 21)})

    def test_a_mention_without_a_verb_is_not_a_dependency(self):
        self.assertEqual(wave.textual_deps("see #14 and owner/name#15", "o/r"), set())


class Selection(unittest.TestCase):
    def test_pick_by_milestone_title_and_by_label_name(self):
        issues = [{"milestone": {"title": "M1"}, "labels": []},
                  {"milestone": {"title": "M2"}, "labels": [{"name": "backend"}]},
                  {"milestone": None, "labels": [{"name": "backend"}, {"name": "epic"}]}]
        self.assertEqual([i["milestone"]["title"] for i in wave.pick_by_selector(issues, "M1", None)], ["M1"])
        self.assertEqual(len(wave.pick_by_selector(issues, None, "backend")), 2)


class AdoptMilestone(unittest.TestCase):
    def test_five_issues_become_sub_issues_with_their_dependencies_wired(self):
        p, calls = adopt(["--milestone", "M1"], scenario("adopt-run1-11"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('epic: o/r#30 (created from milestone "M1"', p.stdout)
        created = next(c for c in calls if c[:2] == ["issue", "create"])
        self.assertEqual(created[created.index("--title") + 1], "M1")
        self.assertIn("epic", created, "the created epic carries the `epic` label")
        # four attached: #21 #22 #23 #25 — #24 already has a parent and is reported, not moved
        self.assertEqual({int(field(c, "sub_issue_id")) for c in posts(calls, "issues/30/sub_issues")},
                         {2121, 2222, 2323, 2525})
        self.assertIn("already has a parent: o/r#9", p.stdout)
        # four dependencies wired: #21<-#22 (body), #22<-#23 (a comment), #23<-o/y#7 (cross-repo), #25<-#21 (body)
        self.assertEqual(wired_by(calls), {"21": 2222, "22": 2323, "23": 7077, "25": 2121})
        self.assertIn("(from a comment)", p.stdout)
        self.assertIn("blocked by o/y#7", p.stdout)
        # #25 has no `## Done when`: a comment asks for criteria, the body is never edited
        comments = [c for c in calls if c[:2] == ["issue", "comment"]]
        self.assertEqual([c[2] for c in comments], ["25"])
        self.assertNotIn("issue", " ".join(c for c in calls if c[:2] == ["issue", "edit"]))
        self.assertIn("lint: ## Done when", p.stdout, "the lint output is part of the adopt report")

    def test_a_second_run_adds_nothing_twice(self):
        d = scenario("adopt-run2-11")
        p, calls = adopt(["--milestone", "M1"], d)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        issues = json.loads((FIXTURES / "issue_list_o_r.json").read_text())
        epic = dict(issues[0], number=30, id=3030, title="M1", milestone=None, labels=[{"name": "epic"}])
        subs = [{"number": i["number"], "repository_url": i["repository_url"], "state": "open"}
                for i in issues[:3] + issues[4:]]  # what run 1 attached: all but #24, which has a parent
        (d / "issue_list_o_r.json").write_text(json.dumps(issues + [epic]))
        (d / "repos_o_r_issues_30_sub_issues.json").write_text(json.dumps(subs))
        by = {21: [22], 22: [23], 23: [7], 25: [21]}
        for n, blockers in by.items():
            repo = "o/y" if n == 23 else "o/r"
            (d / f"repos_o_r_issues_{n}_dependencies_blocked_by.json").write_text(json.dumps(
                [{"number": b, "repository_url": f"https://api.github.com/repos/{repo}", "state": "open"} for b in blockers]))
        (d / "repos_o_r_issues_25_comments.json").write_text(json.dumps(
            [{"body": "`wave adopt`: this issue has no `## Done when` list — add the observable criteria"}]))
        p, calls = adopt(["--milestone", "M1"], d)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(posts(calls, "sub_issues"), [], "every sub-issue is already attached: nothing POSTed")
        self.assertEqual(posts(calls, "blocked_by"), [], "every dependency is already wired: nothing POSTed")
        self.assertEqual([c for c in calls if c[:2] in (["issue", "create"], ["issue", "comment"])], [])
        self.assertIn("already a sub-issue", p.stdout)
        self.assertIn("already blocked by", p.stdout)
        self.assertIn("epic: o/r#30 (existing", p.stdout)

    def test_explicit_epic_is_used_and_never_created(self):
        p, calls = adopt(["--milestone", "M1", "--epic", "1"], scenario("adopt-epic-11"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("epic: o/r#1 (existing)", p.stdout)
        self.assertFalse([c for c in calls if c[:2] == ["issue", "create"]])
        self.assertEqual({int(field(c, "sub_issue_id")) for c in posts(calls, "issues/1/sub_issues")},
                         {2121, 2222, 2323, 2525})

    def test_explicit_refs_select_only_the_issues_named(self):
        p, calls = adopt(["21", "25", "--epic", "1"], scenario("adopt-refs-11"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual({int(field(c, "sub_issue_id")) for c in posts(calls, "issues/1/sub_issues")},
                         {2121, 2525})
        self.assertEqual(wired_by(calls), {"21": 2222, "25": 2121}, "#22 is not selected but still blocks #21")

    def test_dry_run_writes_nothing_and_names_what_it_could_not_read(self):
        d = scenario("adopt-dry-11")
        issues = json.loads((d / "issue_list_o_r.json").read_text())
        issues[4]["body"] += "\nneeds #99\n"  # o/r#99 has no fixture: unreadable, like a deleted issue
        (d / "issue_list_o_r.json").write_text(json.dumps(issues))
        p, calls = adopt(["--milestone", "M1", "--dry-run"], d)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse([c for c in calls if c[:1] == ["api"] and "-X" in c and c[c.index("-X") + 1] != "GET"], calls)
        self.assertFalse([c for c in calls if c[:2] in (["issue", "create"], ["issue", "comment"])])
        self.assertIn("would create", p.stdout)
        self.assertIn("could not read: o/r#99", p.stdout)
        self.assertIn("nothing written", p.stdout)

    def test_an_unreadable_blocker_is_reported_and_the_rest_is_still_wired(self):
        d = scenario("adopt-unreadable-11")
        issues = json.loads((d / "issue_list_o_r.json").read_text())
        issues[2]["body"] += "\nneeds #99\n"
        (d / "issue_list_o_r.json").write_text(json.dumps(issues))
        p, calls = adopt(["--milestone", "M1"], d)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("could not read: o/r#99", p.stdout)
        self.assertEqual(wired_by(calls), {"21": 2222, "22": 2323, "23": 7077, "25": 2121})


class AdoptRefusals(unittest.TestCase):
    def test_no_selector_and_two_selectors_are_refused(self):
        for args in ([], ["--milestone", "M1", "--label", "x"], ["--milestone", "M1", "21"]):
            with self.subTest(args=args):
                p, calls = adopt(args, scenario("adopt-refuse-11"))
                self.assertNotEqual(p.returncode, 0, p.stdout)
                self.assertFalse([c for c in calls if c[:2] == ["issue", "create"]])

    def test_a_milestone_nobody_carries_is_refused(self):
        p, _ = adopt(["--milestone", "Nope"], scenario("adopt-none-11"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("no open issue", p.stderr + p.stdout)


if __name__ == "__main__":
    unittest.main()
