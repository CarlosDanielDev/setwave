# setwave

Drive a GitHub epic to merged pull requests, wave by wave, with Claude Code — one git worktree per issue, one agent per worktree, verification before anything is relayed, and a merge only when you say so.

In surfing, a *set* is the group of waves that arrives together. That is what this does with issues.

The state lives in **GitHub** (sub-issues, `blocked_by`, PRs, milestones) and in **git** (worktrees, branches). Never in a chat. So the prompt is one line, and it is the same line tomorrow, in the next repo, under another account:

```
/setwave:wave 77
```

## Install

```
/plugin marketplace add CarlosDanielDev/setwave
/plugin install setwave@setwave
```

Needs `gh` (logged in), `git` ≥ 2.38, Python 3. `codegraph` is used when present.

## What it does

| Step | Command (run by Claude, from inside the repo) | Who decides |
| --- | --- | --- |
| preflight | `wave doctor` — gh, git ≥ 2.38, clean checkout, real gate, protected paths, orphan worktrees, disk; `dispatch` refuses on ✗ | the script |
| discover the repo | `wave facts` — remote, base branch, gate from CI, protected paths | the repo |
| explain | `wave why <issue>` — the premises behind READY / NOT READY, with evidence | the script |
| find the next wave | `wave next <epic> --batch 4` — open leaves with no open blocker, no PR, no worktree | GitHub |
| dispatch | `wave dispatch <issues>` — worktrees from `origin/<base>`, one prompt file per issue | Claude spawns one agent per file |
| verify | `wave verify <PR>` — no AI attribution, protected paths untouched, CI green, worktree clean and pushed | the script |
| order | `wave order --epic <epic>` — pairwise `git merge-tree`, conflicts named | the script |
| merge | `wave order --plan p.json` then `wave merge --plan p.json --yes --wait-base-ci` — refuses if the base or a PR head moved since the OK, refuses a still-blocked issue, one at a time, CI green before each, stops at the first conflict | **you**, with an explicit OK on that exact plan |
| close out | `wave close-parents`, `wave cleanup`, `wave status --post` | the script |
| plan an epic | `wave plan <dir>` — creates the issues from `index.tsv` + `deps.tsv` + one body per key, links sub-issues, wires `blocked_by`; refuses to run twice | the audit session writes the bodies |
| measure | `wave stats` — every run is logged to `~/.config/setwave/log.jsonl`: command, seconds, exit | the log |

Conflicts are resolved by merging the base branch *into* the PR's branch and pushing normally. Never a force push. Never `--auto`.

`wave order` does three things a human forgets: pairwise `git merge-tree` between every two PRs, a **chain simulation** that merges them in the suggested order and names the step where the accumulated tree conflicts, and a **serial** check — PRs touching paths declared as `serial` (a migrations list, a generated index) are flagged to land one after the other in their issues' `blocked_by` order, because a textual merge cannot see an index collision.

`wave verify --epic <epic>` also lists the open sibling issues whose bodies cite a file the PR touched, so the agent comments there what changed and the next wave reads it as data.

## Any repo, any account, any stack

- Epics can span repositories: sub-issues and blockers are followed by `repository_url`. Refs are `owner/name#N`.
- `~/.config/setwave/repos.json` maps repositories to local checkouts, `gh` accounts, base branches, gate commands and protected paths. Unknown repos are found by scanning `~/projects`. Per-repo calls use that repo's account token; your global `gh` login is never switched.
- The gate is read from the CI workflow (`run:` lines), or from the manifest (`Cargo.toml`, `package.json`, `pyproject.toml`, `go.mod`, `Package.swift`). Override with `wave repos add . --gate ...` or a `.wave.json` at the repo root.

## What an issue needs

See [`templates/issue-contract.md`](templates/issue-contract.md). Short version: an epic with **sub-issues** (not just mentions), dependencies as **`blocked_by`** (not just words), and a body with `file:line` evidence, a `## Done when` with owners, an `## ADR stub`, an `## Out of scope`, and a `## Handoff` block carrying the branch name and a CodeGraph query. `wave lint <issues>` tells you what is missing.

Agents read the issue **and its comments**: when a PR changes a symbol another open issue cites, the agent leaves a one-line comment there. That is how "what the next wave inherits" becomes data instead of someone's memory.

## Mistake-proofing

The tool prefers making a wrong action impossible over warning about it: no `--yes`, no merge; plan SHAs moved, no merge; issue still blocked, no merge; `doctor` ✗, no dispatch; a directory already applied, no second `plan`. What cannot be made impossible is made loud: `verify` names AI attribution, protected paths touched, red CI, unclean worktrees, and contradictions (branch number ≠ closed issue, PR closing a parent). `why` prints the premises behind every READY so a "why not?" is answered with evidence.

## Lessons baked in

- `git merge-tree` predicts textual conflicts, not semantic ones; the base branch's CI after each merge is the truth.
- A dead agent can leave a package cache half-extracted (`~/.cargo/registry/src`, `node_modules`); every fresh build then fails. Move the crate directory aside and rebuild.
- Four agents at a time, not eleven: same result, a quarter of the tokens, no rate-limit deaths. A dead agent is resumed, not redispatched.
- Everything is Python because the orchestration loop must not depend on the user's shell.
- Idempotent where it can be: `next`, `verify`, `order`, `status`, `lint`, `stats` are read-only; `dispatch` keeps an existing worktree; `close-parents` and `cleanup` skip what is done; `plan` refuses to run twice; `merge` re-checks `MERGEABLE` and CI before every single merge and stops at the first that is not.
- Measured, not promised: the log is the evidence. Numbers from the epic it was built on are in the commit history, and `wave stats` accumulates yours.

## Layout

```
.claude-plugin/plugin.json      manifest
.claude-plugin/marketplace.json this repo is its own marketplace
skills/wave/SKILL.md            the orchestrator procedure Claude follows
scripts/wave.py                 the deterministic steps (stdlib only)
templates/agent.md              the per-issue prompt handed to each agent
templates/issue-contract.md     what an issue must carry
```

MIT.
