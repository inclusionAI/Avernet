"""Version-one Result queue reports projected from frozen black-box evidence.

Both story and mock children held the only credential-bearing traffic; this
offline projection only ever hashes the bounded streams and checks the frozen
child receipts against the controlled BCS runtime record. Digests detect
drift, not a malicious rewrite by the owner of the Run files.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import binding_matches_reviewed_release, executor_digest
from .local_result_queue_contract import (ASSERTION_IDS, CASE_ID, MARKER_KEYS, PARENT, REQUIREMENTS,
                                           STATUS, SUITE)
from .local_result_queue_run import (BINARY_KIND, EXPECTED_PROVIDER_WEBHOOKS, HUMAN_ID,
                                      MOCK_ERR_LOG_NAME, MOCK_LOG_NAME, STORY_ERR_LOG_NAME,
                                      STORY_LOG_NAME)
from .profile import fingerprint
from .run_state import RunStateError, read

SOURCES = ('result-queue-binding.json', 'result-queue-flow-report.json',
           'result-queue-mock-child.json', 'result-queue-child.json', 'owned-bcs.json',
           'result-queue.json', STORY_LOG_NAME, STORY_ERR_LOG_NAME, MOCK_LOG_NAME,
           MOCK_ERR_LOG_NAME)
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-result-queue-completion-v1'
EXPECTED_ASSERTIONS = 18


def _require(value, message):
    if not value:
        raise RunStateError(message)


def _sha(path):
    _require(not path.parent.is_symlink(), 'Result queue evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 8 * 1024 * 1024,
                 'Result queue evidence must be a bounded regular unshared file')
        raw = stream.read(8 * 1024 * 1024 + 1)
    _require(len(raw) <= 8 * 1024 * 1024, 'Result queue evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
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
    return {name for name in SOURCES if (root / name).is_symlink() or (root / name).exists()}


def _bound(root):
    binding = read(root / 'result-queue-binding.json')
    _require(binding.get('contract') == SUITE and binding.get('suite') == SUITE
             and binding.get('case_id') == CASE_ID and binding.get('run_id')
             and binding.get('runtime_dir') == str(root)
             and binding.get('binary_kind') == BINARY_KIND and binding.get('auto_build') is False
             and binding.get('executor_digest') == executor_digest()
             and binding.get('selected_requirement_ids') == list(REQUIREMENTS)
             and binding.get('bcs_binary_sha256') and len(binding['bcs_binary_sha256']) == 64
             and binding.get('environment') == {'local': 'LOCAL_EXECUTOR_ONLY',
                                                'preprod': 'REMOTE_UNSUPPORTED'}
             and binding.get('human_id') == HUMAN_ID
             and binding.get('source_binding', {}).get('parent') == PARENT
             and [row['id'] for row in binding['source_binding']['requirements']] == list(REQUIREMENTS)
             and binding.get('mock_child', {}).get('expected_webhook_requests') == EXPECTED_PROVIDER_WEBHOOKS
             and binding_matches_reviewed_release(binding.get('legacy_inventory', [])),
             'Result queue frozen binding is invalid')
    return binding


def _child(root):
    path = root / 'result-queue-child.json'
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _mock_child(root):
    path = root / 'result-queue-mock-child.json'
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _case(root):
    path = root / 'result-queue.json'
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _runtime(root):
    path = root / 'owned-bcs.json'
    if path.is_symlink() or not path.exists():
        return {}
    return read(path)


def _project(root, binding):
    flow = read(root / 'result-queue-flow-report.json')
    child = _child(root)
    mock_child = _mock_child(root)
    case = _case(root)
    runtime = _runtime(root)
    _require(flow.get('run_id') == binding['run_id'] and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('binary_kind') == BINARY_KIND
             and flow.get('bcs_binary_sha256') == binding['bcs_binary_sha256']
             and flow.get('auto_build') is False and flow.get('human_id') == HUMAN_ID,
             'Result queue final result differs from its frozen declaration')
    _require(flow.get('case') == case
             and flow.get('child_receipt') == (child if child else {'status': 'NOT_CREATED'})
             and flow.get('mock_child_receipt') == (mock_child if mock_child else {'status': 'NOT_CREATED'}),
             'Result queue shared child receipts are not consistent across journals')
    story_lines = {row['id']: list(row['story_lines']) for row in binding['source_binding']['requirements']}
    child_complete = (bool(child) and child.get('markers') == dict.fromkeys(MARKER_KEYS, True)
                      and child.get('observed_checks') == 1
                      and child.get('redaction_gate') is True
                      and child.get('exit_code') == 0 and child.get('timed_out') is False)
    mock_complete = (bool(mock_child) and mock_child.get('ready') is True
                     and isinstance(mock_child.get('origin'), str) and bool(mock_child['origin'])
                     and mock_child.get('exit_code') == 0
                     and mock_child.get('redaction_gate') is True
                     and mock_child.get('observed_webhook_requests') == EXPECTED_PROVIDER_WEBHOOKS
                     and mock_child.get('observed_event_webhook_requests') == 0
                     and mock_child.get('judge_started') is False)
    runtime_complete = (runtime.get('status') == 'STOPPED' and runtime.get('returncode') == 0
                        and runtime.get('ready') is True)
    scheduled_traffic = (isinstance(case.get('scheduled_traffic'), dict)
                        and case['scheduled_traffic'].get('observed_webhook_requests') == EXPECTED_PROVIDER_WEBHOOKS
                        and case['scheduled_traffic'].get('observed_event_webhook_requests') == 0
                        and case['scheduled_traffic'].get('judge_started') is False)
    case_complete = (bool(case) and case.get('status') == STATUS
                     and case.get('run_id') == binding['run_id'] and case.get('case_id') == CASE_ID
                     and case.get('executor_digest') == binding['executor_digest']
                     and case.get('child_receipt') == child and case.get('mock_child_receipt') == mock_child
                     and [row['id'] for row in case.get('requirements', [])] == list(REQUIREMENTS)
                     and all(row.get('status') == 'PASS' for row in case.get('requirements', []))
                     and case.get('bcs_binary_sha256') == binding['bcs_binary_sha256']
                     and case.get('binary_kind') == BINARY_KIND and case.get('human_id') == HUMAN_ID
                     and case.get('story_lines') == story_lines
                     and case.get('story_markers', {}).get('observed') == [True]
                     and case.get('story_markers', {}).get('checks') == 1
                     and scheduled_traffic
                     and case.get('resource_disposition') == 'STORY_SCOPED_TEMPORARY_DIRECTORY')
    complete = (child_complete and mock_complete and runtime_complete and case_complete
                and set(SOURCES) == _sources(root))
    stopped = child.get('exit_code') == 0 if child else False
    cleared = flow.get('credentials', {}).get('credential_memory') == 'NOT_CAPTURED' \
        and flow.get('credentials', {}).get('story_key_scope') == 'STORY_SCOPED_TEMPORARY_DIRECTORY'
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared and flow.get('status') == STATUS else
              'BLOCKED')
    return {'status': status, 'functional': 'PASS' if complete else 'INCOMPLETE',
            'evidence_status': 'PASS' if complete else 'INCOMPLETE',
            'cleanup': 'STORY_SCOPED_TEMPORARY_DIRECTORY' if complete else 'BLOCKED',
            'resource_disposition': case.get('resource_disposition', 'UNVERIFIED'),
            'assertions': case.get('assertions', []),
            'requirements': [{'id': name, 'status': next(
                (row['status'] for row in case.get('requirements', []) if row.get('id') == name), 'NOT_RUN')}
                for name in REQUIREMENTS]}


def _summary(binding, projected, issues):
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    case_requirements = projected.get('requirements', [])
    observed_assertions = sum(row.get('status') == 'PASS'
                              for row in projected.get('assertions', []))
    return {'schema_version': 1, 'run_id': binding['run_id'],
            'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
            'target': {'tier': 'local', 'environment_id': binding['run_id']},
            'scope': {'suite': SUITE, 'selected_cases': [CASE_ID],
                      'legacy_inventory_count': len(binding['legacy_inventory']), 'evidence_granularity': 'OWNED_CHILD_RECEIPT',
                      'independent_slice': True, 'release_parent_denominator': False,
                      'environment': binding['environment'],
                      'full_release_status': 'NOT_QUALIFIED',
                      'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
                      'manifest_digest': fingerprint(binding)},
            'runner': {'executor_digest': binding['executor_digest'],
                       'binary_kind': binding['binary_kind'], 'auto_build': False,
                       'required_arguments': ['--binary', '--binary-sha256']},
            'cases': [{'id': CASE_ID, 'version': 1, 'status': status,
                       'functional': projected.get('functional', 'NOT_RUN'),
                       'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
                       'cleanup': projected.get('cleanup', 'BLOCKED'),
                       'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED')}],
            'assertion_basis': {'source': 'dynamic story AST sites',
                                'counting_rule': 'one public HTTP/CLI/assert check site',
                                'not_machine_readable': []},
            'requirements': case_requirements or [{'id': name, 'status': 'NOT_RUN'} for name in REQUIREMENTS],
            'reports': {'expected_assertion_count': len(ASSERTION_IDS),
                        'observed_assertion_count': observed_assertions},
            'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
            'routes': [],
            'cleanup': {'verification_scope': 'STORY_SCOPED_TEMPORARY_DIRECTORY',
                        'physical_data_erasure': 'STORY_TEMPORARY_DIRECTORY_AUTO_REMOVED',
                        'associated_history_purge': 'NOT_PERFORMED'},
            'mock_boundary': {'not_proven': [
                'per-obligation dynamic isolation (shared black-box child receipts)',
                'remote authentication and authorization', 'Bot engine reasoning',
                'external engine completion', 'remote preprod reachability',
                'friend-auth-sync and work-order event quiescence (mock control plane does not expose their counters)',
                'full release qualification']},
            'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
            'ready_to_execute': False}


def publish_result_queue_report(root: Path, binding: dict, result: dict):
    root = Path(root).resolve()
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'Result queue source changed before report publication')
    _require(read(root / 'result-queue-flow-report.json') == result,
             'Result queue publication must follow the actual run result')
    pending = root / 'result-queue-publication-pending.json'
    pending_digest = _exclusive(pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root / name) for name in _sources(root)}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION, 'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(root / 'result-queue-completion.json', completion)
    _require(_sources(root) == set(hashes)
             and all(_sha(root / name) == digest for name, digest in hashes.items())
             and all(_sha(root / name) == digest for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root / 'result-queue-completion.json') == completion_digest,
             'Result queue source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_result_queue_report(root: Path):
    _require(not root.is_symlink(), 'Result queue report requires original non-symlink root')
    root = Path(root).resolve()
    binding = _bound(root)
    projected, issues = {}, []
    try:
        pending = root / 'result-queue-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),
                 'Result queue publication is incomplete')
        completion = read(root / 'result-queue-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Result queue completion binding differs')
        hashes, outputs = completion['sources'], completion['outputs']
        _require(set(hashes) == _sources(root) and set(outputs) == set(OUTPUTS),
                 'Result queue completion does not match the frozen evidence set')
        projected = _project(root, binding)
        _require(all(_sha(root / name) == digest for name, digest in hashes.items())
                 and all(_sha(root / name) == digest for name, digest in outputs.items())
                 and read(root / 'result-queue-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Result queue evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Result queue completion, source or output evidence is missing, changed or invalid')
    return _summary(binding, projected, issues)
