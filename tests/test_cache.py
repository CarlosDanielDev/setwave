"""The package cache: a crate cargo marked extracted but left half-written, found before a wave pays for it.

A fourth repo, o/c, is a Cargo repo whose Cargo.lock names five packages. Each test gets its own
CARGO_HOME under the temporary directory, fabricated with `tarfile`; the real ~/.cargo is never read.
  whole-1.0.0     archive and directory agree, `.cargo-ok` present
  half-2.0.0      `.cargo-ok` present, two of the archive's four files missing: the incident
  never-1.0.0     archive only, never extracted: cargo extracts it on the next build
  nomarker-1.0.0  files missing and no `.cargo-ok`: cargo removes and re-extracts it by itself
  gitdep, local   not from a registry: not looked at
"""
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from helpers import TMP, WAVE, git, sandbox

C = TMP / "c"
INDEX = "index.crates.io-1949cf8c6b5b557f"
LOCK = """version = 3

[[package]]
name = "whole"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "half"
version = "2.0.0"
source = "sparse+https://index.crates.io/"

[[package]]
name = "never"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "nomarker"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "gitdep"
version = "0.1.0"
source = "git+https://github.com/x/y#abc"

[[package]]
name = "local"
version = "0.1.0"
"""
FILES = {"whole-1.0.0": ["Cargo.toml", "src/lib.rs"],
         "half-2.0.0": ["Cargo.toml", "src/lib.rs", "src/target/apple.rs", "src/generated.rs"],
         "never-1.0.0": ["Cargo.toml", "src/lib.rs"],
         "nomarker-1.0.0": ["Cargo.toml", "src/lib.rs"]}
ON_DISK = {"whole-1.0.0": (FILES["whole-1.0.0"], True),
           "half-2.0.0": (["Cargo.toml", "src/lib.rs"], True),
           "nomarker-1.0.0": (["Cargo.toml"], False)}


def cargo_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="cargo-", dir=TMP))
    (home / "registry" / "cache" / INDEX).mkdir(parents=True)
    for crate, names in FILES.items():
        with tarfile.open(home / "registry" / "cache" / INDEX / f"{crate}.crate", "w:gz") as tar:
            for name in names:
                data = f"// {name}\n".encode()
                info = tarfile.TarInfo(f"{crate}/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    for crate, (names, marker) in ON_DISK.items():
        d = home / "registry" / "src" / INDEX / crate
        for name in names:
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(f"// {name}\n")
        if marker:
            (d / ".cargo-ok").write_text('{"v":1}')
    return home


def wave_in(cwd: Path, *args, **env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WAVE), *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **env})


def line(out: str, text: str) -> str:
    return next(l for l in out.splitlines() if text in l)


def setUpModule():
    sandbox()
    if C.exists():
        return
    bare = TMP / "remote" / "o" / "c.git"
    git("init", "-q", "--bare", str(bare), cwd=TMP)
    git("clone", "-q", str(bare), str(C), cwd=TMP)
    (C / ".wave.json").write_text(json.dumps({"base": "main", "gate": ["true"]}))
    (C / "Cargo.toml").write_text('[package]\nname = "local"\nversion = "0.1.0"\n')
    (C / "Cargo.lock").write_text(LOCK)
    git("add", ".", cwd=C)
    git("commit", "-q", "-m", "base", cwd=C)
    git("push", "-q", "origin", "main", cwd=C)


class Doctor(unittest.TestCase):
    def setUp(self):
        self.home = cargo_home()
        self.src = self.home / "registry" / "src" / INDEX

    def test_a_half_extracted_crate_is_a_hard_failure_named_and_left_in_place(self):
        p = wave_in(C, "doctor", CARGO_HOME=str(self.home))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        bad = line(p.stdout, "cargo cache")
        self.assertTrue(bad.startswith("  ✗ "), bad)
        self.assertIn("half-2.0.0 is missing 2 of 4 files (src/target/apple.rs, src/generated.rs)", bad)
        self.assertIn("`wave doctor --fix-cache`", bad)
        for name in ("whole-1.0.0", "never-1.0.0", "nomarker-1.0.0", "gitdep", "local-"):
            self.assertNotIn(name, bad)
        self.assertTrue((self.src / "half-2.0.0" / "src" / "lib.rs").exists(), "doctor alone never moves anything")

    def test_fix_cache_moves_the_broken_crate_to_quarantine_and_nothing_else(self):
        p = wave_in(C, "doctor", "--fix-cache", CARGO_HOME=str(self.home))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse((self.src / "half-2.0.0").exists())
        moved = sorted((TMP / "config" / "quarantine").glob("half-2.0.0-*"))
        self.assertEqual(len(moved), 1, moved)
        self.assertEqual((moved[0] / "src" / "lib.rs").read_text(), "// src/lib.rs\n", "moved whole, not deleted")
        self.assertIn(f"moved half-2.0.0 to {moved[0]}", line(p.stdout, "cargo cache"))
        for crate in ("whole-1.0.0", "nomarker-1.0.0"):
            self.assertTrue((self.src / crate).exists(), crate)
        again = wave_in(C, "doctor", CARGO_HOME=str(self.home))
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("  ✓ cargo cache", again.stdout)

    def test_a_repo_without_cargo_lock_is_not_looked_at(self):
        p = wave_in(sandbox(), "doctor", CARGO_HOME=str(self.home))
        self.assertNotIn("cargo cache", p.stdout)


class Batch(unittest.TestCase):
    def test_more_than_six_says_why_in_one_sentence(self):
        home = str(cargo_home())
        (Path(home) / "registry" / "src").rename(Path(home) / "registry" / "src-aside")  # a whole cache: only the batch speaks
        big = wave_in(C, "doctor", "--batch", "7", CARGO_HOME=home)
        self.assertEqual(big.returncode, 0, big.stdout + big.stderr)
        warn = line(big.stdout, "batch of 7")
        self.assertTrue(warn.startswith("  ! "), warn)
        self.assertIn("rate limit", warn)
        self.assertIn("half-extracted", warn)
        self.assertIn("--warm", warn)
        self.assertNotIn("batch of 6", wave_in(C, "doctor", "--batch", "6", CARGO_HOME=home).stdout)


class Warm(unittest.TestCase):
    def fake_cargo(self, code: int) -> tuple[Path, Path]:
        bin_dir = Path(tempfile.mkdtemp(prefix="bin-", dir=TMP))
        log = bin_dir / "cargo.log"
        (bin_dir / "cargo").write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            f"open({str(log)!r}, 'a').write(json.dumps([os.getcwd(), sys.argv[1:], os.path.exists({str(TMP / 'c-8')!r})]) + '\\n')\n"
            f"sys.exit({code})\n")
        (bin_dir / "cargo").chmod(0o755)
        return bin_dir, log

    def test_warm_fetches_once_in_the_main_checkout_before_any_worktree(self):
        bin_dir, log = self.fake_cargo(0)
        p = wave_in(C, "dispatch", "8", "--force", "--warm", PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual([json.loads(l) for l in log.read_text().splitlines()], [[str(C), ["fetch"], False]])
        self.assertTrue((TMP / "c-8").exists())

    def test_a_failed_fetch_refuses_the_dispatch(self):
        bin_dir, log = self.fake_cargo(101)
        p = wave_in(C, "dispatch", "9", "--force", "--warm", PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("cargo fetch", p.stdout + p.stderr)
        self.assertFalse((TMP / "c-9").exists())


if __name__ == "__main__":
    unittest.main()
