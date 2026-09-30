# setwave

Drive a GitHub epic to merged pull requests, wave by wave, with Claude Code — one git worktree per issue, one agent per worktree, verification before anything is relayed, and a merge only when you say so.

In surfing, a *set* is the group of waves that arrives together. That is what this does with issues.

The state lives in **GitHub** (sub-issues, `blocked_by`, PRs) and in **git** (worktrees, branches). Never in a chat. So the prompt is one line, and it is the same line tomorrow, in the next repo, under another account:

```
/setwave:wave 77
```

**Status:** v0.3.x. Built on and proven against one epic (30 issues, 11 PRs merged in one wave, [numbers below](#what-has-been-measured)). Cross-repo and multi-account are implemented and untested. What is planned lives in [epic #1](https://github.com/CarlosDanielDev/setwave/issues/1); nowhere in this file is a planned feature described as existing.

## Install

```
/plugin marketplace add CarlosDanielDev/setwave
/plugin install setwave@setwave
```

Then **restart Claude Code** so the skill loads. Needs `gh` (logged in), `git` ≥ 2.38, Python 3. `codegraph` is used when present.

## Quick start

From inside a checkout of the repo you want to work on. `wave` below means `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wave.py"`.

**1. Preflight.**

```bash
wave doctor
```

Every ✗ is something a wave would trip on: `gh` not logged in, tracked changes in your main checkout (the plugin never works there), no real gate. `wave facts` shows what was discovered — remote, base branch, gate commands read from your CI, protected paths. `facts` names where each command came from (`run:`, `uses:`, manifest, or config); a CI step that is an action the plugin does not know is a `!` in `doctor` until you list it under `ignore_actions` in `.wave.json`. If the gate is incomplete, declare it once:

```bash
wave repos add . --gate "cargo fmt --check" "cargo test" --protected src/safety
```

or commit a `.wave.json` at the repo root so everyone who clones gets it (see [Any repo](#any-repo-any-account-any-stack)).

**2. Have an epic the plugin can run.** Two cases.

*New work:* write one body per issue to a directory, following [`templates/issue-contract.md`](templates/issue-contract.md), plus `index.tsv` (`key<TAB>title<TAB>labels<TAB>parent`) and `deps.tsv` (`blocked<TAB>blocker`). Then:

```bash
wave plan ./my-epic --milestone "v2" --dry-run   # look
wave plan ./my-epic --milestone "v2"             # create, link, wire
```

It creates the issues in order, links sub-issues, wires `blocked_by`, fills cross-references, and refuses to run twice. A Claude session doing an audit (poka-yoke, a design review) is the natural author of those bodies; `wave lint` checks them.

*An existing project* (a milestone or label full of flat issues, "depends on #14" written in the text): the plugin reads only real sub-issues and real `blocked_by`, so wire them by hand today — `gh api repos/o/r/issues/<epic>/sub_issues -F sub_issue_id=<id>` and `…/issues/<n>/dependencies/blocked_by -F issue_id=<id>` — or wait for `wave adopt` ([#11](https://github.com/CarlosDanielDev/setwave/issues/11)), which does exactly that from a milestone or label.

**3. Run the wave.** In Claude Code:

```
/setwave:wave 77
```

Claude runs `doctor`, `next` (the leaves that are open, unblocked, without a PR, without a worktree), `dispatch` (a worktree and a prompt per issue), spawns one agent per prompt, `verify` on each PR as it lands, `order --plan` (conflicts predicted, the delta pinned), then **asks you for an OK** and only then `merge --plan --yes`, one PR at a time with CI green before each. It closes parents whose leaves closed, removes worktrees that are clean, pushed and merged, and posts the tree on the epic.

**4. The next day.** Nothing to remember:

```bash
wave next 77      # what is ready now, and why the rest is not
wave why 82       # the premises behind one answer
wave status 77    # the whole tree; --post leaves it on the epic
```

The last status comment on the epic is the paper trail. A single view across all your epics and repos is [#10](https://github.com/CarlosDanielDev/setwave/issues/10).

## What it does

| Step | Command (Claude runs these from inside the repo) | Who decides |
| --- | --- | --- |
| preflight | `wave doctor` — gh, git ≥ 2.38, clean checkout, real gate, protected paths, dead-agent and leftover worktrees, half-extracted crates in the cargo cache (`--fix-cache` moves them aside), disk and size of the batch; `dispatch` refuses on ✗, `dispatch --warm` runs `cargo fetch` once first | the script |
| watch agents | `wave agents` — every issue worktree: minutes since dispatch, minutes since the newest change, open PR, and a verdict (`working`, `quiet`, `likely dead`, `done`); `doctor` names the likely dead with their recovery | files and git, never processes |
| discover | `wave facts` — remote, base branch, gate from CI or manifest, protected paths | the repo |
| find the wave | `wave next <epic> --batch 4` — every open leaf in exactly one state: blocked, in progress, worktree, ready | GitHub |
| explain | `wave why <issue>` — the premises behind READY / NOT READY, with evidence | the script |
| dispatch | `wave dispatch <issues>` — worktrees from `origin/<base>`, CodeGraph index, one prompt file per issue | Claude spawns one agent per file |
| verify | `wave verify <PR> --epic <epic>` — no AI attribution, protected paths untouched, CI green, worktree clean and pushed, no contradictions (branch number ≠ closed issue, PR closing a parent or a still-blocked issue); names sibling issues that cite files the PR touched | the script |
| order | `wave order --epic <epic> --plan p.json` — pairwise `git merge-tree`, a chain simulation naming the step that will conflict, `serial` paths flagged, the delta pinned to a file; `--run-gate` runs the gate on the tree after each step, in a throwaway worktree, and names the first PR that turns it red (`merge --plan` then refuses without `--force`) | the script |
| merge | `wave merge --plan p.json --yes --wait-base-ci` — refuses if the base or a PR head moved since the OK, refuses a still-blocked issue, one at a time, CI green before each, stops at the first conflict | **you**, with an explicit OK on that exact plan |
| close out | `wave close-parents`, `wave cleanup`, `wave status --post` | the script |
| plan an epic | `wave plan <dir>` — issues from bodies + `index.tsv` + `deps.tsv`; links, wires, fills references; refuses to run twice | the audit session writes the bodies |
| measure | `wave stats` — every run logged to `~/.config/setwave/log.jsonl` | the log |

Conflicts are resolved by merging the base branch *into* the PR's branch and pushing normally. Never a force push. Never `--auto`. (`wave resolve`, which automates the clean half of that, is [#8](https://github.com/CarlosDanielDev/setwave/issues/8).)

## Any repo, any account, any stack

- Epics can span repositories: sub-issues and blockers are followed by `repository_url`. Refs are `owner/name#N`; a bare `N` means the repo of the current directory.
- `~/.config/setwave/repos.json` maps repositories to local checkouts, `gh` accounts, base branches, gate commands, protected and serial paths. Unknown repos are found by scanning `~/projects`. Per-repo calls use that repo's account token (`gh auth token --user`); your global `gh` login is never switched.
- A `.wave.json` at the repo root carries the shareable part for everyone who clones:

```json
{
  "base": "main",
  "gate": ["cargo fmt --check", "cargo clippy --all-targets -- -D warnings", "cargo test", "cargo deny check"],
  "protected": ["src/safety"],
  "serial": ["src/store/mod.rs"]
}
```

`protected`: paths a PR must not touch (`verify` fails if it does). `serial`: paths where two PRs cannot land in one batch — a migrations list, a generated index, a version file — because a textual merge cannot see an index collision; `order` flags them to land one after the other in `blocked_by` order.

- The gate is read from the CI workflow (`run:` lines), or from the manifest (`Cargo.toml`, `package.json`, `pyproject.toml`, `go.mod`, `Package.swift`). Steps that are GitHub *actions* are not seen yet ([#3](https://github.com/CarlosDanielDev/setwave/issues/3)); declare those.

## What an issue needs

See [`templates/issue-contract.md`](templates/issue-contract.md). Short version: an epic with **sub-issues** (not just mentions), dependencies as **`blocked_by`** (not just words), and a body with `file:line` evidence, a `## Done when` with owners, an `## ADR stub`, an `## Out of scope`, and a `## Handoff` block carrying the branch name and a CodeGraph query. `wave lint <issues>` tells you what is missing; a body without a handoff still runs (branch and query are derived from the title).

Agents read the issue **and its comments**: when a PR changes a symbol another open issue cites, the agent leaves a one-line comment there, and `verify` names the issues it should have told. That is how "what the next wave inherits" becomes data instead of someone's memory.

Every PR body carries a **Done-when ledger**: the issue's list with `[x]` done, `[ ]` left with a line why, `~~item~~ — dropped: reason`. Today it is read by eye; [#12](https://github.com/CarlosDanielDev/setwave/issues/12) makes `verify` refuse a PR without it and `merge` apply it to the issue, so the next agent receives only what remains.

## Mistake-proofing

The tool prefers making a wrong action impossible over warning about it: no `--yes`, no merge; plan SHAs moved, no merge; issue still blocked, no merge; `doctor` ✗, no dispatch; issue not READY, no dispatch; a directory already applied, no second `plan`. What cannot be made impossible is made loud: `verify` names AI attribution, protected paths touched, red CI, unclean worktrees, and contradictions. `why` prints the premises behind every READY so a "why not?" is answered with evidence, not memory. Every state is one of a fixed, exclusive set, and the code asserts it.

## What has been measured

From the epic this was built on ([CarlosDanielDev/dev-cleaner#77](https://github.com/CarlosDanielDev/dev-cleaner/issues/77), 2026-09-29/30), one wave:

| | |
| --- | --- |
| issues planned / leaves in the wave | 30 / 11 |
| PRs merged, lines | 11, +2,923 / −162 |
| test suite on the base branch | 249 → 328 |
| agent tokens, total / median per agent | ≈ 2.07 M / ≈ 184 k |
| agents killed by rate limit (batch of 11) | 3 of 11, all resumed, no work lost |
| textual conflicts predicted / hit | 3 / 3 |
| semantic conflicts (not predictable textually) | 1, caught by the base branch's CI |
| `wave next` vs. the hand-picked next wave | 9 / 9 |

What is *not* measured: cross-repo, multi-account, a merge driven end to end by `merge --plan --yes`. Those are [#7](https://github.com/CarlosDanielDev/setwave/issues/7) and [#9](https://github.com/CarlosDanielDev/setwave/issues/9). `wave stats` accumulates yours.

## Roadmap — v0.4.0, [epic #1](https://github.com/CarlosDanielDev/setwave/issues/1)

The plugin runs on itself: the epic was created by `wave plan`, and `/setwave:wave CarlosDanielDev/setwave#1` runs it.

- [#2](https://github.com/CarlosDanielDev/setwave/issues/2) tests and a CI gate for the script itself — blocks everything below
- [#3](https://github.com/CarlosDanielDev/setwave/issues/3) the gate sees CI steps that are actions
- [#4](https://github.com/CarlosDanielDev/setwave/issues/4) doctor tells a running agent from a dead one
- [#5](https://github.com/CarlosDanielDev/setwave/issues/5) doctor catches a half-extracted package cache
- [#6](https://github.com/CarlosDanielDev/setwave/issues/6) `order --run-gate`: semantic conflicts found before the merge
- [#7](https://github.com/CarlosDanielDev/setwave/issues/7) cross-repo and per-account, proven
- [#8](https://github.com/CarlosDanielDev/setwave/issues/8) `wave resolve`
- [#9](https://github.com/CarlosDanielDev/setwave/issues/9) an end-to-end run on a sandbox repository
- [#10](https://github.com/CarlosDanielDev/setwave/issues/10) `wave epics`: every open epic across your repos, and what is ready
- [#11](https://github.com/CarlosDanielDev/setwave/issues/11) `wave adopt`: an existing milestone or label becomes an epic
- [#12](https://github.com/CarlosDanielDev/setwave/issues/12) the Done-when ledger, refused by `verify` when missing and applied by `merge`

## Developing

The gate, declared in this repo's `.wave.json` and run by CI on Ubuntu and macOS (`.github/workflows/ci.yml`):

```bash
python3 -m unittest discover -s tests -v
python3 scripts/wave.py guarantees
```

Stdlib only, tests included: nothing to install. The suite runs offline. `tests/helpers.py` builds a temporary bare remote `o/r` and a clone with branches that conflict and branches that do not, points the registry and the run log at that directory, and puts `tests/bin/gh` first on `PATH`: a fake `gh` that answers from `tests/fixtures/*.json`, keyed by the request, and refuses anything that would write to GitHub.

| request | fixture |
| --- | --- |
| `gh api repos/o/r/issues/1/sub_issues?per_page=100` | `repos_o_r_issues_1_sub_issues.json` (the path, query dropped, `/` → `_`) |
| `gh pr list --state open …` | `pr_list_open.json` |
| `gh pr checks 20 …` / `gh pr view 20 …` | `pr_checks_20.json` / `pr_view_20.json` |
| `gh issue list …` / `gh repo view …` | `issue_list.json` / `repo_view.json` |

A missing fixture answers like an unknown path on GitHub (exit 1): for `…/dependencies/blocked_by` that means "no blockers". To record a fixture from a real repo, save the same request, then rename `OWNER/REPO` to `o/r` in the file name and in the content (`repository_url`, `html_url`) so it matches the sandbox:

```bash
gh api 'repos/OWNER/REPO/issues/42/sub_issues?per_page=100' > tests/fixtures/repos_o_r_issues_42_sub_issues.json
gh pr list -R OWNER/REPO --state open --json number,title,headRefName,baseRefName,body,mergeable,url > tests/fixtures/pr_list_open.json
```

A guard is only as good as the test that fails without it: when you add one, remove it once, watch a test fail, restore it, and flip its row in `GUARANTEES` to tested. The version lives in both `.claude-plugin/*.json` (they are `serial`: one PR at a time). Releases are tags `vX.Y.Z` on `main` with notes.

## Lessons baked in

- `git merge-tree` predicts textual conflicts, not semantic ones; `order --run-gate` finds a semantic one before the merge, at the cost of one gate run per step, and the base branch's CI after each merge stays the final word.
- A dead agent can leave a package cache half-extracted (`~/.cargo/registry/src`, `node_modules`); every fresh build then fails. `wave doctor` names the crate; `--fix-cache` moves it aside and cargo re-extracts it.
- Four agents at a time, not eleven: same result, a quarter of the tokens, no rate-limit deaths. A dead agent is resumed, not redispatched.
- Everything is Python because the orchestration loop must not depend on the user's shell.
- Idempotent where it can be: `next`, `verify`, `order`, `status`, `why`, `lint`, `stats` are read-only; `dispatch` keeps an existing worktree; `close-parents` and `cleanup` skip what is done; `plan` refuses to run twice; `merge` re-checks `MERGEABLE` and CI before every single merge and stops at the first that is not.

## Layout

```
.claude-plugin/plugin.json      manifest
.claude-plugin/marketplace.json this repo is its own marketplace
.wave.json                      this repo's own gate and serial files
skills/wave/SKILL.md            the orchestrator procedure Claude follows
scripts/wave.py                 the deterministic steps (stdlib only)
templates/agent.md              the per-issue prompt handed to each agent
templates/issue-contract.md     what an issue must carry
tests/                          the suite: a fake gh, recorded fixtures, a temporary git repo
.github/workflows/ci.yml        the suite and the guarantees on Ubuntu and macOS
```

MIT.
