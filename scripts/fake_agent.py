#!/usr/bin/env python3
"""A stand-in for the agent `wave dispatch` hands a worktree to, for `scripts/e2e.py`.

    fake_agent.py work <owner/name> <N>   in the issue's worktree: make the change the issue asks for, commit with
                                          `Closes #N`, push, open the PR; prints the PR's URL last
    fake_agent.py resolve                 in a worktree `wave resolve` left mid-merge: keep both sides of every
                                          conflict, `git add` them; `wave resolve --continue` does the rest
    fake_agent.py lie <owner/name> <N>    tick every Done-when item on the issue's body with nothing merged
                                          behind the claim — the false done `wave sweep` reverts

The change is a fenced block in the issue body, so the issue says what is done, like a real one:

    ```fake-agent
    append <path>
    <lines appended to path, created with its directories if missing>
    ```

Stdlib only. Never a force push; nothing is deleted.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BLOCK = re.compile(r"^```fake-agent\n(append (\S+)\n)(.*?)^```", re.S | re.M)
MARKER = re.compile(r"^(<{7}|\|{7}|={7}|>{7})(?: |$)")
OWN = "CarlosDanielDev/setwave"  # the plugin's own repository: nothing here is ever written to it


def check_target(slug: str) -> None:
    if slug.lower() == OWN.lower():
        raise SystemExit(f"fake agent refused: {slug} is the plugin's own repository; nothing is ever written to it")


def sh(cmd: list[str], cwd: Path) -> str:
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if p.returncode != 0:
        raise SystemExit(f"fake agent: $ {' '.join(cmd)}\n{p.stdout}{p.stderr}")
    return p.stdout


def change_of(body: str) -> tuple[str, str]:
    """(path, text to append) from the issue's ```fake-agent block."""
    m = BLOCK.search(body)
    if not m:
        raise SystemExit("fake agent: the issue has no ```fake-agent block, so it asks for nothing I can do")
    return m.group(2), m.group(3)


def apply(wt: Path, change: tuple[str, str]) -> None:
    path, text = change
    f = wt / path
    f.parent.mkdir(parents=True, exist_ok=True)
    old = f.read_text() if f.exists() else ""
    f.write_text(old + ("\n" if old and not old.endswith("\n") else "") + text)
    sh(["git", "add", path], wt)


def commit(wt: Path, title: str, n: int) -> None:
    sh(["git", "commit", "-q", "-m", f"{title}\n\nCloses #{n}"], wt)


def pr_body(n: int, issue_body: str) -> str:
    """`Closes #N` and the ledger: the issue's `## Done when` items, every one ticked (the fake agent does all it is asked)."""
    m = re.search(r"^## Done when[ \t]*\n(.*?)(?=^#{1,6} |\Z)", issue_body, re.S | re.M)
    items = re.findall(r"^[-*] \[[ xX]\] (.*)$", m.group(1), re.M) if m else []
    ledger = "\n\n## Done when\n\n" + "".join(f"- [x] {i.strip()}\n" for i in items) if items else ""
    return (f"Closes #{n}\n\nMade by `scripts/fake_agent.py` for an end-to-end run of setwave: the change is the issue's "
            f"`fake-agent` block.{ledger}")


def check_branch(branch: str, n: int) -> None:
    """Push only the issue's own branch (`<kind>/<N>-...`, as dispatch names it): never the base, never another's."""
    if not re.search(rf"/{n}-", branch):
        raise SystemExit(f"fake agent: {branch} is not the branch of #{n}; run me in the worktree `wave dispatch` made for it")


def resolve(wt: Path) -> list[str]:
    """Keep ours then theirs in every conflicted file (the merge base's lines of a diff3 conflict dropped), stage it."""
    files = sh(["git", "diff", "--name-only", "--diff-filter=U"], wt).split()
    for name in files:
        kept, in_base = [], False
        for line in (wt / name).read_text().splitlines(keepends=True):
            m = MARKER.match(line)
            if m:
                in_base = m.group(1).startswith("|")
                continue
            if not in_base:
                kept.append(line)
        (wt / name).write_text("".join(kept))
        sh(["git", "add", name], wt)
    return files


def work(slug: str, n: int, wt: Path) -> str:
    check_target(slug)
    branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], wt).strip()
    check_branch(branch, n)
    iss = json.loads(sh(["gh", "issue", "view", str(n), "-R", slug, "--json", "title,body"], wt))
    apply(wt, change_of(iss["body"]))
    commit(wt, iss["title"], n)
    sh(["git", "push", "-q", "-u", "origin", f"HEAD:refs/heads/{branch}"], wt)
    body = pr_body(n, iss["body"])
    return sh(["gh", "pr", "create", "-R", slug, "--base", "main", "--head", branch, "--title", iss["title"], "--body", body], wt).strip()


def claim_done(body: str) -> str:
    """The lie: every Done-when item ticked, everything outside the section untouched."""
    m = re.search(r"^## Done when[ \t]*\n(.*?)(?=^#{1,6} |\Z)", body, re.S | re.M)
    if not m:
        raise SystemExit("fake agent: the issue has no `## Done when`, so there is nothing to lie about")
    ticked = re.sub(r"(?m)^[-*] \[ \]", "- [x]", m.group(1))
    return body[:m.start(1)] + ticked + body[m.end(1):]


def lie(slug: str, n: int) -> str:
    check_target(slug)
    iss = json.loads(sh(["gh", "issue", "view", str(n), "-R", slug, "--json", "body"], Path.cwd()))
    return sh(["gh", "issue", "edit", str(n), "-R", slug, "--body", claim_done(iss["body"] or "")], Path.cwd())


def main(argv: list[str]) -> None:
    wt = Path.cwd()
    if argv[:1] == ["work"] and len(argv) == 3:
        print(work(argv[1], int(argv[2]), wt))
    elif argv[:1] == ["lie"] and len(argv) == 3:
        lie(argv[1], int(argv[2]))
        print("ticked every Done-when item on", argv[2])
    elif argv == ["resolve"]:
        print("kept both sides in: " + (", ".join(resolve(wt)) or "nothing (no conflict)"))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
