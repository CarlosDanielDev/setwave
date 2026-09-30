"""A gate for the chain-gate tests: green on either half of a semantic conflict, red on their union.

sem/rows.txt says how many rows the screen has; sem/notice.txt takes one of them. Alone, each
branch keeps the count right; merged, the body is one row short, like dev-cleaner #107 x #109.
It appends its working directory to $GATE_CWD_LOG, leaves an untracked file behind (a real gate
writes build output), and fails everywhere when $GATE_RED is set, so a red base can be simulated.
"""
import os
import sys
from pathlib import Path

with open(os.environ["GATE_CWD_LOG"], "a") as log:
    log.write(os.getcwd() + "\n")
Path("gate-output.txt").write_text("build output\n")
if os.environ.get("GATE_RED"):
    sys.exit("the base itself is red")
rows = int(Path("sem/rows.txt").read_text()) if Path("sem/rows.txt").exists() else 25
notice = 1 if Path("sem/notice.txt").exists() else 0
if rows - notice != 25 and Path("sem/rows.txt").exists():
    sys.exit(f"expected 25 body rows, got {rows - notice}: the union is red")
print("25 body rows")
