#!/usr/bin/env python3
"""The skill-lint: every skill's frontmatter, checked before CI offers it to the tooling.

A skill whose frontmatter is broken does not fail loudly — it silently stops being offered,
and nothing else in the loop notices. This linter runs in CI (both matrix OSes) and refuses:

- no frontmatter block, or a `skills/<dir>/SKILL.md` that does not exist at all;
- a `name` that is not kebab-case or that differs from its directory;
- a `description` shorter than 40 characters (a skill one cannot summarize cannot be chosen);
- a link to `templates/...` that names no file in the plugin.

    python3 scripts/validate_skills.py [--root PLUGIN_ROOT]    # exit 0 clean, 1 with one line per problem

Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_DESCRIPTION = 40
KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
TEMPLATE_LINK = re.compile(r"templates/[\w\-./]+")


def field(frontmatter: str, name: str) -> str | None:
    m = re.search(rf"^{name}:[ \t]*(.*?)[ \t]*$", frontmatter, re.M)
    return m.group(1) if m else None


def problems(root: Path) -> list[str]:
    """One line per broken skill, named so the fix is obvious; empty when everything passes."""
    skills = root / "skills"
    if not skills.is_dir():
        return [f"{skills}: no skills directory"]
    out = []
    for d in sorted(p for p in skills.iterdir() if p.is_dir() and not p.name.startswith(".")):
        f = d / "SKILL.md"
        where = f.relative_to(root)
        if not f.exists():
            out.append(f"{where}: no SKILL.md in the skill directory")
            continue
        m = FRONTMATTER.match(f.read_text())
        if not m:
            out.append(f"{where}: no `---` frontmatter block at the top")
            continue
        fm = m.group(1)
        name = field(fm, "name")
        if name is None:
            out.append(f"{where}: frontmatter has no `name`")
        elif not KEBAB.fullmatch(name):
            out.append(f"{where}: name `{name}` is not kebab-case")
        elif name != d.name:
            out.append(f"{where}: name `{name}` differs from its directory `{d.name}`")
        description = field(fm, "description")
        if not description:
            out.append(f"{where}: frontmatter has no `description`")
        elif len(description) < MIN_DESCRIPTION:
            out.append(f"{where}: description is {len(description)} chars, the floor is {MIN_DESCRIPTION}")
        for link in sorted(set(TEMPLATE_LINK.findall(f.read_text()))):
            if not (root / link).exists():
                out.append(f"{where}: dead template link `{link}`")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="validate-skills", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=str(HERE.parent), help="the plugin root (default: this repository)")
    a = p.parse_args(argv)
    root = Path(a.root).resolve()
    if not root.is_dir():
        print(f"{root}: not a directory", file=sys.stderr)
        return 2
    found = problems(root)
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print(f"{len(found)} broken skill(s); a broken frontmatter silently removes the skill from the tooling", file=sys.stderr)
        return 1
    print(f"skills: every SKILL.md frontmatter is valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
