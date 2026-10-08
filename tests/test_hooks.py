"""The guard hooks (hooks/guards.py) and their registration (`wave hooks install|status`).

Every judge is fed the JSON Claude Code pipes to a PreToolUse hook and answers
through its exit code alone: 2 = the command is refused (the reason goes to
stderr, which is what the agent sees), 0 = pass — also on anything the hook
cannot parse or does not survive: a guard must never wedge the loop it guards.
"""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import types
import unittest
import unittest.mock
from pathlib import Path

from helpers import CLONE, TMP, TESTS, run_wave, sandbox, wave

GUARDS = TESTS.parent / "hooks" / "guards.py"
SETTINGS = CLONE / ".claude" / "settings.json"

_spec = importlib.util.spec_from_file_location("guards", GUARDS)
guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guards)


def line(text: str, needle: str) -> str:
    return next((l for l in text.splitlines() if needle in l), "")


def run_guard(name: str, payload, *args) -> subprocess.CompletedProcess:
    raw = json.dumps(payload) if isinstance(payload, dict) else payload
    return subprocess.run([sys.executable, str(GUARDS), name, *args], input=raw, capture_output=True, text=True)


def bash_event(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}


def task_event(prompt, subagent_type="general-purpose") -> dict:
    return {"tool_name": "Task", "tool_input": {"prompt": prompt, "subagent_type": subagent_type}}


def write_settings(data: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n")


def reset_settings() -> None:
    if SETTINGS.exists():
        SETTINGS.unlink()
    for bak in SETTINGS.parent.glob("settings.json.bak-*"):
        bak.unlink()


def backups() -> list[str]:
    return sorted(p.name for p in SETTINGS.parent.glob("settings.json.bak-*"))


def guard_commands(data: dict) -> list[tuple[str, str]]:
    out = []
    for block in data.get("hooks", {}).get("PreToolUse", []):
        for h in block.get("hooks", []):
            parts = (h.get("command") or "").split()
            if len(parts) == 3 and parts[1] == str(GUARDS):
                out.append((parts[2], block.get("matcher")))
    return out


FOREIGN = {"model": "opus", "hooks": {"PreToolUse": [
    {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo keep-me-35"}]}]}}


class Attribution(unittest.TestCase):
    def test_denies_the_trailer_in_a_commit_command(self):
        p = run_guard("attribution", bash_event('git commit -m "Add x\\n\\nCo-Authored-By: Claude <c@x.com>"'))
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("attribution-guard", p.stderr)
        self.assertIn("Co-Authored-By: Claude", p.stderr)

    def test_catches_every_pattern_the_plugin_refuses(self):
        for text in ("Co-Authored-By: Claude <noreply@anthropic.com>", "Generated with [Claude Code](https://claude.com/claude-code)",
                     "git commit -m \"x\" --trailer \"Signed-off-by: noreply@anthropic.com\""):
            with self.subTest(text=text):
                self.assertEqual(run_guard("attribution", bash_event(f'git commit -m "{text}"')).returncode, 2)

    def test_denies_it_hiding_in_a_body_file(self):
        body = TMP / "pr-body-35.md"
        body.write_text("What changed.\n\nGenerated with Claude Code.\n")
        p = run_guard("attribution", bash_event(f"gh pr create --title t --body-file {body}"))
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("attribution-guard", p.stderr)

    def test_allows_a_clean_commit(self):
        p = run_guard("attribution", bash_event('git commit -m "Add x"'))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr, "")

    def test_a_command_that_reads_the_pattern_is_not_writing_it(self):
        for cmd in ('grep -F "Co-Authored-By: Claude" notes.md', 'git log --grep="Co-Authored-By: Claude"',
                    'echo "checking: noreply@anthropic.com"'):
            with self.subTest(cmd=cmd):
                self.assertEqual(run_guard("attribution", bash_event(cmd)).returncode, 0, cmd)

    def test_passes_through_what_it_cannot_judge(self):
        for payload in ("{not json", {"tool_name": "Read", "tool_input": {"command": "x"}}, {"tool_name": "Bash", "tool_input": {}}):
            with self.subTest(payload=payload):
                self.assertEqual(run_guard("attribution", payload).returncode, 0)


class ForbiddenGit(unittest.TestCase):
    DENIED = ["git gc --prune", "git gc --prune=now", "git reflog expire --expire=now --all", "git stash", "git stash pop",
              "git reset --hard origin/main", "git clean -f", "git clean -fd build", "git push --force origin x",
              "git push -f origin x", "git branch -D feat/35-guard-hooks", "rm -rf build", "rm -fr build",
              "rm --recursive --force build"]
    ALLOWED = ["git push origin x", "git branch -d feat/35-guard-hooks", "git clean -n", "git gc", "git reset --soft HEAD~1",
               "git checkout -- f.txt", "rm build.log", "rm -r drafts"]

    def test_denies_each_with_the_safe_alternative(self):
        for cmd in self.DENIED:
            with self.subTest(cmd=cmd):
                p = run_guard("forbidden-git", bash_event(cmd))
                self.assertEqual(p.returncode, 2, p.stderr)
                self.assertIn("forbidden-git-guard", p.stderr)
                self.assertIn("instead", p.stderr)

    def test_allows_the_harmless_forms(self):
        for cmd in self.ALLOWED:
            with self.subTest(cmd=cmd):
                p = run_guard("forbidden-git", bash_event(cmd))
                self.assertEqual(p.returncode, 0, p.stderr)


class SecretRead(unittest.TestCase):
    DENIED = ["cat ~/.config/setwave/repos.json", "cat /Users/me/.config/setwave/repos.json", "cat .env",
              "cat .env.production", "head credentials", "vim auth.json", "grep TOKEN .env", "cat .env | head -5",
              "base64 .env > /tmp/out"]
    ALLOWED = ["ls -la ~/.config/setwave", "stat .env", "ls .env", "grep -c TOKEN .env", "grep -q TOKEN .env",
               "cat .env | wc -l", "cat README.md", "grep TOKEN src/auth.py"]

    def test_denies_printing_the_contents_of_a_secret(self):
        for cmd in self.DENIED:
            with self.subTest(cmd=cmd):
                p = run_guard("secret-read", bash_event(cmd))
                self.assertEqual(p.returncode, 2, p.stderr)
                self.assertIn("secret-read-guard", p.stderr)
                self.assertIn("grep -c", p.stderr)

    def test_allows_the_named_safe_paths(self):
        for cmd in self.ALLOWED:
            with self.subTest(cmd=cmd):
                p = run_guard("secret-read", bash_event(cmd))
                self.assertEqual(p.returncode, 0, p.stderr)


class DispatchContract(unittest.TestCase):
    def test_the_plugin_s_own_template_passes(self):
        prompt = (TESTS.parent / "templates" / "agent.md").read_text()
        self.assertEqual(run_guard("dispatch-contract", task_event(prompt)).returncode, 0)

    def test_a_prompt_missing_sections_is_refused_by_name(self):
        prompt = "# TAREFA — fix it\n\n## 1. Onde\n\nworktree w\n\n## 2. A tarefa\n\nfix the bug\n"
        p = run_guard("dispatch-contract", task_event(prompt))
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("dispatch-contract-guard", p.stderr)
        self.assertIn("the gate to run", p.stderr)
        self.assertIn("Done-when ledger", p.stderr)

    def test_a_fork_is_exempt(self):
        p = run_guard("dispatch-contract", task_event("continue the work", subagent_type="fork"))
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_passes_through_what_it_cannot_judge(self):
        for payload in ("{not json", task_event(""), task_event(None), {"tool_name": "Bash", "tool_input": {}}):
            with self.subTest(payload=payload):
                self.assertEqual(run_guard("dispatch-contract", payload).returncode, 0)

    def test_a_real_but_contract_less_prompt_is_refused(self):
        self.assertEqual(run_guard("dispatch-contract", task_event("go look")).returncode, 2)


class FailOpen(unittest.TestCase):
    CRASH = ["attribution", "forbidden-git", "secret-read", "dispatch-contract"]

    def test_a_crashed_judge_exits_zero(self):
        def boom(event):
            raise RuntimeError("crash-35")

        for name in self.CRASH:
            old = guards.JUDGES[name]
            guards.JUDGES[name] = boom
            try:
                self.assertEqual(guards.main([name], stdin=json.dumps(bash_event("git stash"))), 0)
            finally:
                guards.JUDGES[name] = old

    def test_a_guard_broken_on_disk_fails_open_through_the_cli(self):
        broken = TMP / "guards-crash-35.py"
        text = GUARDS.read_text()
        self.assertIn("def judge_attribution(event):", text)
        broken.write_text(text.replace("def judge_attribution(event):",
                                       "def judge_attribution(event):\n    raise RuntimeError('crash-35')"))
        p = subprocess.run([sys.executable, str(broken), "attribution"],
                           input=json.dumps(bash_event("git commit -m \"Co-Authored-By: Claude\"")),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_an_unknown_guard_name_fails_open(self):
        self.assertEqual(guards.main(["nope"], stdin="{}"), 0)


class Install(unittest.TestCase):
    def setUp(self):
        sandbox()
        reset_settings()

    def test_install_writes_the_four_hooks_and_keeps_what_is_there(self):
        write_settings(FOREIGN)
        p = run_wave("hooks", "install")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(SETTINGS.read_text())
        self.assertEqual(data["model"], "opus")
        self.assertEqual(guard_commands(data),
                         [("attribution", "Bash"), ("forbidden-git", "Bash"), ("secret-read", "Bash"),
                          ("dispatch-contract", "Task")])
        self.assertEqual(len(backups()), 1, backups())
        self.assertEqual(json.loads((SETTINGS.parent / backups()[0]).read_text()), FOREIGN)

    def test_install_on_a_clean_project_creates_the_file_without_a_backup(self):
        p = run_wave("hooks", "install")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(guard_commands(json.loads(SETTINGS.read_text()))), 4)
        self.assertEqual(backups(), [], "nothing to back up when there was no settings file")

    def test_install_is_idempotent(self):
        self.assertEqual(run_wave("hooks", "install").returncode, 0)
        again = run_wave("hooks", "install")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already installed", again.stdout)
        self.assertEqual(len(guard_commands(json.loads(SETTINGS.read_text()))), 4, "no duplicates")
        self.assertEqual(backups(), [], "a no-op run backs nothing up")

    def test_dry_run_prints_the_diff_and_writes_nothing(self):
        write_settings(FOREIGN)
        p = run_wave("hooks", "install", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("+++ ", p.stdout)
        self.assertIn("nothing written", p.stdout)
        self.assertEqual(json.loads(SETTINGS.read_text()), FOREIGN)
        self.assertEqual(backups(), [])

    def test_install_refuses_a_settings_file_it_cannot_parse(self):
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text("{not json")
        p = run_wave("hooks", "install")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("does not parse", p.stdout + p.stderr)
        self.assertEqual(SETTINGS.read_text(), "{not json", "never overwrites a file it cannot read")

    def test_install_refuses_a_settings_file_of_the_wrong_shape(self):
        write_settings({"hooks": ["not", "a", "map"]})
        p = run_wave("hooks", "install")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("hooks", p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_status_reports_a_file_it_cannot_parse_instead_of_refusing(self):
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text("{not json")
        p = run_wave("hooks", "status")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("does not parse", p.stdout)
        self.assertEqual(p.stdout.count("missing"), 4, p.stdout)

    def test_status_tells_active_from_missing(self):
        p = run_wave("hooks", "status")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.count("missing"), 4, p.stdout)
        self.assertEqual(run_wave("hooks", "install").returncode, 0)
        p = run_wave("hooks", "status")
        self.assertEqual(p.stdout.count("active"), 4, p.stdout)
        self.assertNotIn("missing", p.stdout)

    def test_status_names_a_hook_whose_target_is_gone_as_broken(self):
        # the plugin checkout lost its guards.py after the install: the entry is there, the guard cannot answer
        repo = wave.Repo.get("o/r")
        with unittest.mock.patch.object(wave, "HOOKS_SCRIPT", TMP / "gone-35" / "guards.py"):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                wave.cmd_hooks(repo, types.SimpleNamespace(action="install", dry_run=False))
                wave.cmd_hooks(repo, types.SimpleNamespace(action="status", dry_run=False))
        self.assertIn("broken", out.getvalue())
        self.assertIn("attribution", line(out.getvalue(), "broken"))

    def test_doctor_carries_one_line(self):
        p = run_wave("doctor")
        warn = line(p.stdout, "guard hooks installed")
        self.assertTrue(warn.startswith("  ! "), warn)
        self.assertIn("0/4", warn)
        self.assertIn("`wave hooks install`", warn)
        self.assertEqual(run_wave("hooks", "install").returncode, 0)
        p = run_wave("doctor")
        ok = line(p.stdout, "guard hooks installed")
        self.assertTrue(ok.startswith("  ✓ "), ok)
        self.assertIn("4/4", ok)


class GuaranteesTable(unittest.TestCase):
    def test_each_guard_has_a_tested_row(self):
        p = run_wave("guarantees")
        self.assertEqual(p.returncode, 0, p.stderr)
        for needle in ("no AI attribution in a commit or PR-body command", "no forbidden git command",
                       "no secret file's contents are printed", "no Agent dispatch whose prompt lacks the contract"):
            row = line(p.stdout, needle)
            self.assertTrue(row.startswith("  ✓ "), row)


if __name__ == "__main__":
    unittest.main()
