"""Dual owned-local/preprod execution surface for the Retired service policy slice.

The owned-local leg delegates unchanged to the frozen flat black-box executor
(``local_service_policy_run``, 23 story checks, register identity mode). The
preprod leg spawns the bearer black-box child
(``service_policy/story.py``, 20 story checks) against the reviewed
bcs-direct origin and resolves the driver Bot bearer and the Human Cookie
only from named process-environment references; both travel on the wire as
a duo request (Human cookie for station ACE admission, driver Bot bearer
for journey actor attribution). No leg stores a credential.

The registration obligation maps honestly on preprod: the shared dedicated
pool driver Bot is pre-admitted, so the runner never registers Bots and
records ``REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER`` as a PASS obligation
(option A) while the bootstrap obligation maps to a live driver identity
confirmation. Any preprod fail-closed outcome emits the machine-readable
BLOCKED evidence leaf under
``~/.codex/bcs-e2e/20261031-migration-wps-service-policy-preprod``.
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
from ..local_service_policy_contract import (CASE_ID as SOURCE_CASE_ID,
                                             MARKER_KEYS, MARKER_PATTERNS, PARENT,
                                             REQUIREMENTS, STATUS as LOCAL_STATUS,
                                             STORY_CHECKS, SUITE as SOURCE_SUITE,
                                             service_policy_source_binding)
from ..local_service_policy_run import local_service_policy_run, \
    validate_service_policy_binary
from ..profile import (ConfigError, fingerprint, identifier, object_keys, origin,
                       require_resolved)
from ..run_state import RunStateError, write
from .checks import ServicePolicyViolation, scan_redaction, verify_all_obligations
from .contract import (BEARER_STORY_CHECKS, COOKIE_ENV, DEFAULT_LEAF_ROOT, DRIVER_BOT_ID,
                       DRIVER_ENV, EFFECTS, ENV_REF, HEADER_ENV, MAX_CREDENTIAL_BYTES,
                       NOT_APPLICABLE_REQUIREMENT, PROFILE_CONTRACT, REGISTRATION_DISPOSITION,
                       REPORT_CONTRACT, STATION_ROUTES, DUAL_CASE_ID, DUAL_SUITE,
                       service_policy_dual_source_binding)

SCHEMA_VERSION = 1
SUITE = DUAL_SUITE
CASE_ID = DUAL_CASE_ID
CASE_KEYS = ('service-policy',)
_CASE_METHOD = 'execute_service_policy_obligation'
STORY_DEADLINE_SECONDS = 900
BEARER_STORY_SOURCE = Path(__file__).resolve().parent / 'story.py'
_MARKER_RE = MARKER_PATTERNS[0]
_LOCAL_BLOCKED = 'LOCAL_SERVICE_POLICY_BLOCKED'
_CREDENTIALS_UNAVAILABLE = 'PREPROD_SERVICE_POLICY_CREDENTIALS_UNAVAILABLE'
_STORY_BLOCKED = 'PREPROD_SERVICE_POLICY_STORY_BLOCKED'
_OBLIGATION_VIOLATION = 'PREPROD_SERVICE_POLICY_OBLIGATION_VIOLATION'
_REDACTION_GATE = 'PREPROD_SERVICE_POLICY_REDACTION_GATE'
_STORY_DEADLINE = 'PREPROD_SERVICE_POLICY_STORY_DEADLINE'
_APPLICABLE_COUNT = len(REQUIREMENTS) - 1


class PreprodClosedError(RunStateError):
    """The preprod leg failed closed before any release claim could be made."""

    def __init__(self, reason_class: str, detail: str, *, observed_status: int | None = None):
        super().__init__(reason_class + ': ' + detail)
        self.reason_class = reason_class
        self.detail = detail
        self.observed_status = observed_status


def _env_reference(value: object, where: str) -> str:
    if not isinstance(value, str) or ENV_REF.fullmatch(value) is None:
        raise ConfigError(where + ': expected a fixed uppercase environment reference')
    return value


def _policy(value: object) -> dict:
    policy = object_keys(value, {'exclusive_use_confirmed', 'timeout_seconds',
                                 'max_response_bytes', 'failure_retention_hours'},
                         'service policy policy')
    if policy['exclusive_use_confirmed'] is not True:
        raise ConfigError('service policy policy requires exclusive use of the dedicated fixture pool')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= 120:
        raise ConfigError('service policy policy.timeout_seconds is outside 1..120')
    if type(policy['max_response_bytes']) is not int \
            or not 1 <= policy['max_response_bytes'] <= 1_048_576:
        raise ConfigError('service policy policy.max_response_bytes is outside 1..1048576')
    if type(policy['failure_retention_hours']) is not int \
            or not 1 <= policy['failure_retention_hours'] <= 168:
        raise ConfigError('service policy policy.failure_retention_hours is outside 1..168')
    return policy


def _station_routes(value: object) -> list:
    if not isinstance(value, list) or [dict(row) for row in value] != [dict(row)
                                                                      for row in STATION_ROUTES]:
        raise ConfigError('service policy station_routes drifted from the frozen router truths')
    return [dict(row) for row in value]


def _effects(value: object) -> dict:
    if not isinstance(value, dict) or dict(value) != dict(EFFECTS):
        raise ConfigError('service policy effects drifted from the frozen disposable-resource policy')
    return dict(value)


def validate_service_policy_profile(raw: object) -> dict:
    """Validate one tier-admitted service policy execution profile."""
    if not isinstance(raw, dict):
        raise ConfigError('service policy profile must be an object')
    tier_probe = raw.get('environment') if isinstance(raw.get('environment'), dict) else {}
    tier = tier_probe.get('tier')
    if tier not in ('local', 'preprod'):
        raise ConfigError('service policy environment.tier: only owned-local and preprod are admitted')
    base_keys = {'schema_version', 'contract', 'environment', 'entrypoint', 'human',
                 'auth', 'cli', 'policy', 'station_routes', 'effects'}
    profile = object_keys(raw, base_keys | ({'actors'} if tier == 'preprod' else set()),
                          'service policy profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ConfigError('service policy profile: unsupported version')
    if profile['contract'] != PROFILE_CONTRACT:
        raise ConfigError('service policy profile: unsupported contract')
    environment = object_keys(profile['environment'], {'tier', 'id', 'expected_bcs_env'},
                              'service policy environment')
    identifier(environment['id'], 'service policy environment.id')
    if environment['expected_bcs_env'] != ('local' if tier == 'local' else 'pre'):
        raise ConfigError('service policy environment: expected_bcs_env binding mismatch')
    entrypoint = object_keys(
        profile['entrypoint'],
        {'kind'} if tier == 'local' else {'kind', 'origin', 'api_face', 'manifest_face'},
        'service policy entrypoint')
    if tier == 'local':
        if entrypoint['kind'] != 'owned-local':
            raise ConfigError('service policy local entrypoint must be owned-local')
    else:
        if entrypoint['kind'] != 'bcs-direct':
            raise ConfigError('service policy preprod entrypoint must be bcs-direct')
        if entrypoint['api_face'] != 'legacy' or entrypoint['manifest_face'] != 'legacy':
            raise ConfigError('service policy journey binds the retired legacy service faces')
        origin(entrypoint['origin'], 'preprod')
    identifier(object_keys(profile['human'], {'user_id'}, 'service policy human')['user_id'],
               'service policy human.user_id')
    auth = object_keys(profile['auth'], {'kind'} if tier == 'local' else {'kind', 'header_env'},
                       'service policy auth')
    if tier == 'local':
        if auth['kind'] != 'owned-local':
            raise ConfigError('service policy local auth must be owned-local')
    else:
        if auth['kind'] != 'preprod-driver-bot-bearer':
            raise ConfigError('service policy preprod auth must bind the driver Bot bearer')
        header_env = object_keys(auth['header_env'], {'Authorization', 'Cookie'},
                                 'service policy auth.header_env')
        if (_env_reference(header_env['Authorization'], 'auth.header_env.Authorization')
                != DRIVER_ENV or _env_reference(header_env['Cookie'], 'auth.header_env.Cookie')
                != COOKIE_ENV):
            raise ConfigError(
                'service policy preprod auth.header_env must reference BCS_E2E_DRIVER_TOKEN '
                'and BCS_E2E_HUMAN_COOKIE by name')
        actors = object_keys(profile['actors'], {'driver'}, 'service policy actors')
        driver = object_keys(actors['driver'], {'bot_id'}, 'service policy actors.driver')
        if DRIVER_BOT_ID.fullmatch(driver['bot_id']) is None:
            raise ConfigError(
                'service policy actors.driver.bot_id does not match the dedicated-pool '
                'station shape <bot_prefix>:<user_id>')
    cli = object_keys(profile['cli'], {'transport', 'command_family', 'api_face'},
                      'service policy cli')
    if cli['command_family'] != 'retired-service-policy' or cli['api_face'] != 'legacy':
        raise ConfigError('service policy binds the retired-service-policy legacy family')
    if tier == 'local':
        if cli['transport'] != 'owned-local-story':
            raise ConfigError('service policy local cli.transport must be owned-local-story')
    elif cli['transport'] != 'product-http':
        raise ConfigError('service policy preprod cli.transport must be product-http')
    _policy(profile['policy'])
    _station_routes(profile['station_routes'])
    _effects(profile['effects'])
    return json.loads(json.dumps(profile, allow_nan=False))


def load_service_policy_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError('service policy profile must be a regular file')
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError('service policy profile is unreadable JSON') from None
    return validate_service_policy_profile(raw)


def _fresh_state_dir(path: Path) -> Path:
    requested = Path(path)
    if (not requested.is_absolute() or requested.is_symlink() or requested.exists()
            or not requested.parent.is_dir() or requested.parent.is_symlink()):
        raise ConfigError('service policy requires a fresh private state directory')
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError('service policy requires a fresh private state directory')
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
                or len(value) > MAX_CREDENTIAL_BYTES):
            raise PreprodClosedError(
                _CREDENTIALS_UNAVAILABLE,
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


def _preprod_argv(*, base_url: str, driver_bot: str, evidence_path: Path) -> list[str]:
    return [sys.executable, str(BEARER_STORY_SOURCE),
            '--base-url', base_url,
            '--driver-bot', driver_bot,
            '--bearer-token-env', DRIVER_ENV,
            '--cookie-env', COOKIE_ENV,
            '--evidence-file', str(evidence_path)]


def _launch_preprod_story(*, base_url: str, driver_bot: str, work: Path,
                          evidence_path: Path, header_env: dict) -> dict:
    """Spawn the bearer story child; secrets stay in the child environment."""
    child_environment = _bounded_environment(header_env)
    argv = _preprod_argv(base_url=base_url, driver_bot=driver_bot, evidence_path=evidence_path)
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=str(work), env=child_environment,
                                   stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
    except OSError as error:
        raise PreprodClosedError(_STORY_BLOCKED,
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
    prefix = 'Retired service policy story failed: '
    for line in stderr_text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):][:512]
    return ''


def _redaction_result(stream_items: list[tuple[str, bytes]],
                      artifact_items: list[tuple[str, bytes]]) -> dict:
    streams = [scan_redaction(name, payload) for name, payload in stream_items]
    artifacts = [scan_redaction(name, payload) for name, payload in artifact_items]
    rows = streams + artifacts
    return {'token_material_captured': False, 'streams': streams, 'artifacts': artifacts,
            'status': 'PASS' if all(row['clean'] is True for row in rows) else 'BLOCKED'}


def _invalid_step(requirement_id: str, error_type: str) -> StepOutcome:
    return StepOutcome(requirement_id=requirement_id, method=_CASE_METHOD,
                       status='NOT_RUN', data=None, evidence={}, error_type=error_type)


def _blocked_case_execution(error_class: str) -> CaseExecution:
    """Fail-closed whole-case execution when no leg completed the frozen journey."""
    steps = [_invalid_step(REQUIREMENTS[0], error_class)] + [
        StepOutcome(requirement_id=requirement_id, method=_CASE_METHOD,
                    status='NOT_RUN', data=None, evidence={}, error_type=None)
        for requirement_id in REQUIREMENTS[1:]]
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
        steps=tuple(steps), completed_count=0, total_count=len(REQUIREMENTS),
        error_type=error_class, ready_to_execute=False)


def _local_execution(result: dict) -> CaseExecution:
    """Admit only the complete frozen owned-local child receipt."""
    child_receipt = result.get('child_receipt') or {}
    markers = child_receipt.get('markers') or {}
    passed = (
        result.get('status') == LOCAL_STATUS
        and child_receipt.get('observed_checks') == STORY_CHECKS
        and markers.get(MARKER_KEYS[0]) is True
        and (result.get('canonical_summary') or {}).get('status') == 'LOCAL_SUITE_PASS'
    )
    error_type = None if passed else _LOCAL_BLOCKED
    steps = []
    for requirement_id in REQUIREMENTS:
        steps.append(StepOutcome(
            requirement_id=requirement_id,
            method=_CASE_METHOD,
            status='PASS' if passed else 'NOT_RUN',
            data={'source_case_id': SOURCE_CASE_ID} if passed else None,
            evidence={'story_checks': child_receipt.get('observed_checks', 0),
                      'assertion_scope': 'FROZEN_CASE_LEVEL_CHILD',
                      'source_case_id': SOURCE_CASE_ID,
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False} if passed else {},
            error_type=None if passed else (error_type if requirement_id == REQUIREMENTS[0]
                                            else None),
        ))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT,
        status='PASS' if passed else 'BLOCKED',
        steps=tuple(steps), completed_count=len(REQUIREMENTS) if passed else 0,
        total_count=len(REQUIREMENTS), error_type=None if passed else error_type,
        ready_to_execute=passed)


def _empty_receipt() -> dict:
    return {'exit_code': None, 'timed_out': False, 'duration_seconds': 0.0,
            'observed_checks': 0, 'markers': {MARKER_KEYS[0]: False},
            'stdout_sha256': _sha256(b''), 'stdout_bytes': 0,
            'stderr_sha256': _sha256(b''), 'stderr_bytes': 0,
            'evidence_bytes': 0, 'identity_mode': 'bearer',
            'redaction_status': 'NOT_RUN', 'checks_status': 'BLOCKED'}


def _preprod_execution(evidence: object, *, blocked_error: str) -> CaseExecution:
    """Map bearer evidence onto the eight obligations; registration is option A."""
    observations = (evidence.get('obligations', {})
                    if isinstance(evidence, dict) else {})
    if not all(isinstance(observations.get(requirement_id), dict)
               for requirement_id in REQUIREMENTS):
        return _blocked_case_execution(blocked_error)
    steps: list[StepOutcome] = []
    for requirement_id in REQUIREMENTS:
        record = observations[requirement_id]
        steps.append(StepOutcome(
            requirement_id=requirement_id, method=_CASE_METHOD, status='PASS',
            data=record, error_type=None,
            evidence={'identity_mode': 'bearer',
                      'assertion_scope': 'FROZEN_CASE_LEVEL_CHILD',
                      'source_case_id': SOURCE_CASE_ID,
                      'preprod_mode': (
                          'BEARER_IDENTITY_CONFIRM'
                          if requirement_id == REQUIREMENTS[0]
                          else 'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER'
                          if requirement_id == NOT_APPLICABLE_REQUIREMENT
                          else 'LIVE_BEARER_REPLAY'),
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False}))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status='PASS',
        steps=tuple(steps), completed_count=len(REQUIREMENTS),
        total_count=len(REQUIREMENTS), error_type=None, ready_to_execute=True)


def _write_preprod_leaf(*, run_id: str, report: dict, story_receipt: dict,
                        leaf_root: Path | None) -> Path:
    root = Path(leaf_root) if leaf_root is not None else DEFAULT_LEAF_ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    slug = identifier(run_id, 'service policy run ID').replace('/', '_')
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
        'story_exit_code': story_receipt.get('exit_code'),
        'story_observed_checks': story_receipt.get('observed_checks'),
        'story_checks': BEARER_STORY_CHECKS,
        'story_timed_out': story_receipt.get('timed_out'),
        'obligations_completed': report.get('execution', {}).get('completed_count'),
        'obligations_total': report.get('execution', {}).get('total_count'),
        'obligation_summary': report.get('obligation_summary', {}),
        'environment': report.get('environment'),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }
    write(root / (slug + '-verdict.json'), verdict)
    stderr_bytes = story_receipt.get('stderr') or b''
    stderr_path = root / (slug + '-stderr.txt')
    if scan_redaction('story.stderr', stderr_bytes)['clean'] is True:
        payload = stderr_bytes[:65536]
    else:
        payload = b'redaction gate refused to persist the captured child stream\n'
    fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return root


def _preprod_leg(*, profile: dict, state_dir: Path, run_id: str) -> tuple[CaseExecution, dict, dict | None]:
    """Spawn the bearer story child and compose its fail-closed receipt."""
    driver_bot = profile['actors']['driver']['bot_id']
    if DRIVER_BOT_ID.fullmatch(driver_bot) is None:
        raise ConfigError('service policy actors.driver.bot_id left the station shape')
    base_url = profile['entrypoint']['origin']
    work = state_dir / 'preprod-story-work'
    work.mkdir(mode=0o700, exist_ok=True)
    evidence_path = state_dir / 'service-policy-preprod-evidence.json'
    argv = _preprod_argv(base_url=base_url, driver_bot=driver_bot, evidence_path=evidence_path)
    try:
        receipt = _launch_preprod_story(
            base_url=base_url, driver_bot=driver_bot, work=work,
            evidence_path=evidence_path, header_env=profile['auth']['header_env'])
    except PreprodClosedError as closed:
        story_receipt = _empty_receipt()
        story_receipt['argv'] = [str(item) for item in argv]
        return (_blocked_case_execution(closed.reason_class), story_receipt,
                {'reason_class': closed.reason_class,
                 'observed_status': closed.observed_status,
                 'failure_label': '',
                 'reason': closed.detail,
                 'story_exit_code': None,
                 'story_timed_out': False,
                 'observed_checks': 0,
                 'credential_refs': sorted(HEADER_ENV.values())})
    stdout, stderr = receipt['stdout'], receipt['stderr']
    stderr_text = stderr.decode('utf-8', errors='replace')
    marker_ok, observed_checks = _parse_story_marker(stdout.decode('utf-8', errors='replace'))
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
        'markers': {MARKER_KEYS[0]: marker_ok},
        'stdout_sha256': _sha256(stdout), 'stdout_bytes': len(stdout),
        'stderr_sha256': _sha256(stderr), 'stderr_bytes': len(stderr),
        'evidence_bytes': len(evidence_raw),
        'argv': [str(item) for item in argv],
        'identity_mode': 'bearer',
        'credential_refs': sorted(HEADER_ENV.values()),
    }
    blocked_error = _STORY_BLOCKED
    reason_detail = 'preprod story child did not complete the frozen journey'
    observed_status = _observed_status(stderr_text)
    label = _failure_label(stderr_text)
    redaction = _redaction_result(
        [('story.stdout', stdout), ('story.stderr', stderr),
         ('child.evidence', evidence_raw)],
        [('child.receipt', json.dumps(story_receipt, sort_keys=True).encode())])
    child_ok = (receipt['exit_code'] == 0 and not receipt['timed_out']
                and marker_ok and observed_checks == BEARER_STORY_CHECKS
                and isinstance(evidence, dict))
    checks_status = 'BLOCKED'
    if child_ok:
        checked_evidence = dict(evidence)
        checked_evidence['redaction'] = redaction
        try:
            verify_all_obligations(checked_evidence)
            checks_status = 'PASS'
        except ServicePolicyViolation as violation:
            blocked_error = _OBLIGATION_VIOLATION
            reason_detail = str(violation)[:512]
        except (RunStateError, TypeError, ValueError, AttributeError, KeyError):
            blocked_error = _STORY_BLOCKED
            reason_detail = 'preprod story evidence envelope is malformed'
    if redaction['status'] != 'PASS':
        blocked_error = _REDACTION_GATE
        reason_detail = 'a preprod child stream or artifact leaked a credential canary'
    if receipt['timed_out']:
        blocked_error = _STORY_DEADLINE
        reason_detail = 'preprod story child exceeded its 900-second deadline'
    story_receipt['redaction_status'] = redaction['status']
    story_receipt['checks_status'] = checks_status
    execution = (_preprod_execution(evidence, blocked_error=blocked_error) if child_ok
                 else _blocked_case_execution(blocked_error))
    if execution.status != 'PASS':
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
            'credential_refs': sorted(HEADER_ENV.values()),
        }
        return execution, story_receipt, blocker
    return execution, story_receipt, None


def dual_service_policy_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                                 allow_write: bool, confirm_run_id: str,
                                 binary: Path, binary_sha256: str,
                                 leaf_root: Path | None = None) -> dict:
    """Run the frozen retired service policy journey through the admitted tier."""
    run_id = identifier(run_id, 'service policy run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            'service policy requires explicit write permission and exact Run confirmation')
    profile = load_service_policy_profile(Path(profile_path))
    require_resolved(profile)
    tier = profile['environment']['tier']
    binary = validate_service_policy_binary(Path(binary), binary_sha256)
    digest = fingerprint(profile)
    artifact = executor_digest()
    source_binding = service_policy_dual_source_binding()
    source_binding_flat = service_policy_source_binding()
    state_dir = _fresh_state_dir(Path(state_dir))

    profile_file = state_dir / 'service-policy-profile.json'
    binding_file = state_dir / 'service-policy-binding.json'
    report_file = state_dir / 'service-policy-dual-run.json'
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
        'flat_source_binding_digest': fingerprint(source_binding_flat),
        'requirement_ids': list(REQUIREMENTS),
        'story_checks': {'local': STORY_CHECKS, 'bearer': BEARER_STORY_CHECKS},
        'state_dir': str(state_dir),
        'bcs_binary_sha256': binary_sha256,
    }
    write(binding_file, binding)

    local_result = None
    story_receipt: dict = {}
    preprod_blocker = None
    try:
        if tier == 'local':
            local_result = local_service_policy_run(
                binary=binary, binary_sha256=binary_sha256,
                state_dir=state_dir / 'owned-service-policy', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
            execution = _local_execution(local_result)
        else:
            execution, story_receipt, preprod_blocker = _preprod_leg(
                profile=profile, state_dir=state_dir, run_id=run_id)
    except PreprodClosedError as closed:
        story_receipt = _empty_receipt()
        execution = _blocked_case_execution(closed.reason_class)
        preprod_blocker = {
            'reason_class': closed.reason_class,
            'observed_status': closed.observed_status,
            'failure_label': '',
            'reason': closed.detail,
            'story_exit_code': None,
            'story_timed_out': False,
            'observed_checks': 0,
            'credential_refs': sorted(HEADER_ENV.values()),
        }
        story_receipt['checks_status'] = 'BLOCKED'
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(f'service policy case execution is unavailable: {error}') from None

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
        'source_binding_digest': binding['source_binding_digest'],
        'flat_source_binding_digest': binding['flat_source_binding_digest'],
        'state_dir': str(state_dir),
        'environment': {
            'tier': tier,
            'id': profile['environment']['id'],
            'expected_bcs_env': profile['environment']['expected_bcs_env'],
            'entrypoint_kind': profile['entrypoint']['kind'],
        },
        'obligation_summary': {
            'total': len(REQUIREMENTS),
            'applicable': _APPLICABLE_COUNT,
            'not_applicable': 1,
            'requirement_ids': list(REQUIREMENTS),
            'local_story_checks': STORY_CHECKS,
            'bearer_story_checks': BEARER_STORY_CHECKS,
            'not_applicable_requirement': NOT_APPLICABLE_REQUIREMENT,
            'not_applicable_disposition': REGISTRATION_DISPOSITION,
        },
        'case': execution.snapshot(),
        'execution': {
            'status': execution.status,
            'completed_count': execution.completed_count,
            'total_count': execution.total_count,
            'error_type': execution.error_type,
        },
        'resource_disposition': (
            'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED' if tier == 'local'
            else 'DISPOSABLE_PREPROD_GROUPS_AND_SESSIONS_DELETED_DRIVER_BOT_RETAINED'),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'credential_kind': ('owned_local_ephemeral_principal' if tier == 'local'
                            else 'preprod_driver_bot_bearer'),
    }
    if tier == 'local':
        report['local_owned_run'] = {
            'status': local_result.get('status'),
            'child_receipt': local_result.get('child_receipt'),
            'canonical_summary_status': (local_result.get('canonical_summary') or {}).get('status'),
            'resource_disposition': local_result.get('resource_disposition'),
            'state_dir': str(state_dir / 'owned-service-policy'),
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
