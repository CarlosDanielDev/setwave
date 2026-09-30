#!/usr/bin/env python3
"""One end-to-end run of setwave against a sandbox repository, every step through `wave` itself.

    python3 scripts/e2e.py <checkout of owner/name-sandbox> [--any-repo] [--ci-timeout S]

plan an epic of three leaves from tests/e2e-plan/ -> dispatch them -> scripts/fake_agent.py plays each agent
(change, commit `Closes #N`, push, PR) -> verify --epic -> order: the chain names the leaf that conflicts ->
order --plan + merge --plan --yes --wait-base-ci for the others -> wave resolve on the conflicting one (stops on
the conflict by design), the fake agent keeps both sides, resolve --continue gates and pushes -> order --plan +
merge for it -> close-parents --include-epic -> cleanup -> status --post. Every step is timed and logged; a step
that exits other than expected stops the run and says what is left open.

Running this is the owner's OK for the merges it makes, in the sandbox and nowhere else: it refuses the plugin's
own repository always, and any repository whose name does not end in `-sandbox` unless --any-repo. It never
deletes a repository, an issue or a branch; the sandbox keeps every run's history (`wave stats`, the epics).
The sandbox needs, once: a `.wave.json` with a gate, and a CI workflow (merge waits for green checks).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WAVE = HERE / "wave.py"
FAKE_AGENT = HERE / "fake_agent.py"
PLAN = HERE.parent / "tests" / "e2e-plan"
OWN = "CarlosDanielDev/setwave"  # the plugin's own repository: an e2e run never touches it, flag or not

_spec = importlib.util.spec_from_file_location("wave", WAVE)
wave = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wave)


def check_target(slug: str, any_repo: bool) -> None:
    if slug.lower() == OWN.lower():
        raise SystemExit(f"e2e refused: {slug} is the plugin's own repository; an e2e run never runs against it")
    if not slug.lower().endswith("-sandbox") and not any_repo:
        raise SystemExit(f"e2e refused: {slug} does not end in -sandbox. The run opens issues and merges PRs there; "
                         "pass --any-repo only for a repository that exists to be written to")


def prepare_plan(src: Path, dest: Path, run_id: str) -> None:
    """A copy of the plan with {{run}} filled, so every run has titles of its own (`plan` refuses a title twice)."""
    dest.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.iterdir()):
        if f.suffix in (".md", ".tsv"):
            (dest / f.name).write_text(f.read_text().replace("{{run}}", run_id))


def step(log: list, name: str, cmd: list[str], cwd: Path, expect: int = 0) -> str:
    """Run one step, print and log it (exit, expected, seconds); stop the run when it exits otherwise."""
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    rec = {"step": name, "cmd": " ".join(Path(c).name if c in (sys.executable, str(WAVE), str(FAKE_AGENT)) else c for c in cmd),
           "exit": p.returncode, "expected": expect, "seconds": round(time.time() - t0, 1)}
    log.append(rec)
    print(f"[{rec['seconds']:>6.1f}s] exit {rec['exit']} (expected {expect})  {name}: {rec['cmd']}", flush=True)
    for line in (p.stdout + p.stderr).rstrip().splitlines():
        print(f"           {line}")
    if p.returncode != expect:
        raise SystemExit(f"e2e stopped at step {name!r}: exit {p.returncode}, expected {expect}")
    return p.stdout


def wait(log: list, name: str, fn, timeout: int, every: int = 10):
    """Poll `fn` until it returns something other than None; logged like a step (exit 1 on timeout)."""
    t0 = time.time()
    r = wave.wait_for(fn, timeout, every)
    rec = {"step": name, "cmd": "(poll)", "exit": 0 if r is not None else 1, "expected": 0, "seconds": round(time.time() - t0, 1)}
    log.append(rec)
    print(f"[{rec['seconds']:>6.1f}s] exit {rec['exit']} (expected 0)  {name}: {r}", flush=True)
    if r is None:
        raise SystemExit(f"e2e stopped at step {name!r}: nothing after {timeout}s")
    return r


def gh_json(args: list[str]):
    p = subprocess.run(["gh", *args], text=True, capture_output=True)
    return json.loads(p.stdout) if p.stdout.strip() else None


def checks_done(slug: str, pr: int):
    """The PR's check buckets once none is pending; None while any is (or none is reported yet)."""
    buckets = [c["bucket"] for c in gh_json(["pr", "checks", str(pr), "-R", slug, "--json", "bucket"]) or []]
    if not buckets or "pending" in buckets:
        return None
    if set(buckets) - {"pass", "skipping"}:
        raise SystemExit(f"e2e stopped: {slug}#{pr} checks are {buckets}")
    return buckets


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="e2e", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("checkout", help="local clone of the sandbox repository")
    p.add_argument("--any-repo", action="store_true", help="allow a repository whose name does not end in -sandbox")
    p.add_argument("--ci-timeout", type=int, default=900)
    a = p.parse_args(argv)
    root = Path(a.checkout).expanduser().resolve()
    ok, url = wave.sh_ok(["git", "remote", "get-url", "origin"], cwd=root)
    slug = wave.slug_of_url(url) if ok else None
    if not slug:
        raise SystemExit(f"e2e refused: {root} is not a clone with an origin")
    check_target(slug, a.any_repo)
    if not json.loads((root / ".wave.json").read_text() if (root / ".wave.json").exists() else "{}").get("gate"):
        raise SystemExit(f"e2e refused: {root}/.wave.json declares no gate")
    if not list((root / ".github" / "workflows").glob("*.y*ml")):
        raise SystemExit(f"e2e refused: {slug} has no CI workflow, and `merge` merges only on green checks")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    run_dir = Path(tempfile.mkdtemp(prefix=f"setwave-e2e-{run_id}-"))
    prepare_plan(PLAN, run_dir / "plan", run_id)
    log: list[dict] = []
    wv = lambda *args: [sys.executable, str(WAVE), *args]
    fa = lambda *args: [sys.executable, str(FAKE_AGENT), *args]
    print(f"e2e run {run_id} against {slug} ({root}); plan and log in {run_dir}\n")
    t0 = time.time()
    epic, prs = None, {}
    try:
        step(log, "register", wv("repos", "add", str(root)), root)
        step(log, "preflight", wv("doctor", "--batch", "3"), root)
        step(log, "plan", wv("plan", str(run_dir / "plan"), "--slug", slug), root)
        nums = json.loads((run_dir / "plan" / "numbers.json").read_text())
        epic, leaves = nums["e"], [nums[k] for k in "abc"]
        step(log, "next", wv("next", str(epic), "--batch", "3"), root)
        out = step(log, "dispatch", wv("dispatch", *map(str, leaves)), root)
        wts = {int(n): Path(w) for n, w in re.findall(rf"^{re.escape(slug)}#(\d+): \S+ -> (\S+)$", out, re.M)}
        for n in leaves:
            url = step(log, f"agent #{n}", fa("work", slug, str(n)), wts[n]).strip().splitlines()[-1]
            prs[n] = int(url.rstrip("/").split("/")[-1])
        for pr in prs.values():
            wait(log, f"CI on PR {pr}", lambda pr=pr: checks_done(slug, pr), a.ci_timeout)
        step(log, "verify", wv("verify", "--epic", str(epic)), root)
        chain = json.loads(step(log, "order (predict)", wv("order", "--epic", str(epic), "--json"), root))[slug]["chain"]
        conflicting = [n for n, files in chain if files]
        if len(conflicting) != 1:
            raise SystemExit(f"e2e stopped: the plan arranges one conflicting leaf, the chain predicts {chain}")
        late = conflicting[0]
        first = [str(n) for n, files in chain if not files]
        say = f"OK: you ran scripts/e2e.py against {slug}; that is the owner's OK for this merge, in {slug} and nowhere else."
        step(log, "order --plan", wv("order", *first, "--plan", str(run_dir / "plan-1.json")), root)
        print(say)
        step(log, "merge", wv("merge", "--plan", str(run_dir / "plan-1.json"), "--yes", "--wait-base-ci", "--ci-timeout", str(a.ci_timeout)), root)
        step(log, f"resolve {late} (conflict)", wv("resolve", str(late)), root, expect=2)
        step(log, f"agent resolves {late}", fa("resolve"), wts[next(n for n, pr in prs.items() if pr == late)])
        step(log, f"resolve {late} --continue", wv("resolve", str(late), "--continue"), root)
        wait(log, f"CI on PR {late}", lambda: checks_done(slug, late), a.ci_timeout)
        step(log, "order --plan", wv("order", str(late), "--plan", str(run_dir / "plan-2.json")), root)
        print(say)
        step(log, "merge", wv("merge", "--plan", str(run_dir / "plan-2.json"), "--yes", "--wait-base-ci", "--ci-timeout", str(a.ci_timeout)), root)
        wait(log, "leaves closed", lambda: all((gh_json(["issue", "view", str(n), "-R", slug, "--json", "state"]) or {}).get("state") == "CLOSED"
                                               for n in leaves) or None, 300, 5)
        step(log, "close-parents", wv("close-parents", str(epic), "--include-epic"), root)
        step(log, "cleanup", wv("cleanup"), root)
        step(log, "status --post", wv("status", str(epic), "--post"), root)
    finally:
        (run_dir / "e2e-log.json").write_text(json.dumps({"run": run_id, "slug": slug, "steps": log}, indent=2) + "\n")
        if epic:
            print(f"\n{slug}: epic #{epic}, leaf -> PR {prs or 'none yet'}; `wave status {slug}#{epic}` shows what is left open")
        print(f"\n{len(log)} steps in {time.time() - t0:.0f}s; log: {run_dir / 'e2e-log.json'}")
        print(f"{'step':<28}{'exit':>5}{'want':>5}{'seconds':>9}")
        for r in log:
            print(f"{r['step']:<28}{r['exit']:>5}{r['expected']:>5}{r['seconds']:>9.1f}")


if __name__ == "__main__":
    main()
