"""Version-one owned fixed-Loop reports projected from frozen execution evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .case_rewrite import fixed_loop_case_rewrite
from .inventory import binding_matches_reviewed_release, executor_digest
from .local_fixed_loop_contract import (ASSERTION_IDS, CASE_ID, PARENT, SUITE)
from .profile import fingerprint
from .run_state import RunStateError, read

_OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
_SOURCES = (
    'fixed-loop-binding.json', 'fixed-loop-flow-report.json', 'owned-bcs.json',
    'bcs-config.json', 'local-fixtures.json', 'local-fixed-loop.json',
    'local-fixed-loop-adapter.json',
)
_COMPLETION = 'owned-local-fixed-loop-completion-v1'


def _require(value: object, message: str) -> None:
    if not value:
        raise RunStateError(message)


def _sha(path: Path) -> str:
    _require(not path.parent.is_symlink(), 'Fixed Loop evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8 * 1024 * 1024,
                 'Fixed Loop evidence must be a bounded regular unshared file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024, 'Fixed Loop evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value: dict) -> str:
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


def _source_names(root: Path) -> set[str]:
    return {name for name in _SOURCES if (root / name).exists() or (root / name).is_symlink()}


def _bound(root: Path) -> dict:
    plan = fixed_loop_case_rewrite()
    binding = read(root / 'fixed-loop-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('case_id') == CASE_ID and binding.get('run_id')
             and binding.get('runtime_dir') == str(root)
             and binding.get('loop_execution_enabled') is True
             and binding.get('judge_outcomes') == ['revise', 'approved', 'revise', 'revise']
             and binding.get('provider_receipt_limit') == 8
             and binding.get('selected_requirement_ids') == list(plan.requirement_ids)
             and binding.get('adapter_family') == 'OwnedLocalCaseAdapter'
             and binding.get('source_binding', {}).get('parent') == PARENT
             and [row.get('id') for row in binding['source_binding']['requirements']] == list(plan.requirement_ids)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Fixed Loop frozen complete binding is invalid')
    return binding


def _adapter_steps_valid(binding: dict, adapter: dict) -> bool:
    plan = fixed_loop_case_rewrite()
    if (adapter.get('schema_version') != 1 or adapter.get('case_id') != CASE_ID
            or adapter.get('parent_id') != PARENT or adapter.get('run_id') != binding['run_id']):
        return False
    rows = adapter.get('steps', [])
    return (len(rows) == len(plan.requirements)
            and [(row.get('method'), row.get('requirement_id'), row.get('status')) for row in rows]
            == [(requirement.adapter_method, requirement.requirement_id, 'PASS')
                for requirement in plan.requirements])


def _project(root: Path, binding: dict) -> dict:
    plan = fixed_loop_case_rewrite()
    flow = read(root / 'fixed-loop-flow-report.json')
    runtime = read(root / 'owned-bcs.json')
    case = read(root / 'local-fixed-loop.json')
    adapter = read(root / 'local-fixed-loop-adapter.json')
    requirement_ids = list(plan.requirement_ids)
    expected_steps = [
        {'requirement_id': row.requirement_id, 'status': 'PASS'} for row in plan.requirements
    ]
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('case') == case and flow.get('shutdown') == runtime,
             'Fixed Loop final result differs from original journals')
    complete = (case.get('status') == 'PASS' and case.get('completed_count') == len(requirement_ids)
                and case.get('total_count') == len(requirement_ids)
                and case.get('ready_to_execute') is True and case.get('error_type') is None
                and [step.get('requirement_id') for step in case.get('steps', [])] == requirement_ids
                and all(step.get('status') == 'PASS' for step in case.get('steps', []))
                and _adapter_steps_valid(binding, adapter))
    stopped = runtime.get('status') == 'STOPPED' and runtime.get('returncode') == 0
    cleared = flow.get('credentials', {}).get('credential_memory') == 'CLEARED'
    sandbox = flow.get('judge_sandbox', {})
    closed = sandbox.get('state') == 'CLOSED' and sandbox.get('failed') is False
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and closed and flow.get('status') == 'LOCAL_FIXED_LOOP_PASS'
              else 'FAIL' if any(step.get('status') == 'FAIL' for step in case.get('steps', [])) else 'BLOCKED')
    return {
        'status': status,
        'functional': 'PASS' if complete else 'INCOMPLETE',
        'evidence_status': 'PASS' if complete else 'INCOMPLETE',
        'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
        'resource_disposition': ('GROUP_AND_PROVIDER_BOT_DELETED_BY_BUSINESS_API_HISTORY_RETAINED'
                                  if complete else 'UNVERIFIED'),
        'requirements': expected_steps,
        'assertions': case.get('assertions', [{'id': name, 'status': 'NOT_RUN'}
                                              for name in ASSERTION_IDS]),
        'selected_steps': [{'id': row['requirement_id'], 'status': row['status']} for row in expected_steps],
    }


def _summary(binding: dict, projected: dict, issues: list[str]) -> dict:
    plan = fixed_loop_case_rewrite()
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    case_status = projected.get('status', 'BLOCKED')
    observed_assertions = sum(row.get('status') == 'PASS'
                              for row in projected.get('assertions', []))
    return {
        'schema_version': 1, 'run_id': binding['run_id'],
        'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
        'target': {'tier': 'local', 'environment_id': binding['run_id']},
        'scope': {'suite': SUITE, 'selected_cases': [CASE_ID], 'legacy_inventory_count': len(binding['legacy_inventory']),
                  'full_release_status': 'NOT_QUALIFIED',
                  'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                  'manifest_digest': fingerprint(binding)},
        'runner': {'executor_digest': binding['executor_digest']},
        'cases': [{'id': CASE_ID, 'version': 1, 'status': case_status,
                   'functional': projected.get('functional', 'NOT_RUN'),
                   'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                   'cleanup': projected.get('cleanup', 'BLOCKED'),
                   'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
        'assertion_basis': {'source': 'dynamic story AST sites',
                            'counting_rule': 'one public HTTP/CLI/assert check site',
                            'not_machine_readable': []},
        'requirements': projected.get('requirements', [{'id': row.requirement_id,
                                                        'status': 'NOT_RUN'}
                                                       for row in plan.requirements]),
        'reports': {'expected_assertion_count': len(ASSERTION_IDS),
                    'observed_assertion_count': observed_assertions},
        'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
        'routes': [],
        'cleanup': {'verification_scope': 'EXACT_OWNED_GROUP_AND_PROVIDER_BOT',
                    'physical_data_erasure': 'NOT_VERIFIED', 'associated_history_purge': 'NOT_PERFORMED'},
        'mock_boundary': {'not_proven': ['remote authentication and authorization', 'real model reasoning',
                                          'external engine termination', 'physical history erasure',
                                          'remote release qualification']},
        'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
        'ready_to_execute': False,
    }


def publish_fixed_loop(runtime: object, binding: dict, flow: dict) -> dict:
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Fixed Loop source changed before report publication')
    _require(read(root / 'fixed-loop-flow-report.json') == flow
             and read(root / 'owned-bcs.json') == runtime._record(),
             'Fixed Loop publication must follow actual runtime finalization')
    pending = root / 'fixed-loop-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    sources = _source_names(root)
    hashes = {name: _sha(root / name) for name in sources}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in _OUTPUTS}
    completion = {'schema_version': 1, 'contract': _COMPLETION,
                  'binding_digest': fingerprint(binding), 'sources': hashes,
                  'outputs': output_hashes}
    completion_digest = _exclusive(root / 'fixed-loop-completion.json', completion)
    _require(set(hashes) == sources
             and all(_sha(root / name) == digest for name, digest in hashes.items())
             and all(_sha(root / name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root / 'fixed-loop-completion.json') == completion_digest,
             'Fixed Loop source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_fixed_loop_report(root: Path) -> dict:
    _require(not root.is_symlink(), 'Fixed Loop report requires original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected: dict = {}
    issues: list[str] = []
    try:
        pending = root / 'fixed-loop-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(), 'Fixed Loop publication is incomplete')
        completion = read(root / 'fixed-loop-completion.json')
        _require(completion.get('contract') == _COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Fixed Loop completion binding differs')
        hashes, output_hashes = completion['sources'], completion['outputs']
        _require(set(hashes) == _source_names(root) and set(output_hashes) == set(_OUTPUTS)
                 and all(_sha(root / name) == digest for name, digest in hashes.items())
                 and all(_sha(root / name) == digest for name, digest in output_hashes.items()),
                 'Fixed Loop evidence or reports changed')
        projected = _project(root, binding)
    except Exception as failure:
        issues = [type(failure).__name__]
    return _summary(binding, projected, issues)
