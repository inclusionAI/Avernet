"""Gateway Cookie context is explicit, frozen and product-origin only."""
import copy
import contextlib
import io
import json
import tempfile
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bcs_e2e.cli import main
from bcs_e2e.file_transport import RemoteFileClient
from bcs_e2e.profile import ConfigError, browser_headers, fingerprint, validate
from bcs_e2e.transport import ReadClient
from bcs_e2e.write_transport import WriteClient
import test_runner as helpers


class BrowserContextTests(unittest.TestCase):
    def profile(self):
        profile = helpers.profile()
        profile['browser_origin'] = 'https://workbench.example.test'
        return validate(profile)

    def test_explicit_context_headers_do_not_change_api_target_or_profile(self):
        profile = self.profile(); before = copy.deepcopy(profile)
        self.assertEqual(browser_headers(profile), {'Origin': profile['browser_origin'],
                                                   'Referer': profile['browser_origin'] + '/'})
        self.assertEqual(profile, before)
        self.assertNotEqual(profile['browser_origin'], profile['entrypoint']['origin'])

    def test_old_profiles_have_no_inferred_browser_origin_or_digest_change(self):
        profile = helpers.profile(); digest = fingerprint(profile)
        self.assertEqual(browser_headers(validate(profile)), {})
        self.assertEqual(fingerprint(profile), digest)
        profile['browser_origin'] = 'https://workbench.example.test'
        self.assertNotEqual(fingerprint(validate(profile)), digest)

    def test_invalid_context_is_rejected_without_echoing_values(self):
        for value in (None, {}, 1, '', ' https://app.test', 'https://app.test\n',
                      'https://app.test/assistant', 'https://app.test?secret=x',
                      'https://name:secret@app.test', 'https://app.test/#frag',
                      'https://app.test\x7f', 'https://私有.test', 'http://app.test', '//app.test'):
            profile = self.profile(); profile['browser_origin'] = value
            with self.subTest(value=value), self.assertRaises(ConfigError) as caught:
                validate(profile)
            self.assertNotIn('secret', str(caught.exception))

    def test_context_cannot_be_used_to_introduce_arbitrary_headers_or_local_auth(self):
        for kind in ('singlebox', 'mock'):
            profile = self.profile(); profile['entrypoint']['kind'] = kind
            with self.subTest(kind=kind), self.assertRaises(ConfigError):
                browser_headers(profile)
        # bcs-direct is an intentional station-direct browser-context binding
        # (6dc8b954a/6cb819b06/ae4d0a323): authenticated gateway kinds return
        # Origin/Referer only; a binding-less bcs-direct profile is still refused.
        profile = self.profile(); profile['entrypoint']['kind'] = 'bcs-direct'
        self.assertEqual(browser_headers(profile),
                         {'Origin': profile['browser_origin'],
                          'Referer': profile['browser_origin'] + '/'})
        profile = self.profile(); profile['entrypoint']['kind'] = 'bcs-direct'
        profile['auth']['header_env'] = {}
        with self.assertRaises(ConfigError):
            browser_headers(profile)
        profile = self.profile(); profile['auth']['header_env'] = {'X-Custom': 'TEST_HEADER'}
        with self.assertRaises(ConfigError): validate(profile)

    def test_authenticated_bot_gateway_context_sends_only_bot_authorization(self):
        profile = self.profile()
        profile['auth'] = {'header_env': {'Authorization': 'BCS_E2E_DRIVER_BOT_TOKEN'}}
        self.assertEqual(browser_headers(profile), {
            'Origin': profile['browser_origin'],
            'Referer': profile['browser_origin'] + '/',
        })
        with patch.dict(os.environ, {'BCS_E2E_DRIVER_BOT_TOKEN': 'test-bot-token'}):
            read = ReadClient(profile)
        self.assertEqual(read.headers, {
            'Origin': profile['browser_origin'],
            'Referer': profile['browser_origin'] + '/',
            'Authorization': 'test-bot-token',
        })
        self.assertNotIn('test-bot-token', str(read.evidence))

    def test_explicit_unauthenticated_gateway_context_sends_no_credential_header(self):
        profile = self.profile()
        profile['auth'] = {'principal_signing_key_env': 'BCS_E2E_UNAUTHENTICATED'}
        self.assertEqual(browser_headers(profile), {
            'Origin': profile['browser_origin'],
            'Referer': profile['browser_origin'] + '/',
        })
        with patch('bcs_e2e.transport.build_opener') as opener:
            opener.return_value.open.return_value = helpers.Reply(helpers.envelope({}))
            read = ReadClient(profile); result = read.read('environment.read')
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header('Origin'), profile['browser_origin'])
        self.assertEqual(request.get_header('Referer'), profile['browser_origin'] + '/')
        self.assertIsNone(request.get_header('Cookie'))
        self.assertIsNone(request.get_header('Authorization'))
        self.assertNotIn('TEST_BEARER', str(result.evidence))

    def test_identity_request_carries_cookie_and_browser_context_only_to_api_origin(self):
        profile = self.profile()
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
             patch('bcs_e2e.transport.build_opener') as opener:
            opener.return_value.open.return_value = helpers.Reply(helpers.envelope({'user_id': 'owner-1'}))
            read = ReadClient(profile); result = read.read('identity.read')
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, profile['entrypoint']['origin'] + '/openapi/v1/auth/user')
        self.assertEqual(request.get_header('Cookie'), 'test-token')
        self.assertEqual(request.get_header('Origin'), profile['browser_origin'])
        self.assertEqual(request.get_header('Referer'), profile['browser_origin'] + '/')
        self.assertNotIn('X-avernet-principal', request.headers)
        self.assertNotIn('test-token', str(result.evidence))
        self.assertNotIn(profile['browser_origin'], str(result.evidence))

    def test_group_write_uses_same_gateway_context(self):
        profile = self.profile()
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
             patch('bcs_e2e.write_transport.build_opener') as opener:
            opener.return_value.open.return_value = helpers.Reply(helpers.envelope({'group_id': 'owned-group'}))
            WriteClient(profile).call('group.create', body={'name': 'synthetic-test'})
        request = opener.return_value.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith(profile['entrypoint']['origin'] + '/'))
        self.assertEqual(request.get_header('Origin'), profile['browser_origin'])
        self.assertEqual(request.get_header('Referer'), profile['browser_origin'] + '/')
        self.assertEqual(request.get_header('Cookie'), 'test-token')

    def test_file_product_calls_have_context_but_storage_and_share_calls_do_not(self):
        profile = self.profile()
        profile['file_transfer'] = {'allowed_storage_origins': ['https://storage.example.test']}
        with patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
             patch('bcs_e2e.file_transport.build_opener') as opener:
            opener.return_value.open.side_effect = [helpers.Reply(b'{}') for _ in range(3)]
            files = RemoteFileClient(profile)
            files._send('GET', '/api/v1/collaboration/sessions/owned/files', operation='file.list')
            files._send('GET', 'https://storage.example.test/exact-object', operation='file.download',
                        include_auth=False, storage=True)
            files._send('GET', '/api/v1/collaboration/sessions/shared-file/content',
                        operation='file.shared_download', include_auth=False)
        requests = [call.args[0] for call in opener.return_value.open.call_args_list]
        self.assertEqual(requests[0].get_header('Origin'), profile['browser_origin'])
        self.assertEqual(requests[0].get_header('Cookie'), 'test-token')
        for request in requests[1:]:
            for name in ('Cookie', 'Authorization', 'Origin', 'Referer'):
                self.assertIsNone(request.get_header(name))


    def test_identity_probe_does_not_require_unrelated_bot_or_database_inputs(self):
        profile = self.profile()
        profile['observer']['database'] = 'REPLACE_WITH_REVIEWED_DATABASE'
        profile['environment']['expected_bcs_env'] = 'REPLACE_WITH_EXPECTED_ENV'
        profile['actors']['driver']['bot_id'] = 'REPLACE_WITH_BOT_UUID'
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {'BCS_E2E_HUMAN_COOKIE': 'test-token'}), \
             patch('bcs_e2e.transport.build_opener') as opener:
            file = Path(tmp) / 'profile.json'; file.write_text(json.dumps(profile))
            opener.return_value.open.return_value = helpers.Reply(helpers.envelope({'user_id': 'owner-1'}))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc = main(['probe', 'http', '--profile', str(file), '--operation', 'identity.read', '--allow-read'])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'PROBE_PASSED')
            opener.return_value.open.assert_called_once()
            for command in ('check', 'prepare'):
                args = [command, '--profile', str(file)]
                args += ['--allow-read'] if command == 'check' else [
                    '--state-dir', str(Path(tmp) / 'state'), '--run-id', 'unresolved-run']
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(args), 2)
                self.assertIn('REPLACE_WITH', json.loads(output.getvalue())['reason'])
            opener.return_value.open.assert_called_once()
            self.assertFalse((Path(tmp) / 'state').exists())

    def test_identity_probe_still_rejects_unresolved_human_before_network(self):
        profile = self.profile()
        profile['human']['user_id'] = 'REPLACE_WITH_HUMAN'
        for actor in profile['actors'].values(): actor['owner_id'] = 'REPLACE_WITH_HUMAN'
        with tempfile.TemporaryDirectory() as tmp, patch('bcs_e2e.transport.build_opener') as opener:
            file = Path(tmp) / 'profile.json'; file.write_text(json.dumps(profile))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                rc = main(['probe', 'http', '--profile', str(file), '--operation', 'identity.read', '--allow-read'])
            self.assertEqual(rc, 2)
            self.assertIn('REPLACE_WITH', json.loads(output.getvalue())['reason'])
            opener.assert_not_called()
