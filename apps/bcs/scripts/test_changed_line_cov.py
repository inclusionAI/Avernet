"""Tests for the report-only E2E changed-line coverage."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import changed_line_cov

IGNORE = r"(tools/bcs-bridge|tools/bcs-admin)/"


def cobertura(source, classes):
    body = "".join(
        '<class filename="%s"><lines>%s</lines></class>'
        % (name, "".join('<line number="%d" hits="%d"/>' % hit for hit in hits))
        for name, hits in classes
    )
    return ('<?xml version="1.0"?><coverage line-rate="0.5"><sources><source>%s</source>'
            '</sources><packages><package><classes>%s</classes></package></packages>'
            '</coverage>' % (source, body))


class ParseDiffTest(unittest.TestCase):
    def test_collects_added_lines_under_bcs_only(self):
        diff = textwrap.dedent("""\
            diff --git a/apps/bcs/crates/a/src/lib.rs b/apps/bcs/crates/a/src/lib.rs
            --- a/apps/bcs/crates/a/src/lib.rs
            +++ b/apps/bcs/crates/a/src/lib.rs
            @@ -3,0 +4,2 @@ fn x()
            @@ -9 +11 @@ fn y()
            @@ -20,3 +22,0 @@ fn z()
            diff --git a/apps/bcs/old.rs b/apps/bcs/old.rs
            --- a/apps/bcs/old.rs
            +++ /dev/null
            @@ -1,4 +0,0 @@
            diff --git a/singlebox/x.sh b/singlebox/x.sh
            --- a/singlebox/x.sh
            +++ b/singlebox/x.sh
            @@ -1 +1 @@
            """)
        self.assertEqual(changed_line_cov.parse_diff(diff),
                         {"crates/a/src/lib.rs": {4, 5, 11}})


class LoadCoberturaTest(unittest.TestCase):
    def test_normalizes_relative_absolute_and_prefixed_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            bcs = Path(directory) / "apps" / "bcs"
            bcs.mkdir(parents=True)
            report = Path(directory) / "cobertura.xml"
            report.write_text(cobertura(str(bcs), [
                ("crates/a/src/lib.rs", [(1, 0), (2, 3)]),
                (str(bcs / "crates/b/src/lib.rs"), [(5, 1)]),
                ("apps/bcs/crates/c/src/lib.rs", [(7, 0)]),
            ]))
            coverage = changed_line_cov.load_cobertura(str(report), str(bcs))
        self.assertEqual(coverage, {
            "crates/a/src/lib.rs": {1: 0, 2: 3},
            "crates/b/src/lib.rs": {5: 1},
            "crates/c/src/lib.rs": {7: 0},
        })

    def test_source_at_repository_root(self):
        with tempfile.TemporaryDirectory() as directory:
            bcs = Path(directory) / "apps" / "bcs"
            bcs.mkdir(parents=True)
            report = Path(directory) / "cobertura.xml"
            report.write_text(cobertura(directory, [("apps/bcs/crates/a/src/lib.rs", [(3, 1)])]))
            coverage = changed_line_cov.load_cobertura(str(report), str(bcs))
        self.assertEqual(coverage, {"crates/a/src/lib.rs": {3: 1}})


class MeasureTest(unittest.TestCase):
    def test_counts_instrumentable_lines_and_classifies_missing_files(self):
        changed = {
            "crates/a/src/lib.rs": {1, 2, 3, 4},       # 4 is a comment (not instrumented)
            "crates/tools/bcs-bridge/src/x.rs": {1},  # outside e2e scope
            "crates/a/tests/it.rs": {1},              # test-only
            "crates/a/src/setup_tests.rs": {1},       # #[path] test module
            "crates/z/src/lib.rs": {1},               # missing from report
            "Cargo.toml": {1},                        # not Rust source
        }
        coverage = {"crates/a/src/lib.rs": {1: 2, 2: 0, 3: 1}}
        result = changed_line_cov.measure(changed, coverage, IGNORE)
        self.assertEqual((result.covered, result.instrumentable), (2, 3))
        self.assertAlmostEqual(result.rate, 200 / 3)
        self.assertEqual(result.excluded, ["crates/tools/bcs-bridge/src/x.rs"])
        self.assertEqual(result.test_code, ["crates/a/src/setup_tests.rs", "crates/a/tests/it.rs"])
        self.assertEqual(result.unmapped, ["crates/z/src/lib.rs"])

    def test_rate_is_none_without_instrumentable_lines(self):
        result = changed_line_cov.measure({"crates/tools/bcs-bridge/src/x.rs": {1}}, {}, IGNORE)
        self.assertIsNone(result.rate)
        self.assertEqual(result.to_dict()["rate"], None)


class GateIsReportOnlyTest(unittest.TestCase):
    """End to end through e2e_cov_gate.py against a throwaway git repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bcs = self.root / "apps" / "bcs"
        source = self.bcs / "crates" / "a" / "src"
        source.mkdir(parents=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.git = lambda *a: subprocess.run(["git", "-C", str(self.root), *a], check=True,
                                             capture_output=True, env=env)
        self.git("init", "-q")
        (source / "lib.rs").write_text("fn a() {}\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        (source / "lib.rs").write_text("fn a() {}\nfn b() {}\nfn c() {}\n")
        self.git("commit", "-q", "-am", "change")
        self.summary = self.root / "summary.json"
        self.summary.write_text(json.dumps({"data": [{"totals": {
            "lines": {"covered": 50, "count": 100},
            "functions": {"covered": 50, "count": 100},
            "regions": {"covered": 1, "count": 2},
            "branches": {"covered": 0, "count": 0},
        }}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_gate(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "e2e_cov_gate.py"), "--summary", str(self.summary),
             "--line-min", "41", "--method-min", "36", *extra],
            cwd=self.bcs, capture_output=True, text=True, check=False,
            env={k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS")},
        )

    def test_low_changed_line_coverage_reports_but_does_not_fail(self):
        report = self.root / "cobertura.xml"
        report.write_text(cobertura(str(self.bcs), [
            ("crates/a/src/lib.rs", [(1, 1), (2, 1), (3, 0)]),
        ]))
        out = self.root / "changed.json"
        proc = self.run_gate("--cobertura", str(report), "--base-ref", "HEAD~1",
                             "--ignore-filename-regex", IGNORE, "--changed-json", str(out))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("E2E changed-line coverage 50.00% (1 / 2 changed lines; reporting only",
                      proc.stdout)
        details = json.loads(out.read_text())
        self.assertEqual((details["covered_changed_lines"],
                          details["instrumentable_changed_lines"]), (1, 2))

    def test_errors_and_missing_inputs_never_fail_the_gate(self):
        proc = self.run_gate("--cobertura", str(self.root / "missing.xml"), "--base-ref", "HEAD~1")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("could not be computed", proc.stdout)
        proc = self.run_gate()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("no base ref provided", proc.stdout)

    def test_line_threshold_still_gates(self):
        self.summary.write_text(json.dumps({"data": [{"totals": {
            "lines": {"covered": 40, "count": 100},
            "functions": {"covered": 50, "count": 100},
        }}]}))
        proc = self.run_gate()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("E2E line coverage 40.00% is below the required 41.00%", proc.stdout)


if __name__ == "__main__":
    unittest.main()
