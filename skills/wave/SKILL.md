---
name: wave
description: Drive a GitHub epic to merged PRs, wave by wave, from any repo, account or stack — a git worktree per issue, one Claude Code subagent per worktree, verification before relaying, merge only on the owner's explicit OK. State lives in GitHub (sub-issues, blocked_by, PRs) and git, never in the chat. Use when the user says "/setwave:wave", "/wave", "run epic #N", "dispatch the next wave", "what is unblocked", "merge the wave", or hands you an epic to execute across sessions.
---

# wave — one constant prompt, any repo, any epic

```
/setwave:wave <epic>          e.g. /setwave:wave 77   or   /setwave:wave owner/repo#77
```

That is the whole prompt, every time. Invoked with no epic, ask for the ref (`wave epics`, the cross-repo overview, is planned: #10 in this plugin's own repo). The data comes from GitHub and git at run time, so the same words work in the next session, the next wave, the next repo, the next account.

Script: `${CLAUDE_PLUGIN_ROOT}/scripts/wave.py` (Python 3 stdlib; needs `gh` logged in, `git` ≥ 2.38; `codegraph` optional). Define once per session:

```bash
alias wave='python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wave.py"'
```

**You are the orchestrator, never the implementer.** In a wave you do not edit code. You create worktrees, dispatch agents, verify what comes back, resolve a PR conflict only by merging the base branch into that PR's branch, and merge only after an explicit OK from the owner in this conversation.

## The loop

1. **Preflight.** `wave doctor --batch 4` → every ✗ is a mistake dispatch would otherwise let happen: `gh` not logged in, git < 2.38, tracked changes in the main checkout, no real gate, an agent `likely dead` (unmerged worktree, no PR, nothing changed for 30 min: a `!` naming the recovery), a crate cargo marked extracted but left half-written in the registry cache (a Rust repo: `wave doctor --fix-cache` moves it to `~/.config/setwave/quarantine/`, never deletes; cargo re-extracts it), not enough disk; a batch above 6 is a `!`. `dispatch` runs it and refuses on ✗. Then `wave facts` → slug, base branch, gate commands (from CI or the manifest), protected paths, CodeGraph. If the gate looks wrong or incomplete (a `!` in doctor names a CI action the gate cannot map to a command), fix it first with `wave repos add . --gate ... --protected ...` — a wrong gate makes every "green" a lie. `git status --porcelain` in the main checkout must be empty; you never work there.
2. **Next wave.** `wave next <epic> --batch 4` → each open leaf in exactly one state: blocked, in-progress, worktree, ready. `wave why <issue>` prints the premises behind that state with their evidence (leaf? open? blockers closed? PR? worktree?) and the conclusion; when the user asks "why not #N", answer with it, not from memory. `wave next` → leaf issues that are open, unblocked (`blocked_by` closed), without an open PR and without a worktree. Batch 4 by default: eleven parallel builds hit rate limits and cost four times the tokens for the same result. `wave lint <issues>` if a body looks thin (see `templates/issue-contract.md`).
3. **Dispatch.** `wave dispatch --warm <issues>` (Rust: `cargo fetch` in the main checkout first, so archives download once and only extraction is parallel; a failed fetch refuses the dispatch) → a worktree per issue from `origin/<base>`, CodeGraph index, and one prompt file per issue in `../<repo>-handoffs/<N>.md`. Then spawn **one `Agent` (general-purpose) per prompt file, all in a single message**, prompt = the file's content verbatim. Save the agentId↔issue map in a uniquely named file (the scratchpad is shared between agents).
4. **On each completion**, before telling the user anything: `wave verify <PR> --epic <epic>` → attribution grep = 0, protected paths untouched, CI, mergeability, worktree clean and pushed. Its `notify` lines name open sibling issues that cite files the PR touched: make sure the agent (or you) left a one-line comment there. Relay the *verified* report: PR, diff size, what the agent corrected in the issue's premises, what it left alone. An agent killed by a rate limit (429) is **resumed with `SendMessage` to the same agentId** ("resume where you stopped; first `git status` in your worktree") — its context survives; never redispatch from zero. `wave agents` tells which ones died: `working` / `quiet` / `likely dead` / `done`, from what each worktree's files and commits say (a `.setwave.json` stamp, written by `dispatch`, carries the dispatch time). An "interim" report (gate running in background) resumes by itself.
5. **All green → order.** `wave order --epic <epic>` → per repo: pairwise `git merge-tree`, a chain simulation in the suggested order (✗ marks the PR that needs the base merged into it first), and the `SERIAL` list (PRs touching paths declared `serial` in `.wave.json`/registry — one after the other, in `blocked_by` order, never in one batch). Run it with `--plan wave-plan.json`: the file pins the base SHA and every PR head. Add `--run-gate` when two PRs could conflict semantically (one changes what another's test pins): it announces the cost, runs the gate on the tree after each step in a throwaway worktree, names the first red step, and `merge --plan` refuses that plan without `--force`. Show the user the table (PR, issue, diff, CI) and the order, and **ask for an explicit OK** (`AskUserQuestion`). No OK, no merge. `--auto` never.
6. **Merge.** `wave merge --plan wave-plan.json --yes --wait-base-ci` → refuses if the base or any PR head moved since the plan (the OK was for that delta, not another), refuses a PR whose issue still has an open blocker, **re-runs `verify` on each PR itself** (you cannot skip it by forgetting), then merges one at a time, `MERGEABLE` and CI green before each, `--merge` (pass `--squash` if that is the repo's convention). It stops at the first conflict and prints `wave resolve <PR>`: that merges `origin/<base>` into the PR's branch in its worktree (refusing a dirty one), and on a clean merge runs the gate, pushes — **never force push** — and comments on the PR. On a conflict it stops with the files and line ranges; resolve by hand (append-at-the-same-spot is the common case: keep both blocks and check the closing brace git treated as common), `git add`, then `wave resolve <PR> --continue` (refuses leftover markers, commits, gates, pushes, comments). Rerun `merge` from that PR. If the base branch's CI turns red after a merge with no textual conflict, that is a **semantic** conflict: fix forward inside the next PR's base-merge (with a comment) or in a small `fix/` PR; merge nothing more until the base is green.
7. **Close out.** `wave close-parents <epic>`; `wave cleanup` (removes only worktrees that are clean, pushed and merged; branches stay); `wave status <epic> --post` (the tree with states and the next wave, posted on the epic — the paper trail that replaces any handoff document, and the answer to "where are we?" tomorrow). Report, then offer the next wave with the same command.

## Multi-repo, multi-account

- An epic's sub-issues and blockers may live in other repositories; the tree follows `repository_url`. Refs are `owner/name#N`; a bare `N` means the repo of the current directory.
- `~/.config/setwave/repos.json` maps `owner/name` → local checkout, `gh` account, base, gate, protected paths, worktree prefix. Unknown repos are found by scanning `search_paths` (default `~/projects`) for a checkout with that origin. `wave repos add <path> --account <ghuser> ...`, `wave repos scan`, `wave repos list`.
- Calls for a repo with an `account` run with that account's token (`gh auth token --user`); the global `gh` login is never switched. Agents get the account in their prompt.
- A `.wave.json` at a repo root supplies the same keys for everyone who clones it.

## Planning an epic (the other half)

The audit session writes one body per issue to a directory plus `index.tsv` (`key<TAB>title<TAB>labels<TAB>parent-key`) and `deps.tsv` (`blocked<TAB>blocker`), bodies referencing each other as `{{key}}`; `wave plan <dir> --milestone "..."` creates them in order, links sub-issues, wires `blocked_by`, fills the numbers, and writes `numbers.json`. It refuses to run twice on the same directory or the same titles. `wave lint` afterwards.

## Guarantees

`wave guarantees` lists every promise the plugin makes, where in the code it is enforced, and whether a test pins it. When the user asks "how do I know this works?", run it and answer from it. A new gap found in use becomes a new line there, a refusal in code, and a test — never a sentence in a document.

## Measuring

Every run appends to `~/.config/setwave/log.jsonl`; `wave stats` summarises runs, failures and durations per command. When asked whether this works, answer from the log, not from memory.

## The logic underneath (why the tool can be trusted more than a memory)

Every conclusion the tool prints is the last line of a syllogism whose premises it just checked: `why` shows them. States are exhaustive and exclusive (an issue is closed, blocked, in progress, has a worktree, or is ready — never two). Contradictions between what a text says and what the data says are refused, not warned about: a body that says "Depends on" with no `blocked_by`, a handoff branch without the issue number, a PR that closes a parent or a still-blocked issue, a merge plan whose SHAs moved. A wrong action is made impossible where possible (no `--yes` = no merge; no plan match = no merge; doctor ✗ = no dispatch) and loud everywhere else.

## Non-negotiables (yours and every agent's)

- No AI attribution in commits or PRs. None of: `git gc --prune`, `reflog expire`, `stash`, `reset --hard`, `clean -f`, `push --force*`, `branch -D`, `rm -rf`. No `gh pr merge --auto`. Merge only with an explicit OK in this conversation.
- Never touch the main checkout or a worktree that is not yours. Evidence before assertion. Every guard mutation-checked.
- Relay nothing you have not verified with `wave verify`.
- Every PR body carries a `## Done when` ledger — the issue's list with `[x]`, `[ ] item — not done: why`, or `~~item~~ — dropped: reason`. `verify` refuses a PR without it or with items that differ from the issue (`LEDGER-MISSING` / `LEDGER-MISMATCH`): send the agent back to fix its PR body, nobody else edits it. `merge` applies it to the issue; `wave tick <issue> --done <item> --strike <item> --why <reason>` is the same step by hand when `merge` says it could not.

## What an issue must look like

`${CLAUDE_PLUGIN_ROOT}/templates/issue-contract.md`. In one line: epic → sub-issues → leaves; `blocked_by` wired in GitHub; a body with `file:line` evidence, `## Done when` with owners, `## ADR stub`, `## Out of scope`, `## Handoff` carrying the branch line and the CodeGraph query. `wave lint` checks it. Planning an epic to that contract is the audit session's job; executing it is this skill's.

## Lessons this encodes

- `git merge-tree` predicts textual conflicts, not semantic ones. Base-branch CI after each merge is the truth.
- The script is Python so nothing depends on the user's shell (macOS bash 3.2, zsh's 1-indexed arrays and no word-splitting have all bitten).
- `| tail` hides exit codes. `verify` reads CI, not an agent's sentence.
- Agents correct issue premises constantly (line numbers, types, file names). That is the system working; they say it in the PR and comment on sibling issues that cite the same symbols.
- Merging the base into a branch resolves a PR conflict without any force push.
- Parallel first-time builds can leave a crate half-extracted in `~/.cargo/registry/src` (an agent killed mid-`cargo build` did, once): every fresh build on the machine then fails inside that crate with `file not found for module`. `wave doctor` names it (✗) and `wave doctor --fix-cache` moves its directory aside (never `rm -rf`); cargo re-extracts it from the checksummed archive. Same class of failure exists for `node_modules` caches. Prefer `wave dispatch --warm` (Rust) or `npm ci` in the main checkout before dispatching, and batch 4.
