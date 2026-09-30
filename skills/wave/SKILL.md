---
name: wave
description: Drive a GitHub epic to merged PRs, wave by wave, from any repo, account or stack — a git worktree per issue, one Claude Code subagent per worktree, verification before relaying, merge only on the owner's explicit OK. State lives in GitHub (sub-issues, blocked_by, PRs) and git, never in the chat. Use when the user says "/setwave:wave", "/wave", "run epic #N", "dispatch the next wave", "what is unblocked", "merge the wave", or hands you an epic to execute across sessions.
---

# wave — one constant prompt, any repo, any epic

```
/setwave:wave <epic>          e.g. /setwave:wave 77   or   /setwave:wave owner/repo#77
```

That is the whole prompt, every time. The data comes from GitHub and git at run time, so the same words work in the next session, the next wave, the next repo, the next account.

Script: `${CLAUDE_PLUGIN_ROOT}/scripts/wave.py` (Python 3 stdlib; needs `gh` logged in, `git` ≥ 2.38; `codegraph` optional). Define once per session:

```bash
alias wave='python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wave.py"'
```

**You are the orchestrator, never the implementer.** In a wave you do not edit code. You create worktrees, dispatch agents, verify what comes back, resolve a PR conflict only by merging the base branch into that PR's branch, and merge only after an explicit OK from the owner in this conversation.

## The loop

1. **Facts.** `wave facts` → slug, base branch, gate commands (from CI or the manifest), protected paths, CodeGraph. If the gate looks wrong or incomplete (a CI step that is an *action*, not a `run:`), fix it first with `wave repos add . --gate ... --protected ...` — a wrong gate makes every "green" a lie. `git status --porcelain` in the main checkout must be empty; you never work there.
2. **Next wave.** `wave next <epic> --batch 4` → leaf issues that are open, unblocked (`blocked_by` closed), without an open PR and without a worktree. Batch 4 by default: eleven parallel builds hit rate limits and cost four times the tokens for the same result. `wave lint <issues>` if a body looks thin (see `templates/issue-contract.md`).
3. **Dispatch.** `wave dispatch <issues>` → a worktree per issue from `origin/<base>`, CodeGraph index, and one prompt file per issue in `../<repo>-handoffs/<N>.md`. Then spawn **one `Agent` (general-purpose) per prompt file, all in a single message**, prompt = the file's content verbatim. Save the agentId↔issue map in a uniquely named file (the scratchpad is shared between agents).
4. **On each completion**, before telling the user anything: `wave verify <PR>` → attribution grep = 0, protected paths untouched, CI, mergeability, worktree clean and pushed. Relay the *verified* report: PR, diff size, what the agent corrected in the issue's premises, what it left alone. An agent killed by a rate limit (429) is **resumed with `SendMessage` to the same agentId** ("resume where you stopped; first `git status` in your worktree") — its context survives; never redispatch from zero. An "interim" report (gate running in background) resumes by itself.
5. **All green → order.** `wave order --epic <epic>` → pairwise `git merge-tree` per repo, suggested order, conflicting pairs. Show the user a table (PR, issue, diff, CI) and the order, and **ask for an explicit OK** (`AskUserQuestion`). No OK, no merge. `--auto` never.
6. **Merge.** `wave merge <prs…> --yes --wait-base-ci` → one at a time, `MERGEABLE` and CI green before each, `--merge` (pass `--squash` if that is the repo's convention). It stops at the first conflict: in that PR's worktree, `git merge --no-edit origin/<base>`, resolve by hand (append-at-the-same-spot is the common case: keep both blocks and check the closing brace git treated as common), run the gate, `git commit --no-edit`, `git push` — **never force push** — comment on the PR what you resolved, rerun `merge` from that PR. If the base branch's CI turns red after a merge with no textual conflict, that is a **semantic** conflict: fix forward inside the next PR's base-merge (with a comment) or in a small `fix/` PR; merge nothing more until the base is green.
7. **Close out.** `wave close-parents <epic>`; `wave cleanup` (removes only worktrees that are clean, pushed and merged; branches stay); `wave status <epic> --post` (the tree with states, posted on the epic — the paper trail that replaces any handoff document). Report, then offer the next wave with the same command.

## Multi-repo, multi-account

- An epic's sub-issues and blockers may live in other repositories; the tree follows `repository_url`. Refs are `owner/name#N`; a bare `N` means the repo of the current directory.
- `~/.config/wave/repos.json` maps `owner/name` → local checkout, `gh` account, base, gate, protected paths, worktree prefix. Unknown repos are found by scanning `search_paths` (default `~/projects`) for a checkout with that origin. `wave repos add <path> --account <ghuser> ...`, `wave repos scan`, `wave repos list`.
- Calls for a repo with an `account` run with that account's token (`gh auth token --user`); the global `gh` login is never switched. Agents get the account in their prompt.
- A `.wave.json` at a repo root supplies the same keys for everyone who clones it.

## Non-negotiables (yours and every agent's)

- No AI attribution in commits or PRs. None of: `git gc --prune`, `reflog expire`, `stash`, `reset --hard`, `clean -f`, `push --force*`, `branch -D`, `rm -rf`. No `gh pr merge --auto`. Merge only with an explicit OK in this conversation.
- Never touch the main checkout or a worktree that is not yours. Evidence before assertion. Every guard mutation-checked.
- Relay nothing you have not verified with `wave verify`.

## What an issue must look like

`${CLAUDE_PLUGIN_ROOT}/templates/issue-contract.md`. In one line: epic → sub-issues → leaves; `blocked_by` wired in GitHub; a body with `file:line` evidence, `## Done when` with owners, `## ADR stub`, `## Out of scope`, `## Handoff` carrying the branch line and the CodeGraph query. `wave lint` checks it. Planning an epic to that contract is the audit session's job; executing it is this skill's.

## Lessons this encodes

- `git merge-tree` predicts textual conflicts, not semantic ones. Base-branch CI after each merge is the truth.
- The script is Python so nothing depends on the user's shell (macOS bash 3.2, zsh's 1-indexed arrays and no word-splitting have all bitten).
- `| tail` hides exit codes. `verify` reads CI, not an agent's sentence.
- Agents correct issue premises constantly (line numbers, types, file names). That is the system working; they say it in the PR and comment on sibling issues that cite the same symbols.
- Merging the base into a branch resolves a PR conflict without any force push.
