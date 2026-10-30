"""Finite 32-operation owned-local Provider/Organization journey.

Only the caller's live fresh workspace fixtures and exact negative Provider sink
are admitted. Intents are durable before wire submission; uncertain outcomes are
never retried, adopted, or cleaned up. The two original DELETEs are business
steps, not a recovery mechanism. Provider/organization/admin history is retained.
"""
from __future__ import annotations

import copy
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
from urllib.parse import quote, urlsplit

from .inventory import binding_matches_reviewed_release, current_manifest, executor_digest
from .local_provider_contract import CASE_ID, SUITE, REQUIREMENTS, provider_source_binding
from .local_provider_sink import OwnedLocalProviderSink
from .local_provider_storage import OwnedLocalProviderStorage
from .local_role_identity import OwnedLocalRoleIdentity
from .owned_bcs import OwnedBcsProcess
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

ROLES = ('CEO', 'PM', 'ENG', 'QA')
_CREATE = {'register': 'provider', 'bot-publish': 'bot', 'org-create': 'organization',
           'member-add': 'member', 'admin-start': 'admin_run'}
_DELETE = {'member-remove': 'member', 'bot-retire': 'bot'}
_PARENTS = {'provider': None, 'bot': 'provider', 'organization': 'provider',
            'member': 'organization', 'admin_run': 'organization'}
_MAX_BYTES = 65_536


def _require(value, message='Provider finite contract was not confirmed'):
    if not value:
        raise RunStateError(message)


def _exclusive(path: Path, value: dict):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class OwnedLocalProviderApi:
    """One-shot original parent execution. Lifecycle and report publication stay outside."""

    def __init__(self, identities: OwnedLocalRoleIdentity, sink: OwnedLocalProviderSink):
        _require(type(identities) is OwnedLocalRoleIdentity
                 and identities.fixture_set == 'workspace' and identities.roles == ROLES
                 and set(identities._verified) == set(ROLES),
                 'Provider API requires freshly verified workspace CEO/PM/ENG/QA identities')
        _require(type(sink) is OwnedLocalProviderSink, 'Provider API requires the owned sink')
        self.identities, self.runtime, self.sink = identities, identities.runtime, sink
        _require(type(self.runtime) is OwnedBcsProcess and self.runtime.provider_sink is sink,
                 'Provider API cannot borrow another runtime or sink')
        self.root, self.run_id = self.runtime.root, identifier(self.runtime.run_id, 'Provider Run ID')
        self.origin = identities.origin
        parsed = urlsplit(self.origin)
        _require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                 and parsed.port and parsed.netloc == '127.0.0.1:' + str(parsed.port)
                 and not parsed.path and not parsed.query and not parsed.fragment,
                 'Provider API requires the exact owned loopback origin')
        self._port = parsed.port
        self._sink_capability = urlsplit(sink.url).path
        self.executor_digest = executor_digest()
        self.source_binding = provider_source_binding()
        self.source_binding_digest = fingerprint(self.source_binding)
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.binding_path = self.root / 'provider-binding.json'
        self.binding = read(self.binding_path)
        self.binding_digest = fingerprint(self.binding)
        self._credentials = {}
        self._ids = {'provider_bot_ref': self.run_id,
                     'provider_slug': 'e2e-provider-' + hashlib.sha256(self.run_id.encode()).hexdigest()[:24],
                     'organization_code': 'e2e-org-' + hashlib.sha256(self.run_id.encode()).hexdigest()[:24]}
        self.path, self.ledger_path = self.root/'local-provider-api.json', self.root/'local-provider-ledger.json'
        self._journal_digest = self._ledger_digest = ''
        self.state = self.ledger = None
        self._attempted = self._invalidated = False
        self._next = 0
        self._wire_claims = set()
        self._validate_binding()
        _require(sink.summary()['received'] == 0 and not sink.summary()['records']
                 and sink.summary()['failed'] is False, 'Provider sink has already been used')
        _require(not any(p.exists() or p.is_symlink() for p in (self.path, self.ledger_path)),
                 'Provider execution already attempted; never adopt existing journals')

    def _validate_binding(self):
        self.identities._require_roles(ROLES)
        _require(self.identities.runtime is self.runtime and self.runtime.provider_sink is self.sink
                 and self.runtime.root == self.root == self.identities.root
                 and self.runtime.run_id == self.run_id == self.identities.run_id
                 and self.runtime.inputs.root == self.root
                 and self.runtime.inputs.origin == self.origin == self.identities.origin
                 and self.runtime.inputs.human_id == self.identities.human
                 and self.runtime.store_messages is False)
        self.sink.validate_live(self.run_id)
        source = provider_source_binding()
        expected = {'schema_version': 1, 'contract': SUITE, 'suite': SUITE, 'case_id': CASE_ID,
                    'run_id': self.run_id, 'executor_digest': self.executor_digest,
                    'bcs_binary_sha256': self.runtime.binary_sha256,
                    'source_binding': source, 'selected_requirement_ids': list(REQUIREMENTS),
                    'legacy_inventory': current_manifest()['legacy_inventory'],
                    'runtime_dir': str(self.root.resolve()), 'store_messages': False,
                    'sink_url_sha256': hashlib.sha256(self.sink.url.encode()).hexdigest()}
        _require(executor_digest() == self.executor_digest and self.binding == expected
                 and read(self.binding_path) == expected
                 and binding_matches_reviewed_release(expected['legacy_inventory'])
                 and fingerprint(source) == self.source_binding_digest
                 and fingerprint(read(self.identities.fixtures.path)) == self.fixture_digest,
                 'Provider executor, source, binding, or fixture artifact changed')
        for path in (self.binding_path, self.identities.fixtures.path):
            info = path.lstat()
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                     'Provider admission artifacts must be unshared regular files')
        _require(self.binding_path.stat().st_mtime_ns <= self.identities.fixtures.path.stat().st_mtime_ns,
                 'Provider binding must be frozen before fixtures')
        config = json.loads(self.runtime.inputs.config_path.read_text(), object_pairs_hook=unique_keys)
        _require(config.get('store_messages') is False and config.get('allowed_switch_provider_ids') == []
                 and config.get('llm') == {'type': 'none'} and config.get('bcsfuse') == {'enabled': False}
                 and config.get('eventing') == {'enabled': False, 'dispatcher_enabled': False}
                 and config.get('security', {}).get('outbound_url') ==
                 {'block_private_networks': True, 'allow_loopback': True},
                 'Provider owned runtime configuration exceeds the negative local contract')

    def _integrity(self):
        for path, digest in ((self.path, self._journal_digest), (self.ledger_path, self._ledger_digest)):
            if digest:
                _require(fingerprint(read(path)) == digest, 'Provider journal changed externally')

    def _active(self):
        _require(not self._invalidated, 'Provider execution authority has been invalidated')
        self._validate_binding()
        self._integrity()

    def _no_secrets(self, value):
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False)
        sensitive = list(self._credentials.values()) + [v[1] for v in self.identities._entries.values()]
        # The opaque path is a listener capability, even without a token.
        sensitive.append(self._sink_capability)
        _require(not any(secret and secret in encoded for secret in sensitive),
                 'Provider evidence contains non-public data; publication refused')

    def _save(self):
        self._integrity()
        self.state['resources'] = copy.deepcopy(self.ledger['resources'])
        self._no_secrets(self.ledger)
        self._no_secrets(self.state)
        write(self.ledger_path, self.ledger)
        self._ledger_digest = fingerprint(self.ledger)
        write(self.path, self.state)
        self._journal_digest = fingerprint(self.state)

    def _safe_id(self, value):
        value = identifier(value, 'Provider response ID')
        self._no_secrets({'id': value})
        _require(value not in {entry[0] for entry in self.identities._entries.values()},
                 'Provider returned a borrowed fixture identity')
        return value

    def _capture(self, key, value):
        _require(isinstance(value, str) and 1 <= len(value) <= 4096 and value.isascii()
                 and all(32 < ord(c) < 127 for c in value), 'Provider credential is missing or invalid')
        _require(key not in self._credentials and value not in self._credentials.values()
                 and value not in [v[1] for v in self.identities._entries.values()],
                 'Provider credential is reused or borrowed')
        self._credentials[key] = value

    def _resource(self, handle):
        resource = self.ledger['resources'].get(handle, {})
        _require(resource.get('handle') == handle and resource.get('kind') == handle
                 and resource.get('case_id') == CASE_ID and resource.get('attempt') == 1
                 and resource.get('origin') == 'created' and resource.get('parent') == _PARENTS[handle]
                 and resource.get('state') in ('LIVE', 'RETAINED') and resource.get('resource_id'),
                 'Provider operation requires an exact created owned resource')
        expected = {'provider': 'provider_id', 'bot': 'provider_bot_uuid',
                    'organization': 'organization_code', 'member': 'provider_bot_uuid',
                    'admin_run': 'admin_run_id'}[handle]
        _require(resource['resource_id'] == self._ids.get(expected))
        if handle != 'provider':
            self._resource(_PARENTS[handle])
        return resource

    def _transition(self, resource, state, request_id, check=None):
        resource['state'] = state
        event = {'state': state, 'request_id': request_id}
        if check is not None:
            event['check'] = check
        resource['history'].append(event)

    def _spec(self, operation):
        _require(operation in REQUIREMENTS, 'unmapped Provider operation')
        suffix = operation.removeprefix('PROVIDER.')
        requirement = self.source_binding['requirements'][REQUIREMENTS.index(operation)]
        path = requirement['path']
        for key in re.findall(r'\$\{([^}]+)\}', path):
            _require(key in self._ids, 'Provider path has an unresolved owned identity')
            path = path.replace('${' + key + '}', quote(self._ids[key], safe=''))
        ref, org = self._ids['provider_bot_ref'], self._ids['organization_code']
        bot = self._ids.get('provider_bot_uuid')
        bodies = {
            'register': {'name': 'E2E Provider', 'slug': self._ids['provider_slug'],
                         'webhook_url': self.sink.url, 'auth': {'mode': 'static_bearer'},
                         'protocol_version': '2.0', 'coordination': {'mode': 'native_tool'}},
            'duplicate-slug-conflict': {'name': 'Duplicate E2E Provider', 'slug': self._ids['provider_slug'],
                                         'auth': {'mode': 'static_bearer'}},
            'update': {'name': 'E2E Provider Updated', 'protocol_version': '2.0'},
            'bot-publish': {'name': 'Provider Review Agent', 'summary': 'Reviews release plans',
                            'owners': [self.identities.human], 'provider_bot_ref': ref,
                            'domains': ['release'], 'skills': ['review'], 'scopes': ['local']},
            'bot-update': {'name': 'Provider Review Agent (Senior)', 'domains': ['release', 'audit'], 'scopes': []},
            'attributes-write-denied': {'user_visibility': 'public'},
            'org-create': {'organization_code': org, 'name': 'E2E release organization',
                           'description': 'provider-managed release team'},
            'org-update': {'name': 'E2E release organization updated'},
            'member-add': {'role': 'reviewer'}, 'member-profile': {'name': 'E2E organization bot'},
            'admin-start': {'target_bot_uuid': bot, 'message': {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Review the E2E release plan'}]}, 'detach': True},
            'agentpass-negative': {},
            'unknown-run-callback': {'run_id': 'missing-provider-run-' + hashlib.sha256(self.run_id.encode()).hexdigest()[:24],
                                     'state': 'final', 'message': {'text': 'late provider result'}},
            'takeover-denied': {'bot_id': self.identities._entries['QA'][0], 'provider_bot_ref': ref},
        }
        auth = ('human' if suffix in ('register', 'duplicate-slug-conflict') else 'bot_runtime' if suffix in
                ('agentpass-negative', 'unknown-run-callback') else 'provider_admin')
        status = (409 if suffix == 'duplicate-slug-conflict' else 403 if suffix.endswith('-denied')
                  else 404 if suffix == 'unknown-run-callback' else 204 if suffix == 'member-remove' else 200)
        return requirement['method'], path, bodies.get(suffix), auth, status

    def _wire(self, method, path, body, auth, request_id, provider_header=False):
        """One direct loopback HTTP exchange, no redirect/proxy/retry behavior."""
        self._active()
        _require(self.state is not None and request_id not in self._wire_claims,
                 'Provider wire dispatch is not a fresh durable intent')
        if path == '/auth/user':
            _require(not self.state['operations'] and method == 'GET' and body is None
                     and auth == 'human' and provider_header is False
                     and self.state['checks'][-1] == {'id': 'human-before', 'status': 'INTENDED',
                                                     'submitted_request_id': request_id})
        else:
            _require(bool(self.state['operations']), 'Provider business dispatch has no intent')
            row = self.state['operations'][-1]
            spec = self._spec(row['operation'])
            _require(row['status'] == 'INTENDED' and row['submitted_request_id'] == request_id
                     and row['provider_header'] is provider_header
                     and (method, path, auth) == (spec[0], spec[1], spec[3])
                     and json.dumps(body, sort_keys=True, allow_nan=False) ==
                         json.dumps(spec[2], sort_keys=True, allow_nan=False),
                     'Provider wire request differs from its exact admitted operation')
        self._wire_claims.add(request_id)
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'X-Request-ID': request_id}
        if auth != 'human':
            _require(auth in self._credentials, 'Provider principal is unavailable')
            headers['Authorization'] = 'Bearer ' + self._credentials[auth]
        if provider_header:
            headers['X-BCN-Provider-Id'] = self._ids['provider_id']
        payload = json.dumps(body, allow_nan=False).encode() if body is not None else None
        _require(payload is None or len(payload) <= _MAX_BYTES)
        connection = HTTPConnection('127.0.0.1', self._port, timeout=5)
        try:
            deadline = time.monotonic() + 5
            connection.request(method, path, body=payload, headers=headers)
            sock = connection.sock
            response = connection.getresponse()
            raw = bytearray()
            with response:
                while True:
                    remaining = deadline - time.monotonic()
                    _require(remaining > 0, 'Provider response deadline exceeded')
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(4096, _MAX_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    _require(len(raw) <= _MAX_BYTES, 'Provider response exceeds byte budget')
                _require(response.length in (None, 0), 'Provider response was truncated')
                status = response.status
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys) if raw else {}
            _require(isinstance(value, dict), 'Provider response must be an object')
            return status, value
        except Exception:
            raise RunStateError('Provider exchange failed; outcome unknown, no retry or cleanup') from None
        finally:
            connection.close()

    def _check(self, name, observe):
        self._active()
        _require(len(self.state['checks']) < 32 and name not in [c['id'] for c in self.state['checks']],
                 'Provider supplementary observation budget or uniqueness exceeded')
        value = observe()
        self._active()
        self._no_secrets(value)
        self.state['checks'].append({'id': name, 'status': 'OBSERVED', 'evidence': value})
        self._save()
        return value

    def _human(self):
        request_id = 'e2e-provider-check-' + uuid.uuid4().hex
        # This is supplementary admission, never a mapped business operation.
        self.state['checks'].append({'id': 'human-before', 'status': 'INTENDED',
                                     'submitted_request_id': request_id})
        self._save()
        status, value = self._wire('GET', '/auth/user', None, 'human', request_id)
        _require(status == 200 and value.get('user_id') == self.identities.human)
        self.state['checks'][-1].update(status='OBSERVED', evidence={'user_id': self.identities.human})
        self._save()

    def _observe_provider(self, name, expected_name, disabled):
        value = self._check(name, lambda: self.storage.provider(self._ids['provider_id']))
        p = value['provider']
        _require(p['provider_id'] == self._ids['provider_id'] and p['created_by'] == self.identities.human
                 and p['owners'] == [self.identities.human] and p['name'] == expected_name
                 and p['disabled'] == int(disabled) and p['auth_mode'] == 'static_bearer'
                 and p['protocol_version'] == '2.0' and p['downlink_enabled'] is True
                 and p['coordination_mode'] == 'native_tool' and p['admin_callback_configured'] is False
                 and p['sink_url_sha256'] == self.binding['sink_url_sha256']
                 and value['credentials'] == [{'credential_kind': 'downlink_bcs_to_provider', 'disabled': 0},
                                             {'credential_kind': 'provider_admin', 'disabled': 0}])
        return value

    def _observe_bot(self, name, deleted=False):
        value = self._check(name, lambda: self.storage.bot(self._ids['provider_id'],
            self._ids['provider_bot_uuid'], self.run_id, self._credentials['bot_runtime']))
        b, binding = value['bot'], value['binding']
        _require(b['bot_uuid'] == self._ids['provider_bot_uuid'] and b['created_by'] == self.identities.human
                 and b['actor_kind'] == 'bot' and b['is_deleted'] == int(deleted)
                 and binding['disabled'] == int(deleted)
                 and binding['provider_id'] == self._ids['provider_id']
                 and binding['provider_bot_ref'] == self.run_id)
        if not deleted:
            _require(b['credential_matches'] == 1)
        return value

    def _observe_org(self, name, member_state):
        value = self._check(name, lambda: self.storage.organization(self._ids['provider_id'],
                       self._ids['organization_code'], self._ids['provider_bot_uuid']))
        org, members = value['organization'], value['members']
        _require(org['code'] == self._ids['organization_code'] and org['disabled'] == 0
                 and org['managing_provider_id'] == self._ids['provider_id'])
        if member_state is None:
            _require(members == [])
        else:
            _require(members == [{'organization_code': org['code'], 'bot_uuid': self._ids['provider_bot_uuid'],
                                 'role': 'reviewer', 'disabled': member_state}])
        return value

    def _downstream(self):
        value = self.sink.summary()
        _require(value['state'] == 'LIVE' and value['failed'] is False and value['received'] == 1
                 and value['rejected'] == 1 and value['engine'] == 'NOT_STARTED'
                 and len(value['records']) == 1)
        row = value['records'][0]
        _require(row['status'] == 'REJECTED_NO_ENGINE' and row['http_status'] == 503
                 and row['owner_run_id'] == self.run_id and row['provider_id'] == self._ids['provider_id']
                 and row['bot_id'] == self._ids['provider_bot_uuid'] and row['provider_bot_ref'] == self.run_id
                 and row['provider_run_id'] == self._ids['admin_run_id'] and row['engine'] == 'NOT_STARTED'
                 and row['method'] == 'chat.send' and row['protocol_version'] == '2.0'
                 and isinstance(row['body_sha256'], str) and re.fullmatch('[0-9a-f]{64}', row['body_sha256']))
        self._no_secrets(value)
        self.state['downstream'] = value
        return value

    def _verify(self, suffix, value):
        """Validate every source assertion; return only a safe semantic projection."""
        p, b, org = (self._ids.get(k) for k in ('provider_id', 'provider_bot_uuid', 'organization_code'))
        def fields(expected):
            _require(all(k in value and type(value[k]) is type(v) and value[k] == v
                         for k, v in expected.items()))
            return expected
        def listed(key, id_key, expected):
            rows = value.get(key)
            _require(isinstance(rows, list) and len(rows) <= 50 and all(isinstance(r, dict) for r in rows))
            ids = [r.get(id_key) for r in rows]
            _require(ids == expected)
            return {key: [{id_key: item} for item in ids]}
        if suffix == 'register':
            self._capture('provider_admin', value.get('provider_admin_token'))
            self._capture('downlink', value.get('bcs_to_provider_token'))
            self._ids['provider_id'] = self._safe_id(value.get('provider_id'))
            return {'provider_id': self._ids['provider_id'], 'credential_kinds_returned': ['provider_admin', 'downlink']}
        if suffix == 'duplicate-slug-conflict':
            return {'conflict': True}
        if suffix in ('read', 'update', 'disable', 'enable'):
            expected = {'provider_id': p, 'disabled': suffix == 'disable'}
            if suffix == 'read':
                expected['auth_mode'] = 'static_bearer'
            if suffix == 'update':
                expected['name'] = 'E2E Provider Updated'
            _require(value.get('webhook_url') == self.sink.url and not value.get('admin_callback_url'))
            return fields(expected)
        if suffix == 'bot-publish':
            self._capture('bot_runtime', value.get('bot_runtime_token'))
            b = self._safe_id(value.get('bot_uuid'))
            _require(b != p)
            self._ids['provider_bot_uuid'] = b
            return fields({'provider_id': p, 'provider_bot_ref': self.run_id, 'bot_uuid': b})
        if suffix == 'bot-update':
            return fields({'bot_uuid': b, 'provider_bot_ref': self.run_id,
                           'name': 'Provider Review Agent (Senior)', 'domains': ['release', 'audit'], 'scopes': []})
        if suffix in ('attributes-read-denied', 'attributes-write-denied'):
            return {'denied': True}
        if suffix in ('org-create', 'org-read', 'org-update'):
            return fields({'organization_code': org, 'name': 'E2E release organization' +
                           (' updated' if suffix == 'org-update' else '')})
        if suffix == 'org-list':
            return listed('organizations', 'organization_code', [org])
        if suffix in ('candidate-list-before', 'candidate-list-after'):
            return listed('bots', 'bot_uuid', [b] if suffix.endswith('before') else [])
        if suffix in ('candidate-before', 'candidate-after'):
            return fields({'bot_uuid': b, 'is_member': suffix.endswith('after')})
        if suffix in ('member-add', 'member-read'):
            return fields({'bot_uuid': b, 'role': 'reviewer'})
        if suffix == 'member-profile':
            _require(isinstance(value.get('profile'), dict) and value['profile'].get('name') == 'E2E organization bot')
            return {'profile': {'name': 'E2E organization bot'}}
        if suffix == 'member-list':
            return listed('members', 'bot_uuid', [b])
        if suffix in ('admin-start', 'admin-read'):
            _require(value.get('code') == 20000 and isinstance(value.get('data'), dict))
            data = value['data']
            actual_run = self._safe_id(data.get('run_id'))
            if suffix == 'admin-start':
                _require(value.get('message') == 'accepted')
                self._ids['admin_run_id'] = actual_run
            _require(actual_run == self._ids['admin_run_id'] and data.get('target_bot_uuid') == b
                     and data.get('provider_id') == p and data.get('organization_code') == org
                     and data.get('status') == 'delivery_failed')
            sink = self._downstream()
            _require(data.get('session_id') == sink['records'][0]['session_id'])
            return {'data': {k: data[k] for k in ('run_id', 'provider_id', 'organization_code',
                                                'target_bot_uuid', 'session_id', 'status')},
                    'variant': 'REJECTED_NO_ENGINE', 'engine_success': False}
        if suffix == 'member-remove':
            _require(value == {})
            return {'removed_bot_uuid': b, 'organization_code': org, 'deletion': 'LOGICAL'}
        if suffix in ('bot-list', 'bot-list-empty'):
            return listed('items', 'bot_uuid', [b] if suffix == 'bot-list' else [])
        if suffix in ('roster-denied', 'takeover-denied'):
            diagnostic = ('not allowed to access the task-mode roster' if suffix == 'roster-denied'
                          else 'not allowed to switch bot delivery')
            _require(diagnostic in json.dumps(value, ensure_ascii=True))
            return {'denied': True, 'diagnostic': diagnostic}
        if suffix == 'agentpass-negative':
            return fields({'agent_code': None, 'provider_bot_binding': None})
        if suffix == 'unknown-run-callback':
            return fields({'error': 'run_not_found'})
        if suffix == 'bot-retire':
            return fields({'deleted': True, 'provider_id': p, 'provider_bot_ref': self.run_id, 'bot_uuid': b})
        raise RunStateError('Provider assertion has no implementation')

    def _step(self, operation):
        self._active()
        _require(self.state is not None and self._next < len(REQUIREMENTS)
                 and operation == REQUIREMENTS[self._next]
                 and [r['operation'] for r in self.state['operations']] == list(REQUIREMENTS[:self._next]),
                 'Provider operation is outside the finite ordered one-attempt plan')
        suffix = operation.removeprefix('PROVIDER.')
        method, path, body, auth, expected_status = self._spec(operation)
        if suffix != 'register':
            self._resource('provider')
        if self._next > 4 and suffix != 'bot-list-empty':
            self._resource('bot')
        if suffix == 'admin-start':
            self._resource('member')
            self.sink.arm(self._ids['provider_id'], self._ids['provider_bot_uuid'], self._credentials['downlink'])
        request_id = 'e2e-provider-' + uuid.uuid4().hex
        resource = None
        if suffix in _CREATE:
            handle = _CREATE[suffix]
            _require(handle not in self.ledger['resources'], 'Provider resource creation cannot repeat')
            if _PARENTS[handle] is not None:
                self._resource(_PARENTS[handle])
            resource = {'handle': handle, 'kind': handle, 'case_id': CASE_ID, 'attempt': 1,
                        'parent': _PARENTS[handle], 'request_id': request_id, 'origin': 'created',
                        'resource_id': None, 'state': 'INTENDED', 'history': []}
            self.ledger['resources'][handle] = resource
            self._transition(resource, 'INTENDED', request_id)
        elif suffix in _DELETE:
            resource = self._resource(_DELETE[suffix])
            self._transition(resource, 'DELETE_INTENDED', request_id)
        provider_header = suffix in ('admin-start', 'admin-read', 'agentpass-negative', 'unknown-run-callback')
        row = {'operation': operation, 'method': method, 'path': path, 'auth': auth,
               'submitted_request_id': request_id, 'status': 'INTENDED', 'http_status': None,
               'provider_header': provider_header,
               'transitions': ['INTENDED']}
        self.state['operations'].append(row)
        self._save()  # Both durable intent records precede any business bytes.
        try:
            status, value = self._wire(method, path, body, auth, request_id,
                provider_header)
            row['http_status'] = status
            _require(status == expected_status, 'Provider returned an unexpected business HTTP status')
            observed = self._verify(suffix, value)
            self._no_secrets(observed)
            row['observed'] = observed
            value.clear()  # Never retain the response envelope (registration includes credentials).
            self._active()
            row['status'] = 'ACKNOWLEDGED'
            row['transitions'].append('ACKNOWLEDGED')
            if resource is not None:
                if suffix in _CREATE:
                    key = {'provider': 'provider_id', 'bot': 'provider_bot_uuid',
                           'organization': 'organization_code', 'member': 'provider_bot_uuid',
                           'admin_run': 'admin_run_id'}[resource['handle']]
                    resource['resource_id'] = self._ids[key]
                    self._transition(resource, 'LIVE', request_id)
                else:
                    self._transition(resource, 'DELETE_ACKNOWLEDGED', request_id)
            self._save()
            self._post_check(suffix, resource, request_id)
            row['status'] = 'VERIFIED'
            row['transitions'].append('VERIFIED')
            self.state['assertions'].append({'id': operation, 'status': 'PASS'})
            self._next += 1
            self._save()
        except BaseException:
            if row['status'] == 'INTENDED':
                row['status'] = 'UNKNOWN'
                row['transitions'].append('UNKNOWN')
                if resource is not None:
                    self._transition(resource, 'UNKNOWN', request_id)
            else:
                row['status'] = 'UNCONFIRMED'
                row['transitions'].append('UNCONFIRMED')
            # Final persistence is attempted by run(); no subsequent business call is allowed.
            raise

    def _post_check(self, suffix, resource, request_id):
        if suffix in ('register', 'update', 'disable', 'enable'):
            self._observe_provider('provider-' + suffix,
                'E2E Provider' if suffix == 'register' else 'E2E Provider Updated', suffix == 'disable')
        if suffix == 'bot-publish':
            self._observe_bot('bot-created')
        if suffix == 'bot-update':
            self._bot_before_denials = self._observe_bot('bot-updated')
        if suffix == 'attributes-write-denied':
            _require(self._observe_bot('attributes-unchanged') == self._bot_before_denials)
        if suffix in ('org-create', 'org-update', 'member-add', 'member-remove'):
            self._observe_org('organization-' + suffix,
                              None if suffix in ('org-create', 'org-update') else int(suffix == 'member-remove'))
        if suffix == 'bot-retire':
            self._observe_bot('bot-retired', deleted=True)
        if suffix in _DELETE:
            check = 'organization-member-remove' if suffix == 'member-remove' else 'bot-retired'
            self._transition(resource, 'DELETED', request_id, check)
        if suffix in ('register', 'org-create', 'admin-start'):
            self._transition(resource, 'RETAINED', request_id)

    def run(self) -> dict:
        _require(not self._attempted, 'Provider run cannot repeat')
        self._attempted = True
        self._active()
        common = {'schema_version': 1, 'suite': SUITE, 'case_id': CASE_ID,
                  'run_id': self.run_id, 'binding_digest': self.binding_digest}
        self.ledger = dict(common, resources={})
        self.state = dict(common, status='RUNNING', executor_digest=self.executor_digest,
            source_binding_digest=self.source_binding_digest, operations=[], assertions=[], checks=[],
            resources={}, fixture_state={'before': None, 'after': None}, preserved={}, retention={},
            downstream={}, credentials={'credential_memory': 'NOT_CAPTURED'})
        _exclusive(self.ledger_path, self.ledger)
        self._ledger_digest = fingerprint(self.ledger)
        _exclusive(self.path, self.state)
        self._journal_digest = fingerprint(self.state)
        try:
            self.storage = OwnedLocalProviderStorage(self.runtime)
            self._human()
            before = self._check('fixtures-before', lambda: self.storage.fixture_snapshot(
                self.identities._entries, self.identities.human))
            self.state['fixture_state']['before'] = before
            self._check('fresh-provider-namespace', lambda: self.storage.fresh_namespace(self._ids['organization_code']))
            for operation in REQUIREMENTS:
                self._step(operation)
            after = self._check('fixtures-after', lambda: self.storage.fixture_snapshot(
                self.identities._entries, self.identities.human))
            _require(after == before, 'Provider Story changed a preserved workspace fixture')
            self.state['fixture_state']['after'] = after
            self._observe_provider('provider-retained', 'E2E Provider Updated', False)
            self._observe_org('organization-retained', 1)
            self._downstream()
            self.state.update(status='LOCAL_PROVIDER_PASS',
                preserved={'roles': list(ROLES), 'unchanged': True, 'qa_takeover': 'DENIED_UNCHANGED'},
                retention={'provider': 'RETAINED_ENABLED', 'provider_credentials': 'RETAINED_IN_PRODUCT',
                           'organization': 'RETAINED', 'admin_run': 'RETAINED',
                           'member': 'LOGICALLY_REMOVED_HISTORY_RETAINED',
                           'bot': 'SOFT_DELETED_BINDING_DISABLED', 'database': 'RETAINED',
                           'cleanup': 'NOT_RUN'}, resource_disposition='RETAINED_WITH_ORIGINAL_BUSINESS_DELETIONS')
            _require(self.state['assertions'] == [{'id': name, 'status': 'PASS'} for name in REQUIREMENTS])
            self._active()
        except BaseException as error:
            self._invalidated = True
            self.state['status'] = 'BLOCKED'
            self.state['failure'] = {'phase': 'provider-api', 'error_type': type(error).__name__}
            # No raw exception text, responses, credentials, or capability paths escape.
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise RunStateError('Provider journey blocked; preserve journals, no retry or cleanup') from None
        finally:
            if self.state['status'] != 'LOCAL_PROVIDER_PASS':
                self.state['retention'].update(database='RETAINED', cleanup='NOT_RUN',
                    unknown_resources=[h for h, r in self.ledger['resources'].items()
                                       if r['state'] in ('INTENDED', 'UNKNOWN', 'DELETE_INTENDED', 'DELETE_ACKNOWLEDGED')])
            self._credentials.clear()
            self.state['credentials'] = {'credential_memory': 'CLEARED',
                'memory_cleanup_scope': 'EXECUTOR_REFERENCES_ONLY', 'secure_erasure': False,
                'sink_and_role_credentials': 'OUTER_LIFECYCLE_OWNED'}
            self.state['ledger'] = copy.deepcopy(self.ledger)
            self.state['ledger_digest'] = fingerprint(self.ledger)
            self._save()
        return copy.deepcopy(self.state)
