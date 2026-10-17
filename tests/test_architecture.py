"""scripts/wave.py stays one file (ADR 008), so its size is held by a limit per section, not by a package.

A section runs from one `# ---- name` banner to the next; what precedes the first banner (the docstring, the
imports, the constants) is the section "header". Past SECTION_LIMIT lines, split that section out or write the ADR
that raises the limit: the number below and the one in docs/decisions/008-wave-module-threshold.md move together.
"""
import re
import unittest

from helpers import WAVE

SECTION_LIMIT = 1100  # measured 2026-10-17: the largest section, `commands`, has 993 lines (ADR 008)
BANNER = re.compile(r"^# -{20,} (.+)$")


def section_lines(text: str) -> dict[str, int]:
    sections, name = {"header": 0}, "header"
    for line in text.splitlines():
        m = BANNER.match(line)
        if m:
            name = m.group(1)
            sections.setdefault(name, 0)
        sections[name] += 1
    return sections


def oversize(text: str, limit: int = SECTION_LIMIT) -> dict[str, int]:
    return {n: c for n, c in section_lines(text).items() if c > limit}


class SectionLimit(unittest.TestCase):
    def test_no_section_of_wave_py_passes_the_limit(self):
        self.assertEqual(oversize(WAVE.read_text()), {}, f"split the section or raise SECTION_LIMIT with a new ADR ({SECTION_LIMIT})")

    def test_the_banners_are_still_found(self):
        text = WAVE.read_text()
        sections = section_lines(text)
        self.assertGreaterEqual(len(sections), 20, "a reworded banner would hide a section inside its neighbour")
        self.assertEqual(sum(sections.values()), len(text.splitlines()))

    def test_a_section_past_the_limit_is_named(self):
        text = "header\n# " + "-" * 20 + " big\n" + "x = 1\n" * 5 + "# " + "-" * 20 + " small\nx = 1\n"
        self.assertEqual(oversize(text, limit=5), {"big": 6})
        self.assertEqual(oversize(text, limit=6), {})


if __name__ == "__main__":
    unittest.main()
