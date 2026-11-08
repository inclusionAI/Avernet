import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from e2e import Harness, main


class E2ETest(unittest.TestCase):
    def test_local_guard_precedes_harness_and_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'result.json'
            args = ['e2e.py', '--engine', 'claude-code', '--profile', directory,
                    '--binaries', directory, '--report', str(report)]
            with patch.object(sys, 'argv', args), patch.dict('os.environ', {}, clear=True), \
                    patch('e2e.Harness') as harness:
                self.assertEqual(main(), 1)
            harness.assert_not_called()

    def test_completed_run_with_wrong_content_fails(self):
        harness = Harness.__new__(Harness)
        harness.stage = ''
        harness.bot_id = 'bot'
        harness.caller = {'bot_token': 'test'}
        harness.report = {'cases': []}
        harness.engine = 'claude-code'
        with patch.object(harness, 'api', return_value={'run_id': 'run', 'session_id': 'session'}), \
                patch.object(harness, 'wait', return_value={'state': 'completed', 'response': {'content': 'BCS_ab'}}):
            with self.assertRaises(RuntimeError):
                harness.chat('read', 'Read probe', 'BCS_abcd')
        self.assertEqual(harness.report['cases'][0]['status'], 'FAIL')
