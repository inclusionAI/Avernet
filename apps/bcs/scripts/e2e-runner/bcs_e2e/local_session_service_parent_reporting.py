"""Frozen offline reporting for the owned-local Session Service parent."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import current_manifest, executor_digest
from .local_operator_cli_contract import (
    SESSIONS_SERVICES_PARENT,
    SESSIONS_SERVICES_REQUIREMENTS,
    sessions_services_source_binding,
)
from .local_session_service_cli import (
    LIFECYCLE_ASSERTION_IDS,
    MEMBER_CHAT_ASSERTION_IDS,
    SESSION_SERVICE_LIFECYCLE_CASE_METHOD,
    SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
)
from .local_session_service_completion import (
    COMPLETE_ASSERTION_IDS,
    ROUNDTRIP_ASSERTION_IDS,
    SESSION_SERVICE_ASSERTION_IDS,
    SESSION_SERVICE_COMPLETE_CASE_METHOD,
    SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
)
from .local_session_service_parent_run import (
    BINDING_CONTRACT,
    BINDING_PATH,
    CASE_PATH,
    EXECUTION_PROFILE_PATH,
    FLOW_PATH,
    DRIVER_TOKEN_ENV,
    MEMBER_TOKEN_ENV,
    ROLES,
    SUCCESS_STATUS,
    VALIDATION_PROFILE_PATH,
)
from .profile import fingerprint
from .run_state import RunStateError, read
from .session_cli_profile import (
    validate_session_cli_complete_invoke_profile,
    validate_session_cli_lifecycle_profile,
)
from .workspace_cli_profile import (
    SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT,
    SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT,
)

OUTPUTS = ('reports/summary.json', 'reports/junit.xml', 'reports/summary.md')
SOURCES = (
    BINDING_PATH,
    FLOW_PATH,
    'bcs-config.json',
    'owned-bcs.json',
    'local-fixtures.json',
    VALIDATION_PROFILE_PATH,
    EXECUTION_PROFILE_PATH,
    CASE_PATH,
)
COMPLETION = 'owned-local-session-service-parent-completion-v1'
RESOURCE_DISPOSITION = 'FOUR_OWNED_SESSION_GROUPS_DELETED_AND_RUNTIME_HISTORY_RETAINED'
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
METHODS = (
    'execute_local_session_service_lifecycle_case',
    'execute_local_session_service_member_chat_case',
    'execute_local_session_service_complete_case',
    'execute_local_session_service_roundtrip_case',
)
FIXTURE_EFFECTS = (
    'OWNED_SESSION_LIFECYCLE_THEN_GENERIC_GROUP_DELETE',
    'OWNED_SESSION_MEMBER_CHAT_THEN_GENERIC_GROUP_DELETE',
    'OWNED_SESSION_COMPLETION_THEN_GENERIC_GROUP_DELETE',
    'OWNED_SESSION_COMPLETION_AND_INVOCATION_THEN_CREATOR_SCOPED_CLEANUP',
)
ASSERTION_GROUPS = (
    (LIFECYCLE_ASSERTION_IDS, SESSION_SERVICE_LIFECYCLE_CASE_METHOD,
     FIXTURE_EFFECTS[0], SESSIONS_SERVICES_REQUIREMENTS[0]),
    (MEMBER_CHAT_ASSERTION_IDS, SESSION_SERVICE_MEMBER_CHAT_CASE_METHOD,
     FIXTURE_EFFECTS[1], SESSIONS_SERVICES_REQUIREMENTS[1]),
    (COMPLETE_ASSERTION_IDS, SESSION_SERVICE_COMPLETE_CASE_METHOD,
     FIXTURE_EFFECTS[2], SESSIONS_SERVICES_REQUIREMENTS[2]),
    (ROUNDTRIP_ASSERTION_IDS, SESSION_SERVICE_ROUNDTRIP_CASE_METHOD,
     FIXTURE_EFFECTS[3], SESSIONS_SERVICES_REQUIREMENTS[3]),
)
ASSERTION_METHODS = {
    assertion_id: (method, effect, requirement)
    for assertion_ids, method, effect, requirement in ASSERTION_GROUPS
    for assertion_id in assertion_ids
}



def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RunStateError(message)


def _sha(path: Path) -> str:
    _require(not path.parent.is_symlink(),
             'Session Service evidence directory cannot be a symlink')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                 and info.st_size <= MAX_EVIDENCE_BYTES,
                 'Session Service evidence must be a bounded regular file')
        raw = stream.read(MAX_EVIDENCE_BYTES + 1)
    _require(len(raw) <= MAX_EVIDENCE_BYTES,
             'Session Service evidence exceeded byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path: Path, value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True, indent=2,
                      allow_nan=False) + '\n').encode()
    _require(not path.exists() and not path.is_symlink(),
             'Session Service publication marker must be fresh')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | os.O_NOFOLLOW, 0o600)
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
    root = root.resolve()
    _require(root.is_absolute() and not root.is_symlink() and root.is_dir(),
             'Session Service report requires its original owned directory')
    binding = read(root / BINDING_PATH)
    source = sessions_services_source_binding()
    legacy = binding.get('legacy_inventory')
    _require(
        binding.get('schema_version') == 1
        and binding.get('contract') == BINDING_CONTRACT
        and binding.get('selected_requirement_ids')
        == [row['id'] for row in source['requirements']]
        and binding.get('source_binding') == source
        and source['parent'] == SESSIONS_SERVICES_PARENT
        and [row['id'] for row in source['requirements']]
        == list(SESSIONS_SERVICES_REQUIREMENTS)
        and binding.get('fixture_set') == 'cli-file'
        and binding.get('fixture_roles') == list(ROLES)
        and binding.get('store_messages') is False
        and binding.get('validation_profile_contract')
        == SESSION_CLI_LIFECYCLE_PROFILE_CONTRACT
        and binding.get('validation_profile_path') == VALIDATION_PROFILE_PATH
        and binding.get('execution_profile_contract')
        == SESSION_CLI_COMPLETE_INVOKE_PROFILE_CONTRACT
        and binding.get('execution_profile_path') == EXECUTION_PROFILE_PATH
        and legacy == current_manifest()['legacy_inventory']
        and isinstance(binding.get('executor_digest'), str)
        and len(binding['executor_digest']) == 64
        and isinstance(binding.get('bcs_binary_sha256'), str)
        and len(binding['bcs_binary_sha256']) == 64
        and isinstance(binding.get('cli_binary_sha256'), str)
        and len(binding['cli_binary_sha256']) == 64,
        'Session Service frozen binding is invalid')
    lifecycle = validate_session_cli_lifecycle_profile(
        read(root / VALIDATION_PROFILE_PATH))
    complete = validate_session_cli_complete_invoke_profile(
        read(root / EXECUTION_PROFILE_PATH))
    _require(binding.get('validation_profile_digest') == fingerprint(lifecycle)
             and binding.get('execution_profile_digest') == fingerprint(complete)
             and binding.get('runtime_dir') == str(root)
             and lifecycle['auth']['driver_token_env'] == DRIVER_TOKEN_ENV
             and complete['auth']['driver_token_env'] == DRIVER_TOKEN_ENV
             and complete['auth']['member_token_env'] == MEMBER_TOKEN_ENV,
             'Session Service profile digest or runtime binding differs')
    return binding


def _request_valid(request: object, effect: str) -> bool:
    if not isinstance(request, dict):
        return False
    if (request.get('remote_validation') != 'NOT_RUN'
            or request.get('grants_release_credit') is not False
            or request.get('credential_kind') != 'owned_local_bot_token'
            or request.get('fixture_effect') != effect
            or not isinstance(request.get('operation'), str)
            or not request.get('operation')
            or not isinstance(request.get('case_class'), str)
            or not request.get('case_class')):
        return False
    transport = request.get('transport')
    if transport == 'PRODUCT_CLI':
        # The reviewed bounded wait may exit 1 with its session-specific
        # timeout projection.  The case adapter rejects every other nonzero
        # wait result before evidence publication.
        bounded_wait = (request.get('operation') == 'session-cli.service.wait'
                         and request.get('case_class')
                         == 'session-cli.service.wait')
        exit_status = request.get('exit_status')
        return ((exit_status == 0 or (bounded_wait and exit_status == 1))
                and isinstance(request.get('command_arguments'), list)
                and isinstance(request.get('route_declared'), str)
                and isinstance(request.get('http_method_declared'), str))
    if transport == 'PRODUCT_HTTP':
        return (request.get('http_status') in (200, 404)
                and isinstance(request.get('method'), str)
                and request.get('method')
                and isinstance(request.get('path'), str)
                and request.get('path'))
    return False


def _case_valid(root: Path, binding: dict, case: dict) -> bool:
    steps = case.get('steps')
    expected_count = len(SESSION_SERVICE_ASSERTION_IDS)
    if (case.get('case_id') != binding['case_id']
            or case.get('parent_id') != SESSIONS_SERVICES_PARENT
            or case.get('status') != 'PASS'
            or case.get('run_id') != binding['run_id']
            or case.get('executor_digest') != binding['executor_digest']
            or case.get('binding_contract') != binding['contract']
            or case.get('validation_profile_contract')
            != binding['validation_profile_contract']
            or case.get('execution_profile_contract')
            != binding['execution_profile_contract']
            or case.get('completed_count') != expected_count
            or case.get('total_count') != expected_count
            or case.get('error_type') is not None
            or not isinstance(steps, list) or len(steps) != expected_count):
        return False
    groups: list[str] = []
    for step, assertion_id in zip(steps, SESSION_SERVICE_ASSERTION_IDS):
        method, effect, _requirement = ASSERTION_METHODS[assertion_id]
        if (not isinstance(step, dict)
                or set(step) != {'requirement_id', 'method', 'status', 'data',
                                 'evidence', 'error_type', 'error_gap',
                                 'error_detail'}
                or step.get('requirement_id') != assertion_id
                or step.get('method') != method
                or step.get('status') != 'PASS'
                or step.get('error_type') is not None
                or not isinstance(step.get('data'), dict)
                or not _request_valid(step.get('evidence'), effect)):
            return False
        group_id = step['data'].get('group_id')
        if group_id is not None:
            if not isinstance(group_id, str) or not group_id:
                return False
            groups.append(group_id)
    return len(set(groups)) == 4


def _credentials_valid(credentials: object) -> bool:
    return (isinstance(credentials, dict)
            and credentials.get('state') == 'CLOSED'
            and credentials.get('selected_roles') == list(ROLES)
            and credentials.get('verified_roles') == list(ROLES)
            and credentials.get('credential_memory') == 'CLEARED'
            and credentials.get('memory_cleanup_scope')
            == 'HOLDER_REFERENCES_ONLY'
            and credentials.get('secure_erasure') is False
            and credentials.get('remote_authentication') == 'NOT_TESTED')


def _project(root: Path, binding: dict) -> dict:
    flow = read(root / FLOW_PATH)
    runtime = read(root / 'owned-bcs.json')
    fixtures = read(root / 'local-fixtures.json')
    case = read(root / CASE_PATH)
    _require(flow.get('run_id') == binding['run_id']
             and flow.get('case_id') == binding['case_id']
             and flow.get('executor_digest') == binding['executor_digest']
             and flow.get('binding_digest') == fingerprint(binding)
             and flow.get('case') == case
             and flow.get('shutdown') == runtime
             and flow.get('remote_validation') == 'NOT_RUN'
             and flow.get('full_release_status') == 'NOT_QUALIFIED'
             and flow.get('ready_to_execute') is False,
             'Session Service flow differs from original journals')
    _require(runtime.get('run_id') == binding['run_id']
             and runtime.get('binary_sha256') == binding['bcs_binary_sha256'],
             'Session Service runtime binding differs')
    _require(fixtures.get('run_id') == binding['run_id']
             and fixtures.get('fixture_set') == binding['fixture_set']
             and fixtures.get('runtime_origin') == runtime.get('origin'),
             'Session Service fixture binding differs')
    complete = (_case_valid(root, binding, case)
                and _credentials_valid(flow.get('credentials'))
                and _sources(root) == set(SOURCES))
    stopped = (runtime.get('status') == 'STOPPED'
               and type(runtime.get('returncode')) is int
               and runtime.get('returncode') == 0
               and runtime.get('forced_stop') is False)
    cleared = _credentials_valid(flow.get('credentials'))
    status = ('CANCELLED' if flow.get('status') == 'CANCELLED'
              else 'PASS' if (complete and stopped and cleared
                             and flow.get('status') == SUCCESS_STATUS)
              else 'BLOCKED')
    steps = case.get('steps') if isinstance(case.get('steps'), list) else []
    assertions = [{'id': step.get('requirement_id'),
                   'status': step.get('status', 'NOT_RUN')}
                  for step in steps if isinstance(step, dict)]
    requirements = []
    for assertion_ids, _method, _effect, requirement in ASSERTION_GROUPS:
        rows = [row for row in assertions if row.get('id') in assertion_ids]
        requirements.append({
            'id': requirement,
            'status': ('PASS' if len(rows) == len(assertion_ids)
                       and all(row.get('status') == 'PASS' for row in rows)
                       else 'NOT_RUN'),
        })
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
        'functional': projected.get('functional', 'NOT_RUN'),
        'target': {'tier': 'local', 'environment_id': binding['run_id']},
        'scope': {
            'suite': binding['suite'],
            'selected_cases': [binding['case_id']],
            'legacy_inventory_count': len(binding['legacy_inventory']),
            'full_release_status': 'NOT_QUALIFIED',
            'full_story_status': ('LOCAL_INTEGRATED' if status == 'PASS'
                                  else 'NOT_QUALIFIED'),
            'manifest_digest': fingerprint(binding),
        },
        'runner': {'executor_digest': binding['executor_digest']},
        'cases': [{
            'id': binding['case_id'], 'version': 1, 'status': status,
            'functional': projected.get('functional', 'NOT_RUN'),
            'evidence_status': projected.get('evidence_status', 'INCOMPLETE'),
            'cleanup': projected.get('cleanup', 'BLOCKED'),
            'resource_disposition': projected.get('resource_disposition',
                                                   'UNVERIFIED'),
        }],
        'requirements': projected.get(
            'requirements',
            [{'id': name, 'status': 'NOT_RUN'}
             for name in SESSIONS_SERVICES_REQUIREMENTS]),
        'reports': {
            'expected_assertion_count': len(SESSION_SERVICE_ASSERTION_IDS),
            'observed_assertion_count': sum(
                1 for row in projected.get('assertions', [])
                if row.get('status') == 'PASS'),
        },
        'integrity': {'status': 'BLOCKED' if issues else 'PASS',
                      'issues': issues},
        'routes': [],
        'cleanup': {
            'verification_scope': 'SESSION_SERVICE_OWNED_RUNTIME_ONLY',
            'physical_data_erasure': 'NOT_PERFORMED',
            'business_history_disposition': 'RETAINED_FOR_DIAGNOSIS',
        },
        'mock_boundary': {
            'not_proven': [
                'remote gateway authentication and authorization',
                'Bot engine reasoning', 'actual remote message delivery',
                'full release qualification',
            ],
        },
        'timing': {'interpretation': 'functional diagnostic only; not a benchmark'},
        'grants_release_credit': False,
        'ready_to_execute': False,
    }


def publish_session_service_parent(runtime, binding: dict, flow: dict) -> dict:
    root = runtime.root
    _require(_bound(root) == binding
             and executor_digest() == binding['executor_digest'],
             'Session Service source changed before report publication')
    _require(read(root / FLOW_PATH) == flow
             and read(root / 'owned-bcs.json') == runtime._record(),
             'Session Service publication must follow runtime finalization')
    pending = root / 'session-service-parent-publication-pending.json'
    pending_digest = _exclusive(pending, {
        'schema_version': 1, 'run_id': binding['run_id']})
    hashes = {name: _sha(root / name) for name in SOURCES}
    summary = _summary(binding, _project(root, binding), [])
    from .reporting import write_reports
    outputs = write_reports(summary, root / 'reports', {'all'})
    output_hashes = {name: _sha(root / name) for name in OUTPUTS}
    completion = {'schema_version': 1, 'contract': COMPLETION,
                  'binding_digest': fingerprint(binding),
                  'sources': hashes, 'outputs': output_hashes}
    completion_digest = _exclusive(
        root / 'session-service-parent-completion.json', completion)
    _require(_sources(root) == set(hashes)
             and all(_sha(root / name) == digest
                     for name, digest in hashes.items())
             and all(_sha(root / name) == digest
                     for name, digest in output_hashes.items())
             and _sha(pending) == pending_digest
             and _sha(root / 'session-service-parent-completion.json')
             == completion_digest,
             'Session Service source or output changed during publication')
    pending.unlink()
    return {'status': summary['status'], 'summary': summary,
            'outputs': outputs}


def build_session_service_parent_report(root: Path) -> dict:
    _require(not root.is_symlink(),
             'Session Service report requires an original non-symlink root')
    root = root.resolve()
    binding = _bound(root)
    projected: dict = {}
    issues: list[str] = []
    try:
        pending = root / 'session-service-parent-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),
                 'Session Service publication is incomplete')
        completion = read(
            root / 'session-service-parent-completion.json')
        _require(completion.get('contract') == COMPLETION
                 and completion.get('binding_digest') == fingerprint(binding),
                 'Session Service completion binding differs')
        hashes = completion.get('sources')
        output_hashes = completion.get('outputs')
        _require(isinstance(hashes, dict) and isinstance(output_hashes, dict)
                 and set(hashes) == _sources(root)
                 and set(output_hashes) == set(OUTPUTS)
                 and all(_sha(root / name) == digest
                         for name, digest in hashes.items())
                 and all(_sha(root / name) == digest
                         for name, digest in output_hashes.items()),
                 'Session Service evidence or reports changed')
        projected = _project(root, binding)
        _require(all(_sha(root / name) == digest
                     for name, digest in hashes.items())
                 and all(_sha(root / name) == digest
                         for name, digest in output_hashes.items())
                 and read(root / 'session-service-parent-completion.json')
                 == completion
                 and not pending.exists() and not pending.is_symlink(),
                 'Session Service evidence changed during projection')
    except (OSError, RunStateError, ValueError, KeyError, TypeError):
        issues.append('Session Service completion, source or output evidence '
                      'is missing, changed or invalid')
    return _summary(binding, projected, issues)
