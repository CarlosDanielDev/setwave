# ADR 001 — guard hooks are opt-in per project, all four through one install

2026-10-08 · issue #35

The fork was hooks in the plugin manifest (always-on for everyone who installs the plugin) versus `wave hooks install` writing them into a project's `.claude/settings.json`. Chosen: per project, all four guards, one mechanism. Hooks that are always on change the session of anyone who only wanted `/setwave:wave`, and a split install (two always-on, two opt-in) would give `hooks status` two sources of truth and twice the semantics for no measured incident.

The guards cost one command where they are wanted (`wave hooks install`, idempotent, backed up, `--dry-run` to look first), and `wave doctor` makes an unguarded project loud instead of silently unguarded. If a real incident shows a session that should have been guarded without installing, shipping a plugin `hooks/hooks.json` for `attribution` and `forbidden-git` is the follow-up: the guard scripts already speak the hook stdin-JSON protocol and would move there unchanged.

Provenance, per guard: `attribution` and `forbidden-git` encode this repository's own incident record — the rules lived as prose in `templates/agent.md` and were ignored exactly often enough to need an environment backstop. `secret-read` is demeter-workspace's `block-secret-read.sh` ported (the registry's `repos.json` carries account tokens, so it joins the common `.env`/`credentials` denylist), and `dispatch-contract` is kyte-ai-orquestration's `agent-dispatch-contract.py` ported (the prompt is all the agent sees, so the prompt is what is checked).
