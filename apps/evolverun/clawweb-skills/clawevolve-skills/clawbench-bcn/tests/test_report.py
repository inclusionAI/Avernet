import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from report import generate_report

class ReportTests(unittest.TestCase):
    def test_report_preserves_score_and_uses_external_prompt(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'REPORT_TEST_KEY':'test'}):
            root=Path(d)
            result='{"execution_status":"timeout","score":null}'
            (root/'result.json').write_text(result)
            prompt=root/'custom.md';prompt.write_text('Custom report instructions')
            response=MagicMock();response.read.return_value=json.dumps({'choices':[{'message':{'content':'# Report\n未评分'}}]}).encode()
            opener=MagicMock();opener.open.return_value.__enter__.return_value=response
            config={'judge':{'model':'example','url':'https://example.com/v1/chat/completions','api_key_env':'REPORT_TEST_KEY','timeout_seconds':2}}
            with patch('report.urllib.request.build_opener',return_value=opener):
                output=generate_report(root,config,prompt)
            self.assertTrue(output.exists())
            self.assertEqual((root/'result.json').read_text(), result)
            request=opener.open.call_args.args[0]
            self.assertEqual(json.loads(request.data)['messages'][0]['content'],'Custom report instructions')
            self.assertEqual(json.loads((root/'report-generation.json').read_text())['status'],'completed')

    def test_report_failure_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'REPORT_TEST_KEY':'test'}):
            root=Path(d);(root/'result.json').write_text('{"score":null}')
            (root/'analysis-report.md').write_text('Previous report')
            config={'judge':{'model':'example','url':'https://example.com','api_key_env':'REPORT_TEST_KEY','timeout_seconds':2}}
            with patch('report.urllib.request.build_opener',side_effect=TimeoutError),self.assertRaises(TimeoutError):
                generate_report(root,config)
            self.assertEqual((root/'analysis-report.md').read_text(),'Previous report')
            self.assertEqual(json.loads((root/'report-generation.json').read_text())['status'],'error')
