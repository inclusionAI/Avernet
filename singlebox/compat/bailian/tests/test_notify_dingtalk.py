import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configure import ENGINES
from notify_dingtalk import DeliveryError, main, payload, read_rows, send, signed_url


class DingTalkTest(unittest.TestCase):
    def setUp(self):
        self.webhook = 'https://oapi.dingtalk.com/robot/send?access_token=test-placeholder'
        self.rows = [dict(engine=e, readiness='READY', engine_version='1.2',
                          plugin_version='2.0', selection='latest', smoke='PASS', e2e='PASS', commit='a' * 40)
                     for e in ENGINES]
        self.jobs = dict.fromkeys(('harness-tests', 'smoke', 'build-bcs', 'e2e', 'readiness'), 'success')

    def report(self, rows=None, previous=None):
        return payload(self.rows if rows is None else rows, previous or [], self.jobs,
                       'https://example.test/run/1', '1', datetime(2026, 10, 22, 9, 30))

    def test_webhook_only_and_optional_signing(self):
        self.assertEqual(signed_url(self.webhook, '', 123), self.webhook)
        query = parse_qs(urlsplit(signed_url(self.webhook, 'test-signing-key', 123)).query)
        self.assertEqual(query['timestamp'], ['123'])
        self.assertEqual(query['access_token'], ['test-placeholder'])
        self.assertEqual(len(query['sign'][0]), 44)
        self.assertNotEqual(query['sign'], parse_qs(urlsplit(
            signed_url(self.webhook, 'test-signing-key', 124)).query)['sign'])
        for invalid in (self.webhook.replace('https:', 'http:'),
                        self.webhook.replace('oapi.dingtalk.com', 'example.test')):
            with self.assertRaises(DeliveryError):
                signed_url(invalid, '', 123)

    def test_success_version_changes_and_failure_gates(self):
        self.assertIn('全部通过', self.report()['markdown']['title'])
        self.assertFalse(self.report()['at']['isAtAll'])
        previous = [dict(self.rows[0], engine_version='1.1')]
        self.assertIn('引擎 1.1 → 1.2', self.report(previous=previous)['markdown']['text'])
        self.jobs['build-bcs'] = 'failure'
        result = self.report()['markdown']
        self.assertIn('需要关注', result['title'])
        self.assertIn('build-bcs=failure', result['text'])

    def test_bridge_revision_changes_without_package_release(self):
        for engine in ('claude-code', 'codex'):
            current = dict(self.rows[0], engine=engine, plugin_version='bcs-bridge')
            previous = dict(current, commit='b' * 40)
            body = self.report(rows=[current], previous=[previous])['markdown']['text']
            self.assertIn('BCS/bridge 提交：' + 'a' * 40, body)
            self.assertIn('BCS/bridge 提交 ' + 'b' * 40 + ' → ' + 'a' * 40, body)
            self.assertNotIn('版本未变化', body)

    def test_missing_or_malformed_evidence_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.json'
            self.assertEqual(read_rows(path), [])
            path.write_text('{invalid')
            self.assertEqual(read_rows(path), [])
        result = self.report(rows=[])['markdown']
        self.assertIn('需要关注', result['title'])
        self.assertIn('UNKNOWN', result['text'])
        self.assertIn('missing_evidence', result['text'])
        self.assertIn('无可用基线', result['text'])

    @patch('notify_dingtalk.build_opener')
    def test_application_error_is_failure_even_with_http_success(self, opener):
        opener.return_value.open.return_value.__enter__.return_value = io.StringIO('{"errcode":310000,"errmsg":"keywords not in content secret-placeholder"}')
        with self.assertRaisesRegex(DeliveryError, '^api_310000_keyword$'):
            send(self.webhook, '', self.report())
        opener.return_value.open.return_value.__enter__.return_value = io.StringIO('{"errcode":0}')
        send(self.webhook, '', self.report())
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(json.loads(request.data)['msgtype'], 'markdown')

    @patch('notify_dingtalk.send', side_effect=OSError('secret URL and response must not leak'))
    def test_transport_failure_does_not_log_secrets(self, mocked_send):
        env = {'NEEDS_JSON': json.dumps({k: {'result': v} for k, v in self.jobs.items()}),
               'GITHUB_SERVER_URL': 'https://example.test', 'GITHUB_REPOSITORY': 'org/repo',
               'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1',
               'BCS_ENGINE_COMPAT_DINGTALK_WEBHOOK': self.webhook}
        output = io.StringIO()
        with patch.dict('os.environ', env, clear=True), patch.object(sys, 'argv', [
                'notify', '--report', '/nonexistent/current.json', '--previous', '/nonexistent/previous.json']), contextlib.redirect_stdout(output):
            self.assertEqual(main(), 1)
        self.assertNotIn('secret URL', output.getvalue())
        self.assertNotIn('test-placeholder', output.getvalue())
        self.assertEqual(mocked_send.call_args.args[1], '')
