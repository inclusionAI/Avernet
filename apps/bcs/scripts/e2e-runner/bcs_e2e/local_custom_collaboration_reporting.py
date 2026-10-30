"""Frozen reporting for the owned-local custom-collaboration parent lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import current_manifest, executor_digest
from .local_custom_collaboration_case import (
    CASE_ID,
    REQUIREMENTS,
    SUITE,
    custom_collaboration_case_plan,
    custom_collaboration_source_binding,
)
from .local_custom_collaboration_run import (
    BINDING_CONTRACT,
    BINDING_PATH,
    CASE_PATH,
    DEFINITION_PATH,
    FLOW_PATH,
    ROLES,
    SUCCESS_STATUS,
)
from .local_operator_cli_contract import (
    CUSTOM_COLLABORATION_PARENT,
)
from .profile import fingerprint
from .run_state import RunStateError, read

SOURCES = (
    BINDING_PATH,
    FLOW_PATH,
    'bcs-config.json',
    'owned-bcs.json',
    'local-fixtures.json',
    'run.json',
    DEFINITION_PATH,
    CASE_PATH,
)
OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
COMPLETION = 'owned-local-custom-collaboration-lifecycle-completion-v1'
RESOURCE_DISPOSITION = 'OWNED_RUNTIME_SCOPED_TEST_HISTORY_RETAINED'
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RunStateError(message)


def _sha(path: Path) -> str:
    _require(not path.parent.is_symlink(),
             'custom-collaboration evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_size <= MAX_EVIDENCE_BYTES,
                 'custom-collaboration evidence must be a bounded regular file')
        raw = stream.read(MAX_EVIDENCE_BYTES + 1)
    _require(len(raw) <= MAX_EVIDENCE_BYTES,
             'custom-collaboration evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    _require(not path.exists() and not path.is_symlink(),
             'custom-collaboration publication marker must be fresh')
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


def _sources(root: Path) -> set[str]:
    return {name for name in SOURCES
            if (root / name).exists() or (root / name).is_symlink()}


def _bound(root: Path) -> dict:
    _require(root.is_absolute() and not root.is_symlink() and root.is_dir(),
             'custom-collaboration report requires its original owned directory')
    binding = read(root / BINDING_PATH)
    source = custom_collaboration_source_binding()
    legacy = binding.get('legacy_inventory')
    _require(
        binding.get('schema_version') == 1
        and binding.get('contract') == BINDING_CONTRACT
        and binding.get('suite') == SUITE
        and binding.get('case_id') == CASE_ID
        and binding.get('case_version') == 1
        and binding.get('run_id')
        and binding.get('runtime_dir') == str(root)
        and binding.get('selected_requirement_ids') == list(REQUIREMENTS)
        and binding.get('source_binding') == source
        and source['parent'] == CUSTOM_COLLABORATION_PARENT
        and [row['id'] for row in source['requirements']] == list(REQUIREMENTS)
        and binding.get('fixture_set') == 'pilot'
        and binding.get('fixture_roles') == list(ROLES)
        and binding.get('store_messages') is False
        and legacy == current_manifest()['legacy_inventory']
        and isinstance(binding.get('executor_digest'), str)
        and len(binding['executor_digest']) == 64
        and isinstance(binding.get('bcs_binary_sha256'), str)
        and len(binding['bcs_binary_sha256']) == 64
        and isinstance(binding.get('cli_binary_sha256'), str)
        and len(binding['cli_binary_sha256']) == 64,
        'custom-collaboration frozen binding is invalid')
    return binding


def _credential_valid(credentials: dict, driver_id: str) -> bool:
    return (isinstance(credentials, dict)
            and credentials.get('state') == 'CLEARED'
            and credentials.get('credential_memory') == 'CLEARED'
            and credentials.get('bot_id') == driver_id
            and credentials.get('memory_cleanup_scope') == 'HOLDER_REFERENCES_ONLY'
            and credentials.get('secure_erasure') is False
            and credentials.get('remote_authentication') == 'NOT_TESTED')


def _operations_valid(operations: object) -> bool:
    if not isinstance(operations, dict):
        return False
    expected = {
        'validate': 'LOCAL_CUSTOM_COLLABORATION_VALIDATE_PASS',
        'trial': 'LOCAL_CUSTOM_COLLABORATION_TRIAL_PASS',
        'create-persistent-group': 'LOCAL_CUSTOM_COLLABORATION_CREATE_GROUP_PASS',
    }
    if set(operations) != set(expected):
        return False
    for name, status in expected.items():
        row = operations.get(name)
        if not isinstance(row, dict) or row.get('status') != status:
            return False
        if (row.get('remote_validation') != 'NOT_RUN'
                or row.get('grants_release_credit') is not False):
            return False
    created = operations['create-persistent-group']
    group_id = created.get('group_id')
    return (isinstance(group_id, str) and bool(group_id)
            and created.get('group_cleanup') == 'DELETED')


def _business_evidence(root: Path, binding: dict, case: dict,
                       fixtures: dict) -> bool:
    roles = fixtures.get('roles', {})
    human = fixtures.get('human', {})
    operations = case.get('operations')
    if (not isinstance(roles, dict) or set(roles) != set(ROLES)
            or not isinstance(human, dict) or not human.get('user_id')
            or not isinstance(operations, dict)):
        return False
    driver_binding = roles.get('driver') if isinstance(roles.get('driver'), dict) else None
    reviewer_binding = roles.get('member') if isinstance(roles.get('member'), dict) else None
    if not isinstance(driver_binding, dict) or not isinstance(reviewer_binding, dict):
        return False
    driver = driver_binding.get('bot_id')
    reviewer = reviewer_binding.get('bot_id')
    if any((not isinstance(row.get('owner_id'), str)
            or row.get('owner_id') != human.get('user_id')
            or row.get('dedicated') is not True
            or row.get('origin') != 'created')
           for row in (driver_binding, reviewer_binding)):
        return False
    return (case.get('status') == SUCCESS_STATUS
            and case.get('case_id') == CASE_ID
            and case.get('legacy_id') == CUSTOM_COLLABORATION_PARENT
            and case.get('assertions') == [{'id': name, 'status': 'PASS'}
                                            for name in REQUIREMENTS]
            and case.get('selected_requirement_ids') == list(REQUIREMENTS)
            and case.get('parent_requirement_count') == len(REQUIREMENTS)
            and case.get('full_parent_coverage') == 'LOCAL_PARENT_REQUIREMENTS_ONLY'
            and case.get('persistent_group_cleanup') == 'DELETED'
            and case.get('driver_bot') == driver
            and case.get('human_participant') == 'human_' + human['user_id']
            and case.get('binary_sha256') == binding['cli_binary_sha256']
            and case.get('yaml_path') == str(root / DEFINITION_PATH)
            and case.get('remote_validation') == 'NOT_RUN'
            and case.get('grants_release_credit') is False
            and case.get('legacy_counts') == custom_collaboration_case_plan()
            and _operations_valid(operations)
            and isinstance(driver, str) and bool(driver)
            and isinstance(reviewer, str) and bool(reviewer)
            and driver != reviewer)


def _project(root: Path, binding: dict) -> dict:
    flow = read(root / FLOW_PATH)
    runtime = read(root / 'owned-bcs.json')
    fixtures = read(root / 'local-fixtures.json')
    case = read(root / CASE_PATH)
    run_state = read(root / 'run.json')
    _require(run_state == {
        'schema_version': 1, 'status': 'PREPARED', 'run_id': binding['run_id'],
        'ledger_path': str(root / 'run.sqlite'),
    }, 'custom-collaboration Run journal binding differs')
    _require(flow.get('run_id') == binding['run_id']
             and flow.get('case_id') == CASE_ID
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('binding_digest') == fingerprint(binding)
             and flow.get('case') == case
             and flow.get('shutdown') == runtime
             and flow.get('remote_validation') == 'NOT_RUN'
             and flow.get('full_release_status') == 'NOT_QUALIFIED'
             and flow.get('ready_to_execute') is False,
             'custom-collaboration flow differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'custom-collaboration runtime binding differs')
    _require(fixtures.get('run_id') == binding['run_id']
             and fixtures.get('runtime_origin') == runtime.get('origin')
             and isinstance(fixtures.get('human'), dict)
            and bool(fixtures['human'].get('user_id')),
             'custom-collaboration fixture binding differs')
    complete = (_business_evidence(root, binding, case, fixtures)
                and _sources(root) == set(SOURCES))
    stopped = (runtime.get('status') == 'STOPPED'
               and type(runtime.get('returncode')) is int
               and runtime.get('returncode') == 0
               and runtime.get('forced_stop') is False)
    fixture_roles = fixtures.get('roles')
    fixture_roles = (fixture_roles if isinstance(fixture_roles, dict)
                     else {}) if not isinstance(fixture_roles, dict) else fixture_roles
    driver_entry = fixture_roles.get('driver') if isinstance(fixture_roles.get('driver'), dict) else {}
    driver = driver_entry.get('bot_id')
    cleared = _credential_valid(flow.get('credentials'), driver)
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED' else
              'PASS' if complete and stopped and cleared
              and flow.get('status') == SUCCESS_STATUS else 'BLOCKED')
    assertions = case.get('assertions', [])
    if not isinstance(assertions, list):
        assertions = []
    requirements = [{'id': name,
                     'status': next((row.get('status') for row in assertions
                                     if isinstance(row, dict)
                                     and row.get('id') == name), 'NOT_RUN')}
                    for name in REQUIREMENTS]
    return {
        'status': status,
        'functional': 'PASS' if complete else 'INCOMPLETE',
        'evidence_status': 'PASS' if complete else 'INCOMPLETE',
        'cleanup': 'PASS' if complete and stopped and cleared else 'BLOCKED',
        'resource_disposition': RESOURCE_DISPOSITION if complete else 'UNVERIFIED',
        'requirements': requirements,
        'assertions': assertions,
    }


def _summary(binding: dict, projected: dict, issues: list[str]) -> dict:
    status = projected.get('status', 'BLOCKED') if not issues else 'BLOCKED'
    return {
        'schema_version': 1,
        'run_id': binding['run_id'],
        'status': 'LOCAL_SUITE_PASS' if status == 'PASS' else status,
        'target': {'tier': 'local', 'environment_id': binding['run_id']},
        'scope': {
            'suite': SUITE,
            'selected_cases': [CASE_ID],
            'legacy_inventory_count': len(binding['legacy_inventory']),
            'full_release_status': 'NOT_QUALIFIED',
            'full_story_status': 'LOCAL_INTEGRATED' if status == 'PASS' else 'NOT_QUALIFIED',
            'manifest_digest': fingerprint(binding),
        },
        'runner': {'executor_digest': binding['executor_digest']},
        'cases': [{
            'id': CASE_ID,
            'version': 1,
            'status': status,
            'functional': projected.get('functional', 'NOT_RUN'),
            'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
            'cleanup': projected.get('cleanup', 'BLOCKED'),
            'resource_disposition': projected.get('resource_disposition', 'UNVERIFIED'),
        }],
        'requirements': projected.get(
            'requirements', [{'id': name, 'status': 'NOT_RUN'} for name in REQUIREMENTS]),
        'reports': {
            'expected_assertion_count': len(REQUIREMENTS),
            'observed_assertion_count': len(projected.get('assertions', [])),
        },
        'integrity': {'status': 'BLOCKED' if issues else 'PASS', 'issues': issues},
        'routes': [],
        'cleanup': {
            'verification_scope': 'CUSTOM_COLLABORATION_OWNED_RUNTIME_ONLY',
            'physical_data_erasure': 'NOT_PERFORMED',
            'business_history_disposition': 'RETAINED_FOR_DIAGNOSIS',
        },
        'mock_boundary': {
            'not_proven': [
                'remote authentication and authorization',
                'Bot engine reasoning',
                'actual message delivery',
                'full release qualification',
            ],
        },
        'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
        'grants_release_credit': False,
        'ready_to_execute': False,
    }


def publish_custom_collaboration(runtime, binding: dict, flow: dict) -> dict:
    root = runtime.root
    _require(_bound(root) == binding and executor_digest() == binding['executor_digest'],
             'custom-collaboration source changed before report publication')
    _require(read(root / FLOW_PATH) == flow
             and read(root / 'owned-bcs.json') == runtime._record(),
             'custom-collaboration publication must follow runtime finalization')
    pending = root / 'custom-collaboration-publication-pending.json'
    pending_digest = _exclusive(
        pending, {'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root / name) for name in SOURCES}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {
        'schema_version': 1,
        'contract': COMPLETION,
        'binding_digest': fingerprint(binding),
        'sources': hashes,
        'outputs': output_hashes,
    }
    completion_digest = _exclusive(root / 'custom-collaboration-completion.json', completion)
    _require(_sources(root) == set(hashes)
             and all(_sha(root / name) == digest
                     for name, digest in hashes.items())
             and all(_sha(root / name) == digest
                     for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root / 'custom-collaboration-completion.json') == completion_digest,
             'custom-collaboration source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary, 'outputs': outputs}


def build_custom_collaboration_report(root: Path) -> dict:
    """Project a completed Run without starting a process or resolving a token."""
    _require(not root.is_symlink(),
             'custom-collaboration report requires an original non-symlink root')
    root = root.resolve(strict=True)
    binding = _bound(root)
    projected: dict = {}
    issues: list[str] = []
    try:
        pending = root / 'custom-collaboration-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),
                 'custom-collaboration publication is incomplete')
        completion = read(root / 'custom-collaboration-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'custom-collaboration completion binding differs')
        hashes, output_hashes = completion.get('sources'), completion.get('outputs')
        _require(isinstance(hashes, dict) and isinstance(output_hashes, dict)
                 and set(hashes) == _sources(root)
                 and set(output_hashes) == set(OUTPUTS)
                 and all(_sha(root / name) == digest
                         for name, digest in hashes.items())
                 and all(_sha(root / name) == digest
                         for name, digest in output_hashes.items()),
                 'custom-collaboration evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root / name) == digest
                     for name, digest in hashes.items())
                 and all(_sha(root / name) == digest
                         for name, digest in output_hashes.items())
                 and read(root / 'custom-collaboration-completion.json') == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'custom-collaboration evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('custom-collaboration completion, source or output evidence is '
                      'missing, changed or invalid')
    return _summary(binding, projected, issues)
