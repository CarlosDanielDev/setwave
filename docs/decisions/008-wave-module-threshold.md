# ADR 008 — wave.py stays one file, with a measured limit per section

2026-10-17 · issue #79

The fork the issue names: keep `scripts/wave.py` as one file behind a tested boundary, or split it into a stdlib package (`wave/`) behind a `scripts/wave.py` shim. Chosen: stay single, and fail the suite (`tests/test_architecture.py`) when any section passes 1100 lines. The issue's example limit of 800 is contradicted by the numbers: the `commands` section alone is 993 lines today, so 800 would fail on day one and force a split made to satisfy a round number. 1100 is the largest section plus about 10%.

## The numbers (main at 8661c2b)

3855 lines, 180 `def`s (146 at module level, 1 class), 22 sections. A section runs from one `# ---- name` banner to the next; the first is everything before the first banner.

| section | lines | defs |
| --- | ---: | ---: |
| commands (`cmd_repos` … `cmd_cleanup` and their helpers) | 993 | 38 |
| repo | 329 | 29 |
| profile | 265 | 13 |
| sweep | 212 | 11 |
| why / doctor | 186 | 3 |
| init (first run) | 176 | 9 |
| typed issue templates | 168 | 11 |
| guard hooks | 165 | 7 |
| liveness | 162 | 13 |
| adopt | 150 | 3 |
| prepare / amend | 137 | 6 |
| guarantees | 130 | 1 |
| body surgery for prepare / amend | 113 | 6 |
| main | 112 | 4 |
| release | 108 | 4 |
| plan | 97 | 3 |
| header and constants | 93 | 0 |
| registry | 77 | 5 |
| refs & tree | 77 | 8 |
| plumbing | 47 | 7 |
| prompt | 45 | 2 |
| Done-when ledger | 13 | 1 |

One section is past 800 lines and holds a quarter of the file. The other 21 average 136 and none passes 330. The issue's "3546 lines" has moved to 3855 since it was written: v0.7.1 plus the release, worktree-state and hook work.

## The coupling, measured

- `candidates` (line 1288, 30 lines, the issue said 1231): reaches 9 module names in 6 sections (liveness, refs & tree, typed issue templates, repo, plumbing, constants); 34 functions transitively.
- `cmd_doctor` (line 3042, 158 lines, the issue said 2826 and 145): reaches 32 module names in 11 sections; 56 functions transitively, in 14 of the 22 sections. It is the widest reader in the file.
- 107 ordered section pairs call each other. 12 of them point forward (an earlier section uses a later one, e.g. `repo` uses `profile` and `PR_LIST_LIMIT`, `commands` uses `cmd_doctor`, `prepare_issue` and `append_log`). In one file that costs nothing; across modules each is an import cycle to break first.
- `GUARANTEES` (100 rows, `tests/test_token.py` reads it): the "enforced where" column names functions by text. A split makes those names stale unless each row also names its module.
- `cmd_guarantees` scans `HERE.glob("*.py")` for forbidden git commands. A `wave/` package is invisible to a flat glob, so a split must change that scan or the guarantee quietly stops covering most of the code.
- Tests: 29 test files load the one module through `tests/helpers.py` (`wave`), use 61 distinct `wave.X` names, and monkeypatch module globals (`profile_skills`, `PR_LIST_LIMIT`, `HOOKS_SCRIPT`). Patching a name only works in the module that looks it up, so each patched name pins where its readers must live after a split.
- `HERE` and `TEMPLATES` (lines 47 and 1542) derive from `__file__`; a package moves both.

## Why stay

The README rule that makes a single file valuable is intact: `python3 scripts/wave.py` runs from anywhere with no relative imports, and the judge, the linter and the guarantee scan read one file. A split buys nothing a reader needs yet: 21 of 22 sections are small and cohesive, the one big section is a list of independent `cmd_*` verbs (the most separable thing to split later: one verb per entry point), and the costs above (12 forward references, a rewritten guarantee scan, re-pointed patches in 29 test files) are paid today for no behaviour change.

## The rule and the exit

`tests/test_architecture.py` fails when a section passes `SECTION_LIMIT` (1100). It also fails if fewer than 20 banners are found, so rewording the banner cannot hide a section inside its neighbour. When a feature does not fit, the order of extraction is: `commands` first (it is the one at 90% of the limit), as modules grouped by verb, leaving a `scripts/wave.py` shim that keeps `python3 scripts/wave.py` working; then `profile`, then `doctor` and `guard hooks`. Raising the limit needs a new ADR that carries fresh numbers, not an edit to the constant.
