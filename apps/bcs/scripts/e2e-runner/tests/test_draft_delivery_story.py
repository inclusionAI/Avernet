"""The frozen 32-obligation child story against a deterministic HTTP fence.

The product-CLI steps are injected through the Story.cli seam so the journey,
evidence assembly, identity symmetry and redaction gates are exercised offline;
the real binary path is proven by the owned-local live run.
"""
import io
import json
import subprocess
import os
import sys
import tempfile
import threading
import unittest
import uuid
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.draft_delivery.checks import verify_all_obligations
from bcs_e2e.draft_delivery.contract import FIXTURE_SOURCE, REQUIREMENTS, STORY_CHECKS
from bcs_e2e.draft_delivery.story import (EXPECTED_CHECKS, FIXTURE_BYTES, Story,
                                          StoryFailure)
import bcs_e2e.draft_delivery.story as story_module

FIXTURE = FIXTURE_SOURCE
REGISTRATION_TOKEN = 'registration-secret-only-in-memory'
AUTHOR_TOKEN = 'owner-secret-only-in-memory'
CARD_TOKEN = 'A' * 43
SOURCE_TEXT = json.loads(FIXTURE.read_text(encoding='utf-8'))['revision']['definition_yaml']
SOURCE_BYTES = SOURCE_TEXT.encode('utf-8')
UPDATED_BYTES = SOURCE_BYTES + b'\n# second revision\n'
SOURCE_HASH = __import__('hashlib').sha256(SOURCE_BYTES).hexdigest()
UPDATED_HASH = __import__('hashlib').sha256(UPDATED_BYTES).hexdigest()


class DraftFence:
    """One disposable Draft, revision 1 incident bytes, revision 2 appended."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.head = 0
        self.known: set[str] = set()
        self.revoked: set[int] = set()
        self.deleted = False
        self.records: list[dict] = []

    def content(self, text: bytes, revision: int) -> dict:
        return {
            'draft_id': 'observed', 'revision': revision,
            'definition_yaml': text.decode('utf-8'),
            'title': 'Draft delivery regression',
            'content_hash': __import__('hashlib').sha256(text).hexdigest(),
            'size_bytes': len(text),
            'created_by': 'author', 'created_at': '2026-10-30T00:00:00Z',
        }

    def available(self, draft_id: str, revision: int) -> bool:
        return (draft_id in self.known and not self.deleted
                and revision not in self.revoked and 1 <= revision <= self.head)


class Handler(BaseHTTPRequestHandler):
    fence: DraftFence

    def log_message(self, *_args):
        pass

    def observe(self):
        self.fence.records.append({
            'method': self.command, 'path': urlsplit(self.path).path,
            'x_mock': 'X-Mock-User-Id' in self.headers,
            'auth': 'Authorization' in self.headers,
        })

    def _send(self, status, value=None, *, envelope=False):
        raw = b'' if value is None else json.dumps(value).encode()
        self.send_response(status)
        if envelope:
            self.send_header('Content-Type', 'application/json')
        if self.path.startswith('/collaboration/drafts/') and not envelope:
            self.send_header('Cache-Control', 'no-store')
        if envelope:
            self.send_header('Cache-Control', 'no-store')
        if raw:
            if not envelope:
                self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
        else:
            self.send_header('Content-Length', '0')
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _authorization(self):
        return self.headers.get('Authorization') == 'Bearer ' + AUTHOR_TOKEN

    def _body(self):
        length = int(self.headers.get('Content-Length', '0'))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        self.observe()
        path = urlsplit(self.path).path
        fence = self.fence
        if path == '/register/token':
            return self._send(200, {'token': REGISTRATION_TOKEN})
        if path.startswith('/api/v1/collaboration/drafts/'):
            parts = path.split('/')
            draft_id, revision = parts[5], int(parts[7])
            known = fence.available(draft_id, revision)
            payload = ({'code': 'OK', 'message': '', 'requestId': str(uuid.uuid4()),
                        'data': fence.content(SOURCE_BYTES if revision == 1 else UPDATED_BYTES, revision)}
                       if known else
                       {'code': 'DRAFT_REFERENCE_UNAVAILABLE',
                        'message': 'Draft reference is unavailable', 'data': None,
                        'requestId': str(uuid.uuid4())})
            return self._send(200 if known else 404, payload, envelope=True)
        if path.startswith('/collaboration/drafts/'):
            parts = path.split('/')
            if len(parts) == 4:
                if not self._authorization():
                    return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
                if parts[3] not in fence.known:
                    return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
                return self._send(200, {'draft_id': parts[3], 'owner_bot_id': 'author-bot',
                                        'head_revision': fence.head, 'deleted': fence.deleted,
                                        'created_at': '2026-10-30T00:00:00Z'})
            revision = int(parts[5])
            if len(parts) == 7 and parts[6] == 'reference':
                if not self._authorization():
                    return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
                if not fence.available(parts[3], revision):
                    return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
                return self._send(200, {'schemaVersion': 2, 'draftId': parts[3],
                                        'revision': revision, 'readToken': CARD_TOKEN})
            if not fence.available(parts[3], revision):
                return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
            return self._send(200, fence.content(
                SOURCE_BYTES if revision == 1 else UPDATED_BYTES, revision))
        return self._send(404, {'error': 'NOT_FOUND'})

    def do_POST(self):
        self.observe()
        path = urlsplit(self.path).path
        fence = self.fence
        if path == '/me/ensure-human':
            return self._send(200, {})
        if path.startswith('/register'):
            query = parse_qs(urlsplit(self.path).query)
            if query.get('token', [''])[0] != REGISTRATION_TOKEN:
                return self._send(403, {'error': 'REGISTRATION_TOKEN_REJECTED'})
            issued = 'bot_' + uuid.uuid4().hex[:8]
            return self._send(200, {'bot_uuid': issued, 'bot_token': AUTHOR_TOKEN})
        if path == '/bots/onboard':
            if not self._authorization():
                return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
            return self._send(200, {})
        if path.startswith('/api/v1/collaboration/drafts/'):
            parts = path.split('/')
            draft_id, revision = parts[5], int(parts[7])
            known = fence.available(draft_id, revision)
            payload = ({'code': 'OK', 'message': '', 'requestId': str(uuid.uuid4()),
                        'data': {'valid': True}}
                       if known else
                       {'code': 'DRAFT_REFERENCE_UNAVAILABLE',
                        'message': 'Draft reference is unavailable', 'data': None,
                        'requestId': str(uuid.uuid4())})
            return self._send(200 if known else 404, payload, envelope=True)
        if path.startswith('/collaboration/drafts/'):
            parts = path.split('/')
            if parts[-1] == 'validate' and len(parts) == 7:
                if not fence.available(parts[3], int(parts[5])):
                    return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
                return self._send(200, {'draft_id': parts[3], 'revision': int(parts[5]),
                                        'content_hash': SOURCE_HASH,
                                        'validation': {'valid': True}})
            if parts[-1] == 'revisions' and len(parts) == 5:
                if not self._authorization():
                    return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
                body = self._body()
                if body.get('expected_revision') != fence.head:
                    return self._send(409, {'error': 'DRAFT_CONFLICT'})
                if fence.head == 1:
                    fence.head = 2
                    return self._send(200, fence.content(UPDATED_BYTES, 2))
                return self._send(200, fence.content(UPDATED_BYTES, fence.head))
        return self._send(404, {'error': 'NOT_FOUND'})

    def do_PUT(self):
        self.observe()
        path = urlsplit(self.path).path
        fence = self.fence
        if not path.startswith('/collaboration/drafts/') or len(path.split('/')) != 4:
            return self._send(404, {'error': 'NOT_FOUND'})
        if not self._authorization():
            return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
        if fence.deleted:
            return self._send(404, {'error': 'DRAFT_REFERENCE_UNAVAILABLE'})
        body = self._body()
        if fence.head == 0:
            fence.head = 1
            fence.known.add(path.split('/')[3])
            return self._send(201, fence.content(SOURCE_BYTES, 1))
        if body.get('definition_yaml') == SOURCE_TEXT:
            return self._send(200, fence.content(SOURCE_BYTES, 1))
        return self._send(409, {'error': 'DRAFT_CONFLICT'})

    def do_DELETE(self):
        self.observe()
        path = urlsplit(self.path).path
        fence = self.fence
        if not path.startswith('/collaboration/drafts/'):
            return self._send(404, {'error': 'NOT_FOUND'})
        header = self.headers.get('Authorization')
        if header is None:
            return self._send(401, {'error': 'DRAFT_BOT_AUTH_REQUIRED'})
        if header != 'Bearer ' + AUTHOR_TOKEN:
            return self._send(403, {'error': 'DRAFT_FORBIDDEN'})
        parts = path.split('/')
        if len(parts) == 7 and parts[6] == 'share':
            fence.revoked.add(int(parts[5]))
            return self._send(204)
        if len(parts) == 4:
            fence.deleted = True
            return self._send(204)
        return self._send(404, {'error': 'NOT_FOUND'})


class DraftFenceServer:
    def __init__(self):
        self.fence = DraftFence()
        Handler.fence = self.fence
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = 'http://127.0.0.1:' + str(self.server.server_address[1])

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def reset(self):
        self.fence.reset()


class FakeCli:
    """Product-CLI stand-in routed through the shared in-process fence state."""

    def __init__(self, fence: DraftFence, work: Path):
        self.fence = fence
        self.work = work

    def __call__(self, label: str, arguments: list[str]) -> dict:
        fence = self.fence
        if arguments and arguments[0] == 'preview':
            text = Path(arguments[1]).read_text(encoding='utf-8')
            return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0,
                    'json': {'snapshot': {'schemaVersion': 1, 'definitionYaml': text}}}
        if '--draft-id' in arguments:
            draft_id = arguments[arguments.index('--draft-id') + 1]
        else:
            draft_id = arguments[2] if len(arguments) > 2 else ''
        operation = arguments[1]
        if operation == 'create':
            payload = Path(arguments[2]).read_bytes()
            if payload == SOURCE_BYTES:
                if fence.head == 0:
                    fence.head = 1
                    fence.known.add(draft_id)
                    return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0, 'json': {
                        'saved': True, 'phase': 'ready', 'draft_id': draft_id,
                        'revision': 1, 'size_bytes': FIXTURE_BYTES,
                        'content_hash': SOURCE_HASH,
                        'validation': {'validation': {'valid': True}}}}
                return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0, 'json': {
                    'saved': True, 'phase': 'ready', 'draft_id': draft_id,
                    'revision': 1, 'size_bytes': FIXTURE_BYTES, 'content_hash': SOURCE_HASH,
                    'matched_existing_revision': False,
                    'validation': {'validation': {'valid': True}}}}
            return {'exit_code': 1, 'stdout': '', 'stderr_bytes': 31,
                    'json': {'http_status': 409, 'error': 'DRAFT_CONFLICT'}}
        if operation == 'get':
            output = arguments[arguments.index('--output') + 1]
            Path(output).write_bytes(SOURCE_BYTES)
            return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0, 'json': {
                'output': output, 'draft_id': draft_id, 'revision': 1,
                'size_bytes': FIXTURE_BYTES}}
        if operation == 'validate':
            return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0, 'json': {
                'draft_id': draft_id, 'revision': 1, 'validation': {'valid': True}}}
        if operation == 'update':
            payload = Path(arguments[3]).read_bytes()
            expected = arguments[arguments.index('--expected-revision') + 1]
            if not fence.known or fence.deleted or expected != str(fence.head) or fence.head != 1:
                return {'exit_code': 1, 'stdout': '', 'stderr_bytes': 31,
                        'json': {'http_status': 409, 'error': 'DRAFT_CONFLICT'}}
            if payload != UPDATED_BYTES:
                return {'exit_code': 1, 'stdout': '', 'stderr_bytes': 31,
                        'json': {'http_status': 409, 'error': 'DRAFT_CONFLICT'}}
            fence.head = 2
            return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0, 'json': {
                'saved': True, 'phase': 'ready', 'draft_id': draft_id, 'revision': 2,
                'size_bytes': len(UPDATED_BYTES), 'content_hash': UPDATED_HASH,
                'validation': {'validation': {'valid': True}}}}
        if operation == 'preview':
            revision = int(arguments[arguments.index('--revision') + 1])
            reference = {'draftId': draft_id, 'revision': revision, 'schemaVersion': 2}
            if revision == 2:
                reference = {'draftId': draft_id, 'revision': 2, 'schemaVersion': 2}
            return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0, 'json': {
                'phase': 'ready', 'size_bytes': FIXTURE_BYTES if revision == 1 else len(UPDATED_BYTES),
                'reference': reference}}
        if operation == 'revoke':
            revision = int(arguments[arguments.index('--revision') + 1])
            fence.revoked.add(revision)
            return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0,
                    'json': {'revoked': True}}
        if operation == 'delete':
            fence.deleted = True
            return {'exit_code': 0, 'stdout': '', 'stderr_bytes': 0,
                    'json': {'deleted': True}}
        raise AssertionError('unexpected CLI verb: ' + ' '.join(arguments))


class DraftCliCookieForwardingTests(unittest.TestCase):
    """Bearer-mode product-CLI subprocesses receive the validated Human cookie."""

    def _story(self, identity_mode: str, cookie_env: str = 'BCS_E2E_HUMAN_COOKIE'):
        with patch.dict('os.environ', {'BCS_E2E_DRAFT_AUTHOR_TOKEN': AUTHOR_TOKEN,
                                        'BCS_E2E_HUMAN_COOKIE': 'duo-cookie-canary'}):
            return Story('http://127.0.0.1:1', 'e2ehuman', FIXTURE,
                         Path('/unused/product-cli'), identity_mode=identity_mode,
                         bearer_token_env='BCS_E2E_DRAFT_AUTHOR_TOKEN',
                         cookie_env=cookie_env)

    def _capture(self, argv):
        return subprocess.CompletedProcess(argv, 0, stdout=b'{"saved": true}', stderr=b'')

    def test_bearer_mode_cli_subprocess_env_carries_bcs_cookie(self):
        story = self._story('bearer')
        captured = {}

        def fake_run(argv, **kwargs):
            captured['argv'] = argv
            captured['env'] = kwargs.get('env') or {}
            return self._capture(argv)

        with patch.object(story_module.subprocess, 'run', side_effect=fake_run):
            receipt = story.cli('guard', ['draft', 'create', 'x.yaml'])
        self.assertEqual(receipt['exit_code'], 0)
        self.assertEqual(captured['env'].get('BCS_COOKIE'), 'duo-cookie-canary')
        self.assertEqual(captured['env'].get('MOLTIS_BCS_URL'), 'http://127.0.0.1:1')

    def test_register_mode_cli_subprocess_env_has_no_cookie(self):
        story = self._story('register')
        captured = {}

        def fake_run(argv, **kwargs):
            captured['env'] = kwargs.get('env') or {}
            return self._capture(argv)

        with patch.object(story_module.subprocess, 'run', side_effect=fake_run):
            story.cli('guard', ['preview', 'x.yaml'])
        self.assertNotIn('BCS_COOKIE', captured['env'])

    def test_absent_cookie_env_leaves_the_cli_subprocess_without_cookie(self):
        with patch.dict('os.environ', {'BCS_E2E_DRAFT_AUTHOR_TOKEN': AUTHOR_TOKEN},
                        clear=True):
            story = Story('http://127.0.0.1:1', 'e2ehuman', FIXTURE,
                          Path('/unused/product-cli'), identity_mode='bearer',
                          bearer_token_env='BCS_E2E_DRAFT_AUTHOR_TOKEN',
                          cookie_env='BCS_E2E_HUMAN_COOKIE')
            self.assertEqual(story._human_cookie, '')
            captured = {}

            def fake_run(argv, **kwargs):
                captured['env'] = kwargs.get('env') or {}
                return self._capture(argv)

            with patch.object(story_module.subprocess, 'run', side_effect=fake_run):
                story.cli('guard', ['draft', 'create', 'x.yaml'])
        self.assertNotIn('BCS_COOKIE', captured['env'])


class DraftDeliveryStoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fence_server = DraftFenceServer()
        cls.addClassCleanup(cls.fence_server.close)

    def setUp(self):
        self.fence_server.reset()
        self.work = Path(__file__).resolve().parent

    def run_story(self, identity_mode: str, bearer_token_env: str = '') -> tuple:
        story = Story(self.fence_server.base_url, 'e2ehuman', FIXTURE,
                      Path('/unused/product-cli'), identity_mode=identity_mode,
                      bearer_token_env=bearer_token_env)
        story.work = self.work
        story.cli = FakeCli(self.fence_server.fence, self.work)
        evidence = story.run()
        return story, evidence

    def test_register_mode_journey_emits_the_frozen_65_check_evidence(self):
        story, evidence = self.run_story('register')
        self.assertEqual(story.checks, EXPECTED_CHECKS)
        self.assertEqual(EXPECTED_CHECKS, STORY_CHECKS)
        self.assertEqual(evidence['schema_version'], 2)
        self.assertEqual(evidence['identity_mode'], 'register')
        self.assertEqual(set(evidence['obligations']), set(REQUIREMENTS))
        self.assertEqual(len(evidence['obligations']), 32)
        self.assertEqual(evidence['author']['resolved'], True)
        self.assertEqual(evidence['fixture']['size_bytes'], FIXTURE_BYTES)
        self.assertEqual(evidence['fixture']['content_hash'], SOURCE_HASH)
        self.assertNotEqual(evidence['fixture']['content_hash'],
                            evidence['fixture']['updated_content_hash'])
        matrix = {row['status'] for row in evidence['responses']}
        self.assertTrue({200, 401, 404, 409}.issubset(matrix))
        self.assertTrue(all(row['cache_control'] == 'no-store'
                            for row in evidence['responses']))
        self.assertIsNone(evidence['public_v1_face'])
        payload = json.dumps(evidence, sort_keys=True)
        for canary in (REGISTRATION_TOKEN, AUTHOR_TOKEN, 'readToken', 'Bearer ',
                       'bot_token', 'read_token'):
            self.assertNotIn(canary, payload)
        verify_all_obligations(evidence)

    def test_register_bootstrap_accepts_the_server_issued_legacy_bot_id(self):
        story, evidence = self.run_story('register')
        self.assertTrue(story.author_token)
        # Trusted legacy /register commits bot_ + 8 uuid hex ids
        # (bcs-bot core::ids); canonical uuid4 strings stay accepted too.
        self.assertRegex(evidence['author']['uuid'], r'^(bot_[0-9a-f]{8}|[0-9a-f-]{36})$')
        verify_all_obligations(evidence)

    def test_register_mode_sends_mock_identity_only_where_the_owned_leg_sends_it(self):
        self.run_story('register')
        records = self.fence_server.fence.records
        bootstrap = [row for row in records
                     if row['path'] in ('/me/ensure-human', '/register', '/register/token',
                                        '/bots/onboard')]
        self.assertEqual(len(bootstrap), 4)
        self.assertTrue(all(row['x_mock'] for row in bootstrap))
        fixed_reads = [row for row in records
                       if row['path'].startswith('/collaboration/drafts/')
                       and '/revisions/' in row['path'] and '/reference' not in row['path']]
        self.assertTrue(len(fixed_reads) >= 4)
        anonymous = [row for row in fixed_reads if not row['auth']]
        self.assertTrue(anonymous)
        self.assertFalse(any(row['x_mock'] for row in anonymous))
        self.assertFalse(any(row['x_mock'] for row in fixed_reads
                             if row['method'] == 'GET' and not row['auth']))
        metadata_reads = [row for row in records
                          if row['path'].startswith('/collaboration/drafts/')
                          and row['auth'] and row['method'] == 'GET'
                          and '/revisions/' not in row['path']]
        self.assertTrue(metadata_reads)
        self.assertTrue(all(row['x_mock'] for row in metadata_reads))
        owner = [row for row in records
                 if row['path'].startswith('/collaboration/drafts/') and row['auth']]
        self.assertTrue(owner)
        self.assertTrue(all(row['x_mock'] for row in owner))

    def test_bearer_mode_journey_is_the_same_frozen_journey(self):
        with patch.dict('os.environ', {'BCS_E2E_DRAFT_AUTHOR_TOKEN': AUTHOR_TOKEN}):
            story, evidence = self.run_story('bearer', 'BCS_E2E_DRAFT_AUTHOR_TOKEN')
        self.assertEqual(story.checks, EXPECTED_CHECKS)
        self.assertEqual(evidence['schema_version'], 2)
        self.assertEqual(evidence['identity_mode'], 'bearer')
        self.assertEqual(evidence['author']['uuid'], None)
        self.assertEqual(evidence['author']['resolved'], True)
        self.assertEqual(set(evidence['obligations']), set(REQUIREMENTS))
        matrix = {row['status'] for row in evidence['responses']}
        self.assertTrue({200, 401, 404, 409}.issubset(matrix))
        self.assertFalse(any(row['x_mock'] for row in self.fence_server.fence.records))
        verify_all_obligations(evidence)
        face = evidence['public_v1_face']
        self.assertIsInstance(face, dict)
        self.assertEqual(face['read_envelope_code'], 'DRAFT_REFERENCE_UNAVAILABLE')
        self.assertEqual(face['read_envelope_message'], 'Draft reference is unavailable')
        self.assertEqual(face['validate_envelope_code'], 'DRAFT_REFERENCE_UNAVAILABLE')
        self.assertEqual(face['legacy_tokenless_read_error'], 'DRAFT_REFERENCE_UNAVAILABLE')
        payload = json.dumps(evidence, sort_keys=True)
        for canary in (AUTHOR_TOKEN, 'readToken', 'Bearer ', 'bot_token', 'read_token'):
            self.assertNotIn(canary, payload)

    def test_main_emits_only_the_marker_and_a_fresh_evidence_file(self):
        import bcs_e2e.draft_delivery.story as story_module
        marker_payload = {'schema_version': 2, 'draft_id': str(uuid.uuid4()),
                          'identity_mode': 'register',
                          'fixture': {'content_hash': SOURCE_HASH,
                                      'updated_content_hash': UPDATED_HASH,
                                      'size_bytes': FIXTURE_BYTES},
                          'author': {'uuid': None, 'resolved': True},
                          'obligations': {}, 'responses': [], 'public_v1_face': None}

        class SimpleStory:
            checks = STORY_CHECKS

            def __init__(self, *constructor_args, **constructor_kwargs):
                pass

            def run(self):
                return marker_payload

        with tempfile.TemporaryDirectory() as temporary:
            previous = Path.cwd()
            os.chdir(temporary)
            try:
                with patch.object(story_module, 'Story', SimpleStory):
                    with redirect_stdout(io.StringIO()) as stdout:
                        code = story_module.main([
                            '--base-url', 'http://127.0.0.1:1', '--human', 'e2ehuman',
                            '--fixture', str(FIXTURE), '--evidence-file',
                            'evidence.json', '--cli-binary', '/unused/product-cli'])
            finally:
                os.chdir(previous)
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(),
                             'Draft delivery story: 65 live HTTP assertions passed\n')
            emitted = json.loads((Path(temporary) / 'evidence.json').read_text(encoding='utf-8'))
            self.assertEqual(emitted, marker_payload)

    def test_main_fails_closed_on_count_mismatch_and_canary_evidence(self):
        import bcs_e2e.draft_delivery.story as story_module

        class UnevenStory:
            checks = 57

            def __init__(self, *constructor_args, **constructor_kwargs):
                pass

            def run(self):
                return {'schema_version': 2}

        class CanaryStory:
            checks = STORY_CHECKS

            def __init__(self, *constructor_args, **constructor_kwargs):
                pass

            def run(self):
                return {'schema_version': 2, 'draft_id': str(uuid.uuid4()),
                        'identity_mode': 'register',
                        'fixture': {'content_hash': SOURCE_HASH,
                                    'updated_content_hash': UPDATED_HASH,
                                    'size_bytes': FIXTURE_BYTES},
                        'author': {'uuid': None, 'resolved': True},
                        'obligations': {}, 'responses': [],
                        'public_v1_face': {'leak': 'readToken'}}

        for staged, expected in ((UnevenStory, 'assertion count mismatch'),
                                 (CanaryStory, 'redaction gate')):
            with self.subTest(story=staged.__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    previous = Path.cwd()
                    os.chdir(temporary)
                    try:
                        evidence_path = Path(temporary) / 'evidence.json'
                        with patch.object(story_module, 'Story', staged):
                            with self.assertRaisesRegex(StoryFailure, expected):
                                story_module.main([
                                    '--base-url', 'http://127.0.0.1:1', '--human', 'e2ehuman',
                                    '--fixture', str(FIXTURE), '--evidence-file',
                                    str(evidence_path), '--cli-binary',
                                    '/unused/product-cli'])
                    finally:
                        os.chdir(previous)
                    self.assertFalse(evidence_path.exists())

    def test_fixture_is_the_frozen_2174_byte_incident(self):
        self.assertEqual(len(SOURCE_BYTES), FIXTURE_BYTES)
        self.assertEqual(len(UPDATED_BYTES), FIXTURE_BYTES + len(b'\n# second revision\n'))
        self.assertNotEqual(SOURCE_HASH, UPDATED_HASH)


if __name__ == '__main__':
    unittest.main()
