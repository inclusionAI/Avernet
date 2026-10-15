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


class GitPathEncodingTest(unittest.TestCase):
    """Paths with spaces or non-ASCII characters must not drop out of the denominator."""

    def test_real_git_diff_keeps_space_and_non_ascii_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], check=True,
                               capture_output=True, env=env)

            git("init", "-q")
            git("config", "core.quotePath", "true")  # Git's default: C-quote non-ASCII
            src = root / "apps" / "bcs" / "crates" / "a" / "src"
            src.mkdir(parents=True)
            names = ["plain.rs", "with space.rs", "中文.rs"]
            for name in names:
                (src / name).write_text("fn a() {}\n")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            for name in names:
                (src / name).write_text("fn a() {}\nfn b() {}\n")
            git("commit", "-q", "-am", "change")

            changed = changed_line_cov.git_changed_lines(str(root), "HEAD~1")
            self.assertEqual(set(changed), {"crates/a/src/" + n for n in names})
            coverage = {"crates/a/src/plain.rs": {2: 1},
                        "crates/a/src/with space.rs": {2: 0},
                        "crates/a/src/中文.rs": {2: 0}}
            result = changed_line_cov.measure(changed, coverage, IGNORE)
            self.assertEqual((result.covered, result.instrumentable), (1, 3))

            # Unmapped variants of the same paths are reported, not silently dropped.
            missing = changed_line_cov.measure(changed, {"crates/a/src/plain.rs": {2: 1}}, IGNORE)
            self.assertEqual(missing.unmapped, ["crates/a/src/with space.rs", "crates/a/src/中文.rs"])

    def test_decodes_c_quoted_escapes(self):
        decode = changed_line_cov._decode_git_path
        self.assertEqual(decode('"b/a\\"q\\".rs"'), 'b/a"q".rs')
        self.assertEqual(decode('"b/\\344\\270\\255.rs"'), "b/中.rs")
        self.assertEqual(decode("b/with space.rs\t"), "b/with space.rs")


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

    def test_skip_regex_excludes_memory_repos_listed_in_report(self):
        skip = r"/src/(.*/)?memory(_[A-Za-z0-9_]+)?(\.rs$|/)"
        changed = {
            "crates/services/s/src/sqlite.rs": {1, 2},
            "crates/services/s/src/memory.rs": {1, 2, 3},             # in report, 0 hits
            "crates/services/s/src/memory_store.rs": {1},
            "crates/services/s/src/memory_operations/op_1.rs": {1},
            "crates/services/s/src/memory_tests.rs": {1},             # test module, not skipped
            "crates/services/s/src/memoryless.rs": {1},               # not an in-memory repo
        }
        coverage = {
            "crates/services/s/src/sqlite.rs": {1: 1, 2: 1},
            "crates/services/s/src/memory.rs": {1: 0, 2: 0, 3: 0},
            "crates/services/s/src/memory_store.rs": {1: 0},
            "crates/services/s/src/memory_operations/op_1.rs": {1: 0},
            "crates/services/s/src/memoryless.rs": {1: 0},
        }
        result = changed_line_cov.measure(changed, coverage, IGNORE, skip)
        self.assertEqual(result.skipped, [
            "crates/services/s/src/memory.rs",
            "crates/services/s/src/memory_operations/op_1.rs",
            "crates/services/s/src/memory_store.rs",
        ])
        self.assertEqual(result.test_code, ["crates/services/s/src/memory_tests.rs"])
        self.assertEqual((result.covered, result.instrumentable), (2, 3))  # sqlite 2/2 + memoryless 0/1

    def test_listed_file_with_e2e_hits_is_counted_again(self):
        skip = r"^crates/services/(s/src/memory\.rs)"
        changed = {"crates/services/s/src/memory.rs": {1, 2}}
        coverage = {"crates/services/s/src/memory.rs": {1: 3, 2: 0}}
        result = changed_line_cov.measure(changed, coverage, IGNORE, skip)
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.skip_overridden, ["crates/services/s/src/memory.rs"])
        self.assertEqual((result.covered, result.instrumentable), (1, 2))

    def test_e2e_memory_skip_list_is_valid(self):
        """The list in e2e_coverage.sh must exist and must not hide reachable in-memory repos."""
        script = (SCRIPTS / "e2e_coverage.sh").read_text()
        body = script.split("e2e_unreachable_memory_repos=(", 1)[1].split(")", 1)[0]
        listed = [line.strip() for line in body.splitlines() if line.strip()]
        self.assertEqual(len(listed), 17)
        services = SCRIPTS.parent / "crates" / "services"
        for entry in listed:
            self.assertTrue((services / entry).exists(), entry)
        for reachable in ("bcs-chat-run-store/src/memory.rs",
                          "bcs-interaction/src/memory_store.rs",
                          "bcs-channel-store/src/memory.rs"):
            self.assertNotIn(reachable, listed)

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

    def test_failed_calculation_replaces_a_previous_report(self):
        report = self.root / "cobertura.xml"
        report.write_text(cobertura(str(self.bcs), [
            ("crates/a/src/lib.rs", [(1, 1), (2, 1), (3, 1)]),
        ]))
        out = self.root / "changed.json"
        self.run_gate("--cobertura", str(report), "--base-ref", "HEAD~1", "--changed-json", str(out))
        self.assertEqual(json.loads(out.read_text())["rate"], 100.0)

        proc = self.run_gate("--cobertura", str(self.root / "missing.xml"), "--base-ref", "HEAD~1",
                             "--changed-json", str(out))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        details = json.loads(out.read_text())
        self.assertEqual(details["status"], "unavailable")
        self.assertNotIn("rate", details)

    def gate_with(self, hits, *extra):
        """Run the >60% gate on the fixture's two changed lines (lines 2 and 3)."""
        report = self.root / "cobertura.xml"
        report.write_text(cobertura(str(self.bcs), [("crates/a/src/lib.rs", hits)]))
        return self.run_gate("--cobertura", str(report), "--base-ref", "HEAD~1",
                             "--ignore-filename-regex", IGNORE, "--changed-line-min", "60", *extra)

    def test_changed_line_gate_is_strictly_greater_than(self):
        # 2 instrumentable changed lines; min-lines lowered to exercise the threshold.
        passed = self.gate_with([(1, 1), (2, 1), (3, 1)], "--changed-line-min-lines", "2")
        self.assertEqual(passed.returncode, 0, passed.stdout)
        self.assertIn("100.00% (2 / 2 changed lines; requirement: >60.00%) — OK", passed.stdout)
        failed = self.gate_with([(1, 1), (2, 1), (3, 0)], "--changed-line-min-lines", "2")
        self.assertEqual(failed.returncode, 1, failed.stdout)
        self.assertIn("50.00% (1 / 2 changed lines) is not > 60.00%", failed.stdout)
        exact = self.gate_with([(1, 1), (2, 1), (3, 0)], "--changed-line-min-lines", "2",
                               "--changed-line-min", "50")
        self.assertEqual(exact.returncode, 1, "50%% must not pass a >50%% gate: " + exact.stdout)

    def test_changed_line_gate_reports_only_below_min_lines(self):
        proc = self.gate_with([(1, 1), (2, 0), (3, 0)])  # default min-lines 20 > 2
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("fewer than 20 lines in scope — reported only", proc.stdout)

    def test_changed_line_gate_passes_when_not_applicable(self):
        proc = self.gate_with([(1, 1)])  # changed lines 2-3 not instrumentable
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("not applicable", proc.stdout)

    def test_changed_line_gate_fails_closed(self):
        missing_base = self.run_gate("--changed-line-min", "60")
        self.assertEqual(missing_base.returncode, 1, missing_base.stdout)
        self.assertIn("requires --cobertura and --base-ref", missing_base.stdout)
        bad_xml = self.run_gate("--cobertura", str(self.root / "missing.xml"), "--base-ref",
                                "HEAD~1", "--changed-line-min", "60")
        self.assertEqual(bad_xml.returncode, 1, bad_xml.stdout)
        # A report whose paths match no file in apps/bcs means the mapping is broken.
        report = self.root / "cobertura.xml"
        report.write_text(cobertura(str(self.bcs), [("crates/other/src/lib.rs", [(1, 1)])]))
        broken = self.run_gate("--cobertura", str(report), "--base-ref", "HEAD~1",
                               "--changed-line-min", "60")
        self.assertEqual(broken.returncode, 1, broken.stdout)
        self.assertIn("path mapping is broken", broken.stdout)

    def test_files_absent_from_report_are_listed_not_failed(self):
        # build.rs / type-only files have no executable lines, so they are not in the report.
        (self.bcs / "build.rs").write_text("fn main() {}\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "add build.rs")
        report = self.root / "cobertura.xml"
        report.write_text(cobertura(str(self.bcs), [("crates/a/src/lib.rs", [(1, 1)])]))
        proc = self.run_gate("--cobertura", str(report), "--base-ref", "HEAD~1",
                             "--changed-line-min", "60")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("1 changed Rust file(s) with no executable lines", proc.stdout)

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
