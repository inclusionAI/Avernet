"""R1 artifact budget gate: tolerant import, fail-closed digest at Run time."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

RUNNER_ROOT = Path(__file__).resolve().parents[1]
if str(RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNNER_ROOT))

import bcs_e2e.artifact as artifact  # noqa: E402


class ArtifactBudgetGateTests(unittest.TestCase):
    def test_under_budget_digest_is_hex_and_matches_snapshot(self):
        digest = artifact.disk_digest()
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        int(digest, 16)
        self.assertEqual(artifact.PROCESS_EXECUTOR_DIGEST, digest)
        self.assertEqual(artifact.process_digest(), digest)

    def test_budget_breach_returns_none_instead_of_raising(self):
        with patch.object(
            artifact, '_artifact_paths',
            lambda: [artifact.ROOT / 'runner.py'] * (artifact.FLAT_FILE_BUDGET + 1),
        ):
            self.assertIsNone(artifact.disk_digest())

    def test_budget_breach_fails_closed_at_run_with_actionable_message(self):
        with patch.object(artifact, 'PROCESS_EXECUTOR_DIGEST', None):
            with self.assertRaises(ValueError) as caught:
                artifact.process_digest()
            self.assertIn('flat source-file budget', str(caught.exception))
            self.assertIn('split', str(caught.exception))

    def test_drift_and_current_breach_block_the_run(self):
        with patch.object(artifact, 'disk_digest', return_value='0' * 64):
            with self.assertRaises(ValueError):
                artifact.process_digest()
        with patch.object(artifact, 'disk_digest', return_value=None):
            with self.assertRaises(ValueError):
                artifact.process_digest()

    def test_fresh_import_survives_breach_and_gates_digest(self):
        probes = [
            RUNNER_ROOT / 'bcs_e2e' / '_artifact_budget_probe_a.py',
            RUNNER_ROOT / 'bcs_e2e' / '_artifact_budget_probe_b.py',
        ]
        env = {**os.environ, 'PYTHONPATH': str(RUNNER_ROOT)}
        try:
            for probe in probes:
                probe.write_text('"""R1 budget-gate probe; transient."""\n')
            imported = subprocess.run(
                [sys.executable, '-c',
                 'import bcs_e2e.artifact as a; '
                 'raise SystemExit(0 if a.PROCESS_EXECUTOR_DIGEST is None else 2)'],
                cwd=RUNNER_ROOT, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertIn('budget', imported.stderr.lower())
            gated = subprocess.run(
                [sys.executable, '-c',
                 'import bcs_e2e.artifact as a; a.process_digest()'],
                cwd=RUNNER_ROOT, env=env, capture_output=True, text=True, check=False)
            self.assertNotEqual(gated.returncode, 0, gated.stdout)
            self.assertIn('flat source-file budget', gated.stderr)
        finally:
            for probe in probes:
                if probe.exists():
                    probe.unlink()


if __name__ == '__main__':
    unittest.main()
