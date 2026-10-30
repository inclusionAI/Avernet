"""Frozen six-helper Session reporting, composing the original public result."""
from pathlib import Path
import sqlite3

from .inventory import binding_matches_reviewed_release, executor_digest
from .ledger import Ledger, LedgerError
from .local_session_reporting import (
    _sha, _exclusive, _require, V1_REQUIREMENTS, SOURCES as PUBLIC_SOURCES,
    OUTPUTS as PUBLIC_OUTPUTS, build_session_public_report)
from .local_session_contract import PUBLIC_ASSERTION_IDS, SHARING_ASSERTION_IDS
from .profile import fingerprint
from .run_state import RunStateError, read

CASE_ID = 'LOCAL-SESSION-SHARING-01'
SUITE = 'owned-local-session-sharing-v1'
SOURCES = (*PUBLIC_SOURCES, *PUBLIC_OUTPUTS, 'session-public-completion.json',
           'session-sharing-plan.json', 'session-sharing-binding.json',
           'session-sharing-flow-report.json', 'local-session-sharing.json',
           'session-sharing-resources.sqlite')
OUTPUTS = ('sharing-reports/summary.json', 'sharing-reports/junit.xml', 'sharing-reports/summary.md')
COMPLETION = 'owned-local-session-sharing-completion-v1'


def _sources(root):
    return {name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}


def _bound(root):
    binding = read(root/'session-sharing-binding.json')
    plan = read(root/'session-sharing-plan.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('case_id') == CASE_ID and binding.get('runtime_dir') == str(root)
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS[:6])
             and binding.get('parent_requirement_ids') == list(V1_REQUIREMENTS)
             and [row['id'] for row in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])) and binding.get('store_messages') is True
             and binding.get('public_journal_digest') == fingerprint(read(root/'local-session-public.json'))
             and {k: v for k, v in binding.items() if k != 'public_journal_digest'} == plan,
             'Session sharing original plan or binding differs')
    return binding


V1_OPERATIONS = (
    'eng-visibility-before', 'eng-visibility-public', 'eng-visibility-public-read',
    'h1-group-create', 'h1-session-create', 'h1-session-list', 'h1-session-get',
    'h1-group-delete', 'h1-group-deleted-404',
    'h2-group-create', 'h2-group-invite', 'h2-group-join', 'h2-group-delete', 'h2-group-deleted-404',
    'h3-group-create', 'h3-session-create', 'h3-session-invite', 'h3-group-delete', 'h3-group-deleted-404',
    'h4-group-create', 'h4-bot-groups', 'h4-group-delete', 'h4-group-deleted-404',
    'h5-group-create', 'h5-session-create', 'h5-session-invite', 'h5-session-join',
    'h5-group-delete', 'h5-group-deleted-404', 'eng-visibility-restore', 'eng-visibility-restored-read',
)
V1_CLI_OPERATIONS = ('h1-group-create', 'h1-session-create', 'h1-session-list', 'h1-session-get',
    'h2-group-create', 'h3-group-create', 'h3-session-create', 'h3-session-invite', 'h4-group-create', 'h5-group-create')


def _business(root, binding, case):
    operations = case.get('operations', [])
    checks = case.get('checks', [])
    if ([op.get('operation') for op in operations] != list(V1_OPERATIONS)
            or [row.get('id') for row in checks] != list(SHARING_ASSERTION_IDS)
            or any(row.get('status') != 'PASS' for row in checks)):
        return False
    for op in operations:
        status = 404 if op['operation'].endswith('-deleted-404') else 201 if op['operation'].endswith('-session-create') else 200
        if (op.get('status') != 'ACKNOWLEDGED' or op.get('http_status') != status
                or [row.get('status') for row in op.get('transitions', [])] != ['INTENDED', 'ACKNOWLEDGED']
                or not isinstance(op.get('submitted_request_id'), str) or not op['submitted_request_id']):
            return False
        if op['operation'] in V1_CLI_OPERATIONS:
            if (op.get('transport') != 'PRODUCT_CLI' or op.get('cli_exit_code') != 0
                    or op.get('credential_cleanup') != 'CLEARED' or op.get('auth_contract') != 'owned-bot-bearer'
                    or op.get('output_validation', {}).get('raw_output_persisted') is not False):
                return False
    if len({op['submitted_request_id'] for op in operations}) != len(operations):
        return False
    resources = case.get('resources', {})
    groups, sessions = resources.get('groups', {}), resources.get('sessions', {})
    if set(groups) != {'h1', 'h2', 'h3', 'h4', 'h5'} or set(sessions) != {'h1', 'h3', 'h5'}:
        return False
    expected = []
    for name, group in groups.items():
        expected += [(group['handle'], 'group', group['id'], None),
                     (group['initial_session_handle'], 'session', group['initial_session_id'], group['handle'])]
        if name in sessions:
            expected.append((sessions[name]['handle'], 'session', sessions[name]['id'], group['handle']))
    if len({row[2] for row in expected}) != 13:
        return False
    with Ledger(root/'session-sharing-resources.sqlite', binding['run_id'], fingerprint(binding), read_only=True) as ledger:
        rows = ledger.resource_snapshot()
    fields = ('handle', 'case_id', 'attempt', 'kind', 'parent', 'request_id', 'origin', 'resource_id', 'state')
    if (len(rows) != 13 or {(r['handle'], r['kind'], r['resource_id'], r['parent']) for r in rows} != set(expected)
            or any(r['case_id'] != CASE_ID or r['attempt'] != 1 or r['origin'] != 'created' or r['state'] != 'deleted' for r in rows)
            or resources.get('ledger') != [{k: r[k] for k in fields} for r in rows]):
        return False
    admissions = case.get('deletion_admissions', [])
    if not admissions:
        return False
    for item in admissions:
        if (item.get('status') != 'LOCAL_DELETION_ADMITTED' or item.get('engine') != 'NOT_STARTED'
                or item.get('receiver') != 'NOT_OPENED' or item.get('business_session_terminal') != 'NOT_CLAIMED'
                or any(item.get(key) != 0 for key in ('provider_bindings', 'queued_deliveries', 'state_machine_runs'))):
            return False
    proofs = case.get('deletion_proofs', [])
    histories = case.get('retained_histories', {})
    if [p.get('helper') for p in proofs] != ['h1', 'h2', 'h3', 'h4', 'h5']:
        return False
    for proof in proofs:
        name = proof['helper']; n = 2 if name in sessions else 1
        sql = proof.get('sqlite', {})
        if (proof.get('group_id') != groups[name]['id'] or proof.get('http_status') != 200
                or proof.get('not_found_http_status') != 404 or sql.get('status') != 'PASS'
                or proof.get('ledger_mark_order') != ['session']*n+['group']
                or sql.get('child_session_count') != n
                or sql.get('retained_history') != histories.get('groups', {}).get(groups[name]['id'])
                or any(sql.get(key) != 0 for key in ('group_rows', 'group_participant_rows', 'session_rows',
                                                    'session_participant_rows', 'delivery_rows', 'state_machine_run_rows'))):
            return False
    for key in ('fixture_state', 'unrelated_state'):
        record = case.get(key, {})
        if record.get('preserved') is not True or not record.get('before') or record['before'] != record.get('after'):
            return False
    visibility = case.get('visibility', {})
    return (case.get('binding_digest') == fingerprint(binding)
            and case.get('plan_digest') == fingerprint(read(root/'session-sharing-plan.json'))
            and case.get('source_binding_digest') == fingerprint(binding['source_binding'])
            and case.get('predecessor', {}).get('journal_digest') == binding['public_journal_digest']
            and case['predecessor'].get('retained_history') == read(root/'local-session-public.json')['storage_before_delete']['history']
            and case.get('credential_contexts_cleared') == {'count': 10, 'all_cleared': True}
            and case.get('cli') == {'operation_count': 10, 'acknowledged_count': 10}
            and visibility == {'before': 'protected', 'admitted': 'public', 'after': 'protected', 'restored': True}
            and histories.get('preserved') is True and histories.get('before_delete') == histories.get('after_delete')
            and histories.get('before_delete', {}).get('bootstrap_row_count') == 16
            and case.get('resource_disposition') == 'LEDGER_13_TOMBSTONES_HISTORY_RETAINED')


def _project(root, binding):
    public = build_session_public_report(root)
    flow = read(root/'session-sharing-flow-report.json')
    case = read(root/'local-session-sharing.json') if (root/'local-session-sharing.json').exists() else {}
    runtime = read(root/'owned-bcs.json')
    _require(flow.get('case') == case and flow.get('shutdown') == runtime
             and flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest'],
             'Session sharing flow differs from original journals')
    assertions = case.get('assertions', [])
    complete = (public['status'] == 'LOCAL_SLICE_PASS' and case.get('status') == 'LOCAL_SESSION_SHARING_PASS'
                and [row.get('id') for row in assertions] == list(SHARING_ASSERTION_IDS)
                and all(row.get('status') == 'PASS' for row in assertions)
                and case.get('run_id') == binding['run_id']
                and case.get('case_id') == CASE_ID and case.get('executor_digest') == binding['executor_digest']
                and set(SOURCES) == _sources(root))
    if complete:
        complete = _business(root, binding, case)
    stopped = (runtime.get('status') == 'STOPPED' and runtime.get('returncode') == 0
               and runtime.get('run_id') == binding['run_id'] and runtime.get('binary_sha256') == binding['bcs_binary_sha256'])
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_SESSION_SHARING_PASS' else
              'FAIL' if any(row.get('status') == 'FAIL' for row in case.get('assertions', [])) else 'BLOCKED')
    public_status = public['requirements'][0]['status'] if public.get('requirements') else 'NOT_RUN'
    sharing_assertions = case.get('assertions', [])
    sharing_all_pass = (len(sharing_assertions) == len(SHARING_ASSERTION_IDS)
                        and all(r.get('status') == 'PASS' for r in sharing_assertions))
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name,
                              'status': (public_status if index == 0 else
                                         'PASS' if 1 <= index <= 5 and sharing_all_pass else 'NOT_RUN')}
                             for index, name in enumerate(V1_REQUIREMENTS)]}


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SLICE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID], 'legacy_inventory_count': len(binding['legacy_inventory']),
                      'full_release_status': 'NOT_QUALIFIED', 'full_story_status': 'NOT_MIGRATED',
                      'selected_requirement_ids': list(V1_REQUIREMENTS[:6]), 'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'requirements': projected.get('requirements', [{'id': name, 'status': 'NOT_RUN'} for name in V1_REQUIREMENTS]),
            'reports': {'expected_assertion_count': 37, 'parent_requirement_count': 9,
                        'observed_assertion_count': (
                            (31 if projected.get('requirements') and projected['requirements'][0]['status'] == 'PASS' else 0)
                            + (6 if len(projected.get('requirements', [])) >= 6
                               and all(r['status'] == 'PASS' for r in projected['requirements'][1:6]) else 0))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'OWNED_GROUPS_SESSIONS_AND_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote gateway authentication', 'Bot engine reasoning',
                                           'remaining lifecycle service collection helpers', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_sharing(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'], 'Session sharing binding changed')
    _require(read(root/'session-sharing-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Session sharing publication must follow actual stopped-process result')
    pending = root/'session-sharing-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'sharing-reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'session-sharing-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'session-sharing-completion.json') == completion_digest,
             'Session sharing evidence changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_sharing_report(root: Path):
    _require(not root.is_symlink(), 'Session sharing requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'session-sharing-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Session sharing publication incomplete')
        completion = read(root/'session-sharing-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Session sharing completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()), 'Session sharing evidence changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'session-sharing-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(), 'Session sharing changed during projection')
    except (OSError, RunStateError, LedgerError, sqlite3.DatabaseError, ValueError, KeyError, TypeError):
        issues.append('Session sharing completion, source or outputs missing, changed or invalid')
    return _summary(binding, projected, issues)
