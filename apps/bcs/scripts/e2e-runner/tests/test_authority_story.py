"""The frozen 42-obligation Bot-authority journey against a dual-plane HTTP fence.

The fence replays the frozen product surface offline: a trusted mock-header
registration plane, a Gateway-Principal-HS256 business plane carrying the
frozen ``{code, message, request_id, data}`` envelope on every status, the
manager single-item polarities, the renewal-window transfer chain and both
transfer inboxes. The product-CLI readiness gate runs as a disposable stub
executable because the journey owns exactly that subprocess seam.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bcs_e2e.authority.story as story_module
from bcs_e2e.authority import checks, contract
from bcs_e2e.authority.story import CLAIM_CANARIES, EXPECTED_CHECKS, Story, StoryFailure, main

OWNER_HUMAN = 'wp2authorityownerhuman00000000'
TARGET_HUMAN = '002'
MINE_HUMAN = '137'
NEVER_HUMAN = 'never-seen-fence-fixed-0001'
SIGNING_KEY = 'wp2-fence-signing-key-0123456789abcdef0123456789abcdef'
BOT_TOKEN = 'wp2-fence-bot-token-only-in-memory'
INTERNAL = contract.INTERNAL_PREFIX
PUBLIC = contract.PUBLIC_PREFIX

HEALTHY_STUB = '#!/bin/sh\nprintf \'{"status":"healthy"}\'\n'
UNHEALTHY_STUB = '#!/bin/sh\nprintf \'{"status":"unhealthy"}\'\n'
FAILING_STUB = '#!/bin/sh\nexit 1\n'


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def mint_principal(subject: str, *, expired: bool = False, key: str = SIGNING_KEY) -> str:
    now = int(time.time())
    header = {'alg': 'HS256', 'kid': 'bare', 'typ': 'JWT'}
    claims = {'iss': 'gateway', 'aud': 'bcs', 'iat': now,
              'exp': (now - 120) if expired else now + 60,
              'principals': [{'type': 'user', 'tenant': 'bcs-e2e',
                              'subject': {'id': subject, 'username': subject,
                                          'display_name': None, 'full_name': None,
                                          'tenant_id': 'bcs-e2e'}}]}
    signing_input = (_b64(json.dumps(header, sort_keys=True, separators=(',', ':')).encode())
                     + '.' + _b64(json.dumps(claims, sort_keys=True, separators=(',', ':')).encode()))
    digest = hmac.new(key.encode(), signing_input.encode('ascii'), hashlib.sha256).digest()
    return signing_input + '.' + _b64(digest)


def primitive_open(url, *, method='GET', headers=None):
    request = urllib.request.Request(url, method=method)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    return request


class AuthorityFence:
    """Deterministic offline product surface for the owned journey."""

    def reset(self):
        self.tokens: dict[str, str] = {}
        self.humans: set[str] = set()
        self.bots: dict[str, str] = {}
        self.owners: dict[str, str] = {}
        self.managers: dict[str, set[str]] = {}
        self.transfers: dict[str, dict] = {}
        self.records: list[dict] = []
        self.collide_bot_ids = False
        self.broken_route: str | None = None
        self.envelope_drift = False
        self.principal_reject = False


class Handler(BaseHTTPRequestHandler):
    fence: AuthorityFence

    def log_message(self, *_args):
        pass

    # ------------------------------------------------------------- plumbing
    def _record(self, plane: str, principal_subject: str | None) -> None:
        self.fence.records.append({
            'method': self.command, 'path': urllib.parse.urlsplit(self.path).path, 'plane': plane,
            'x_mock': any(name.startswith('X-Mock-') for name in self.headers),
            'principal': 'X-Avernet-Principal' in self.headers,
            'principal_subject': principal_subject})

    def _send(self, status: int, value) -> None:
        raw = json.dumps(value).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _business_send(self, status: int, data) -> None:
        payload = {'code': status, 'message': 'authority fence observation',
                   'request_id': str(uuid.uuid4()), 'data': data}
        if self.fence.envelope_drift:
            payload.pop('request_id')
        return self._send(status, payload)

    def _principal_subject(self) -> tuple[str | None, str | None]:
        token = self.headers.get('X-Avernet-Principal')
        if token is None:
            return None, 'missing'
        try:
            header_segment, claims_segment, signature_segment = token.split('.')
            header = json.loads(base64.urlsafe_b64decode(header_segment + '=='))
            claims = json.loads(base64.urlsafe_b64decode(claims_segment + '=='))
        except (ValueError, json.JSONDecodeError, TypeError):
            return None, 'malformed'
        if not isinstance(header, dict) or header.get('alg') != 'HS256' or header.get('kid') != 'bare':
            return None, 'algorithm'
        if claims.get('iss') != 'gateway' or claims.get('aud') != 'bcs':
            return None, 'claims'
        now = int(time.time())
        if type(claims.get('exp')) is not int or not (now - 5 <= claims['exp'] <= now + 120):
            return None, 'expired'
        principals = claims.get('principals')
        if not isinstance(principals, list) or not principals:
            return None, 'subject'
        subject = principals[0].get('subject', {}).get('id') if isinstance(principals[0], dict) else None
        if not isinstance(subject, str):
            return None, 'subject'
        expected = hmac.new(SIGNING_KEY.encode(),
                            token.rsplit('.', 1)[0].encode('ascii'), hashlib.sha256).digest()
        padding = '=' * (-len(signature_segment) % 4)
        try:
            given = base64.urlsafe_b64decode(signature_segment + padding)
        except (ValueError, TypeError):
            return None, 'signature'
        if not hmac.compare_digest(expected, given):
            return None, 'signature'
        return subject, None

    def _business_gate(self) -> tuple[str | None, str | None]:
        """Return (subject, rejection) honoring the frozen admission order.

        Mock identity headers never reach a business path; an unresolvable or
        non-human principal is rejected before any authorization decision.
        """
        if any(name.startswith('X-Mock-') for name in self.headers):
            return None, 'mock'
        subject, failure = self._principal_subject()
        if self.fence.principal_reject or subject is None or failure is not None:
            return None, 'principal_' + (failure or 'missing')
        return subject, None

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        path, _, query_text = self.path.partition('?')
        if path == '/health':
            self._record('infra', None)
            return self._send(200, {'status': 'ok'})
        if path == '/register/token':
            self._record('register', None)
            user = self.headers.get('X-Mock-User-Id')
            if not user:
                return self._send(401, {'error': 'MOCK_IDENTITY_REQUIRED'})
            token = uuid.uuid4().hex
            self.fence.tokens[token] = user
            return self._send(200, {'token': token})
        parts = path.split('/')
        query = urllib.parse.parse_qs(query_text)
        exempt_public_mine = path == PUBLIC + '/bots/mine'
        subject, rejection = self._business_gate()
        if subject is None:
            if rejection == 'mock':
                self._record('business', None)
                return self._business_send(401, {'error_code': 'mock_headers_forbidden_on_business_paths'})
            self._record('business', None)
            return self._business_send(401, {'error_code': rejection})
        if not exempt_public_mine and subject not in self.fence.humans:
            self._record('business', subject)
            return self._business_send(401, {'error_code': 'principal_unknown'})
        self._record('business', subject)
        if path.startswith(INTERNAL + '/ownership-transfers') and len(parts) == 5:
            return self._get_inbox(subject, query.get('direction', [''])[0])
        if path.startswith(PUBLIC + '/bots/mine'):
            self.fence.humans.add(subject)
            return self._business_send(200, {'user_id': subject, 'bots': []})
        if path.startswith(INTERNAL + '/bots/') and len(parts) == 7:
            if parts[6] == 'ownership':
                return self._get_ownership(subject, parts[5])
            if parts[6] == 'managers':
                return self._get_managers(subject, parts[5])
        if (path.startswith(INTERNAL + '/bots/') and len(parts) == 8
                and parts[6] == 'ownership-transfers'):
            return self._get_transfer(subject, parts[5], parts[7])
        return self._business_send(404, {'error_code': 'unknown_route'})

    def do_POST(self):
        path, _, query_text = self.path.partition('?')
        if path == '/register':
            self._record('register', None)
            user = self.headers.get('X-Mock-User-Id')
            query = urllib.parse.parse_qs(query_text)
            token = query.get('token', [''])[0]
            name = query.get('bot-name', [''])[0]
            if self.fence.tokens.pop(token, None) != user or not user or not 2 <= len(name) <= 64:
                return self._send(403, {'error': 'REGISTRATION_TOKEN_REJECTED'})
            bot_id = 'bot_' + uuid.uuid4().hex[:8]
            if self.fence.collide_bot_ids and self.fence.bots:
                bot_id = next(iter(self.fence.bots))
            self.fence.bots[bot_id] = BOT_TOKEN
            self.fence.owners[bot_id] = user
            self.fence.managers[bot_id] = set()
            self.fence.humans.add(user)
            return self._send(200, {'bot_uuid': bot_id, 'bot_token': BOT_TOKEN})
        parts = path.split('/')
        subject, rejection = self._business_gate()
        if subject is None:
            if rejection == 'mock':
                self._record('business', None)
                return self._business_send(401, {'error_code': 'mock_headers_forbidden_on_business_paths'})
            self._record('business', None)
            return self._business_send(401, {'error_code': rejection})
        if subject not in self.fence.humans:
            self._record('business', subject)
            return self._business_send(401, {'error_code': 'principal_unknown'})
        self._record('business', subject)
        if (path.startswith(INTERNAL + '/bots/') and len(parts) == 9
                and parts[6] == 'ownership-transfers' and parts[8] == 'accept'):
            return self._accept_transfer(subject, parts[5])
        return self._business_send(404, {'error_code': 'unknown_route'})

    def do_PUT(self):
        path, _, _ = self.path.partition('?')
        parts = path.split('/')
        subject, rejection = self._business_gate()
        if subject is None:
            if rejection == 'mock':
                self._record('business', None)
                return self._business_send(401, {'error_code': 'mock_headers_forbidden_on_business_paths'})
            self._record('business', None)
            return self._business_send(401, {'error_code': rejection})
        if subject not in self.fence.humans:
            self._record('business', subject)
            return self._business_send(401, {'error_code': 'principal_unknown'})
        self._record('business', subject)
        if (path.startswith(INTERNAL + '/bots/') and len(parts) == 8
                and parts[6] == 'managers'):
            return self._put_manager(subject, parts[5], parts[7])
        if (path.startswith(INTERNAL + '/bots/') and len(parts) == 8
                and parts[6] == 'ownership-transfers'):
            return self._put_transfer(subject, parts[5], parts[7])
        return self._business_send(404, {'error_code': 'unknown_route'})

    def do_DELETE(self):
        path, _, _ = self.path.partition('?')
        parts = path.split('/')
        subject, rejection = self._business_gate()
        if subject is None:
            if rejection == 'mock':
                self._record('business', None)
                return self._business_send(401, {'error_code': 'mock_headers_forbidden_on_business_paths'})
            self._record('business', None)
            return self._business_send(401, {'error_code': rejection})
        if subject not in self.fence.humans:
            self._record('business', subject)
            return self._business_send(401, {'error_code': 'principal_unknown'})
        self._record('business', subject)
        if (path.startswith(INTERNAL + '/bots/') and len(parts) == 8
                and parts[6] == 'managers'):
            return self._delete_manager(subject, parts[5], parts[7])
        return self._business_send(404, {'error_code': 'unknown_route'})

    # ------------------------------------------------------------ behaviors
    def _actor_is_owner(self, subject: str, bot_id: str) -> bool:
        return self.fence.owners.get(bot_id) == subject

    def _actor_is_transfer_target(self, subject: str, bot_id: str) -> bool:
        transfer = self.fence.transfers.get(bot_id)
        return transfer is not None and transfer['target'] == subject

    def _get_ownership(self, subject: str, bot_id: str) -> None:
        if self.fence.broken_route == 'ownership':
            return self._business_send(500, {'error_code': 'fence_broken_route'})
        if not (self._actor_is_owner(subject, bot_id)
                or self._actor_is_transfer_target(subject, bot_id)):
            return self._business_send(403, {'error_code': 'ownership_not_granted'})
        return self._business_send(200, {'bot_id': bot_id,
                                         'owner_user_id': self.fence.owners[bot_id]})

    def _get_managers(self, subject: str, bot_id: str) -> None:
        if not (self._actor_is_owner(subject, bot_id)
                or self._actor_is_transfer_target(subject, bot_id)):
            return self._business_send(403, {'error_code': 'ownership_not_granted'})
        items = sorted(self.fence.managers.get(bot_id, set()))
        owner = self.fence.owners.get(bot_id)
        return self._business_send(200, {'owner': {'owner_user_id': owner},
                                         'total': len(items), 'items': items})

    def _put_manager(self, subject: str, bot_id: str, user_id: str) -> None:
        if not self._actor_is_owner(subject, bot_id):
            return self._business_send(409, {'error_code': 'owner_not_manager'})
        if user_id == self.fence.owners.get(bot_id):
            return self._business_send(409, {'error_code': 'owner_not_manager'})
        if user_id not in self.fence.humans:
            return self._business_send(400, {'error_code': 'bot_authority_human_unknown'})
        self.fence.managers.setdefault(bot_id, set()).add(user_id)
        return self._business_send(200, {'added': 1})

    def _delete_manager(self, subject: str, bot_id: str, user_id: str) -> None:
        if not self._actor_is_owner(subject, bot_id):
            return self._business_send(409, {'error_code': 'owner_not_manager'})
        current = self.fence.managers.setdefault(bot_id, set())
        removed = 1 if user_id in current else 0
        current.discard(user_id)
        return self._business_send(200, {'removed': removed})

    def _put_transfer(self, subject: str, bot_id: str, target_actor: str) -> None:
        if not self._actor_is_owner(subject, bot_id):
            return self._business_send(403, {'error_code': 'ownership_not_granted'})
        if target_actor != 'human_' + TARGET_HUMAN:
            return self._business_send(400, {'error_code': 'bot_authority_human_unknown'})
        transfer = self.fence.transfers.get(bot_id)
        if transfer is None:
            transfer = {'from': subject, 'target': TARGET_HUMAN,
                        'expires': int(time.time() * 1000) + 300000, 'status': 'pending'}
            self.fence.transfers[bot_id] = transfer
            return self._business_send(201, {'status': 'pending',
                                             'target_owner_user_id': TARGET_HUMAN,
                                             'expires_at': transfer['expires']})
        transfer['expires'] = transfer['expires'] + 1000
        return self._business_send(200, {'status': 'pending',
                                         'target_owner_user_id': TARGET_HUMAN,
                                         'expires_at': transfer['expires']})

    def _get_transfer(self, subject: str, bot_id: str, target_actor: str) -> None:
        transfer = self.fence.transfers.get(bot_id)
        if transfer is None or target_actor != 'human_' + TARGET_HUMAN:
            return self._business_send(404, {'error_code': 'transfer_unavailable'})
        if not (self._actor_is_owner(subject, bot_id) or subject == transfer['target']):
            return self._business_send(403, {'error_code': 'ownership_not_granted'})
        return self._business_send(200, {'status': transfer['status'],
                                         'target_owner_user_id': transfer['target'],
                                         'expires_at': transfer['expires']})

    def _accept_transfer(self, subject: str, bot_id: str) -> None:
        transfer = self.fence.transfers.get(bot_id)
        if transfer is None or subject != transfer['target']:
            return self._business_send(409, {'error_code': 'transfer_not_pending_target'})
        transfer['status'] = 'accepted'
        ex_owner = self.fence.owners[bot_id]
        self.fence.owners[bot_id] = subject
        self.fence.managers[bot_id] = {ex_owner}
        return self._business_send(200, {'status': 'accepted', 'owner_user_id': subject})

    def _get_inbox(self, subject: str, direction: str) -> None:
        rows = []
        for bot_id, transfer in self.fence.transfers.items():
            if transfer['status'] != 'accepted':
                continue
            row = {'bot_id': bot_id, 'from': transfer['from'], 'to': transfer['target'],
                   'status': 'accepted'}
            if direction == 'received' and transfer['target'] == subject:
                rows.append(row)
            if direction == 'sent' and transfer['from'] == subject:
                rows.append(row)
        return self._business_send(200, {'items': rows})


class AuthorityFenceServer:
    def __init__(self):
        self.fence = AuthorityFence()
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

    def register_bot(self, user_id: str) -> str:
        opener = urllib.request.build_opener()
        with opener.open(primitive_open(self.base_url + '/register/token',
                                        headers={'X-Mock-User-Id': user_id})) as response:
            token = json.loads(response.read())['token']
        query = urllib.parse.urlencode({'token': token, 'bot-name': 'fence-' + user_id})
        with opener.open(primitive_open(self.base_url + '/register?' + query, method='POST',
                                        headers={'X-Mock-User-Id': user_id})) as response:
            committed = json.loads(response.read())
        return committed['bot_uuid']


class AuthorityStoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.cli_stub = cls.base / 'healthy-cli'
        cls.cli_stub.write_text(HEALTHY_STUB, encoding='utf-8')
        cls.cli_stub.chmod(0o755)
        cls.fence_server = AuthorityFenceServer()
        cls.addClassCleanup(cls.fence_server.close)
        cls.environment = patch.dict(os.environ, {contract.PRINCIPAL_SIGNING_KEY_ENV: SIGNING_KEY})
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)

    def setUp(self):
        self.fence_server.reset()

    def bootstrap(self) -> tuple[str, str]:
        owner_bot = self.fence_server.register_bot(OWNER_HUMAN)
        target_bot = self.fence_server.register_bot(TARGET_HUMAN)
        return owner_bot, target_bot

    def journey(self) -> tuple[Story, dict]:
        journey = Story(self.fence_server.base_url, OWNER_HUMAN, TARGET_HUMAN, MINE_HUMAN,
                        Path(self.cli_stub), signing_key_env=contract.PRINCIPAL_SIGNING_KEY_ENV,
                        run_tag='wp2-story-fence')
        evidence = journey.run()
        return journey, evidence

    # ------------------------------------------------------------ happy path
    def test_the_frozen_journey_completes_42_checks_with_valid_evidence(self):
        journey, evidence = self.journey()
        self.assertEqual(journey.checks, 42)
        self.assertEqual(EXPECTED_CHECKS, 42)
        self.assertEqual(contract.STORY_CHECKS, 42)
        self.assertEqual(set(evidence['obligations']), set(contract.REQUIREMENTS))
        self.assertEqual(checks.verify_all_obligations(evidence),
                         {'status': 'PASS', 'checked': list(contract.REQUIREMENTS)})
        self.assertEqual(evidence['case_id'], 'LOCAL-AUTHORITY-JOURNEY-02')
        self.assertEqual(evidence['identity_mode'], 'register')
        self.assertEqual(evidence['owner']['human_id'], OWNER_HUMAN)
        self.assertRegex(evidence['owner']['bot_id'], r'^bot_[0-9a-f]{8}$')
        self.assertRegex(evidence['target']['bot_id'], r'^bot_[0-9a-f]{8}$')
        self.assertNotEqual(evidence['owner']['bot_id'], evidence['target']['bot_id'])
        self.assertEqual(evidence['http_call_count'], 24)
        self.assertEqual(evidence['bootstrap_http_call_count'], 4)
        self.assertEqual(evidence['business_http_call_count'], 20)
        self.assertEqual((journey.http_call_count, journey.bootstrap_http_call_count,
                          journey.business_http_call_count), (24, 4, 20))
        self.assertEqual(evidence['cli_readiness_gate'], {
            'exit_code': 0, 'health_status': 'healthy', 'transport': 'product-cli',
            'role': 'readiness-gate-only', 'authority_business_verbs': 'none-frozen'})
        self.assertEqual(evidence['registration_plane'],
                         {'mock_admitted': True, 'bots_registered': 2, 'distinct_bot_ids': True})
        self.assertEqual(evidence['business_envelope'],
                         {'enforced': True, 'observed_keys': ['code', 'message', 'request_id', 'data']})
        self.assertTrue(evidence['never_user_id'].startswith('never-seen-'))
        self.assertNotIn(evidence['never_user_id'], (OWNER_HUMAN, TARGET_HUMAN, MINE_HUMAN))
        transfer = evidence['transfer']
        self.assertEqual(transfer['renewed'], True)
        self.assertEqual(transfer['delta_ms'],
                         transfer['refreshed_expires_at'] - transfer['initiated_expires_at'])
        self.assertGreaterEqual(transfer['delta_ms'], 0)
        self.assertLess(transfer['delta_ms'], 300000)
        self.assertEqual(evidence['resource_disposition'],
                         'AUTHORITY_TWO_BOTS_CREATED_AND_RETAINED')
        payload = json.dumps(evidence, sort_keys=True)
        for canary in CLAIM_CANARIES + (SIGNING_KEY, BOT_TOKEN):
            self.assertNotIn(canary, payload)

    def test_wire_records_keep_mock_identity_on_the_registration_plane_only(self):
        self.journey()
        records = self.fence_server.fence.records
        register_rows = [row for row in records if row['plane'] == 'register']
        business_rows = [row for row in records if row['plane'] == 'business']
        self.assertEqual(len(register_rows), 4)
        self.assertEqual(len(business_rows), 20)
        self.assertTrue(all(row['x_mock'] for row in register_rows))
        self.assertFalse(any(row['x_mock'] for row in business_rows))
        self.assertTrue(all(row['principal'] for row in business_rows))
        self.assertFalse(any(row['principal'] for row in register_rows))

    def test_business_plane_carries_only_the_three_journey_principal_subjects(self):
        self.journey()
        subjects = {row['principal_subject'] for row in self.fence_server.fence.records
                    if row['plane'] == 'business'}
        self.assertEqual(subjects, {OWNER_HUMAN, TARGET_HUMAN, MINE_HUMAN})
        # The story seam mints a principal per business request; claims are
        # second-granular, so byte-distinctness is not asserted across one second.
        business_rows = [row for row in self.fence_server.fence.records if row['plane'] == 'business']
        self.assertEqual(len(business_rows), 20)
        self.assertTrue(all(row['principal'] for row in business_rows))

    def test_public_mine_read_ensures_the_mine_human_before_manager_add(self):
        _, evidence = self.journey()
        owner_bot = evidence['owner']['bot_id']
        sequence = [row['path'] for row in self.fence_server.fence.records]
        self.assertIn(PUBLIC + '/bots/mine', sequence)
        self.assertLess(sequence.index(PUBLIC + '/bots/mine'),
                        sequence.index(INTERNAL + '/bots/' + owner_bot
                                       + '/managers/' + MINE_HUMAN))
        self.assertIn(MINE_HUMAN, self.fence_server.fence.humans)

    def test_registered_and_ensured_humans_bound_the_manager_domain(self):
        _, evidence = self.journey()
        self.assertIn(OWNER_HUMAN, self.fence_server.fence.humans)
        self.assertIn(TARGET_HUMAN, self.fence_server.fence.humans)
        self.assertIn(MINE_HUMAN, self.fence_server.fence.humans)
        self.assertNotIn(evidence['never_user_id'], self.fence_server.fence.humans)

    # ------------------------------------------------------------- fences
    def test_origin_fence_rejects_non_loopback_and_portless_origins(self):
        for base_url in ('https://127.0.0.1:' + str(self.fence_server.server.server_address[1]),
                         'http://localhost:' + str(self.fence_server.server.server_address[1]),
                         'http://127.0.0.1'):
            with self.subTest(base_url=base_url):
                with self.assertRaisesRegex(StoryFailure, 'owned loopback origin'):
                    Story(base_url, OWNER_HUMAN, TARGET_HUMAN, MINE_HUMAN, Path(self.cli_stub),
                          signing_key_env=contract.PRINCIPAL_SIGNING_KEY_ENV, run_tag='fence')

    def test_registration_must_commit_two_distinct_disposable_bots(self):
        self.fence_server.fence.collide_bot_ids = True
        journey = Story(self.fence_server.base_url, OWNER_HUMAN, TARGET_HUMAN, MINE_HUMAN,
                        Path(self.cli_stub), signing_key_env=contract.PRINCIPAL_SIGNING_KEY_ENV,
                        run_tag='wp2-story-fence')
        with self.assertRaisesRegex(StoryFailure, 'two distinct disposable Bots'):
            journey.run()
        self.assertEqual(journey.checks, 0)

    def test_unexpected_http_status_keeps_the_unclassified_phrase(self):
        self.fence_server.fence.broken_route = 'ownership'
        journey = Story(self.fence_server.base_url, OWNER_HUMAN, TARGET_HUMAN, MINE_HUMAN,
                        Path(self.cli_stub), signing_key_env=contract.PRINCIPAL_SIGNING_KEY_ENV,
                        run_tag='wp2-story-fence')
        with self.assertRaisesRegex(StoryFailure, 'unexpected HTTP status 500'):
            journey.run()
        self.assertEqual(journey.checks, 0)

    def test_envelope_precondition_rejects_a_drifted_business_map(self):
        self.fence_server.fence.envelope_drift = True
        journey = Story(self.fence_server.base_url, OWNER_HUMAN, TARGET_HUMAN, MINE_HUMAN,
                        Path(self.cli_stub), signing_key_env=contract.PRINCIPAL_SIGNING_KEY_ENV,
                        run_tag='wp2-story-fence')
        with self.assertRaisesRegex(StoryFailure, 'envelope keys differ from the frozen map'):
            journey.run()
        self.assertEqual(journey.checks, 0)

    def test_cli_readiness_gate_separates_the_cli_binary_from_business_paths(self):
        failing = self.base / 'failing-cli'
        failing.write_text(FAILING_STUB, encoding='utf-8')
        failing.chmod(0o755)
        unhealthy = self.base / 'unhealthy-cli'
        unhealthy.write_text(UNHEALTHY_STUB, encoding='utf-8')
        unhealthy.chmod(0o755)
        for stub, phrase in ((failing, 'did not report a healthy BCS'),
                             (unhealthy, 'did not report a healthy BCS'),
                             (self.base / 'absent-cli', 'CLI invocation failed')):
            with self.subTest(stub=stub.name):
                journey = Story(self.fence_server.base_url, OWNER_HUMAN, TARGET_HUMAN, MINE_HUMAN,
                                stub, signing_key_env=contract.PRINCIPAL_SIGNING_KEY_ENV,
                                run_tag='wp2-story-fence')
                with self.assertRaisesRegex(StoryFailure, phrase):
                    journey.run()
                self.assertEqual(journey.checks, 0)
                if stub.name == 'absent-cli':
                    # The subprocess never launched, so no receipt is kept.
                    self.assertEqual(journey.cli_readiness_gate, {})
                else:
                    self.assertEqual(journey.cli_readiness_gate['role'], 'readiness-gate-only')
                    self.assertEqual(journey.cli_readiness_gate['authority_business_verbs'],
                                     'none-frozen')

    # ---------------------------------------------------------- product fences
    def test_register_plane_requires_mock_identity_and_burns_one_time_tokens(self):
        opener = urllib.request.build_opener()
        with self.assertRaises(urllib.error.HTTPError):
            opener.open(primitive_open(self.fence_server.base_url + '/register/token'))
        with opener.open(primitive_open(self.fence_server.base_url + '/register/token',
                                        headers={'X-Mock-User-Id': OWNER_HUMAN})) as response:
            token = json.loads(response.read())['token']
        query = urllib.parse.urlencode({'token': token, 'bot-name': 'fence-cross-user'})
        with self.assertRaises(urllib.error.HTTPError):
            opener.open(primitive_open(self.fence_server.base_url + '/register?' + query,
                                       method='POST', headers={'X-Mock-User-Id': TARGET_HUMAN}))
        with self.assertRaises(urllib.error.HTTPError):
            opener.open(primitive_open(self.fence_server.base_url + '/register?' + query,
                                       method='POST', headers={'X-Mock-User-Id': OWNER_HUMAN}))

    def test_bad_principals_and_mock_headers_are_rejected_on_business_paths(self):
        owner_bot, _ = self.bootstrap()
        opener = urllib.request.build_opener()
        manager_path = (self.fence_server.base_url + INTERNAL + '/bots/' + owner_bot
                        + '/managers/' + TARGET_HUMAN)

        def put(headers):
            request = primitive_open(manager_path, method='PUT', headers=headers)
            try:
                with opener.open(request) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        principal = mint_principal(OWNER_HUMAN)
        status, payload = put({'X-Avernet-Principal': principal})
        self.assertEqual(status, 200)
        self.assertEqual(set(payload), {'code', 'message', 'request_id', 'data'})
        self.assertEqual(payload['data']['added'], 1)
        status, payload = put({'X-Avernet-Principal': principal, 'X-Mock-User-Id': OWNER_HUMAN})
        self.assertEqual(status, 401)
        self.assertEqual(payload['data']['error_code'], 'mock_headers_forbidden_on_business_paths')
        status, _ = put({'X-Avernet-Principal': mint_principal(OWNER_HUMAN, key='wrong-key')})
        self.assertEqual(status, 401)
        status, _ = put({'X-Avernet-Principal': mint_principal(OWNER_HUMAN, expired=True)})
        self.assertEqual(status, 401)
        status, _ = put({'X-Avernet-Principal': mint_principal(NEVER_HUMAN)})
        self.assertEqual(status, 401)
        status, _ = put({})
        self.assertEqual(status, 401)

    def test_manager_and_transfer_polarities_keep_the_stable_error_codes(self):
        opener = urllib.request.build_opener()

        def request(method, path, subject=None):
            wire = primitive_open(self.fence_server.base_url + path, method=method)
            if subject is not None:
                wire.add_header('X-Avernet-Principal', mint_principal(subject))
            try:
                with opener.open(wire) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        owner_bot, _ = self.bootstrap()
        managers = INTERNAL + '/bots/' + owner_bot + '/managers/'
        status, payload = request('PUT', managers + OWNER_HUMAN, OWNER_HUMAN)
        self.assertEqual(status, 409)
        self.assertEqual(payload['data']['error_code'], 'owner_not_manager')
        status, payload = request('PUT', managers + NEVER_HUMAN, OWNER_HUMAN)
        self.assertEqual(status, 400)
        self.assertEqual(payload['data']['error_code'], 'bot_authority_human_unknown')
        transfer = INTERNAL + '/bots/' + owner_bot + '/ownership-transfers/human_' + TARGET_HUMAN
        status, payload = request('PUT', transfer, OWNER_HUMAN)
        self.assertEqual(status, 201)
        self.assertEqual(payload['data']['status'], 'pending')
        self.assertEqual(payload['data']['target_owner_user_id'], TARGET_HUMAN)
        status, payload = request('PUT', transfer, OWNER_HUMAN)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload['data']['expires_at'], 0)
        status, _ = request('PUT', transfer, TARGET_HUMAN)
        self.assertEqual(status, 403)

    def test_main_replays_the_journey_and_publishes_only_the_marker(self):
        evidence_path = self.base / 'authority-evidence.json'
        argv = ['--base-url', self.fence_server.base_url, '--owner-human', OWNER_HUMAN,
                '--target-user-id', TARGET_HUMAN, '--mine-user-id', MINE_HUMAN,
                '--evidence-file', str(evidence_path), '--cli-binary', str(self.cli_stub),
                '--principal-signing-key-env', contract.PRINCIPAL_SIGNING_KEY_ENV,
                '--run-tag', 'wp2-main-fence']
        with redirect_stdout(io.StringIO()) as stdout:
            code = main(argv)
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(),
                         'Bot authority journey: 42 live HTTP assertions passed\n')
        evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
        self.assertEqual(set(evidence['obligations']), set(contract.REQUIREMENTS))
        written = evidence_path.read_text(encoding='utf-8')
        for canary in ('Bearer ', 'X-Avernet-Principal', SIGNING_KEY, BOT_TOKEN):
            self.assertNotIn(canary, written)
        with self.assertRaisesRegex(StoryFailure, 'evidence file must be fresh'):
            main(argv)

    def test_main_fails_closed_on_count_mismatch_and_redaction_canaries(self):
        marker_payload = {
            'schema_version': 1, 'case_id': 'LOCAL-AUTHORITY-JOURNEY-02',
            'identity_mode': 'register',
            'owner': {'human_id': OWNER_HUMAN, 'bot_id': 'bot_fenceowner1'},
            'target': {'user_id': TARGET_HUMAN, 'bot_id': 'bot_fencetarge'},
            'mine': {'user_id': MINE_HUMAN}, 'never_user_id': NEVER_HUMAN,
            'cli_readiness_gate': {'exit_code': 0, 'health_status': 'healthy',
                                   'transport': 'product-cli', 'role': 'readiness-gate-only',
                                   'authority_business_verbs': 'none-frozen'},
            'registration_plane': {'mock_admitted': True, 'bots_registered': 2,
                                   'distinct_bot_ids': True},
            'business_envelope': {'enforced': True,
                                  'observed_keys': ['code', 'message', 'request_id', 'data']},
            'http_call_count': 24, 'bootstrap_http_call_count': 4,
            'business_http_call_count': 20,
            'transfer': {'initiated_expires_at': 100, 'refreshed_expires_at': 101,
                         'delta_ms': 1, 'renewed': True},
            'resource_disposition': 'AUTHORITY_TWO_BOTS_CREATED_AND_RETAINED',
            'obligations': {}}

        class SimpleStory:
            checks = 42
            secrets: list[str] = []

            def __init__(self, *constructor_args, **constructor_kwargs):
                pass

            def run(self):
                return json.loads(json.dumps(marker_payload))

        class UnevenStory:
            checks = 41
            secrets: list[str] = []

            def __init__(self, *constructor_args, **constructor_kwargs):
                pass

            def run(self):
                return json.loads(json.dumps(marker_payload))

        class CanaryStory:
            checks = 42
            secrets: list[str] = []

            def __init__(self, *constructor_args, **constructor_kwargs):
                pass

            def run(self):
                payload = json.loads(json.dumps(marker_payload))
                payload['business_envelope'] = {'enforced': True,
                                                'observed_keys': ['code', 'message',
                                                                  'request_id', 'data'],
                                                'note': 'Bearer leak canary'}
                return payload

        for staged, phrase in ((SimpleStory, None), (UnevenStory, 'assertion count mismatch'),
                               (CanaryStory, 'redaction gate')):
            with self.subTest(story=staged.__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    evidence_path = Path(temporary) / 'evidence.json'
                    argv = ['--base-url', 'http://127.0.0.1:1', '--owner-human', OWNER_HUMAN,
                            '--target-user-id', TARGET_HUMAN, '--mine-user-id', MINE_HUMAN,
                            '--evidence-file', str(evidence_path), '--cli-binary',
                            '/unused/product-cli', '--principal-signing-key-env',
                            contract.PRINCIPAL_SIGNING_KEY_ENV, '--run-tag', 'wp2-main-fence']
                    with patch.object(story_module, 'Story', staged):
                        if phrase is None:
                            with redirect_stdout(io.StringIO()) as stdout:
                                code = main(argv)
                            self.assertEqual(code, 0)
                            self.assertEqual(stdout.getvalue(),
                                             'Bot authority journey: 42 live HTTP assertions passed\n')
                            published = json.loads(evidence_path.read_text(encoding='utf-8'))
                            self.assertEqual(published['obligations'], {})
                        else:
                            with self.assertRaisesRegex(StoryFailure, phrase):
                                main(argv)
                            self.assertFalse(evidence_path.exists())

    def test_upstream_source_and_story_module_share_one_frozen_42_check_shape(self):
        self.assertTrue(contract.STORY_SOURCE.is_file())
        upstream = contract.STORY_SOURCE.read_text(encoding='utf-8')
        self.assertTrue(upstream.startswith('#!/bin/bash'))
        child_source = Path(story_module.__file__).read_text(encoding='utf-8')
        self.assertIn('EXPECTED_CHECKS = 42', child_source)
        self.assertEqual(EXPECTED_CHECKS, contract.STORY_CHECKS)
        self.assertEqual(len(contract.REQUIREMENTS), 42)


if __name__ == '__main__':
    unittest.main()
