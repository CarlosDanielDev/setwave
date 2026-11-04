# PRD — Smart zone: keep every wave agent inside the context band where models perform best

Date: 2026-11-03. Status: grilled with the owner (rounds 1–2, all recommendations accepted).

## Problem

Wave agents drift out of the context band where LLMs perform best — the owner's
"smart zone", around 100k tokens of context. An agent that carries too much
context produces dumber output, and setwave today has no notion of that: #119
(#120) added a *cost* brake (budget) and an issue *size* class, but neither says
"this issue's implementer will leave the smart zone — split it".

## Evidence

- `issue_size` (scripts/wave.py) classifies small/medium/large by Done-when
  items and cited files; a large issue already carries "consider splitting" —
  but nothing measures *context fit*.
- `budget_note` + `.setwave-tokens.jsonl` + `wave stats` already track spend vs
  a ceiling per issue — the exact plumbing a context band needs.
- Owner report 2026-11-03: models perform best around ~100k tokens. This is a
  heuristic, not a verified constant — shipped configurable, never hardcoded
  as science.

## Goals

1. `smart_zone` becomes a first-class repo config: `.wave.json` key, default
   100000 tokens, validated like `budget` (positive int; a bad value refuses
   before any worktree exists).
2. Dispatch-time static check: a crude, documented estimate of the implementer's
   context (Done-when items × a named per-item constant + cited files) compared
   against the zone. Over the zone → a loud `!` warning with "split this issue"
   advice. **Never a refusal.**
3. Run-time burn: `wave stats` shows spend vs `smart_zone` per issue (same
   ledger as the budget ceiling); `wave agents` shows each seat's zone state
   (in-zone / near-edge / over).
4. Docs reach: the `Size:` line gains a zone-fit clause; `skills/define`
   gains the rule "an issue whose implementer would exceed the zone must
   split"; `templates/agent.md` gains a short context-hygiene block
   (checkpoint findings to files, scoped reads — stay in the zone).

## Non-goals

- No live context-window reading — the zone is an estimate from usage lines and
  the size heuristic, documented as such everywhere it prints.
- No new CLI flag (`--budget` stays the cost brake; the zone is a model
  property, not a per-issue cost).
- No dispatch refusal on zone overflow; the budget ceiling stays the only hard
  brake.
- No judge changes (judges already read a scoped patch).
- No `init` proposal wiring for the new key.
- No claim that 100k is a scientific constant.

## Success criteria

- Every behavior above is pinned by a test (budget/validation tests for the
  config key, pure tests for the estimate, skill-lint for the docs sections).
- `wave prepare`'s `Size:` line states zone fit; `wave dispatch` prints the
  `!` warning on an over-zone issue and still dispatches with `--force`-free
  normal flow.
- `wave guarantees` carries the new promise with its enforcement point.
- Full gate green: formatter/linter/tests per `.wave.json`.

## Done when

See the GitHub issue (issue-contract format, owners per item). This PRD is the
argument; the issue body is the contract.

## Open questions

None — grilled 2026-11-03 (Q2 estimate honesty, Q3 numbers/config, Q4 warn-not-refuse, Q6 docs reach all answered).
