from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DETECT = ROOT / 'apps/bcs/scripts/ci/detect_changes.py'


class BcsChangeDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '--initial-branch=dev')
        self.git('config', 'user.name', 'CI test')
        self.git('config', 'user.email', 'ci-test@example.com')
        self.write('apps/bcs/baseline.txt', 'base')
        self.commit('base')
        self.base = self.git('rev-parse', 'HEAD')

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

    def detect(self, event: str, before: str = '') -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        output = Path(self.temporary.name) / 'output'
        output.write_text('')
        result = subprocess.run([sys.executable, str(DETECT)], cwd=self.repo,
                                env={**os.environ, 'GITHUB_EVENT_NAME': event,
                                     'BCS_PUSH_BEFORE': before, 'GITHUB_OUTPUT': str(output)},
                                capture_output=True, text=True)
        return result, dict(line.split('=', 1) for line in output.read_text().splitlines())

    def test_push_uses_before_sha_even_when_only_an_earlier_commit_changes_bcs(self) -> None:
        self.write('apps/bcs/changed.rs', 'change')
        self.commit('BCS')
        self.write('README.md', 'docs')
        self.commit('unrelated last commit')
        self.git('update-ref', 'refs/remotes/origin/dev', 'HEAD')
        result, output = self.detect('push', self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output['base-ref'], self.base)
        self.assertEqual(output['skip-bcs'], 'false')

    def test_pr_diff_uses_merge_first_parent_not_target_only_changes(self) -> None:
        self.git('switch', '-c', 'feature')
        self.write('README.md', 'PR docs')
        self.commit('PR docs')
        self.git('switch', 'dev')
        self.write('apps/bcs/target-only.rs', 'target change')
        self.commit('target BCS')
        expected_base = self.git('rev-parse', 'HEAD')
        self.git('merge', '--no-ff', 'feature', '-m', 'synthetic PR merge')
        result, output = self.detect('pull_request')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output['base-ref'], expected_base)
        self.assertEqual(output['skip-bcs'], 'true')

    def test_manual_dispatch_forces_a_run(self) -> None:
        result, output = self.detect('workflow_dispatch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output['skip-bcs'], 'false')
        self.assertEqual(output['dsh-bcn-changed'], 'true')

    def test_first_push_uses_the_empty_tree(self) -> None:
        result, output = self.detect('push', '0' * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output['skip-bcs'], 'false')
        self.assertEqual(self.git('cat-file', '-t', output['base-ref']), 'tree')

    def test_unrelated_push_skips_bcs(self) -> None:
        self.write('README.md', 'unrelated')
        self.commit('docs')
        result, output = self.detect('push', self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output['skip-bcs'], 'true')

    def test_ci_only_changes_run_the_bcs_gate(self) -> None:
        for path in ['.github/workflows/unit-tests.yml',
                     'singlebox/ci/tests/test_bcs_build_metadata.py',
                     'singlebox/ci/tests/test_unit_test_workflow_diff.py']:
            with self.subTest(path=path):
                self.git('reset', '--hard', self.base)
                self.write(path, 'CI change')
                self.commit('CI only')
                result, output = self.detect('push', self.base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output['skip-bcs'], 'false')

    def test_plugin_changes_remain_detected(self) -> None:
        self.write('apps/bcs/crates/plugins/deepseek-harness-channel-bcn/index.ts', 'plugin')
        self.commit('plugin')
        result, output = self.detect('push', self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output['dsh-bcn-changed'], 'true')

    def test_invalid_or_missing_push_base_fails_closed(self) -> None:
        for before in ['', 'f' * 40]:
            with self.subTest(before=before):
                result, output = self.detect('push', before)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(output.get('skip-bcs'), 'true')

    def test_workflow_caches_only_instrumented_dependencies_from_trusted_pushes(self) -> None:
        job = (ROOT / '.github/workflows/unit-tests.yml').read_text().split('\n  backend:', 1)[0]
        self.assertIn('uses: Swatinem/rust-cache@', job)
        self.assertIn('apps/bcs -> target/llvm-cov-target', job)
        self.assertIn('cache-workspace-crates: false', job)
        self.assertIn('cache-bin: false', job)
        self.assertIn("save-if: ${{ github.event_name == 'push' }}", job)
        self.assertIn('env-vars: CARGO_PROFILE_ CARGO_INCREMENTAL', job)
        self.assertNotIn('uses: actions/cache@', job)
        self.assertIn('BCS_PUSH_BEFORE: ${{ github.event.before }}', job)
        self.assertIn('BCS_BASE_REF: ${{ steps.changes.outputs.base-ref }}', job)
        self.assertIn('run: python3 apps/bcs/scripts/ci/detect_changes.py', job)


if __name__ == '__main__':
    unittest.main()
