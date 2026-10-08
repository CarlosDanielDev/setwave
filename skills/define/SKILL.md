---
name: define
description: The refinement session that only leaves an issue when an implementer would start it without asking a single question — grilling in rounds, every question numbered and carrying a recommended answer, environment facts fetched by a read-only subagent, escalation sensors, and a local plan directory (index.tsv + one body per issue) that only `wave plan` turns into issues, behind an explicit OK. Use when the user says "define a #N", "refina a #N", "deixa pronta", "make #N ready", "refine this issue", "plan an epic", or hands you a problem that still has no issue. Do not use it for high uncertainty or a top-level bet — say so and suggest promoting the work to an epic instead of running this session.
---

# define — the session ends only at Ready

The audit that authors the bodies `/setwave:wave` executes. You interview the owner relentlessly about the work until an implementer could start cold; the test, in plain words: **would an implementer begin without a single question?** Whatever they would still ask is a pendency, not a detail for later. Script: the same `wave` alias as the orchestrator (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/wave.py"`).

## Load first

Read what is already decided before asking anything — a session that re-asks the parent's decisions is noise, not refinement.

```bash
wave lint <N>                          # on an existing issue: its output IS the gap list
gh issue view <N> -R <owner>/<repo> --json title,body,labels,comments
gh api repos/<owner>/<repo>/issues/<N>/sub_issues   # its children, when it is a parent
```

The parent comes from the body's `Parent: #N` line; read the parent's body and its comments too. Then read `templates/issue-contract.md` and **the template file for the issue's type** (`templates/issue/story.md`, `bug.md`, `chore.md`, `feature.md`) before writing any body — never paraphrase from memory. The type is a decision of the session, not a default: a chore says why it is not a story; a feature names its slices.

## The rounds

Decisions form a tree: each one branches into the decisions that depend on it. The **frontier** is every decision whose prerequisites are already resolved — the questions answerable now without guessing at answers you have not heard. Ask the whole frontier in one round, then wait. Every answer recomputes the frontier: resolved decisions push it forward and release the questions that depended on them. A question whose answer depends on one still open belongs to a **later round**, never this one.

```
❓ **Q1** - **<the question>**: <the body, with alternatives when there are any>

➡️ <your recommended answer>
```

Number within the round (Q1..Qn); each block is self-contained, because the answers come back to it. Lead with the branches that change the result: observable behavior, scope boundaries, error states, who is affected.

## Facts are yours, not the owner's

Whether the code really does what the owner just said is a fact of the environment, not a question for them: dispatch a **read-only subagent** (`Explore`) to cross-reference it and keep the round moving — only the questions that depend on the fact wait for a later round. Prose at a known path (the issue, a template, a README section) you read inline; a subagent is for searching code and state. If the subagent fails or stalls, the fact becomes a pendency of investigation — the question never waits forever.

## Escalation sensors

The sensors measure uncertainty the session cannot resolve — not apparent complexity. Any one firing: pause, name it, and suggest promoting the work to an epic with `wave plan`. Never convert alone; the owner decides.

1. The outcome cannot be phrased observably: no `## Done when` item can name the test that passes or the command whose output proves it.
2. Two or more "don't know / we would have to find out" answers to product questions across the session — accumulated uncertainty, not two in one breath.
3. A decision depends on evidence that does not exist yet, or on an issue that has not been authored.
4. The work grows on two or more fronts at once — the false positive is touching an intersection (a bug on a boundary between two areas is still one front); the sensor is for scope being pulled in two directions.
5. The answer implies a strategy or product decision the owner has not made.

What the session already settled becomes the epic's first drafted bodies — nothing is thrown away. A leaf of an existing epic is never promoted (an epic inside an epic): its doubt becomes a pendency in its own body, with an owner, and if the doubt touches sibling leaves, the parent's body says so.

## The output is a plan directory (local first)

The session writes `plans/<slug>/`: `index.tsv` (`key<TAB>title<TAB>labels<TAB>parent`, parent a plan key, `#N` for an existing issue, or `-`), optional `deps.tsv` (`blocked<TAB>blocker`), and one body per key, referencing siblings as `{{key}}` and, inside branch lines and `Closes`, as `{{key.n}}`. **Nothing exists on GitHub yet.** Lint it with the same rules `wave lint` runs on issues — fill first, exactly as `wave plan` fills bodies once every issue exists, with provisional numbers standing in for the real ones:

```bash
PLUGIN="${CLAUDE_PLUGIN_ROOT:-}" python3 - <<'PY'
import os, sys
from pathlib import Path

root = os.environ.get("PLUGIN", "")
if not root or not (Path(root) / "scripts" / "wave.py").exists():
    sys.exit(f"define lint: scripts/wave.py not found under {root or '(CLAUDE_PLUGIN_ROOT is unset)'} — set PLUGIN at the top of this block to the plugin root")
sys.path.insert(0, str(Path(root) / "scripts"))
from wave import ISSUE_TYPES, fill_refs, lint_body

d = Path("plans/my-epic")  # the directory the session wrote
if not (d / "index.tsv").exists():
    sys.exit(f"define lint: no index.tsv in {d}/ — edit `d` in this block to the directory the session wrote")
rows = [l.split("\t") for l in (d / "index.tsv").read_text().splitlines()
        if l.strip() and not l.startswith("#")]
missing_bodies = [k for k, *_r in rows if not (d / f"{k}.md").exists()]
if missing_bodies:
    sys.exit(f"define lint: no body for key(s) {', '.join(missing_bodies)} in {d}/")
numbers = {k: 900 + i for i, (k, *_rest) in enumerate(rows)}  # provisional; `wave plan` writes the real ones
bad = 0
for k, _title, labels, _parent in rows:
    body = fill_refs((d / f"{k}.md").read_text(), numbers)
    typ = next((t for t in ISSUE_TYPES if t in labels.split(",")), None)
    missing = lint_body(body, numbers[k], typ)
    bad += bool(missing)
    print(f"{k} [{typ or 'no type'}]: " + ("ok" if not missing else "; ".join(missing)))
sys.exit(1 if bad else 0)
PY
```

A body that lints clean here lints clean on GitHub after creation: the branch carries the issue's number and `Closes` names its own issue once the real numbers are filled. `wave lint <N...>` on the created issues is the re-check that closes the loop.

## The only door to GitHub

```bash
wave plan plans/<slug> --dry-run    # the owner reads this
wave plan plans/<slug>              # only after an explicit OK in this conversation
wave lint <the new numbers>
```

No other step in this skill writes to GitHub — issue creation, body edits, comments: none of them happen here. `wave plan` is the door the owner sees: it refuses a directory already applied (`numbers.json`) and titles that already exist as issues, so a session cannot create a second copy by running twice. **Refining an existing issue** produces the refined body as a draft in the plan directory and hands it to the owner — this skill does not edit issues; a tool that writes bodies (`wave amend`, #14 in this plugin's repo) will be that door when it lands.

## Pendencies block Ready

What the session cannot close — a fact nobody can establish yet, a person not present, a decision the owner deferred — becomes a section in the drafted body, never a footnote in the chat:

```markdown
## Pendências (bloqueiam o Ready)

- <the question, in one line> — owner: <a name, never nobody>
```

A pendency without an owner is a pendency forever. When one resolves, its answer moves into the body section it belongs to and the line leaves the list.

## Closing

Say explicitly what the session decided, which Ready criteria are met, and what is missing. If any pendency is open, the issue is not Ready — say that in one line and stop. The plan directory, the dry-run and the OK request are the handoff; the owner's OK creates the issues, and the wave takes it from there.

## Non-negotiables (yours and every agent's)

- No AI attribution in commits or PRs. None of: `git gc --prune`, `reflog expire`, `stash`, `reset --hard`, `clean -f`, `push --force*`, `branch -D`, `rm -rf`.
- Nothing on GitHub without the owner's explicit OK, and only through `wave plan`.
- Evidence before assertion: a fact the session states about the code comes from a command's output, not from memory.
- You never work in the main checkout; a session that needs a scratch directory takes one outside it.
