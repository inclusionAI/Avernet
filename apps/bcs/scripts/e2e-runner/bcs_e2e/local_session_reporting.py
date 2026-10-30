"""Version-one Session public reports projected from original frozen evidence.

Digests detect drift, not a malicious rewrite by the owner of the Run files.
Offline verification never resolves identities or contacts the old runtime.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sqlite3

from .inventory import binding_matches_reviewed_release, executor_digest
from .ledger import Ledger, LedgerError
from .local_session_contract import PUBLIC_ASSERTION_IDS
from .profile import fingerprint
from .run_state import RunStateError, read

CASE_ID = 'LOCAL-SESSION-PUBLIC-01'
SUITE = 'owned-local-session-public-v1'
V1_REQUIREMENTS = tuple('SESSION.'+name for name in (
    'public-jwt-ws', 'cli-create-list-get', 'group-invite', 'cli-session-invite',
    'bot-groups', 'invite-join', 'lifecycle', 'complete-invoke', 'collection'))
SOURCES = ('session-binding.json', 'session-public-flow-report.json', 'bcs-config.json',
           'owned-bcs.json', 'local-fixtures.json', 'local-session-public.json',
           'session-public-resources.sqlite')

OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-session-public-completion-v1'


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Session public evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8*1024*1024,
                 'Session public evidence must be a bounded regular unshared file')
        raw = stream.read(8*1024*1024+1)
    _require(len(raw) <= 8*1024*1024, 'Session public evidence exceeded byte limit')
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
    binding = read(root/'session-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root) and binding.get('case_id') == CASE_ID
             and binding.get('selected_requirement_ids') == [V1_REQUIREMENTS[0]]
             and binding.get('parent_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('store_messages') is True
             and binding.get('source_binding', {}).get('parent') == 'story_user_runs_and_shares_sessions'
             and [r['id'] for r in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Session public frozen complete binding is invalid')
    return binding


def _project(root, binding):
    flow = read(root/'session-public-flow-report.json')
    runtime = read(root/'owned-bcs.json')
    case = read(root/'local-session-public.json') if (root/'local-session-public.json').exists() else {}
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime and flow.get('case') == case,
             'Session public final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Session public runtime binding differs')
    expected = [{'id': name, 'status': 'PASS'} for name in PUBLIC_ASSERTION_IDS]
    assertions = case.get('assertions', [])
    complete = (case.get('status') == 'LOCAL_SESSION_PUBLIC_PASS' and assertions == expected
                and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business_evidence(root, binding, case)
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_SESSION_PUBLIC_PASS' else
              'FAIL' if any(r.get('status') == 'FAIL' for r in assertions) else 'BLOCKED')
    public_all_pass = (len(assertions) == len(PUBLIC_ASSERTION_IDS)
                       and all(r.get('status') == 'PASS' for r in assertions))
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('disposition', 'UNVERIFIED'),
            'requirements': [{'id': name,
                              'status': 'PASS' if (index == 0 and public_all_pass) else 'NOT_RUN'}
                             for index, name in enumerate(V1_REQUIREMENTS)],
            'assertions': assertions}


V1_OPERATIONS = (
    'groups-missing-principal', 'groups-unknown-field', 'bots-mine', 'bots-query', 'bot-detail',
    'group-create', 'group-list', 'group-detail', 'group-patch',
    'session-create', 'session-list', 'session-list-pm', 'session-detail', 'session-patch', 'session-messages',
    'session-token', 'session-ws-connect', 'session-delete', 'session-deleted-404',
    'group-delete', 'group-deleted-404')
V1_CHECKS = (
    'missing-principal-401', 'unknown-create-field-400', 'public-bots-mine', 'public-bots-query',
    'public-bot-detail', 'group-create-201', 'group-list', 'group-detail', 'group-patch',
    'session-create-201', 'session-human-view-empty', 'session-list', 'session-detail', 'session-patch', 'session-messages',
    'session-token-no-store', 'session-websocket-101-closed', 'session-delete-200-exact-404',
    'group-delete-200-exact-404')


def _business_evidence(root, binding, case):
    operations, checks = case.get('operations', []), case.get('checks', [])
    if ([op.get('operation') for op in operations] != list(V1_OPERATIONS)
            or [row.get('id') for row in checks] != list(V1_CHECKS)
            or any(row.get('status') != 'PASS' for row in checks)):
        return False
    statuses = {'groups-missing-principal': 401, 'groups-unknown-field': 400,
                'group-create': 201, 'session-create': 201, 'session-ws-connect': 101,
                'session-deleted-404': 404, 'group-deleted-404': 404}
    for op in operations:
        if (op.get('status') != 'ACKNOWLEDGED' or op.get('http_status') != statuses.get(op['operation'], 200)
                or [row.get('status') for row in op.get('transitions', [])] != ['INTENDED', 'ACKNOWLEDGED']
                or not isinstance(op.get('submitted_request_id'), str) or not op['submitted_request_id']):
            return False
    if len({op['submitted_request_id'] for op in operations}) != len(operations):
        return False
    resources = case.get('resources', {})
    group, session = resources.get('group', {}), resources.get('session', {})
    expected = [(group.get('handle'), 'group', group.get('id'), None),
                (group.get('initial_session_handle'), 'session', group.get('initial_session_id'), group.get('handle')),
                (session.get('handle'), 'session', session.get('id'), group.get('handle'))]
    if (any(not handle or not resource_id for handle, _, resource_id, _ in expected)
            or group['initial_session_id'] == session['id']):
        return False
    with Ledger(root/'session-public-resources.sqlite', binding['run_id'], fingerprint(binding), read_only=True) as ledger:
        rows = ledger.resource_snapshot()
        fields = ('handle', 'case_id', 'attempt', 'kind', 'parent', 'request_id', 'origin', 'resource_id', 'state')
        if (len(rows) != 3 or {(r['handle'], r['kind'], r['resource_id'], r['parent']) for r in rows} != set(expected)
                or any(r['state'] != 'deleted' or r['origin'] != 'created' or r['attempt'] != 1 or r['case_id'] != CASE_ID for r in rows)
                or case.get('ledger', {}).get('resources') != [{k: r[k] for k in fields} for r in rows]
                or case['ledger'].get('cleanup_plan') != ledger.cleanup_plan()):
            return False
    by_name = {op['operation']: op for op in operations}
    if (rows[0]['request_id'] != by_name['group-create']['submitted_request_id']
            or rows[2]['request_id'] != by_name['session-create']['submitted_request_id']):
        return False
    websocket = case.get('websocket', {})
    if (websocket.get('status') != 'CLOSED' or websocket.get('http_status') != 101
            or websocket.get('messages_sent') != 0 or websocket.get('business_messages_received') != 0
            or not by_name['session-ws-connect'].get('connect_request_id')):
        return False
    admissions = case.get('deletion_admission', [])
    if [row.get('target') for row in admissions] != ['session', 'group']:
        return False
    for row in admissions:
        if (row.get('status') != 'LOCAL_DELETION_ADMITTED' or row.get('websocket') != 'CLOSED'
                or row.get('isolated_config') is not True or row.get('engine_started_by_executor') is not False
                or any(row.get(name) != 0 for name in ('provider_bindings', 'queued_deliveries', 'state_machine_runs'))
                or row.get('business_session_terminal') != 'NOT_CLAIMED'):
            return False
    for name, error in (('session', 'session_not_found'), ('group', 'group_not_found')):
        result = case.get(name+'_delete_verification', {})
        if (result.get('status') != 'PASS' or result.get('http_status') != 200
                or result.get('not_found_http_status') != 404 or result.get('not_found_error_code') != error
                or result.get('sqlite', {}).get('status') != 'PASS'):
            return False
    session_counts = case['session_delete_verification']['sqlite']
    if any(session_counts.get(key) != 0 for key in ('session_rows', 'session_participant_rows', 'delivery_rows', 'state_machine_run_rows')):
        return False
    if (session_counts.get('group_rows') != 1 or session_counts.get('initial_session_rows') != 1
            or session_counts.get('message_rows') != 2 or session_counts.get('history_preserved') is not True):
        return False
    group_counts = case['group_delete_verification']['sqlite']
    if any(group_counts.get(key) != 0 for key in ('group_rows', 'group_participant_rows', 'session_rows',
                                                  'session_participant_rows', 'delivery_rows', 'state_machine_run_rows')):
        return False
    if group_counts.get('message_rows') != 4 or group_counts.get('history_preserved') is not True:
        return False
    credentials = case.get('credential_persistence', {})
    return (credentials == {'gateway_principal': False, 'connection_token': False, 'websocket_url': False, 'private_signing_key': False}
            and case.get('binding_digest') == fingerprint(binding)
            and case.get('fixture_digest') == fingerprint(read(root/'local-fixtures.json'))
            and case.get('source_binding_digest') == fingerprint(binding['source_binding'])
            and bool(case.get('fixture_before')) and case['fixture_before'] == case.get('fixture_after')
            and bool(case.get('unrelated_before')) and case['unrelated_before'] == case.get('unrelated_after')
            and case.get('storage_before_delete', {}).get('status') == 'PASS'
            and case['storage_before_delete'].get('history', {}).get('row_count') == 4
            and case.get('history_readback', {}).get('status') == 'PASS'
            and case['history_readback'].get('message_count') == 1
            and case.get('disposition') == 'GROUP_SESSIONS_DELETED_BOOTSTRAP_HISTORY_RETAINED')


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SLICE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID], 'legacy_inventory_count': len(binding['legacy_inventory']),
                      'full_release_status': 'NOT_QUALIFIED', 'full_story_status': 'NOT_MIGRATED',
                      'selected_requirement_ids': [V1_REQUIREMENTS[0]], 'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'requirements': projected.get('requirements', [{'id': name, 'status': 'NOT_RUN'} for name in V1_REQUIREMENTS]),
            'reports': {'expected_assertion_count': 31, 'parent_requirement_count': 9, 'observed_assertion_count': len(projected.get('assertions', []))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'OWNED_PUBLIC_GROUP_SESSION_AND_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote authentication and authorization', 'Bot engine reasoning',
                                           'remaining eight Session helpers', 'remote Session execution and cleanup', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_session_public(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Session public source changed before report publication')
    _require(read(root/'session-public-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Session public publication must follow actual runtime finalization')
    pending = root/'session-public-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'session-public-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'session-public-completion.json') == completion_digest,
             'Session public source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_session_public_report(root: Path):
    _require(not root.is_symlink(), 'Session public report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'session-public-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Session public publication is incomplete')
        completion = read(root/'session-public-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Session public completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()),
                 'Session public evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'session-public-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Session public evidence changed during projection')
    except (OSError, RunStateError, LedgerError, sqlite3.DatabaseError, ValueError, KeyError, TypeError):
        issues.append('Session public completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
