"""Dual owned-local/preprod execution surface for the Draft delivery slice.

One frozen 32-obligation / 65-check child story runs unchanged on both
environments. The owned-local leg reuses the backlog executor in register
identity mode. The preprod leg spawns the same story child in bearer identity
mode against the reviewed bcs-direct origin and resolves driver credentials
only from named process-environment references; the Human Cookie reference is
admitted on the profile and threaded through the environment untouched. No leg
stores a credential. Any preprod fail-closed outcome emits the machine-readable
BLOCKED evidence leaf under
``~/.codex/bcs-e2e/20261031-migration-wpe3-draft-delivery-preprod``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..case_executor import CaseExecution, StepOutcome
from ..inventory import executor_digest
from ..profile import (
    ConfigError,
    fingerprint,
    identifier,
    object_keys,
    origin,
    require_resolved,
)
from ..run_state import RunStateError, write
from .checks import (
    DraftDeliveryViolation,
    OBLIGATION_CHECKS,
    scan_redaction,
    verify_all_obligations,
)
from .contract import (
    CASE_ID as SOURCE_CASE_ID,
    FIXTURE_SOURCE,
    MARKER_PATTERNS,
    PARENT,
    REQUIREMENTS,
    STATUS as LOCAL_STATUS,
    STORY_CHECKS,
    SUITE as SOURCE_SUITE,
    draft_delivery_source_binding,
)
from .run import STORY_DEADLINE_SECONDS, STORY_SOURCE, validate_draft_delivery_binary

SCHEMA_VERSION = 1
PROFILE_CONTRACT = 'dual-draft-delivery-profile-v1'
REPORT_CONTRACT = 'dual-draft-delivery-case-run-v1'
CASE_ID = 'DUAL-DRAFT-DELIVERY-01'
SUITE = 'dual-draft-delivery-v1'
CASE_KEYS = ('draft-delivery',)
_CASE_METHOD = 'execute_draft_delivery_obligation'
LEAF_ROOT_NAME = '20261031-migration-wpe3-draft-delivery-preprod'
DEFAULT_LEAF_ROOT = Path.home() / '.codex' / 'bcs-e2e' / LEAF_ROOT_NAME
_COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
_DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
_HEADER_ENV = {'Authorization': _DRIVER_ENV, 'Cookie': _COOKIE_ENV}
_ENV_REF = re.compile(r'[A-Z][A-Z0-9_]{2,100}')
_MAX_CREDENTIAL_BYTES = 8192
_MARKER_RE = MARKER_PATTERNS[0]


class PreprodClosedError(RunStateError):
    """The preprod leg failed closed before any release claim could be made."""

    def __init__(self, reason_class: str, detail: str, *, observed_status: int | None = None):
        super().__init__(reason_class + ': ' + detail)
        self.reason_class = reason_class
        self.detail = detail
        self.observed_status = observed_status


def _env_reference(value: object, where: str) -> str:
    if not isinstance(value, str) or _ENV_REF.fullmatch(value) is None:
        raise ConfigError(where + ': expected a fixed uppercase environment reference')
    return value


def _policy(value: object) -> dict:
    policy = object_keys(value, {'exclusive_use_confirmed', 'timeout_seconds',
                                 'max_response_bytes', 'failure_retention_hours'},
                         'draft delivery policy')
    if policy['exclusive_use_confirmed'] is not True:
        raise ConfigError('draft delivery policy requires exclusive use of the disposable ID')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= 120:
        raise ConfigError('draft delivery policy.timeout_seconds is outside 1..120')
    if type(policy['max_response_bytes']) is not int or not 1 <= policy['max_response_bytes'] <= 1_048_576:
        raise ConfigError('draft delivery policy.max_response_bytes is outside 1..1048576')
    if type(policy['failure_retention_hours']) is not int or not 1 <= policy['failure_retention_hours'] <= 168:
        raise ConfigError('draft delivery policy.failure_retention_hours is outside 1..168')
    return policy


def validate_draft_delivery_profile(raw: object) -> dict:
    """Validate one tier-admitted draft delivery execution profile."""
    if not isinstance(raw, dict):
        raise ConfigError('draft delivery profile must be an object')
    profile = object_keys(raw, {'schema_version', 'contract', 'environment', 'entrypoint',
                                'human', 'auth', 'cli', 'policy'}, 'draft delivery profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ConfigError('draft delivery profile: unsupported version')
    if profile['contract'] != PROFILE_CONTRACT:
        raise ConfigError('draft delivery profile: unsupported contract')
    environment = object_keys(profile['environment'], {'tier', 'id', 'expected_bcs_env'},
                              'draft delivery environment')
    tier = environment['tier']
    if tier not in ('local', 'preprod'):
        raise ConfigError('draft delivery environment.tier: only owned-local and preprod are admitted')
    identifier(environment['id'], 'draft delivery environment.id')
    if environment['expected_bcs_env'] != ('local' if tier == 'local' else 'pre'):
        raise ConfigError('draft delivery environment: expected_bcs_env binding mismatch')
    entrypoint = object_keys(
        profile['entrypoint'],
        {'kind'} if tier == 'local' else {'kind', 'origin', 'api_face', 'manifest_face'},
        'draft delivery entrypoint')
    if tier == 'local':
        if entrypoint['kind'] != 'owned-local':
            raise ConfigError('draft delivery local entrypoint must be owned-local')
    else:
        if entrypoint['kind'] != 'bcs-direct':
            raise ConfigError('draft delivery preprod entrypoint must be bcs-direct')
        if entrypoint['api_face'] != 'legacy' or entrypoint['manifest_face'] != 'legacy':
            raise ConfigError('draft delivery journey binds the legacy Draft surface')
        origin(entrypoint['origin'], 'preprod')
    identifier(object_keys(profile['human'], {'user_id'}, 'draft delivery human')['user_id'],
               'draft delivery human.user_id')
    auth = object_keys(profile['auth'], {'kind'} if tier == 'local' else {'kind', 'header_env'},
                       'draft delivery auth')
    if tier == 'local':
        if auth['kind'] != 'owned-local':
            raise ConfigError('draft delivery local auth must be owned-local')
    else:
        if auth['kind'] != 'preprod-driver-bot-bearer':
            raise ConfigError('draft delivery preprod auth must bind the driver Bot bearer')
        header_env = object_keys(auth['header_env'], {'Authorization', 'Cookie'},
                                 'draft delivery auth.header_env')
        if (_env_reference(header_env['Authorization'], 'auth.header_env.Authorization')
                != _DRIVER_ENV
                or _env_reference(header_env['Cookie'], 'auth.header_env.Cookie') != _COOKIE_ENV):
            raise ConfigError(
                'draft delivery preprod auth.header_env must reference BCS_E2E_DRIVER_TOKEN '
                'and BCS_E2E_HUMAN_COOKIE by name')
    cli = object_keys(profile['cli'], {'transport', 'command_family', 'api_face'},
                      'draft delivery cli')
    if cli['command_family'] != 'collaboration-draft' or cli['api_face'] != 'legacy':
        raise ConfigError('draft delivery binds the collaboration-draft legacy family')
    if tier == 'local':
        if cli['transport'] != 'owned-local-story':
            raise ConfigError('draft delivery local cli.transport must be owned-local-story')
    elif cli['transport'] != 'product-cli-http':
        raise ConfigError('draft delivery preprod cli.transport must be product-cli-http')
    _policy(profile['policy'])
    return json.loads(json.dumps(profile, allow_nan=False))


def load_draft_delivery_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError('draft delivery profile must be a regular file')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError('draft delivery profile is unreadable JSON') from None
    return validate_draft_delivery_profile(raw)


def draft_delivery_adaptability() -> dict:
    """Describe the dual surface without granting remote release credit."""
    return {
        'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID,
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'environments': ['owned-local-full-story', 'preprod-full-story-bcs-direct-legacy'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'preprod_executor_status': 'PREPROD_FULL_STORY_BEARER_IDENTITY',
        'required_assertion_count': STORY_CHECKS,
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': (
            'Both legs execute the same frozen 32-obligation child story; the owned-local '
            'leg registers an ephemeral author principal, the preprod leg replays it with '
            'a pre-admitted driver Bot bearer identity. Dual execution is a migration '
            'receipt, not release credit.'
        ),
    }


def _fresh_state_dir(path: Path) -> Path:
    requested = Path(path)
    if (not requested.is_absolute() or requested.is_symlink() or requested.exists()
            or not requested.parent.is_dir() or requested.parent.is_symlink()):
        raise ConfigError('draft delivery requires a fresh private state directory')
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError('draft delivery requires a fresh private state directory')
    state_dir.mkdir(mode=0o700, parents=False)
    return state_dir


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounded_environment(header_env: dict) -> dict:
    """Resolve the two named credential references from the current environment."""
    child_environment = dict(os.environ)
    for header, reference in header_env.items():
        value = os.environ.get(reference, '')
        if (not value or not value.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or len(value) > _MAX_CREDENTIAL_BYTES):
            raise PreprodClosedError(
                'PREPROD_DRAFT_DELIVERY_CREDENTIALS_UNAVAILABLE',
                'environment reference ' + reference + ' for header ' + header
                + ' is not loaded; source the preprod credential environment first')
        child_environment[reference] = value
    return child_environment


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass


def _preprod_argv(*, base_url: str, human: str, cli_binary: Path,
                  evidence_path: Path) -> list[str]:
    return [sys.executable, str(STORY_SOURCE),
            '--base-url', base_url,
            '--human', human,
            '--fixture', str(FIXTURE_SOURCE),
            '--evidence-file', str(evidence_path),
            '--cli-binary', str(cli_binary),
            '--identity-mode', 'bearer',
            '--bearer-token-env', _DRIVER_ENV,
            '--cookie-env', _COOKIE_ENV]


def _launch_preprod_story(*, base_url: str, human: str, cli_binary: Path, work: Path,
                          evidence_path: Path, header_env: dict) -> dict:
    """Spawn the frozen story child in bearer identity mode; secrets stay in env."""
    child_environment = _bounded_environment(header_env)
    argv = _preprod_argv(base_url=base_url, human=human, cli_binary=cli_binary,
                         evidence_path=evidence_path)
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=str(work), env=child_environment,
                                   stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
    except OSError as error:
        raise PreprodClosedError('PREPROD_DRAFT_DELIVERY_STORY_BLOCKED',
                                 'story child could not start: ' + type(error).__name__) from None
    try:
        stdout, stderr = process.communicate(timeout=STORY_DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        return {'exit_code': None, 'timed_out': True, 'duration_seconds': 0.0,
                'stdout': b'', 'stderr': b''}
    except BaseException:
        _terminate_process_group(process)
        raise
    return {'exit_code': process.returncode, 'timed_out': False,
            'duration_seconds': round(time.monotonic() - started, 3),
            'stdout': stdout, 'stderr': stderr}


def _parse_story_marker(text) -> tuple[bool, int]:
    if isinstance(text, bytes):
        text = text.decode('utf-8', errors='replace')
    match = _MARKER_RE.search(text)
    if match is None:
        return False, 0
    return True, int(match.group(1))


def _observed_status(stderr_text: str) -> int | None:
    match = re.search(r'unexpected HTTP status (\d{3})', stderr_text)
    return int(match.group(1)) if match else None


def _failure_label(stderr_text: str) -> str:
    for line in stderr_text.splitlines():
        if line.startswith('Draft delivery story failed: '):
            return line[len('Draft delivery story failed: '):][:512]
    return ''


def _redaction_result(stream_items: list[tuple[str, bytes]],
                      artifact_items: list[tuple[str, bytes]]) -> dict:
    streams = [scan_redaction(name, payload) for name, payload in stream_items]
    artifacts = [scan_redaction(name, payload) for name, payload in artifact_items]
    rows = streams + artifacts
    return {'token_material_captured': False, 'streams': streams, 'artifacts': artifacts,
            'status': 'PASS' if all(row['clean'] is True for row in rows) else 'BLOCKED'}


def _local_execution(result: dict) -> CaseExecution:
    observed = result.get('child_receipt', {}).get('observed_checks', 0)
    checked = set(result.get('checks', {}).get('checked', []))
    passed = (
        result.get('status') == LOCAL_STATUS
        and result.get('checks', {}).get('status') == 'PASS'
        and observed == STORY_CHECKS
        and checked == set(REQUIREMENTS)
    )
    error_type = None if passed else 'LOCAL_DRAFT_DELIVERY_BLOCKED'
    steps = []
    for index, requirement_id in enumerate(REQUIREMENTS):
        step_passed = passed and requirement_id in checked
        steps.append(StepOutcome(
            requirement_id=requirement_id,
            method=_CASE_METHOD,
            status='PASS' if step_passed else 'NOT_RUN',
            data={'source_case_id': SOURCE_CASE_ID} if step_passed else None,
            evidence={'story_checks': observed,
                      'assertion_scope': 'FROZEN_CASE_LEVEL_CHILD',
                      'source_case_id': SOURCE_CASE_ID,
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False} if step_passed else {},
            error_type=None if passed else (error_type if index == 0 else None),
        ))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT,
        status='PASS' if passed else 'BLOCKED',
        steps=tuple(steps), completed_count=len(REQUIREMENTS) if passed else 0,
        total_count=len(REQUIREMENTS), error_type=None if passed else error_type,
        ready_to_execute=passed,
    )


def _invalid_step(requirement_id: str, error_type: str) -> StepOutcome:
    return StepOutcome(requirement_id=requirement_id, method=_CASE_METHOD,
                       status='NOT_RUN', data=None, evidence={}, error_type=error_type)


def _blocked_case_execution(error_class: str) -> CaseExecution:
    """Fail-closed whole-case execution when the story child did not complete."""
    steps = [_invalid_step(REQUIREMENTS[0], error_class)] + [
        StepOutcome(requirement_id=requirement_id, method=_CASE_METHOD,
                    status='NOT_RUN', data=None, evidence={}, error_type=None)
        for requirement_id in REQUIREMENTS[1:]]
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
        steps=tuple(steps), completed_count=0, total_count=len(REQUIREMENTS),
        error_type=error_class, ready_to_execute=False,
    )


def _preprod_execution(evidence: object, *, blocked_error: str) -> CaseExecution:
    """Fail-fast per-obligation steps from whatever evidence the child produced."""
    steps: list[StepOutcome] = []
    completed = 0
    failed = False
    observations = (evidence.get('obligations', {})
                    if isinstance(evidence, dict) else {})
    if not all(isinstance(observations.get(requirement_id), dict)
               for requirement_id, _check in OBLIGATION_CHECKS):
        # A malformed/incomplete obligation envelope invalidates the whole case.
        return _blocked_case_execution(blocked_error)
    for requirement_id, check in OBLIGATION_CHECKS:
        if failed:
            steps.append(StepOutcome(requirement_id=requirement_id, method=_CASE_METHOD,
                                     status='NOT_RUN', data=None, evidence={},
                                     error_type=None))
            continue
        record = observations.get(requirement_id)
        if not isinstance(record, dict):
            steps.append(_invalid_step(requirement_id, blocked_error))
            failed = True
            continue
        try:
            check(evidence)
        except DraftDeliveryViolation:
            steps.append(_invalid_step(
                requirement_id, 'PREPROD_DRAFT_DELIVERY_OBLIGATION_VIOLATION'))
            failed = True
            continue
        completed += 1
        steps.append(StepOutcome(
            requirement_id=requirement_id, method=_CASE_METHOD, status='PASS',
            data=record, error_type=None,
            evidence={'identity_mode': evidence.get('identity_mode')
                      if isinstance(evidence, dict) else 'bearer',
                      'assertion_scope': 'FROZEN_CASE_LEVEL_CHILD',
                      'source_case_id': SOURCE_CASE_ID,
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False}))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT,
        status='PASS' if completed == len(REQUIREMENTS) else 'BLOCKED',
        steps=tuple(steps), completed_count=completed,
        total_count=len(REQUIREMENTS),
        error_type=None if completed == len(REQUIREMENTS) else blocked_error,
        ready_to_execute=completed == len(REQUIREMENTS),
    )


def _write_preprod_leaf(*, run_id: str, report: dict, story_receipt: dict,
                        leaf_root: Path | None) -> Path:
    root = Path(leaf_root) if leaf_root is not None else DEFAULT_LEAF_ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    slug = identifier(run_id, 'draft delivery run ID').replace('/', '_')
    stdout_path = root / (slug + '-stdout.json')
    if not stdout_path.exists():
        stdout_payload = dict(report)
        stdout_payload.setdefault('schema_version', SCHEMA_VERSION)
        write(stdout_path, stdout_payload)
    verdict = {
        'schema_version': SCHEMA_VERSION,
        'contract': REPORT_CONTRACT,
        'case_id': CASE_ID,
        'parent_id': PARENT,
        'run_id': run_id,
        'status': report.get('status'),
        'error_type': (report.get('error_type')
                       or report.get('preprod_blocker', {}).get('reason_class')),
        'reason': report.get('preprod_blocker', {}).get('reason', ''),
        'observed_status': report.get('preprod_blocker', {}).get('observed_status'),
        'profile_digest': report.get('profile_digest'),
        'executor_digest': report.get('executor_digest'),
        'bcs_binary_sha256': report.get('bcs_binary_sha256'),
        'cli_binary_sha256': report.get('cli_binary_sha256'),
        'story_exit_code': story_receipt.get('exit_code'),
        'story_observed_checks': story_receipt.get('observed_checks'),
        'story_timed_out': story_receipt.get('timed_out'),
        'obligations_completed': report.get('execution', {}).get('completed_count'),
        'obligations_total': report.get('execution', {}).get('total_count'),
        'story_checks': STORY_CHECKS,
        'environment': report.get('environment'),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }
    write(root / (slug + '-verdict.json'), verdict)
    stderr_bytes = story_receipt.get('stderr') or b''
    if scan_redaction('story.stderr', stderr_bytes)['clean'] is True:
        (root / (slug + '-stderr.txt')).write_bytes(stderr_bytes[:65536])
    else:
        (root / (slug + '-stderr.txt')).write_bytes(
            b'redaction gate refused to persist the captured child stream\n')
    return root


def dual_draft_delivery_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                                 allow_write: bool, confirm_run_id: str,
                                 binary: Path, binary_sha256: str,
                                 cli_binary: Path, cli_binary_sha256: str,
                                 leaf_root: Path | None = None) -> dict:
    """Run the frozen Draft delivery journey through the tier admitted by the profile."""
    run_id = identifier(run_id, 'draft delivery run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            'draft delivery requires explicit write permission and exact Run confirmation')
    profile = load_draft_delivery_profile(Path(profile_path))
    require_resolved(profile)
    tier = profile['environment']['tier']
    # Both frozen runtime roles are validated on entry; the preprod story child
    # only ever receives the bcs-cli author binary.
    binary = validate_draft_delivery_binary(Path(binary), binary_sha256)
    cli_binary = validate_draft_delivery_binary(Path(cli_binary), cli_binary_sha256,
                                                label='CLI')
    digest = fingerprint(profile)
    artifact = executor_digest()
    source_binding = draft_delivery_source_binding()
    state_dir = _fresh_state_dir(Path(state_dir))

    profile_file = state_dir / 'draft-delivery-profile.json'
    binding_file = state_dir / 'draft-delivery-binding.json'
    report_file = state_dir / 'draft-delivery-dual-run.json'
    write(profile_file, profile)
    binding = {
        'schema_version': SCHEMA_VERSION,
        'contract': REPORT_CONTRACT,
        'suite': SUITE,
        'source_suite': SOURCE_SUITE,
        'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID,
        'parent_id': PARENT,
        'run_id': run_id,
        'profile_digest': digest,
        'executor_digest': artifact,
        'source_binding_digest': fingerprint(source_binding),
        'requirement_ids': list(REQUIREMENTS),
        'story_checks': STORY_CHECKS,
        'state_dir': str(state_dir),
        'bcs_binary_sha256': binary_sha256,
        'cli_binary_sha256': cli_binary_sha256,
    }
    write(binding_file, binding)

    local_result = None
    story_receipt: dict = {}
    preprod_blocker = None
    try:
        if tier == 'local':
            from .run import local_draft_delivery_run

            local_result = local_draft_delivery_run(
                binary=binary, binary_sha256=binary_sha256,
                cli_binary=cli_binary, cli_binary_sha256=cli_binary_sha256,
                state_dir=state_dir / 'owned-draft-delivery', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
            execution = _local_execution(local_result)
        else:
            execution, story_receipt, preprod_blocker = _preprod_leg(
                profile=profile, cli_binary=cli_binary, state_dir=state_dir, run_id=run_id)
    except PreprodClosedError as closed:
        story_receipt = _empty_receipt(profile)
        execution = CaseExecution(
            case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
            steps=tuple(_invalid_step(requirement_id, closed.reason_class)
                        if index == 0 else
                        StepOutcome(requirement_id=requirement_id, method=_CASE_METHOD,
                                    status='NOT_RUN', data=None, evidence={}, error_type=None)
                        for index, requirement_id in enumerate(REQUIREMENTS)),
            completed_count=0, total_count=len(REQUIREMENTS),
            error_type=closed.reason_class, ready_to_execute=False)
        preprod_blocker = {
            'reason_class': closed.reason_class,
            'observed_status': closed.observed_status,
            'failure_label': '',
            'reason': closed.detail,
            'story_exit_code': None,
            'story_timed_out': False,
            'observed_checks': 0,
            'credential_refs': list(_HEADER_ENV.keys()),
        }
        story_receipt['checks_status'] = 'BLOCKED'
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(f'draft delivery case execution is unavailable: {error}') from None

    status = 'PASS' if execution.status == 'PASS' else 'BLOCKED'
    report = {
        'schema_version': SCHEMA_VERSION,
        'contract': REPORT_CONTRACT,
        'case_id': execution.case_id,
        'source_case_id': SOURCE_CASE_ID,
        'parent_id': PARENT,
        'run_id': run_id,
        'status': status,
        'profile_digest': digest,
        'executor_digest': artifact,
        'bcs_binary_sha256': binary_sha256,
        'cli_binary_sha256': cli_binary_sha256,
        'source_binding_digest': binding['source_binding_digest'],
        'state_dir': str(state_dir),
        'environment': {
            'tier': tier,
            'id': profile['environment']['id'],
            'expected_bcs_env': profile['environment']['expected_bcs_env'],
            'entrypoint_kind': profile['entrypoint']['kind'],
        },
        'obligation_summary': {
            'total': len(REQUIREMENTS),
            'requirement_ids': list(REQUIREMENTS),
            'story_checks': STORY_CHECKS,
            'source_case_id': SOURCE_CASE_ID,
        },
        'case': execution.snapshot(),
        'execution': {
            'status': execution.status,
            'completed_count': execution.completed_count,
            'total_count': execution.total_count,
            'error_type': execution.error_type,
        },
        'resource_disposition': (
            'DISPOSABLE_DRAFT_TOMBSTONED' if tier == 'local'
            else 'DISPOSABLE_PREPROD_DRAFT_TOMBSTONED'),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'credential_kind': (
            'owned_local_ephemeral_principal' if tier == 'local'
            else 'preprod_driver_bot_bearer'),
    }
    if tier == 'local':
        report['local_owned_run'] = {
            'status': local_result.get('status'),
            'child_receipt': local_result.get('child_receipt'),
            'checks': local_result.get('checks', {}).get('status'),
            'canonical_summary_status': (
                local_result.get('canonical_summary', {}).get('status')),
            'state_dir': str(state_dir / 'owned-draft-delivery'),
        }
    else:
        report['preprod_story'] = story_receipt
        if preprod_blocker is not None:
            report['preprod_blocker'] = preprod_blocker
            report['environment']['api_face'] = 'legacy'
            leaf = _write_preprod_leaf(run_id=run_id, report=report,
                                       story_receipt=story_receipt, leaf_root=leaf_root)
            report['preprod_evidence_leaf'] = str(leaf)
    write(report_file, report)
    return report


def _empty_receipt(profile: dict) -> dict:
    return {'exit_code': None, 'timed_out': False, 'duration_seconds': 0.0,
            'observed_checks': 0,
            'markers': {'draft-delivery-compliance': False},
            'stdout_sha256': _sha256(b''), 'stdout_bytes': 0,
            'stderr_sha256': _sha256(b''), 'stderr_bytes': 0,
            'evidence_bytes': 0, 'identity_mode': 'bearer',
            'redaction_status': 'NOT_RUN', 'checks_status': 'BLOCKED'}


def _preprod_leg(*, profile: dict, cli_binary: Path, state_dir: Path,
                 run_id: str) -> tuple[CaseExecution, dict, dict | None]:
    """Spawn the bearer-mode story child and compose its fail-closed receipt."""
    human = profile['human']['user_id']
    base_url = profile['entrypoint']['origin']
    work = state_dir / 'preprod-story-work'
    work.mkdir(mode=0o700, exist_ok=True)
    evidence_path = state_dir / 'draft-delivery-preprod-evidence.json'
    argv = _preprod_argv(base_url=base_url, human=human, cli_binary=cli_binary,
                         evidence_path=evidence_path)
    try:
        receipt = _launch_preprod_story(
            base_url=base_url, human=human, cli_binary=cli_binary, work=work,
            evidence_path=evidence_path, header_env=profile['auth']['header_env'])
    except PreprodClosedError as closed:
        story_receipt = _empty_receipt(profile)
        story_receipt['argv'] = [str(item) for item in argv]
        return (CaseExecution(
                    case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
                    steps=tuple(_invalid_step(requirement_id, closed.reason_class)
                                if index == 0 else
                                StepOutcome(requirement_id=requirement_id,
                                            method=_CASE_METHOD, status='NOT_RUN',
                                            data=None, evidence={}, error_type=None)
                                for index, requirement_id in enumerate(REQUIREMENTS)),
                    completed_count=0, total_count=len(REQUIREMENTS),
                    error_type=closed.reason_class, ready_to_execute=False),
                story_receipt,
                {'reason_class': closed.reason_class,
                 'observed_status': closed.observed_status,
                 'failure_label': '',
                 'reason': closed.detail,
                 'story_exit_code': None,
                 'story_timed_out': False,
                 'observed_checks': 0})
    stdout, stderr = receipt['stdout'], receipt['stderr']
    stderr_text = stderr.decode('utf-8', errors='replace')
    marker_ok, observed_checks = _parse_story_marker(
        stdout.decode('utf-8', errors='replace'))
    evidence_raw = (evidence_path.read_bytes()
                    if evidence_path.is_file() and not evidence_path.is_symlink()
                    else b'')
    evidence = None
    try:
        evidence = json.loads(evidence_raw) if evidence_raw else None
    except (UnicodeError, json.JSONDecodeError):
        evidence = None
    story_receipt = {
        'exit_code': receipt['exit_code'], 'timed_out': receipt['timed_out'],
        'duration_seconds': receipt['duration_seconds'],
        'observed_checks': observed_checks,
        'markers': {'draft-delivery-compliance': marker_ok},
        'stdout_sha256': _sha256(stdout), 'stdout_bytes': len(stdout),
        'stderr_sha256': _sha256(stderr), 'stderr_bytes': len(stderr),
        'evidence_bytes': len(evidence_raw),
        'argv': [str(item) for item in argv],
        'identity_mode': 'bearer',
    }
    blocked_error = 'PREPROD_DRAFT_DELIVERY_STORY_BLOCKED'
    reason_detail = 'preprod story child did not complete the frozen journey'
    observed_status = _observed_status(stderr_text)
    label = _failure_label(stderr_text)
    redaction = _redaction_result(
        [('story.stdout', stdout), ('story.stderr', stderr),
         ('child.evidence', evidence_raw)],
        [('child.receipt', json.dumps(story_receipt, sort_keys=True).encode())])
    child_ok = (receipt['exit_code'] == 0 and not receipt['timed_out']
                and marker_ok and observed_checks == STORY_CHECKS
                and isinstance(evidence, dict))
    checks_status = 'BLOCKED'
    if child_ok:
        checked_evidence = dict(evidence)
        checked_evidence['redaction'] = redaction
        try:
            verify_all_obligations(checked_evidence)
            checks_status = 'PASS'
        except DraftDeliveryViolation as violation:
            blocked_error = 'PREPROD_DRAFT_DELIVERY_OBLIGATION_VIOLATION'
            reason_detail = str(violation)[:512]
        except (RunStateError, TypeError, ValueError, AttributeError, KeyError):
            blocked_error = 'PREPROD_DRAFT_DELIVERY_STORY_BLOCKED'
            reason_detail = 'preprod story evidence envelope is malformed'
    if redaction['status'] != 'PASS':
        blocked_error = 'PREPROD_DRAFT_DELIVERY_REDACTION_GATE'
        reason_detail = 'a preprod child stream or artifact leaked a credential canary'
    if receipt['timed_out']:
        blocked_error = 'PREPROD_DRAFT_DELIVERY_STORY_DEADLINE'
        reason_detail = 'preprod story child exceeded its 900-second deadline'
    story_receipt['redaction_status'] = redaction['status']
    story_receipt['checks_status'] = checks_status
    execution = (
        _preprod_execution(evidence, blocked_error=blocked_error) if child_ok
        else _blocked_case_execution(blocked_error)
    )
    if execution.status != 'PASS':
        # Keep the fail-fast partial completion count honest in the receipt.
        execution = CaseExecution(
            case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
            steps=execution.steps, completed_count=execution.completed_count,
            total_count=len(REQUIREMENTS), error_type=blocked_error,
            ready_to_execute=False)
        blocker = {
            'reason_class': blocked_error,
            'observed_status': observed_status,
            'failure_label': label,
            'reason': reason_detail,
            'story_exit_code': receipt['exit_code'],
            'story_timed_out': receipt['timed_out'],
            'observed_checks': observed_checks,
        }
        return execution, story_receipt, blocker
    return execution, story_receipt, None
