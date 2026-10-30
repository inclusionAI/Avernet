"""Finite owned-local migration of the first seven legacy Group obligations.

The caller owns the live :class:`OwnedBcsProcess` and the one-lifecycle Bot
credentials.  This adapter has no remote profile, shell harness, cleanup, or
retry path.  Both Groups and their implicit initial Sessions are retained in
the isolated local database for independent inspection.
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
from .workspace_retention import MAX_RETAINED_READS, retain_value
from .errors import has_cancellation
from .inventory import executor_digest
from .ledger import Ledger
from .local_role_identity import OwnedLocalRoleIdentity
from .local_sqlite import OwnedSqliteDatabase
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write
from .trust_baseline import visibility

CASE_ID = 'LOCAL-WORKSPACE-API-01'
OBLIGATIONS = (
    'WORKSPACE.create',
    'WORKSPACE.detail',
    'WORKSPACE.patch',
    'WORKSPACE.list',
    'WORKSPACE.members',
    'WORKSPACE.label',
    'WORKSPACE.visibility',
)
REMAINING_PARENT_OBLIGATIONS = (
    'WORKSPACE.cli-create-get',
    'WORKSPACE.cli-add-member',
    'WORKSPACE.cli-fuse',
    'WORKSPACE.cli-status',
    'WORKSPACE.incident-group',
    'WORKSPACE.participant-mode',
    'WORKSPACE.workspace',
    'WORKSPACE.routing-service',
    'WORKSPACE.messages',
    'WORKSPACE.incident-delete',
)
ROLES = ('CEO', 'PM', 'ENG', 'QA')
WRITE_SEQUENCE = (
    'fixture-ceo-visibility-public', 'fixture-pm-visibility-public', 'fixture-eng-visibility-public',
    'group-create-primary', 'group-create-with-members', 'group-patch', 'group-member-add',
    'group-member-remove', 'group-label', 'group-visibility',
)
MAX_WRITE_OPERATIONS = len(WRITE_SEQUENCE)
RUNNER_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = RUNNER_ROOT / 'migration-contracts.json'
GROUP_SOURCE_PATH = RUNNER_ROOT.parent / 'e2e-test' / 'group.sh'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_source_binding() -> dict:
    """Validate and return the finite legacy-source/obligation binding."""
    try:
        contract = json.loads(CONTRACT_PATH.read_text(), object_pairs_hook=unique_keys)
        story = contract['stories'][0]
        requirements = tuple(item['id'] for item in story['requirements'])
        source_digest = _sha256(GROUP_SOURCE_PATH)
        if (story['legacy_id'] != 'story_user_operates_group_workspace'
                or story['migration_status'] != 'NOT_MIGRATED'
                or tuple(story['required_bot_roles']) != ROLES
                or requirements != OBLIGATIONS + REMAINING_PARENT_OBLIGATIONS
                or contract['sources']['group.sh'] != source_digest):
            raise ValueError
        return {
            'source_revision': identifier(contract['source_revision'], 'migration source revision'),
            'migration_contract_sha256': _sha256(CONTRACT_PATH),
            'group_source_sha256': source_digest,
            'legacy_parent': story['legacy_id'],
            'covered_obligations': list(OBLIGATIONS),
            'remaining_obligations': list(REMAINING_PARENT_OBLIGATIONS),
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        raise RunStateError('workspace legacy source or migration contract binding changed') from None


class OwnedLocalWorkspaceApi:
    """Run seven exact legacy Group obligations in one caller-owned runtime."""

    def __init__(self, identities: OwnedLocalRoleIdentity):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'workspace'
                or tuple(identities.roles) != ROLES
                or set(identities._verified) != set(ROLES)):
            raise RunStateError(
                'workspace API requires freshly verified CEO/PM/ENG/QA owned-local identities')
        identities._require_roles(ROLES)
        self.identities = identities
        self.runtime = identities.runtime
        self.path = self.runtime.root / 'local-workspace-api.json'
        self.ledger_path = self.runtime.root / 'workspace-resources.sqlite'
        self.executor_digest = executor_digest()
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.source_binding = workspace_source_binding()
        self.binding_digest = fingerprint({
            'run_id': self.runtime.run_id,
            'fixture_digest': self.fixture_digest,
            'executor_digest': self.executor_digest,
            'source_binding': self.source_binding,
        })
        self.state = None
        self.ledger = None
        self._journal_digest = ''
        self._invalidated = False

    def _journal_valid(self):
        if self._journal_digest:
            try:
                valid = fingerprint(read(self.path)) == self._journal_digest
            except Exception:
                valid = False
            if not valid:
                self._invalidated = True
                raise RunStateError('workspace API journal changed; preserve the original artifact')

    def _save(self):
        self._journal_valid()
        write(self.path, self.state)
        self._journal_digest = fingerprint(self.state)

    def _active(self):
        if self._invalidated:
            raise RunStateError('workspace API mutation authority is invalidated')
        self.identities._require_roles(ROLES)
        if (executor_digest() != self.executor_digest
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest
                or fingerprint(workspace_source_binding()) != fingerprint(self.source_binding)):
            raise RunStateError('workspace executor, fixture, or legacy source binding changed')
        self._journal_valid()

    def _id(self, role: str) -> str:
        self._active()
        return self.identities._entries[role][0]

    def _human_identity(self) -> str:
        self._active()
        request = Request(self.identities.origin + '/auth/user', method='GET',
                          headers={'Accept': 'application/json'})
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                if response.status != 200:
                    raise RunStateError('workspace local Human identity was not confirmed')
                raw = response.read(16_385)
            self._active()
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if (len(raw) > 16_384 or not isinstance(value, dict)
                    or value.get('user_id') != self.identities.human):
                raise RunStateError('workspace local Human identity differs from the owned runtime')
            return 'human_' + self.identities.human
        except RunStateError:
            raise
        except Exception:
            raise RunStateError('workspace local Human identity read failed') from None

    def _read(self, path: str, *, role: str | None = None) -> dict:
        self._active()
        allowed = {'/groups?offset=0&limit=50&group_kind=all'} | {
            '/groups/' + quote(resource['id'], safe='') for resource in self.state['resources'].values()}
        if path not in allowed or role is not None:
            raise RunStateError('workspace read is outside the exact owned Group plan')
        headers = {'Accept': 'application/json'}
        if role is not None:
            token = self.identities._entries[role][1]
            headers['Authorization'] = 'Bearer ' + token
        request = Request(self.identities.origin + path, method='GET', headers=headers)
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                if response.status != 200:
                    raise RunStateError('workspace read was not confirmed')
                raw = response.read(65_537)
            self._active()
            if len(raw) > 65_536:
                raise RunStateError('workspace read exceeds its byte budget')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if not isinstance(value, dict):
                raise RunStateError('workspace read response is not an object')
            self.state['reads'].append({'method': 'GET', 'path': path,
                'after_operation': self.state['operations'][-1]['operation'] if self.state['operations'] else None,
                'value': retain_value(value)})
            if len(self.state['reads']) > MAX_RETAINED_READS:
                raise RunStateError('workspace retained reads exceed their finite bound')
            return value
        except HTTPError as error:
            error.close()
            raise RunStateError('workspace read authorization or route was rejected') from None
        except RunStateError:
            raise
        except Exception:
            raise RunStateError('workspace read transport or response failed') from None

    def _admit_write(self, role: str | None):
        """Admit the exact local Human or Bot actor without fabricated headers."""
        self._active()
        if role is None:
            first = self._human_identity()
            second = self._human_identity()
            if first != second:
                raise RunStateError('workspace local Human identity changed during write admission')
            return first, None
        entry = self.identities._entries[role]
        bot_id, token = entry
        for operation in ('visibility', 'friends', 'visibility'):
            value = self.identities._request(operation, bot_id, token)
            self._active()
            if operation == 'visibility':
                visibility(value, bot_id)
            elif not isinstance(value.get('data'), list) or len(value['data']) > 100:
                raise RunStateError('workspace caller self-read is invalid or oversized')
        if self.identities._entries[role] != entry:
            raise RunStateError('workspace role credential changed during write admission')
        return entry

    def _require_next(self, operation: str):
        self._active()
        index = len(self.state['operations']) if self.state is not None else MAX_WRITE_OPERATIONS
        if self.ledger is None or index >= MAX_WRITE_OPERATIONS or WRITE_SEQUENCE[index] != operation:
            raise RunStateError('workspace write is outside its finite ordered operation plan')

    def _require_write_scope(self, operation, method, path, body, role, expected_status):
        self._require_next(operation)
        if operation.startswith('fixture-'):
            expected_role = {'fixture-ceo-visibility-public': 'CEO',
                             'fixture-pm-visibility-public': 'PM',
                             'fixture-eng-visibility-public': 'ENG'}.get(operation)
            expected = ('PUT', '/bots/' + quote(self._id(expected_role), safe='') + '/visibility',
                        {'visibility': 'public'}, expected_role)
        else:
            primary = self.state['resources'].get('group-create-primary')
            if not primary:
                raise RunStateError('workspace write requires its confirmed primary Group')
            row = self.ledger._get(primary['handle'])
            if (row['resource_id'] != primary['id'] or row['origin'] != 'created'
                    or row['state'] != 'live' or row['kind'] != 'group'
                    or row['case_id'] != CASE_ID or row['attempt'] != 1):
                raise RunStateError('workspace primary Group ledger scope changed')
            base = '/groups/' + quote(primary['id'], safe='')
            plan = {
                'group-patch': ('PATCH', base, {'name': 'e2e-workspace-patched',
                                               'context': 'e2e-workspace-context'}, None),
                'group-member-add': ('POST', base+'/members', {'bot_uuid': self._id('PM')}, None),
                'group-member-remove': ('DELETE', base+'/members/'+quote(self._id('PM'), safe=''), {}, None),
                'group-label': ('PUT', base+'/label', {'label': 'e2e-workspace-label'}, 'CEO'),
                'group-visibility': ('PUT', base+'/visibility', {'visibility': 'public'}, None),
            }
            expected = plan.get(operation)
        if expected is None or (method, path, body, role) != expected or expected_status != 200:
            raise RunStateError('workspace mutation differs from its exact owned scope')

    def _write(self, operation: str, method: str, path: str, body: dict, *,
               role: str | None = None, expected_status: int = 200) -> dict:
        self._require_write_scope(operation, method, path, body, role, expected_status)
        actor_id, token = self._admit_write(role)
        request_id = 'e2e-workspace-' + uuid.uuid4().hex
        event = {
            'operation': operation,
            'role': role if role is not None else 'HUMAN',
            'actor_id': actor_id,
            'actor_contract': 'OWNED_LOCAL_BOT' if role is not None else 'BUILT_IN_LOCAL_HUMAN',
            'method': method,
            'path': path,
            'submitted_request_id': request_id,
            'status': 'INTENDED',
            'resource_binding': 'BOUND_OWNED_ROLE' if operation.startswith('fixture-') else 'BOUND_PRIMARY_GROUP',
        }
        self.state['operations'].append(event)
        self._save()  # Request identity and resource intent precede dispatch.
        self._active()
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-Request-ID': request_id,
        }
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(
            self.identities.origin + path, method=method,
            data=json.dumps(body, separators=(',', ':'), allow_nan=False).encode(),
            headers=headers,
        )
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                event['http_status'] = response.status
                record_response_id(event, response.headers.get('x-request-id'), source='HEADER',
                                   sensitive_values=((token,) if token is not None else ()))
                if response.status != expected_status:
                    raise RunStateError('workspace mutation returned an unexpected status')
                raw = response.read(65_537)
            self._active()
            if len(raw) > 65_536:
                raise RunStateError('workspace mutation response exceeds its byte budget')
            result = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if not isinstance(result, dict):
                raise RunStateError('workspace mutation response is not an object')
            event['value'] = retain_value(result)
            event['status'] = 'ACKNOWLEDGED'
            self._save()
            return result
        except BaseException as error:
            self._invalidated = True
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            if isinstance(error, HTTPError):
                event['http_status'] = error.code
                error.close()
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise RunStateError('workspace mutation failed; retain exact journal without retry') from None

    @staticmethod
    def _participant_ids(group: dict) -> list[str]:
        participants = group.get('participants')
        if (not isinstance(participants, list) or len(participants) > 20
                or any(not isinstance(row, dict) or not isinstance(row.get('bot_uuid'), str)
                       for row in participants)):
            raise RunStateError('workspace participant projection is invalid')
        ids = [row['bot_uuid'] for row in participants]
        if len(ids) != len(set(ids)):
            raise RunStateError('workspace participant projection contains duplicates')
        return ids

    def _set_actor_public(self, role: str):
        bot_id = self._id(role)
        token = self.identities._entries[role][1]
        original = visibility(self.identities._request('visibility', bot_id, token), bot_id)
        if original != 'protected':
            raise RunStateError('fresh workspace participant visibility is not protected')
        result = self._write(
            'fixture-' + role.lower() + '-visibility-public',
            'PUT', '/bots/' + quote(bot_id, safe='') + '/visibility',
            {'visibility': 'public'}, role=role,
        )
        if (result.get('success') is not True
                or result.get('data') != {'bot_uuid': bot_id, 'visibility': 'public'}
                or visibility(self.identities._request('visibility', bot_id, token), bot_id) != 'public'):
            raise RunStateError('owned workspace participant visibility was not exactly confirmed')
        self.state['fixture_visibility']['actors'].append({
            'role': role,
            'bot_id': bot_id,
            'original': original,
            'retained': 'public',
            'restoration': 'NOT_PERFORMED',
            'ownership': 'FRESH_OWNED_LOCAL_BOT',
        })
        self._save()

    def _create_group(self, operation: str, participants: tuple[str, ...]) -> dict:
        self._require_next(operation)
        expected = {'group-create-primary': ('CEO',), 'group-create-with-members': ('CEO', 'PM', 'ENG')}
        if expected.get(operation) != participants:
            raise RunStateError('workspace creation roles are outside the exact plan')
        request_id = 'e2e-workspace-' + uuid.uuid4().hex
        group_handle, session_handle = self.ledger.group_creation_intent(CASE_ID, 1, request_id)
        body = {
            'driver_bot': self._id('CEO'),
            'participants': [
                {'bot_uuid': self._id(role), 'role': None}
                for role in participants if role != 'CEO'
            ],
            'create_initial_session': True,
        }
        # Use a dedicated write implementation here so the submitted request ID
        # is identical to the ledger's pre-dispatch creation identity.
        actor_id, _ = self._admit_write(None)
        event = {
            'operation': operation,
            'role': 'HUMAN',
            'actor_id': actor_id,
            'actor_contract': 'BUILT_IN_LOCAL_HUMAN',
            'method': 'POST',
            'path': '/groups',
            'submitted_request_id': request_id,
            'status': 'INTENDED',
            'resource_binding': 'UNKNOWN',
            'handles': {'group': group_handle, 'initial_session': session_handle},
        }
        if (len(self.state['operations']) >= MAX_WRITE_OPERATIONS
                or any(item['operation'] == operation for item in self.state['operations'])):
            raise RunStateError('workspace group creation is outside its finite operation plan')
        self.state['operations'].append(event)
        self._save()
        request = Request(
            self.identities.origin + '/groups', method='POST',
            data=json.dumps(body, separators=(',', ':')).encode(),
            headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                     'X-Request-ID': request_id},
        )
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                event['http_status'] = response.status
                record_response_id(event, response.headers.get('x-request-id'), source='HEADER',
                                   sensitive_values=())
                if response.status != 200:
                    raise RunStateError('workspace Group creation was not confirmed')
                raw = response.read(65_537)
            self._active()
            if len(raw) > 65_536:
                raise RunStateError('workspace Group creation response exceeds its byte budget')
            result = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            event['value'] = retain_value(result)
            group_id = identifier(result.get('id'), 'workspace Group ID')
            session_id = identifier(result.get('session_id'), 'workspace initial Session ID')
            expected_ids = [self._id(role) for role in participants]
            if (result.get('driver_bot') != self._id('CEO') or (not isinstance(result.get('participants'), list)
                    or len(result['participants']) != len(expected_ids)
                    or any(not isinstance(value, str) for value in result['participants'])
                    or set(result['participants']) != set(expected_ids))
                    or not session_id.startswith(group_id + ':')):
                raise RunStateError('workspace Group creation response has mismatched identities')
            event['candidate_ids'] = {'group': group_id, 'initial_session': session_id}
            self._save()
            self.ledger.observed(group_handle, group_id, origin='created')
            self.ledger.observed(session_handle, session_id, origin='created')
            event.update(status='ACKNOWLEDGED', resource_binding='BOUND')
            resource = {
                'id': group_id,
                'handle': group_handle,
                'initial_session': {'id': session_id, 'handle': session_handle},
            }
            self.state['resources'][operation] = resource
            self._save()
            return resource
        except BaseException as error:
            self._invalidated = True
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            if isinstance(error, HTTPError):
                event['http_status'] = error.code
                error.close()
            try:
                self.ledger.unknown(group_handle)
                self.ledger.unknown(session_handle)
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise RunStateError('workspace Group creation failed; retain without retry or adoption') from None

    def _verify_storage(self, primary: dict, members: dict):
        self._active()
        expected_groups = {primary['id'], members['id']}
        with OwnedSqliteDatabase(self.runtime)._connection() as database:
            database.execute('BEGIN')
            groups = [dict(row) for row in database.execute(
                'SELECT group_id,label,status,driver_bot,originator,context,group_kind,visibility '
                "FROM bcs_groups WHERE env='local' AND group_id IN (?,?) ORDER BY group_id LIMIT 3",
                (primary['id'], members['id'])).fetchall()]
            participant_rows = [tuple(row) for row in database.execute(
                'SELECT group_id,bot_uuid FROM bcs_group_participants WHERE env=? AND group_id IN (?,?) ORDER BY group_id,bot_uuid LIMIT 5',
                ('local', primary['id'], members['id'])).fetchall()]
            sessions = [tuple(row) for row in database.execute(
                'SELECT session_id,group_id,status FROM bcs_group_sessions WHERE env=? AND group_id IN (?,?) ORDER BY group_id LIMIT 3',
                ('local', primary['id'], members['id'])).fetchall()]
        self._active()
        if (len(groups) != 2 or {row['group_id'] for row in groups} != expected_groups
                or any(row['driver_bot'] != self._id('CEO') or row['originator'] != self._id('CEO')
                       or row['status'] != 'active' or row['group_kind'] != 'normal'
                       for row in groups)):
            raise RunStateError('workspace stored Group inventory differs from the exact owned resources')
        primary_row = next(row for row in groups if row['group_id'] == primary['id'])
        if (primary_row['label'] != 'e2e-workspace-label'
                or primary_row['context'] != 'e2e-workspace-context'
                or primary_row['visibility'] != 'public'):
            raise RunStateError('workspace stored mutable Group fields differ from readback')
        expected_participants = {
            (primary['id'], self._id('CEO')),
            (members['id'], self._id('CEO')),
            (members['id'], self._id('PM')),
            (members['id'], self._id('ENG')),
        }
        if set(participant_rows) != expected_participants or len(participant_rows) != 4:
            raise RunStateError('workspace stored membership inventory is not exact')
        expected_sessions = {
            (primary['initial_session']['id'], primary['id'], 'running'),
            (members['initial_session']['id'], members['id'], 'running'),
        }
        if set(sessions) != expected_sessions or len(sessions) != 2:
            raise RunStateError('workspace initial Session inventory or parent binding is not exact')
        ledger = self.ledger.resource_snapshot()
        if (len(ledger) != 4 or [row['kind'] for row in ledger] != ['group', 'session', 'group', 'session']
                or any(row['origin'] != 'created' or row['state'] != 'live' for row in ledger)
                or any(row['parent'] != ledger[index - 1]['handle'] for index, row in enumerate(ledger)
                       if row['kind'] == 'session')):
            raise RunStateError('workspace Group/initial Session ledger is incomplete')
        self.state['storage_verification'] = {
            'status': 'PASS',
            'groups': 2,
            'initial_sessions': 2,
            'participant_rows': 4,
            'ledger_resources': 4,
        }
        self._save()

    def _assert(self, obligation: str, passed: bool):
        self.state['assertions'].append({'id': obligation, 'status': 'PASS' if passed else 'FAIL'})
        self._save()
        if not passed:
            raise RunStateError('workspace API business obligation failed')

    def _execute(self):
        self._set_actor_public('CEO')
        self._set_actor_public('PM')
        self._set_actor_public('ENG')
        primary = self._create_group('group-create-primary', ('CEO',))
        members = self._create_group('group-create-with-members', ('CEO', 'PM', 'ENG'))

        primary_detail = self._read('/groups/' + quote(primary['id'], safe=''))
        member_detail = self._read('/groups/' + quote(members['id'], safe=''))
        create_ok = (
            primary_detail.get('id') == primary['id']
            and self._participant_ids(primary_detail) == [self._id('CEO')]
            and member_detail.get('id') == members['id']
            and set(self._participant_ids(member_detail)) == {
                self._id('CEO'), self._id('PM'), self._id('ENG')}
            and len(self._participant_ids(member_detail)) == 3
        )
        self._assert('WORKSPACE.create', create_ok)
        detail_ok = (
            primary_detail.get('driver_bot') == self._id('CEO')
            and primary_detail.get('group_kind') == 'normal'
            and primary_detail.get('visibility') == 'private'
            and primary_detail.get('latest_running_session_id') == primary['initial_session']['id']
        )

        self._assert('WORKSPACE.detail', detail_ok)
        self._write('group-patch', 'PATCH', '/groups/' + quote(primary['id'], safe=''), {
            'name': 'e2e-workspace-patched',
            'context': 'e2e-workspace-context',
        })
        patched = self._read('/groups/' + quote(primary['id'], safe=''))
        patch_ok = (patched.get('id') == primary['id']
                    and patched.get('label') == 'e2e-workspace-patched'
                    and patched.get('context') == 'e2e-workspace-context')

        self._assert('WORKSPACE.patch', patch_ok)
        page = self._read('/groups?offset=0&limit=50&group_kind=all')
        items = page.get('items')
        matches = [row for row in items if isinstance(row, dict) and row.get('id') == primary['id']] \
            if isinstance(items, list) else []
        list_ok = (isinstance(items, list) and len(items) <= 50 and len(matches) == 1
                   and matches[0].get('driver_bot') == self._id('CEO')
                   and page.get('offset') == 0 and page.get('limit') == 50)

        self._assert('WORKSPACE.list', list_ok)
        self._write('group-member-add', 'POST',
                    '/groups/' + quote(primary['id'], safe='') + '/members',
                    {'bot_uuid': self._id('PM')})
        added = self._read('/groups/' + quote(primary['id'], safe=''))
        add_ok = added.get('id') == primary['id'] and set(self._participant_ids(added)) == {self._id('CEO'), self._id('PM')} \
            and len(self._participant_ids(added)) == 2
        if not add_ok:
            self._assert('WORKSPACE.members', False)
        self._write('group-member-remove', 'DELETE',
                    '/groups/' + quote(primary['id'], safe='') + '/members/'
                    + quote(self._id('PM'), safe=''), {})
        removed = self._read('/groups/' + quote(primary['id'], safe=''))
        members_ok = add_ok and removed.get('id') == primary['id'] and self._participant_ids(removed) == [self._id('CEO')]

        self._assert('WORKSPACE.members', members_ok)
        label_result = self._write('group-label', 'PUT',
                                   '/groups/' + quote(primary['id'], safe='') + '/label',
                                   {'label': 'e2e-workspace-label'}, role='CEO')
        labeled = self._read('/groups/' + quote(primary['id'], safe=''))
        label_ok = (label_result.get('updated') is True
                    and label_result.get('group_id') == primary['id']
                    and label_result.get('changed_by') == self._id('CEO')
                    and labeled.get('id') == primary['id']
                    and labeled.get('label') == 'e2e-workspace-label'
                    and labeled.get('context') == 'e2e-workspace-context')

        self._assert('WORKSPACE.label', label_ok)
        visibility_result = self._write('group-visibility', 'PUT',
                                        '/groups/' + quote(primary['id'], safe='') + '/visibility',
                                        {'visibility': 'public'})
        visible = self._read('/groups/' + quote(primary['id'], safe=''))
        visibility_ok = (visibility_result.get('updated') is True
                         and visibility_result.get('group_id') == primary['id']
                         and visibility_result.get('changed_by') == self._id('CEO')
                         and visible.get('id') == primary['id']
                         and visible.get('visibility') == 'public'
                         and visible.get('driver_bot') == self._id('CEO'))

        self._assert('WORKSPACE.visibility', visibility_ok)
        self._verify_storage(primary, members)

    def run(self) -> dict:
        self._active()
        if self.path.exists() or self.path.is_symlink() or self.ledger_path.exists() or self.ledger_path.is_symlink():
            raise RunStateError('workspace API requires fresh journal and resource ledger paths')
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.state = {
            'schema_version': 1,
            'contract': 'owned-local-workspace-api-v1',
            'case_id': CASE_ID,
            'run_id': self.runtime.run_id,
            'status': 'RUNNING',
            'binding_digest': self.binding_digest,
            'executor_digest': self.executor_digest,
            'fixture_digest': self.fixture_digest,
            'source_binding': self.source_binding,
            'fixture_set': 'workspace',
            'actor_contract': {
                'legacy_helpers': 'BUILT_IN_LOCAL_HUMAN',
                'label_mutation': 'OWNED_DRIVER_BOT',
                'mock_headers': False,
                'local_mock_auth': True,
            },
            'verified_roles': list(ROLES),
            'operations': [],
            'reads': [],
            'resources': {},
            'assertions': [],
            'storage_verification': {'status': 'NOT_RUN'},
            'fixture_visibility': {
                'status': 'RETAINED',
                'restoration': 'NOT_PERFORMED',
                'actors': [],
                'note': 'Only fresh owned CEO/PM/ENG Bots are public for participant and public-Group admission.',
            },
            'resource_disposition': 'RETAINED_LOCAL_DATABASE',
            'automatic_delete': False,
            'cleanup': 'NOT_RUN',
            'full_story_status': 'NOT_MIGRATED',
            'remaining_parent_obligations': {
                'status': 'NOT_RUN',
                'ids': list(REMAINING_PARENT_OBLIGATIONS),
            },
            'remote_execution': 'NOT_RUN',
            'raw_remote_profile': 'UNSUPPORTED',
            'shell_harness': 'NOT_USED',
            'ready_to_execute': False,
        }
        self._save()
        with Ledger(self.ledger_path, self.runtime.run_id, self.binding_digest, create=True) as ledger:
            self.ledger = ledger
            try:
                self._execute()
                if (len(self.state['operations']) != MAX_WRITE_OPERATIONS
                        or any(item['status'] != 'ACKNOWLEDGED' for item in self.state['operations'])
                        or self.state['assertions'] != [
                            {'id': obligation, 'status': 'PASS'} for obligation in OBLIGATIONS]
                        or set(self.state['resources']) != {
                            'group-create-primary', 'group-create-with-members'}):
                    raise RunStateError('workspace API evidence is incomplete or out of order')
                self.state['status'] = 'LOCAL_WORKSPACE_API_PASS'
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
                raise RunStateError('workspace API slice failed; inspect retained local artifacts') from None
