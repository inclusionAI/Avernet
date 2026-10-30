"""Version-one Structured reports projected from original frozen evidence.

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

CASE_ID = 'LOCAL-STRUCTURED-01'
SUITE = 'owned-local-structured-v1'
V1_REQUIREMENTS = tuple('STRUCTURED.'+name for name in (
    'templates-list','template-detail','group-create','group-read','definition-read','definition-patch',
    'upgrade-missing','run-start','run-read','graph-read','node-read','pending-human-empty',
    'human-response-denied','run-cancel','run-terminal','rerun-aborted-denied','group-delete'))
V1_ORDER = ('fixture-public', *V1_REQUIREMENTS[:3], 'implicit-read', *V1_REQUIREMENTS[3:16],
            'implicit-cancel', 'implicit-terminal', V1_REQUIREMENTS[16], 'fixture-restore')

SOURCES = ('structured-binding.json','structured-flow-report.json','bcs-config.json',
           'owned-bcs.json','local-fixtures.json','local-structured.json','template-assets.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-structured-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Structured evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Structured evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Structured evidence exceeded byte limit')
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
    names = {name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}
    if 'template-assets.json' in names:
        import re
        receipt = read(root/'template-assets.json')
        files = receipt.get('files', [])
        _require(isinstance(files,list) and 3 <= len(files) <= 256, 'template source list is invalid')
        for row in files:
            name = row.get('path', '')
            _require(isinstance(name,str) and re.fullmatch(r'(?:registry\.yaml|(?:zh-CN|en-US)/[a-z0-9]+(?:-[a-z0-9]+)*\.yaml)', name),
                     'template evidence path is outside catalog')
            names.add('templates/'+name)
    return names


def _bound(root):
    binding = read(root/'structured-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == 'story_user_runs_structured_collaboration'
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Structured frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'structured-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-structured.json') if (root/'local-structured.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Structured final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Structured runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in V1_REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_STRUCTURED_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES).issubset(_sources(root)))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_STRUCTURED_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else 'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'RETAINED' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next((r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                             for name in V1_REQUIREMENTS], 'assertions': assertions}


def _retention_valid(case):
    proof=case.get('retention',{}); group=case.get('group_id')
    implicit=case.get('implicit',{}); explicit=case.get('explicit',{})
    ids={implicit.get('run_id'),explicit.get('run_id')}
    runs=proof.get('runs',[]); nodes=proof.get('nodes',[]); definitions=proof.get('definitions',[])
    return (None not in ids and len(ids)==2 and proof.get('source')=='local-sqlite'
            and proof.get('group_id')==group and proof.get('group_present') is False
            and proof.get('session_count')==0 and proof.get('runtime_binding_count')==0
            and len(runs)==2 and {row.get('run_id') for row in runs}==ids
            and all(row.get('status')=='aborted' and row.get('group_id')==group
                    and row.get('definition_id')==case.get('definition_id') for row in runs)
            and len(nodes)==2 and {row.get('run_id') for row in nodes}==ids
            and all(row.get('node_id')=='answer' for row in nodes)
            and len(definitions)==2
            and {row.get('version') for row in definitions}=={case.get('base_version'),case.get('patched_version')}
            and all(row.get('definition_id')==case.get('definition_id') for row in definitions))


def _business_evidence(root, binding, case):
    from urllib.parse import quote
    fixtures = read(root/'local-fixtures.json')
    ceo = fixtures['roles']['CEO']['bot_id']; pm = fixtures['roles']['PM']['bot_id']
    group = case.get('group_id'); implicit = case.get('implicit',{}); explicit = case.get('explicit',{})
    if (not group or implicit.get('run_id') == explicit.get('run_id')
            or implicit.get('session_id') == explicit.get('session_id')
            or implicit.get('session_id') != case.get('initial_session_id')
            or not implicit.get('run_id') or not explicit.get('run_id')
            or any(row.get('group_id') != group for row in (implicit,explicit))
            or case.get('unknown_operations') != []): return False
    operations = case.get('operations', [])
    if ([row.get('operation') for row in operations] != list(V1_ORDER)
            or case.get('binding_digest') != fingerprint(binding)): return False
    ids = []
    for row in operations:
        operation = row['operation']
        if operation in V1_REQUIREMENTS:
            original = binding['source_binding']['requirements'][V1_REQUIREMENTS.index(operation)]
            method, actor = original['method'], original['actor']
            path = original['path'].replace('${group_id}',quote(group,safe='')).replace(
                '${run_id}',quote(explicit['run_id'],safe='')).replace('${BOT_CEO_UUID}',quote(ceo,safe=''))
            code = {V1_REQUIREMENTS[6]:404,V1_REQUIREMENTS[7]:202,V1_REQUIREMENTS[12]:400,V1_REQUIREMENTS[15]:409}.get(operation,200)
        elif operation in ('fixture-public','fixture-restore'):
            method,actor,path,code='PUT','PM','/bots/'+quote(pm,safe='')+'/visibility',200
        else:
            method,actor,code=('POST' if operation=='implicit-cancel' else 'GET'),'HUMAN',200
            path='/state-machine-runs/'+quote(implicit['run_id'],safe='')+('/cancel' if operation=='implicit-cancel' else '')
        if (row.get('method'),row.get('path'),row.get('actor'),row.get('http_status')) != (method,path,actor,code): return False
        if row.get('status')!='VERIFIED' or row.get('transitions')!=['INTENDED','VERIFIED'] or 'observed' not in row: return False
        ids.append(row.get('request_id'))
    if any(not isinstance(value,str) or not value for value in ids) or len(set(ids))!=len(V1_ORDER):return False
    by_id={row['operation']:row['observed'] for row in operations}
    if ('single-bot-guided-answer' not in by_id[V1_REQUIREMENTS[0]].get('template_ids',[])
            or by_id[V1_REQUIREMENTS[1]] != {'id':'single-bot-guided-answer','lang':'zh-CN',
                'yaml_sha256':_sha(root/'templates/zh-CN/single-bot-guided-answer.yaml')}
            or by_id[V1_REQUIREMENTS[2]] != {'group_id':group,'initial_session_id':case['initial_session_id'],
                'driver_bot':ceo,'participants':[ceo,pm]}
            or by_id[V1_REQUIREMENTS[3]] != {'group_strategy':'state_machine'}
            or by_id[V1_REQUIREMENTS[4]].get('yaml_source')!='original'
            or by_id[V1_REQUIREMENTS[4]].get('id')!=case.get('definition_id')
            or by_id[V1_REQUIREMENTS[4]].get('version')!=case.get('base_version')
            or by_id[V1_REQUIREMENTS[5]].get('id')!=case.get('definition_id')
            or by_id[V1_REQUIREMENTS[5]].get('version')!=case.get('base_version',0)+1
            or by_id[V1_REQUIREMENTS[5]].get('revised_instruction') is not True
            or by_id[V1_REQUIREMENTS[6]]!={'definition_id':case['definition_id'],'missing_version':case['patched_version']+100,'rejected':True}
            or by_id[V1_REQUIREMENTS[10]]!={'node_id':'answer','run_id':explicit['run_id']}
            or by_id[V1_REQUIREMENTS[11]]!=[] or by_id[V1_REQUIREMENTS[12]]!={'non_human_node_rejected':True}
            or by_id[V1_REQUIREMENTS[15]]!={'aborted_rerun_rejected':True}
            or by_id[V1_REQUIREMENTS[16]]!={'delete_accepted':True,'group_id':group}
            or by_id['fixture-public']!={'visibility':'public'} or by_id['fixture-restore']!={'visibility':'protected'}):return False
    for name in ('implicit','explicit'):
        run=case[name]; effect=case.get('effects',{}).get(name,{})
        terminal=case.get('terminal_evidence',{}).get(name,{})
        if (effect.get('status')!='CANCELLED' or effect.get('run_id')!=run['run_id']
                or terminal.get('run',{}).get('run_id')!=run['run_id']
                or terminal['run'].get('group_id')!=group or terminal['run'].get('status')!='aborted'
                or terminal.get('session',{}).get('status')!='completed'
                or terminal['session'].get('error_message')!='aborted'):return False
        checks = ('implicit-cancel','implicit-terminal') if name=='implicit' else V1_REQUIREMENTS[13:15]
        for key in checks:
            if by_id[key]!={'run_id':run['run_id'],'group_id':group,'session_id':run['session_id'],
                            'status':'aborted','node_id':'answer'}:return False
        receipt=case.get(name+'_delivery',{})
        if (receipt.get('status')!='TRANSPORT_RECEIVED_ONLY' or receipt.get('run_id')!=run['run_id']
                or receipt.get('session_id')!=run['session_id'] or receipt.get('group_id')!=group
                or receipt.get('target_bot_id')!=ceo or receipt.get('target_role')!='CEO'
                or receipt.get('method')!='chat.send' or receipt.get('node_id')!='answer'
                or type(receipt.get('attempt')) is not int or receipt['attempt']<0
                or receipt.get('request_id')!='smnode-'+run['run_id']+'-answer-'+str(receipt['attempt'])
                or receipt.get('wire_group_id')!=run['session_id'] or receipt.get('channel_thread_id')!=group
                or receipt.get('instruction_sha256')!=hashlib.sha256((
                    'Draft a concise release recommendation.' if name=='implicit' else
                    'Draft and verify a concise release recommendation.').encode()).hexdigest()
                or receipt.get('engine')!='NOT_STARTED' or receipt.get('success_ack_sent') is not False):return False
    for key in V1_REQUIREMENTS[7:10]:
        view=by_id[key]
        if (view.get('run_id')!=explicit['run_id'] or view.get('group_id')!=group
                or view.get('session_id')!=explicit['session_id'] or view.get('node_id')!='answer' or not view.get('status')):return False
    state=case.get('fixture_state',{}); before=state.get('before')
    if not before or state.get('after')!=before or state.get('preserved') is not True:return False
    receipt=read(root/'template-assets.json')
    if (receipt.get('run_id')!=binding['run_id'] or receipt.get('manifest_sha256')!=binding['template_manifest_sha256']
            or any(_sha(root/'templates'/row['path'])!=row['sha256'] for row in receipt['files'])):return False
    return (case.get('effects',{}).get('group')=={'status':'DELETED','id':group}
            and case.get('receiver',{}).get('state')=='CLOSED'
            and case['receiver'].get('deliveries')==[case['implicit_delivery'],case['explicit_delivery']]
            and case['receiver'].get('success_ack_sent') is False
            and case['receiver'].get('engine')=='NOT_STARTED'
            and _retention_valid(case)
            and case.get('resource_disposition')=='GROUP_SESSIONS_DELETED_HISTORY_RETAINED')


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
            'reports': {'expected_assertion_count': 17, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'EXACT_GROUP_SESSIONS_REMOVED_RUN_DEFINITION_HISTORY_RETAINED',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                           'external engine completion', 'shared environment cancellation', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_structured(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Structured source changed before report publication')
    _require(read(root/'structured-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Structured publication must follow actual runtime finalization')
    pending = root/'structured-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'structured-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'structured-completion.json') == completion_digest,
             'Structured source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_structured_report(root: Path):
    _require(not root.is_symlink(), 'Structured report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'structured-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Structured publication is incomplete')
        completion = read(root/'structured-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Structured completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Structured evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'structured-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Structured evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Structured completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
