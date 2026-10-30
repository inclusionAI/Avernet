"""Version-one Direct chat reports projected from original frozen evidence.

Digests detect drift, not a malicious rewrite by the owner of the Run files.
Offline verification never resolves identities or contacts the old runtime.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import binding_matches_reviewed_release, executor_digest
from .profile import fingerprint
from .run_state import RunStateError, read

CASE_ID = 'LOCAL-DIRECT-CHAT-01'
SUITE = 'owned-local-direct-chat-v1'
V1_REQUIREMENTS = tuple('DM.'+name for name in ('ping-submit','main-submit','main-read','main-cancel','main-terminal'))

SOURCES = ('direct-chat-binding.json','direct-chat-flow-report.json','bcs-config.json',
           'owned-bcs.json','local-fixtures.json','local-direct-chat.json','local-direct-chat-ledger.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-direct-chat-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Direct chat evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Direct chat evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Direct chat evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(raw).hexdigest()


def _sources(root):
    return {name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}


def _bound(root):
    binding = read(root/'direct-chat-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == 'story_user_has_direct_agent_conversation'
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Direct chat frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'direct-chat-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-direct-chat.json') if (root/'local-direct-chat.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Direct chat final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Direct chat runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in V1_REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_DIRECT_CHAT_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_DIRECT_CHAT_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else 'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next((r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                             for name in V1_REQUIREMENTS], 'assertions': assertions}


V1_OPERATIONS=('pm-visibility-before','pm-visibility-public','pm-visibility-public-read',
    'ping-submit','main-submit','main-read','main-cancel','main-terminal',
    'ping-cancel','ping-terminal','pm-visibility-restore','pm-visibility-restored-read')


def _business_evidence(root, binding, case):
    from urllib.parse import quote
    ledger=read(root/'local-direct-chat-ledger.json')
    fixtures=read(root/'local-fixtures.json')
    owner,target=(fixtures['roles'][r]['bot_id'] for r in ('CEO','PM'))
    effects=ledger.get('effects',{})
    if (ledger.get('contract')!='owned-local-direct-chat-ledger-v1' or ledger.get('status')!='TWO_OWNED_RUNS_CANCELLED'
            or any(ledger.get(k)!=binding[k] for k in ('run_id','case_id','executor_digest'))
            or ledger.get('binding_digest')!=fingerprint(binding) or set(effects)!={'ping','main'}
            or case.get('binding_digest')!=fingerprint(binding)
            or case.get('source_binding_digest')!=fingerprint(binding['source_binding'])):
        return False
    rows=[effects[n] for n in ('ping','main')]
    if any(not isinstance(r.get(k),str) or not r[k] for r in rows for k in ('run_id','session_id','request_id')):
        return False
    if len({r['run_id'] for r in rows})!=2 or len({r['session_id'] for r in rows})!=2:
        return False
    for name,row in zip(('ping','main'),rows):
        if (row.get('name')!=name or row.get('case_id')!=CASE_ID or row.get('source')!='ORIGINAL_DIRECT_CHAT_STORY'
                or row.get('owner_id')!=owner or row.get('target_id')!=target or row.get('origin')!='created'
                or row.get('state')!='CANCELLED'
                or row.get('transitions')!=['INTENDED','ADMITTED','CANCEL_REQUESTED','CANCELLED']):
            return False
    disposition='TWO_OWNED_MEMORY_RUNS_CANCELLED_RETAINED_UNTIL_PROCESS_STOP'
    if case.get('resources')!={'ledger_name':'local-direct-chat-ledger.json','ledger_digest':fingerprint(ledger),
                              'effects':rows,'disposition':disposition}:
        return False
    ping,main=rows
    visibility='/bots/'+quote(target,safe='')+'/visibility';submit='/bots/'+quote(target,safe='')+'/chat-async'
    mainpath='/chat/runs/'+quote(main['run_id'],safe='');pingpath='/chat/runs/'+quote(ping['run_id'],safe='')
    methods=('GET','PUT','GET','POST','POST','GET','POST','GET','POST','GET','PUT','GET')
    paths=(visibility,visibility,visibility,submit,submit,mainpath,mainpath+'/cancel',mainpath,
           pingpath+'/cancel',pingpath,visibility,visibility)
    bodies=(None,{'visibility':'public'},None,{'message':'ping'},
        {'message':'Prepare a release checklist','timeout_ms':60000,'tags':['e2e','release']},
        None,None,None,None,None,{'visibility':'protected'},None)
    operations=case.get('operations',[])
    if [o.get('operation') for o in operations]!=list(V1_OPERATIONS):return False
    request_ids=[]
    for i,op in enumerate(operations):
        status=202 if i in (3,4) else 200
        if (op.get('status')!='ACKNOWLEDGED' or op.get('transitions')!=['INTENDED','ACKNOWLEDGED']
                or op.get('method')!=methods[i] or op.get('path')!=paths[i] or op.get('request_body')!=bodies[i]
                or op.get('http_status')!=status or op.get('expected_http_status')!=status
                or op.get('auth_role')!=('PM' if i in (0,1,2,10,11) else 'CEO')
                or not isinstance(op.get('submitted_request_id'),str) or not op['submitted_request_id']):return False
        request_ids.append(op['submitted_request_id'])
    if len(set(request_ids))!=12 or ping['request_id']!=request_ids[3] or main['request_id']!=request_ids[4]:return False
    for i,mode in ((0,'protected'),(1,'public'),(2,'public'),(10,'protected'),(11,'protected')):
        if operations[i].get('observed')!={'bot_uuid':target,'visibility':mode}:return False
    for i,row in ((3,ping),(4,main)):
        if operations[i].get('observed')!={'run_id':row['run_id'],'session_id':row['session_id'],'bot_uuid':target,'status':'running'}:return False
    for i,row,terminal in ((5,main,False),(7,main,True),(9,ping,True)):
        obs=operations[i].get('observed',{});version=obs.get('version')
        if (type(version) is not int or version<1 or obs!={'run_id':row['run_id'],'session_id':row['session_id'],
                'bot_uuid':target,'from_bot_id':owner,'state':'cancelled' if terminal else 'running',
                'is_terminal':terminal,'version':version}):return False
    for i,row,read_index in ((6,main,7),(8,ping,9)):
        obs=operations[i].get('observed',{});version=obs.get('version')
        if (type(version) is not int or version<1 or obs!={'run_id':row['run_id'],'cancelled':True,'state':'cancelled','version':version}
                or operations[read_index]['observed']['version']<version):return False
    receiver=case.get('receiver',{})
    receipts=receiver.get('deliveries',[])
    if (receiver.get('state')!='CLOSED' or receiver.get('role')!='PM' or receiver.get('bot_id')!=target
            or receiver.get('engine')!='NOT_STARTED' or receiver.get('success_ack_sent') is not False
            or receiver.get('scope')!='DIRECT_RUN_NOT_GROUP_SESSION' or len(receipts)!=2):return False
    for row,receipt,text,tags in zip(rows,receipts,('ping','Prepare a release checklist'),([],['e2e','release'])):
        if receipt!={'status':'TRANSPORT_RECEIVED_ONLY','request_id':row['run_id'],'run_id':row['run_id'],
                'session_id':row['session_id'],'caller_id':owner,'target_id':target,'method':'chat.send',
                'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'tags':tags,'success_ack_sent':False,
                'engine':'NOT_STARTED','scope':'DIRECT_RUN_NOT_GROUP_SESSION'}:return False
    state=case.get('fixture_preservation',{});before=state.get('before')
    if not before or state.get('after')!=before or state.get('preserved') is not True:return False
    if (before.get('async_chat_run_store')!='memory' or before.get('chat_run_sqlite_evidence')!='NOT_ASSERTED_MEMORY_STORE'
            or before.get('friendships')!=[] or before.get('friend_requests')!=[]
            or before.get('provider_count')!=0 or before.get('provider_binding_count')!=0
            or before.get('engine')!='NOT_STARTED' or before.get('fixture_digest')!=fingerprint(fixtures)):return False
    roles=before.get('roles',{})
    if set(roles)!={'CEO','PM','ENG','QA'} or any(r.get('bot_uuid')!=fixtures['roles'][role]['bot_id']
            or r.get('visibility')!='protected' or r.get('credential_matches')!=1
            or r.get('created_by')!=fixtures['human']['user_id'] or r.get('is_deleted')!=0 for role,r in roles.items()):return False
    checks=case.get('checks',[])
    names=('owned-local-boundary-before','ping-transport-receipt','DM.ping-submit','main-transport-receipt',
        'DM.main-submit','DM.main-read','DM.main-cancel','DM.main-terminal',
        'supplementary-ping-cancel-and-terminal-read','owned-local-boundary-after')
    if [c.get('id') for c in checks]!=list(names) or any(c.get('status')!='PASS' for c in checks):return False
    evidence={c['id']:c['evidence'] for c in checks}
    expected={'owned-local-boundary-before':before,'owned-local-boundary-after':before,
        'ping-transport-receipt':receipts[0],'main-transport-receipt':receipts[1],
        'DM.ping-submit':{'http_status':202,'run_id':ping['run_id'],'session_id':ping['session_id'],'receiver':receipts[0]},
        'DM.main-submit':{'http_status':202,'run_id':main['run_id'],'session_id':main['session_id'],'receiver':receipts[1]},
        'DM.main-read':operations[5]['observed'],'DM.main-cancel':operations[6]['observed'],
        'DM.main-terminal':operations[7]['observed'],
        'supplementary-ping-cancel-and-terminal-read':{'cancel':operations[8]['observed'],
            'terminal':operations[9]['observed'],'parent_requirement_credit':False}}
    return (evidence==expected and case.get('visibility')=={'before':'protected','public_write':'public','public_read':'public',
        'restore_write':'protected','restored_read':'protected','restored':True}
        and case.get('execution_scope')=={'runtime':'OWNED_LOCAL_FOREGROUND','transport':'NATIVE_HTTP_AND_BOT_WEBSOCKET',
            'async_chat_run_store':'MEMORY_DEFAULT','store_messages':False,'provider':'NOT_CONFIGURED','engine':'NOT_STARTED',
            'success_ack':'NOT_SENT','remote_validation':'NOT_RUN','remote_engine_quiescence':'NOT_CLAIMED',
            'group_or_session_delete':'NOT_PERFORMED'} and case.get('resource_disposition')==disposition)


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID], 'legacy_inventory_count': len(binding['legacy_inventory']),
                      'full_release_status': 'NOT_QUALIFIED',
                      'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                      'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'requirements': projected.get('requirements', [{'id': name, 'status': 'NOT_RUN'} for name in V1_REQUIREMENTS]),
            'reports': {'expected_assertion_count': 5, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'EXACT_OWNED_MEMORY_RUNS_RECEIVER_AND_PROCESS_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                           'external engine termination', 'persistent chat-run database', 'remote direct-chat lifecycle', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_direct_chat(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Direct chat source changed before report publication')
    _require(read(root/'direct-chat-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Direct chat publication must follow actual runtime finalization')
    pending = root/'direct-chat-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'direct-chat-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'direct-chat-completion.json') == completion_digest,
             'Direct chat source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_direct_chat_report(root: Path):
    _require(not root.is_symlink(), 'Direct chat report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'direct-chat-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Direct chat publication is incomplete')
        completion = read(root/'direct-chat-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Direct chat completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Direct chat evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'direct-chat-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Direct chat evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Direct chat completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
