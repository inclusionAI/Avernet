"""Dual owned-local/preprod execution surface for the slow-Judge callback slice.

The owned-local leg delegates unchanged to the frozen flat black-box executor
(``local_slow_judge_run`` over the reused owned loopback Provider/Judge receiver;
22 obligations, register identity mode, inherited ledger MOCK gate).  The preprod
leg is admitted by a bearer profile, resolves the driver Bot token and Human
Cookie only from named process-environment references, and then fail-closes at
journey admission: the frozen bearer slow-Judge journey child is a mainline seam
that this wave does not publish, so the preprod leg opens zero sockets and
records 22 honest NOT_RUN obligations.  Any preprod fail-closed outcome emits the
machine-readable BLOCKED evidence leaf under
``~/.codex/bcs-e2e/20261031-migration-w3a-s28-slow-judge-preprod``.

No leg stores a credential; the CLI author pair, when supplied, is admitted and
digest-fenced but never consumed by this wave.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from ..case_executor import CaseExecution, StepOutcome
from ..inventory import current_manifest, executor_digest
from ..local_judge_contract import (CASE_ID as SOURCE_CASE_ID, PARENT,
                                    REQUIREMENTS, SUITE as SOURCE_SUITE,
                                    judge_source_binding)
from ..local_slow_judge_run import local_slow_judge_run
from ..owned_bcs import file_sha256
from ..profile import (ConfigError, fingerprint, identifier, object_keys, origin,
                       require_resolved, unique_keys)
from ..run_state import RunStateError, write
from .contract import (CLI_PAIR_STATUS, COMMAND, DEFAULT_LEAF_ROOT, DUAL_CASE_ID,
                       DUAL_SUITE, EFFECTS_BLOCKS, ENTRYPOINT_KINDS, HEADER_ENV,
                       IDENTITY_MODES, JUDGE_BLOCKS, LOCAL_PROFILE_KEYS,
                       MAX_CREDENTIAL_BYTES, MOCK_GATE_INHERITANCE, OBLIGATION_COUNT,
                       OPTIONAL_PARAMETERS, PREPROD_ACTOR_KEYS, PREPROD_JOURNEY,
                       PREPROD_MODES, PROFILE_CONTRACT, REPORT_CONTRACT,
                       REQUIRED_PARAMETERS, STATION_BOT_ID, ENVIRONMENT, ENV_REF,
                       SOURCE_SUITE, preprod_obligation_modes,
                       slow_judge_dual_source_binding)
from .reporting import publish_slow_judge_dual_report, scan_redaction

SCHEMA_VERSION = 1
SUITE = DUAL_SUITE
CASE_ID = DUAL_CASE_ID
CASE_KEYS = ('slow-judge',)
_CASE_METHOD = 'execute_slow_judge_obligation'
LOCAL_STATUS = 'LOCAL_SLOW_JUDGE_PASS'
LOCAL_SUITE_ID = SOURCE_SUITE
_JUDGE_MODEL = 'owned-local-slow-judge'
_JUDGE_RESPONSE_FORMAT = 'json_schema'
_LOCAL_BLOCKED = 'LOCAL_SLOW_JUDGE_BLOCKED'
_CREDENTIALS_UNAVAILABLE = 'PREPROD_SLOW_JUDGE_CREDENTIALS_UNAVAILABLE'
_JOURNEY_SEAM_PENDING = 'PREPROD_SLOW_JUDGE_JOURNEY_SEAM_PENDING'
_STATION_DRIFT = 'PREPROD_SLOW_JUDGE_STATION_DRIFT'
_LEG_UNAVAILABLE = 'PREPROD_SLOW_JUDGE_LEG_UNAVAILABLE'
JUDGE_LEAF_STDERR = ('slow judge preprod leg fail-closed at journey admission; '
                     'zero sockets opened by this wave\n')


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
                         'slow judge policy')
    if policy['exclusive_use_confirmed'] is not True:
        raise ConfigError('slow judge policy requires exclusive use of the dedicated fixture pool')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= 120:
        raise ConfigError('slow judge policy.timeout_seconds is outside 1..120')
    if type(policy['max_response_bytes']) is not int \
            or not 1 <= policy['max_response_bytes'] <= 1_048_576:
        raise ConfigError('slow judge policy.max_response_bytes is outside 1..1048576')
    if type(policy['failure_retention_hours']) is not int \
            or not 1 <= policy['failure_retention_hours'] <= 168:
        raise ConfigError('slow judge policy.failure_retention_hours is outside 1..168')
    return policy


def _judge_block(value: object, tier: str) -> dict:
    if not isinstance(value, dict) or dict(value) != dict(JUDGE_BLOCKS[tier]):
        raise ConfigError('slow judge judge block drifted from the frozen family admission')
    return dict(value)


def _effects_block(value: object, tier: str) -> dict:
    if not isinstance(value, dict) or dict(value) != dict(EFFECTS_BLOCKS[tier]):
        raise ConfigError('slow judge effects block drifted from the frozen disposable policy')
    return dict(value)


def validate_slow_judge_profile(raw: object) -> dict:
    """Validate one tier-admitted slow-Judge execution profile."""
    if not isinstance(raw, dict):
        raise ConfigError('slow judge profile must be an object')
    tier_probe = raw.get('environment') if isinstance(raw.get('environment'), dict) else {}
    tier = tier_probe.get('tier')
    if tier not in ('local', 'preprod'):
        raise ConfigError('slow judge environment.tier: only owned-local and preprod are admitted')
    profile = object_keys(raw, LOCAL_PROFILE_KEYS | ({'actors'} if tier == 'preprod' else set()),
                          'slow judge profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ConfigError('slow judge profile: unsupported version')
    if profile['contract'] != PROFILE_CONTRACT:
        raise ConfigError('slow judge profile: unsupported contract')
    environment = object_keys(profile['environment'], {'tier', 'id', 'expected_bcs_env'},
                              'slow judge environment')
    identifier(environment['id'], 'slow judge environment.id')
    if environment['expected_bcs_env'] != ('local' if tier == 'local' else 'pre'):
        raise ConfigError('slow judge environment: expected_bcs_env binding mismatch')
    entrypoint = object_keys(profile['entrypoint'], {'kind'} if tier == 'local'
                             else {'kind', 'origin', 'api_face', 'manifest_face'},
                             'slow judge entrypoint')
    if tier == 'local':
        if entrypoint['kind'] != ENTRYPOINT_KINDS['local']:
            raise ConfigError('slow judge local entrypoint must be owned-local-story')
    else:
        if entrypoint['kind'] != ENTRYPOINT_KINDS['preprod']:
            raise ConfigError('slow judge preprod entrypoint must be bcs-direct')
        if entrypoint['api_face'] != 'current' or entrypoint['manifest_face'] != 'current':
            raise ConfigError('slow judge journey binds the current BCN/BCS faces')
        origin(entrypoint['origin'], 'preprod')
    identifier(object_keys(profile['human'], {'user_id'}, 'slow judge human')['user_id'],
               'slow judge human.user_id')
    auth = object_keys(profile['auth'], {'kind'} if tier == 'local'
                       else {'kind', 'header_env'}, 'slow judge auth')
    if tier == 'local':
        if auth['kind'] != 'owned-local':
            raise ConfigError('slow judge local auth must be owned-local')
    else:
        if auth['kind'] != 'preprod-driver-bot-bearer':
            raise ConfigError('slow judge preprod auth must bind the driver Bot bearer')
        header_env = object_keys(auth['header_env'], {'Authorization', 'Cookie'},
                                 'slow judge auth.header_env')
        if (_env_reference(header_env['Authorization'], 'auth.header_env.Authorization')
                != HEADER_ENV['Authorization']
                or _env_reference(header_env['Cookie'], 'auth.header_env.Cookie')
                != HEADER_ENV['Cookie']):
            raise ConfigError(
                'slow judge preprod auth.header_env must reference BCS_E2E_DRIVER_TOKEN '
                'and BCS_E2E_HUMAN_COOKIE by name')
        actors = object_keys(profile['actors'], set(PREPROD_ACTOR_KEYS), 'slow judge actors')
        for role in sorted(PREPROD_ACTOR_KEYS):
            station = object_keys(actors[role], {'bot_id'}, 'slow judge actors.' + role)
            if STATION_BOT_ID.fullmatch(station['bot_id']) is None:
                raise ConfigError(
                    'slow judge actors.' + role + '.bot_id does not match the dedicated-pool '
                    'station shape <bot_prefix>:<user_id>')
    cli = object_keys(profile['cli'], {'transport', 'command_family', 'api_face'},
                      'slow judge cli')
    if cli['command_family'] != 'judged-provider-callback' or cli['api_face'] != 'current':
        raise ConfigError('slow judge binds the judged-provider-callback family')
    if tier == 'local':
        if cli['transport'] != 'owned-local-story':
            raise ConfigError('slow judge local cli.transport must be owned-local-story')
    elif cli['transport'] != 'product-http':
        raise ConfigError('slow judge preprod cli.transport must be product-http')
    _policy(profile['policy'])
    _judge_block(profile['judge'], tier)
    _effects_block(profile['effects'], tier)
    return json.loads(json.dumps(profile, allow_nan=False))


def load_slow_judge_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError('slow judge profile must be a regular file')
    try:
        raw = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError('slow judge profile is unreadable JSON') from None
    return validate_slow_judge_profile(raw)


def _fresh_state_dir(path: Path) -> Path:
    requested = Path(path)
    if (not requested.is_absolute() or requested.is_symlink() or requested.exists()
            or not requested.parent.is_dir() or requested.parent.is_symlink()):
        raise ConfigError('slow judge requires a fresh private state directory')
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError('slow judge requires a fresh private state directory')
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
                + ' is not loaded; source the preprod credential window first')
        child_environment[reference] = value
    return child_environment


def _validate_binary_pair(path: object, digest: object, label: str) -> Path:
    """Admit one self-consistent executable/sha256 pair before any state write."""
    binary = Path(path) if path is not None else None
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in '0123456789abcdef' for char in digest)):
        raise ConfigError('slow judge ' + label + ' sha256 must be 64 lowercase hex digits')
    if binary is None or not binary.is_absolute() or binary.is_symlink() \
            or not binary.is_file() or not os.access(binary, os.X_OK):
        raise RunStateError(
            'slow judge ' + label + ' binary must be an absolute regular executable')
    if file_sha256(binary) != digest:
        raise RunStateError(
            'slow judge ' + label + ' binary digest differs from the admitted sha256 pair')
    return binary


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
        steps=tuple(steps), completed_count=0, total_count=OBLIGATION_COUNT,
        error_type=error_class, ready_to_execute=False)


def _receiver_receipt(snapshot: object) -> dict:
    """Project the sanitized loopback receiver snapshot into the frozen receipt."""
    if not isinstance(snapshot, dict):
        raise RunStateError('slow judge receiver snapshot is missing')
    drift = []
    if snapshot.get('state') != 'CLOSED':
        drift.append('state')
    if snapshot.get('failed') is not False:
        drift.append('failed')
    if snapshot.get('judge_started') is not True:
        drift.append('judge_started')
    if snapshot.get('judge_released') is not True:
        drift.append('judge_released')
    judge_receipt = snapshot.get('judge_receipt')
    if (not isinstance(judge_receipt, dict) or judge_receipt.get('model') != _JUDGE_MODEL
            or judge_receipt.get('response_format_type') != _JUDGE_RESPONSE_FORMAT):
        drift.append('judge_receipt')
    provider_receipts = snapshot.get('provider_receipts')
    if (not isinstance(provider_receipts, list) or len(provider_receipts) < 2
            or len({row.get('delivery_request_id') for row in provider_receipts}) < 2):
        drift.append('provider_receipts')
    elif any(not isinstance(row, dict) or row.get('method') != 'chat.send'
             or row.get('protocol_version') != '2.0' for row in provider_receipts):
        drift.append('provider_receipts')
    if snapshot.get('judge_request_count') != 1:
        drift.append('judge_request_count')
    if snapshot.get('judge_admissions') != 1:
        drift.append('judge_admissions')
    connection_total = snapshot.get('connection_total')
    if type(connection_total) is not int or connection_total < 3:
        drift.append('connection_total')
    inject_count = snapshot.get('provider_inject_count')
    if type(inject_count) is not int or inject_count < 0:
        drift.append('provider_inject_count')
    if drift:
        raise RunStateError('slow judge receiver receipt drift: ' + ','.join(drift))
    return {
        'state': 'CLOSED',
        'failed': False,
        'judge_started': True,
        'judge_released': True,
        'judge_model': _JUDGE_MODEL,
        'judge_response_format': _JUDGE_RESPONSE_FORMAT,
        'provider_chat_send_receipts': len(provider_receipts),
        'distinct_delivery_requests': len({row['delivery_request_id']
                                           for row in provider_receipts}),
        'judge_request_count': 1,
        'judge_admissions': 1,
        'connection_total': connection_total,
        'provider_inject_count': inject_count,
        'credential_material': 'none',
    }


def _local_execution(result: dict, run_id: str) -> CaseExecution:
    """Admit only the complete frozen owned-local child journey and receipt."""
    from ..case_rewrite import slow_judge_case_rewrite

    plan = slow_judge_case_rewrite()
    source = executor_digest()
    case = result.get('case') or {}
    steps = case.get('steps') or []
    passed = (
        type(result.get('schema_version')) is int and result.get('schema_version') == 1
        and result.get('status') == LOCAL_STATUS
        and result.get('case_id') == SOURCE_CASE_ID
        and result.get('run_id') == run_id
        and result.get('executor_digest') == source
        and case.get('case_id') == SOURCE_CASE_ID
        and case.get('parent_id') == PARENT
        and case.get('status') == 'PASS'
        and case.get('completed_count') == OBLIGATION_COUNT
        and case.get('total_count') == OBLIGATION_COUNT
        and case.get('ready_to_execute') is True
        and case.get('error_type') is None
        and case.get('run_id') == run_id
        and case.get('executor_digest') == source
        and [row.get('requirement_id') for row in steps] == list(plan.requirement_ids)
        and [row.get('method') for row in steps] == [plan.adapters[requirement.requirement_id]
                                                     for requirement in plan.requirements]
        and all(row.get('status') == 'PASS' for row in steps)
        and (result.get('shutdown') or {}).get('status') == 'STOPPED'
        and (result.get('shutdown') or {}).get('returncode') == 0
        and (result.get('credentials') or {}).get('credential_memory') == 'CLEARED'
        and (result.get('canonical_summary') or {}).get('status') == 'LOCAL_SUITE_PASS'
    )
    receiver = _receiver_receipt(result.get('judge_sandbox')) if passed else None
    steps_out = []
    for requirement in plan.requirements:
        adapter_method = plan.adapters[requirement.requirement_id]
        steps_out.append(StepOutcome(
            requirement_id=requirement.requirement_id, method=adapter_method,
            status='PASS' if passed else 'NOT_RUN',
            data={'source_case_id': SOURCE_CASE_ID} if passed else None,
            evidence={'assertion_scope': 'OWNED_LOCAL_FULL_JOURNEY',
                      'source_case_id': SOURCE_CASE_ID,
                      'adapter_method': adapter_method,
                      'receiver_state': receiver['state'] if receiver else None,
                      'judge_request_count': 1 if receiver else 0,
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False} if passed else {},
            error_type=None if passed else _LOCAL_BLOCKED,
        ))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT,
        status='PASS' if passed else 'BLOCKED',
        steps=tuple(steps_out), completed_count=OBLIGATION_COUNT if passed else 0,
        total_count=OBLIGATION_COUNT, error_type=None if passed else _LOCAL_BLOCKED,
        ready_to_execute=passed)


def _empty_receipt() -> dict:
    return {'exit_code': None, 'timed_out': False, 'duration_seconds': 0.0,
            'observed_obligations': 0, 'argv': [],
            'markers': {'slow-judge-journey-admission': False},
            'stdout_sha256': _sha256(b''), 'stdout_bytes': 0,
            'stderr_sha256': _sha256(b''), 'stderr_bytes': 0,
            'identity_mode': 'bearer',
            'credential_refs': sorted(HEADER_ENV.values()),
            'network_requests': 0, 'redaction_status': 'NOT_RUN',
            'checks_status': 'BLOCKED'}


def _preprod_leg(*, profile: dict, state_dir: Path, run_id: str) -> tuple[CaseExecution, dict, dict | None]:
    """Admit the bearer profile, then fail closed at the unpublished journey seam."""
    receipt = _empty_receipt()
    try:
        actors = profile['actors']
        for role in sorted(PREPROD_ACTOR_KEYS):
            if STATION_BOT_ID.fullmatch(actors[role]['bot_id']) is None:
                raise ConfigError('slow judge actors.' + role + '.bot_id left the station shape')
        _bounded_environment(dict(HEADER_ENV))
        # The frozen bearer slow-Judge journey child is a mainline seam; this wave
        # never spawns a story child, never attaches the Cookie, and never opens
        # a socket, so journey admission can only fail closed.
        raise PreprodClosedError(
            _JOURNEY_SEAM_PENDING,
            PREPROD_JOURNEY['story_child'] + '; journey admission fail-closes with '
            + str(OBLIGATION_COUNT) + ' NOT_RUN obligations and network_requests=0')
    except PreprodClosedError as closed:
        reason_class = closed.reason_class
        detail = closed.detail
    except ConfigError:
        reason_class = _STATION_DRIFT
        detail = 'preprod profile station identities drifted after admission'
    except OSError:
        reason_class = _LEG_UNAVAILABLE
        detail = 'slow judge preprod leg is unavailable'
    execution = _blocked_case_execution(reason_class)
    blocker = {
        'reason_class': reason_class,
        'observed_status': None,
        'failure_label': '',
        'reason': detail,
        'story_exit_code': None,
        'story_timed_out': False,
        'observed_obligations': 0,
        'credential_refs': sorted(HEADER_ENV.values()),
        'network_requests': 0,
        'journey_child_status': PREPROD_JOURNEY['status'],
    }
    receipt['checks_status'] = 'BLOCKED'
    receipt['journey_child_status'] = PREPROD_JOURNEY['status']
    return execution, receipt, blocker


def _write_preprod_leaf(*, run_id: str, report: dict, receipt: dict,
                        leaf_root: Path | None) -> Path:
    root = Path(leaf_root) if leaf_root is not None else DEFAULT_LEAF_ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    slug = identifier(run_id, 'slow judge run ID').replace('/', '_')
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
        'observed_status': None,
        'profile_digest': report.get('profile_digest'),
        'executor_digest': report.get('executor_digest'),
        'bcs_binary_sha256': report.get('bcs_binary_sha256'),
        'cli_binary_sha256': report.get('cli_binary_sha256'),
        'obligations_completed': report.get('execution', {}).get('completed_count'),
        'obligations_total': report.get('execution', {}).get('total_count'),
        'obligation_summary': report.get('obligation_summary', {}),
        'environment': report.get('environment'),
        'identity_mode': 'bearer',
        'network_requests': 0,
        'credential_refs': sorted(HEADER_ENV.values()),
        'mock_gate_inherited': MOCK_GATE_INHERITANCE['ledger_gate'],
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }
    write(root / (slug + '-verdict.json'), verdict)
    stderr_bytes = JUDGE_LEAF_STDERR.encode('utf-8')
    if not scan_redaction('preprod.stderr', stderr_bytes)['clean']:
        stderr_bytes = b'redaction gate refused to persist the captured child stream\n'
    fd = os.open(root / (slug + '-stderr.txt'),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(stderr_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    return root


def dual_slow_judge_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                             allow_write: bool, confirm_run_id: str,
                             binary: Path, binary_sha256: str,
                             cli_binary: Path | None = None,
                             cli_binary_sha256: str | None = None,
                             leaf_root: Path | None = None) -> dict:
    """Run the frozen slow-Judge journey through the tier admitted by the profile."""
    run_id = identifier(run_id, 'slow judge run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError('slow judge requires explicit write permission and exact Run confirmation')
    profile = load_slow_judge_profile(Path(profile_path))
    require_resolved(profile)
    if (cli_binary is None) != (cli_binary_sha256 is None):
        raise ConfigError('slow judge requires the CLI author binary pair together or not at all')
    if cli_binary is not None:
        cli_binary = _validate_binary_pair(cli_binary, cli_binary_sha256, 'CLI')
    binary = _validate_binary_pair(binary, binary_sha256, 'BCS')
    digest = fingerprint(profile)
    artifact = executor_digest()
    source_binding = slow_judge_dual_source_binding()
    flat_binding = judge_source_binding()
    tier = profile['environment']['tier']
    state_dir = _fresh_state_dir(Path(state_dir))

    profile_file = state_dir / 'slow-judge-dual-profile.json'
    binding_file = state_dir / 'slow-judge-dual-binding.json'
    report_file = state_dir / 'slow-judge-dual-run.json'
    write(profile_file, profile)
    modes = preprod_obligation_modes()
    binding = {
        'schema_version': SCHEMA_VERSION,
        'contract': REPORT_CONTRACT,
        'suite': SUITE,
        'source_suite': LOCAL_SUITE_ID,
        'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID,
        'parent_id': PARENT,
        'run_id': run_id,
        'profile_digest': digest,
        'executor_digest': artifact,
        'source_binding_digest': fingerprint(source_binding),
        'flat_source_binding_digest': fingerprint(flat_binding),
        'requirement_ids': list(REQUIREMENTS),
        'obligation_count': OBLIGATION_COUNT,
        'local_suite_id': LOCAL_SUITE_ID,
        'story_checks': {'local': OBLIGATION_COUNT, 'preprod_bearer': 'SEAM_PENDING'},
        'preprod_obligation_modes': {mode: len([name for name in REQUIREMENTS
                                                if modes[name] == mode]) for mode in PREPROD_MODES},
        'state_dir': str(state_dir),
        'bcs_binary_sha256': binary_sha256,
        'cli_binary_sha256': (cli_binary_sha256 if cli_binary is not None else None),
        'cli_pair_status': (CLI_PAIR_STATUS if cli_binary is not None
                            else 'NOT_SUPPLIED_BY_CALLER'),
        'judge_gate': dict(JUDGE_BLOCKS[tier]),
        'legacy_inventory_count': len(current_manifest()['legacy_inventory']),
    }
    write(binding_file, binding)

    local_result = None
    preprod_receipt: dict = {}
    preprod_blocker = None
    try:
        if tier == 'local':
            local_result = local_slow_judge_run(
                binary=binary, binary_sha256=binary_sha256,
                state_dir=state_dir / 'owned-slow-judge', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
            execution = _local_execution(local_result, run_id)
        else:
            execution, preprod_receipt, preprod_blocker = _preprod_leg(
                profile=profile, state_dir=state_dir, run_id=run_id)
    except PreprodClosedError as closed:
        preprod_receipt = _empty_receipt()
        execution = _blocked_case_execution(closed.reason_class)
        preprod_blocker = {
            'reason_class': closed.reason_class,
            'observed_status': closed.observed_status,
            'failure_label': '',
            'reason': closed.detail,
            'story_exit_code': None,
            'story_timed_out': False,
            'observed_obligations': 0,
            'credential_refs': sorted(HEADER_ENV.values()),
            'network_requests': 0,
        }
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(f'slow judge case execution is unavailable: {error}') from None

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
        'cli_binary_sha256': binding['cli_binary_sha256'],
        'cli_pair_status': binding['cli_pair_status'],
        'source_binding_digest': binding['source_binding_digest'],
        'flat_source_binding_digest': binding['flat_source_binding_digest'],
        'state_dir': str(state_dir),
        'environment': {
            'tier': tier,
            'id': profile['environment']['id'],
            'expected_bcs_env': profile['environment']['expected_bcs_env'],
            'entrypoint_kind': profile['entrypoint']['kind'],
            'identity_mode': IDENTITY_MODES[tier],
            'frozen_contract': ENVIRONMENT[tier],
        },
        'obligation_summary': {
            'total': OBLIGATION_COUNT,
            'applicable': OBLIGATION_COUNT,
            'requirement_ids': list(REQUIREMENTS),
            'local_suite_id': LOCAL_SUITE_ID,
            'local_assertion_count': OBLIGATION_COUNT,
            'inherited_ledger_gate': MOCK_GATE_INHERITANCE['ledger_gate'],
            'ledger_row': PARENT,
            'preprod_obligation_modes': {mode: [name for name in REQUIREMENTS
                                                if modes[name] == mode]
                                         for mode in PREPROD_MODES},
        },
        'case': execution.snapshot(),
        'execution': {
            'status': execution.status,
            'completed_count': execution.completed_count,
            'total_count': execution.total_count,
            'error_type': execution.error_type,
        },
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'credential_kind': ('owned_local_ephemeral_principal' if tier == 'local'
                            else 'preprod_driver_bot_bearer'),
    }
    if tier == 'local':
        receiver = None
        if execution.status == 'PASS':
            try:
                receiver = _receiver_receipt((local_result or {}).get('judge_sandbox'))
            except RunStateError as drifted:
                raise ConfigError(
                    f'slow judge case execution is unavailable: {drifted}') from None
        report['resource_disposition'] = 'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED'
        report['judge_gate'] = {
            'profile_kind': JUDGE_BLOCKS['local']['kind'],
            'receiver_module': MOCK_GATE_INHERITANCE['receiver_module'],
            'receiver_class': MOCK_GATE_INHERITANCE['receiver_class'],
            'receiver_reuse': MOCK_GATE_INHERITANCE['receiver_reuse'],
            'consumed_this_wave': True,
        }
        report['local_owned_run'] = {
            'status': local_result.get('status'),
            'suite': LOCAL_SUITE_ID,
            'case_id': local_result.get('case_id'),
            'executor_digest': local_result.get('executor_digest'),
            'canonical_summary_status': (local_result.get('canonical_summary') or {}).get('status'),
            'case_status': (local_result.get('case') or {}).get('status'),
            'case_completed_count': (local_result.get('case') or {}).get('completed_count'),
            'case_total_count': (local_result.get('case') or {}).get('total_count'),
            'shutdown_status': (local_result.get('shutdown') or {}).get('status'),
            'shutdown_returncode': (local_result.get('shutdown') or {}).get('returncode'),
            'credential_memory': (local_result.get('credentials') or {}).get('credential_memory'),
            'judge_receiver_receipt': receiver,
            'resource_disposition': 'OWNED_RUNTIME_STOPPED_AND_EVIDENCE_RETAINED',
            'state_dir': str(state_dir / 'owned-slow-judge'),
        }
    else:
        report['resource_disposition'] = 'PREPROD_JOURNEY_SEAM_PENDING_NO_REMOTE_RESOURCES'
        report['judge_gate'] = {
            'profile_kind': JUDGE_BLOCKS['preprod']['kind'],
            'consumed_this_wave': False,
            'note': ('the preprod leg never creates, blocks or releases a judge; '
                     'the frozen bearer journey child is a mainline seam'),
        }
        report['preprod_story'] = preprod_receipt
        report['preprod_journey'] = dict(PREPROD_JOURNEY)
        if preprod_blocker is not None:
            report['preprod_blocker'] = preprod_blocker
            report['environment']['api_face'] = 'current'
            leaf = _write_preprod_leaf(run_id=run_id, report=report,
                                       receipt=preprod_receipt, leaf_root=leaf_root)
            report['preprod_evidence_leaf'] = str(leaf)
    write(report_file, report)
    publish_slow_judge_dual_report(state_dir, binding, report)
    return report


def _arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    for name in REQUIRED_PARAMETERS:
        flag = '--' + name.replace('_', '-')
        if name == 'allow_write':
            parser.add_argument(flag, action='store_true')
        elif name in ('profile', 'binary', 'state_dir'):
            parser.add_argument(flag, type=Path, required=True)
        else:
            parser.add_argument(flag, required=True)
    parser.add_argument('--cli-binary', type=Path, required=False, default=None)
    parser.add_argument('--cli-binary-sha256', required=False, default=None)
    parser.add_argument('--leaf-root', type=Path, required=False, default=None)
    return parser


def build_slow_judge_dual_argv_parser() -> argparse.ArgumentParser:
    return _arguments(argparse.ArgumentParser(
        description='Frozen slow-Judge provider callback dual case executor'))


def register_slow_judge_dual_case_commands(commands) -> None:
    """Drop-in seam helper; mainline owns registration and inventory edits."""
    _arguments(commands.add_parser(
        COMMAND,
        help='Run the 22-obligation slow-Judge provider callback journey through '
             'owned-local or fail-closed preprod journey admission'))


assert set(OPTIONAL_PARAMETERS) == {'cli_binary', 'cli_binary_sha256', 'leaf_root'}
assert set(REQUIRED_PARAMETERS) == {'profile', 'binary', 'binary_sha256', 'state_dir', 'run_id', 'confirm_run_id', 'allow_write'}
assert COMMAND == 'dual-slow-judge-run' and DUAL_SUITE == 'dual-slow-judge-v1'
