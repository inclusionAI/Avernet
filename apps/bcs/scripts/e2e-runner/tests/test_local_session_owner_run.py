"""Offline behavior guards for the owned-local session owner executor.

The executor is an in-process port of the upstream frozen HTTP story, so these
guards replace the upstream live deployment with a strict dual-face FakeBCS
(station legacy mock headers plus public openapi/v1 gateway Principals), drive
the full 60-assertion run plus the 4 cleanup dispositions through the real
executor, and mirror the service-policy wrapper lifecycle guards for the
owned-local publication chain. No transport is ever opened and no credential
beyond obvious fixture placeholders exists anywhere in this file.
"""
import base64
import copy
import hashlib
import hmac
import json
import re
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.inventory import current_manifest
from bcs_e2e.local_story_commands import CASES, LOCAL_STORY_COMMANDS
from bcs_e2e.migration_inventory import migration_plan
from bcs_e2e.profile import fingerprint
from bcs_e2e.run_state import RunStateError, read, write
from bcs_e2e.session_owner.local_session_owner_reporting import (COMPLETION, OUTPUTS,
                                                                  SOURCES, build_session_owner_report)
from bcs_e2e.session_owner.local_session_owner_run import (BINDING_NAME, BINARY_KIND, CHECK_IDS,
                                                            CLEANUP_OBLIGATION, CREDENTIAL_PERSISTENCE_KEYS,
                                                            EXECUTOR_ADMISSION, JOURNAL_NAME, LEDGER_NAME,
                                                            PRINCIPAL_ENV, REQUIRED_ASSERTION_COUNT,
                                                            REQUIRED_ASSERTION_INVENTORY, RESOURCE_DISPOSITION_PASS,
                                                            REQUIRED_ASSERTION_INVENTORY, OwnedLocalSessionOwner,
                                                            SessionOwnerRunError, local_session_owner_run,
                                                            validate_session_owner_binary)
from bcs_e2e.session_owner.session_owner_contract import (CASE_ID, CLEANUP_CHECKS, DOCUMENTED_GAPS,
                                                           ENVIRONMENT, PARENT, REQUIREMENTS, STATUS,
                                                           STORY_CHECKS, SUITE, session_owner_source_binding)

RUN_MODULE = 'bcs_e2e.session_owner.local_session_owner_run'
REPORTING_MODULE = 'bcs_e2e.session_owner.local_session_owner_reporting'
PUBLIC_PREFIX = '/openapi/v1/collaboration'

RUN_ID = 'session-owner-test'
EXECUTOR_DIGEST = hashlib.sha256(b'session-owner-fixture-executor').hexdigest()
BINARY_SHA = hashlib.sha256(b'session-owner-fixture-binary').hexdigest()
SIGNING_KEY = 'session-owner-fixture-signing-key'
ORIGIN = 'http://127.0.0.1:39999'
CONFIG_AUTH = {'chain': ['local', 'session'], 'require_authentication': False,
               'allow_mock_headers': True}

OBLIGATION_HTTP = (10, 3, 4, 9, 7, 14, 6, 7)
CLEANUP_HTTP = 4


def executable_binary(directory: Path) -> tuple[Path, str]:
    binary = directory / 'bcs'
    binary.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    binary.chmod(0o755)
    return binary, hashlib.sha256(binary.read_bytes()).hexdigest()


def write_runtime_config(root: Path, *, auth=None, principal_env=PRINCIPAL_ENV) -> None:
    (Path(root) / 'bcs-config.json').write_text(json.dumps(
        {'auth': dict(CONFIG_AUTH) if auth is None else auth,
         'gateway_principal': {'signing_key_env': principal_env}}, sort_keys=True), encoding='utf-8')


def make_binding(root: Path, *, run_id=RUN_ID, digest=EXECUTOR_DIGEST, binary_sha=BINARY_SHA) -> dict:
    binding = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
               'run_id': run_id, 'executor_digest': digest, 'binary_kind': BINARY_KIND,
               'bcs_binary_sha256': binary_sha, 'auto_build': False,
               'source_binding': session_owner_source_binding(),
               'selected_requirement_ids': list(REQUIREMENTS),
               'legacy_inventory': current_manifest()['legacy_inventory'],
               'runtime_dir': str(Path(root).resolve()), 'environment': ENVIRONMENT,
               'executor_admission': dict(EXECUTOR_ADMISSION)}
    write(Path(root) / BINDING_NAME, binding)
    return binding


class FakeRuntime:
    """Duck-typed owned-local runtime admitting only the strict matrix."""

    def __init__(self, root, *, origin=ORIGIN, signing_key=SIGNING_KEY, binary_sha=BINARY_SHA,
                 run_id=RUN_ID, environment=None):
        self.allow_ambient_local_identity = False
        self.root = Path(root)
        self.run_id = run_id
        self.binary = Path(root) / 'owned-bcs'
        self.binary_sha256 = binary_sha
        if environment is None:
            environment = {PRINCIPAL_ENV: signing_key}
        self.inputs = SimpleNamespace(origin=origin, environment=environment)


def fresh_state(temporary, name='owner-state') -> tuple[Path, dict, FakeRuntime]:
    root = Path(temporary) / name
    root.mkdir(mode=0o700)
    write_runtime_config(root)
    binding = make_binding(root)
    return root, binding, FakeRuntime(root)


class FakeBCS:
    """Strict in-memory dual-face BCS for the frozen session owner story.

    Station face: per-request X-Mock-User-Id admission, ensure-human, one-time
    register tokens, owner-scoped Bot visibility/delete. Public face: real
    HS256 gateway Principal verification, creator/manager plus active
    participant relay authorization, the full participants PATCH matrix,
    actor-scoped listing/history/collect, and revocation isolation.
    """

    def __init__(self, *, signing_key=SIGNING_KEY, fail_at=None, fail_status=500,
                 fail_code=50000, fail_message='Unexpected upstream failure',
                 fail_cleanup_first=False):
        self.signing_key = signing_key
        self.fail_at = fail_at
        self.fail_status = fail_status
        self.fail_code = fail_code
        self.fail_message = fail_message
        self.fail_cleanup_first = fail_cleanup_first
        self.humans: list[str] = []
        self.register_tokens: dict[str, str] = {}
        self.used_tokens: set[str] = set()
        self.bots: dict[str, dict] = {}
        self.bot_tokens: dict[str, str] = {}
        self.bot_serial = 0
        self.groups: dict[str, dict] = {}
        self.group_serial = 0
        self.sessions: dict[str, dict] = {}
        self.session_serial = 0
        self.dispatched = 0
        self.secrets: list[str] = [signing_key]

    # --- dispatch seam ------------------------------------------------------

    def dispatch(self, opener, method: str, url: str, headers: dict, payload):
        self.dispatched += 1
        split = urlsplit(url)
        path = split.path
        query = {key: values[0] for key, values in parse_qs(split.query).items()}
        public = path.startswith(PUBLIC_PREFIX)
        if self.fail_at is not None and self.dispatched == self.fail_at:
            return self._failure(method, path, public)
        if self.fail_cleanup_first and self.dispatched == STORY_CHECKS + CLEANUP_HTTP - 3:
            return self._failure(method, path, True)
        if public:
            actor = self._principal_actor(headers.get('X-Avernet-Principal'))
            if actor is None:
                return self._failure(method, path, True, status=401, code=40100, message='unauthorized')
            body = json.loads(payload.decode('utf-8')) if payload else {}
            return self._public(method, path[len(PUBLIC_PREFIX):], query, actor, body, headers)
        actor = headers.get('X-Mock-User-Id')
        if not actor:
            return self._failure(method, path, False, status=401, code=40100, message='unauthorized')
        body = json.loads(payload.decode('utf-8')) if payload else {}
        return self._station(method, path, query, actor, body, headers)

    def _failure(self, method, path, public, status=None, code=None, message=None):
        if public:
            return self._envelope((status or self.fail_status) * 100, message or self.fail_message,
                                  {'error_code': 'unexpected_failure'} if code is None else code)
        return status or self.fail_status, json.dumps(
            {'error_code': 'unexpected_failure', 'message': message or self.fail_message}).encode()

    # --- authentication -----------------------------------------------------

    def _principal_actor(self, token):
        try:
            raw_header, raw_claims, raw_signature = token.split('.')

            def decode(segment: str) -> bytes:
                return base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))

            header = json.loads(decode(raw_header))
            claims = json.loads(decode(raw_claims))
            if header.get('alg') != 'HS256' or header.get('kid') != 'bare':
                return None
            expected = hmac.new(self.signing_key.encode(),
                                f'{raw_header}.{raw_claims}'.encode('ascii'), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, decode(raw_signature)):
                return None
            if claims.get('iss') != 'gateway' or claims.get('aud') != 'bcs':
                return None
            if claims.get('exp', 0) - claims.get('iat', 0) != 60:
                return None
            principals = claims.get('principals')
            if not isinstance(principals, list) or len(principals) != 1:
                return None
            principal = principals[0]
            if principal.get('type') != 'user' or principal.get('tenant') != 'bcs-e2e':
                return None
            subject = principal.get('subject') or {}
            if subject.get('id') != subject.get('username'):
                return None
            if principal.get('tenant') != subject.get('tenant_id'):
                return None
            return subject.get('id')
        except Exception:
            return None

    # --- response helpers ---------------------------------------------------

    def _envelope(self, code: int, message: str, data) -> tuple[int, bytes]:
        return code // 100, json.dumps({'code': code, 'message': message, 'data': data,
                                        'request_id': f'ez-req-{self.dispatched}'},
                                       separators=(',', ':')).encode()

    def _public_error(self, status: int, error_code: str) -> tuple[int, bytes]:
        message = {400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
                   404: 'Not Found'}.get(status, 'Error')
        return self._envelope(status * 100, message, {'error_code': error_code})

    def _station_ok(self, data) -> tuple[int, bytes]:
        return 200, json.dumps(data, separators=(',', ':')).encode()

    def _station_error(self, status: int, error_code: str) -> tuple[int, bytes]:
        return status, json.dumps({'error_code': error_code, 'message': error_code}).encode()

    # --- ownership helpers --------------------------------------------------

    def _bot_owner(self, bot_uuid) -> str | None:
        bot = self.bots.get(bot_uuid)
        return None if bot is None else bot['owner']

    def _owns_active_bot(self, actor: str, session: dict) -> bool:
        for row in session['participants']:
            if row.get('active') and row['kind'] == 'bot' and self._bot_owner(row['actor_id']) == actor:
                return True
        return False

    def _relay_active(self, actor: str, session: dict) -> bool:
        if actor == session['creator']:
            return True
        for row in session['participants']:
            if not row.get('active', True):
                continue
            if row['kind'] == 'human' and row['actor_id'] == 'human_' + actor:
                return True
            if row['kind'] == 'bot' and self._bot_owner(row['actor_id']) == actor:
                return True
        return False

    def _group_readable(self, actor: str, group: dict) -> bool:
        if actor == group['creator']:
            return True
        for session in self.sessions.values():
            if session['group_id'] == group['group_id'] and not session['deleted'] \
                    and self._relay_active(actor, session):
                return True
        return False

    # --- station legacy face ------------------------------------------------

    def _station(self, method, path, query, actor, body, headers):
        ensured = actor in self.humans
        if method == 'POST' and path == '/me/ensure-human':
            if not ensured:
                self.humans.append(actor)
            return self._station_ok({'human_id': 'human_' + actor})
        if method == 'GET' and path == '/register/token':
            token = 'ez-register-token-' + actor
            self.register_tokens[actor] = token
            self.secrets.append(token)
            return self._station_ok({'token': token})
        if method == 'POST' and path == '/register':
            token = query.get('token', '')
            name = query.get('bot-name', '')
            if (not ensured or self.register_tokens.get(actor) != token or token in self.used_tokens
                    or not name or len(name) > 64):
                return self._station_error(403, 'forbidden')
            self.used_tokens.add(token)
            self.bot_serial += 1
            bot_uuid = f'ez-bot-{self.bot_serial}'
            bot_token = f'ez-bot-token-{actor}-{self.bot_serial}'
            self.bots[bot_uuid] = {'bot_uuid': bot_uuid, 'owner': actor, 'name': name,
                                   'visibility': 'private', 'deleted': False}
            self.bot_tokens[bot_token] = bot_uuid
            self.secrets.append(bot_token)
            return self._station_ok({'bot_uuid': bot_uuid, 'bot_token': bot_token, 'owner': actor})
        if method == 'POST' and path == '/bots/onboard':
            bot = self.bots.get(self.bot_tokens.get(headers.get('X-BCS-Bot-Token'), ''))
            if (not ensured or bot is None or bot['owner'] != actor or bot['deleted']
                    or body.get('name') != bot['name']):
                return self._station_error(403, 'forbidden')
            return self._station_ok({'bot_uuid': bot['bot_uuid'], 'onboarded': True})
        if method == 'PUT' and path.startswith('/bots/') and path.endswith('/visibility'):
            bot = self.bots.get(path.split('/')[2])
            if bot is None or bot['owner'] != actor or bot['deleted']:
                return self._station_error(403, 'forbidden')
            bot['visibility'] = body.get('visibility', bot['visibility'])
            return self._station_ok({'bot_uuid': bot['bot_uuid'], 'visibility': bot['visibility']})
        if method == 'DELETE' and path.startswith('/bots/'):
            bot = self.bots.get(path.split('/')[2])
            if bot is None or bot['owner'] != actor or bot['deleted']:
                return self._station_error(403, 'forbidden')
            bot['deleted'] = True
            return self._station_ok({'bot_uuid': bot['bot_uuid'], 'deleted': True})
        return self._station_error(404, 'unknown_route')

    # --- public openapi/v1 face --------------------------------------------

    def _public(self, method, route, query, actor, body, headers):
        parts = route.strip('/').split('/')
        if parts[0] == 'groups' and len(parts) == 1:
            if method != 'POST':
                return self._public_error(404, 'unknown_route')
            driver = self.bots.get(body.get('driver_bot_uuid', ''))
            if (actor not in self.humans or driver is None or driver['deleted']
                    or driver['owner'] != actor):
                return self._public_error(403, 'forbidden')
            self.group_serial += 1
            group_id = f'ez-group-{self.group_serial}'
            self.groups[group_id] = {'group_id': group_id, 'creator': actor,
                                     'visibility': body.get('group_kind') and 'private',
                                     'deleted': False,
                                     'participants': [
                                         {'actor_id': driver['bot_uuid'], 'kind': 'bot', 'role': 'driver'},
                                         {'actor_id': 'human_' + actor, 'kind': 'human', 'role': 'manager'}]}
            return self._envelope(20100, 'Created', {'group_id': group_id})
        if parts[0] == 'groups' and len(parts) == 3 and parts[2] == 'sessions':
            group = self.groups.get(parts[1])
            if group is None or group['deleted']:
                return self._public_error(404, 'not_found')
            if method == 'POST':
                if actor != group['creator']:
                    return self._public_error(403, 'forbidden')
                self.session_serial += 1
                session_id = f'ez-session-{self.session_serial}'
                self.sessions[session_id] = {
                    'session_id': session_id, 'group_id': group['group_id'], 'creator': actor,
                    'title': body.get('title', ''), 'deleted': False,
                    'participants': [
                        {'actor_id': group['participants'][0]['actor_id'], 'kind': 'bot',
                         'role': 'driver', 'mode': 'auto', 'message_view_scope': 'full', 'active': True},
                        {'actor_id': 'human_' + actor, 'kind': 'human', 'role': 'manager',
                         'mode': 'present', 'message_view_scope': 'full', 'active': True}]}
                return self._envelope(20100, 'Created', {'session_id': session_id})
            if method == 'GET':
                view_bot = self.bots.get(query.get('view_bot_id', ''))
                if view_bot is None or view_bot['deleted'] or view_bot['owner'] != actor:
                    return self._public_error(403, 'forbidden')
                listed = [{'session_id': session['session_id'], 'view_bot_id': view_bot['bot_uuid']}
                          for session in self.sessions.values()
                          if session['group_id'] == group['group_id'] and not session['deleted']
                          and any(row['actor_id'] == view_bot['bot_uuid'] and row.get('active', True)
                                  for row in session['participants'])]
                return self._envelope(20000, 'OK', {'sessions': listed})
            return self._public_error(404, 'unknown_route')
        if parts[0] == 'groups' and len(parts) == 2:
            group = self.groups.get(parts[1])
            if group is None or group['deleted']:
                return self._public_error(404, 'not_found')
            if method == 'GET':
                if not self._group_readable(actor, group):
                    return self._public_error(403, 'forbidden')
                return self._envelope(20000, 'OK', {'group_id': group['group_id'],
                                                    'visibility': group['visibility'],
                                                    'participants': [dict(row) for row in group['participants']]})
            if method == 'PATCH':
                if actor != group['creator']:
                    return self._public_error(403, 'forbidden')
                for key in ('visibility', 'name'):
                    if key in body:
                        group[key] = body[key]
                return self._envelope(20000, 'OK', {'group_id': group['group_id']})
            if method == 'DELETE':
                if actor != group['creator']:
                    return self._public_error(403, 'forbidden')
                group['deleted'] = True
                return self._envelope(20000, 'OK', {'group_id': group['group_id'], 'deleted': True})
            return self._public_error(404, 'unknown_route')
        if parts[0] == 'sessions' and len(parts) >= 2:
            session = self.sessions.get(parts[1])
            if session is None or session['deleted']:
                return self._public_error(404, 'not_found')
            if len(parts) == 2:
                if method == 'GET':
                    if not (actor == session['creator'] or self._relay_active(actor, session)):
                        return self._public_error(403, 'forbidden')
                    return self._envelope(20000, 'OK', {'session_id': session['session_id'],
                                                        'title': session['title'],
                                                        'participants': [dict(row) for row in session['participants']]})
                if method == 'PATCH':
                    if actor != session['creator']:
                        return self._public_error(403, 'forbidden')
                    if 'title' in body:
                        session['title'] = body['title']
                    return self._envelope(20000, 'OK', {'session_id': session['session_id']})
                if method == 'DELETE':
                    if actor != session['creator']:
                        return self._public_error(403, 'forbidden')
                    session['deleted'] = True
                    return self._envelope(20000, 'OK', {'session_id': session['session_id'], 'deleted': True})
                return self._public_error(404, 'unknown_route')
            if len(parts) == 3 and parts[2] == 'participants' and method == 'POST':
                if actor != session['creator']:
                    return self._public_error(403, 'forbidden')
                bot = self.bots.get(body.get('bot_uuid', ''))
                if bot is None or bot['deleted']:
                    return self._public_error(404, 'not_found')
                if not any(row['actor_id'] == bot['bot_uuid'] for row in session['participants']):
                    session['participants'].append({'actor_id': bot['bot_uuid'], 'kind': 'bot',
                                                    'role': 'participant', 'mode': 'auto',
                                                    'message_view_scope': 'full', 'active': True})
                return self._envelope(20000, 'OK', {'actor_id': bot['bot_uuid'], 'mode': 'auto',
                                                    'message_view_scope': 'full'})
            if len(parts) == 3 and parts[2] == 'messages' and method == 'GET':
                if 'limit' not in query:
                    return self._public_error(404, 'unknown_route')
                if not (actor == session['creator'] or self._relay_active(actor, session)):
                    return self._public_error(403, 'forbidden')
                return self._envelope(20000, 'OK', {'messages': []})
            if len(parts) == 3 and parts[2] == 'collect':
                if not (actor == session['creator'] or self._relay_active(actor, session)):
                    return self._public_error(403, 'forbidden')
                return self._envelope(20000, 'OK', {'collected': True})
            if len(parts) == 4 and parts[2] == 'participants':
                return self._participant(method, actor, session, parts[3], body)
        return self._public_error(404, 'unknown_route')

    def _participant(self, method, actor, session, target, body):
        row = next((item for item in session['participants'] if item['actor_id'] == target), None)
        if method == 'DELETE':
            if actor != session['creator']:
                return self._public_error(403, 'forbidden')
            if row is None:
                return self._public_error(404, 'participant_not_found')
            row['active'] = False
            return self._envelope(20000, 'OK', {'actor_id': target, 'active': False})
        if method != 'PATCH':
            return self._public_error(404, 'unknown_route')
        creator = actor == session['creator']
        if row is None or not row.get('active', True):
            # Human self-join is ownership-bound: the joining Human must already
            # hold an active Bot relation in this Session.
            if (row is None and target == 'human_' + actor and body.get('mode') == 'present'
                    and self._owns_active_bot(actor, session)):
                joined = {'actor_id': target, 'kind': 'human', 'role': 'participant',
                          'mode': 'present', 'message_view_scope': 'full', 'active': True}
                session['participants'].append(joined)
                return self._envelope(20000, 'OK', dict(joined))
            if creator:
                return self._public_error(404, 'participant_not_found')
            return self._public_error(403, 'forbidden')
        kind = row['kind']
        mode = body.get('mode')
        scope = body.get('message_view_scope')
        if mode is not None:
            allowed = ('auto', 'muted') if kind == 'bot' else ('present', 'absent')
            if mode not in allowed:
                return self._public_error(400, 'invalid_participant_mode')
        if scope is not None:
            allowed = ('full',) if kind == 'bot' else ('full', 'participant')
            if scope not in allowed:
                return self._public_error(400, 'invalid_message_view_scope')
        mode_authorized = (mode is None) or (
            kind == 'bot' and (creator or self._bot_owner(row['actor_id']) == actor)
            or (kind == 'human' and row['actor_id'] == 'human_' + actor))
        scope_authorized = (scope is None) or creator or (
            kind == 'human' and row['actor_id'] == 'human_' + actor)
        if mode is not None and not mode_authorized:
            return self._public_error(403, 'forbidden')
        if scope is not None and not scope_authorized:
            return self._public_error(403, 'message_view_scope_forbidden')
        if mode is not None:
            row['mode'] = mode
        if scope is not None:
            row['message_view_scope'] = scope
        return self._envelope(20000, 'OK', dict(row))


def obligation_http_counts() -> dict:
    return {name: count for name, count in zip(REQUIREMENTS, OBLIGATION_HTTP)}


def session_owner_journal(**overrides) -> dict:
    operations = []
    index = 0
    for obligation, count in zip(REQUIREMENTS, OBLIGATION_HTTP):
        for _step in range(count):
            index += 1
            operations.append({'operation': obligation, 'index': index, 'method': 'GET',
                               'route': PUBLIC_PREFIX + '/fixture', 'public': True,
                               'status': 'ACKNOWLEDGED', 'kind': 'CHECK', 'http_status': 200,
                               'transitions': [{'status': 'INTENDED'},
                                               {'status': 'ACKNOWLEDGED', 'http_status': 200}]})
    for _step in range(CLEANUP_HTTP):
        index += 1
        operations.append({'operation': CLEANUP_OBLIGATION, 'index': index, 'method': 'DELETE',
                           'route': PUBLIC_PREFIX + '/fixture', 'public': True,
                           'status': 'ACKNOWLEDGED', 'kind': 'MUTATION', 'http_status': 200,
                           'transitions': [{'status': 'INTENDED'},
                                           {'status': 'ACKNOWLEDGED', 'http_status': 200}]})
    resources = [
        {'kind': 'human', 'owner': 'manager', 'id': 'sessionmanager-fixture', 'state': 'created'},
        {'kind': 'human', 'owner': 'owner', 'id': 'sessionowner-fixture', 'state': 'created'},
        {'kind': 'human', 'owner': 'outsider', 'id': 'sessionoutsider-fixture', 'state': 'created'},
        {'kind': 'bot', 'owner': 'manager', 'id': 'ez-bot-driver', 'state': 'deleted'},
        {'kind': 'bot', 'owner': 'owner', 'id': 'ez-bot-owned', 'state': 'deleted'},
        {'kind': 'group', 'owner': 'manager', 'id': 'ez-group-1', 'state': 'deleted'},
        {'kind': 'session', 'owner': 'manager', 'id': 'ez-session-1', 'state': 'deleted'}]
    journal = {
        'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID, 'parent': PARENT,
        'run_id': RUN_ID, 'status': STATUS, 'executor_digest': EXECUTOR_DIGEST,
        'binding_digest': '0' * 64, 'source_binding_digest': '0' * 64,
        'bcs_binary_sha256': BINARY_SHA, 'binary_kind': BINARY_KIND, 'auto_build': False,
        'ownership': {'humans': {'manager': 'sessionmanager-fixture', 'owner': 'sessionowner-fixture',
                                 'outsider': 'sessionoutsider-fixture'},
                      'human_actors': {'owner': 'human_sessionowner-fixture',
                                       'outsider': 'human_sessionoutsider-fixture'},
                      'bots': [{'bot_uuid': 'ez-bot-driver', 'owner': 'manager', 'role': 'driver'},
                               {'bot_uuid': 'ez-bot-owned', 'owner': 'owner', 'role': 'owned'}],
                      'group': 'ez-group-1', 'session': 'ez-session-1'},
        'requirements': [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS],
        'operations': operations, 'checks': [{'id': name, 'status': 'PASS', 'evidence': {}}
                                             for name in CHECK_IDS],
        'resources': [dict(row) for row in resources],
        'story_markers': {'observed': True, 'http_checks': STORY_CHECKS},
        'story_http_checks': STORY_CHECKS, 'cleanup_http_checks': CLEANUP_CHECKS,
        'observed_assertion_count': STORY_CHECKS + CLEANUP_CHECKS,
        'required_assertion_count': REQUIRED_ASSERTION_COUNT,
        'required_assertion_inventory': dict(REQUIRED_ASSERTION_INVENTORY),
        'cleanup': {'status': 'PASS', 'http_checks': CLEANUP_CHECKS},
        'resource_disposition': RESOURCE_DISPOSITION_PASS,
        'credential_persistence': dict.fromkeys(CREDENTIAL_PERSISTENCE_KEYS, False),
        'execution_scope': {'runtime': 'OWNED_LOCAL_FOREGROUND',
                            'strict_mock_header_admission': True,
                            'ambient_local_identity': False, 'remote_capabilities': 'NONE'},
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False}
    journal.update(overrides)
    return journal


def run_with_fake_bcs(root, fake, runtime=None):
    with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
            patch(RUN_MODULE + '._dispatch_http', side_effect=fake.dispatch):
        return OwnedLocalSessionOwner(runtime or FakeRuntime(root)).run()


def construct_executor(root, runtime=None):
    with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST):
        return OwnedLocalSessionOwner(runtime or FakeRuntime(root))


def build_owner_report(state):
    with patch(REPORTING_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST):
        return build_session_owner_report(Path(state))


class SessionOwnerModuleGuardTests(unittest.TestCase):
    def test_frozen_counts_and_inventory_are_self_consistent(self):
        self.assertEqual(STORY_CHECKS, 60)
        self.assertEqual(CLEANUP_CHECKS, 4)
        self.assertEqual(len(CHECK_IDS), 13)
        self.assertEqual(len(REQUIREMENTS), 9)
        self.assertEqual(REQUIRED_ASSERTION_COUNT, STORY_CHECKS + CLEANUP_CHECKS + len(CHECK_IDS) + 1)
        self.assertEqual(REQUIRED_ASSERTION_INVENTORY, {'run_http': 60, 'cleanup_http': 4,
                                                        'structural_python': 13, 'shell': 1,
                                                        'total': 78})

    def test_executor_source_guards(self):
        source = (ROOT / (RUN_MODULE.replace('.', '/') + '.py')).resolve()
        text = source.read_text(encoding='utf-8')
        for forbidden in ('requests.', '/api/v1/', 'shell=True', 'subprocess'):
            self.assertNotIn(forbidden, text, forbidden)
        for required in ('Session owner story marker drift', "JOURNAL_NAME = 'session-owner-journal.json'",
                         'X-Mock-User-Id', "PUBLIC_PREFIX = '/openapi/v1/collaboration'"):
            self.assertIn(required, text, required)

    def test_offline_modules_never_open_a_transport(self):
        for name in ('remote_session_owner_adapter', 'session_owner_case_run',
                     'local_session_owner_reporting'):
            text = (ROOT / 'bcs_e2e' / 'session_owner' / (name + '.py')).read_text(encoding='utf-8')
            for forbidden in ('urlopen', 'build_opener', 'requests.', 'HTTPConnection'):
                self.assertNotIn(forbidden, text, name + ':' + forbidden)


class SessionOwnerAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root, self.binding, self.runtime = fresh_state(temporary.name)

    def _rejects(self, runtime):
        with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                self.assertRaises(SessionOwnerRunError):
            OwnedLocalSessionOwner(runtime)

    def test_missing_or_bare_runtimes_are_rejected(self):
        with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                self.assertRaises(SessionOwnerRunError):
            OwnedLocalSessionOwner(None)
        self._rejects(object())

    def test_ambient_identity_and_non_loopback_origins_are_rejected(self):
        self._rejects(SimpleNamespace(allow_ambient_local_identity=True, root=self.root,
                                      run_id=RUN_ID, binary=self.root / 'bcs', binary_sha256=BINARY_SHA,
                                      inputs=SimpleNamespace(origin=ORIGIN,
                                                             environment={PRINCIPAL_ENV: SIGNING_KEY})))
        for bad in ('https://127.0.0.1:39999', 'http://localhost:39999', 'http://127.0.0.1:80',
                    'http://127.0.0.1:70000', 'http://127.0.0.1:39999/x',
                    'http://127.0.0.1:39999?x=1', 'http://user:pass@127.0.0.1:39999',
                    'http://[::1]:39999', 'not-a-url'):
            runtime = FakeRuntime(self.root, origin=bad)
            self._rejects(runtime)

    def test_principal_signing_key_admission_is_strict(self):
        self._rejects(FakeRuntime(self.root, environment={}))
        self._rejects(FakeRuntime(self.root, environment={PRINCIPAL_ENV: ''}))
        self._rejects(FakeRuntime(self.root, environment={PRINCIPAL_ENV: 'bad key\nvalue'}))
        self._rejects(FakeRuntime(self.root, environment={PRINCIPAL_ENV: None}))
        self._rejects(FakeRuntime(self.root, environment='not-a-mapping'))

    def test_runtime_attribute_and_identity_contracts_are_rejected(self):
        self._rejects(SimpleNamespace(allow_ambient_local_identity=False, root=self.root,
                                      run_id='bad run id', binary=self.root / 'bcs',
                                      binary_sha256=BINARY_SHA,
                                      inputs=SimpleNamespace(origin=ORIGIN,
                                                             environment={PRINCIPAL_ENV: SIGNING_KEY})))
        self._rejects(SimpleNamespace(allow_ambient_local_identity=False, root=self.root,
                                      run_id=RUN_ID, binary=self.root / 'bcs',
                                      inputs=SimpleNamespace(origin=ORIGIN,
                                                             environment={PRINCIPAL_ENV: SIGNING_KEY})))
        self._rejects(FakeRuntime(self.root, binary_sha='not-a-hex-digest'))
        self._rejects(FakeRuntime(self.root, binary_sha='Z' * 64))

    def test_runtime_configuration_admission_matrix_is_rejected(self):
        variants = (
            {'auth': dict(CONFIG_AUTH, mock_user_id='borrowed'),
             'gateway_principal': {'signing_key_env': PRINCIPAL_ENV}},
            {'auth': dict(CONFIG_AUTH, allow_mock_headers=False),
             'gateway_principal': {'signing_key_env': PRINCIPAL_ENV}},
            {'auth': {'chain': ['session'], 'require_authentication': False,
                      'allow_mock_headers': True},
             'gateway_principal': {'signing_key_env': PRINCIPAL_ENV}},
            {'gateway_principal': {'signing_key_env': PRINCIPAL_ENV}},
            {'auth': dict(CONFIG_AUTH), 'gateway_principal': {'signing_key_env': 'OTHER_ENV'}},
            {'auth': dict(CONFIG_AUTH)},
        )
        for variant in variants:
            with self.subTest(variant=sorted(variant)):
                (self.root / 'bcs-config.json').write_text(json.dumps(variant, sort_keys=True),
                                                           encoding='utf-8')
                self._rejects(FakeRuntime(self.root))

    def test_frozen_binding_file_is_required_and_untampered(self):
        executor = construct_executor(self.root)
        self.assertEqual(executor.executor_digest, EXECUTOR_DIGEST)
        self.assertEqual(executor.binding['case_id'], CASE_ID)
        (self.root / BINDING_NAME).unlink()
        with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                self.assertRaises(SessionOwnerRunError):
            OwnedLocalSessionOwner(FakeRuntime(self.root))

    def test_tampered_binding_refuses_execution(self):
        with tempfile.TemporaryDirectory() as other:
            root = Path(other) / 'owner-state'
            root.mkdir(mode=0o700)
            write_runtime_config(root)
            make_binding(root)
            tampered = make_binding(root)
            tampered['case_id'] = 'LOCAL-OTHER-99'
            write(root / BINDING_NAME, tampered)
            with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                    self.assertRaises(SessionOwnerRunError):
                OwnedLocalSessionOwner(FakeRuntime(root))


class SessionOwnerExecutorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root, self.binding, self.runtime = fresh_state(temporary.name)
        self.fake = FakeBCS()

    def test_green_run_executes_the_frozen_ownership_matrix(self):
        journal = run_with_fake_bcs(self.root, self.fake)
        self.assertEqual(journal['status'], STATUS)
        self.assertEqual(journal['case_id'], CASE_ID)
        self.assertEqual(journal['parent'], PARENT)
        self.assertEqual(journal['run_id'], RUN_ID)
        self.assertEqual(journal['story_http_checks'], STORY_CHECKS)
        self.assertEqual(journal['cleanup_http_checks'], CLEANUP_CHECKS)
        self.assertEqual(journal['observed_assertion_count'], STORY_CHECKS + CLEANUP_CHECKS)
        self.assertEqual(journal['required_assertion_count'], REQUIRED_ASSERTION_COUNT)
        self.assertEqual(journal['required_assertion_inventory'],
                         {'run_http': 60, 'cleanup_http': 4, 'structural_python': 13, 'shell': 1,
                          'total': 78})
        self.assertEqual(journal['story_markers'], {'observed': True, 'http_checks': 60})
        self.assertEqual(journal['resource_disposition'], RESOURCE_DISPOSITION_PASS)
        self.assertEqual(journal['cleanup'], {'status': 'PASS', 'http_checks': CLEANUP_CHECKS})
        self.assertEqual(journal['credential_persistence'],
                         {key: False for key in CREDENTIAL_PERSISTENCE_KEYS})
        self.assertEqual(journal['execution_scope'],
                         {'runtime': 'OWNED_LOCAL_FOREGROUND', 'strict_mock_header_admission': True,
                          'ambient_local_identity': False, 'remote_capabilities': 'NONE'})
        self.assertEqual(journal['remote_validation'], 'NOT_RUN')
        self.assertEqual(journal['grants_release_credit'], False)
        self.assertEqual([row['id'] for row in journal['requirements']], list(REQUIREMENTS))
        self.assertTrue(all(row['status'] == 'PASS' for row in journal['requirements']))
        self.assertEqual([row['id'] for row in journal['checks']], list(CHECK_IDS))
        self.assertTrue(all(row['status'] == 'PASS' for row in journal['checks']))
        operations = journal['operations']
        self.assertEqual(len(operations), STORY_CHECKS + CLEANUP_CHECKS)
        self.assertTrue(all(row['status'] == 'ACKNOWLEDGED' for row in operations))
        self.assertEqual(journal['ownership']['humans'].keys(), {'manager', 'owner', 'outsider'})
        self.assertEqual({re.sub(r'[0-9a-f]{32}$', '', value)
                          for value in journal['ownership']['humans'].values()},
                         {'sessionmanager', 'sessionowner', 'sessionoutsider'})
        self.assertEqual(journal['ownership']['human_actors']['owner'],
                         'human_' + journal['ownership']['humans']['owner'])
        self.assertEqual(len(journal['ownership']['bots']), 2)
        self.assertEqual({row['role'] for row in journal['ownership']['bots']}, {'driver', 'owned'})
        self.assertEqual({row['owner'] for row in journal['ownership']['bots']}, {'manager', 'owner'})
        self.assertTrue(journal['ownership']['group'].startswith('ez-group-'))
        self.assertTrue(journal['ownership']['session'].startswith('ez-session-'))
        # Exact per-obligation HTTP accounting in upstream order.
        counts = {}
        for row in operations:
            counts[row['operation']] = counts.get(row['operation'], 0) + 1
        expected = obligation_http_counts()
        expected[CLEANUP_OBLIGATION] = CLEANUP_HTTP
        self.assertEqual(counts, expected)
        # Both faces are exercised: 12 station mock-header dispatches, 52 public.
        self.assertEqual(sum(1 for row in operations if not row['public']), 12)
        self.assertEqual(sum(1 for row in operations if row['public']), 52)
        statuses = [row['http_status'] for row in operations]
        self.assertEqual(statuses.count(200), 40)
        self.assertEqual(statuses.count(201), 2)
        self.assertEqual(statuses.count(400), 3)
        self.assertEqual(statuses.count(403), 18)
        self.assertEqual(statuses.count(404), 1)
        routes = {(row['method'], row['route']) for row in operations}
        self.assertIn(('POST', PUBLIC_PREFIX + '/groups'), routes)
        self.assertIn(('POST', '/me/ensure-human'), routes)  # upstream register() uses POST
        self.assertIn(('POST', '/register'), routes)
        owned = journal['ownership']['bots'][1]['bot_uuid']
        self.assertIn(('DELETE', '/bots/' + owned), routes)
        group = journal['ownership']['group']
        self.assertIn(('PATCH', PUBLIC_PREFIX + '/groups/' + group), routes)
        # The session-only owned Bot never leaks into the Group projection.
        hide = next(row for row in journal['checks']
                    if row['id'] == 'group-participants-hide-session-bot')
        self.assertTrue(hide['evidence']['session_only_bot_hidden'])
        # Durable evidence: journal plus resource ledger with owned dispositions.
        on_disk = read(self.root / JOURNAL_NAME)
        self.assertEqual(on_disk['status'], STATUS)
        self.assertEqual(on_disk, journal)
        ledger = read(self.root / LEDGER_NAME)
        self.assertEqual(ledger['schema_version'], 1)
        self.assertEqual(ledger['humans_bootstrap_retained'], True)
        rows = ledger['resources']
        self.assertEqual({row['kind'] for row in rows}, {'human', 'bot', 'group', 'session'})
        self.assertEqual(sum(1 for row in rows if row['kind'] == 'human'), 3)
        self.assertEqual(sum(1 for row in rows if row['kind'] == 'bot'), 2)
        self.assertTrue(all(row['state'] == 'created' for row in rows if row['kind'] == 'human'))
        self.assertTrue(all(row['state'] == 'deleted' for row in rows
                            if row['kind'] in ('bot', 'group', 'session')))
        # Durable evidence never carries a credential.
        durable = (self.root / JOURNAL_NAME).read_bytes() + (self.root / LEDGER_NAME).read_bytes()
        for secret in self.fake.secrets:
            self.assertNotIn(secret.encode(), durable, secret[:12])
        self.assertNotIn(b'X-Avernet-Principal', durable)
        # The frozen binding survives the run untouched.
        self.assertEqual(read(self.root / BINDING_NAME), self.binding)

    def test_first_failure_short_circuits_and_cleanup_still_disposes(self):
        fake = FakeBCS(fail_at=20)
        with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                patch(RUN_MODULE + '._dispatch_http', side_effect=fake.dispatch), \
                self.assertRaises(SessionOwnerRunError) as caught:
            OwnedLocalSessionOwner(FakeRuntime(self.root)).run()
        message = str(caught.exception)
        self.assertIn('PATCH', message)
        self.assertIn('expected 200, got 500', message)
        self.assertIn('/sessions/', message)
        self.assertLessEqual(len(message), 800)
        self.assertNotIn('?', message)
        journal = read(self.root / JOURNAL_NAME)
        self.assertEqual(journal['status'], 'BLOCKED')
        self.assertEqual(journal['error_type'], 'SessionOwnerRunError')
        self.assertEqual(journal['error_detail'], message)
        operations = journal['operations']
        self.assertEqual(len(operations), 24)
        self.assertEqual(sum(1 for row in operations if row['status'] == 'ACKNOWLEDGED'), 23)
        failed = operations[19]
        self.assertEqual(failed['status'], 'FAILED')
        self.assertEqual(failed['error_type'], 'SessionOwnerRunError')
        self.assertEqual(failed['operation'], REQUIREMENTS[3])
        self.assertEqual(failed['route'].split('?')[0], failed['route'])
        self.assertEqual(journal['requirements'][0]['status'], 'PASS')
        self.assertEqual(journal['requirements'][1]['status'], 'PASS')
        self.assertEqual(journal['requirements'][2]['status'], 'PASS')
        self.assertTrue(all(row['status'] == 'NOT_RUN' for row in journal['requirements'][3:8]))
        self.assertEqual(journal['requirements'][8]['status'], 'PASS')
        self.assertEqual(journal['observed_assertion_count'], 23)
        self.assertEqual(journal['cleanup'], {'status': 'PASS', 'http_checks': CLEANUP_CHECKS})
        self.assertEqual(journal['resource_disposition'], RESOURCE_DISPOSITION_PASS)
        ledger = read(self.root / LEDGER_NAME)
        rows = {row['kind']: row['state'] for row in ledger['resources']}
        self.assertEqual(rows['human'], 'created')
        self.assertEqual(rows['bot'], 'deleted')
        self.assertEqual(rows['group'], 'deleted')
        self.assertEqual(rows['session'], 'deleted')

    def test_cleanup_failure_blocks_without_claiming_disposition(self):
        fake = FakeBCS(fail_cleanup_first=True)
        with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                patch(RUN_MODULE + '._dispatch_http', side_effect=fake.dispatch), \
                self.assertRaises(SessionOwnerRunError) as caught:
            OwnedLocalSessionOwner(FakeRuntime(self.root)).run()
        self.assertIn('Story cleanup failed', str(caught.exception))
        journal = read(self.root / JOURNAL_NAME)
        self.assertEqual(journal['status'], 'BLOCKED')
        self.assertEqual(journal['error_type'], 'SessionOwnerRunError')
        self.assertIn('Story cleanup failed', journal['error_detail'])
        self.assertLessEqual(len(journal['error_detail']), 800)
        self.assertTrue(all(row['status'] == 'PASS' for row in journal['requirements'][:8]))
        self.assertEqual(journal['requirements'][8]['status'], 'NOT_RUN')
        self.assertEqual(journal['cleanup']['status'], 'FAIL')
        self.assertEqual(journal['cleanup']['http_checks'], 3)
        self.assertEqual(journal['resource_disposition'], 'UNVERIFIED')
        ledger = read(self.root / LEDGER_NAME)
        states = {(row['kind'], row['id']): row['state'] for row in ledger['resources']}
        session_states = [state for (kind, _), state in states.items() if kind == 'session']
        self.assertEqual(session_states, ['created'])

    def test_story_marker_drift_is_refused_before_status_claims_pass(self):
        fake = FakeBCS()
        with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                patch(RUN_MODULE + '._dispatch_http', side_effect=fake.dispatch), \
                patch(RUN_MODULE + '.STORY_CHECKS', 61), \
                self.assertRaises(SessionOwnerRunError) as caught:
            OwnedLocalSessionOwner(FakeRuntime(self.root)).run()
        self.assertIn('marker drift', str(caught.exception))
        journal = read(self.root / JOURNAL_NAME)
        self.assertEqual(journal['status'], 'BLOCKED')
        self.assertFalse(journal['story_markers']['observed'])
        self.assertEqual(journal['story_http_checks'], 0)

    def test_binding_drift_during_the_run_invalidates_authority(self):
        executor = construct_executor(self.root)
        tampered = copy.deepcopy(self.binding)
        tampered['case_id'] = 'LOCAL-OTHER-99'
        write(self.root / BINDING_NAME, tampered)
        fake = FakeBCS()
        with patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                patch(RUN_MODULE + '._dispatch_http', side_effect=fake.dispatch), \
                self.assertRaises(SessionOwnerRunError):
            executor.run()
        journal = read(self.root / JOURNAL_NAME)
        self.assertEqual(journal['status'], 'BLOCKED')
        self.assertIn('changed', journal['error_detail'])

    def test_validate_session_owner_binary_freezes_the_frozen_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'bcs'
            binary.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
            binary.chmod(0o755)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            self.assertEqual(validate_session_owner_binary(binary, digest), binary)
            with self.assertRaises(RunStateError):
                validate_session_owner_binary(binary, '0' * 64)
            with self.assertRaises(RunStateError):
                validate_session_owner_binary(Path(temporary) / 'missing', digest)
            plain = Path(temporary) / 'plain'
            plain.write_text('not executable', encoding='utf-8')
            with self.assertRaises(RunStateError):
                validate_session_owner_binary(plain, hashlib.sha256(b'x').hexdigest())
            linked = Path(temporary) / 'linked'
            linked.symlink_to(binary)
            with self.assertRaises(RunStateError):
                validate_session_owner_binary(linked, digest)


class FakeOwnedRuntime:
    instances: list = []
    enter_error: Exception | None = None
    final_status = 'STOPPED'
    final_returncode = 0

    def __init__(self, *, binary, binary_sha256, root, run_id, allow_ambient_local_identity=True):
        self.init_kwargs = {'binary': Path(binary), 'binary_sha256': binary_sha256,
                            'root': Path(root), 'run_id': run_id,
                            'allow_ambient_local_identity': allow_ambient_local_identity}
        FakeOwnedRuntime.instances.append(self)
        self.root = Path(root).resolve()
        self.inputs = SimpleNamespace(origin=ORIGIN, environment={PRINCIPAL_ENV: SIGNING_KEY})
        if FakeOwnedRuntime.enter_error is not None:
            raise FakeOwnedRuntime.enter_error

    def _persist(self, status: str) -> None:
        ready = status == 'STOPPED'
        (self.root / 'owned-bcs.json').write_text(json.dumps(
            {'schema_version': 1, 'run_id': self.init_kwargs['run_id'], 'status': status,
             'origin': ORIGIN, 'pid': None, 'returncode': FakeOwnedRuntime.final_returncode,
             'binary_sha256': self.init_kwargs['binary_sha256'], 'ready': ready,
             'forced_stop': False}, sort_keys=True, indent=2), encoding='utf-8')

    def __enter__(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        write_runtime_config(self.root)
        self._persist('STARTING')
        return self

    def __exit__(self, *_args):
        self._persist(FakeOwnedRuntime.final_status)
        return False


class FakeExecutor:
    journal = None
    run_error = None
    instances: list = []

    def __init__(self, runtime):
        self.runtime = runtime
        FakeExecutor.instances.append(self)

    def run(self):
        if FakeExecutor.run_error is not None:
            raise FakeExecutor.run_error
        journal = copy.deepcopy(FakeExecutor.journal)
        write(self.runtime.root / JOURNAL_NAME, journal)
        write(self.runtime.root / LEDGER_NAME,
              {'schema_version': 1, 'run_id': journal['run_id'], 'contract': SUITE,
               'case_id': CASE_ID, 'humans_bootstrap_retained': True,
               'resources': [dict(row) for row in journal['resources']]})
        return journal


def wrapper_kwargs(state: Path, binary: Path, digest: str, **changes) -> dict:
    base = dict(binary=binary, binary_sha256=digest, state_dir=state, run_id=RUN_ID,
                confirm_run_id=RUN_ID, allow_local_processes=True, allow_fixture_write=True,
                allow_write=True)
    base.update(changes)
    return base


def owned_wrapper_patches(binary_digest: str):
    return [patch(RUN_MODULE + '.OwnedBcsProcess', FakeOwnedRuntime),
            patch(RUN_MODULE + '.OwnedLocalSessionOwner', FakeExecutor),
            patch(RUN_MODULE + '.file_sha256', return_value=binary_digest),
            patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST),
            patch(REPORTING_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST)]


class SessionOwnerWrapperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeOwnedRuntime.instances = []
        FakeExecutor.instances = []
        FakeOwnedRuntime.enter_error = None
        FakeOwnedRuntime.final_status = 'STOPPED'
        FakeOwnedRuntime.final_returncode = 0
        FakeExecutor.journal = session_owner_journal()
        FakeExecutor.run_error = None

    def _run(self, state, **changes):
        kwargs = wrapper_kwargs(state, self.binary, self.digest)
        kwargs.update(changes)
        patches = owned_wrapper_patches(self.digest)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            return local_session_owner_run(**kwargs)

    def test_success_run_publishes_the_owned_local_evidence_chain(self):
        state = self.root / 'state'
        result = self._run(state)
        self.assertEqual(result['status'], STATUS)
        self.assertEqual(result['case_id'], CASE_ID)
        self.assertEqual(result['parent'], PARENT)
        self.assertEqual(result['executor_digest'], EXECUTOR_DIGEST)
        self.assertEqual(result['binary_kind'], BINARY_KIND)
        self.assertEqual(result['bcs_binary_sha256'], self.digest)
        self.assertEqual(result['auto_build'], False)
        self.assertEqual(result['environment'], ENVIRONMENT)
        self.assertEqual(result['documented_gaps'], DOCUMENTED_GAPS)
        self.assertEqual(result['remote_validation'], 'NOT_RUN')
        self.assertEqual(result['full_release_status'], 'NOT_QUALIFIED')
        self.assertEqual(result['ready_to_execute'], False)
        self.assertEqual(result['observed_assertion_count'], 64)
        self.assertEqual(result['required_assertion_count'], REQUIRED_ASSERTION_COUNT)
        runtime = FakeOwnedRuntime.instances[0]
        self.assertEqual(runtime.init_kwargs['allow_ambient_local_identity'], False)
        self.assertEqual(runtime.init_kwargs['binary_sha256'], self.digest)
        self.assertEqual(runtime.init_kwargs['run_id'], RUN_ID)
        self.assertEqual(runtime.init_kwargs['root'], state)
        binding = read(state / BINDING_NAME)
        self.assertEqual(binding['contract'], SUITE)
        self.assertEqual(binding['case_id'], CASE_ID)
        self.assertEqual(binding['executor_digest'], EXECUTOR_DIGEST)
        self.assertEqual(binding['auto_build'], False)
        self.assertEqual(binding['environment'], ENVIRONMENT)
        self.assertEqual(binding['executor_admission'], EXECUTOR_ADMISSION)
        self.assertEqual(binding['selected_requirement_ids'], list(REQUIREMENTS))
        self.assertEqual(binding['source_binding']['parent'], PARENT)
        self.assertEqual(binding['source_binding']['independent_slice'], True)
        self.assertEqual(binding['source_binding']['release_parent_denominator'], False)
        self.assertEqual(binding['legacy_inventory'], current_manifest()['legacy_inventory'])
        self.assertEqual(binding['runtime_dir'], str(state.resolve()))
        for name in SOURCES:
            self.assertTrue((state / name).is_file(), name)
        for relative in OUTPUTS:
            self.assertTrue((state / relative).is_file(), relative)
        case = read(state / 'session-owner.json')
        self.assertEqual(case['status'], STATUS)
        self.assertEqual(case['run_id'], RUN_ID)
        self.assertEqual([row['id'] for row in case['requirements']], list(REQUIREMENTS))
        self.assertTrue(all(row['status'] == 'PASS' for row in case['requirements']))
        self.assertEqual(case['story_markers'], {'observed': True, 'http_checks': STORY_CHECKS})
        self.assertEqual(case['story_http_checks'], STORY_CHECKS)
        self.assertEqual(case['cleanup_http_checks'], CLEANUP_CHECKS)
        self.assertEqual(case['observed_assertion_count'], 64)
        self.assertEqual(case['required_assertion_count'], REQUIRED_ASSERTION_COUNT)
        self.assertEqual(case['resource_disposition'], RESOURCE_DISPOSITION_PASS)
        self.assertEqual(case['remote_validation'], 'NOT_RUN')
        self.assertEqual(case['credential_persistence'],
                         {key: False for key in CREDENTIAL_PERSISTENCE_KEYS})
        self.assertEqual(case['documented_gaps'], DOCUMENTED_GAPS)
        self.assertEqual(case['case_id'], CASE_ID)
        journal = read(state / JOURNAL_NAME)
        self.assertEqual(journal['status'], STATUS)
        resources = read(state / LEDGER_NAME)
        self.assertEqual(len([row for row in resources['resources'] if row['kind'] == 'human']), 3)
        owned = read(state / 'owned-bcs.json')
        self.assertEqual(owned['status'], 'STOPPED')
        self.assertEqual(owned['returncode'], 0)
        completion = read(state / 'session-owner-completion.json')
        self.assertEqual(completion['contract'], COMPLETION)
        self.assertEqual(completion['binding_digest'], fingerprint(binding))
        summary = result['canonical_summary']
        self.assertEqual(summary, build_owner_report(state))
        self.assertEqual(summary['status'], 'LOCAL_SUITE_PASS')
        self.assertEqual(summary['scope']['suite'], SUITE)
        self.assertEqual(summary['scope']['selected_cases'], [CASE_ID])
        self.assertEqual(summary['scope']['independent_slice'], True)
        self.assertEqual(summary['scope']['release_parent_denominator'], False)
        self.assertEqual(summary['scope']['environment'], ENVIRONMENT)
        self.assertEqual(summary['scope']['legacy_inventory_count'],
                         len(current_manifest()['legacy_inventory']))
        self.assertEqual(summary['reports']['expected_assertion_count'], 9)
        self.assertEqual(summary['reports']['observed_assertion_count'], 9)
        self.assertEqual(summary['integrity']['status'], 'PASS')
        self.assertEqual(summary['integrity']['issues'], [])
        self.assertEqual(summary['cases'][0]['id'], CASE_ID)
        self.assertEqual(summary['cases'][0]['status'], 'PASS')
        self.assertTrue(all(row['status'] == 'PASS' for row in summary['requirements']))
        not_proven = ' '.join(summary['verification_boundary']['not_proven'])
        for gap in DOCUMENTED_GAPS:
            self.assertIn(gap, not_proven, gap)
        self.assertFalse(summary['ready_to_execute'])
        self.assertFalse((state / 'session-owner-publication-pending.json').exists())

    def test_blocked_journal_blocks_the_case_receipt_and_report(self):
        state = self.root / 'state-blocked'
        FakeExecutor.journal = session_owner_journal(status='BLOCKED', observed_assertion_count=0)
        result = self._run(state)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse((state / 'session-owner.json').exists())
        self.assertTrue((state / 'session-owner-flow-report.json').is_file())
        self.assertTrue((state / 'session-owner-completion.json').is_file())
        summary = build_owner_report(state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertEqual(summary, result['canonical_summary'])

    def test_cancellation_canonicalises_without_pass_claims(self):
        state = self.root / 'state-cancel'
        FakeExecutor.run_error = KeyboardInterrupt()
        try:
            result = self._run(state)
        finally:
            FakeExecutor.run_error = None
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(result['error_type'], 'KeyboardInterrupt')
        self.assertFalse((state / 'session-owner.json').exists())
        summary = build_owner_report(state)
        self.assertEqual(summary['status'], 'CANCELLED')
        self.assertEqual(summary, result['canonical_summary'])

    def test_runtime_bootstrap_failure_surfaces_without_evidence_directory(self):
        state = self.root / 'state-broken'
        FakeOwnedRuntime.enter_error = OSError('owned runtime bootstrap failed')
        try:
            with self.assertRaises(OSError):
                self._run(state)
        finally:
            FakeOwnedRuntime.enter_error = None
        self.assertFalse(state.exists())

    def test_runtime_shutdown_disposition_gates_the_pass(self):
        state = self.root / 'state-stop'
        FakeOwnedRuntime.final_status = 'FAILED_STOP'
        FakeOwnedRuntime.final_returncode = 1
        try:
            result = self._run(state)
        finally:
            FakeOwnedRuntime.final_status = 'STOPPED'
            FakeOwnedRuntime.final_returncode = 0
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(read(state / 'owned-bcs.json')['status'], 'FAILED_STOP')

    def test_executor_drift_fence_refuses_the_pass(self):
        state = self.root / 'state-drift'
        seen = {'count': 0}

        def drifting():
            seen['count'] += 1
            return 'session-owner-drifted-digest' if seen['count'] == 2 else EXECUTOR_DIGEST

        with patch(RUN_MODULE + '.OwnedBcsProcess', FakeOwnedRuntime), \
                patch(RUN_MODULE + '.OwnedLocalSessionOwner', FakeExecutor), \
                patch(RUN_MODULE + '.file_sha256', return_value=self.digest), \
                patch(RUN_MODULE + '.executor_digest', side_effect=drifting), \
                patch(REPORTING_MODULE + '.executor_digest', side_effect=drifting):
            result = local_session_owner_run(**wrapper_kwargs(state, self.binary, self.digest))
        self.assertEqual(result['status'], 'BLOCKED')
        binding = read(state / BINDING_NAME)
        self.assertEqual(binding['executor_digest'], EXECUTOR_DIGEST)
        self.assertTrue((state / 'session-owner-flow-report.json').is_file())
        self.assertFalse((state / 'session-owner.json').exists())

    def test_permissions_fresh_state_exact_confirmation_and_parent_fences(self):
        state = self.root / 'state-fences'
        for changes in ({'allow_local_processes': False}, {'allow_fixture_write': False},
                        {'allow_write': False}, {'confirm_run_id': 'session-owner-other'}):
            with self.subTest(changes=sorted(changes)):
                with self.assertRaises(SessionOwnerRunError):
                    self._run(state, **changes)
        self._run(state)
        with self.assertRaises(SessionOwnerRunError):
            self._run(state)
        linked = self.root / 'linked-parent'
        linked.symlink_to(self.root)
        with self.assertRaises(SessionOwnerRunError):
            self._run(linked / 'state-symlink')

    def test_frozen_binary_fences_reject_replay_and_drift(self):
        state = self.root / 'state-binary'
        with self.assertRaises(RunStateError):
            self._run(state, binary=self.root / 'missing-binary')
        linked = self.root / 'linked-binary'
        linked.symlink_to(self.binary)
        with self.assertRaises(RunStateError):
            self._run(self.root / 'state-link', binary=linked)
        plain = self.root / 'plain-binary'
        plain.write_text('not executable', encoding='utf-8')
        with self.assertRaises(RunStateError):
            self._run(self.root / 'state-plain', binary=plain)
        with patch(RUN_MODULE + '.OwnedBcsProcess', FakeOwnedRuntime), \
                patch(RUN_MODULE + '.OwnedLocalSessionOwner', FakeExecutor), \
                patch(RUN_MODULE + '.file_sha256', return_value='f' * 64), \
                patch(RUN_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST), \
                patch(REPORTING_MODULE + '.executor_digest', return_value=EXECUTOR_DIGEST):
            with self.assertRaises(RunStateError):
                local_session_owner_run(**wrapper_kwargs(self.root / 'state-digest',
                                                         self.binary, self.digest))


class SessionOwnerReportingTamperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary, self.digest = executable_binary(self.root)
        FakeOwnedRuntime.instances = []
        FakeExecutor.instances = []
        FakeOwnedRuntime.enter_error = None
        FakeOwnedRuntime.final_status = 'STOPPED'
        FakeOwnedRuntime.final_returncode = 0
        FakeExecutor.journal = session_owner_journal()
        FakeExecutor.run_error = None
        state = self.root / 'state'
        patches = owned_wrapper_patches(self.digest)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            self.result = local_session_owner_run(
                **wrapper_kwargs(state, self.binary, self.digest))
        self.state = state
        self.assertEqual(self.result['status'], STATUS)

    def test_tampered_case_receipt_cannot_claim_success(self):
        case = read(self.state / 'session-owner.json')
        tampered = copy.deepcopy(case)
        tampered['requirements'].pop()
        (self.state / 'session-owner.json').write_text(json.dumps(tampered, sort_keys=True),
                                                       encoding='utf-8')
        summary = build_owner_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_tampered_runtime_disposition_blocks_the_projection(self):
        owned = read(self.state / 'owned-bcs.json')
        tampered = copy.deepcopy(owned)
        tampered['status'] = 'FAILED_STOP'
        (self.state / 'owned-bcs.json').write_text(json.dumps(tampered, sort_keys=True),
                                                   encoding='utf-8')
        summary = build_owner_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_missing_journal_is_blocked_not_invented(self):
        (self.state / JOURNAL_NAME).unlink()
        summary = build_owner_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])

    def test_publication_gap_and_symlinked_root_are_blocked(self):
        pending = self.state / 'session-owner-publication-pending.json'
        pending.write_text('{}', encoding='utf-8')
        summary = build_owner_report(self.state)
        self.assertEqual(summary['status'], 'BLOCKED')
        self.assertTrue(summary['integrity']['issues'])
        pending.unlink()
        linked = self.root / 'linked-state'
        linked.symlink_to(self.state)
        with self.assertRaises(RunStateError):
            build_owner_report(linked)


class SessionOwnerIndependentSliceFenceTests(unittest.TestCase):
    def test_promoted_slice_uses_dual_commands_without_release_credit(self):
        self.assertNotIn(CASE_ID, {case.case_id for case in CASES})
        self.assertIn('local-session-owner-run', LOCAL_STORY_COMMANDS)
        self.assertIn('local-session-owner-report', LOCAL_STORY_COMMANDS)
        self.assertIn('dual-session-owner-run', CASE_COMMANDS)
        plan = migration_plan()
        registered = {row['case_id'] for row in plan['local_registered_executors']}
        independent = {row['case_id'] for row in plan['local_independent_slices']}
        self.assertNotIn(CASE_ID, registered)
        self.assertNotIn(CASE_ID, independent)
        row = next(item for item in plan['local_backlog_case_slices']
                   if item['case_id'] == CASE_ID)
        self.assertEqual(row['status'], 'LOCAL_EXECUTOR_ONLY_BACKLOG')
        self.assertEqual(row['canonical_reporting'], 'DUAL_CASE_RECEIPT')
        self.assertEqual(row['execution_command'], 'dual-session-owner-run')
        self.assertEqual(row['report_command'], '')
        self.assertEqual(row['required_parameters'], ['profile', 'binary', 'binary_sha256',
                                                        'cli_binary', 'cli_binary_sha256',
                                                        'state_dir', 'run_id',
                                                        'confirm_run_id', 'allow_write'])
        self.assertEqual(row['source_map'], 'session-owner-permissions-migration-contract.json')
        self.assertFalse(row['release_parent_denominator'])
        self.assertTrue(row['independent_slice'])
        self.assertFalse(row['grants_release_credit'])
        self.assertEqual(row['remote_validation'], 'NOT_RUN')


if __name__ == '__main__':
    unittest.main()
