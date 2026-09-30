"""The gate read from CI: `run:` lines and `uses:` steps, against tests/fixtures/workflow_actions.yml.

A second repo, o/w, carries that workflow and no `gate` key in its `.wave.json`, so the gate is
the one read from CI; `doctor` and `facts` run inside it as a user would.
"""
import json
import shutil
import unittest

from helpers import TMP, git, run_wave, sandbox, wave, TESTS

W = TMP / "w"


def setUpModule():
    sandbox()
    if W.exists():
        return
    bare = TMP / "remote" / "o" / "w.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(W), cwd=TMP)
    (W / ".github" / "workflows").mkdir(parents=True)
    shutil.copy(TESTS / "fixtures" / "workflow_actions.yml", W / ".github" / "workflows" / "ci.yml")
    (W / ".wave.json").write_text(json.dumps({"base": "main"}))
    git("add", ".", cwd=W)
    git("commit", "-q", "-m", "base", cwd=W)
    git("push", "-q", "origin", "main", cwd=W)


def repo(**entry):
    return wave.Repo("o/w", {"path": str(W), **entry})


class CiGate(unittest.TestCase):
    def test_run_lines_and_known_actions_are_commands(self):
        cmds, unknown = repo().ci_gate()
        self.assertEqual(cmds, [("cargo test", "run:"), ("cargo deny check", "uses:")])
        self.assertEqual(unknown, ["acme/secret-lint@v1"],
                         "checkout and rust-toolchain are ignored, a local reusable workflow is read on its own")

    def test_the_gate_is_the_ci_commands(self):
        self.assertEqual(repo().gate(), ["cargo test", "cargo deny check"])

    def test_ignore_actions_silences_an_unknown_action(self):
        self.assertEqual(repo(ignore_actions=["acme/*"]).ci_gate()[1], [])
        self.assertEqual(repo(ignore_actions="nope/*").ci_gate()[1], ["acme/secret-lint@v1"],
                         "a bare string is one pattern: its letters must not become `*`, silencing everything")

    def test_a_declared_gate_is_config(self):
        self.assertEqual(repo(gate=["make check"]).gate_sources(), [("make check", "config")])


class Doctor(unittest.TestCase):
    def test_an_unknown_action_is_named(self):
        p = run_wave("doctor", cwd=W)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("  ✓ gate is declared and real: cargo test && cargo deny check\n", p.stdout)
        self.assertIn("  ! CI runs `acme/secret-lint@v1` and the gate does not: "
                      "if it is a gate step, declare its command in `.wave.json`; then add it to `ignore_actions`\n", p.stdout)


class Facts(unittest.TestCase):
    def test_each_gate_command_carries_its_source(self):
        p = run_wave("facts", cwd=W)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["gate_sources"], {"cargo test": "run:", "cargo deny check": "uses:"})


if __name__ == "__main__":
    unittest.main()
