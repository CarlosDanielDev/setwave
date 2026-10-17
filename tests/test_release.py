"""`wave release`: prepare the Release PR and stop. Four paths: dry-run, the real run, the refusals, and "never merges or tags".

A throwaway checkout o/rel (a bare remote, tag v0.7.1, then a merge-commit PR and a squash PR on main)
and o/bare (no plugin manifest). The fake gh accepts `pr create` only when FAKE_GH_PR_CREATE is set.
"""
import json
import os
import subprocess
import sys
import unittest

from helpers import TMP, WAVE, git

REL, BARE = TMP / "rel", TMP / "bare"
MANIFEST = '{\n  "name": "o",\n  "version": "0.7.1"\n}\n'
MARKET = '{\n  "plugins": [\n    {\n      "name": "o",\n      "version": "0.7.1"\n    }\n  ]\n}\n'


def mkrepo(slug: str, clone, files: dict[str, str]):
    bare = TMP / "remote" / f"{slug}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(clone), cwd=TMP)
    files[".wave.json"] = json.dumps({"base": "main"})
    for name, text in files.items():
        (clone / name).parent.mkdir(exist_ok=True)
        (clone / name).write_text(text)
    git("add", ".", cwd=clone)
    git("commit", "-q", "-m", "base", cwd=clone)
    git("push", "-q", "origin", "main", cwd=clone)


def setUpModule():
    (TMP / "config").mkdir(exist_ok=True)
    (TMP / "config" / "repos.json").write_text(json.dumps({"search_paths": [str(TMP)], "repos": {}}))
    mkrepo("o/rel", REL, {".claude-plugin/plugin.json": MANIFEST, ".claude-plugin/marketplace.json": MARKET})
    git("tag", "v0.7.1", cwd=REL)
    git("push", "-q", "origin", "v0.7.1", cwd=REL)
    git("checkout", "-q", "-b", "feat/9-x", cwd=REL)
    (REL / "x.txt").write_text("x\n")
    git("add", ".", cwd=REL)
    git("commit", "-q", "-m", "Add x", cwd=REL)
    git("checkout", "-q", "main", cwd=REL)
    git("merge", "-q", "--no-ff", "-m", "Merge pull request #9 from o/feat/9-x", "-m", "Add the x thing", "feat/9-x", cwd=REL)
    (REL / "y.txt").write_text("y\n")
    git("add", ".", cwd=REL)
    git("commit", "-q", "-m", "Fix y (#10)", cwd=REL)
    git("push", "-q", "origin", "main", cwd=REL)
    mkrepo("o/bare", BARE, {"README.md": "no plugin here\n"})


def release(*args, cwd=REL, create=False):
    log = TMP / "gh-release.log"
    log.unlink(missing_ok=True)
    env = {**os.environ, "FAKE_GH_LOG": str(log)}
    if create:
        env["FAKE_GH_PR_CREATE"] = "1"
    p = subprocess.run([sys.executable, str(WAVE), "release", *args], cwd=cwd, capture_output=True, text=True, env=env)
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return p, calls


def head(cwd=REL):
    return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True).stdout.strip()


class Release(unittest.TestCase):
    def test_dry_run_prints_branch_manifest_diff_and_one_note_per_pr_and_writes_nothing(self):
        p, calls = release("0.8.1", "--dry-run", create=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("release/0.8.1", p.stdout)
        for f in ("plugin.json", "marketplace.json"):
            self.assertIn(f"+++ b/.claude-plugin/{f}", p.stdout)
        self.assertEqual(p.stdout.count('-  "version": "0.7.1"') + p.stdout.count('-      "version": "0.7.1"'), 2, p.stdout)
        self.assertIn('+  "version": "0.8.1"', p.stdout)
        self.assertIn("- Add the x thing (#9)", p.stdout)
        self.assertIn("- Fix y (#10)", p.stdout)
        self.assertEqual(head(), "main", "dry-run creates no branch")
        self.assertIn("0.7.1", (REL / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual([c for c in calls if c[:2] == ["pr", "create"]], [], "dry-run opens no PR")

    def test_real_run_branches_commits_pushes_opens_the_pr_and_stops(self):
        self.addCleanup(git, "switch", "-q", "main", cwd=REL)
        p, calls = release("0.8.2", create=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(head(), "release/0.8.2")
        self.assertIn('"version": "0.8.2"', (REL / ".claude-plugin" / "plugin.json").read_text())
        subject = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=REL, capture_output=True, text=True).stdout
        self.assertEqual(subject.strip(), "Release 0.8.2")
        remote = subprocess.run(["git", "ls-remote", "--heads", "origin", "release/0.8.2"], cwd=REL, capture_output=True, text=True).stdout
        self.assertTrue(remote.strip(), "the branch is pushed")
        (create,) = [c for c in calls if c[:2] == ["pr", "create"]]
        self.assertTrue(create[create.index("--title") + 1].startswith("Release 0.8.2: "), create)
        self.assertIn("- Fix y (#10)", create[create.index("--body") + 1])
        self.assertEqual([c for c in calls if c[:2] in (["pr", "merge"], ["release", "create"])], [], "merge and gh release stay the owner's")
        tags = subprocess.run(["git", "tag", "--list", "v0.8.2"], cwd=REL, capture_output=True, text=True).stdout
        self.assertEqual(tags.strip(), "", "no tag is created")
        self.assertIn("git tag v0.8.2", p.stdout, "the owner's remaining steps are printed")
        self.assertIn("/pull/99", p.stdout)

    def refused(self, code, *args, cwd=REL):
        p, calls = release(*args, cwd=cwd, create=True)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(f"{code}:", p.stderr)
        self.assertEqual([c for c in calls if c[:2] == ["pr", "create"]], [], "a refusal opens no PR")
        self.assertEqual(head(cwd), "main", "a refusal creates no branch")

    def test_a_dirty_tree_is_refused(self):
        (REL / "scratch.txt").write_text("wip\n")
        self.addCleanup((REL / "scratch.txt").unlink)
        self.refused("RELEASE-DIRTY-TREE", "0.9.0")

    def test_a_version_not_above_the_last_tag_is_refused(self):
        self.refused("RELEASE-VERSION-NOT-NEWER", "0.7.1")
        self.refused("RELEASE-VERSION-NOT-NEWER", "0.7.0")

    def test_a_checkout_without_plugin_manifests_is_refused(self):
        self.refused("RELEASE-WRONG-WORKSPACE", "0.9.0", cwd=BARE)

    def test_a_malformed_version_is_refused(self):
        self.refused("RELEASE-BAD-VERSION", "0.9")


if __name__ == "__main__":
    unittest.main()
