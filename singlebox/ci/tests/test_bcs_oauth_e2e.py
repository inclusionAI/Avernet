"""Regression tests for the OAuth E2E isolation boundary and gate integration."""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import build_opener, HTTPSHandler

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / 'apps/bcs/scripts/e2e-test/oauth/provider_stub.py'
SPEC = importlib.util.spec_from_file_location('oauth_provider_stub', MODULE)
STUB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STUB)


class OAuthE2eTest(unittest.TestCase):
    def test_stub_uses_verified_tls_and_does_not_forward_unknown_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            with STUB.provider_stub(Path(directory)) as state:
                proxy = STUB.LoopbackProxyHandler(state.proxy_url)
                untrusted = build_opener(proxy, HTTPSHandler(context=ssl.create_default_context()))
                with self.assertRaises(URLError):
                    untrusted.open('https://api.github.com/user', timeout=5)
                context = ssl.create_default_context(cafile=str(state.root / 'ca.pem'))
                # Python 3.13 defaults to strict verification; exercise it on
                # earlier Python versions too, without disabling hostname checks.
                context.verify_flags |= ssl.VERIFY_X509_STRICT
                self.assertTrue(context.check_hostname)
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                for filename in ('ca.conf', 'extensions'):
                    extensions = (state.root / filename).read_text()
                    self.assertIn('subjectKeyIdentifier=hash', extensions)
                    self.assertIn('authorityKeyIdentifier=keyid:always', extensions)
                trusted = build_opener(proxy, HTTPSHandler(context=context))
                with patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'}):
                    with self.assertRaises(HTTPError) as error:
                        trusted.open('https://api.github.com/user', timeout=5)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()
                self.assertEqual(state.count('userinfo'), 1)
                with socket.create_connection(('127.0.0.1', urlsplit(state.proxy_url).port), timeout=5) as sock:
                    sock.sendall(b'CONNECT unknown.invalid:443 HTTP/1.1\r\nHost: unknown.invalid\r\n\r\n')
                    self.assertIn(b'403', sock.recv(1024))
                self.assertFalse(state.errors)

    def test_codes_are_single_use_and_bound_to_the_provider_and_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            state = STUB.State(Path(directory))
            params = {'redirect_uri': 'http://127.0.0.1:1234/callback', 'state': 'opaque'}
            status, _, headers = state.handle('github.com', '/login/oauth/authorize', 'GET', params, {})
            self.assertEqual(status, 302)
            code = parse_qs(urlsplit(headers['Location']).query)['code'][0]
            grant = {'code': code, 'grant_type': 'authorization_code',
                     'client_secret': state.secret, 'redirect_uri': params['redirect_uri']}
            self.assertEqual(state.handle('github.com', '/login/oauth/access_token', 'POST', grant, {})[0], 200)
            self.assertEqual(state.handle('github.com', '/login/oauth/access_token', 'POST', grant, {})[0], 400)
            _, _, headers = state.handle('github.com', '/login/oauth/authorize', 'GET', params, {})
            grant['code'] = parse_qs(urlsplit(headers['Location']).query)['code'][0]
            self.assertEqual(state.handle('oauth2.googleapis.com', '/token', 'POST', grant, {})[0], 400)
            _, _, headers = state.handle('github.com', '/login/oauth/authorize', 'GET', params, {})
            grant['code'] = parse_qs(urlsplit(headers['Location']).query)['code'][0]
            grant['redirect_uri'] = 'http://127.0.0.1:1234/other-callback'
            self.assertEqual(state.handle('github.com', '/login/oauth/access_token', 'POST', grant, {})[0], 400)

    def test_build_failure_invalidates_stale_artifacts_without_erasing_shared_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / 'bcs/scripts'
            scripts.mkdir(parents=True)
            runner = scripts / 'oauth_e2e_coverage.sh'
            shutil.copyfile(ROOT / 'apps/bcs/scripts/oauth_e2e_coverage.sh', runner)
            shutil.copyfile(ROOT / 'apps/bcs/scripts/build_e2e_companions.sh', scripts / 'build_e2e_companions.sh')
            runtime = root / 'bcs/target/cov-e2e/llvm-cov-target'
            runtime.mkdir(parents=True)
            (runtime / 'oauth-old.profraw').write_bytes(b'old')
            (runtime / 'bcs-shared.profraw').write_bytes(b'shared')
            report = runtime.parent / 'oauth_e2e.json'
            report.write_text('{"status":"passed"}')
            tools = root / 'bin'
            tools.mkdir()
            cargo = tools / 'cargo'
            cargo.write_text('#!/bin/sh\nexit 23\n')
            cargo.chmod(0o755)
            result = subprocess.run(['bash', str(runner)], capture_output=True,
                env={**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH']})
            self.assertEqual(result.returncode, 23)
            self.assertEqual(json.loads(report.read_text())['status'], 'failed')
            self.assertFalse((runtime / 'oauth-old.profraw').exists())
            self.assertEqual((runtime / 'bcs-shared.profraw').read_bytes(), b'shared')

    def test_companion_is_required_and_report_is_copied(self):
        runner = (ROOT / 'apps/bcs/scripts/e2e_coverage.sh').read_text()
        self.assertIn('bash "$bcs_dir/scripts/oauth_e2e_coverage.sh" || e2e_status=$?', runner)
        self.assertNotIn('    bcs-user-identity/src/memory.rs', runner)
        singlebox = (ROOT / 'singlebox/ci/singlebox_coverage.sh').read_text()
        self.assertIn('oauth_e2e.json', singlebox)
        companion = (ROOT / 'apps/bcs/scripts/oauth_e2e_coverage.sh').read_text()
        self.assertIn('"status":"failed"', companion)
        self.assertIn('LLVM_PROFILE_FILE="$cov_runtime/oauth-%m-%p.profraw"', companion)
        self.assertNotIn('cargo test', companion)


if __name__ == '__main__':
    unittest.main()
