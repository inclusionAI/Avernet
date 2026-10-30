"""Business deletion admitted only by live, foreground-owned local execution.

This is NOT a remote quiescence proof or a claim that a retained Session was
completed. No engine was launched; no Bot output is synthesized. The exact
owned Group is removed via the product API, never by SQL or broad cleanup.
"""
from __future__ import annotations

import json
import os
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request

from .errors import has_cancellation
from .ledger import Ledger
from .local_bot_receiver import OwnedLocalBotReceiver
from .local_sqlite import OwnedSqliteDatabase
from .local_workspace_messages import OwnedLocalWorkspaceMessages
from .profile import fingerprint, unique_keys
from .run_state import RunStateError, read, write
from .workspace_retention import retain_value

CASE_ID = 'LOCAL-WORKSPACE-DELETE-01'
OBLIGATIONS = ('WORKSPACE.incident-delete',)


class OwnedLocalWorkspaceDelete:
    case_id = CASE_ID
    obligation = OBLIGATIONS[0]
    success_status = 'LOCAL_WORKSPACE_DELETE_PASS'

    def __init__(self, message_execution):
        if type(message_execution) is not OwnedLocalWorkspaceMessages or not message_execution._completed:
            raise RunStateError('Incident deletion requires the live completed owned message execution')
        self.message = message_execution
        self.scope = message_execution.scope
        self.identities, self.runtime = message_execution.identities, message_execution.runtime
        self.group_id, self.session_id = message_execution.group_id, message_execution.session_id
        self.path = self.runtime.root/'local-workspace-delete.json'
        self.ledger_path = self.scope.ledger_path
        self.binding_digest = self.scope.binding_digest
        self.resource = message_execution.resource
        self.state = None
        self._digest = ''
        self._attempted = False

    def _save(self):
        if self._digest and fingerprint(read(self.path)) != self._digest:
            raise RunStateError('Incident deletion journal changed; preserve original')
        write(self.path, self.state)
        self._digest = fingerprint(self.state)

    def _admit(self):
        self.message._active()
        return self._closed_local_execution()

    def _closed_local_execution(self):
        self.message.scope._active()
        if fingerprint(read(self.message.path)) != self.message._digest:
            raise RunStateError('Completed message evidence changed')
        if (self.message.state.get('status') != 'LOCAL_WORKSPACE_MESSAGES_PASS'
                or self.message.state.get('receiver_state') != 'CLOSED'
                or self.message.state.get('storage_verification', {}).get('new_rows') != 3
                or set(self.message._receivers) != {'CEO', 'PM', 'ENG'}
                or any(type(receiver) is not OwnedLocalBotReceiver
                       or receiver.identities is not self.identities
                       or receiver.summary()['state'] != 'CLOSED'
                       or receiver._connection is not None or receiver._token
                       for receiver in self.message._receivers.values())):
            raise RunStateError('Incident local receiver execution is not closed')
        config = json.loads(self.runtime.inputs.config_path.read_text(), object_pairs_hook=unique_keys)
        if (config.get('eventing', {}).get('enabled') is not False
                or config.get('eventing', {}).get('dispatcher_enabled') is not False
                or config.get('collaboration', {}).get('loop_execution_enabled') is not False
                or config.get('llm', {}).get('type') != 'none'
                or config.get('human_notify', {}).get('providers') != []
                or config.get('security', {}).get('outbound_url', {}).get('block_private_networks') is not True
                or config.get('security', {}).get('outbound_url', {}).get('allow_loopback') is not False):
            raise RunStateError('Incident deletion requires the isolated local transport-only configuration')
        bot_ids = [self.scope._id(role) for role in ('CEO','PM','ENG','QA')]
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            providers = db.execute('SELECT bot_uuid FROM bcs_provider_bot_bindings WHERE env=? AND bot_uuid IN (?,?,?,?) LIMIT 1',
                                   ('local', *bot_ids)).fetchall()
            deliveries = db.execute('SELECT delivery_id FROM bcs_message_deliveries WHERE env=? AND group_id=? LIMIT 1',
                                    ('local', self.group_id)).fetchall()
            runs = db.execute('SELECT run_id FROM bcs_state_machine_runs WHERE env=? AND group_id=? LIMIT 1',
                              ('local', self.group_id)).fetchall()
        if providers or deliveries or runs:
            raise RunStateError('Incident deletion cannot admit Provider or queued/state-machine work')
        return {'contract': 'owned-local-foreground-transport-only-v1',
                'status': 'LOCAL_EXECUTION_CLOSED', 'receiver_count': 3,
                'engine_started': False, 'provider_bindings': 0, 'queued_deliveries': 0,
                'state_machine_runs': 0, 'business_session_terminal': 'NOT_CLAIMED',
                'legacy_run_context_terminal': 'NOT_CLAIMED', 'remote_quiescence': 'NOT_APPLICABLE'}

    def _snapshot(self):
        self.identities._require_roles(('CEO','PM','ENG','QA'))
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            db.execute('BEGIN')
            groups = [dict(row) for row in db.execute(
                'SELECT group_id,driver_bot,status FROM bcs_groups WHERE env=? AND group_id=? LIMIT 2',
                ('local',self.group_id)).fetchall()]
            participants = [dict(row) for row in db.execute(
                'SELECT bot_uuid,role FROM bcs_group_participants WHERE env=? AND group_id=? ORDER BY bot_uuid LIMIT 4',
                ('local',self.group_id)).fetchall()]
            sessions = [dict(row) for row in db.execute(
                'SELECT session_id,status FROM bcs_group_sessions WHERE env=? AND group_id=? LIMIT 2',
                ('local',self.group_id)).fetchall()]
            session_members = [dict(row) for row in db.execute(
                'SELECT bot_uuid FROM bcs_session_participants WHERE env=? AND session_id=? ORDER BY bot_uuid LIMIT 4',
                ('local',self.session_id)).fetchall()]
            messages = [dict(row) for row in db.execute(
                'SELECT message_id,session_id,sender_id,content,status FROM bcs_messages WHERE env=? AND group_id=? ORDER BY message_id LIMIT 21',
                ('local',self.group_id)).fetchall()]
        if len(groups)>1 or len(participants)>3 or len(sessions)>1 or len(session_members)>3 or len(messages)>20:
            raise RunStateError('Incident deletion storage scope exceeds its declared bounds')
        return dict(groups=groups, participants=participants, sessions=sessions,
                    session_members=session_members, messages=messages)

    def _unrelated_snapshot(self):
        """Bounded verification inside the dedicated local DB, never a remote scan."""
        self.identities._require_roles(('CEO','PM','ENG','QA'))
        result = {}
        queries = {
            'groups': ('SELECT * FROM bcs_groups WHERE env=? AND group_id<>? ORDER BY group_id LIMIT 8', ('local',self.group_id)),
            'participants': ('SELECT * FROM bcs_group_participants WHERE env=? AND group_id<>? ORDER BY id LIMIT 33', ('local',self.group_id)),
            'sessions': ('SELECT * FROM bcs_group_sessions WHERE env=? AND group_id<>? ORDER BY session_id LIMIT 8', ('local',self.group_id)),
            'session_members': ('SELECT * FROM bcs_session_participants WHERE env=? AND session_id<>? ORDER BY id LIMIT 33', ('local',self.session_id)),
            'messages': ('SELECT * FROM bcs_messages WHERE env=? AND group_id<>? ORDER BY message_id LIMIT 65', ('local',self.group_id)),
        }
        limits = {'groups':7,'participants':32,'sessions':7,'session_members':32,'messages':64}
        with OwnedSqliteDatabase(self.runtime)._connection() as db:
            db.execute('BEGIN')
            for name,(sql,params) in queries.items():
                rows = [dict(row) for row in db.execute(sql,params).fetchall()]
                if len(rows)>limits[name]:
                    raise RunStateError('Unrelated local resource snapshot exceeded its finite test bound')
                result[name] = {'row_count':len(rows), 'digest':fingerprint({'rows':rows})}
        return result

    def _delete(self):
        if self._attempted:
            raise RunStateError('Incident deletion cannot be dispatched twice')
        self.state['admission'] = self._admit()
        actor, _token = self.scope._actor('CEO')
        if actor != self.state['driver_bot_id']:
            raise RunStateError('Incident delete driver changed')
        self._attempted = True
        request_id = 'e2e-incident-delete-'+uuid.uuid4().hex
        path = '/groups/'+quote(self.group_id,safe='')+'?'+urlencode({'bot_id':actor})
        event = {'operation':'incident-delete','method':'DELETE','path':path,
                 'submitted_request_id':request_id,'status':'INTENDED',
                 'authorization_contract':'OWNED_LOCAL_LEGACY_QUERY_COORDINATOR_NOT_REMOTE_AUTH'}
        self.state['operations'].append(event)
        self._save()
        try:
            request = Request(self.identities.origin+path,method='DELETE',headers={'Accept':'application/json','X-Request-ID':request_id})
            with self.identities.opener.open(request,timeout=5) as response:
                raw=response.read(16_385)
                event['http_status']=response.status
            value=json.loads(raw.decode(),object_pairs_hook=unique_keys)
            if len(raw)>16_384 or response.status!=200 or value!={'id':self.group_id,'deleted':True}:
                raise RunStateError('Incident deletion response was not exact')
            event['value']=retain_value(value)
            event['status']='ACKNOWLEDGED'
            self._save()
        except BaseException as error:
            event['status']='UNKNOWN'
            event['error_type']=type(error).__name__
            if isinstance(error,HTTPError):
                event['http_status']=error.code
                error.close()
            try:self._save()
            except BaseException:pass
            if has_cancellation(error):raise KeyboardInterrupt() from None
            raise RunStateError('Incident deletion failed; preserve evidence without replay') from None

    def _verify_not_found(self):
        self.identities._require_roles(('CEO','PM','ENG','QA'))
        request=Request(self.identities.origin+'/groups/'+quote(self.group_id,safe=''),method='GET')
        try:
            with self.identities.opener.open(request,timeout=5):
                raise RunStateError('Deleted incident Group remains accessible')
        except HTTPError as error:
            try:
                raw=error.read(16_385)
                value=json.loads(raw.decode(),object_pairs_hook=unique_keys)
                if (error.code!=404 or len(raw)>16_384 or not isinstance(value,dict)
                        or value.get('status')!=404 or value.get('code')!='group_not_found'
                        or value.get('params')!={'group_id':self.group_id}):
                    raise RunStateError('Incident not-found read was not confirmed')
            finally:error.close()
        self.state['not_found']={'status':'PASS','http_status':404,'group_id':self.group_id}
        self._save()

    def run(self):
        if self.path.exists() or self.path.is_symlink():
            raise RunStateError('Incident deletion requires fresh evidence')
        with Ledger(self.ledger_path,self.runtime.run_id,self.binding_digest) as ledger:
            self.scope.ledger=ledger
            admission=self._admit()
            before=self._snapshot()
            unrelated=self._unrelated_snapshot()
            if (len(before['groups'])!=1 or before['groups'][0]['driver_bot']!=self.scope._id('CEO')
                    or len(before['sessions'])!=1 or before['sessions'][0]['session_id']!=self.session_id
                    or len(before['participants'])!=3):
                raise RunStateError('Incident pre-delete state is not exact')
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
            self.state={'schema_version':1,'case_id':self.case_id,'run_id':self.runtime.run_id,
                'executor_digest':self.scope.executor_digest,'status':'RUNNING',
                'message_journal_digest':self.message._digest,'group_id':self.group_id,'initial_session_id':self.session_id,
                'driver_bot_id':self.scope._id('CEO'),'admission':admission,'operations':[],'assertions':[],
                'remote_validation':'NOT_RUN','full_story_status':'NOT_MIGRATED','ready_to_execute':False,
                'retained_message_digest':fingerprint({'rows':before['messages']}),
                'unrelated_before':unrelated}
            self._save()
            try:
                self._delete()
                self._verify_not_found()
                after=self._snapshot()
                if any(after[name] for name in ('groups','participants','sessions','session_members')) or after['messages']!=before['messages']:
                    raise RunStateError('Incident deletion removed wrong scope or altered retained history')
                if self._unrelated_snapshot() != unrelated:
                    raise RunStateError('Business deletion changed unrelated local resources')
                self.state['unrelated_after'] = unrelated
                # Product cascade is independently observed before child-first ledger acknowledgement.
                ledger.mark_deleted(self.resource['initial_session']['handle'])
                ledger.mark_deleted(self.resource['handle'])
                self.state.update(status=self.success_status,assertions=[{'id':self.obligation,'status':'PASS'}],
                    storage_verification={'status':'PASS','group_rows':0,'participant_rows':0,'session_rows':0,
                                          'session_member_rows':0,'retained_message_rows':len(after['messages'])},
                    disposition='EXACT_GROUP_SESSION_DELETED_HISTORY_RETAINED')
                self._save()
                return json.loads(json.dumps(self.state))
            except BaseException as error:
                self.state.update(status='CANCELLED' if has_cancellation(error) else 'BLOCKED',error_type=type(error).__name__)
                try:self._save()
                except BaseException:pass
                if has_cancellation(error):raise KeyboardInterrupt() from None
                raise RunStateError('Incident deletion not qualified; inspect retained evidence') from None
