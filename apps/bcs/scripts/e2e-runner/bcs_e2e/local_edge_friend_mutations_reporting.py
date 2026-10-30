"""Offline verification and reports for the seven reviewed release-parent
Edge friend-mutation cases plus the two backlog TestCase slices."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .edge_friend_mutation_case import (
    BACKLOG_CASE_IDS,
    CASE_IDS,
    PARENTS,
    RELEASE_CASE_IDS,
    TOTAL_ASSERTION_COUNT,
    edge_friend_mutation_case_plan,
)
from .inventory import binding_matches_reviewed_release, executor_digest, reviewed_release_inventory
from .local_edge_friend_mutations_run import journal_filename
from .profile import fingerprint
from .run_state import RunStateError, read

SUITE = 'owned-local-edge-friend-mutations-v1'
COMPLETION = 'owned-local-edge-friend-mutations-completion-v1'
JOURNALS = tuple(journal_filename(case_id) for case_id in CASE_IDS)
SOURCES = (
    'edge-friend-mutations-binding.json', 'edge-friend-mock.json',
    'edge-friend-mutations-flow-report.json', 'bcs-config.json',
    'owned-bcs.json', 'local-fixtures.json',
    'edge-friend-ensure-admission.json', *JOURNALS,
)
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path: Path):
    _require(not path.parent.is_symlink(),
             'Edge friend-mutation evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_size <= 8 * 1024 * 1024,
                 'Edge friend-mutation evidence must be a bounded regular unshared file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024,
             'Edge friend-mutation evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(raw).hexdigest()


def _sources(root: Path):
    return {name for name in SOURCES if (root / name).exists() or (root / name).is_symlink()}


def _bound(root: Path):
    binding = read(root / 'edge-friend-mutations-binding.json')
    plans = {row.case_id: row for row in
             (edge_friend_mutation_case_plan(case_id) for case_id in CASE_IDS)}
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root)
             and binding.get('case_ids') == list(CASE_IDS)
             and binding.get('release_parent_case_ids') == list(RELEASE_CASE_IDS)
             and binding.get('backlog_case_ids') == list(BACKLOG_CASE_IDS)
             and binding.get('expected_assertion_count') == TOTAL_ASSERTION_COUNT
             and set(binding.get('source_bindings', {})) == set(CASE_IDS)
             and set(binding.get('selected_requirement_ids', {})) == set(CASE_IDS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', []))
             and set(PARENTS) == {plans[case_id].parent_id for case_id in CASE_IDS},
             'Edge friend-mutation frozen complete binding is invalid')
    for case_id in CASE_IDS:
        plan = plans[case_id]
        selected = binding['selected_requirement_ids'][case_id]
        source_ids = [row['id']
                      for row in binding['source_bindings'][case_id]['requirements']]
        _require(selected == list(plan.requirement_ids)
                 and source_ids == list(plan.requirement_ids),
                 f'Edge friend-mutation {case_id} source binding differs')
    return binding


def _mock(root: Path, binding: dict) -> dict:
    mock = read(root / 'edge-friend-mock.json')
    _require(mock.get('schema_version') == 1
             and mock.get('run_id') == binding['run_id']
             and mock.get('script') == 'http_provider_judge_mock.py'
             and mock.get('status') == 'STOPPED'
             and mock.get('ready') is True
             and mock.get('stop_signal_accepted') is True
             and mock.get('engine_process') == 'NOT_STARTED'
             and mock.get('origin') == binding.get('friend_work_order_base_url')
             and type(mock.get('returncode')) is int,
             'Edge friend-mutation mock child differs from its binding')
    _require(mock.get('stdout_bytes') <= 1024 * 1024
             and mock.get('stderr_bytes') <= 1024 * 1024,
             'Edge friend-mutation mock child exceeded its evidence budget')
    return mock


def _project(root: Path, binding: dict):
    flow = read(root / 'edge-friend-mutations-flow-report.json')
    runtime = read(root / 'owned-bcs.json')
    mock = _mock(root, binding)
    journals = [read(root / journal_filename(case_id)) for case_id in CASE_IDS]
    summary_cases = flow.get('cases', [])
    _require(flow.get('run_id') == binding['run_id']
             and flow.get('suite') == SUITE
             and flow.get('case_ids') == list(binding['case_ids'])
             and flow.get('release_parent_case_ids') == list(RELEASE_CASE_IDS)
             and flow.get('backlog_case_ids') == list(BACKLOG_CASE_IDS)
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime
             and flow.get('mock_child') == mock
             and [row.get('case_id') for row in summary_cases] == list(binding['case_ids'])
             and [row.get('status') for row in summary_cases] == ['PASS'] * len(CASE_IDS)
             and [row.get('status') for row in journals] == ['PASS'] * len(CASE_IDS),
             'Edge friend-mutation final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Edge friend-mutation runtime binding differs')
    plans = {row.case_id: row for row in
             (edge_friend_mutation_case_plan(case_id) for case_id in CASE_IDS)}
    expected_rows = []
    functional = True
    evidence = True
    observed_assertions = 0
    for case, journal in zip(summary_cases, journals):
        plan = plans[case['case_id']]
        rows = journal.get('steps', [])
        statuses = [row.get('status') for row in rows]
        complete_case = (case.get('status') == 'PASS'
                         and case.get('error_type') is None
                         and case.get('completed_count') == case.get('total_count')
                         and journal.get('status') == 'PASS'
                         and journal.get('run_id') == binding['run_id']
                         and journal.get('executor_digest') == binding['executor_digest']
                         and journal.get('parent_id') == case.get('parent_id') == plan.parent_id
                         and len(rows) == len(plan.requirements)
                         and statuses == ['PASS'] * len(plan.requirements)
                         and all(row.get('requirement_id') == requirement.requirement_id
                                 and row.get('method') == requirement.adapter_method
                                 for row, requirement in zip(rows, plan.requirements))
                         and all(row.get('evidence', {}).get('transport') == 'owned-local-http'
                                 and 'Bearer' not in json.dumps(row, ensure_ascii=True)
                                 for row in rows))
        functional = functional and complete_case
        evidence = evidence and complete_case
        observed_assertions += sum(row.get('status') == 'PASS' for row in rows)
        for requirement in plan.requirements:
            expected_rows.append({
                'id': requirement.requirement_id,
                'status': next((row['status'] for row in rows
                                if row.get('requirement_id') == requirement.requirement_id),
                               'NOT_RUN'),
            })
    stopped = (runtime.get('status') == 'STOPPED'
               and type(runtime.get('returncode')) is int
               and runtime['returncode'] == 0
               and mock.get('status') == 'STOPPED'
               and mock.get('stop_signal_accepted') is True)
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    complete = (functional and evidence and stopped and cleared
                and flow.get('status') == 'LOCAL_EDGE_FRIEND_MUTATIONS_PASS')
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED'
              else 'PASS' if complete
              else 'FAIL' if any(row['status'] == 'FAIL' for row in expected_rows)
              else 'BLOCKED')
    case_results = []
    for case, journal in zip(summary_cases, journals):
        complete_case = (case.get('status') == 'PASS'
                         and journal.get('status') == 'PASS'
                         and len(journal.get('steps', [])) == len(plans[case['case_id']].requirements))
        case_results.append({
            'id': case['case_id'], 'version': 1,
            'status': 'PASS' if complete and complete_case else status,
            'functional': 'PASS' if functional and complete_case else 'INCOMPLETE',
            'evidence_status': 'PASS' if evidence and complete_case else 'INCOMPLETE',
            'cleanup': 'PASS' if stopped and cleared else 'BLOCKED',
            'release_parent_denominator': case['case_id'] in RELEASE_CASE_IDS,
            'backlog_slice': case['case_id'] in BACKLOG_CASE_IDS,
            'resource_disposition':
                'FRIEND_EDGES_REVOKED_BY_BUSINESS_API_REQUEST_ROWS_RETAINED_IN_OWNED_RUNTIME',
        })
    return {
        'status': status, 'functional': 'PASS' if functional else 'INCOMPLETE',
        'evidence_status': 'PASS' if evidence else 'INCOMPLETE',
        'cleanup': 'PASS' if complete else 'BLOCKED',
        'cases': case_results, 'requirements': expected_rows,
        'observed_assertion_count': observed_assertions,
    }


def _summary(binding: dict, projected: dict, issues: list[str]):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {
        'schema_version': 1, 'run_id': binding['run_id'],
        'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
        'target': {'tier': 'local', 'environment_id': binding['run_id']},
        'scope': {'suite': SUITE, 'selected_cases': list(binding['case_ids']),
                  'release_parent_cases': list(RELEASE_CASE_IDS),
                  'backlog_cases': list(BACKLOG_CASE_IDS),
                  'legacy_inventory_count': len(binding['legacy_inventory']),
                  'release_parent_denominator': len(reviewed_release_inventory()),
                  'backlog_case_count': len(BACKLOG_CASE_IDS),
                  'full_release_status': 'NOT_QUALIFIED',
                  'full_story_status': 'LOCAL_EXECUTOR_ONLY' if status == 'PASS' else 'NOT_QUALIFIED',
                  'remote_validation': 'NOT_RUN', 'manifest_digest': fingerprint(binding)},
        'runner': {'executor_digest': binding['executor_digest']},
        'cases': projected.get('cases', []),
        'requirements': projected.get('requirements', []),
        'reports': {'expected_assertion_count': binding['expected_assertion_count'],
                     'observed_assertion_count': projected.get('observed_assertion_count', 0)},
        'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
        'routes': [],
        'cleanup': {'verification_scope': 'OWNED_RUNTIME_BUSINESS_API_ONLY',
                      'physical_data_erasure': 'NOT_VERIFIED',
                      'associated_history_purge': 'NOT_PERFORMED'},
        'mock_boundary': {'not_proven': [
            'remote authentication and authorization', 'preprod route admission',
            'remote friend-mutation write admission',
            'singlebox route admission', 'full release qualification']},
        'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
        'ready_to_execute': False,
    }


def publish_edge_friend_mutations(runtime, binding: dict, flow: dict):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Edge friend-mutation source changed before report publication')
    _require(read(root / 'edge-friend-mutations-flow-report.json') == flow
             and read(root / 'owned-bcs.json') == runtime._record(),
             'Edge friend-mutation publication must follow actual runtime finalization')
    pending = root / 'edge-friend-mutations-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root / name) for name in _sources(root)}
    _require(set(hashes) == set(SOURCES),
             'Edge friend-mutation publication source set is incomplete')
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION,
                  'binding_digest': fingerprint(binding), 'sources': hashes,
                  'outputs': output_hashes}
    completion_digest = _exclusive(root / 'edge-friend-mutations-completion.json', completion)
    _require(set(_sources(root)) == set(hashes)
             and all(_sha(root / name) == digest for name, digest in hashes.items())
             and all(_sha(root / name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root / 'edge-friend-mutations-completion.json') == completion_digest,
             'Edge friend-mutation source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_edge_friend_mutations_report(root: Path):
    _require(not root.is_symlink(),
             'Edge friend-mutation report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected = {}
    issues = []
    try:
        pending = root / 'edge-friend-mutations-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),
                 'Edge friend-mutation publication is incomplete')
        completion = read(root / 'edge-friend-mutations-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Edge friend-mutation completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == set(SOURCES) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root / name) == digest for name, digest in hashes.items())
                 and all(_sha(root / name) == digest for name, digest in outputs.items()),
                 'Edge friend-mutation evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root / name) == digest for name, digest in hashes.items())
                 and all(_sha(root / name) == digest for name, digest in outputs.items())
                 and read(root / 'edge-friend-mutations-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Edge friend-mutation evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Edge friend-mutation completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
