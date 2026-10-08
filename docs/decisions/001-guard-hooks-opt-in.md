# ADR 001 — guard hooks are opt-in per project, all four through one install

2026-10-08 · issue #35

The fork was hooks in the plugin manifest (always-on for everyone who installs the plugin) versus `wave hooks install` writing them into a project's `.claude/settings.json`. Chosen: per project, all four guards, one mechanism. Hooks that are always on change the session of anyone who only wanted `/setwave:wave`, and a split install (two always-on, two opt-in) would give `hooks status` two sources of truth and twice the semantics for no measured incident.

The guards cost one command where they are wanted (`wave hooks install`, idempotent, backed up, `--dry-run` to look first), and `wave doctor` makes an unguarded project loud instead of silently unguarded. If a real incident shows a session that should have been guarded without installing, shipping a plugin `hooks/hooks.json` for `attribution` and `forbidden-git` is the follow-up: the guard scripts already speak the hook stdin-JSON protocol and would move there unchanged.
