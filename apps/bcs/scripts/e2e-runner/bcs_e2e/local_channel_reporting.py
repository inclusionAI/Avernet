"""Version-one Channel HTTP reports projected from original frozen evidence.

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

CASE_ID = 'LOCAL-CHANNEL-HTTP-01'
SUITE = 'owned-local-channel-http-v1'
V1_REQUIREMENTS = tuple('CHANNEL.'+name for name in (
    'bind-denied','list-before','target-empty','pause-missing','delete-missing','list-after'))

SOURCES = ('channel-http-binding.json','channel-http-flow-report.json','bcs-config.json',
           'owned-bcs.json','local-fixtures.json','local-channel-http.json')
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-channel-http-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Channel HTTP evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Channel HTTP evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Channel HTTP evidence exceeded byte limit')
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
    binding = read(root/'channel-http-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('source_binding', {}).get('parent') == 'story_user_validates_external_channel_setup'
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Channel HTTP frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'channel-http-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-channel-http.json') if (root/'local-channel-http.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Channel HTTP final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Channel HTTP runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in V1_REQUIREMENTS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_CHANNEL_HTTP_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_CHANNEL_HTTP_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else 'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name, 'status': next((r['status'] for r in assertions if r.get('id') == name), 'NOT_RUN')}
                             for name in V1_REQUIREMENTS], 'assertions': assertions}


def _business_evidence(root, binding, case):
    from urllib.parse import quote
    fixtures = read(root/'local-fixtures.json')
    ceo = fixtures['roles']['CEO']['bot_id']
    missing = 'missing-channel-'+hashlib.sha256(binding['run_id'].encode()).hexdigest()[:24]
    paths = ['/channels/bindings','/channels/bindings',
             '/channels/bindings/by-target?target_type=bot&target_id='+quote(ceo,safe='')+'&channel_type=e2e-uninstalled-channel',
             '/channels/bindings/'+missing,'/channels/bindings/'+missing,'/channels/bindings']
    methods = ['POST','GET','GET','PATCH','DELETE','GET']
    expected = [{'code':'bad_request','disabled_bridge':True},{'items':[]},{'items':[]},
                {'ok':True},{'ok':True},{'items':[]}]
    operations = case.get('operations', [])
    if ([r.get('operation') for r in operations] != list(V1_REQUIREMENTS)
            or case.get('binding_digest') != fingerprint(binding)
            or case.get('source_binding_digest') != fingerprint(binding['source_binding'])
            or case.get('missing_binding_id') != missing
            or case.get('resources') != {'created':[],'deleted':[],'unknown':[]}):
        return False
    ids = []
    for index, row in enumerate(operations):
        if (row.get('method') != methods[index] or row.get('path') != paths[index]
                or row.get('http_status') != (400 if index == 0 else 200)
                or row.get('status') != 'VERIFIED' or row.get('transitions') != ['INTENDED','VERIFIED']
                or row.get('observed') != expected[index] or not isinstance(row.get('submitted_request_id'),str)
                or not row['submitted_request_id']):
            return False
        ids.append(row['submitted_request_id'])
    if len(set(ids)) != 6:
        return False
    state = case.get('fixture_state', {}); before = state.get('before')
    if not before or state.get('after') != before or state.get('preserved') is not True:
        return False
    if before.get('empty_tables') != {name:True for name in (
            'bcs_channel_bindings','bcs_channel_conversations','bcs_channel_im_participants')}:
        return False
    roles = before.get('fixtures',{}).get('roles',{})
    if set(roles) != {'CEO','PM','ENG','QA'} or any(
            r.get('bot_uuid') != fixtures['roles'][role]['bot_id'] or r.get('created_by') != fixtures['human']['user_id']
            or r.get('credential_matches') != 1 or r.get('is_deleted') != 0 or r.get('provider_bindings') != 0
            for role,r in roles.items()):
        return False
    return (case.get('no_op_admissions') == [{'operation':V1_REQUIREMENTS[i],'binding_id':missing,
                'basis':'DISABLED_BRIDGE_AND_EMPTY_OWNED_DATABASE','snapshot':before} for i in (3,4)]
            and case.get('resource_disposition') == 'NO_BINDING_CREATED_NO_RESOURCE_DELETED')


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
            'reports': {'expected_assertion_count': 6, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'DISABLED_BRIDGE_NO_RESOURCES_CREATED_OWNED_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                           'enabled channel providers', 'actual message delivery', 'CLI Channel parent', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_channel_http(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Channel HTTP source changed before report publication')
    _require(read(root/'channel-http-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Channel HTTP publication must follow actual runtime finalization')
    pending = root/'channel-http-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'channel-http-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'channel-http-completion.json') == completion_digest,
             'Channel HTTP source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_channel_http_report(root: Path):
    _require(not root.is_symlink(), 'Channel HTTP report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'channel-http-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Channel HTTP publication is incomplete')
        completion = read(root/'channel-http-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Channel HTTP completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Channel HTTP evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'channel-http-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Channel HTTP evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Channel HTTP completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
