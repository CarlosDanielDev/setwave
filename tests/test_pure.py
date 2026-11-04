"""The functions that read text and return text: no gh, no git, no network."""
import unittest

from helpers import wave


class SlugOfUrl(unittest.TestCase):
    def test_every_origin_form(self):
        for url in ("git@github.com:o/r.git", "https://github.com/o/r.git", "https://github.com/o/r",
                    "https://github.com/o/r/", "ssh://git@github.com/o/r.git", "/tmp/remote/o/r.git\n"):
            with self.subTest(url=url):
                self.assertEqual(wave.slug_of_url(url), "o/r")

    def test_dots_and_dashes_survive(self):
        self.assertEqual(wave.slug_of_url("git@github.com:my-org/my.repo.git"), "my-org/my.repo")

    def test_not_a_url(self):
        self.assertIsNone(wave.slug_of_url("nothing"))


class ParseRef(unittest.TestCase):
    def setUp(self):
        self.default = object()
        self.other = object()
        wave.Repo._cache["x/y"] = self.other  # Repo.get answers from its cache first

    def tearDown(self):
        wave.Repo._cache.pop("x/y", None)

    def test_bare_number_and_hash_mean_the_default_repo(self):
        for text in ("12", "#12", " #12 "):
            with self.subTest(text=text):
                self.assertEqual(wave.parse_ref(text, self.default), (self.default, 12))

    def test_slug_names_another_repo(self):
        self.assertEqual(wave.parse_ref("x/y#7", self.default), (self.other, 7))

    def test_bad_refs_are_refused(self):
        for text in ("x/y", "#", "abc", "x/y#7#8", "12a"):
            with self.subTest(text=text), self.assertRaises(SystemExit):
                wave.parse_ref(text, self.default)

    def test_bare_ref_without_a_repo_is_refused(self):
        with self.assertRaises(SystemExit):
            wave.parse_ref("#12", None)


class BranchFor(unittest.TestCase):
    def test_the_handoff_line_wins(self):
        iss = {"number": 3, "title": "Anything", "labels": [],
               "body": "```bash\ngit worktree add -b feat/3-from-body ../r-3 origin/main\n```"}
        self.assertEqual(wave.branch_for(iss), "feat/3-from-body")

    def test_derived_from_the_title(self):
        iss = {"number": 4, "title": "Make `wave next` see blockers!", "labels": [], "body": None}
        self.assertEqual(wave.branch_for(iss), "feat/4-make-wave-next-see-blockers")

    def test_a_bug_is_a_fix(self):
        iss = {"number": 5, "title": "Crash", "labels": [{"name": "bug"}], "body": ""}
        self.assertEqual(wave.branch_for(iss), "fix/5-crash")

    def test_long_titles_are_cut_without_a_trailing_dash(self):
        iss = {"number": 6, "title": "a" * 39 + " bcdef", "labels": [], "body": ""}
        self.assertEqual(wave.branch_for(iss), "feat/6-" + "a" * 39)


class CodegraphQuery(unittest.TestCase):
    def test_the_body_query_wins(self):
        iss = {"title": "Ignored", "body": 'first `codegraph explore "parse_ref candidates"` then read'}
        self.assertEqual(wave.codegraph_query(iss), "parse_ref candidates")

    def test_fallback_is_the_title_words_of_four_letters_or_more(self):
        iss = {"title": "Fix the merge tree for a big repo now please", "body": None}
        self.assertEqual(wave.codegraph_query(iss), "merge tree repo please")


class FillRefs(unittest.TestCase):
    def test_mention_and_bare_number(self):
        body = "Parent: {{epic}}\ngit worktree add -b feat/{{t1.n}}-x\nCloses #{{t1.n}}, after {{t0}}. {{unknown}} stays."
        self.assertEqual(wave.fill_refs(body, {"epic": 1, "t0": 5, "t1": 6}),
                         "Parent: #1\ngit worktree add -b feat/6-x\nCloses #6, after #5. {{unknown}} stays.")


class Closes(unittest.TestCase):
    """Every phrasing GitHub documents for linking a PR to an issue (docs.github.com, "Linking a pull request to an issue")."""

    def test_every_keyword(self):
        for kw in ("close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved"):
            with self.subTest(kw=kw):
                self.assertEqual(wave.CLOSES.findall(f"This PR {kw} #10."), [("", "10")])

    def test_uppercase_and_colons(self):
        for text in ("CLOSES #10", "Closes: #10", "CLOSES: #10", "fixes:  #10"):
            with self.subTest(text=text):
                self.assertEqual(wave.CLOSES.findall(text), [("", "10")])

    def test_another_repository(self):
        self.assertEqual(wave.CLOSES.findall("Fixes octo-org/octo.repo#100"), [("octo-org/octo.repo", "100")])

    def test_several_issues(self):
        self.assertEqual(wave.CLOSES.findall("Resolves #10, resolves #123, resolves octo-org/octo-repo#100"),
                         [("", "10"), ("", "123"), ("octo-org/octo-repo", "100")])

    def test_not_a_closing_phrase(self):
        for text in ("Refs #10", "disclose #10", "closes #", "closes #10abc", "prefix #10"):
            with self.subTest(text=text):
                self.assertEqual(wave.CLOSES.findall(text), [])


class ZoneEstimate(unittest.TestCase):
    """#121: the crude, documented context estimate and the zone words every print carries."""

    def test_the_estimate_is_items_and_files_times_named_constants(self):
        self.assertEqual(wave.zone_estimate({"items": 4, "files": 3}),
                         4 * wave.TOKENS_PER_ITEM + 3 * wave.TOKENS_PER_FILE)
        self.assertEqual(wave.zone_estimate({"items": 0, "files": 0}), 0)

    def test_the_default_zone_is_the_heuristic_band(self):
        self.assertEqual(wave.DEFAULT_ZONE, 100000)

    def test_the_size_line_says_estimate_and_the_fit(self):
        fits = wave.size_note({"size": "small", "items": 1, "files": 1, "miller": "verde"}, zone=100000)
        self.assertIn("crude", fits, "the wording says crude, never a live context reading")
        self.assertIn("estimate", fits)
        self.assertIn("fits the zone", fits)
        over = wave.size_note({"size": "large", "items": 30, "files": 10, "miller": "vermelho"}, zone=100000)
        self.assertIn("over the zone — split", over)

    def test_without_a_zone_the_zone_clause_is_absent(self):
        self.assertEqual(
            wave.size_note({"size": "small", "items": 1, "files": 1, "miller": "verde"}),
            "Size: small (1 Done-when items, 1 files cited) — one agent, no judge/fix phase — "
            "the owner reviews the diff directly; Miller: verde")

    def test_the_zone_state_words(self):
        self.assertEqual([wave.zone_state(u, 100000) for u in (None, 1, 79999, 80000, 100000, 100001)],
                         ["no usage", "in-zone", "in-zone", "near-edge", "near-edge", "over"])

    def test_the_zone_edge_is_exact_integer_math_on_non_round_zones(self):
        """#127: `int(zone * 0.8)` truncates — zone 7 has its 80% edge at 5.6, so 5 sits in-zone and
        6 turns near-edge; exact integer math (`used * 5 >= zone * 4`) never errs a token."""
        self.assertEqual([wave.zone_state(u, 7) for u in (5, 6, 7, 8)],
                         ["in-zone", "near-edge", "near-edge", "over"])


class MillerClass(unittest.TestCase):
    """#126, the attention gate from the owner's vault kit (`_AI/Skills/engenharia-de-atencao/miller-
    carga-cognitiva.md`; Miller 1956, Cowan 2001): META = 5, TETO DURO = 7, above 7 reproves. The
    boundary is exact — 5 is still verde, 7 is still inside, 8 reproves."""

    def test_the_constants_carry_meta_and_ceiling_per_dimension(self):
        self.assertEqual(wave.LIMITES, {"items": (5, 7), "files": (5, 7), "concepts": (1, 2)})

    def test_the_items_boundary_is_exact_4_5_6_7_8(self):
        for n, want in ((4, "verde"), (5, "verde"), (6, "âmbar"), (7, "âmbar"), (8, "vermelho")):
            with self.subTest(items=n):
                self.assertEqual(wave.miller_class(n, 0, 0), want)

    def test_the_files_boundary_is_exact_4_5_6_7_8(self):
        for n, want in ((4, "verde"), (5, "verde"), (6, "âmbar"), (7, "âmbar"), (8, "vermelho")):
            with self.subTest(files=n):
                self.assertEqual(wave.miller_class(0, n, 0), want)

    def test_the_concepts_boundary_is_exact(self):
        # the kit's third counter is coarser: forks 1–2, so verde ends at 1 and everything past 2 reproves
        for n, want in ((0, "verde"), (1, "verde"), (2, "âmbar"), (3, "vermelho")):
            with self.subTest(concepts=n):
                self.assertEqual(wave.miller_class(0, 0, n), want)
        for n in (4, 5, 6, 7, 8):
            with self.subTest(concepts=n):
                self.assertEqual(wave.miller_class(0, 0, n), "vermelho")

    def test_the_dimensions_combine_and_one_past_the_ceiling_reproves(self):
        self.assertEqual(wave.miller_class(2, 4, 1), "verde")
        self.assertEqual(wave.miller_class(2, 4, 2), "âmbar")
        self.assertEqual(wave.miller_class(5, 8, 1), "vermelho", "one dimension past the ceiling reproves")
        self.assertEqual(wave.miller_class(8, 1, 1), "vermelho")

    def test_the_named_adr_forks_come_from_the_stub(self):
        body = "## ADR stub\n\nWhere the class lives: (a) beside `issue_size`; (b) a new section.\n"
        self.assertEqual(wave.miller_concepts(body), 2)
        self.assertEqual(wave.miller_concepts("## ADR stub\n\nOne option, no fork named.\n"), 0)
        self.assertEqual(wave.miller_concepts("no stub here"), 0)

    def test_issue_size_counts_miller_from_the_body(self):
        body = "## Done when\n\n" + "- [ ] a\n" * 6
        self.assertEqual(wave.issue_size(body)["miller"], "âmbar", "6 items: past the meta of 5, inside the ceiling of 7")
        body += "- [ ] b\n- [ ] c\n"
        self.assertEqual(wave.issue_size(body)["miller"], "vermelho", "8 items is past the hard ceiling")


if __name__ == "__main__":
    unittest.main()
