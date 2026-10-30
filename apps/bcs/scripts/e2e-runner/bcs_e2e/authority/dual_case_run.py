"""Dual owned-local/preprod execution surface for the Bot-authority slice.

The owned-local leg delegates to the dual-binary owned executor
(``run.local_authority_dual_run``): one owned BCS server plus the frozen
42-obligation black-box child story. The preprod leg is deliberately
narrower: the frozen preprod column is
``REMOTE_ADMISSION_GATED_READ_ONLY``, so the mutating journey is never
replayed remotely. The leg resolves the named driver/credential
references, then always performs the credential-free route probes
(``/health`` and the unauthenticated registration mint rejection) and,
when credentials are actually admitted, two read-only authenticated GET
probes. Every leg fails closed with one machine reason class, records
honest observations only, never retries expired authentication and never
claims preprod release credit. No credential value is ever stored.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from ..case_executor import CaseExecution, StepOutcome
from ..inventory import assert_reviewed_parent, executor_digest, reviewed_release_inventory
from ..profile import (ConfigError, fingerprint, identifier, object_keys, origin,
                       require_resolved)
from ..run_state import RunStateError, write
from ..transport import NoRedirect
from .checks import scan_redaction
from .contract import (CASE_ID as SOURCE_CASE_ID, DISPOSITION, ENVIRONMENT, INTERNAL_PREFIX,
                       MINE_USER_ID, PARENT, PRINCIPAL_SIGNING_KEY_ENV, PUBLIC_PREFIX,
                       REQUIREMENTS, STATUS as LOCAL_STATUS, STORY_CHECKS,
                       SUITE as SOURCE_SUITE, TARGET_USER_ID, authority_source_binding)
from .run import validate_authority_binary

SCHEMA_VERSION = 1
PROFILE_CONTRACT = 'dual-authority-profile-v1'
REPORT_CONTRACT = 'dual-authority-case-run-v1'
CASE_ID = 'DUAL-AUTHORITY-JOURNEY-01'
SUITE = 'dual-authority-v1'
CASE_KEYS = ('authority',)
_CASE_METHOD = 'execute_authority_journey_obligation'
LEAF_ROOT_NAME = '20261031-migration-wp2-authority-preprod'
DEFAULT_LEAF_ROOT = Path.home() / '.codex' / 'bcs-e2e' / LEAF_ROOT_NAME
_DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
_COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
_HEADER_ENV = {'Authorization': _DRIVER_ENV, 'Cookie': _COOKIE_ENV}
_ENV_REF = re.compile(r'^[A-Z][A-Z0-9_]{2,100}$')
_MAX_CREDENTIAL_BYTES = 8192
PROBE_TIMEOUT_SECONDS = 15
_LOCAL_PROFILE = 'owned-local'
_PREPROD_PROFILE = 'preprod'
DISPOSITION_PREPROD_BLOCKED = 'AUTHORITY_PREPROD_ADMISSION_BLOCKED'

_PREPROD_CREDENTIALS_UNAVAILABLE = 'PREPROD_AUTHORITY_CREDENTIALS_UNAVAILABLE'
_PREPROD_ROUTE_PROBE_MISMATCH = 'PREPROD_AUTHORITY_ROUTE_PROBE_MISMATCH'
_PREPROD_ADMISSION_BLOCKED = 'PREPROD_AUTHORITY_ADMISSION_BLOCKED'
_LOCAL_BLOCKED = 'LOCAL_AUTHORITY_DUAL_BLOCKED'


class AuthorityClosedError(RunStateError):
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


def _policy(value: object, *, timeout_max: int, byte_max: int, where: str) -> dict:
    policy = object_keys(value, {'timeout_seconds', 'max_response_bytes',
                                 'failure_retention_hours'}, where + ' policy')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= timeout_max:
        raise ConfigError(where + ' policy.timeout_seconds is outside 1..' + str(timeout_max))
    if type(policy['max_response_bytes']) is not int \
            or not 1 <= policy['max_response_bytes'] <= byte_max:
        raise ConfigError(where + ' policy.max_response_bytes is outside 1..' + str(byte_max))
    if type(policy['failure_retention_hours']) is not int \
            or not 1 <= policy['failure_retention_hours'] <= 168:
        raise ConfigError(where + ' policy.failure_retention_hours is outside 1..168')
    return policy


def validate_authority_profile(raw: object) -> dict:
    """Validate one tier-admitted Bot-authority execution profile."""
    if not isinstance(raw, dict):
        raise ConfigError('Bot authority profile must be an object')
    profile = object_keys(raw, {'schema_version', 'contract', 'environment', 'entrypoint',
                                'human', 'auth', 'authority', 'cli', 'policy'},
                          'Bot authority profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ConfigError('Bot authority profile: unsupported version')
    if profile['contract'] != PROFILE_CONTRACT:
        raise ConfigError('Bot authority profile: unsupported contract')
    environment = object_keys(profile['environment'], {'tier', 'id', 'expected_bcs_env'},
                              'Bot authority environment')
    tier = environment['tier']
    if tier not in (_LOCAL_PROFILE, _PREPROD_PROFILE):
        raise ConfigError('Bot authority environment.tier: only owned-local and preprod are admitted')
    identifier(environment['id'], 'Bot authority environment.id')
    expected = 'local' if tier == _LOCAL_PROFILE else 'pre'
    if environment['expected_bcs_env'] != expected:
        raise ConfigError('Bot authority environment: expected_bcs_env binding mismatch')
    human = object_keys(profile['human'], {'user_id'}, 'Bot authority human')
    identifier(human['user_id'], 'Bot authority human.user_id')
    if tier == _LOCAL_PROFILE:
        entrypoint = object_keys(profile['entrypoint'], {'kind'}, 'Bot authority entrypoint')
        if entrypoint['kind'] != 'owned-local':
            raise ConfigError('Bot authority local entrypoint must be owned-local')
        auth = object_keys(profile['auth'], {'kind', 'principal_signing_key_env'},
                           'Bot authority auth')
        if auth['kind'] != _LOCAL_PROFILE:
            raise ConfigError('Bot authority local auth must be owned-local')
        if auth['principal_signing_key_env'] != PRINCIPAL_SIGNING_KEY_ENV:
            raise ConfigError('Bot authority local auth must reference the owned principal '
                              'signing key by name')
        authority = object_keys(profile['authority'], {'target_user_id', 'mine_user_id'},
                                'Bot authority authority')
        if authority['target_user_id'] != TARGET_USER_ID \
                or authority['mine_user_id'] != MINE_USER_ID:
            raise ConfigError('Bot authority local authority block differs from the frozen map')
        cli = object_keys(profile['cli'], {'transport', 'command_family', 'api_face'},
                          'Bot authority cli')
        if cli['transport'] != 'owned-local-story' \
                or cli['command_family'] != 'collaboration-authority' \
                or cli['api_face'] != 'public-v1':
            raise ConfigError('Bot authority local cli block differs from the frozen owned-local face')
        _policy(profile['policy'], timeout_max=120, byte_max=1_048_576,
                where='Bot authority local')
    else:
        entrypoint = object_keys(profile['entrypoint'],
                                 {'kind', 'origin', 'api_face', 'manifest_face'},
                                 'Bot authority entrypoint')
        if entrypoint['kind'] != 'bcs-direct':
            raise ConfigError('Bot authority preprod entrypoint must be bcs-direct')
        if entrypoint['api_face'] != 'internal-and-public-v1' \
                or entrypoint['manifest_face'] != 'internal-and-public-v1':
            raise ConfigError('Bot authority preprod entrypoint must bind both v1 faces')
        origin(entrypoint['origin'], 'preprod')
        auth = object_keys(profile['auth'], {'kind', 'header_env'}, 'Bot authority auth')
        if auth['kind'] != 'preprod-driver-bot-bearer':
            raise ConfigError('Bot authority preprod auth must bind the driver Bot bearer')
        header_env = object_keys(auth['header_env'], {'Authorization', 'Cookie'},
                                 'Bot authority auth.header_env')
        if (_env_reference(header_env['Authorization'], 'auth.header_env.Authorization') != _DRIVER_ENV
                or _env_reference(header_env['Cookie'], 'auth.header_env.Cookie') != _COOKIE_ENV):
            raise ConfigError('Bot authority preprod auth.header_env must reference '
                              + _DRIVER_ENV + ' and ' + _COOKIE_ENV + ' by name')
        authority = object_keys(profile['authority'],
                                {'target_user_id_env', 'mine_user_id_env'},
                                'Bot authority authority')
        if _env_reference(authority['target_user_id_env'], 'authority.target_user_id_env') \
                != 'BCS_E2E_AUTHORITY_STAFF_B':
            raise ConfigError('Bot authority preprod authority block must reference '
                              'BCS_E2E_AUTHORITY_STAFF_B by name')
        if _env_reference(authority['mine_user_id_env'], 'authority.mine_user_id_env') \
                != 'BCS_E2E_AUTHORITY_STAFF_MINE':
            raise ConfigError('Bot authority preprod authority block must reference '
                              'BCS_E2E_AUTHORITY_STAFF_MINE by name')
        cli = object_keys(profile['cli'], {'transport', 'command_family', 'api_face'},
                          'Bot authority cli')
        if cli['transport'] != 'product-cli-http' \
                or cli['command_family'] != 'collaboration-authority' \
                or cli['api_face'] != 'public-v1':
            raise ConfigError('Bot authority preprod cli block differs from the frozen product face')
        _policy(profile['policy'], timeout_max=120, byte_max=65536,
                where='Bot authority preprod')
    return json.loads(json.dumps(profile, allow_nan=False))


def load_authority_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError('Bot authority profile must be a regular file')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError('Bot authority profile is unreadable JSON') from None
    return validate_authority_profile(raw)


def authority_dual_adaptability() -> dict:
    """Describe the dual surface without granting remote release credit."""
    return {
        'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID,
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'environments': ['owned-local-full-story', 'preprod-read-only-admission-probes'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'preprod_executor_status': 'PREPROD_READ_ONLY_ADMISSION_GATED_PROBES',
        'required_assertion_count': STORY_CHECKS,
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': (
            'Both legs share one frozen 42-obligation map: the owned-local leg replays the full '
            'journey on an owned runtime, while the preprod leg is frozen to the read-only '
            'admission column (credential-free route probes plus, only when admitted, two '
            'read-only authenticated GETs). The mutating preprod journey stays outside this '
            'slice until the column unfreezes. Dual execution is a migration receipt, not '
            'release credit.'
        ),
    }


def _fresh_state_dir(path: Path) -> Path:
    requested = Path(path)
    if (not requested.is_absolute() or requested.is_symlink() or requested.exists()
            or not requested.parent.is_dir() or requested.parent.is_symlink()):
        raise ConfigError('Bot authority dual execution requires a fresh private state directory')
    state_dir = requested.resolve()
    if state_dir.is_symlink() or state_dir.exists():
        raise ConfigError('Bot authority dual execution requires a fresh private state directory')
    state_dir.mkdir(mode=0o700, parents=False)
    return state_dir


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _admission(header_env: dict) -> tuple[dict, dict | None, AuthorityClosedError | None]:
    """Resolve the named credential references; values never leave memory."""
    resolved: dict = {}
    values: dict = {}
    for header in ('Authorization', 'Cookie'):
        reference = header_env.get(header, '')
        if _ENV_REF.fullmatch(reference) is None:
            return resolved, None, AuthorityClosedError(
                _PREPROD_CREDENTIALS_UNAVAILABLE,
                'auth.header_env must declare the frozen uppercase references')
        value = os.environ.get(reference, '')
        safe = (isinstance(value, str) and bool(value) and value.isascii()
                and not any(ord(char) < 32 or ord(char) == 127 for char in value)
                and len(value) <= _MAX_CREDENTIAL_BYTES)
        resolved[header] = {'env_ref': reference, 'resolved': bool(safe)}
        if safe:
            values[header] = value
    if len(values) != len(header_env):
        return resolved, None, AuthorityClosedError(
            _PREPROD_CREDENTIALS_UNAVAILABLE,
            'environment references ' + ' and '.join(sorted(header_env.values()))
            + ' are not loaded; expired preprod authentication is disclosed and never retried')
    return resolved, values, None


def _probe_opener() -> object:
    return build_opener(ProxyHandler({}), NoRedirect())


def _http_probe(url: str, *, headers: dict | None = None) -> dict:
    """One honest read-only probe; transport failures are observations, not raises."""
    observation = {'method': 'GET', 'url': url, 'request_headers': sorted(headers or {}),
                   'observed_status': None, 'transport_error': None, 'body_status': None}
    try:
        request = urllib.request.Request(url, headers=headers or {})
        with _probe_opener().open(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            observation['observed_status'] = response.status
            raw = response.read(65537)
            try:
                body = json.loads(raw.decode('utf-8', errors='replace')) if raw else None
            except (UnicodeError, json.JSONDecodeError):
                body = None
            observation['body_status'] = body.get('status') if isinstance(body, dict) else None
    except urllib.error.HTTPError as error:
        # An HTTP status is an observed surface fact, never a transport failure.
        observation['observed_status'] = error.status
        raw = error.read(65537)
        try:
            body = json.loads(raw.decode('utf-8', errors='replace')) if raw else None
        except (UnicodeError, json.JSONDecodeError):
            body = None
        observation['body_status'] = body.get('status') if isinstance(body, dict) else None
        observation['transport_error'] = None
    except Exception as error:  # noqa: BLE001 - honest observation, never a raise
        observation['transport_error'] = type(error).__name__
    return observation


def _route_probes(base_url: str) -> list[dict]:
    """Credential-free route probes; always run, never retried."""
    health = _http_probe(base_url + '/health')
    health['route'] = 'health'
    health['expected'] = 'HTTP 200 with a healthy body status'
    health['passed'] = (health['observed_status'] == 200
                        and isinstance(health['body_status'], str)
                        and health['body_status'] in ('ok', 'healthy'))
    mint = _http_probe(base_url + '/register/token')
    mint['route'] = 'register-token-unauthenticated'
    mint['expected'] = 'HTTP 401 or 403 without registration-plane mock admission'
    mint['passed'] = mint['observed_status'] in (401, 403)
    return [health, mint]


def _authenticated_probes(base_url: str, values: dict) -> list[dict]:
    """Two read-only authenticated GETs; statuses only, values never recorded."""
    ownership = _http_probe(base_url + INTERNAL_PREFIX + '/bots/' + uuid.uuid4().hex
                            + '/ownership',
                            headers={'Authorization': 'Bearer ' + values['Authorization']})
    ownership['route'] = 'ownership-read-driver-bearer'
    ownership['expected'] = 'read-only internal ownership GET with the driver Bot bearer'
    ownership['passed'] = isinstance(ownership['observed_status'], int)
    mine = _http_probe(base_url + PUBLIC_PREFIX + '/bots/mine?limit=1',
                       headers={'Cookie': values['Cookie']})
    mine['route'] = 'public-mine-read-human-cookie'
    mine['expected'] = 'read-only public bots/mine GET with the human cookie'
    mine['passed'] = isinstance(mine['observed_status'], int)
    return [ownership, mine]


def _empty_receipt(*, admission: dict, route_probes: list[dict],
                   authenticated: list[dict]) -> dict:
    return {'exit_code': None, 'timed_out': False, 'duration_seconds': 0.0,
            'observed_checks': 0, 'markers': {'authority-journey-compliance': False},
            'argv': [], 'identity_mode': 'read-only-admission-probes',
            'redaction_status': 'NOT_RUN', 'checks_status': 'BLOCKED',
            'admission': admission, 'route_probe_observations': route_probes,
            'authenticated_probe_observations': authenticated}


def _invalid_step(requirement_id: str, error_type: str) -> StepOutcome:
    return StepOutcome(requirement_id=requirement_id, method=_CASE_METHOD,
                       status='NOT_RUN', data=None, evidence={}, error_type=error_type)


def _blocked_case_execution(error_class: str) -> CaseExecution:
    steps = [_invalid_step(REQUIREMENTS[0], error_class)] + [
        StepOutcome(requirement_id=requirement_id, method=_CASE_METHOD,
                    status='NOT_RUN', data=None, evidence={}, error_type=None)
        for requirement_id in REQUIREMENTS[1:]]
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
        steps=tuple(steps), completed_count=0, total_count=len(REQUIREMENTS),
        error_type=error_class, ready_to_execute=False)


def _local_execution(result: dict) -> CaseExecution:
    observed = result.get('child_receipt', {}).get('observed_checks', 0)
    checked = set(result.get('checks', {}).get('checked', []))
    passed = (
        result.get('status') == LOCAL_STATUS
        and result.get('checks', {}).get('status') == 'PASS'
        and observed == STORY_CHECKS
        and checked == set(REQUIREMENTS)
    )
    error_type = None if passed else _LOCAL_BLOCKED
    steps = []
    for requirement_id in REQUIREMENTS:
        steps.append(StepOutcome(
            requirement_id=requirement_id,
            method=_CASE_METHOD,
            status='PASS' if passed else 'NOT_RUN',
            data={'source_case_id': SOURCE_CASE_ID} if passed else None,
            evidence={'story_checks': observed,
                      'assertion_scope': 'FROZEN_CASE_LEVEL_CHILD',
                      'source_case_id': SOURCE_CASE_ID,
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False} if passed else {},
            error_type=None if passed else error_type,
        ))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT,
        status='PASS' if passed else 'BLOCKED',
        steps=tuple(steps), completed_count=len(REQUIREMENTS) if passed else 0,
        total_count=len(REQUIREMENTS), error_type=None if passed else error_type,
        ready_to_execute=passed,
    )


def _reason_priority(admitted: bool, route_ok: bool) -> tuple[str, str]:
    if not admitted:
        return (_PREPROD_CREDENTIALS_UNAVAILABLE,
                'named preprod credential references are not resolved in this process; '
                'expired driver tokens and human cookies are disclosed, never retried')
    if not route_ok:
        return (_PREPROD_ROUTE_PROBE_MISMATCH,
                'credential-free route probes did not observe the frozen healthy or '
                'unauthenticated-mint-rejection surface')
    return (_PREPROD_ADMISSION_BLOCKED,
            'mutating journey outside frozen REMOTE_ADMISSION_GATED_READ_ONLY column')


def _authority_preprod_leg(*, profile: dict) -> tuple[CaseExecution, dict, dict | None]:
    """Admission-check, probe honestly, then fail closed with one reason class."""
    header_env = profile['auth']['header_env']
    base_url = profile['entrypoint']['origin']
    admission, values, closed = _admission(header_env)
    route_probes = _route_probes(base_url)
    route_ok = all(row['passed'] is True for row in route_probes)
    authenticated: list[dict] = []
    if closed is not None:
        reason_class, detail = closed.reason_class, closed.detail
    else:
        reason_class, detail = _reason_priority(values is not None, route_ok)
        if values is not None:
            authenticated = _authenticated_probes(base_url, values)
    observed_status = None
    for observation in route_probes:
        if not observation['passed']:
            observed_status = observation['observed_status']
            break
    execution = _blocked_case_execution(reason_class)
    story_receipt = _empty_receipt(admission=admission, route_probes=route_probes,
                                   authenticated=authenticated)
    blocker = {'reason_class': reason_class, 'observed_status': observed_status,
               'failure_label': '', 'reason': detail, 'story_exit_code': None,
               'story_timed_out': False, 'observed_checks': 0,
               'credential_refs': [_DRIVER_ENV, _COOKIE_ENV],
               'route_probes': [{'route': row['route'], 'expected': row['expected'],
                                 'observed_status': row['observed_status'],
                                 'transport_error': row['transport_error'],
                                 'passed': row['passed']} for row in route_probes],
               'authenticated_probe_routes': [row['route'] for row in authenticated]}
    return execution, story_receipt, blocker


def _write_preprod_leaf(*, run_id: str, report: dict, story_receipt: dict,
                        leaf_root: Path | None) -> Path:
    root = Path(leaf_root) if leaf_root is not None else DEFAULT_LEAF_ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    slug = identifier(run_id, 'Bot authority run ID').replace('/', '_')
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
        'route_probes': story_receipt.get('route_probe_observations', []),
        'authenticated_probe_routes': [row['route'] for row in
                                       story_receipt.get('authenticated_probe_observations', [])],
    }
    stdout_path = root / (slug + '-stdout.json')
    if not stdout_path.exists():
        stdout_payload = dict(report)
        stdout_payload.setdefault('schema_version', SCHEMA_VERSION)
        write(stdout_path, stdout_payload)
    persisted = (json.dumps(verdict, sort_keys=True)
                 + json.dumps(story_receipt, sort_keys=True)).encode('utf-8')
    redaction_clean = scan_redaction('preprod-leaf', persisted)['clean'] is True
    if not redaction_clean:
        # The frozen projections carry header names and statuses only; any hit
        # here is structural drift, so the disclosed probe rows redact away.
        verdict['route_probes'] = []
        verdict['authenticated_probe_routes'] = []
    write(root / (slug + '-verdict.json'), verdict)
    stderr_text = ('read-only preprod admission probes only; no mutating story child '
                   'was launched\n')
    if not redaction_clean:
        stderr_text = 'redaction gate refused to persist the captured child stream\n'
    (root / (slug + '-stderr.txt')).write_text(stderr_text, encoding='utf-8')
    diagnosis = {
        'schema_version': SCHEMA_VERSION,
        'contract': 'authority-preprod-diagnosis-v1',
        'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID,
        'parent_id': PARENT,
        'run_id': run_id,
        'status': report.get('status'),
        'environment': report.get('environment'),
        'credential_refs': [_DRIVER_ENV, _COOKIE_ENV],
        'redaction_gate': {'status': 'PASS' if redaction_clean else 'BLOCKED',
                           'policy': 'credential values are never persisted'},
        'admission': story_receipt.get('admission', {}) if redaction_clean else {},
        'route_probe_observations': (story_receipt.get('route_probe_observations', [])
                                      if redaction_clean else []),
        'authenticated_probe_observations': (
            story_receipt.get('authenticated_probe_observations', []) if redaction_clean else []),
        'reason_class': report.get('preprod_blocker', {}).get('reason_class'),
        'observed_status': report.get('preprod_blocker', {}).get('observed_status'),
        'reason': report.get('preprod_blocker', {}).get('reason', ''),
        'root_causes': [report.get('preprod_blocker', {}).get('reason_class')],
        'context': {
            'mainline_reaccount': ('2026-10-30 trio probe: member bot gek online; driver bot '
                                   'token and human cookie both expired (ACE LOGIN); this leg '
                                   'stays the read-only admission route only'),
            'environment_column': 'REMOTE_ADMISSION_GATED_READ_ONLY',
            'slice': 'dual-authority-v1 preprod leg',
        },
        'no_retry_statement': ('Expired preprod authentication is disclosed, never retried; no '
                               'authentication burn-down and no credential refresh happens '
                               'inside this slice.'),
        'no_credit_statement': ('No preprod execution credit is claimed by this leaf; the '
                                'owned-local receipt is the only executed leg and '
                                'grants_release_credit stays false.'),
        'local_leg': {
            'executor': 'bcs_e2e.authority.run.local_authority_dual_run',
            'obligations': len(REQUIREMENTS),
            'story_checks': STORY_CHECKS,
            'environment': dict(ENVIRONMENT),
        },
    }
    write(root / 'preprod-diagnosis.json', diagnosis)
    return root


def dual_authority_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                            allow_write: bool, confirm_run_id: str,
                            binary: Path, binary_sha256: str,
                            cli_binary: Path, cli_binary_sha256: str,
                            leaf_root: Path | None = None) -> dict:
    """Run the frozen Bot-authority journey through the tier admitted by the profile."""
    run_id = identifier(run_id, 'Bot authority run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError('Bot authority dual execution requires explicit write permission and exact Run confirmation')
    profile = load_authority_profile(Path(profile_path))
    require_resolved(profile)
    tier = profile['environment']['tier']
    binary = validate_authority_binary(Path(binary), binary_sha256, label='BCS')
    cli_binary = validate_authority_binary(Path(cli_binary), cli_binary_sha256, label='CLI')
    assert_reviewed_parent(PARENT, family='authority-dual')
    if PARENT not in {row['id'] for row in reviewed_release_inventory()}:
        raise RunStateError('Bot authority parent left the reviewed release denominator')
    digest = fingerprint(profile)
    artifact = executor_digest()
    source_binding = authority_source_binding()
    state_dir = _fresh_state_dir(Path(state_dir))
    profile_file = state_dir / 'authority-profile.json'
    binding_file = state_dir / 'authority-dual-binding.json'
    report_file = state_dir / 'authority-dual-run.json'
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
        'case_keys': list(CASE_KEYS),
        'state_dir': str(state_dir),
        'bcs_binary_sha256': binary_sha256,
        'cli_binary_sha256': cli_binary_sha256,
    }
    write(binding_file, binding)
    local_result = None
    story_receipt: dict = {}
    preprod_blocker = None
    try:
        if tier == _LOCAL_PROFILE:
            from .run import local_authority_dual_run

            local_result = local_authority_dual_run(
                binary=binary, binary_sha256=binary_sha256,
                cli_binary=cli_binary, cli_binary_sha256=cli_binary_sha256,
                state_dir=state_dir / 'owned-authority', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
            execution = _local_execution(local_result)
        else:
            execution, story_receipt, preprod_blocker = _authority_preprod_leg(
                profile=profile)
    except AuthorityClosedError as closed:
        story_receipt = _empty_receipt(admission={}, route_probes=[], authenticated=[])
        execution = _blocked_case_execution(closed.reason_class)
        preprod_blocker = {
            'reason_class': closed.reason_class, 'observed_status': closed.observed_status,
            'failure_label': '', 'reason': closed.detail, 'story_exit_code': None,
            'story_timed_out': False, 'observed_checks': 0,
            'credential_refs': [_DRIVER_ENV, _COOKIE_ENV],
            'route_probes': [], 'authenticated_probe_routes': []}
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(f'Bot authority case execution is unavailable: {error}') from None
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
        'resource_disposition': (DISPOSITION if tier == _LOCAL_PROFILE
                                 else DISPOSITION_PREPROD_BLOCKED),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'credential_kind': ('owned_local_ephemeral_principal' if tier == _LOCAL_PROFILE
                            else 'preprod_driver_bot_bearer_and_human_cookie_refs'),
    }
    if tier == _LOCAL_PROFILE:
        report['local_owned_run'] = {
            'status': local_result.get('status'),
            'child_receipt': local_result.get('child_receipt'),
            'checks': local_result.get('checks', {}).get('status'),
            'canonical_summary_status': local_result.get('canonical_summary', {}).get('status'),
            'state_dir': str(state_dir / 'owned-authority'),
        }
    else:
        report['preprod_story'] = story_receipt
        if preprod_blocker is not None:
            report['preprod_blocker'] = preprod_blocker
            report['environment']['api_face'] = 'internal-and-public-v1'
            leaf = _write_preprod_leaf(run_id=run_id, report=report,
                                       story_receipt=story_receipt, leaf_root=leaf_root)
            report['preprod_evidence_leaf'] = str(leaf)
    write(report_file, report)
    return report


def build_authority_dual_argv_parser():
    import argparse
    parser = argparse.ArgumentParser(
        description='Dual owned-local/preprod Bot-authority journey executor')
    parser.add_argument('--profile', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--confirm-run-id', required=True)
    parser.add_argument('--allow-write', action='store_true')
    parser.add_argument('--binary', required=True)
    parser.add_argument('--binary-sha256', required=True)
    parser.add_argument('--cli-binary', required=True)
    parser.add_argument('--cli-binary-sha256', required=True)
    return parser


COMMAND = 'dual-authority-run'


def register_authority_dual_case_commands(commands) -> None:
    """Drop-in parser body for the frozen registration seam (describe-only)."""
    case = commands.add_parser(
        COMMAND,
        help='Run the frozen 42-obligation Bot authority journey through owned-local or the bounded preprod admission probe')
    case.add_argument('--profile', type=Path, required=True)
    case.add_argument('--binary', type=Path, required=True)
    case.add_argument('--binary-sha256', required=True)
    case.add_argument('--cli-binary', type=Path, required=True)
    case.add_argument('--cli-binary-sha256', required=True)
    case.add_argument('--state-dir', type=Path, required=True)
    case.add_argument('--run-id', required=True)
    case.add_argument('--confirm-run-id', required=True)
    case.add_argument('--allow-write', action='store_true')


def main(argv: list[str] | None = None) -> int:
    args = build_authority_dual_argv_parser().parse_args(argv)
    try:
        report = dual_authority_case_run(
            profile_path=Path(args.profile), state_dir=Path(args.state_dir),
            run_id=args.run_id, allow_write=args.allow_write,
            confirm_run_id=args.confirm_run_id, binary=Path(args.binary),
            binary_sha256=args.binary_sha256, cli_binary=Path(args.cli_binary),
            cli_binary_sha256=args.cli_binary_sha256)
    except (ConfigError, RunStateError, OSError) as error:
        payload = {'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT,
                   'status': 'BLOCKED', 'error_type': type(error).__name__}
        print(json.dumps(payload, sort_keys=True, indent=2))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report.get('status') == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
