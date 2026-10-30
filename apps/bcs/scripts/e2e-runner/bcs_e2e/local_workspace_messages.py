"""Real owned Workspace ingress and Bot-wire observations, never engine reasoning."""
from __future__ import annotations

from contextlib import ExitStack
import json
import hashlib
import os
import uuid
from urllib.request import Request

from .errors import has_cancellation
from .workspace_retention import retain_value
from .ledger import Ledger
from .local_workspace_incident import CASE_ID as INCIDENT_CASE, OBLIGATIONS as INCIDENT_OBLIGATIONS, OwnedLocalWorkspaceIncident
from .local_sqlite import OwnedSqliteDatabase
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

CASE_ID = 'LOCAL-WORKSPACE-MESSAGES-01'
OBLIGATIONS = ('WORKSPACE.messages',)
OPERATIONS = ('persistent-message', 'live-chat', 'agent-callback')
PERSISTENT_TEXT = 'Please validate the hotfix'
CALLBACK_TEXT = 'Engineering validation finished'


class OwnedLocalWorkspaceMessages:
    def __init__(self, identities):
        self.scope = OwnedLocalWorkspaceIncident(identities)
        self.identities, self.runtime = identities, identities.runtime
        scope = self.scope
        scope.state = read(scope.path)
        if (scope.state.get('status') != 'LOCAL_WORKSPACE_INCIDENT_CONFIG_PASS'
                or scope.state.get('case_id') != INCIDENT_CASE
                or scope.state.get('run_id') != self.runtime.run_id
                or scope.state.get('binding_digest') != scope.binding_digest
                or scope.state.get('assertions') != [{'id': name, 'status': 'PASS'} for name in INCIDENT_OBLIGATIONS]):
            raise RunStateError('Workspace messages require the exact successful incident configuration')
        scope._journal_digest = fingerprint(scope.state)
        config = json.loads(self.runtime.inputs.config_path.read_text(), object_pairs_hook=unique_keys)
        if config.get('store_messages') is not True:
            raise RunStateError('Workspace messages require storage enabled before owned runtime startup')
        self.resource = scope.state['resources']['incident']
        self.group_id = self.resource['id']
        self.session_id = self.resource['initial_session']['id']
        self.path = self.runtime.root/'local-workspace-messages.json'
        self.state = None
        self._digest = ''
        self._invalidated = False
        self._completed = False
        self._receivers = {}

    def _active(self):
        if self._invalidated:
            raise RunStateError('Workspace message authority is invalidated')
        self.scope._active()
        self.scope._require_owned_resource(self.resource)
        if self._digest and fingerprint(read(self.path)) != self._digest:
            raise RunStateError('Workspace message journal changed; preserve the original')

    def _save(self):
        if self._digest and fingerprint(read(self.path)) != self._digest:
            raise RunStateError('Workspace message journal changed; preserve the original')
        write(self.path, self.state)
        self._digest = fingerprint(self.state)

    def _expected(self, operation):
        ceo = self.scope._id('CEO')
        name = read(self.identities.fixtures.path)['roles']['PM']['name']
        return {
            'persistent-message': ('messages', {'sender': ceo, 'content': PERSISTENT_TEXT,
                                              'message_type': 'bot', 'role': 'user'}, 'HUMAN'),
            'live-chat': ('chat', {'message': '@'+name+' please coordinate the release', 'from': ceo}, 'CEO'),
            'agent-callback': ('callback', {'message': CALLBACK_TEXT, 'mentions': [ceo],
                                          'metadata': {'source': 'e2e'}}, 'HUMAN'),
        }[operation]

    def _post(self, operation):
        self._active()
        index = len(self.state['operations'])
        if index >= len(OPERATIONS) or OPERATIONS[index] != operation:
            raise RunStateError('Workspace message operation is outside the exact ordered plan')
        suffix, body, role = self._expected(operation)
        actor, token = self.scope._actor(role)
        event = {'operation': operation, 'method': 'POST', 'path': '/groups/'+self.group_id+'/'+suffix,
                 'body': body, 'role': role, 'actor_id': actor, 'status': 'INTENDED',
                 'submitted_request_id': 'e2e-workspace-message-'+uuid.uuid4().hex}
        self.state['operations'].append(event)
        self._save()
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'X-Request-ID': event['submitted_request_id']}
        if token:
            headers['Authorization'] = 'Bearer '+token
        request = Request(self.identities.origin+event['path'], method='POST', headers=headers,
                          data=json.dumps(body, separators=(',', ':')).encode())
        try:
            with self.identities.opener.open(request, timeout=10) as response:
                event['http_status'] = response.status
                raw = response.read(65_537)
            self._active()
            if response.status != 200 or len(raw) > 65_536:
                raise RunStateError('Workspace message response was not confirmed')
            result = json.loads(raw.decode(), object_pairs_hook=unique_keys)
            if not isinstance(result, dict):
                raise RunStateError('Workspace message response is not an object')
            event['value'] = retain_value(result)
            event['status'] = 'ACKNOWLEDGED'
            self._save()
            return result
        except BaseException as error:
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            if hasattr(error, 'code'):
                event['http_status'] = error.code
                # Retain the bounded failure body verbatim (PR #3173 dual-
                # admission discipline): a 500 carries the ServiceError text
                # that classifies the failed branch; never replay from it.
                try:
                    event['error_body'] = error.read(65_537).decode(
                        'utf-8', 'replace')[:65_536]
                except BaseException:
                    event['error_body'] = None
                error.close()
            self._invalidated = True
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise RunStateError('Workspace message request failed; retain without replay') from None

    def _deliveries(self, result, expected):
        rows = result.get('delivery_results')
        required = {(self.scope._id(role), method.removeprefix('chat.')) for role, method in expected}
        if (result.get('group_id') != self.group_id or result.get('delivered') is not True
                or result.get('delivered_count') != len(required) or result.get('failed_count') != 0
                or not isinstance(rows, list) or len(rows) != len(required)
                or any(not isinstance(row, dict) or row.get('success') is not True for row in rows)
                or {(row.get('bot_uuid'), row.get('delivery_type')) for row in rows} != required):
            raise RunStateError('Workspace delivery result failed its exact target/type/success assertion')

    def _receive(self, receivers, operation, text, expected):
        for role, method in expected:
            self._active()
            evidence = receivers[role].receive_delivery(group_id=self.group_id, session_id=self.session_id,
                                                         text=text, method=method)
            if (evidence.get('status') != 'TRANSPORT_RECEIVED_ONLY'
                    or evidence.get('target_role') != role or evidence.get('target_bot_id') != self.scope._id(role)
                    or evidence.get('method') != method or evidence.get('group_id') != self.group_id
                    or evidence.get('success_ack_sent') is not False
                    or evidence.get('text_sha256') != hashlib.sha256(text.encode()).hexdigest()
                    or not evidence.get('request_id')
                    or evidence['request_id'] in {row['request_id'] for row in self.state['wire_receipts']}):
                raise RunStateError('Workspace Bot receipt is incomplete or has a foreign scope')
            self.state['wire_receipts'].append({'operation': operation, **evidence})
            self._save()

    def _read_storage(self):
        self._active()
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT message_id,session_id,sender_id,sender_type,message_type,content,status '
                'FROM bcs_messages WHERE env=? AND group_id=? ORDER BY created_at LIMIT 21',
                ('local', self.group_id)).fetchall()]
        if (len(rows) > 20 or len({row['message_id'] for row in rows}) != len(rows)
                or any(row['session_id'] != self.session_id for row in rows)):
            raise RunStateError('Workspace message storage exceeds its bound or has a foreign Session')
        return rows

    def _storage(self):
        rows = self._read_storage()
        baseline = {row['message_id']: row for row in self._baseline}
        current = {row['message_id']: row for row in rows}
        if any(current.get(key) != row for key, row in baseline.items()):
            raise RunStateError('Workspace pre-message history changed')
        rows = [row for row in rows if row['message_id'] not in baseline]
        if len(rows) != 3:
            raise RunStateError('Workspace message stage must add exactly three persisted rows')
        name = read(self.identities.fixtures.path)['roles']['PM']['name']
        wanted = [(PERSISTENT_TEXT, self.scope._id('CEO'), 'bot', 'chat'),
                  ('@'+name+' please coordinate the release', self.scope._id('CEO'), 'bot', 'chat'),
                  (CALLBACK_TEXT, 'system', 'system', 'system')]
        evidence = []
        for text, sender, sender_type, message_type in wanted:
            matches = [row for row in rows if json.loads(row['content']) == text]
            if (len(matches) != 1 or matches[0]['sender_id'] != sender
                    or matches[0]['sender_type'] != sender_type or matches[0]['message_type'] != message_type
                    or matches[0]['status'] != 'normal'
                    or matches[0]['session_id'] != self.session_id):
                raise RunStateError('Workspace message storage did not preserve exact content and sender')
            row = matches[0]
            evidence.append({'message_id': identifier(row['message_id'], 'stored message ID'),
                             'session_id': row['session_id'], 'sender_id': sender, 'status': 'PASS'})
        self.state['storage_verification'] = {'status': 'PASS', 'messages': evidence,
            'baseline_rows': len(self._baseline), 'new_rows': len(rows), 'baseline_unchanged': True,
            'api_message_id_equals_database_id': self.state['persistent_message_id'] == evidence[0]['message_id']}
        self._save()

    def run(self):
        from .local_bot_receiver import OwnedLocalBotReceiver
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Workspace messages require a fresh journal')
        with Ledger(self.scope.ledger_path, self.runtime.run_id, self.scope.binding_digest, read_only=True) as ledger:
            self.scope.ledger = ledger
            self._active()
            fd = os.open(self.path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
            os.close(fd)
            self.state = {'schema_version': 1, 'case_id': CASE_ID, 'run_id': self.runtime.run_id,
                'executor_digest': self.scope.executor_digest, 'status': 'RUNNING',
                'incident_digest': self.scope._journal_digest, 'group_id': self.group_id,
                'initial_session_id': self.session_id, 'operations': [], 'wire_receipts': [], 'assertions': [],
                'storage_verification': {'status': 'NOT_RUN'}, 'receiver_state': 'NOT_OPENED',
                'engine_reasoning': 'NOT_TESTED', 'remote_validation': 'NOT_RUN',
                'resource_disposition': 'RETAINED_LOCAL_DATABASE', 'automatic_delete': False,
                'ready_to_execute': False, 'full_story_status': 'NOT_MIGRATED'}
            self._save()
            try:
                self._baseline = self._read_storage()
                self.state['message_baseline'] = {'row_count': len(self._baseline),
                                                 'digest': fingerprint({'rows': self._baseline})}
                self._save()
                with ExitStack() as stack:
                    receivers = {role: stack.enter_context(OwnedLocalBotReceiver(self.identities, role))
                                 for role in ('CEO', 'PM', 'ENG')}
                    self._receivers = receivers
                    self.state['receiver_state'] = 'CONNECTED'
                    self._save()
                    result = self._post('persistent-message')
                    self.state['persistent_message_id'] = identifier(result.get('message_id'), 'API message ID')
                    targets = result.get('routed_to')
                    if not isinstance(targets, list) or len(targets) != 2 or set(targets) != {self.scope._id('PM'), self.scope._id('ENG')}:
                        raise RunStateError('Workspace persistent message targets differ from the original scope')
                    self._receive(receivers, 'persistent-message', PERSISTENT_TEXT,
                                  (('PM', 'chat.inject'), ('ENG', 'chat.inject')))
                    result = self._post('live-chat')
                    expected = (('CEO', 'chat.inject'), ('PM', 'chat.send'), ('ENG', 'chat.inject'))
                    self._deliveries(result, expected)
                    self._receive(receivers, 'live-chat', 'please coordinate the release', expected)
                    result = self._post('agent-callback')
                    expected = (('CEO', 'chat.send'), ('PM', 'chat.inject'), ('ENG', 'chat.inject'))
                    self._deliveries(result, expected)
                    self._receive(receivers, 'agent-callback', CALLBACK_TEXT, expected)
                    self._storage()
                if any(receiver.summary()['state'] != 'CLOSED' for receiver in receivers.values()):
                    raise RunStateError('Workspace Bot receivers did not close')
                if len(self.state['wire_receipts']) != 8 or len(self.state['operations']) != 3:
                    raise RunStateError('Workspace message execution evidence is incomplete')
                self.state['receiver_state'] = 'CLOSED'
                self._active()
                self.state['assertions'] = [{'id': OBLIGATIONS[0], 'status': 'PASS'}]
                self.state['status'] = 'LOCAL_WORKSPACE_MESSAGES_PASS'
                self._save()
                self._completed = True
                return json.loads(json.dumps(self.state))
            except BaseException as error:
                self._invalidated = True
                self.state['status'] = 'CANCELLED' if has_cancellation(error) else 'BLOCKED'
                self.state['error_type'] = type(error).__name__
                self.state['assertions'] = [{'id': OBLIGATIONS[0], 'status': 'FAIL'}]
                self.state['receiver_state'] = 'EXITED_CONTEXT'
                try:
                    self._save()
                except BaseException:
                    pass
                if has_cancellation(error):
                    raise KeyboardInterrupt() from None
                raise RunStateError('Workspace messages failed; inspect retained artifacts') from None
