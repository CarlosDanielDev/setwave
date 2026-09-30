"""The registry names a repo's main checkout, never one of its worktrees.

A repo o/wt-main with its main checkout W and a linked worktree W-25. Any command run inside W-25 (here
`guarantees`, this repo's own gate step) must leave the registry pointing at W; a path that is missing
or names a worktree is repaired to W. Inside the worktree, commands still reach the worktree's siblings.
"""
import json
import unittest

from helpers import TMP, git, run_wave, sandbox, wave

W = TMP / "wt-main"
WT = TMP / "wt-main-25"
REG = TMP / "config" / "repos.json"


def setUpModule():
    sandbox()
    if W.exists():
        return
    bare = TMP / "remote" / "o" / "wt-main.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(W), cwd=TMP)
    (W / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true"]}))
    git("add", ".", cwd=W)
    git("commit", "-q", "-m", "base", cwd=W)
    git("push", "-q", "origin", "main", cwd=W)
    git("worktree", "add", "-q", "-b", "fix/25-x", str(WT), "origin/main", cwd=W)


def path_of(slug: str) -> str | None:
    return json.loads(REG.read_text())["repos"].get(slug, {}).get("path")


def set_path(slug: str, path: str) -> None:
    reg = json.loads(REG.read_text())
    reg["repos"].setdefault(slug, {})["path"] = path
    REG.write_text(json.dumps(reg))


class RegistryHoldsTheMainCheckout(unittest.TestCase):
    def tearDown(self):
        set_path("o/wt-main", str(W))

    def test_a_command_inside_a_worktree_leaves_the_main_checkout_in_the_registry(self):
        set_path("o/wt-main", str(W))
        p = run_wave("guarantees", cwd=WT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(path_of("o/wt-main"), str(W))

    def test_a_worktree_path_left_in_the_registry_is_repaired(self):
        set_path("o/wt-main", str(WT))
        self.assertEqual(run_wave("guarantees", cwd=WT).returncode, 0)
        self.assertEqual(path_of("o/wt-main"), str(W))

    def test_a_missing_path_is_repaired(self):
        set_path("o/wt-main", str(TMP / "gone"))
        self.assertEqual(run_wave("guarantees", cwd=W).returncode, 0)
        self.assertEqual(path_of("o/wt-main"), str(W))

    def test_inside_a_worktree_the_repo_root_is_the_main_checkout(self):
        wave.Repo._cache.pop("o/wt-main", None)
        r = wave.Repo.from_cwd(str(WT))
        self.assertEqual(r.root, W)
        self.assertEqual(r.worktree(25), WT, "siblings are derived from the main checkout, not from the worktree")


if __name__ == "__main__":
    unittest.main()
