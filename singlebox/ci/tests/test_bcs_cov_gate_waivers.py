"""Integration + unit tests for cov_gate.py's expiring changed-line waivers.

Builds a throwaway git repository whose HEAD commit adds one pure-move file
(``src/moved.rs``) and appends lines to another (``src/a.rs``), fabricates a
matching ``testresult/{junit,cobertura}.xml`` on disk, then runs the real
gate as a subprocess — mirroring test_bcs_ci_change_detection.py's approach.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COV_GATE = ROOT / 'apps/bcs/scripts/cov_gate.py'
MOVED_LINES = 10    # instrumentable changed lines in src/moved.rs
A_ADDED_LINES = 2   # instrumentable changed lines appended to src/a.rs


def _cov_gate_module():
    """Import cov_gate.py directly (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location('bcs_cov_gate', COV_GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(path: str, expires: str = '2099-01-01') -> dict:
    return {'path': path, 'reason': 'pure move covered by e2e changed-line gate',
            'owner': 'ci-test', 'expires': expires}


class BcsCovGateWaiverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / 'repo'
        self.repo.mkdir()
        self.bcs = self.repo / 'apps/bcs'
        self.git('init', '--initial-branch=dev')
        self.git('config', 'user.name', 'CI test')
        self.git('config', 'user.email', 'ci-test@example.com')
        self.write('apps/bcs/src/a.rs', 'a1\na2\na3\n')
        self.commit('base')
        self.base = self.git('rev-parse', 'HEAD')
        # HEAD commit: relocated pure-move file plus two appended a.rs lines.
        self.write('apps/bcs/src/a.rs', 'a1\na2\na3\na4\na5\n')
        self.write('apps/bcs/src/moved.rs',
                   ''.join(f'm{i}\n' for i in range(1, MOVED_LINES + 1)))
        self.commit('move')

    def git(self, *args: str) -> str:
        return subprocess.run(['git', *args], cwd=self.repo, check=True,
                              text=True, capture_output=True).stdout.strip()

    def write(self, path: str, text: str) -> None:
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def commit(self, message: str) -> None:
        self.git('add', '.')
        self.git('commit', '-m', message)

    def write_testresult(self, moved_hits: int, a_hits: int) -> None:
        """Fabricate testresult/{junit,cobertura}.xml over the changed lines.

        Cobertura filenames must equal the gate's bcs-relative diff paths
        (`src/...`, not `apps/bcs/src/...`) or the lines never map.
        """
        result = self.bcs / 'testresult'
        result.mkdir(parents=True, exist_ok=True)
        (result / 'junit.xml').write_text(
            '<testsuites tests="1" failures="0" errors="0" skipped="0"/>\n')
        classes = [
            ('src/moved.rs', range(1, MOVED_LINES + 1), moved_hits),
            ('src/a.rs', range(4, 4 + A_ADDED_LINES), a_hits),
        ]
        (result / 'cobertura.xml').write_text(''.join([
            '<coverage line-rate="0.9">',
            *('<class filename="%s"><lines>%s</lines></class>'
              % (name, ''.join('<line number="%d" hits="%d"/>' % (n, hits)
                               for n in numbers))
              for name, numbers, hits in classes),
            '</coverage>\n',
        ]))

    def run_gate(self, waivers_path: str | None = None
                 ) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, str(COV_GATE), '--base-ref', self.base,
               '--bcs-dir', str(self.bcs)]
        if waivers_path:
            cmd += ['--changed-line-waivers', waivers_path]
        env = {k: v for k, v in os.environ.items()
               if k not in ('CI', 'GITHUB_ACTIONS')}
        return subprocess.run(cmd, cwd=self.repo, env=env,
                               capture_output=True, text=True)

    def test_no_waiver_file_keeps_gate_output_and_failure(self) -> None:
        self.write_testresult(moved_hits=0, a_hits=0)
        result = self.run_gate()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('[pass-rate] tests=1 passed=1', result.stdout)
        self.assertIn('Pass rate 100.00% (requirement: 100.00%) — OK',
                      result.stdout)
        self.assertIn(f'changed-instrumentable-lines={MOVED_LINES + A_ADDED_LINES} '
                      'covered=0 -> 0.00%', result.stdout)
        self.assertIn('Changed-line coverage 0.00% is below the required 80.00%',
                      result.stdout)
        self.assertNotIn('waiver', result.stdout.lower())

    def test_default_waiver_file_waiving_all_changed_files_is_not_applicable(self) -> None:
        self.write_testresult(moved_hits=0, a_hits=0)
        # Written to the default location (bcs-dir/scripts/), deliberately
        # untracked so it never enters the diff; run without the flag to
        # exercise the default path resolution used by CI and pre-push.
        self.write('apps/bcs/scripts/coverage_waivers.json',
                   json.dumps({'version': 1, 'waivers': [_entry('src/')]}))
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('[changed-line-cov] waived 2 file(s) / '
                      f'{MOVED_LINES + A_ADDED_LINES} instrumentable changed '
                      'line(s) — pure move covered by e2e changed-line gate '
                      '(owner ci-test, expires 2099-01-01)', result.stdout)
        self.assertIn('no instrumentable changed source lines mapped; '
                      'gate not applicable', result.stdout)

    def test_partial_waiver_subtracts_only_matching_files(self) -> None:
        self.write_testresult(moved_hits=0, a_hits=0)
        waivers = Path(self.temporary.name) / 'partial.json'
        waivers.write_text(json.dumps({'version': 1,
                                       'waivers': [_entry('src/moved.rs')]}))
        result = self.run_gate(waivers_path=str(waivers))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('[changed-line-cov] waived 1 file(s) / '
                      f'{MOVED_LINES} instrumentable changed line(s)',
                      result.stdout)
        # Only the unwaived a.rs additions remain in the denominator.
        self.assertIn(f'changed-instrumentable-lines={A_ADDED_LINES} '
                      'covered=0 -> 0.00%', result.stdout)
        self.assertIn('Changed-line coverage 0.00% is below the required 80.00%',
                      result.stdout)

    def test_waived_covered_lines_leave_numerator_and_denominator(self) -> None:
        self.write_testresult(moved_hits=1, a_hits=1)
        waivers = Path(self.temporary.name) / 'covered-waived.json'
        waivers.write_text(json.dumps({'version': 1,
                                       'waivers': [_entry('src/moved.rs')]}))
        result = self.run_gate(waivers_path=str(waivers))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('[changed-line-cov] waived 1 file(s) / '
                      f'{MOVED_LINES} instrumentable changed line(s)',
                      result.stdout)
        # The 10 waived lines were covered, but must drop out of the
        # numerator too — otherwise covered would exceed the denominator.
        self.assertIn(f'changed-instrumentable-lines={A_ADDED_LINES} '
                      f'covered={A_ADDED_LINES} -> 100.00%', result.stdout)
        self.assertIn('Changed-line coverage 100.00% (requirement: >=80.00%) — OK',
                      result.stdout)

    def test_expired_waiver_fails_closed_and_no_longer_exempts(self) -> None:
        self.write_testresult(moved_hits=0, a_hits=0)
        waivers = Path(self.temporary.name) / 'expired.json'
        waivers.write_text(json.dumps(
            {'version': 1, 'waivers': [_entry('src/moved.rs', '2000-01-01')]}))
        result = self.run_gate(waivers_path=str(waivers))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("coverage waiver for 'src/moved.rs' expired on "
                      '2000-01-01 — renew or re-cover', result.stdout)
        # The expired entry is void, so its lines count against the threshold.
        self.assertNotIn('[changed-line-cov] waived', result.stdout)
        self.assertIn(f'changed-instrumentable-lines={MOVED_LINES + A_ADDED_LINES} '
                      'covered=0 -> 0.00%', result.stdout)

    def test_unusable_waiver_file_fails_closed(self) -> None:
        # Coverage is otherwise perfect, so only the broken file fails.
        self.write_testresult(moved_hits=1, a_hits=1)
        cases = {
            'invalid-json': '{not json',
            'not-an-object': json.dumps([{'version': 1}]),
            'wrong-version': json.dumps({'version': 2, 'waivers': []}),
            'version-true': json.dumps({'version': True, 'waivers': []}),
            'version-float': json.dumps({'version': 1.0, 'waivers': []}),
            'version-string': json.dumps({'version': '1', 'waivers': []}),
            'top-level-extra-key': json.dumps({'version': 1, 'waivers': [],
                                               'extra': 1}),
            'entry-not-object': json.dumps({'version': 1, 'waivers': ['src/moved.rs']}),
            'missing-key': json.dumps({'version': 1,
                                       'waivers': [{'path': 'src/moved.rs'}]}),
            'empty-field': json.dumps({'version': 1,
                                       'waivers': [_entry('src/moved.rs')
                                                   | {'reason': ''}]}),
            'newline-in-field': json.dumps({'version': 1,
                                            'waivers': [_entry('src/moved.rs')
                                                        | {'reason': 'one\ntwo'}]}),
            'bad-date-format': json.dumps({'version': 1,
                                           'waivers': [_entry('src/moved.rs',
                                                              '2099/01/01')]}),
            'impossible-date': json.dumps({'version': 1,
                                           'waivers': [_entry('src/moved.rs',
                                                             '2000-13-01')]}),
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                path = Path(self.temporary.name) / f'{name}.json'
                path.write_text(text)
                result = self.run_gate(waivers_path=str(path))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('coverage waiver list unusable', result.stdout)
                if name == 'newline-in-field':
                    self.assertIn("waivers[0] ('src/moved.rs')", result.stdout)
                    self.assertIn('must not contain line breaks', result.stdout)

    def test_stale_waiver_notifies_cleanup_and_passes(self) -> None:
        self.write_testresult(moved_hits=1, a_hits=1)
        waivers = Path(self.temporary.name) / 'stale.json'
        waivers.write_text(json.dumps({'version': 1,
                                       'waivers': [_entry('src/gone.rs')]}))
        result = self.run_gate(waivers_path=str(waivers))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[changed-line-cov] waiver for 'src/gone.rs' matched no "
                      'changed files — consider removing', result.stdout)
        self.assertIn('Changed-line coverage 100.00% (requirement: >=80.00%) — OK',
                      result.stdout)


class WaiverMechanicsTest(unittest.TestCase):
    """Direct unit tests of the gate script's waiver helpers (via importlib)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cov_gate = _cov_gate_module()

    def test_trailing_slash_matches_as_directory_prefix(self) -> None:
        matches = self.cov_gate.waiver_matches
        self.assertTrue(matches('crates/cli/src/', 'crates/cli/src/main.rs'))
        self.assertTrue(matches('crates/cli/src/', 'crates/cli/src/sub/x.rs'))
        self.assertFalse(matches('crates/cli/src/', 'crates/cli/src.rs'))
        self.assertFalse(matches('crates/cli/src/', 'other/x.rs'))

    def test_without_trailing_slash_requires_exact_match(self) -> None:
        matches = self.cov_gate.waiver_matches
        self.assertTrue(matches('crates/cli/src/main.rs', 'crates/cli/src/main.rs'))
        self.assertFalse(matches('crates/cli/src/main', 'crates/cli/src/main.rs'))
        self.assertFalse(matches('src/moved.rs', 'src/moved.rs.bak'))

    def test_load_waivers_parses_and_validates(self) -> None:
        load = self.cov_gate.load_waivers
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'w.json'
            path.write_text(json.dumps({'version': 1, 'waivers': [
                {'path': 'a/', 'reason': 'r', 'owner': 'o',
                 'expires': '2099-01-02'}]}))
            waivers, error = load(str(path))
            self.assertIsNone(error)
            self.assertEqual(len(waivers), 1)
            self.assertEqual(waivers[0]['date'].isoformat(), '2099-01-02')
            path.write_text(json.dumps({'version': 1, 'waivers': [
                {'path': 'a/', 'reason': 'r', 'expires': '2099-01-02'}]}))
            self.assertIsNotNone(load(str(path))[1])  # missing owner key
            self.assertEqual(load(str(Path(tmp) / 'absent.json')), ([], None))


if __name__ == '__main__':
    unittest.main()
