"""`.wave.json` is read from origin/<base>, the tree every worktree is cut from, not from the main checkout.

A repo o/s whose main checkout S is behind its remote: a second clone pushed a new gate that S has
not pulled. The prompt, `facts` and `doctor` must carry the remote gate. A repo o/u whose
`.wave.json` exists only in the working tree keeps using it, since there is no remote copy.
"""
import json
import unittest

from helpers import TMP, git, run_wave, sandbox, wave

S = TMP / "s"
S2 = TMP / "s2"
S3 = TMP / "s3"  # a second main checkout left behind, fetched only by the prompt test
U = TMP / "u"
OLD = {"base": "main", "gate": ["python3 -m py_compile x.py"]}
NEW = {"base": "main", "gate": ["python3 -m unittest discover -s tests -v"]}


def setUpModule():
    sandbox()
    if S.exists():
        return
    bare = TMP / "remote" / "o" / "s.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(S), cwd=TMP)
    (S / ".wave.json").write_text(json.dumps(OLD))
    git("add", ".", cwd=S)
    git("commit", "-q", "-m", "base", cwd=S)
    git("push", "-q", "origin", "main", cwd=S)
    git("clone", "-q", str(bare), str(S3), cwd=TMP)
    git("clone", "-q", str(bare), str(S2), cwd=TMP)  # someone else changes the gate; S never pulls it
    (S2 / ".wave.json").write_text(json.dumps(NEW))
    git("commit", "-q", "-am", "gate: the test suite", cwd=S2)
    git("push", "-q", "origin", "main", cwd=S2)

    bare = TMP / "remote" / "o" / "u.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(U), cwd=TMP)
    (U / "f.txt").write_text("f\n")
    git("add", ".", cwd=U)
    git("commit", "-q", "-m", "base", cwd=U)
    git("push", "-q", "origin", "main", cwd=U)
    (U / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["make check"]}))  # never committed


class FromOrigin(unittest.TestCase):
    def test_the_prompt_carries_the_gate_of_origin_after_the_fetch(self):
        repo = wave.Repo("o/s", {"path": str(S3)})
        self.assertEqual(repo.gate(), OLD["gate"], "origin/main as last fetched: still the old gate")
        repo.fetch()  # what dispatch and prompt do before rendering
        self.assertEqual(json.loads((S3 / ".wave.json").read_text()), OLD, "the main checkout is still behind")
        self.assertEqual(repo.gate(), NEW["gate"])
        text = wave.render_prompt(repo, {"number": 7, "title": "Some work", "html_url": "u"}, "fix/7-x", "abc1234", 1)
        self.assertIn(NEW["gate"][0], text)
        self.assertNotIn(OLD["gate"][0], text)

    def test_facts_names_where_the_config_came_from(self):
        git("fetch", "-q", "origin", cwd=S)
        p = run_wave("facts", cwd=S)
        self.assertEqual(p.returncode, 0, p.stderr)
        facts = json.loads(p.stdout)
        self.assertEqual(facts["config_source"], "origin/main:.wave.json")
        self.assertEqual(facts["gate"], NEW["gate"])

    def test_doctor_warns_when_the_main_checkout_copy_differs(self):
        p = run_wave("doctor", cwd=S)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("  ! main checkout's `.wave.json` matches origin/main's: differs in gate", p.stdout)
        self.assertIn("  ✓ gate is declared and real: " + NEW["gate"][0] + "\n", p.stdout)

    def test_no_remote_copy_falls_back_to_the_working_tree(self):
        repo = wave.Repo("o/u", {"path": str(U)})
        repo.fetch()
        self.assertEqual(repo.gate(), ["make check"])
        self.assertEqual(repo.cfg_source, "working tree .wave.json")
        self.assertIn("  ✓ main checkout's `.wave.json` matches origin/main's: no copy on origin/main, the working tree's is used\n",
                      run_wave("doctor", cwd=U).stdout)


if __name__ == "__main__":
    unittest.main()
