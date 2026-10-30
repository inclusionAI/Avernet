"""Offline verification and reports for the owned-local Edge read cases."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import binding_matches_reviewed_release, executor_digest
from .profile import fingerprint
from .run_state import RunStateError, read

from .edge_read_case import (
    ADMISSION_CASE_ID,
    ADMISSION_PARENT,
    FRIEND_REQUESTS_CASE_ID,
    FRIEND_REQUESTS_PARENT,
    FRIEND_CONNECTIONS_CASE_ID,
    FRIEND_CONNECTIONS_PARENT,
    edge_read_case_plans,
)
from .edge_search_case import (
    CASE_ID as BOT_SEARCH_CASE_ID,
    PARENT as BOT_SEARCH_PARENT,
    FIXTURE_REQUIREMENT_ID as BOT_SEARCH_FIXTURE_REQUIREMENT_ID,
    edge_search_case_plan,
)
from .local_edge_read_run import journal_filename

SUITE = 'owned-local-edge-read-v1'
COMPLETION = 'owned-local-edge-read-completion-v1'
JOURNALS = tuple(journal_filename(case_id)
                 for case_id in (ADMISSION_CASE_ID, FRIEND_REQUESTS_CASE_ID,
                                 FRIEND_CONNECTIONS_CASE_ID, BOT_SEARCH_CASE_ID))
SOURCES = (
    'edge-read-binding.json', 'edge-read-flow-report.json', 'bcs-config.json',
    'owned-bcs.json', 'local-fixtures.json', 'bot-search-fixture.json', *JOURNALS,
)
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path: Path):
    _require(not path.parent.is_symlink(), 'Edge read evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8 * 1024 * 1024,
                 'Edge read evidence must be a bounded regular unshared file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024, 'Edge read evidence exceeded byte limit')
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
    binding = read(root / 'edge-read-binding.json')
    parents = (ADMISSION_PARENT, FRIEND_REQUESTS_PARENT, FRIEND_CONNECTIONS_PARENT,
               BOT_SEARCH_PARENT)
    plans = {row.parent_id: row for row in edge_read_case_plans()}
    search_plan = edge_search_case_plan()
    plans[BOT_SEARCH_PARENT] = search_plan
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('runtime_dir') == str(root)
             and binding.get('case_ids') == [ADMISSION_CASE_ID, FRIEND_REQUESTS_CASE_ID,
                                             FRIEND_CONNECTIONS_CASE_ID, BOT_SEARCH_CASE_ID]
             and binding.get('expected_assertion_count')
                 == sum(len(binding['selected_requirement_ids'][parent]) for parent in parents)
             and set(binding.get('source_bindings', {})) == set(parents)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Edge read frozen complete binding is invalid')
    for parent in parents:
        plan = plans[parent]
        selected = binding['selected_requirement_ids'][parent]
        source_ids = [row['id'] for row in binding['source_bindings'][parent]['requirements']]
        if parent == BOT_SEARCH_PARENT:
            expected_source_ids = selected[:6] + [BOT_SEARCH_FIXTURE_REQUIREMENT_ID] + selected[6:]
            _require(selected == list(plan.requirement_ids)
                     and source_ids == expected_source_ids,
                     f'Edge read {parent} source binding differs')
        else:
            _require(selected == list(plan.requirement_ids) and source_ids == list(plan.requirement_ids),
                     f'Edge read {parent} source binding differs')
    return binding


def _project(root: Path, binding: dict):
    flow = read(root / 'edge-read-flow-report.json')
    runtime = read(root / 'owned-bcs.json')
    journals = [read(root / journal_filename(case_id))
                for case_id in (ADMISSION_CASE_ID, FRIEND_REQUESTS_CASE_ID,
                                FRIEND_CONNECTIONS_CASE_ID, BOT_SEARCH_CASE_ID)]
    summary_cases = flow.get('cases', [])
    _require(flow.get('run_id') == binding['run_id'] and flow.get('suite') == SUITE
             and flow.get('case_ids') == list(binding['case_ids'])
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('shutdown') == runtime
             and [row.get('case_id') for row in summary_cases] == list(binding['case_ids'])
             and [row.get('status') for row in summary_cases] == ['PASS'] * len(binding['case_ids'])
             and [row.get('status') for row in journals] == ['PASS'] * len(binding['case_ids']),
             'Edge read final result differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Edge read runtime binding differs')
    plans = {row.case_id: row for row in edge_read_case_plans()}
    plans[BOT_SEARCH_CASE_ID] = edge_search_case_plan()
    expected_rows = []
    functional = True
    evidence = True
    observed_assertions = 0
    for case, journal in zip(summary_cases, journals):
        plan = plans[case['case_id']]
        rows = journal.get('steps', [])
        statuses = [row.get('status') for row in rows]
        complete_case = (case.get('status') == 'PASS' and case.get('error_type') is None
                         and case.get('completed_count') == case.get('total_count')
                         and journal.get('status') == 'PASS'
                         and journal.get('run_id') == binding['run_id']
                         and journal.get('executor_digest') == binding['executor_digest']
                         and journal.get('parent_id') == case.get('parent_id') == plan.parent_id
                         and len(rows) == len(plan.requirements)
                         and statuses == ['PASS'] * len(plan.requirements)
                         and all(row.get('requirement_id') == requirement.requirement_id
                                 and row.get('method') == plan.adapters[requirement.requirement_id]
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
                                if row.get('requirement_id') == requirement.requirement_id), 'NOT_RUN'),
            })
    stopped = runtime.get('status') == 'STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode'] == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    complete = (functional and evidence and stopped and cleared
                and flow.get('status') == 'LOCAL_EDGE_READ_PASS')
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete else
              'FAIL' if any(row['status'] == 'FAIL' for row in expected_rows) else 'BLOCKED')
    case_results = []
    for case, journal in zip(summary_cases, journals):
        plan = plans[case['case_id']]
        complete_case = (case.get('status') == 'PASS' and journal.get('status') == 'PASS'
                         and len(journal.get('steps', [])) == len(plan.requirements))
        case_results.append({
            'id': case['case_id'], 'version': 1,
            'status': 'PASS' if complete and complete_case else status,
            'functional': 'PASS' if functional and complete_case else 'INCOMPLETE',
            'evidence_status': 'PASS' if evidence and complete_case else 'INCOMPLETE',
            'cleanup': 'PASS' if stopped and cleared else 'BLOCKED',
            'resource_disposition': 'READ_ONLY_OWNED_RUNTIME_NO_BUSINESS_RESOURCE',
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
                  'legacy_inventory_count': len(binding['legacy_inventory']),
                  'full_release_status': 'NOT_QUALIFIED',
                  'full_story_status': 'LOCAL_READ_ONLY_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                  'remote_validation': 'NOT_RUN', 'manifest_digest': fingerprint(binding)},
        'runner': {'executor_digest': binding['executor_digest']},
        'cases': projected.get('cases', []), 'requirements': projected.get('requirements', []),
        'reports': {'expected_assertion_count': binding['expected_assertion_count'],
                    'observed_assertion_count': projected.get('observed_assertion_count', 0)},
        'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
        'routes': [], 'cleanup': {'verification_scope': 'OWNED_READ_ONLY_RUNTIME_ONLY',
                                  'physical_data_erasure': 'NOT_VERIFIED',
                                  'associated_history_purge': 'NOT_PERFORMED'},
        'mock_boundary': {'not_proven': [
            'remote authentication and authorization', 'preprod route admission',
            'singlebox route admission', 'remote Edge read lifecycle', 'full release qualification']},
        'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
        'ready_to_execute': False,
    }


def publish_edge_read(runtime, binding: dict, flow: dict):
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Edge read source changed before report publication')
    _require(read(root / 'edge-read-flow-report.json') == flow
             and read(root / 'owned-bcs.json') == runtime._record(),
             'Edge read publication must follow actual runtime finalization')
    pending = root / 'edge-read-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root / name) for name in _sources(root)}
    _require(set(hashes) == set(SOURCES), 'Edge read publication source set is incomplete')
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION,
                  'binding_digest': fingerprint(binding), 'sources': hashes,
                  'outputs': output_hashes}
    completion_digest = _exclusive(root / 'edge-read-completion.json', completion)
    _require(set(_sources(root)) == set(hashes)
             and all(_sha(root / name) == digest for name, digest in hashes.items())
             and all(_sha(root / name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root / 'edge-read-completion.json') == completion_digest,
             'Edge read source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_edge_read_report(root: Path):
    _require(not root.is_symlink(), 'Edge read report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected = {}
    issues = []
    try:
        pending = root / 'edge-read-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Edge read publication is incomplete')
        completion = read(root / 'edge-read-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Edge read completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == set(SOURCES) and set(outputs) == set(OUTPUTS)
                 and all(_sha(root / name) == digest for name, digest in hashes.items())
                 and all(_sha(root / name) == digest for name, digest in outputs.items()),
                 'Edge read evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root / name) == digest for name, digest in hashes.items())
                 and all(_sha(root / name) == digest for name, digest in outputs.items())
                 and read(root / 'edge-read-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Edge read evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Edge read completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
