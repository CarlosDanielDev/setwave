"""`docs/`: the share card, canonical, sitemap and FAQ markup the Pages site needs to unfurl and index.

GitHub Pages runs no build, so every one of these is a committed file; nothing else notices
when one goes missing or drifts from the page, and a chat just shows the default grey card.
"""
import json
import re
import struct
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
README = DOCS.parent / "README.md"
SITE = "https://carlosdanieldev.github.io/setwave/"
HTML = (DOCS / "index.html").read_text()


def meta(prop):
    m = re.search(r'<meta (?:property|name)="%s" content="([^"]*)"' % re.escape(prop), HTML)
    return m.group(1) if m else None


class ShareCard(unittest.TestCase):
    def test_png_is_1200x630(self):
        head = (DOCS / "og-card.png").read_bytes()[:24]
        self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", head[16:24]), (1200, 630))

    def test_page_references_card_with_dimensions(self):
        for prop in ("og:image", "twitter:image"):
            self.assertEqual(meta(prop), SITE + "og-card.png", prop)
        self.assertEqual(meta("og:image:width"), "1200")
        self.assertEqual(meta("og:image:height"), "630")
        self.assertEqual(meta("twitter:card"), "summary_large_image")


class Canonical(unittest.TestCase):
    def test_canonical_and_og_url_are_the_pages_url(self):
        self.assertIn('<link rel="canonical" href="%s">' % SITE, HTML)
        self.assertEqual(meta("og:url"), SITE)


class Crawlers(unittest.TestCase):
    def test_sitemap_lists_the_page(self):
        self.assertIn("<loc>%s</loc>" % SITE, (DOCS / "sitemap.xml").read_text())

    def test_robots_allows_all_and_points_at_sitemap(self):
        robots = (DOCS / "robots.txt").read_text()
        self.assertIn("Sitemap: %ssitemap.xml" % SITE, robots)
        self.assertNotRegex(robots, r"(?m)^Disallow:\s*/\s*$")


class Faq(unittest.TestCase):
    def jsonld(self):
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', HTML, re.S)
        return [json.loads(b) for b in blocks]

    def test_faqpage_matches_the_visible_details(self):
        faq = [b for b in self.jsonld() if b.get("@type") == "FAQPage"]
        self.assertEqual(len(faq), 1)
        questions = [(q["name"], q["acceptedAnswer"]["text"]) for q in faq[0]["mainEntity"]]
        shown = re.findall(r"<summary>(.*?)</summary>", HTML)
        self.assertEqual([q for q, _ in questions], shown)
        for _, answer in questions:
            self.assertTrue(answer.strip())
        for q in faq[0]["mainEntity"]:
            self.assertEqual(q["@type"], "Question")
            self.assertEqual(q["acceptedAnswer"]["@type"], "Answer")


class Readme(unittest.TestCase):
    def test_showcase_badge_links_the_page(self):
        self.assertRegex(README.read_text(), r"\[!\[Showcase\]\([^)]*\)\]\(%s\)" % re.escape(SITE))


if __name__ == "__main__":
    unittest.main()
