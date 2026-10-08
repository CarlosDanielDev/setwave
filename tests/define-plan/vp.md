Parent: #1

The rule says the plugin's version lives in both manifests and the pair is `serial` — one PR at a time (`README.md` "Developing", and `.claude-plugin/plugin.json:4` currently `0.3.19`). The practice has split: through PR #33 every feature commit bumped it (`e9105c1`: `0.3.18` → `0.3.19`, `e74eff8`: `0.3.17` → `0.3.18`), and the four features merged on 2026-10-08 (#42 typed issues, #43 judge, #44 guard hooks, #45 state sweep) shipped without one — `git log e9105c1..HEAD --oneline -- .claude-plugin/` prints nothing. The rule collides with parallel waves (two agents, one serial file) and the agents resolved the collision by silently dropping the bump. Nothing reads the manifest version at runtime: `grep -rn "plugin.json" scripts/ tests/ hooks/ .github/workflows/` matches no file, so the per-PR bump is an edit no one consumes, paid for on every wave.

## Por que não é story

No user-visible behavior changes; the gain is hygiene — the manifest stops claiming a version `main` does not carry, and parallel waves stop paying a serial-file collision on an edit nothing reads.

## Done when

- [ ] README "Developing" states the policy in one sentence: `version` moves at release, next to the tag, not per PR — `grep -c "at release" README.md` prints 1 — owner: implementer
- [ ] both manifests carry the same version string — `python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'] == json.load(open('.claude-plugin/marketplace.json'))['plugins'][0]['version'])"` prints `True` — owner: implementer
- [ ] the next release tag carries the bumped version in both files, and `git log --oneline -- .claude-plugin/plugin.json` shows one change per release from then on — owner: reviewer

## ADR stub

Bump on every feature PR (what the older handoffs instructed) versus bump at release, where the tag is the version. Context: nothing at runtime reads the manifest version, the pair is `serial`, and the drift already happened — four features landed unbumped on 2026-10-08 while the older rule told every agent to touch a file another agent was touching. Recommendation (to argue with): bump once at release, beside the tag; a check that both manifests agree is the only enforcement the rule needs.

## Out of scope

- A release tool or automation (`wave release`) — this issue decides the policy; the tool would be its own issue.
- Retroactively editing merged PRs or older issue texts that instruct the per-PR bump.

## Handoff

```bash
git worktree add -b chore/{{vp.n}}-version-policy ../setwave-{{vp.n}} origin/main
```

`codegraph explore "GUARANTEES cmd_guarantees"` and read `README.md` "Developing". Gate: `python3 -m unittest discover -s tests -v && python3 scripts/wave.py guarantees`. Skills: caveman ultra, ponytail, superpowers:test-driven-development. No AI attribution; none of the forbidden git commands; the worktree stays. `Closes {{vp.n}}`.
