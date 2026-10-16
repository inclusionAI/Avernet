import importlib.util
import json
import tempfile
import tomllib
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location('provider', Path(__file__).parents[1] / 'qwenwork_provider.py')
provider = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider)
VALID = {'provider_id': 'prv_local', 'slug': 'local', 'enabled': True,
         'auth_mode': 'static_bearer', 'protocol_version': '2.0'}


class ProvisioningTests(unittest.TestCase):
    def test_creates_once_and_reuses_without_retaining_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'provider.json'
            client = Mock()
            client.request.side_effect = [urllib.error.HTTPError('', 404, '', {}, None),
                                          {'provider_admin_token': 'never-persist-this'}, VALID, VALID]
            self.assertEqual(provider.ensure_provider(client, state), 'prv_local')
            self.assertEqual(provider.ensure_provider(client, state), 'prv_local')
            self.assertEqual(json.loads(state.read_text()), {'provider_id': 'prv_local', 'slug': 'local'})
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sum(call.args[0] == '/providers' for call in client.request.call_args_list), 1)
            self.assertEqual(client.request.call_args_list[1].args[2]['X-Mock-User-Id'], 'qwenwork-bootstrap')

    def test_non_404_discovery_does_not_create(self):
        client = Mock()
        client.request.side_effect = urllib.error.HTTPError('', 503, '', {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            provider.ensure_provider(client, Path('/unused'))
        client.request.assert_called_once()

    def test_incompatible_existing_provider_is_not_mutated(self):
        client = Mock()
        client.request.return_value = {**VALID, 'enabled': False}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state'
            with self.assertRaises(ValueError):
                provider.ensure_provider(client, path)
            self.assertFalse(path.exists())
            client.request.assert_called_once()

    def test_authorization_preserves_multiline_ids_and_other_sections(self):
        text = 'port = 21000\n[openapi_v1]\nregistration_self_service_provider_ids = [\n "other",\n]\npublic_collaboration_base_url="/custom"\n[llm]\ntype="none"\n'
        updated = provider.authorized_config(text, 'prv_local')
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed['openapi_v1']['registration_self_service_provider_ids'], ['other', 'prv_local'])
        self.assertEqual(parsed['openapi_v1']['public_collaboration_base_url'], '/custom')
        self.assertEqual(parsed['llm'], {'type': 'none'})
        self.assertEqual(provider.authorized_config(updated, 'prv_local'), updated)
        fresh = tomllib.loads(provider.authorized_config('port=21000\n', 'prv_local'))
        self.assertEqual(fresh['openapi_v1']['registration_self_service_provider_ids'], ['prv_local'])

    def test_verification_uses_ordinary_users_and_checks_scope(self):
        token = {'code': 20000, 'data': {'token': 'test', 'registration': {
            'token_version': 2, 'provider_id': 'prv_local', 'allowed_modes': ['plugin']}}}
        client = Mock()
        client.request.side_effect = [VALID, token, VALID, token]
        provider.verify(client, 'prv_local')
        cookies = [call.kwargs['headers']['Cookie'] for call in client.request.call_args_list]
        self.assertEqual(len(set(cookies)), 2)
        self.assertTrue(all('qwenwork-bootstrap' not in cookie for cookie in cookies))
        client.request.side_effect = [VALID, {**token, 'data': {'token': 'test', 'registration': {'token_version': 1}}}]
        with self.assertRaises(ValueError):
            provider.verify(client, 'prv_local')

    def test_refuses_external_provisioning(self):
        for url in ['https://example.com', 'http://192.168.1.1', 'http://user:secret@127.0.0.1', 'http://127.0.0.1/private']:
            with self.assertRaises(ValueError):
                provider.Client(url)


if __name__ == '__main__':
    unittest.main()
