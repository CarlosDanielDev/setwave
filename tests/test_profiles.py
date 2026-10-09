"""The repo's own orchestration, detected per phase and rendered into the prompt.

Three fixture repos, the shapes the finding describes: o/p carries a full `.claude/` orchestration
(skills, commands, agents, hooks, issue templates, a Makefile, a CLAUDE.md) plus one declared
override in `.wave.json`; o/h has hooks only; o/c has a CLAUDE.md and nothing under `.claude/`.
Detection is by name and location; `.wave.json` `phases` overrides any phase.
"""
import json
import unittest

from helpers import TMP, git, run_wave, sandbox, wave

P = TMP / "profile-full"  # full .claude/ orchestration
H = TMP / "profile-hooks"  # hooks only
C = TMP / "profile-docs"  # CLAUDE.md only


def build(name: str) -> None:
    bare = TMP / "remote" / "o" / f"{name}.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(TMP / name), cwd=TMP)


def commit_all(root, message: str) -> None:
    git("add", ".", cwd=root)
    git("commit", "-q", "-m", message, cwd=root)
    git("push", "-q", "origin", "main", cwd=root)


def setUpModule():
    sandbox()
    build("profile-full")
    (P / ".wave.json").write_text(json.dumps(
        {"base": "main", "phases": {"rules": ["docs/CONVENTIONS.md"], "kickoff": "sprint-start"}}))
    (P / "docs").mkdir()
    (P / "docs" / "CONVENTIONS.md").write_text("# Conventions\n")
    (P / "CLAUDE.md").write_text("# Rules\n\nRun the gate with:\n\n```bash\nmake ci\n```\n")
    for s in ("define", "kickoff", "issue-handoff", "git-workflow", "work-tracking"):
        (P / ".claude" / "skills" / s).mkdir(parents=True)
        (P / ".claude" / "skills" / s / "SKILL.md").write_text(f"---\nname: {s}\ndescription: repo skill {s}\n---\n")
    (P / ".claude" / "commands").mkdir(parents=True)
    (P / ".claude" / "commands" / "sync-main.md").write_text("sync main\n")
    (P / ".claude" / "commands" / "start-work.md").write_text("start\n")
    (P / ".claude" / "agents").mkdir(parents=True)
    (P / ".claude" / "agents" / "critical-reviewer.md").write_text("review\n")
    (P / ".claude" / "agents" / "code-qa.md").write_text("qa\n")
    hooks = P / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "notify.sh").write_text("#!/bin/sh\necho notified\n")
    (hooks / "notify.sh").chmod(0o755)
    (hooks / "dispatch-contract.py").write_text("print('contract')\n")
    (hooks / "missing-interp.sh").write_text("#!/usr/bin/env no-such-interp-15\necho x\n")
    (hooks / "missing-interp.sh").chmod(0o755)
    (P / ".claude" / "issue-templates").mkdir(parents=True)
    (P / ".claude" / "issue-templates" / "story.md").write_text("# story\n\n## Done when\n\n## Handoff\n")
    (P / "Makefile").write_text(".PHONY: sync test\n\nsync:\n\t@touch .synced\n\ntest:\n\t@echo testing\n")
    commit_all(P, "full orchestration")

    build("profile-hooks")
    (H / ".wave.json").write_text(json.dumps({"base": "main"}))
    (H / ".claude" / "hooks").mkdir(parents=True)
    (H / ".claude" / "hooks" / "off.sh").write_text("#!/bin/sh\necho off\n")
    (H / ".claude" / "hooks" / "wired.py").write_text("print('wired')\n")
    (H / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": ["Bash(ls:*)"]},
         "hooks": {"PreToolUse": [{"matcher": "Bash",
                                   "hooks": [{"type": "command", "command": "python3 .claude/hooks/wired.py arg"}]}]}}))
    commit_all(H, "hooks only")

    build("profile-docs")
    (C / ".wave.json").write_text(json.dumps({"base": "main"}))
    (C / "CLAUDE.md").write_text("# Rules\n\nBe careful.\n")
    commit_all(C, "a CLAUDE.md and nothing else")


def repo(name: str, **entry):
    return wave.Repo(f"o/profile-{name}", {"path": str(TMP / f"profile-{name}"), **entry})


def seed_plan(**bodies):
    """A plan directory under TMP with one row per body; each body's key is its row key."""
    d = TMP / "plan-15"
    d.mkdir(exist_ok=True)
    (d / "index.tsv").write_text("".join(f"{k}\tStory {k}\tstory\t-\n" for k in bodies))
    for k, text in bodies.items():
        (d / f"{k}.md").write_text(text)
    return d


def prompt_for(name: str) -> str:
    r = repo(name)
    return wave.render_prompt(r, {"number": 1, "title": "T", "html_url": "u", "body": ""}, "feat/1-t", "abc1234", 1)


class FullRepo(unittest.TestCase):
    def test_every_phase_is_detected_or_declared(self):
        self.assertEqual(repo("full").profile()["phases"], {
            "define": {"provider": "define", "source": "detected"},
            "handoff": {"provider": "issue-handoff", "source": "detected",
                        "text": ".claude/skills/issue-handoff/SKILL.md"},
            "kickoff": {"provider": "sprint-start", "source": "declared"},
            "sync": {"provider": "make sync", "source": "detected"},
            "rules": {"provider": ["docs/CONVENTIONS.md", "CLAUDE.md"], "source": "declared + detected"},
            "agent_skills": {"provider": ["git-workflow", "work-tracking"], "source": "detected"},
            "review": {"provider": ["code-qa", "critical-reviewer"], "source": "detected"},
            "enforcement": {"provider": [".claude/hooks/dispatch-contract.py", ".claude/hooks/missing-interp.sh",
                                         ".claude/hooks/notify.sh"], "source": "detected"},
            "gate": {"provider": ["make test"], "source": "detected"},
            "worktree": {"provider": "../<repo>-<N>", "source": "default"},
        })

    def test_what_was_found_is_listed(self):
        p = repo("full").profile()
        self.assertEqual(p["skills"], ["define", "git-workflow", "issue-handoff", "kickoff", "work-tracking"])
        self.assertEqual(p["commands"], ["start-work", "sync-main"])
        self.assertEqual(p["agents"], ["code-qa", "critical-reviewer"])
        self.assertEqual(p["issue_templates"], ["story"])
        self.assertEqual(p["makefile_targets"], ["sync", "test"])

    def test_hooks_carry_whether_they_can_run(self):
        by_name = {h["path"]: h for h in repo("full").profile()["hooks"]}
        self.assertTrue(by_name[".claude/hooks/notify.sh"]["ok"])
        self.assertFalse(by_name[".claude/hooks/dispatch-contract.py"]["ok"])
        self.assertIn("not executable", by_name[".claude/hooks/dispatch-contract.py"]["why"])
        self.assertFalse(by_name[".claude/hooks/missing-interp.sh"]["ok"])
        self.assertIn("no-such-interp-15", by_name[".claude/hooks/missing-interp.sh"]["why"])

    def test_facts_prints_the_profile(self):
        p = run_wave("facts", cwd=P)
        self.assertEqual(p.returncode, 0, p.stderr)
        facts = json.loads(p.stdout)
        self.assertEqual(facts["profile"]["phases"]["sync"]["provider"], "make sync")
        self.assertEqual(facts["profile"]["agents"], ["code-qa", "critical-reviewer"])

    def test_the_prompt_renders_the_profile(self):
        text = prompt_for("full")
        self.assertIn("orquestração própria", text)
        self.assertIn("`git-workflow`, `work-tracking`", text, "the declared-order skills, before the default stack")
        self.assertIn("`define`", text)
        self.assertIn("notify.sh", text, "the hooks the repo enforces are named")
        self.assertIn("docs/CONVENTIONS.md", text, "rule files are named, not copied")
        self.assertIn("critical-reviewer", text)
        self.assertIn("perde para elas", text, "a repo rule loses to the non-negotiables, and the agent says so")

    def test_doctor_warns_on_a_doc_command_the_gate_lacks(self):
        p = run_wave("doctor", cwd=P)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("  ! `CLAUDE.md` names `make ci` and the gate does not: "
                      "if it is a gate step, declare it in `.wave.json`'s gate\n", p.stdout)
        self.assertNotIn("`Makefile` names `make test`", p.stdout, "the Makefile's gate target IS the gate here")

    def test_doctor_lists_the_hooks_and_warns_on_the_broken_ones(self):
        p = run_wave("doctor", cwd=P)
        self.assertIn("  ! the repo's own hooks can run (3): .claude/hooks/dispatch-contract.py: not executable"
                      " (chmod +x); .claude/hooks/missing-interp.sh: interpreter `no-such-interp-15` is not on PATH\n",
                      p.stdout)

    def test_sync_provider_runs_before_the_worktrees(self):
        synced = P / ".synced"
        synced.unlink(missing_ok=True)
        wave.sync_provider(repo("full"), dry_run=False)
        self.assertTrue(synced.exists(), "make sync ran in the repo root")
        synced.unlink()

    def test_sync_provider_dry_run_runs_nothing(self):
        synced = P / ".synced"
        synced.unlink(missing_ok=True)
        wave.sync_provider(repo("full"), dry_run=True)
        self.assertFalse(synced.exists())

    def test_a_failing_declared_sync_refuses(self):
        with self.assertRaises(SystemExit):
            wave.sync_provider(repo("full", phases={"sync": "false"}), dry_run=False)

    def test_a_declared_sync_that_is_not_a_command_is_named(self):
        with self.assertRaises(SystemExit) as ctx:
            wave.sync_provider(repo("full", phases={"sync": ["make", "sync"]}), dry_run=False)
        self.assertIn("phases.sync", str(ctx.exception))

    def test_a_command_file_provider_is_prose_not_a_script(self):
        r = repo("full", phases={"sync": "sync-main.md"})
        self.assertIn("nothing to run", wave.sync_provider(r, dry_run=False))

    def test_plan_refuses_a_body_missing_the_repo_template_sections(self):
        d = seed_plan(one="# Story one\n\n## Done when\n\n- [ ] x\n")
        p = run_wave("plan", str(d), "--dry-run", cwd=P)
        self.assertEqual(p.returncode, 1)
        self.assertIn("## Handoff", p.stdout + p.stderr, "the missing template section is named")

    def test_plan_accepts_a_body_with_the_template_sections(self):
        d = seed_plan(one="# Story one\n\n## Done when\n\n- [ ] x\n\n## Handoff\n\nbranch\n")
        p = run_wave("plan", str(d), "--dry-run", cwd=P)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("1 issues", p.stdout)

    def test_plan_force_skips_the_template(self):
        d = seed_plan(one="# Story one\n\n## Done when\n\n- [ ] x\n")
        p = run_wave("plan", str(d), "--dry-run", "--force", cwd=P)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class HooksOnly(unittest.TestCase):
    def test_every_phase_is_default_but_enforcement(self):
        ph = repo("hooks").profile()["phases"]
        self.assertEqual(ph["enforcement"], {"provider": [".claude/hooks/off.sh", ".claude/hooks/wired.py"],
                                             "source": "detected"})
        self.assertEqual(ph["sync"], {"provider": "git fetch --prune", "source": "default"})
        self.assertEqual(ph["rules"], {"provider": [], "source": "default"})
        self.assertEqual(ph["gate"], {"provider": [], "source": "default"})

    def test_a_hook_wired_through_an_interpreter_needs_no_executable_bit(self):
        by_name = {h["path"]: h for h in repo("hooks").profile()["hooks"]}
        self.assertTrue(by_name[".claude/hooks/wired.py"]["ok"],
                        "settings.json runs it with python3; the bit is not charged")
        self.assertFalse(by_name[".claude/hooks/off.sh"]["ok"], "referenced by nothing, executable by no one")

    def test_the_prompt_says_the_default_stack_applies_and_names_the_hooks(self):
        text = prompt_for("hooks")
        self.assertIn("nenhuma skill própria", text, "the default stack is named as the stack in use")
        self.assertIn("off.sh", text)


class ClaudeMdOnly(unittest.TestCase):
    def test_rules_detected_everything_else_default(self):
        p = repo("docs").profile()
        self.assertEqual(p["phases"]["rules"], {"provider": ["CLAUDE.md"], "source": "detected"})
        self.assertEqual(p["phases"]["handoff"], {"provider": "`wave prepare` (#14)", "source": "default"})
        self.assertEqual(p["phases"]["agent_skills"], {"provider": [], "source": "default"})
        self.assertEqual(p["hooks"], [])
        self.assertEqual(p["makefile_targets"], [])

    def test_the_prompt_names_the_rules_and_the_default_stack(self):
        text = prompt_for("docs")
        self.assertIn("`CLAUDE.md`", text)
        self.assertIn("nenhuma skill própria", text)
        self.assertIn("perde para elas", text, "the one rule file could still contradict a non-negotiable")

    def test_nothing_detected_says_so(self):
        text = wave.profile_prompt(wave.detect_profile(TMP / "nothing-15", {}))
        self.assertIn("Nenhuma orquestração própria detectada", text)
        self.assertIn("stack padrão", text)

    def test_a_malformed_phases_key_is_loud_not_silent(self):
        with self.assertRaises(SystemExit) as ctx:
            wave.detect_profile(TMP / "nothing-15", {"phases": "define"})
        self.assertIn("phases", str(ctx.exception))
        with self.assertRaises(SystemExit):
            wave.detect_profile(TMP / "nothing-15", {"phases": {"rules": "CLAUDE.md"}})
        with self.assertRaises(SystemExit):
            wave.detect_profile(TMP / "nothing-15", {"phases": {"agent_skills": "git-workflow"}})

    def test_a_settings_command_that_cannot_run_is_broken_not_fatal(self):
        d = TMP / "profile-badhooks"
        h = d / ".claude"
        h.mkdir(parents=True, exist_ok=True)
        (h / "settings.json").write_text(json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": 42},
                {"type": "command", "command": "python3 'unterminated"},
            ]}]}}))
        hooks = wave.hook_health(d)
        self.assertEqual([h["ok"] for h in hooks], [False, False])
        self.assertIn("command", hooks[0]["why"])
        self.assertIn("parse", hooks[1]["why"])

    def test_an_unreadable_rule_file_does_not_crash_doc_commands(self):
        d = TMP / "profile-lockedoc"
        d.mkdir(exist_ok=True)
        (d / "CLAUDE.md").mkdir()
        self.assertEqual(wave.doc_commands(d), [], "a CLAUDE.md that is a directory (or unreadable) is skipped")


if __name__ == "__main__":
    unittest.main()
