"""Frozen complete Session reporting, composing both prior stage results."""
from pathlib import Path
import sqlite3

from .inventory import binding_matches_reviewed_release, executor_digest
from .ledger import Ledger, LedgerError
from .local_session_reporting import _sha, _exclusive, _require, V1_REQUIREMENTS
from .local_session_sharing_reporting import (SOURCES as SHARING_SOURCES,
    OUTPUTS as SHARING_OUTPUTS, build_sharing_report)
from .local_session_contract import COMPLETION_ASSERTION_IDS
from .profile import fingerprint
from .run_state import RunStateError, read

CASE_ID = 'LOCAL-SESSION-FLOW-01'
SUITE = 'owned-local-session-flow-v1'
SOURCES = (*SHARING_SOURCES, *SHARING_OUTPUTS, 'session-sharing-completion.json',
           'session-completion-plan.json', 'session-completion-binding.json',
           'session-flow-report.json', 'local-session-completion.json',
           'session-completion-resources.sqlite')
OUTPUTS = ('session-reports/summary.json', 'session-reports/junit.xml', 'session-reports/summary.md')
COMPLETION = 'owned-local-session-flow-completion-v1'


def _sources(root):
    return {name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}


def _bound(root):
    binding = read(root/'session-completion-binding.json')
    plan = read(root/'session-completion-plan.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('case_id') == CASE_ID and binding.get('runtime_dir') == str(root)
             and binding.get('selected_requirement_ids') == list(V1_REQUIREMENTS)
             and binding.get('parent_requirement_ids') == list(V1_REQUIREMENTS)
             and [row['id'] for row in binding['source_binding']['requirements']] == list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])) and binding.get('store_messages') is True
             and binding.get('public_journal_digest') == fingerprint(read(root/'local-session-public.json'))
             and binding.get('sharing_journal_digest') == fingerprint(read(root/'local-session-sharing.json'))
             and {k: v for k, v in binding.items() if k not in ('public_journal_digest', 'sharing_journal_digest')} == plan,
             'Session flow original plan or binding differs')
    return binding


V1_OPERATIONS = ('lifecycle-visibility-before',
 'lifecycle-visibility-public',
 'lifecycle-visibility-public-read',
 'lifecycle-group-create',
 'lifecycle-session-create',
 'lifecycle-session-patch',
 'lifecycle-member-add',
 'lifecycle-member-mute',
 'lifecycle-chat',
 'lifecycle-messages',
 'lifecycle-member-remove',
 'lifecycle-session-delete',
 'lifecycle-session-list-absence',
 'lifecycle-group-delete',
 'lifecycle-group-deleted-404',
 'lifecycle-visibility-restore',
 'lifecycle-visibility-restored-read',
 'complete-visibility-before',
 'complete-visibility-public',
 'complete-visibility-public-read',
 'complete-group-create',
 'complete-session-create',
 'complete-delivery-get',
 'complete-delivery-batch',
 'complete-cancel-one',
 'complete-cancel-message',
 'complete-manual-resolve',
 'complete-chat-complete',
 'complete-chat-read',
 'complete-settings',
 'complete-service-create',
 'complete-service-read',
 'complete-service-foreign',
 'complete-service-wrong-complete',
 'complete-chat-delete',
 'complete-service-delete',
 'complete-group-delete',
 'complete-group-deleted-404',
 'complete-visibility-restore',
 'complete-visibility-restored-read',
 'collection-visibility-before',
 'collection-visibility-public',
 'collection-visibility-public-read',
 'collection-group-create',
 'collection-session-create',
 'collection-initial-empty',
 'collection-missing-participant',
 'collection-collect',
 'collection-collect-repeat',
 'collection-pm-contains',
 'collection-eng-empty',
 'collection-qa-nonparticipant',
 'collection-uncollect',
 'collection-uncollect-repeat',
 'collection-final-empty',
 'collection-group-delete',
 'collection-group-deleted-404',
 'collection-visibility-restore',
 'collection-visibility-restored-read')

def _business(root, binding, case):
    operations, checks = case.get('operations', []), case.get('checks', [])
    if ([row.get('operation') for row in operations] != list(V1_OPERATIONS)
            or [row.get('id') for row in checks] != ['lifecycle', 'complete', 'collection']
            or any(row.get('status') != 'PASS' for row in checks)):
        return False
    status_codes = {'complete-cancel-one': 400, 'complete-cancel-message': 400,
                    'complete-manual-resolve': 403, 'complete-service-create': 202,
                    'complete-service-foreign': 403, 'complete-service-wrong-complete': 403,
                    'collection-missing-participant': 400, 'collection-qa-nonparticipant': 404}
    settings_form = next((row.get('evidence', {}).get('settings_form')
                          for row in case.get('assertions', [])
                          if row.get('id') == 'SESSION.complete-invoke.service-start'), None)
    for op in operations:
        name = op['operation']
        status = 404 if name.endswith('group-deleted-404') else 201 if name.endswith('session-create') else status_codes.get(name, 200)
        if name == 'complete-settings':
            expected_form = 'retired_410' if op.get('http_status') == 410 else 'service_spec_ack'
            if (op.get('http_status') not in (200, 410)
                    or settings_form != expected_form):
                return False
        elif op.get('http_status') != status:
            return False
        if (op.get('status') != 'ACKNOWLEDGED'
                or [row.get('status') for row in op.get('transitions', [])] != ['INTENDED', 'ACKNOWLEDGED']
                or not isinstance(op.get('submitted_request_id'), str) or not op['submitted_request_id']):
            return False
        if name in ('lifecycle-group-create', 'collection-group-create'):
            if (op.get('transport') != 'PRODUCT_CLI' or op.get('cli_exit_code') != 0
                    or op.get('credential_cleanup') != 'CLEARED' or op.get('auth_contract') != 'owned-bot-bearer'):
                return False
    if len({op['submitted_request_id'] for op in operations}) != len(operations):
        return False
    resources = case.get('resources', {})
    groups, sessions = resources.get('groups', {}), resources.get('sessions', {})
    helpers = ('lifecycle', 'complete', 'collection')
    if set(groups) != set(helpers) or set(sessions) != set(helpers):
        return False
    expected = []
    for helper in helpers:
        g, session = groups[helper], sessions[helper]
        expected.extend([(g['handle'], 'group', g['id'], None),
                         (g['initial_session_handle'], 'session', g['initial_session_id'], g['handle']),
                         (session['handle'], 'session', session['id'], g['handle'])])
    service = resources.get('service_session', {})
    expected.append((service['handle'], 'session', service['id'], groups['complete']['handle']))
    if len({row[2] for row in expected}) != 10:
        return False
    fields = ('handle', 'case_id', 'attempt', 'kind', 'parent', 'request_id', 'origin', 'resource_id', 'state')
    with Ledger(root/'session-completion-resources.sqlite', binding['run_id'], fingerprint(binding), read_only=True) as ledger:
        rows = ledger.resource_snapshot()
    if (len(rows) != 10 or {(r['handle'], r['kind'], r['resource_id'], r['parent']) for r in rows} != set(expected)
            or any(r['case_id'] != CASE_ID or r['attempt'] != 1 or r['origin'] != 'created' or r['state'] != 'deleted' for r in rows)
            or resources.get('ledger') != [{key: row[key] for key in fields} for row in rows]):
        return False
    admissions, proofs = case.get('deletion_admissions', []), case.get('deletion_proofs', [])
    if ([row.get('helper') for row in admissions] != list(helpers)
            or [row.get('helper') for row in proofs] != list(helpers)):
        return False
    for admission, proof in zip(admissions, proofs):
        helper = admission['helper']
        if (admission.get('status') != 'LOCAL_DELETION_ADMITTED'
                or admission.get('engine') != 'NOT_STARTED' or admission.get('receiver') != 'NOT_OPENED'
                or admission.get('isolated_config') is not True or admission.get('service_business_execution') != 'NOT_CLAIMED'
                or any(admission.get(key) != 0 for key in ('provider_bindings', 'queued_deliveries', 'state_machine_runs'))):
            return False
        sql = proof.get('sqlite', {})
        if (sql.get('status') != 'PASS' or not sql.get('retained_history')
                or proof.get('ledger_mark_order') != ['session']*(2 if helper == 'collection' else 1)+['group']
                or any(sql.get(key) != 0 for key in ('group_rows', 'group_participant_rows', 'session_rows',
                                                   'session_participant_rows', 'delivery_rows', 'state_machine_run_rows'))):
            return False
    for key in ('fixture_state', 'unrelated_state'):
        state = case.get(key, {})
        if state.get('preserved') is not True or not state.get('before') or state['before'] != state.get('after'):
            return False
    histories = case.get('histories', {})
    if (not histories.get('predecessor_before') or histories['predecessor_before'] != histories.get('predecessor_after')
            or histories['predecessor_before'].get('row_count') != 21):
        return False
    visibility = case.get('visibility', {})
    if set(visibility) != set(helpers) or any(visibility[h].get('before') != 'protected' or visibility[h].get('restored') is not True for h in helpers):
        return False
    evidence = case.get('helper_evidence', {})
    lifecycle = evidence.get('lifecycle', {}); completion = evidence.get('complete', {}); collection = evidence.get('collection', {})
    return (case.get('binding_digest') == fingerprint(binding)
            and case.get('plan_digest') == fingerprint(read(root/'session-completion-plan.json'))
            and case.get('source_binding_digest') == fingerprint(binding['source_binding'])
            and case.get('predecessors') == {'public': binding['public_journal_digest'], 'sharing': binding['sharing_journal_digest']}
            and case.get('cli') == {'operation_count': 2, 'acknowledged_count': 2, 'credential_contexts_cleared': True}
            and lifecycle.get('chat_http_status') == 200 and lifecycle.get('message', {}).get('status') == 'PASS'
            and lifecycle['message'].get('http_contains_message') is True
            and completion.get('manual_chat_terminality') == 'COMPLETED'
            and completion.get('service_business_execution') == 'NOT_CLAIMED' and completion.get('empty_delivery_rows') is True
            and all(collection.get(key) is True for key in ('initial_empty', 'idempotent_collect', 'per_bot_isolation', 'qa_nonparticipant_404', 'final_empty'))
            and case.get('resource_disposition') == 'LEDGER_10_TOMBSTONES')


def _project(root, binding):
    predecessor = build_sharing_report(root)
    flow = read(root/'session-flow-report.json')
    case = read(root/'local-session-completion.json') if (root/'local-session-completion.json').exists() else {}
    runtime = read(root/'owned-bcs.json')
    _require(flow.get('case') == case and flow.get('shutdown') == runtime
             and flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest'],
             'Session flow flow differs from original journals')
    assertions = case.get('assertions', [])
    complete = (predecessor['status'] == 'LOCAL_SLICE_PASS' and case.get('status') == 'LOCAL_SESSION_COMPLETION_PASS'
                and [row.get('id') for row in assertions] == list(COMPLETION_ASSERTION_IDS)
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
              'PASS' if complete and stopped and cleared and flow.get('status') == 'LOCAL_SESSION_FLOW_PASS' else
              'FAIL' if any(row.get('status') == 'FAIL' for row in case.get('assertions', [])) else 'BLOCKED')
    completion_assertions = case.get('assertions', [])
    completion_all_pass = (len(completion_assertions) == len(COMPLETION_ASSERTION_IDS)
                           and all(r.get('status') == 'PASS' for r in completion_assertions))
    prior = predecessor.get('requirements', [])
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'requirements': [{'id': name,
                              'status': (prior[index]['status'] if index < 6
                                         else 'PASS' if 6 <= index <= 8 and completion_all_pass else 'NOT_RUN')}
                             for index, name in enumerate(V1_REQUIREMENTS)]}


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID], 'legacy_inventory_count': len(binding['legacy_inventory']),
                      'full_release_status': 'NOT_QUALIFIED', 'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                      'selected_requirement_ids': list(V1_REQUIREMENTS), 'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'requirements': projected.get('requirements', [{'id': name, 'status': 'NOT_RUN'} for name in V1_REQUIREMENTS]),
            'reports': {'expected_assertion_count': 87, 'parent_requirement_count': 9,
                        'observed_assertion_count': (
                            (31 if projected.get('requirements') and projected['requirements'][0]['status'] == 'PASS' else 0)
                            + (6 if len(projected.get('requirements', [])) >= 6
                               and all(r['status'] == 'PASS' for r in projected['requirements'][1:6]) else 0)
                            + (50 if len(projected.get('requirements', [])) >= 9
                               and all(r['status'] == 'PASS' for r in projected['requirements'][6:9]) else 0))},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues}, 'routes': [],
            'cleanup': {'verification_scope': 'OWNED_GROUPS_SESSIONS_AND_RUNTIME_ONLY',
                        'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': ['remote gateway authentication', 'Bot engine reasoning',
                                           'remote execution closure and cleanup', 'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'}, 'ready_to_execute': False}


def publish_session_flow(runtime, binding, flow):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'], 'Session flow binding changed')
    _require(read(root/'session-flow-report.json') == flow and read(root/'owned-bcs.json') == runtime._record(),
             'Session flow publication must follow actual stopped-process result')
    pending = root/'session-flow-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root/name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root/'session-reports', {'all'})
    output_hashes = {name: _sha(root/name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root/'session-flow-completion.json', completion)
    _require(_sources(root) == set(hashes) and all(_sha(root/name) == digest for name, digest in hashes.items())
             and all(_sha(root/name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest and _sha(root/'session-flow-completion.json') == completion_digest,
             'Session flow evidence changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_session_flow_report(root: Path):
    _require(not root.is_symlink(), 'Session flow requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root/'session-flow-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Session flow publication incomplete')
        completion = read(root/'session-flow-completion.json')
        _require(completion.get('contract') == COMPLETION and completion.get('binding_digest') == fingerprint(binding),
                 'Session flow completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items()), 'Session flow evidence changed')
        projected = _project(root, binding)
        _require(all(_sha(root/name) == digest for name, digest in hashes.items())
                 and all(_sha(root/name) == digest for name, digest in outputs.items())
                 and read(root/'session-flow-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(), 'Session flow changed during projection')
    except (OSError, RunStateError, LedgerError, sqlite3.DatabaseError, ValueError, KeyError, TypeError):
        issues.append('Session flow completion, source or outputs missing, changed or invalid')
    return _summary(binding, projected, issues)
