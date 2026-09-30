Parent: {{e}}

Append `greet_{{run}}_b` to the end of `app.py`. The other of {{a}} and {{b}} appends at the same place: the conflict is arranged, and `wave resolve` settles it.

```fake-agent
append app.py

def greet_{{run}}_b():
    return "b"
```

## Done when

- [ ] `app.py` defines `greet_{{run}}_b` and still compiles (`python3 -m py_compile app.py`) — owner: `scripts/fake_agent.py`

## Handoff

```bash
git worktree add -b e2e/{{b.n}}-{{run}}-b ../setwave-sandbox-{{b.n}} origin/main
```
