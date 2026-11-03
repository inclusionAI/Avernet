"""The unit gate must count uncovered relocated and subsequently added code."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "apps/bcs/scripts/cov_gate.py"


class ChangedLineCoverageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bcs = self.root / "apps/bcs"
        self.source = self.bcs / "crates/tools/bcs-cli/src/cli/new_behavior.rs"
        self.source.parent.mkdir(parents=True)
        self.git("init", "--initial-branch=dev")
        self.git("config", "user.name", "Coverage regression")
        self.git("config", "user.email", "coverage@example.test")
        (self.root / "README.md").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.source.write_text("\n".join(f"fn branch_{n}() {{}}" for n in range(10)) + "\n")
        self.git("add", ".")
        self.git("commit", "-m", "new behavior")
        result = self.bcs / "testresult"
        result.mkdir()
        (result / "junit.xml").write_text('<testsuites tests="1" failures="0" errors="0" skipped="0"/>')

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def run_gate(self, covered):
        lines = ''.join(f'<line number="{n}" hits="{int(n <= covered)}"/>' for n in range(1, 11))
        (self.bcs / "testresult/cobertura.xml").write_text(
            '<coverage line-rate="0.90"><class filename="crates/tools/bcs-cli/src/cli/new_behavior.rs">'
            f'<lines>{lines}</lines></class></coverage>')
        env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS")}
        return subprocess.run([sys.executable, str(GATE), "--base-ref", self.base,
                               "--bcs-dir", str(self.bcs)], env=env, text=True, capture_output=True)

    def test_uncovered_new_logic_is_rejected(self):
        result = self.run_gate(0)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("changed-instrumentable-lines=10 covered=0", result.stdout)

    def test_at_least_eighty_percent_is_required(self):
        self.assertEqual(self.run_gate(7).returncode, 1)
        self.assertEqual(self.run_gate(8).returncode, 0)

    def test_path_waiver_cannot_hide_new_uncovered_behavior(self):
        scripts = self.bcs / "scripts"
        scripts.mkdir()
        (scripts / "coverage_waivers.json").write_text(json.dumps({
            "version": 1, "waivers": [{
                "path": "crates/tools/bcs-cli/src/cli/", "reason": "old pure move",
                "owner": "test", "expires": "2099-01-01",
            }],
        }))
        result = self.run_gate(0)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("changed-instrumentable-lines=10 covered=0", result.stdout)
        self.assertNotIn("gate not applicable", result.stdout)


if __name__ == "__main__":
    unittest.main()
