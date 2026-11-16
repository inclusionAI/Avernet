"""BCS unit coverage thresholds and the instrumentable small-change exemption."""
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
        self.source.write_text("\n".join(f"fn branch_{n}() {{}}" for n in range(100)) + "\n")
        self.git("add", ".")
        self.git("commit", "-m", "new behavior")
        result = self.bcs / "testresult"
        result.mkdir()
        (result / "junit.xml").write_text('<testsuites tests="1" failures="0" errors="0" skipped="0"/>')

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def run_gate(self, covered, instrumentable=100, overall="0.90", base_ref=None):
        lines = ''.join(f'<line number="{n}" hits="{int(n <= covered)}"/>'
                        for n in range(1, instrumentable + 1))
        (self.bcs / "testresult/cobertura.xml").write_text(
            f'<coverage line-rate="{overall}">'
            '<class filename="crates/tools/bcs-cli/src/cli/new_behavior.rs">'
            f'<lines>{lines}</lines></class></coverage>')
        return self.invoke_gate(base_ref)

    def invoke_gate(self, base_ref=None):
        env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS")}
        return subprocess.run([sys.executable, str(GATE), "--base-ref", base_ref or self.base,
                               "--bcs-dir", str(self.bcs)], env=env, text=True, capture_output=True)

    def test_uncovered_new_logic_is_rejected(self):
        result = self.run_gate(0)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("changed-instrumentable-lines=100 covered=0", result.stdout)

    def test_changed_line_coverage_must_be_strictly_above_eighty_five_percent(self):
        result = self.run_gate(85)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Changed-line coverage 85.00% is not > 85.00%", result.stdout)
        result = self.run_gate(46, instrumentable=54)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Changed-line coverage 85.19%", result.stdout)

    def test_fifty_instrumentable_lines_are_report_only_despite_larger_physical_diff(self):
        result = self.run_gate(0, instrumentable=50)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed-instrumentable-lines=50 covered=0", result.stdout)
        self.assertIn("100 changed line(s)", result.stdout)
        self.assertIn("reporting only", result.stdout)

    def test_fifty_one_instrumentable_lines_enforce_changed_line_gate(self):
        result = self.run_gate(0, instrumentable=51)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("changed-instrumentable-lines=51 covered=0", result.stdout)
        self.assertNotIn("reporting only", result.stdout)

    def test_small_change_still_requires_overall_coverage_above_eighty_seven_percent(self):
        result = self.run_gate(0, instrumentable=50, overall="0.87")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Overall line coverage 87.00% is not > 87.00%", result.stdout)
        self.assertIn("reporting only", result.stdout)
        result = self.run_gate(0, instrumentable=50, overall="0.8701")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_small_change_still_requires_all_tests_to_pass(self):
        for attribute in ("failures", "errors", "skipped"):
            with self.subTest(attribute=attribute):
                (self.bcs / "testresult/junit.xml").write_text(
                    f'<testsuites tests="1" {attribute}="1"/>')
                result = self.run_gate(0, instrumentable=50)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("FAIL: Pass rate", result.stdout)
                self.assertIn("reporting only", result.stdout)

    def test_no_instrumentable_changed_lines_are_not_applicable(self):
        result = self.run_gate(0, instrumentable=0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("gate not applicable", result.stdout)
        self.assertNotIn("reporting only", result.stdout)

    def test_failed_diff_does_not_pass_as_an_exempt_change(self):
        result = self.run_gate(0, instrumentable=50, base_ref="missing-base-ref")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("git diff failed", result.stdout)

    def test_invalid_reports_fail_closed(self):
        for report in ("junit.xml", "cobertura.xml"):
            with self.subTest(report=report):
                path = self.bcs / "testresult" / report
                original = path.read_text() if path.exists() else None
                path.write_text("<broken")
                result = self.invoke_gate()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"failed to read or parse {report}", result.stdout)
                if original is not None:
                    path.write_text(original)

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
        self.assertIn("changed-instrumentable-lines=100 covered=0", result.stdout)
        self.assertNotIn("gate not applicable", result.stdout)


if __name__ == "__main__":
    unittest.main()
