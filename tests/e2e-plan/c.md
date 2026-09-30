Parent: {{e}}

Add `runs/{{run}}.txt`, a file no other leaf touches: it merges cleanly in any order.

```fake-agent
append runs/{{run}}.txt
run {{run}}: leaf c
```

## Done when

- [ ] `runs/{{run}}.txt` exists — owner: `scripts/fake_agent.py`

## Handoff

```bash
git worktree add -b e2e/{{c.n}}-{{run}}-c ../setwave-sandbox-{{c.n}} origin/main
```
