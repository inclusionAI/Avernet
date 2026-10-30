"""Version-one Channel CLI reports projected from original frozen evidence.

Digests detect drift, not a malicious rewrite by the owner of the Run files.
Offline verification never resolves identities or contacts the old runtime.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sqlite3

from .ledger import LedgerError

from .inventory import binding_matches_reviewed_release, executor_digest
from .profile import fingerprint
from .run_state import RunStateError, read

CASE_ID = 'LOCAL-CHANNEL-CLI-01'
SUITE = 'owned-local-channel-cli-v1'
V1_REQUIREMENTS = tuple('CHANNEL-CLI.'+name for name in ('bind-redacted','list-before','group-create',
    'session-create','conversation-empty','unbind-missing','list-after','session-delete','group-delete'))

SOURCES = ('channel-cli-binding.json','channel-cli-flow-report.json','bcs-config.json',
           'owned-bcs.json','local-fixtures.json','local-channel-cli.json','channel-cli-resources.sqlite')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-channel-cli-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Channel CLI evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Channel CLI evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Channel CLI evidence exceeded byte limit')
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
    binding = read(root/'channel-cli-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == 'story_cli_operator_validates_channel_management'
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Channel CLI frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'channel-cli-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-channel-cli.json') if (root/'local-channel-cli.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Channel CLI final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Channel CLI runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in V1_REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_CHANNEL_CLI_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_CHANNEL_CLI_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else 'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next((r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                             for name in V1_REQUIREMENTS], 'assertions': assertions}


V1_OPERATIONS = ('channel-bind','channel-list-before','eng-visibility-before','eng-visibility-public',
    'eng-visibility-public-read','group-create','session-create','channel-conversation-empty',
    'channel-unbind','channel-list-after','session-delete','group-delete',
    'eng-visibility-restore','eng-visibility-restored-read')
V1_CLI = ('channel-bind','channel-list-before','group-create','session-create',
          'channel-conversation-empty','channel-unbind','channel-list-after')


def _business_evidence(root, binding, case):
    from urllib.parse import quote, urlencode
    from .ledger import Ledger
    resources = case.get('resources', {})
    group, session = resources.get('group', {}), resources.get('session', {})
    if not group or not session:
        return False
    fields = ('handle','case_id','attempt','kind','parent','request_id','origin','resource_id','state')
    with Ledger(root/'channel-cli-resources.sqlite', binding['run_id'], fingerprint(binding), read_only=True) as ledger:
        rows = ledger.resource_snapshot()
    expected = {(group['handle'],'group',group['id'],None),
                (group['initial_session_handle'],'session',group['initial_session_id'],group['handle']),
                (session['handle'],'session',session['id'],group['handle'])}
    if (len(rows) != 3 or len({r['resource_id'] for r in rows}) != 3
            or {(r['handle'],r['kind'],r['resource_id'],r['parent']) for r in rows} != expected
            or any(r['case_id'] != CASE_ID or r['attempt'] != 1 or r['origin'] != 'created' or r['state'] != 'deleted' for r in rows)
            or resources.get('ledger_summary') != {'resource_count':3,'deleted_count':3,
                'resources':[{k:r[k] for k in fields} for r in rows],
                'mark_order':['explicit_session','implicit_session','group']}):
        return False
    fixtures = read(root/'local-fixtures.json')
    pm,eng = (fixtures['roles'][r]['bot_id'] for r in ('PM','ENG'))
    missing = 'missing-channel-cli-'+hashlib.sha256(binding['run_id'].encode()).hexdigest()[:24]
    gid,sid = quote(group['id'],safe=''),quote(session['id'],safe='')
    visibility = '/bots/'+quote(eng,safe='')+'/visibility'
    methods = ('POST','GET','GET','PUT','GET','POST','POST','GET','DELETE','GET','DELETE','DELETE','PUT','GET')
    paths = ('/channels/bindings','/channels/bindings',visibility,visibility,visibility,'/groups',
        '/groups/'+gid+'/sessions','/channels/conversations/by-session?bcs_session_id='+sid+'&channel_type=dingtalk',
        '/channels/bindings/'+missing,'/channels/bindings','/sessions/'+sid+'?'+urlencode({'bot_id':pm}),
        '/groups/'+gid+'?'+urlencode({'bot_id':pm}),visibility,visibility)
    operations = case.get('operations', [])
    if ([o.get('operation') for o in operations] != list(V1_OPERATIONS)
            or case.get('missing_binding_id') != missing or case.get('binding_digest') != fingerprint(binding)
            or case.get('source_binding_digest') != fingerprint(binding['source_binding'])):
        return False
    ids=[]
    for i,op in enumerate(operations):
        cli = op['operation'] in V1_CLI
        status = 400 if i==0 else 201 if i==6 else 200
        if (op.get('method') != methods[i] or op.get('path') != paths[i]
                or op.get('http_status') != status or op.get('status') != 'ACKNOWLEDGED'
                or op.get('transport') != ('PRODUCT_CLI' if cli else 'LEGACY_HTTP')
                or [r.get('status') for r in op.get('transitions', [])] != ['INTENDED','ACKNOWLEDGED']
                or not isinstance(op.get('submitted_request_id'),str) or not op['submitted_request_id']):
            return False
        ids.append(op['submitted_request_id'])
        if cli:
            if (op.get('credential_cleanup') != 'CLEARED' or op.get('auth_contract') != 'owned-bot-bearer'
                    or type(op.get('cli_exit_code')) is not int
                    or (op['cli_exit_code']==0) != (i!=0)):
                return False
            output=op.get('output_validation',{})
            if i==0:
                if output != {'kind':'bind-redacted-error','expected_nonzero':True,'disabled_bridge':True,
                        'stdout_bytes_retained':False,'stderr_bytes_retained':False,'secret_redacted':True}:
                    return False
            elif i==5:
                if output != {'kind':'group-create-human','group_id':group['id'],
                        'initial_session_id':group['initial_session_id'],'raw_output_persisted':False}:
                    return False
            elif output != {'kind':'json','json_matches_forwarded_response':True,'raw_output_persisted':False}:
                return False
    if len(set(ids)) != len(operations):
        return False
    if (next(r for r in rows if r['kind']=='group')['request_id'] != ids[5]
            or next(r for r in rows if r['resource_id']==session['id'])['request_id'] != ids[6]):
        return False
    checks=case.get('checks', [])
    expected_checks=['channel-empty-before',V1_REQUIREMENTS[0],V1_REQUIREMENTS[1],V1_REQUIREMENTS[2],
        V1_REQUIREMENTS[3],V1_REQUIREMENTS[4],'unbind-empty-admission',V1_REQUIREMENTS[5],V1_REQUIREMENTS[6],
        'owned-resource-snapshot','cleanup-admission','retained-history-before','session-delete-proof',
        V1_REQUIREMENTS[7],'group-delete-proof',V1_REQUIREMENTS[8]]
    if [c.get('id') for c in checks] != expected_checks or any(c.get('status')!='PASS' for c in checks):
        return False
    evidence={c['id']:c['evidence'] for c in checks}
    empty=evidence['channel-empty-before']
    counts={t:0 for t in ('bcs_channel_bindings','bcs_channel_conversations','bcs_channel_im_participants')}
    if (empty != {'status':'PASS','source':'OWNED_LOCAL_SQLITE','row_counts':counts,'digest':fingerprint({'row_counts':counts})}
            or evidence['unbind-empty-admission'] != empty):
        return False
    expected_observed=[{'http_status':400,'code':'bad_request','disabled_bridge':True,'secret_redacted_stdout_stderr':True},
        {'items':[]},{'group_id':group['id'],'initial_session_id':group['initial_session_id']},
        {'session_id':session['id']},{'items':[]},{'ok':True,'binding_id':missing,'admission':'PROVED_ABSENT'},
        {'items':[],'sqlite':empty},evidence['session-delete-proof'],evidence['group-delete-proof']]
    if any(evidence[name] != value for name,value in zip(V1_REQUIREMENTS,expected_observed)):
        return False
    owned=evidence['owned-resource-snapshot']
    if owned != {'status':'PASS','group_rows':1,'group_participant_rows':2,'session_rows':2,
                 'session_participant_rows':4,'delivery_rows':0,'state_machine_run_rows':0}:
        return False
    admission=evidence['cleanup-admission']
    if (admission.get('status')!='LOCAL_DELETION_ADMITTED' or admission.get('engine')!='NOT_STARTED'
            or admission.get('receiver')!='NOT_OPENED' or admission.get('business_execution')!='NOT_CLAIMED'
            or any(admission.get(k)!=0 for k in ('provider_bindings','queued_deliveries','state_machine_runs'))):
        return False
    if evidence['session-delete-proof'] != {'status':'PASS','session_rows':0,'session_participant_rows':0}:
        return False
    proof=evidence['group-delete-proof']
    if (proof.get('status')!='PASS' or proof.get('retained_history')!=evidence['retained-history-before']
            or any(proof.get(k)!=0 for k in ('group_rows','group_participant_rows','session_rows',
                'session_participant_rows','delivery_rows','state_machine_run_rows'))):
        return False
    for key in ('fixtures','unrelated'):
        state=case.get('preservation',{}).get(key,{})
        if state.get('preserved') is not True or not state.get('before') or state['before']!=state.get('after'):
            return False
    return (case.get('visibility') == {'before':'protected','public_write':'public','public_read':'public',
                'restore_write':'protected','restored_read':'protected'}
            and case.get('cli') == {'operation_count':7,'acknowledged_count':7,'credential_contexts_cleared':True}
            and case.get('resource_disposition') == 'LEDGER_3_TOMBSTONES_NO_CHANNEL_BINDING')


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
            'reports': {'expected_assertion_count': 9, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'OWNED_GROUP_SESSIONS_AND_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                           'enabled channel providers', 'actual message delivery', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_channel_cli(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Channel CLI source changed before report publication')
    _require(read(root/'channel-cli-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Channel CLI publication must follow actual runtime finalization')
    pending = root/'channel-cli-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'channel-cli-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'channel-cli-completion.json') == completion_digest,
             'Channel CLI source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_channel_cli_report(root: Path):
    _require(not root.is_symlink(), 'Channel CLI report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'channel-cli-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Channel CLI publication is incomplete')
        completion = read(root/'channel-cli-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Channel CLI completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Channel CLI evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'channel-cli-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Channel CLI evidence changed during projection')
    except (OSError, RunStateError, LedgerError, sqlite3.DatabaseError, ValueError, KeyError, TypeError):
        issues.append('Channel CLI completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
