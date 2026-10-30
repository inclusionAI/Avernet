"""Actual product-CLI increment for four owned-local Workspace obligations.

The caller has already completed :class:`OwnedLocalWorkspaceApi`, prepared the
bounded PM/ENG fusion files, and still owns the same BCS process and role
credentials.  This slice creates two additional Groups and their default
Sessions.  It never deletes a Group or Session, retries an unknown write, uses a
remote route, or treats the legacy unauthenticated fuse wire as gateway proof.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import uuid
from urllib.parse import quote
from urllib.request import Request

from .bounded_process import capture
from .correlation import record_response_id
from .errors import has_cancellation
from .inventory import executor_digest
from .ledger import Ledger
from .local_cli import require_cli_binary
from .local_cli_exchange import LocalCliExchange
from .local_role_identity import OwnedLocalRoleIdentity
from .local_sqlite import OwnedSqliteDatabase
from .local_workspace_api import OBLIGATIONS as API_OBLIGATIONS, workspace_source_binding
from .local_workspace_context import FUSION_CONTEXT, verify_workspace_context
from .profile import fingerprint, identifier, unique_keys
from .run_state import RunStateError, read, write
from .trust_baseline import visibility
from .workspace_retention import retain_text, retain_value

CASE_ID = 'LOCAL-WORKSPACE-CLI-01'
OBLIGATIONS = (
    'WORKSPACE.cli-create-get',
    'WORKSPACE.cli-add-member',
    'WORKSPACE.cli-fuse',
    'WORKSPACE.cli-status',
)
ROLES = ('CEO', 'PM', 'ENG', 'QA')
COMMAND_SEQUENCE = (
    'create-primary',
    'get-primary',
    'add-member',
    'get-after-add',
    'fuse',
    'status-primary',
    'get-after-status',
    'create-termination',
    'get-before-terminate',
    'terminate',
    'get-termination',
)
QUESTION = 'how to align?'


class OwnedLocalWorkspaceCli:
    """Run the four exact CLI obligations inside one caller-owned lifecycle."""

    def __init__(self, identities, binary, binary_sha256):
        if (type(identities) is not OwnedLocalRoleIdentity
                or identities.fixture_set != 'workspace'
                or tuple(identities.roles) != ROLES
                or set(identities._verified) != set(ROLES)):
            raise RunStateError(
                'Workspace CLI requires freshly verified CEO/PM/ENG/QA owned-local identities')
        identities._require_roles(ROLES)
        require_cli_binary(binary, binary_sha256)
        self.identities = identities
        self.runtime = identities.runtime
        self.binary = binary.resolve()
        self.binary_sha256 = binary_sha256
        self.path = self.runtime.root/'local-workspace-cli.json'
        self.ledger_path = self.runtime.root/'workspace-cli-resources.sqlite'
        self.executor_digest = executor_digest()
        self.fixture_digest = fingerprint(read(identities.fixtures.path))
        self.source_binding = workspace_source_binding()
        self.api_journal = self._verified_api_journal()
        self.api_journal_digest = fingerprint(self.api_journal)
        self.context_receipt = verify_workspace_context(identities)
        self.context_digest = fingerprint(self.context_receipt)
        self.binding_digest = fingerprint({
            'run_id': self.runtime.run_id,
            'executor_digest': self.executor_digest,
            'fixture_digest': self.fixture_digest,
            'source_binding': self.source_binding,
            'api_journal_digest': self.api_journal_digest,
            'context_digest': self.context_digest,
            'cli_sha256': binary_sha256,
        })
        self.state = None
        self.ledger = None
        self.digest = ''
        self.invalidated = False

    def _verified_api_journal(self):
        journal = read(self.runtime.root/'local-workspace-api.json')
        actors = journal.get('fixture_visibility', {}).get('actors')
        if (journal.get('status') != 'LOCAL_WORKSPACE_API_PASS'
                or journal.get('case_id') != 'LOCAL-WORKSPACE-API-01'
                or journal.get('run_id') != self.runtime.run_id
                or journal.get('executor_digest') != self.executor_digest
                or journal.get('fixture_digest') != self.fixture_digest
                or journal.get('source_binding') != self.source_binding
                or journal.get('assertions') != [
                    {'id': value, 'status': 'PASS'} for value in API_OBLIGATIONS]
                or not isinstance(actors, list)
                or [(row.get('role'), row.get('retained')) for row in actors
                    if isinstance(row, dict)] != [('CEO', 'public'), ('PM', 'public'), ('ENG', 'public')]
                or journal.get('resource_disposition') != 'RETAINED_LOCAL_DATABASE'
                or journal.get('automatic_delete') is not False):
            raise RunStateError('Workspace CLI requires the exact successful API journal binding')
        for role, expected in (('CEO', 'public'), ('PM', 'public'), ('ENG', 'public'), ('QA', 'protected')):
            bot_id, token = self.identities._entries[role]
            if visibility(self.identities._request('visibility', bot_id, token), bot_id) != expected:
                raise RunStateError('Workspace API fixture visibility binding changed before CLI execution')
        return journal

    def _journal_valid(self):
        if self.digest:
            try:
                valid = fingerprint(read(self.path)) == self.digest
            except Exception:
                valid = False
            if not valid:
                self.invalidated = True
                raise RunStateError('Workspace CLI journal changed; preserve the original artifact')

    def _save(self):
        self._journal_valid()
        write(self.path, self.state)
        self.digest = fingerprint(self.state)

    def _active(self):
        if self.invalidated:
            raise RunStateError('Workspace CLI mutation authority is invalidated')
        self.identities._require_roles(ROLES)
        require_cli_binary(self.binary, self.binary_sha256)
        if (executor_digest() != self.executor_digest
                or fingerprint(read(self.identities.fixtures.path)) != self.fixture_digest
                or fingerprint(workspace_source_binding()) != fingerprint(self.source_binding)
                or fingerprint(read(self.runtime.root/'local-workspace-api.json')) != self.api_journal_digest
                or fingerprint(verify_workspace_context(self.identities)) != self.context_digest):
            raise RunStateError('Workspace CLI source, API, fixture, or fusion binding changed')
        self._journal_valid()

    def _id(self, role):
        self._active()
        return self.identities._entries[role][0]

    def _admit_pm(self):
        self._active()
        entry = self.identities._entries['PM']
        bot_id, token = entry
        for operation in ('visibility', 'friends', 'visibility'):
            value = self.identities._request(operation, bot_id, token)
            self._active()
            if operation == 'visibility':
                if visibility(value, bot_id) != 'public':
                    raise RunStateError('Workspace CLI PM is no longer public')
            elif not isinstance(value.get('data'), list) or len(value['data']) > 100:
                raise RunStateError('Workspace CLI PM self-read is invalid or oversized')
        if self.identities._entries['PM'] != entry:
            raise RunStateError('Workspace CLI PM credential changed during admission')
        return entry

    def _set_qa_public(self):
        self._active()
        event = self.state['qa_visibility']
        if event != {'status': 'NOT_ATTEMPTED'}:
            raise RunStateError('Workspace CLI QA admission cannot be repeated')
        qa, token = self.identities._entries['QA']
        if visibility(self.identities._request('visibility', qa, token), qa) != 'protected':
            raise RunStateError('Workspace CLI QA is not the retained protected API fixture')
        request_id = 'e2e-workspace-cli-qa-' + uuid.uuid4().hex
        event.update(operation='fixture-qa-visibility-public', method='PUT',
                     path='/bots/' + quote(qa, safe='') + '/visibility',
                     submitted_request_id=request_id, status='INTENDED',
                     resource_binding='BOUND_OWNED_QA', authentication='OWNED_LOCAL_QA_BOT')
        self._save()
        request = Request(
            self.identities.origin + event['path'], method='PUT', data=b'{"visibility":"public"}',
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                     'Content-Type': 'application/json', 'X-Request-ID': request_id})
        try:
            with self.identities.opener.open(request, timeout=5) as response:
                event['http_status'] = response.status
                record_response_id(event, response.headers.get('x-request-id'), source='HEADER',
                                   sensitive_values=(token,))
                raw = response.read(65_537)
            self._active()
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
            if (len(raw) > 65_536 or event['http_status'] != 200
                    or value.get('success') is not True
                    or value.get('data') != {'bot_uuid': qa, 'visibility': 'public'}
                    or visibility(self.identities._request('visibility', qa, token), qa) != 'public'):
                raise RunStateError('Workspace CLI QA public admission was not exactly confirmed')
            event.update(status='ACKNOWLEDGED', original='protected', retained='public',
                         restoration='NOT_PERFORMED')
            self._save()
        except BaseException as error:
            self.invalidated = True
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            try:
                self._save()
            except BaseException:
                pass
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            raise RunStateError('Workspace CLI QA admission is unknown; retain without retry') from None

    def _expected(self, operation):
        pm, eng, qa = self._id('PM'), self._id('ENG'), self._id('QA')
        participants = [
            {'bot_uuid': pm, 'role': None},
            {'bot_uuid': eng, 'role': None},
        ]
        create_body = {'id': None, 'label': None, 'driver_bot': pm,
                       'participants': participants, 'create_initial_session': True}
        primary = self.state['resources'].get('primary')
        termination = self.state['resources'].get('termination')
        if operation == 'create-primary':
            return ('POST', '/groups', create_body | {'topic': 'e2e-workspace-cli-primary'},
                    ['create-group', '--driver', pm, '--participants', pm + ',' + eng,
                     '--topic', 'e2e-workspace-cli-primary'], 200, 'bearer')
        if operation == 'create-termination':
            return ('POST', '/groups', create_body | {'topic': 'e2e-workspace-cli-termination'},
                    ['create-group', '--driver', pm, '--participants', pm + ',' + eng,
                     '--topic', 'e2e-workspace-cli-termination'], 200, 'bearer')
        if operation in ('get-primary', 'add-member', 'get-after-add', 'fuse',
                          'status-primary', 'get-after-status') and primary is None:
            raise RunStateError('Workspace CLI primary Group is not bound')
        if operation in ('get-before-terminate', 'terminate', 'get-termination') and termination is None:
            raise RunStateError('Workspace CLI termination Group is not bound')
        if operation in ('get-primary', 'get-after-add', 'get-after-status'):
            gid = primary['id']
            return ('GET', '/groups/' + quote(gid, safe=''), None,
                    ['get-group', '--id', gid], 200, 'group-get-no-auth')
        if operation == 'add-member':
            gid = primary['id']
            return ('POST', '/groups/' + quote(gid, safe='') + '/members', {'bot_uuid': qa},
                    ['add-member', '--group', gid, '--bot-uuid', qa], 200, 'bearer')
        if operation == 'fuse':
            gid = primary['id']
            ids = [pm, eng]
            return ('POST', '/groups/' + quote(gid, safe='') + '/fuse',
                    {'question': QUESTION, 'participants': ids, 'focus': None,
                     'session_id': gid, 'fusion_mode': None},
                    ['fuse', '--group', gid, '--question', QUESTION,
                     '--participants', ','.join(ids)], 200, 'fuse-no-auth')
        if operation == 'status-primary':
            gid = primary['id']
            return ('PUT', '/groups/' + quote(gid, safe='') + '/status',
                    {'status': 'completed', 'reason': 'e2e workspace CLI completed'},
                    ['group-status', '--group', gid, '--status', 'completed',
                     '--reason', 'e2e workspace CLI completed'], 200, 'bearer')
        if operation == 'terminate':
            gid = termination['id']
            return ('POST', '/groups/' + quote(gid, safe='') + '/terminate', None,
                    ['terminate-group', '--group', gid], 200, 'bearer')
        if operation in ('get-before-terminate', 'get-termination'):
            gid = termination['id']
            return ('GET', '/groups/' + quote(gid, safe=''), None,
                    ['get-group', '--id', gid], 200, 'group-get-no-auth')
        raise RunStateError('Workspace CLI operation is not in the finite plan')

    def _require_scope(self, operation, method, path, body, args, expected_status, auth_mode):
        self._active()
        previous = [item['operation'] for item in self.state['operations']]
        index = len(previous)
        if (index >= len(COMMAND_SEQUENCE) or previous != list(COMMAND_SEQUENCE[:index])
                or operation != COMMAND_SEQUENCE[index]
                or (method, path, body, args, expected_status, auth_mode) != self._expected(operation)):
            raise RunStateError('Workspace CLI command differs from its finite ordered operation plan')
        if not operation.startswith('create-'):
            name = ('termination' if operation in
                    ('get-before-terminate', 'terminate', 'get-termination') else 'primary')
            resource = self.state['resources'][name]
            row = self.ledger._get(resource['handle'])
            if (row['kind'] != 'group' or row['origin'] != 'created' or row['state'] != 'live'
                    or row['case_id'] != CASE_ID or row['attempt'] != 1
                    or row['resource_id'] != resource['id']):
                raise RunStateError('Workspace CLI existing Group ledger scope changed')

    def _context(self, index, token, origin):
        root = self.runtime.root/('workspace-cli-command-%02d-%s' % (index, uuid.uuid4().hex))
        root.mkdir(mode=0o700)
        for name in ('home', 'bot', 'tmp'):
            (root/name).mkdir(mode=0o700)
        (root/'bot'/'.bcs').mkdir(mode=0o700)
        context = root/'bot'/'.bcs'/'session.json'
        directories = {item: (item.stat().st_dev, item.stat().st_ino)
                       for item in (root, root/'bot', context.parent)}
        credential = {'schema_version': 1, 'token': token,
                      'bot_uuid': self._id('PM'), 'api_base_url': origin}

        def require(expected=None):
            for item, inode in directories.items():
                info = item.stat()
                if item.is_symlink() or not item.is_dir() or (info.st_dev, info.st_ino) != inode:
                    raise RunStateError('Workspace CLI private credential directory changed')
            if context.is_symlink() or (context.exists() and not context.is_file()):
                raise RunStateError('Workspace CLI credential target is not a regular file')
            if expected is not None and fingerprint(read(context)) != fingerprint(expected):
                raise RunStateError('Workspace CLI private credential context changed')
        environment = {'PATH': '/usr/bin:/bin', 'HOME': str(root/'home'),
                       'BOT_DATA_DIR': str(root/'bot'), 'TMPDIR': str(root/'tmp'),
                       'LANG': 'C', 'SERVER_ENV': 'local', 'tc_sdb_nenv': 'production'}
        return root, context, environment, credential, require

    def _invoke(self, operation, method, path, body, args, expected_status, auth_mode):
        # This rejects foreign targets, bodies, duplicates and order drift before
        # journal intent or the creation ledger can be written.
        self._require_scope(operation, method, path, body, args, expected_status, auth_mode)
        pm, token = self._admit_pm()
        request_id = 'e2e-workspace-cli-' + uuid.uuid4().hex
        handles = None
        if operation.startswith('create-'):
            handles = self.ledger.group_creation_intent(CASE_ID, 1, request_id)
        event = {'operation': operation, 'method': method, 'path': path,
                 'submitted_request_id': request_id, 'status': 'INTENDED',
                 'resource_binding': 'UNKNOWN' if handles else 'BOUND_EXISTING_GROUP',
                 'credential_cleanup': 'NOT_YET_OBSERVED',
                 'actor_id': pm,
                 'actor_contract': ('OWNED_LOCAL_PM_CONTEXT_NO_WIRE_IDENTITY'
                                    if auth_mode != 'bearer' else 'OWNED_LOCAL_PM_BOT')}
        if handles:
            event['handles'] = {'group': handles[0], 'initial_session': handles[1]}
        self.state['operations'].append(event)
        self._save()
        exchange_token = token if auth_mode == 'bearer' else ''
        exchange_options = ({'auth_contract': {
            'fuse-no-auth': 'owned-local-legacy-fuse-no-auth-v1',
            'group-get-no-auth': 'owned-local-legacy-group-get-no-auth-v1',
        }[auth_mode]} if auth_mode != 'bearer' else {})
        context = credential = require_context = None
        try:
            with LocalCliExchange(self, event, token=exchange_token, body=body,
                                  **exchange_options) as exchange:
                # The legacy no-auth Group reads/fuse contract requires an empty
                # wire credential: the product CLI auto-discovers a non-empty
                # session token and would send Bearer, which the exchange
                # admission correctly rejects for an unauthenticated operation.
                root, context, environment, credential, require_context = self._context(
                    len(self.state['operations']), exchange_token, exchange.origin)
                write(context, credential)
                require_context(credential)
                result = capture([str(self.binary), '--json', '--log-level', 'error', *args],
                                 cwd=root, environment=environment, timeout_seconds=12,
                                 max_output_bytes=65_536)
            self._active()
            require_context(credential)
            if (exchange.failure is not None or exchange.attempts != 1 or exchange.forwarded != 1
                    or result.returncode != 0 or event.get('http_status') != expected_status
                    or not isinstance(exchange.response, dict)):
                raise RunStateError('Workspace CLI product command was not exactly confirmed')
            event['cli_exit_code'] = result.returncode
            event['wire_authentication'] = {'fuse-no-auth': 'NONE_LEGACY_LOCAL_FUSE',
                                            'group-get-no-auth': 'NONE_LEGACY_LOCAL_GROUP_GET',
                                            'bearer': 'OWNED_LOCAL_PM_BEARER'}[auth_mode]
            reply = exchange.response
            output = result.stdout.decode('utf-8')
            event['value'] = retain_value(reply)
            event['stdout'], event['stdout_truncated'] = retain_text(output)
            self._accept_output(operation, reply, output, handles, request_id)
            event.update(status='ACKNOWLEDGED', resource_binding='BOUND')
            self._save()
            return reply
        except BaseException as error:
            self.invalidated = True
            event['status'] = 'UNKNOWN'
            event['error_type'] = type(error).__name__
            if handles:
                try:
                    self.ledger.unknown(handles[0]); self.ledger.unknown(handles[1])
                except BaseException:
                    pass
            if getattr(error, 'child_shutdown_status', None) == 'UNCONFIRMED':
                event['child_shutdown_status'] = 'UNCONFIRMED'
            if has_cancellation(error):
                raise KeyboardInterrupt() from None
            failure = RunStateError('Workspace CLI outcome unconfirmed; retain without retry')
            if event.get('child_shutdown_status') == 'UNCONFIRMED':
                failure.child_shutdown_status = 'UNCONFIRMED'
            raise failure from None
        finally:
            try:
                if credential is not None:
                    require_context()  # Recheck directory ownership before clearing partial writes.
                    cleared = {'schema_version': 1, 'token': '',
                               'api_base_url': self.identities.origin}
                    write(context, cleared)
                    require_context(cleared)
                event['credential_cleanup'] = 'CLEARED' if credential is not None else 'NOT_WRITTEN'
            except BaseException:
                self.invalidated = True
                event['credential_cleanup'] = 'UNCONFIRMED'
                raise
            finally:
                self._save()

    def _accept_output(self, operation, reply, output, handles, request_id):
        if operation.startswith('create-'):
            group_id = identifier(reply.get('id'), 'Workspace CLI Group ID')
            session_id = identifier(reply.get('session_id'), 'Workspace CLI initial Session ID')
            expected_ids = [self._id('PM'), self._id('ENG')]
            if (re.findall(r'^  ID: (.+)$', output, re.M) != [group_id]
                    or re.findall(r'^  Session: (.+)$', output, re.M) != [session_id]
                    or reply.get('driver_bot') != self._id('PM')
                    or not isinstance(reply.get('participants'), list)
                    or len(reply['participants']) != 2 or set(reply['participants']) != set(expected_ids)
                    or not session_id.startswith(group_id + ':')):
                raise RunStateError('Workspace CLI create output or response differs from the product contract')
            self.ledger.observed(handles[0], group_id, origin='created', actual_request_id=request_id)
            self.ledger.observed(handles[1], session_id, origin='created')
            name = 'primary' if operation == 'create-primary' else 'termination'
            self.state['resources'][name] = {
                'id': group_id, 'handle': handles[0],
                'initial_session': {'id': session_id, 'handle': handles[1]},
            }
            return
        if operation in ('get-primary', 'get-after-add', 'get-after-status',
                         'get-before-terminate', 'get-termination', 'status-primary', 'terminate'):
            parsed = json.loads(output, object_pairs_hook=unique_keys)
            if fingerprint(parsed) != fingerprint(reply):
                raise RunStateError('Workspace CLI JSON output differs from the forwarded product response')
        if operation in ('get-primary', 'get-after-add', 'get-after-status'):
            expected = self.state['resources']['primary']
            expected_members = ({self._id('PM'), self._id('ENG')} if operation == 'get-primary' else
                                {self._id('PM'), self._id('ENG'), self._id('QA')})
            expected_status = 'completed' if operation == 'get-after-status' else 'active'
            if (reply.get('id') != expected['id'] or reply.get('driver_bot') != self._id('PM')
                    or reply.get('status') != expected_status
                    or set(self._participant_ids(reply)) != expected_members):
                raise RunStateError('Workspace CLI primary get response is not exact')
        elif operation == 'add-member':
            expected = ('✓ Member added to group:\n  Group: ' + self.state['resources']['primary']['id']
                        + '\n  Bot: ' + self._id('QA') + '\n')
            member = reply.get('member')
            role = member.get('role') if isinstance(member, dict) else None
            if role is not None:
                expected += '  Role: ' + role + '\n'
            if (output != expected or reply.get('added') is not True
                    or not isinstance(member, dict) or member.get('bot_uuid') != self._id('QA')):
                raise RunStateError('Workspace CLI add-member output or response is not exact')
        elif operation == 'fuse':
            if not output.startswith('Fusion result:\n'):
                raise RunStateError('Workspace CLI fuse output prefix changed')
            parsed = json.loads(output[len('Fusion result:\n'):], object_pairs_hook=unique_keys)
            perspectives = reply.get('perspectives')
            by_id = {row.get('bot_uuid'): row for row in perspectives if isinstance(row, dict)} \
                if isinstance(perspectives, list) else {}
            markers = {role: [text.strip() for text in FUSION_CONTEXT[role].values()]
                       for role in ('PM', 'ENG')}
            if (fingerprint(parsed) != fingerprint(reply)
                    or reply.get('recommendation') != 'Simple fusion for: ' + QUESTION
                    or not isinstance(perspectives, list) or len(perspectives) != 2
                    or set(by_id) != {self._id('PM'), self._id('ENG')}
                    or any(not isinstance(by_id[self._id(role)].get('summary'), str)
                           or not all(marker in by_id[self._id(role)]['summary'] for marker in markers[role])
                           for role in ('PM', 'ENG'))):
                raise RunStateError('Workspace CLI fuse did not use both configured product contexts')
        elif operation == 'status-primary':
            if (reply.get('updated') is not True
                    or reply.get('group_id') != self.state['resources']['primary']['id']
                    or reply.get('status') != 'completed'
                    or reply.get('changed_by') != self._id('PM')):
                raise RunStateError('Workspace CLI group-status response is not exact')
        elif operation == 'terminate':
            if (reply.get('terminated') is not True
                    or reply.get('group_id') != self.state['resources']['termination']['id']
                    or reply.get('status') != 'completed'
                    or reply.get('terminated_by') != self._id('PM')):
                raise RunStateError('Workspace CLI terminate response is not exact')
        elif operation in ('get-before-terminate', 'get-termination'):
            expected_status = 'active' if operation == 'get-before-terminate' else 'completed'
            if (reply.get('id') != self.state['resources']['termination']['id']
                    or reply.get('status') != expected_status
                    or reply.get('driver_bot') != self._id('PM')):
                raise RunStateError('Workspace CLI independent termination Group get is not exact')

    @staticmethod
    def _participant_ids(group):
        participants = group.get('participants')
        if (not isinstance(participants, list) or len(participants) > 20
                or any(not isinstance(row, dict) or not isinstance(row.get('bot_uuid'), str)
                       for row in participants)):
            raise RunStateError('Workspace CLI participant projection is invalid')
        ids = [row['bot_uuid'] for row in participants]
        if len(ids) != len(set(ids)):
            raise RunStateError('Workspace CLI participant projection contains duplicates')
        return ids

    def _assert(self, obligation, passed):
        self.state['assertions'].append({'id': obligation, 'status': 'PASS' if passed else 'FAIL'})
        self._save()
        if not passed:
            raise RunStateError('Workspace CLI business obligation failed')

    def _execute(self):
        self._set_qa_public()
        first = self._expected('create-primary')
        self._invoke('create-primary', *first)
        detail = self._invoke('get-primary', *self._expected('get-primary'))
        self._assert('WORKSPACE.cli-create-get',
                     detail.get('latest_running_session_id') == self.state['resources']['primary']['initial_session']['id'])
        self._invoke('add-member', *self._expected('add-member'))
        added = self._invoke('get-after-add', *self._expected('get-after-add'))
        self._assert('WORKSPACE.cli-add-member',
                     set(self._participant_ids(added)) == {self._id('PM'), self._id('ENG'), self._id('QA')}
                     and len(self._participant_ids(added)) == 3)
        self._invoke('fuse', *self._expected('fuse'))
        self._assert('WORKSPACE.cli-fuse', True)
        self._invoke('status-primary', *self._expected('status-primary'))
        completed = self._invoke('get-after-status', *self._expected('get-after-status'))
        self._invoke('create-termination', *self._expected('create-termination'))
        active = self._invoke('get-before-terminate', *self._expected('get-before-terminate'))
        self._invoke('terminate', *self._expected('terminate'))
        final = self._invoke('get-termination', *self._expected('get-termination'))
        self._verify_storage()
        self._assert('WORKSPACE.cli-status',
                     completed.get('status') == 'completed'
                     and active.get('status') == 'active'
                     and final.get('status') == 'completed')

    def _verify_storage(self):
        self._active()
        resources = self.state['resources']
        group_ids = [resources[name]['id'] for name in ('primary', 'termination')]
        session_ids = [resources[name]['initial_session']['id'] for name in ('primary', 'termination')]
        with OwnedSqliteDatabase(self.runtime)._connection() as database:
            database.execute('BEGIN')
            groups = [tuple(row) for row in database.execute(
                "SELECT group_id,driver_bot,originator,status FROM bcs_groups WHERE env='local' "
                'AND group_id IN (?,?) ORDER BY group_id LIMIT 3', group_ids).fetchall()]
            sessions = [tuple(row) for row in database.execute(
                "SELECT session_id,group_id,status FROM bcs_group_sessions WHERE env='local' "
                'AND session_id IN (?,?) ORDER BY session_id LIMIT 3', session_ids).fetchall()]
        self._active()
        if (len(groups) != 2 or {row[0] for row in groups} != set(group_ids)
                or any(row[1] != self._id('PM') or row[2] != self._id('PM') or row[3] != 'completed'
                       for row in groups)
                or set(sessions) != {
                    (resources['primary']['initial_session']['id'], resources['primary']['id'], 'running'),
                    (resources['termination']['initial_session']['id'], resources['termination']['id'], 'running'),
                } or len(sessions) != 2):
            raise RunStateError('Workspace CLI stored Group/default Session state is not exact')
        rows = self.ledger.resource_snapshot()
        if (len(rows) != 4 or [row['kind'] for row in rows] != ['group', 'session', 'group', 'session']
                or any(row['case_id'] != CASE_ID or row['attempt'] != 1
                       or row['origin'] != 'created' or row['state'] != 'live' for row in rows)
                or {row['resource_id'] for row in rows if row['kind'] == 'group'} != set(group_ids)
                or {row['resource_id'] for row in rows if row['kind'] == 'session'} != set(session_ids)
                or rows[1]['parent'] != rows[0]['handle'] or rows[3]['parent'] != rows[2]['handle']):
            raise RunStateError('Workspace CLI resource ledger is incomplete')
        self.state['storage_verification'] = {
            'status': 'PASS', 'groups': 2, 'default_initial_sessions': 2,
            'group_status': 'completed', 'session_status': 'running', 'ledger_resources': 4,
        }
        self._save()

    def run(self):
        self._active()
        if (self.path.exists() or self.path.is_symlink()
                or self.ledger_path.exists() or self.ledger_path.is_symlink()):
            raise RunStateError('Workspace CLI requires fresh journal and resource ledger paths')
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.state = {
            'schema_version': 1, 'contract': 'owned-local-workspace-cli-v1',
            'case_id': CASE_ID, 'run_id': self.runtime.run_id, 'status': 'RUNNING',
            'executor_digest': self.executor_digest, 'fixture_digest': self.fixture_digest,
            'source_binding': self.source_binding, 'api_journal_digest': self.api_journal_digest,
            'context_receipt': self.context_receipt, 'context_digest': self.context_digest,
            'cli_binary_sha256': self.binary_sha256, 'binding_digest': self.binding_digest,
            'verified_roles': list(ROLES), 'operations': [], 'resources': {}, 'assertions': [],
            'qa_visibility': {'status': 'NOT_ATTEMPTED'},
            'fusion_authentication': {
                'wire': 'NONE', 'contract': 'owned-local-legacy-fuse-no-auth-v1',
                'scope': 'LOCAL_LOOPBACK_PRODUCT_LEGACY_ROUTE_ONLY',
                'remote_or_gateway_authentication': 'NOT_PROVEN',
                'cli_context_credential': 'OWNED_LOCAL_PM_TOKEN',
            },
            'group_read_authentication': {'wire': 'NONE',
                'contract': 'owned-local-legacy-group-get-no-auth-v1',
                'scope': 'LOCAL_LOOPBACK_PRODUCT_LEGACY_ROUTE_ONLY',
                'remote_or_gateway_authentication': 'NOT_PROVEN'},
            'storage_verification': {'status': 'NOT_RUN'},
            'resource_disposition': 'RETAINED_LOCAL_DATABASE', 'automatic_delete': False,
            'group_delete': 'NOT_RUN', 'session_delete': 'NOT_RUN', 'broad_stop': 'NOT_USED',
            'full_story_status': 'NOT_MIGRATED', 'remote_execution': 'NOT_RUN',
            'ready_to_execute': False,
        }
        self._save()
        with Ledger(self.ledger_path, self.runtime.run_id, self.binding_digest, create=True) as ledger:
            self.ledger = ledger
            try:
                self._execute()
                if (len(self.state['operations']) != len(COMMAND_SEQUENCE)
                        or [row['operation'] for row in self.state['operations']] != list(COMMAND_SEQUENCE)
                        or any(row['status'] != 'ACKNOWLEDGED'
                               or row['credential_cleanup'] != 'CLEARED'
                               for row in self.state['operations'])
                        or self.state['assertions'] != [
                            {'id': value, 'status': 'PASS'} for value in OBLIGATIONS]
                        or set(self.state['resources']) != {'primary', 'termination'}
                        or self.state['qa_visibility'].get('status') != 'ACKNOWLEDGED'):
                    raise RunStateError('Workspace CLI evidence is incomplete or out of order')
                self.state['status'] = 'LOCAL_WORKSPACE_CLI_PASS'
                self._save()
                return json.loads(json.dumps(self.state, allow_nan=False))
            except BaseException as error:
                self.invalidated = True
                cancelled = has_cancellation(error)
                self.state['status'] = 'CANCELLED' if cancelled else 'BLOCKED'
                try:
                    self._save()
                except BaseException:
                    pass
                if cancelled:
                    raise KeyboardInterrupt() from None
                raise RunStateError('Workspace CLI slice failed; inspect retained local artifacts') from None
