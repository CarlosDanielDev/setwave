# PRD — Progress bar: waves visible without running any command

Date: 2026-11-03. Status: distilled from CarlosDanielDev/setwave#116 + owner screenshots (2026-11-03).

## Problem

During wave 1 of #93 the owner saw agent progress only through harness
notifications and hand-run `wave agents`. Three visibility layers exist; the
plugin arms none of them by default.

## Evidence

- #116 (open, wave 2 of epic #93) carries the full Done-when list.
- Owner screenshots 2026-11-03: **named** agents (`wave-550`, `wave-551`)
  render native progress rows (`14s · ↓ 86.7k tokens`) in the host UI;
  anonymous `general-purpose` spawns do not. This confirms #116's experiment
  hypothesis: naming the spawn makes the host render the bar.
- The owner already runs a custom statusline (model, `152k/1m` context, rate
  limits) — the setwave segment must **chain after it, never replace it**
  (#116 ADR: option b).

## Goals

Three layers, strongest guarantee first:

1. **Statusline segment** (renders every turn in any client): `wave agents
   --line` — one line, `setwave: 2 working · 1 stalled · 1 done(#99) · 0 dead`,
   < 120 chars, exit 0 always, reads only local stamps/state (no `gh`), degrades
   to an empty line outside a wave repo. The plugin ships the exact `statusLine`
   snippet; `wave init --yes` and `wave hooks install` **offer** to install it
   (one consent; chains; removal note in the README).
2. **Chat seat table** (zero config): the SKILL mandates the orchestrator print
   the seat table in the conversation at every agent event (dispatch, milestone,
   PR open, nudge, stall); `tests/test_skill_lint.py` fails if the instruction
   leaves.
3. **Native agent bars** (best effort): dispatch/SKILL name every spawn
   `wave-<N>` when the host's spawn accepts a name — naming is the lever that
   makes the host render its native per-agent rows. Never a guarantee: the host
   UI controls it.

## Non-goals

- Injecting rendering into the host UI beyond naming (the native task bar is
  not controllable by a plugin — #116 out of scope, unchanged).
- Push notifications.
- Replacing or editing the user's existing statusline.
- Token/zone numbers in the statusline segment (seats only; the native rows
  already show per-agent tokens, zone burn lives in `wave stats`/`wave agents`).

## Success criteria

- All #116 Done-when items check out via the PR ledger; `wave guarantees`
  gains "seat progress is visible without running any command once armed",
  pinned, mutation-checked in the PR.
- `wave agents --line` exits 0 and prints ≤ 1 line (≤ 120 chars) inside a wave
  repo, outside one, and with no stamps at all.

## Done when

See #116 — its body is the contract; this PRD adds the naming decision
(`wave-<N>`, not `setwave-<N>`) and the seats-only statusline decision, both
owner-confirmed 2026-11-03.

## Open questions

None — #116 + grill rounds 1–2 settle everything.
