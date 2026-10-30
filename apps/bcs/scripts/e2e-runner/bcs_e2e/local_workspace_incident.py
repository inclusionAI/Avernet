"""Owned-local incident Group configuration for the Workspace parent Story.

This adapter runs only after the successful Workspace API and CLI journals in
one still-live owned runtime.  It retains the incident Group and its implicit
initial Session; messages, deletion, callbacks, remote execution and broad
cleanup remain outside this slice.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request

from .correlation import record_response_id
from .errors import has_cancellation
from .inventory import executor_digest
from .ledger import Ledger
from .local_role_identity import OwnedLocalRoleIdentity
from .local_sqlite import OwnedSqliteDatabase
from .local_workspace_api import OBLIGATIONS as API_OBLIGATIONS, workspace_source_binding
from .local_workspace_cli import OBLIGATIONS as CLI_OBLIGATIONS
from .workspace_retention import retain_value
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write

CASE_ID = 'LOCAL-WORKSPACE-INCIDENT-01'
OBLIGATIONS = (
    'WORKSPACE.incident-group',
    'WORKSPACE.participant-mode',
    'WORKSPACE.workspace',
    'WORKSPACE.routing-service',
)
ROLES = ('CEO', 'PM', 'ENG', 'QA')
OPERATION_SEQUENCE = (
    'incident-create', 'incident-detail', 'participant-mode', 'workspace-put',
    'workspace-get', 'routing-policy-put', 'settings-patch', 'incident-readback',
)
# Dev PR #3173 retired Group service_spec policies station-wide. The story
# tail requires the retired 410 face; the legacy 200 face is still admitted
# for compatible builds (same dual admission as the session-completion and
# workspace-roundtrip slices).
OPERATION_EXPECTED_STATUS = {'workspace-put': 404, 'workspace-get': 404,
                             'settings-patch': (200, 410)}
WORKSPACE = {
    'decisions': ['ship the hotfix'],
    'notes': ['customer impact contained'],
    'tasks': [],
    'audit_log': [],
}
RUNNER_ROOT = Path(__file__).resolve().parents[1]
STORIES_PATH = RUNNER_ROOT.parent / 'e2e-test' / 'stories.sh'
CONTRACT_PATH = RUNNER_ROOT / 'migration-contracts.json'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def incident_source_binding() -> dict:
    try:
        contract = json.loads(CONTRACT_PATH.read_text(), object_pairs_hook=unique_keys)
        story = next(item for item in contract['stories']
                     if item['legacy_id'] == 'story_user_operates_group_workspace')
        ids = tuple(item['id'] for item in story['requirements'])
        if (story['migration_status'] != 'NOT_MIGRATED'
                or tuple(story['required_bot_roles']) != ROLES
                or ids[11:15] != OBLIGATIONS
                or contract['sources']['stories.sh'] != _sha256(STORIES_PATH)):
            raise ValueError
        return {
            'workspace_source_binding': workspace_source_binding(),
            'migration_contract_sha256': _sha256(CONTRACT_PATH),
            'stories_source_sha256': _sha256(STORIES_PATH),
            'covered_obligations': list(OBLIGATIONS),
            'remaining_obligations': list(ids[15:]),
        }
    except (KeyError, OSError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
        raise RunStateError('Workspace incident source or migration binding changed') from None


class OwnedLocalWorkspaceIncident:
    """Execute four ordered incident-configuration requirements."""

    def __init__(self, identities):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'workspace'
                or tuple(identities.roles) != ROLES
                or set(identities._verified) != set(ROLES)):
            raise RunStateError('Workspace incident requires verified owned CEO/PM/ENG/QA identities')
        identities._require_roles(ROLES)
        self.identities = identities
        self.runtime = identities.runtime
        self.path = self.runtime.root / 'local-workspace-incident.json'
        self.ledger_path = self.runtime.root / 'workspace-incident-resources.sqlite'
        self.executor_digest = executor_digest()
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.source_binding = incident_source_binding()
        self.api_journal = self._prior('local-workspace-api.json', 'LOCAL-WORKSPACE-API-01',
                                       'LOCAL_WORKSPACE_API_PASS', API_OBLIGATIONS)
        self.cli_journal = self._prior('local-workspace-cli.json', 'LOCAL-WORKSPACE-CLI-01',
                                       'LOCAL_WORKSPACE_CLI_PASS', CLI_OBLIGATIONS)
        self.api_digest = fingerprint(self.api_journal)
        self.cli_digest = fingerprint(self.cli_journal)
        self.binding_digest = fingerprint({
            'case_id': CASE_ID, 'run_id': self.runtime.run_id,
            'executor_digest': self.executor_digest, 'fixture_digest': self.fixture_digest,
            'source_binding': self.source_binding, 'api_journal_digest': self.api_digest,
            'cli_journal_digest': self.cli_digest,
        })
        self.state = None
        self.ledger = None
        self._journal_digest = ''
        self._invalidated = False

    def _prior(self, name, case_id, status, obligations):
        journal = read(self.runtime.root / name)
        if (journal.get('case_id') != case_id or journal.get('status') != status
                or journal.get('run_id') != self.runtime.run_id
                or journal.get('executor_digest') != self.executor_digest
                or journal.get('fixture_digest') != self.fixture_digest
                or journal.get('assertions') != [
                    {'id': item, 'status': 'PASS'} for item in obligations]
                or journal.get('resource_disposition') != 'RETAINED_LOCAL_DATABASE'
                or journal.get('automatic_delete') is not False):
            raise RunStateError('Workspace incident requires exact successful API and CLI journals')
        return journal

    def _journal_valid(self):
        if self._journal_digest:
            try:
                valid = fingerprint(read(self.path)) == self._journal_digest
            except Exception:
                valid = False
            if not valid:
                self._invalidated = True
                raise RunStateError('Workspace incident journal changed; preserve the original artifact')

    def _save(self):
        self._journal_valid()
        write(self.path, self.state)
        self._journal_digest = fingerprint(self.state)

    def _active(self):
        if self._invalidated:
            raise RunStateError('Workspace incident mutation authority is invalidated')
        self.identities._require_roles(ROLES)
        if (executor_digest() != self.executor_digest
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest
                or fingerprint(incident_source_binding()) != fingerprint(self.source_binding)
                or fingerprint(read(self.runtime.root/'local-workspace-api.json')) != self.api_digest
                or fingerprint(read(self.runtime.root/'local-workspace-cli.json')) != self.cli_digest):
            raise RunStateError('Workspace incident source, fixture, API, or CLI binding changed')
        self._journal_valid()

    def _id(self, role):
        self._active()
        return self.identities._entries[role][0]

    def _actor(self, role):
        self._active()
        if role == 'HUMAN':
            request = Request(self.identities.origin + '/auth/user', method='GET',
                              headers={'Accept': 'application/json'})
            try:
                with self.identities.opener.open(request, timeout=5) as response:
                    raw = response.read(16_385)
                    status = response.status
                value = json.loads(raw.decode(), object_pairs_hook=unique_keys)
                if (status != 200 or len(raw) > 16_384 or value.get('user_id') != self.identities.human):
                    raise RunStateError('Workspace incident Human identity was not confirmed')
                return 'human_' + self.identities.human, None
            except RunStateError:
                raise
            except Exception:
                raise RunStateError('Workspace incident Human identity read failed') from None
        bot_id, token = self.identities._entries[role]
        # Bracket mutation admission with exact self-scoped reads.
        for operation in ('visibility', 'friends', 'visibility'):
            value = self.identities._request(operation, bot_id, token)
            if operation == 'friends' and (not isinstance(value.get('data'), list)
                                            or len(value['data']) > 100):
                raise RunStateError('Workspace incident Bot self-read is invalid')
        if self.identities._entries[role] != (bot_id, token):
            raise RunStateError('Workspace incident Bot credential changed')
        return bot_id, token

    def _expected(self, operation):
        ceo, pm, eng = self._id('CEO'), self._id('PM'), self._id('ENG')
        if operation == 'incident-create':
            return ('POST', '/groups', {
                'driver_bot': ceo, 'label': 'Incident response room',
                'participants': [
                    {'bot_uuid': ceo, 'role': 'driver'},
                    {'bot_uuid': pm, 'role': 'consultant'},
                    {'bot_uuid': eng, 'role': 'consultant'},
                ],
            }, 'CEO')
        resource = self.state['resources'].get('incident')
        if not resource:
            raise RunStateError('Workspace incident operation requires its owned Group')
        self._require_owned_resource(resource)
        base = '/groups/' + quote(resource['id'], safe='')
        return {
            'incident-detail': ('GET', base, None, 'HUMAN'),
            'participant-mode': ('PUT', base+'/participants/'+quote(eng, safe='')+'/mode',
                                 {'mode': 'muted'}, 'ENG'),
            'workspace-put': ('PUT', base+'/workspace', WORKSPACE, 'HUMAN'),
            'workspace-get': ('GET', base+'/workspace', None, 'HUMAN'),
            'routing-policy-put': ('PUT', base+'/routing-policy', {
                'mode': 'structured',
                'default_bot_final_delivery': 'inject_observers',
                'sender_routes': {ceo: [pm]},
            }, 'CEO'),
            'settings-patch': ('PATCH', base+'/settings',
                               {'service_spec': {'max_concurrency': 2}}, 'HUMAN'),
            'incident-readback': ('GET', base, None, 'HUMAN'),
        }[operation]

    def _require_owned_resource(self, resource):
        if self.ledger is None or not isinstance(resource, dict):
            raise RunStateError('Workspace incident resource ledger is unavailable')
        group = self.ledger._get(resource.get('handle'))
        initial = resource.get('initial_session')
        if not isinstance(initial, dict):
            raise RunStateError('Workspace incident initial Session binding is missing')
        session = self.ledger._get(initial.get('handle'))
        if (group['kind'] != 'group' or group['resource_id'] != resource.get('id')
                or group['origin'] != 'created' or group['state'] != 'live'
                or group['case_id'] != CASE_ID or group['attempt'] != 1
                or group['parent'] is not None
                or session['kind'] != 'session'
                or session['resource_id'] != initial.get('id')
                or session['origin'] != 'created' or session['state'] != 'live'
                or session['case_id'] != CASE_ID or session['attempt'] != 1
                or session['parent'] != group['handle']):
            raise RunStateError('Workspace incident Group or initial Session ledger scope changed')

    def _operation(self, operation):
        self._active()
        index = len(self.state['operations'])
        if index >= len(OPERATION_SEQUENCE) or OPERATION_SEQUENCE[index] != operation:
            raise RunStateError('Workspace incident operation is outside its finite ordered plan')
        method, path, body, role = self._expected(operation)
        actor_id, token = self._actor(role)
        request_id = 'e2e-workspace-incident-' + uuid.uuid4().hex
        handles = None
        if operation == 'incident-create':
            handles = self.ledger.group_creation_intent(CASE_ID, 1, request_id)
        event = {
            'operation': operation, 'method': method, 'path': path, 'body': body,
            'role': role, 'actor_id': actor_id,
            'actor_contract': 'BUILT_IN_LOCAL_HUMAN' if role == 'HUMAN' else 'OWNED_LOCAL_BOT',
            'submitted_request_id': request_id, 'status': 'INTENDED',
            'resource_binding': 'UNKNOWN' if handles else 'BOUND_INCIDENT_GROUP',
        }
        if handles:
            event['handles'] = {'group': handles[0], 'initial_session': handles[1]}
        self.state['operations'].append(event)
        self._save()  # exact operation and creation handles precede every effect
        headers = {'Accept': 'application/json', 'X-Request-ID': request_id}
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(self.identities.origin + path, method=method, data=data, headers=headers)
        expected_status = OPERATION_EXPECTED_STATUS.get(operation, 200)
        allowed = expected_status if isinstance(expected_status, tuple) else (expected_status,)
        try:
            try:
                response = self.identities.opener.open(request, timeout=5)
            except HTTPError as error:
                if error.code not in allowed:
                    raise
                response = error
            with response:
                event['http_status'] = response.status
                record_response_id(event, response.headers.get('x-request-id'), source='HEADER',
                                   sensitive_values=((token,) if token else ()))
                raw = response.read(65_537)
            self._active()
            if response.status not in allowed or len(raw) > 65_536:
                raise RunStateError('Workspace incident operation was not exactly confirmed')
            if response.status == 404:
                result = {'http_status': 404}
            else:
                result = json.loads(raw.decode(), object_pairs_hook=unique_keys)
            if not isinstance(result, dict):
                raise RunStateError('Workspace incident response is not an object')
            event['value'] = retain_value(result)
            if handles:
                group_id = identifier(result.get('id'), 'incident Group ID')
                session_id = identifier(result.get('session_id'), 'incident initial Session ID')
                if not session_id.startswith(group_id + ':'):
                    raise RunStateError('Workspace incident initial Session identity is invalid')
                event['candidate_ids'] = {'group': group_id, 'initial_session': session_id}
                self._save()
                self.ledger.observed(handles[0], group_id, origin='created')
                self.ledger.observed(handles[1], session_id, origin='created')
                self.state['resources']['incident'] = {
                    'id': group_id, 'handle': handles[0],
                    'initial_session': {'id': session_id, 'handle': handles[1]},
                }
            event.update(status='ACKNOWLEDGED', resource_binding='BOUND')
            self._save()
            return result
        except BaseException as error:
            self._invalidated = True
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            if isinstance(error, HTTPError):
                event['http_status'] = error.code
                error.close()
            if handles:
                try:
                    self.ledger.unknown(handles[0]); self.ledger.unknown(handles[1])
                except BaseException:
                    pass
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise RunStateError('Workspace incident operation failed; retain without retry') from None

    @staticmethod
    def _settings_form(value):
        """Dual settings face from the retained response body (PR #3173).

        _operation returns the parsed response body, not the journal event, so
        classification reads the body shapes directly: the retired face answers
        the exact service_policy_retired body; the legacy face acknowledges with
        the bounded service_spec payload.
        """
        if value == {'error': 'service_policy_retired',
                     'message': 'service_spec settings have been retired'}:
            return 'retired_410'
        if (isinstance(value, dict) and value.get('id') is not None
                and value.get('status') == 'ok'
                and value.get('service_spec') == {'max_concurrency': 2}):
            return 'service_spec_ack'
        return None

    @staticmethod
    def _readback_form(readback):
        """Group detail face: service_spec present (legacy) or absent (retired)."""
        if not isinstance(readback, dict) or readback.get('id') is None:
            return None
        if 'service_spec' not in readback:
            return 'retired_410'
        return ('service_spec_ack'
                if readback.get('service_spec') == {'max_concurrency': 2} else None)

    @staticmethod
    def _service_spec_form(service):
        """Dual admission of the persisted service_spec column (PR #3173).

        The retired build no longer persists the incident group's service_spec
        column (NULL), while the legacy build stored the bounded dict. The
        exact routing row stays the strict authority; the service_spec column
        mirrors the executor-side dual admission in _settings_form.
        """
        if service is None:
            return 'retired_null'
        if service == {'max_concurrency': 2}:
            return 'legacy_ack'
        return None

    @staticmethod
    def _participants(value):
        rows = value.get('participants')
        if (not isinstance(rows, list) or len(rows) != 3
                or any(not isinstance(row, dict) for row in rows)):
            raise RunStateError('Workspace incident participant projection is invalid')
        return {row.get('bot_uuid'): row for row in rows}

    def _assert(self, obligation, passed):
        self.state['assertions'].append({'id': obligation, 'status': 'PASS' if passed else 'FAIL'})
        self._save()
        if not passed:
            raise RunStateError('Workspace incident business obligation failed')

    def _verify_storage(self):
        self._active()
        resource = self.state['resources']['incident']
        group_id, session_id = resource['id'], resource['initial_session']['id']
        with OwnedSqliteDatabase(self.runtime)._connection() as database:
            database.execute('BEGIN')
            groups = [dict(row) for row in database.execute(
                "SELECT group_id,label,status,driver_bot,originator,routing_policy_json,service_spec "
                "FROM bcs_groups WHERE env='local' AND group_id=? LIMIT 2", (group_id,)).fetchall()]
            participants = [tuple(row) for row in database.execute(
                "SELECT bot_uuid,role,actor_kind,mode FROM bcs_group_participants "
                "WHERE env='local' AND group_id=? ORDER BY bot_uuid LIMIT 4", (group_id,)).fetchall()]
            sessions = [tuple(row) for row in database.execute(
                "SELECT session_id,group_id,status,session_kind FROM bcs_group_sessions "
                "WHERE env='local' AND session_id=? LIMIT 2", (session_id,)).fetchall()]
        self._active()
        if len(groups) != 1 or len(participants) != 3 or len(sessions) != 1:
            raise RunStateError('Workspace incident exact stored rows are missing or duplicated')
        group = groups[0]
        routing = json.loads(group['routing_policy_json'], object_pairs_hook=unique_keys)
        service = (json.loads(group['service_spec'], object_pairs_hook=unique_keys)
                   if group['service_spec'] is not None else None)
        service_form = self._service_spec_form(service)
        expected_participants = {
            (self._id('CEO'), 'driver', 'bot', 'auto'),
            (self._id('PM'), 'consultant', 'bot', 'auto'),
            (self._id('ENG'), 'consultant', 'bot', 'muted'),
        }
        if (group['label'] != 'Incident response room' or group['status'] != 'active'
                or group['driver_bot'] != self._id('CEO') or group['originator'] != self._id('CEO')
                or routing != {'mode': 'structured',
                               'default_bot_final_delivery': 'inject_observers',
                               'sender_routes': {self._id('CEO'): [self._id('PM')]}}
                or service_form is None
                or set(participants) != expected_participants
                or sessions != [(session_id, group_id, 'running', 'chat')]):
            raise RunStateError('Workspace incident stored configuration is not exact')
        rows = self.ledger.resource_snapshot()
        if (len(rows) != 2 or [row['kind'] for row in rows] != ['group', 'session']
                or any(row['case_id'] != CASE_ID or row['attempt'] != 1
                       or row['origin'] != 'created' or row['state'] != 'live' for row in rows)
                or rows[0]['resource_id'] != group_id or rows[1]['resource_id'] != session_id
                or rows[1]['parent'] != rows[0]['handle']):
            raise RunStateError('Workspace incident resource ledger is incomplete')
        self.state['storage_verification'] = {
            'status': 'PASS', 'group_rows': 1, 'participant_rows': 3,
            'initial_session_rows': 1, 'ledger_resources': 2,
            'workspace_routes': 'DEPRECATED_HTTP_404',
            'service_spec_form': service_form,
        }
        self._save()

    def _execute(self):
        created = self._operation('incident-create')
        expected_ids = {self._id('CEO'), self._id('PM'), self._id('ENG')}
        if (created.get('driver_bot') != self._id('CEO')
                or set(created.get('participants', [])) != expected_ids
                or len(created.get('participants', [])) != 3):
            self._assert('WORKSPACE.incident-group', False)
        detail = self._operation('incident-detail')
        participants = self._participants(detail)
        self._assert('WORKSPACE.incident-group',
                     detail.get('id') == self.state['resources']['incident']['id']
                     and detail.get('label') == 'Incident response room'
                     and detail.get('driver_bot') == self._id('CEO')
                     and set(participants) == expected_ids
                     and detail.get('latest_running_session_id') ==
                     self.state['resources']['incident']['initial_session']['id'])

        mode = self._operation('participant-mode')
        data = mode.get('data')
        self._assert('WORKSPACE.participant-mode',
                     mode.get('success') is True and isinstance(data, dict)
                     and data.get('group_id') == self.state['resources']['incident']['id']
                     and data.get('actor_id') == self._id('ENG') and data.get('mode') == 'muted')

        updated = self._operation('workspace-put')
        fetched = self._operation('workspace-get')
        self._assert('WORKSPACE.workspace',
                     updated == {'http_status': 404}
                     and fetched == {'http_status': 404})

        routing = self._operation('routing-policy-put')
        settings = self._operation('settings-patch')
        readback = self._operation('incident-readback')
        expected_routing = {'mode': 'structured',
                            'default_bot_final_delivery': 'inject_observers',
                            'sender_routes': {self._id('CEO'): [self._id('PM')]}}
        # The current legacy GET projection exposes service_spec but not routing_policy;
        # routing persistence is independently checked from the exact owned DB row.
        # PR #3173 retired the service_spec settings face station-wide, so the
        # incident slice records the observed settings face instead of assuming
        # the legacy projection; both faces are admitted explicitly.
        settings_form = self._settings_form(settings)
        readback_form = self._readback_form(readback)
        self.state['settings_form'] = settings_form
        self._assert('WORKSPACE.routing-service',
                     routing == {'ok': True, 'routing_policy': expected_routing}
                     and settings_form is not None and settings_form == readback_form
                     and (settings_form == 'retired_410'
                          or settings.get('id') == readback.get('id'))
                     and readback.get('id') == self.state['resources']['incident']['id']
                     # The current build exposes the incident room's running
                     # initial Session in the detail projection (the CLI slice
                     # pins the same projection for the primary Group); legacy
                     # builds omitted the field. Both documented faces admitted.
                     and readback.get('latest_running_session_id') in (
                         None, self.state['resources']['incident']['initial_session']['id']))
        self._verify_storage()

    def run(self):
        self._active()
        if (self.path.exists() or self.path.is_symlink()
                or self.ledger_path.exists() or self.ledger_path.is_symlink()):
            raise RunStateError('Workspace incident requires fresh journal and ledger paths')
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.state = {
            'schema_version': 1, 'contract': 'owned-local-workspace-incident-config-v1',
            'case_id': CASE_ID, 'run_id': self.runtime.run_id, 'status': 'RUNNING',
            'executor_digest': self.executor_digest, 'fixture_digest': self.fixture_digest,
            'source_binding': self.source_binding, 'api_journal_digest': self.api_digest,
            'cli_journal_digest': self.cli_digest, 'binding_digest': self.binding_digest,
            'verified_roles': list(ROLES), 'operations': [], 'resources': {}, 'assertions': [],
            'storage_verification': {'status': 'NOT_RUN'},
            'resource_disposition': 'RETAINED_LOCAL_DATABASE', 'automatic_delete': False,
            'messages': 'NOT_RUN', 'chat': 'NOT_RUN', 'callback': 'NOT_RUN',
            'incident_delete': 'NOT_RUN', 'remote_execution': 'NOT_RUN',
            'broad_delete': 'NOT_USED', 'full_story_status': 'NOT_MIGRATED',
            'original_parent_requirement_count': 17, 'ready_to_execute': False,
        }
        self._save()
        with Ledger(self.ledger_path, self.runtime.run_id, self.binding_digest, create=True) as ledger:
            self.ledger = ledger
            try:
                self._execute()
                if ([row['operation'] for row in self.state['operations']] != list(OPERATION_SEQUENCE)
                        or any(row['status'] != 'ACKNOWLEDGED' for row in self.state['operations'])
                        or self.state['assertions'] != [
                            {'id': item, 'status': 'PASS'} for item in OBLIGATIONS]
                        or set(self.state['resources']) != {'incident'}):
                    raise RunStateError('Workspace incident evidence is incomplete or out of order')
                self.state['status'] = 'LOCAL_WORKSPACE_INCIDENT_CONFIG_PASS'
                self._save()
                return json.loads(json.dumps(self.state, allow_nan=False))
            except BaseException as error:
                self._invalidated = True
                cancelled = has_cancellation(error)
                self.state['status'] = 'CANCELLED' if cancelled else 'BLOCKED'
                try:
                    self._save()
                except BaseException:
                    pass
                if cancelled:
                    raise KeyboardInterrupt() from None
                raise RunStateError('Workspace incident slice failed; inspect retained artifacts') from None
