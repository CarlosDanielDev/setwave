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
| discover the repo | `wave facts` — remote, base branch, gate from CI, protected paths | the repo |
| find the next wave | `wave next <epic> --batch 4` — open leaves with no open blocker, no PR, no worktree | GitHub |
| dispatch | `wave dispatch <issues>` — worktrees from `origin/<base>`, one prompt file per issue | Claude spawns one agent per file |
| verify | `wave verify <PR>` — no AI attribution, protected paths untouched, CI green, worktree clean and pushed | the script |
| order | `wave order --epic <epic>` — pairwise `git merge-tree`, conflicts named | the script |
| merge | `wave merge <PRs> --yes --wait-base-ci` — one at a time, CI green before each, stops at the first conflict | **you**, with an explicit OK |
| close out | `wave close-parents`, `wave cleanup`, `wave status --post` | the script |

Conflicts are resolved by merging the base branch *into* the PR's branch and pushing normally. Never a force push. Never `--auto`.

## Any repo, any account, any stack

- Epics can span repositories: sub-issues and blockers are followed by `repository_url`. Refs are `owner/name#N`.
- `~/.config/setwave/repos.json` maps repositories to local checkouts, `gh` accounts, base branches, gate commands and protected paths. Unknown repos are found by scanning `~/projects`. Per-repo calls use that repo's account token; your global `gh` login is never switched.
- The gate is read from the CI workflow (`run:` lines), or from the manifest (`Cargo.toml`, `package.json`, `pyproject.toml`, `go.mod`, `Package.swift`). Override with `wave repos add . --gate ...` or a `.wave.json` at the repo root.

## What an issue needs

See [`templates/issue-contract.md`](templates/issue-contract.md). Short version: an epic with **sub-issues** (not just mentions), dependencies as **`blocked_by`** (not just words), and a body with `file:line` evidence, a `## Done when` with owners, an `## ADR stub`, an `## Out of scope`, and a `## Handoff` block carrying the branch name and a CodeGraph query. `wave lint <issues>` tells you what is missing.

Agents read the issue **and its comments**: when a PR changes a symbol another open issue cites, the agent leaves a one-line comment there. That is how "what the next wave inherits" becomes data instead of someone's memory.

## Lessons baked in

- `git merge-tree` predicts textual conflicts, not semantic ones; the base branch's CI after each merge is the truth.
- Four agents at a time, not eleven: same result, a quarter of the tokens, no rate-limit deaths. A dead agent is resumed, not redispatched.
- Everything is Python because the orchestration loop must not depend on the user's shell.

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
