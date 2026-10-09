# ADR 005 — a missing checkout is cloned under a size threshold, asked for above it

2026-10-08 · issue #13

The promise "one prompt, any repo, from anywhere" broke on the first meeting: `Repo.get` exited with "no local checkout" for any repo the user had not already placed under a search path, and the fix could swing two ways — clone silently, or always ask. Cloning is read-only and reversible, but it is also disk, time, and a decision about where things live that only the first search path answers by convention.

Chosen: clone into `search_paths[0]/<name>` without asking below `clone_ask_over_mb` (registry, default 500 MB) — below the threshold the question has one sane answer — and above it stop, print the size and the exact `gh repo clone` command, because the user knows something the tool does not (a second disk, an existing checkout elsewhere). The clone is never shallow (worktrees, `merge-tree` and `--merged` read history) and runs as the registry entry's account when one exists. `repos scan` and `Repo.get` share one rule for the checkout: the registry wins, a second checkout is reported, a dead path is repaired.
