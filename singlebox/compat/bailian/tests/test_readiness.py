from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from readiness import summarize


class ReadinessTest(unittest.TestCase):
    def test_missing_e2e_is_not_ready_and_incompatibility_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            smoke = root / 'bailian-smoke-openclaw'
            smoke.mkdir()
            (smoke / 'result.json').write_text('{"status":"PASS"}')
            self.assertEqual(summarize(root)[0]['readiness'], 'UNKNOWN')
            e2e = root / 'bailian-e2e-deepseek-harness'
            e2e.mkdir()
            (e2e / 'install.json').write_text(json.dumps({
                'status': 'FAIL', 'reason': 'published_plugin_incompatible_with_latest_engine'}))
            row = summarize(root)[1]
            self.assertEqual(row['readiness'], 'NOT_READY')
            self.assertEqual(row['reason'], 'published_plugin_incompatible_with_latest_engine')
            (smoke / 'result.json').write_text('{"status":"FAIL"}')
            self.assertEqual(summarize(root)[0]['readiness'], 'NOT_READY')
