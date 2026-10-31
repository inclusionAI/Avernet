"""Dual owned-local/preprod execution surface for Session owner permissions.

Migrates the frozen ``story_session_owner_permissions`` slice (9 obligations,
60 run-phase HTTP assertions plus 4 cleanup dispositions, upstream shell parsed
to one minimal story) from the owned-local-only backlog surface to a
dual-environment case run. The owned-local leg delegates unchanged to the
frozen backlog executor
:func:`bcs_e2e.session_owner.local_session_owner_run.local_session_owner_run`.
The preprod leg is deliberately offline-honest: the public-v1 gateway face
cannot express the station bootstrap legs, the three-Human ownership matrix,
the participants/collect/view-scoped routes or the operator principal minting
(four documented gaps), so the leg runs a bounded wrong-method route-surface
probe plus a named credential admission check plus the offline
``RemoteSessionOwnerAdapter`` preflight gap receipt, and always fail-closes
BLOCKED. The machine-readable disclosure leaf lands under
``~/.codex/bcs-e2e/20261031-migration-wp2-owner-isolation-preprod``.

Both frozen runtime roles enter through biscuit pairs: the server pair
(``--binary``/``--binary-sha256``) is consumed by the owned-local leg, and the
author CLI pair (``--cli-binary``/``--cli-binary-sha256``) is admitted, digest
fenced and disclosed but never consumed by this in-process HTTP family
journey. No leg stores a credential; preprod credentials resolve only from
named process-environment references at run time.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from ..case_executor import CaseExecution, StepOutcome
from ..inventory import executor_digest
from ..owned_bcs import file_sha256
from ..profile import (
    ConfigError,
    fingerprint,
    identifier,
    object_keys,
    origin,
    parsed_origin,
    require_resolved,
    unique_keys,
)
from ..run_state import RunStateError, write
from ..transport import tls_context
from .local_session_owner_run import (
    JOURNAL_NAME,
    RESOURCE_DISPOSITION_PASS,
    STATUS as LOCAL_STATUS,
    SessionOwnerRunError,
    local_session_owner_run,
    validate_session_owner_binary,
)
from .remote_session_owner_adapter import RemoteSessionOwnerAdapter
from .session_owner_contract import (
    CASE_ID as SOURCE_CASE_ID,
    CLEANUP_CHECKS,
    DOCUMENTED_GAPS,
    PARENT,
    REQUIREMENTS,
    STORY_CHECKS,
    SUITE as SOURCE_SUITE,
    session_owner_dual_adaptability,
    session_owner_source_binding,
)

SCHEMA_VERSION = 1
PROFILE_CONTRACT = 'dual-session-owner-profile-v1'
REPORT_CONTRACT = 'dual-session-owner-case-run-v1'
CASE_ID = 'DUAL-SESSION-OWNER-01'
SUITE = 'dual-session-owner-v1'
CASE_KEYS = ('session-owner',)
COMMAND = 'dual-session-owner-run'
CASE_METHOD = 'execute_session_owner_obligation'
LEAF_ROOT_NAME = '20261031-migration-wp2-owner-isolation-preprod'
DEFAULT_LEAF_ROOT = Path.home() / '.codex' / 'bcs-e2e' / LEAF_ROOT_NAME
COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
HEADER_ENV = {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}
PUBLIC_PREFIX = '/openapi/v1/collaboration'
_MAX_CREDENTIAL_BYTES = 8192
_LOCAL_BLOCKED_ERROR = 'LOCAL_SESSION_OWNER_BLOCKED'
_PREPROD_FULL_STORY_ERROR = 'PREPROD_SESSION_OWNER_FULL_STORY_NOT_EXECUTED'
_CREDENTIALS_UNAVAILABLE = 'PREPROD_SESSION_OWNER_CREDENTIALS_UNAVAILABLE'
_AUTHOR_CLI_DISCLOSURE = 'author_cli_pair_admitted_frozen_not_consumed_by_family_journey'
_SENTINEL = '__bcs_e2e_session_owner_probe__'
_BASELINE_PATH = '/__bcs_e2e_absent_session_owner_probe__'
_PRESENT_PROBE_STATUSES = frozenset({400, 401, 403, 405})
_ABSENT_STATUS = 404


class PreprodClosedError(RunStateError):
    """The preprod credential admission failed closed before any probe."""

    def __init__(self, reason_class: str, detail: str):
        super().__init__(reason_class + ': ' + detail)
        self.reason_class = reason_class
        self.detail = detail


REGISTRATION_SEAM = {
    'status': 'DESCRIBE_ONLY_MAINLINE_WRITES',
    'cli_py': (
        "cli.py: import block: from bcs_e2e.session_owner.dual_case_run import (COMMAND, "
        "CASE_KEYS as SESSION_OWNER_DUAL_CASE_KEYS, dual_session_owner_case_run); "
        "dispatch block mirroring the dual-draft-delivery-run shape with both biscuit "
        "pairs and optional --leaf-root; rc = 0 if report['status'] == 'PASS' else 2"),
    'case_command_registration_py': (
        "case_command_registration.py: DUAL_CASE_COMMANDS frozenset gains 'dual-session-owner-run'; parser block with "
        "--profile plus BOTH required biscuit pairs --binary/--binary-sha256 and "
        "--cli-binary/--cli-binary-sha256, --state-dir, --run-id, --confirm-run-id, "
        "--allow-write and optional --leaf-root; the in-module "
        "register_session_owner_dual_case_commands helper is the drop-in parser body"),
    'migration_inventory_py': (
        "migration_inventory.py: session_owner_slice flips status LOCAL_EXECUTOR_ONLY_BACKLOG -> "
        "DUAL_CASE_RECEIPT, execution_command 'dual-session-owner-run', "
        "required_parameters ['profile', 'binary', 'binary_sha256', 'cli_binary', "
        "'cli_binary_sha256', 'state_dir', 'run_id', 'confirm_run_id', 'allow_write'], "
        "source_map 'session-owner-permissions-migration-contract.json'"),
    'executor_migration_json': (
        "executor-migration.json: story_session_owner_permissions row flips mode LOCAL_ONLY -> DUAL with "
        "preprod_verdict NOT_RUN, empty gates, one registered owner-isolation note (r2 local live legs); counts fence "
        "LOCAL_ONLY/NOT_RUN 8 -> 6 and DUAL/NOT_RUN 3 -> 5 requires the matching "
        "_EXPECTED_EXECUTOR_MIGRATION_COUNTS update in inventory.py"),
}


def _env_reference(value: object, where: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[A-Z][A-Z0-9_]{2,100}', value) is None:
        raise ConfigError(where + ': expected a fixed uppercase environment reference')
    return value


def _policy(value: object) -> dict:
    policy = object_keys(value, {'fixture_pool', 'exclusive_use_confirmed',
                                 'timeout_seconds', 'max_response_bytes',
                                 'failure_retention_hours'}, 'session owner policy')
    identifier(policy['fixture_pool'], 'session owner policy.fixture_pool')
    if policy['exclusive_use_confirmed'] is not True:
        raise ConfigError('session owner policy requires exclusive use of the story-scoped runtime')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= 30:
        raise ConfigError('session owner policy.timeout_seconds is outside 1..30')
    if type(policy['max_response_bytes']) is not int or not 1 <= policy['max_response_bytes'] <= 65536:
        raise ConfigError('session owner policy.max_response_bytes is outside 1..65536')
    if type(policy['failure_retention_hours']) is not int or not 1 <= policy['failure_retention_hours'] <= 168:
        raise ConfigError('session owner policy.failure_retention_hours is outside 1..168')
    return policy


def validate_session_owner_profile(raw: object) -> dict:
    """Validate one tier-admitted session owner execution profile."""
    if not isinstance(raw, dict):
        raise ConfigError('session owner profile must be an object')
    profile = object_keys(raw, {'schema_version', 'contract', 'environment',
                                'entrypoint', 'human', 'auth', 'cli', 'policy'},
                          'session owner profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ConfigError('session owner profile: unsupported version')
    if profile['contract'] != PROFILE_CONTRACT:
        raise ConfigError('session owner profile: unsupported contract')
    environment = object_keys(profile['environment'], {'tier', 'id', 'expected_bcs_env'},
                              'session owner environment')
    tier = environment['tier']
    if tier not in ('local', 'preprod'):
        raise ConfigError('session owner environment.tier: only owned-local and preprod are admitted')
    identifier(environment['id'], 'session owner environment.id')
    if environment['expected_bcs_env'] != ('local' if tier == 'local' else 'pre'):
        raise ConfigError('session owner environment: expected_bcs_env binding mismatch')
    human = object_keys(profile['human'], {'user_id'}, 'session owner human')
    identifier(human['user_id'], 'session owner human.user_id')
    cli = object_keys(profile['cli'], {'transport', 'command_family', 'api_face'},
                      'session owner cli')
    if cli['command_family'] != 'session-owner':
        raise ConfigError('session owner binds the session-owner command family')
    if tier == 'local':
        entrypoint = object_keys(profile['entrypoint'], {'kind'}, 'session owner entrypoint')
        if entrypoint['kind'] != 'owned-local':
            raise ConfigError('session owner local entrypoint must be owned-local')
        auth = object_keys(profile['auth'], {'kind'}, 'session owner auth')
        if auth['kind'] != 'owned-local':
            raise ConfigError('session owner local auth must be owned-local')
        if cli['transport'] != 'owned-local-story' or cli['api_face'] != 'public-v1':
            raise ConfigError('session owner local cli must be owned-local-story on the public-v1 face')
    else:
        entrypoint = object_keys(
            profile['entrypoint'], {'kind', 'origin', 'api_face', 'manifest_face'},
            'session owner entrypoint')
        if entrypoint['kind'] != 'gateway':
            raise ConfigError('session owner preprod entrypoint must be gateway')
        if entrypoint['api_face'] != 'public-v1' or entrypoint['manifest_face'] != 'legacy':
            raise ConfigError('session owner preprod journey binds the public-v1 gateway face')
        parsed_origin(entrypoint['origin'], 'preprod')
        auth = object_keys(profile['auth'], {'kind', 'header_env'}, 'session owner auth')
        if auth['kind'] != 'preprod-human-cookie':
            raise ConfigError('session owner preprod auth must bind the Human Cookie')
        header_env = object_keys(auth['header_env'], {'Authorization', 'Cookie'},
                                 'session owner auth.header_env')
        if (_env_reference(header_env['Authorization'], 'auth.header_env.Authorization') != DRIVER_ENV
                or _env_reference(header_env['Cookie'], 'auth.header_env.Cookie') != COOKIE_ENV):
            raise ConfigError(
                'session owner preprod auth.header_env must reference BCS_E2E_DRIVER_TOKEN '
                'and BCS_E2E_HUMAN_COOKIE by name')
        if cli['transport'] != 'product-cli-http' or cli['api_face'] != 'public-v1':
            raise ConfigError('session owner preprod cli must be product-cli-http on public-v1')
    _policy(profile['policy'])
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)


def load_session_owner_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError('session owner profile must be a regular file')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError('session owner profile is unreadable JSON') from None
    return validate_session_owner_profile(raw)


def session_owner_dual_adaptability_ext() -> dict:
    """Describe the dual surface honestly; neither leg grants release credit."""
    family = session_owner_dual_adaptability()
    return {
        'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID,
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'family_adaptability': family,
        'dual_legs': {
            'owned_local': 'local-session-owner-run executor via dual-session-owner-run',
            'preprod': 'bounded wrong-method route probe plus named credential admission '
                       'plus offline RemoteSessionOwnerAdapter preflight gap receipt',
        },
        'biscuit_pairs': {
            'server': ['--binary', '--binary-sha256'],
            'author_cli': ['--cli-binary', '--cli-binary-sha256'],
        },
        'author_cli_pair_status': _AUTHOR_CLI_DISCLOSURE,
        'environments': ['owned-local-full-journal', 'preprod-route-probe-admission-only'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'preprod_executor_status': 'ROUTE_PROBE_ADMISSION_PREFLIGHT_ONLY_DOCUMENTED_GAPS',
        'required_assertion_count': STORY_CHECKS + CLEANUP_CHECKS,
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': (
            'The preprod public-v1 gateway face lacks the station bootstrap legs, '
            'per-request mock admission, a three-Human pilot credential family, the '
            'participants/collect/view-scoped routes and the operator principal signing '
            'key (four documented gaps); the owned-local leg ports all 60 run assertions '
            'plus 4 cleanup dispositions in process. Neither leg grants release credit.'),
    }


def dual_session_owner_case_command() -> dict:
    """Return the frozen dual command declaration for the registration seam."""
    return {
        'command': COMMAND,
        'case_keys': list(CASE_KEYS),
        'profile_contract': PROFILE_CONTRACT,
        'report_contract': REPORT_CONTRACT,
        'required_binary_pairs': {
            'server': ['--binary', '--binary-sha256'],
            'author_cli': ['--cli-binary', '--cli-binary-sha256'],
        },
        'tier_selection': 'profile environment.tier: local delegates to the frozen owned-local '
                          'executor; preprod runs the read-only route probe, the named credential '
                          'admission check and the offline adapter preflight, and always '
                          'fail-closes BLOCKED',
        'registration_seam': dict(REGISTRATION_SEAM),
        'author_cli_pair_status': _AUTHOR_CLI_DISCLOSURE,
        'grants_release_credit': False,
    }


def _validate_frozen_pair(binary: Path, binary_sha256: str, *, label: str) -> Path:
    """Freeze one biscuit pair: regular executable file, digest must match."""
    binary = Path(binary).absolute()
    if binary.is_symlink() or not binary.is_file():
        raise RunStateError(label + ' binary must be a regular frozen file')
    if not os.access(binary, os.X_OK):
        raise RunStateError(label + ' binary must be executable')
    if file_sha256(binary) != binary_sha256:
        raise RunStateError(label + ' binary digest differs from its frozen declaration')
    return binary


def _fresh_state_dir(path: Path) -> Path:
    requested = Path(path)
    if (not requested.is_absolute() or requested.is_symlink() or requested.exists()
            or not requested.parent.is_dir() or requested.parent.is_symlink()):
        raise ConfigError('session owner dual case requires a fresh private state directory')
    requested.mkdir(mode=0o700, parents=False)
    return requested


def _probe_path(path: str) -> str:
    value = (path.replace('{group}', _SENTINEL).replace('{session}', _SENTINEL)
               .replace('{actor}', _SENTINEL).replace('{bot_uuid}', _SENTINEL))
    if '{' in value or '}' in value:
        raise ConfigError('session owner probe route has an unresolved path parameter')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# One wrong-method request per reviewed preprod surface. The probe method always
# differs from the canonical method and is never a bound story write, so the
# probe cannot create, patch, delete or disclose a Group, Session, Human or Bot.
# 'present' rows calibrate the 404 oracle on the public-v1 prefix; 'absent' rows
# are the frozen documented-gap evidence (participants/collect and the bare
# station bootstrap shapes).
PROBE_ROWS = (
    {'operation': 'gateway.group_collection', 'canonical_method': 'GET', 'probe_method': 'PUT',
     'path': PUBLIC_PREFIX + '/groups', 'face': 'gateway-public-v1', 'expected_present': True,
     'expectation': 'Public-v1 Group collection is bound (list/create)'},
    {'operation': 'gateway.group_item_read', 'canonical_method': 'GET', 'probe_method': 'POST',
     'path': PUBLIC_PREFIX + '/groups/{group}', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Group item read is bound'},
    {'operation': 'gateway.group_patch', 'canonical_method': 'PATCH', 'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/groups/{group}', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Group visibility patch is bound'},
    {'operation': 'gateway.group_delete', 'canonical_method': 'DELETE', 'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/groups/{group}', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Group delete is bound'},
    {'operation': 'gateway.session_create', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/groups/{group}/sessions', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Session create under Group is bound'},
    {'operation': 'gateway.session_item', 'canonical_method': 'GET', 'probe_method': 'POST',
     'path': PUBLIC_PREFIX + '/sessions/{session}', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Session item read is bound'},
    {'operation': 'gateway.session_patch', 'canonical_method': 'PATCH', 'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/sessions/{session}', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Session title patch is bound'},
    {'operation': 'gateway.session_delete', 'canonical_method': 'DELETE', 'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/sessions/{session}', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Session delete is bound'},
    {'operation': 'gateway.session_messages', 'canonical_method': 'GET', 'probe_method': 'PUT',
     'path': PUBLIC_PREFIX + '/sessions/{session}/messages', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Session messages read is bound'},
    {'operation': 'gateway.session_token', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/sessions/{session}/token', 'face': 'gateway-public-v1',
     'expected_present': True, 'expectation': 'Public-v1 Session token mint is bound'},
    {'operation': 'gateway.register_token', 'canonical_method': 'GET', 'probe_method': 'POST',
     'path': PUBLIC_PREFIX + '/register/token', 'face': 'gateway-public-v1',
     'expected_present': True,
     'expectation': 'Public-v1 register-token is bound but cannot carry station X-Mock-User-Id bootstrap'},
    {'operation': 'gateway.register_bot', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/register', 'face': 'gateway-public-v1', 'expected_present': True,
     'expectation': 'Public-v1 register is bound but the story needs the station register shape'},
    {'operation': 'gateway.bot_onboard', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/bots/onboard', 'face': 'gateway-public-v1',
     'expected_present': True,
     'expectation': 'Public-v1 bot onboard is bound but not station-admissible'},
    {'operation': 'gateway.session_participants_add', 'canonical_method': 'POST',
     'probe_method': 'GET', 'path': PUBLIC_PREFIX + '/sessions/{session}/participants',
     'face': 'gateway-public-v1', 'expected_present': False,
     'expectation': 'Participants add is outside the reviewed public-v1 route bindings'},
    {'operation': 'gateway.session_participants_patch', 'canonical_method': 'PATCH',
     'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/sessions/{session}/participants/{actor}',
     'face': 'gateway-public-v1', 'expected_present': False,
     'expectation': 'Participants mode patch is outside the reviewed public-v1 route bindings'},
    {'operation': 'gateway.session_participants_remove', 'canonical_method': 'DELETE',
     'probe_method': 'GET',
     'path': PUBLIC_PREFIX + '/sessions/{session}/participants/{actor}',
     'face': 'gateway-public-v1', 'expected_present': False,
     'expectation': 'Participants revoke is outside the reviewed public-v1 route bindings'},
    {'operation': 'gateway.session_collect_add', 'canonical_method': 'POST',
     'probe_method': 'GET', 'path': PUBLIC_PREFIX + '/sessions/{session}/collect',
     'face': 'gateway-public-v1', 'expected_present': False,
     'expectation': 'Session collect add is outside the reviewed public-v1 route bindings'},
    {'operation': 'gateway.session_collect_remove', 'canonical_method': 'DELETE',
     'probe_method': 'GET', 'path': PUBLIC_PREFIX + '/sessions/{session}/collect',
     'face': 'gateway-public-v1', 'expected_present': False,
     'expectation': 'Session collect remove is outside the reviewed public-v1 route bindings'},
    {'operation': 'station.ensure_human', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/me/ensure-human', 'face': 'station-legacy-bare', 'expected_present': False,
     'expectation': 'Bare station ensure-human bootstrap is absent from the gateway origin'},
    {'operation': 'station.register_token', 'canonical_method': 'GET', 'probe_method': 'POST',
     'path': '/register/token', 'face': 'station-legacy-bare', 'expected_present': False,
     'expectation': 'Bare station register-token is absent from the gateway origin'},
    {'operation': 'station.register_bot', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/register', 'face': 'station-legacy-bare', 'expected_present': False,
     'expectation': 'Bare station register is absent from the gateway origin'},
    {'operation': 'station.bot_onboard', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/bots/onboard', 'face': 'station-legacy-bare', 'expected_present': False,
     'expectation': 'Bare station bot onboard is absent from the gateway origin'},
    {'operation': 'station.bot_delete', 'canonical_method': 'DELETE', 'probe_method': 'GET',
     'path': '/bots/{bot_uuid}', 'face': 'station-legacy-bare', 'expected_present': False,
     'expectation': 'Bare station bot delete is absent from the gateway origin'},
    {'operation': 'station.bot_visibility', 'canonical_method': 'PUT', 'probe_method': 'GET',
     'path': '/bots/{bot_uuid}/visibility', 'face': 'station-legacy-bare',
     'expected_present': False,
     'expectation': 'Bare station bot visibility write is absent from the gateway origin'},
)
PROBE_BY_OPERATION = {row['operation']: row for row in PROBE_ROWS}


class SessionOwnerProbeClient:
    """Bounded wrong-method probe client; it never stores a credential."""

    def __init__(self, profile: dict, credentials: dict):
        self.profile = profile
        self.base = origin(profile['entrypoint']['origin'], 'preprod').rstrip('/')
        header_env = profile['auth']['header_env']
        self.cookie_reference = header_env['Cookie']
        self.cookie = credentials.get(self.cookie_reference, '')
        self.max_response_bytes = profile['policy']['max_response_bytes']
        self.timeout_seconds = profile['policy']['timeout_seconds']
        self.opener = build_opener(
            ProxyHandler({}), HTTPSHandler(context=tls_context()), _NoRedirect())

    def request(self, *, method: str, path: str) -> dict:
        headers = {'Accept': 'application/json'}
        if self.cookie:
            headers['Cookie'] = self.cookie
        request = Request(self.base + path, method=method, headers=headers)
        record = {'method': method, 'path': path, 'http_status': None,
                  'response_bytes': 0, 'cookie_sent': bool(self.cookie),
                  'authorization_sent': False, 'transport_error': None}
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout_seconds)
            except HTTPError as error:
                response = error
            with response:
                record['http_status'] = response.code
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise RunStateError(
                        'session owner probe response exceeded its byte budget')
                record['response_bytes'] = len(raw)
        except (OSError, URLError, ValueError, RunStateError) as error:
            record['transport_error'] = type(error).__name__
        return record


def run_preprod_session_owner_route_probe(profile: dict, *, credentials: dict,
                                          client: object | None = None) -> dict:
    """Probe the reviewed preprod paths with wrong methods; no writes occur."""
    probe_client = client if client is not None else SessionOwnerProbeClient(profile, credentials)
    baseline = probe_client.request(method='GET', path=_BASELINE_PATH)
    if baseline['http_status'] != _ABSENT_STATUS:
        return {
            'status': 'BLOCKED', 'error_type': 'PREPROD_PROBE_BASELINE_UNEXPECTED',
            'baseline': baseline, 'checks': [], 'present_count': 0, 'absent_count': 0,
            'match_count': 0, 'required_count': len(PROBE_ROWS),
            'credentialed': bool(credentials), 'remote_validation': 'NOT_RUN',
            'grants_release_credit': False,
        }
    checks = []
    for row in PROBE_ROWS:
        record = probe_client.request(method=row['probe_method'],
                                      path=_probe_path(row['path']))
        status = record['http_status']
        present = status in _PRESENT_PROBE_STATUSES
        absent = status == _ABSENT_STATUS
        record.update({'operation': row['operation'], 'face': row['face'],
                       'canonical_method': row['canonical_method'],
                       'probe_method': row['probe_method'],
                       'expectation': row['expectation'],
                       'expected_present': row['expected_present'],
                       'route_present': present, 'route_absent': absent,
                       'expectation_matched': present == row['expected_present']})
        checks.append(record)
    present_count = sum(row['route_present'] for row in checks)
    absent_count = sum(row['route_absent'] for row in checks)
    matched = sum(row['expectation_matched'] for row in checks)
    passed = (matched == len(PROBE_ROWS)
              and all(row['http_status'] is not None for row in checks))
    return {
        'status': 'PASS' if passed else 'BLOCKED',
        'error_type': None if passed else 'PREPROD_SESSION_OWNER_ROUTE_UNEXPECTED',
        'baseline': baseline, 'checks': checks, 'present_count': present_count,
        'absent_count': absent_count, 'match_count': matched,
        'required_count': len(PROBE_ROWS), 'credentialed': bool(credentials),
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'note': ('Wrong-method read-only reconnaissance only; the Session owner journey '
                 'itself is NOT_RUN on preprod for the four documented gaps '
                 '(station bootstrap face, three-Human family, operator principal minting, '
                 'unbound participants/collect routes).'),
    }


def _camel_case_field(record: object, camel: str, snake: str) -> tuple[object, str]:
    """Read either spelling; logging/observation use only, never a decision input."""
    if not isinstance(record, dict):
        return None, 'absent'
    if camel in record:
        return record[camel], camel
    if snake in record:
        return record[snake], snake
    return None, 'absent'


def _serialization_sample() -> dict:
    value, spelling = _camel_case_field(
        {'requestId': 'probe-fingerprint', 'request_id': 'probe-fingerprint'},
        'requestId', 'request_id')
    return {'field': 'requestId', 'camel_precedence_value': value,
            'spelling_selected': spelling, 'decision_use': 'none_logging_only',
            'note': ('The frozen journey reads snake_case station and gateway envelopes; '
                     'product-CLI artifacts may serialize camelCase. The reader is retained '
                     'for dual-surface serde symmetry with the draft precedent and is never '
                     'a decision input for this family.')}


def _resolve_credential_values(header_env: dict) -> dict:
    """Resolve every named reference from the process environment only.

    Fail-closed admission aggregates ALL blocked references into the reason so
    the disclosure names every missing credential channel; only environment
    reference names and header names ever enter the reason, never values.
    """
    problems = []
    resolved = {}
    for header, reference in header_env.items():
        value = os.environ.get(reference, '')
        if (not value or not value.isascii()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or len(value) > _MAX_CREDENTIAL_BYTES):
            problems.append('environment reference ' + reference + ' for header ' + header
                            + ' is not loaded or fails its shape check')
            continue
        resolved[reference] = value
    if problems:
        raise PreprodClosedError(
            _CREDENTIALS_UNAVAILABLE,
            '; '.join(problems)
            + '; source the preprod credential environment first')
    return resolved


def _credential_admission(header_env: dict) -> dict:
    """Fail-closed admission record; names only, never values."""
    try:
        _resolve_credential_values(header_env)
    except PreprodClosedError as closed:
        return {'status': 'BLOCKED', 'reason_class': closed.reason_class,
                'reason': closed.detail, 'resolved_references': [],
                'source': 'process-environment-only'}
    return {'status': 'ADMITTED', 'reason_class': None, 'reason': '',
            'resolved_references': sorted(header_env.values()),
            'source': 'process-environment-only'}


def _steps_from_journal(journal: dict, error_class: str) -> tuple[list, int]:
    """Per-obligation steps from the frozen journal; first failure fences the rest."""
    rows = {row.get('id'): row.get('status') for row in journal.get('requirements', [])
            if isinstance(row, dict)}
    steps: list[StepOutcome] = []
    blocked = False
    for requirement_id in REQUIREMENTS:
        row_pass = rows.get(requirement_id) == 'PASS'
        if not blocked and row_pass:
            steps.append(StepOutcome(
                requirement_id=requirement_id, method=CASE_METHOD, status='PASS',
                data={'source_case_id': SOURCE_CASE_ID},
                evidence={'story_http_checks': journal.get('story_http_checks'),
                          'cleanup_http_checks': journal.get('cleanup_http_checks'),
                          'observed_assertion_count': journal.get('observed_assertion_count'),
                          'assertion_scope': 'OWNED_LOCAL_JOURNAL',
                          'source_case_id': SOURCE_CASE_ID,
                          'remote_validation': 'NOT_RUN',
                          'grants_release_credit': False,
                          'author_cli_pair': _AUTHOR_CLI_DISCLOSURE},
                error_type=None))
            continue
        if not blocked:
            blocked = True
            steps.append(StepOutcome(requirement_id=requirement_id, method=CASE_METHOD,
                                     status='NOT_RUN', data=None, evidence={},
                                     error_type=error_class))
            continue
        steps.append(StepOutcome(requirement_id=requirement_id, method=CASE_METHOD,
                                 status='NOT_RUN', data=None, evidence={}, error_type=None))
    return steps, sum(1 for step in steps if step.status == 'PASS')


def _session_local_execution(result: dict) -> CaseExecution:
    journal = result.get('journal', {}) if isinstance(result, dict) else {}
    rows = {row.get('id'): row.get('status') for row in journal.get('requirements', [])
            if isinstance(row, dict)}
    journal_gate = (
        journal.get('status') == LOCAL_STATUS
        and journal.get('story_http_checks') == STORY_CHECKS
        and journal.get('cleanup_http_checks') == CLEANUP_CHECKS
        and journal.get('observed_assertion_count') == STORY_CHECKS + CLEANUP_CHECKS
        and journal.get('story_markers') == {'observed': True, 'http_checks': STORY_CHECKS}
        and journal.get('resource_disposition') == RESOURCE_DISPOSITION_PASS
    )
    passed = (isinstance(result, dict) and result.get('status') == LOCAL_STATUS
              and journal_gate
              and result.get('canonical_summary', {}).get('status') == 'LOCAL_SUITE_PASS'
              and all(rows.get(requirement_id) == 'PASS' for requirement_id in REQUIREMENTS))
    if passed:
        steps = tuple(StepOutcome(
            requirement_id=requirement_id, method=CASE_METHOD, status='PASS',
            data={'source_case_id': SOURCE_CASE_ID},
            evidence={'story_http_checks': journal.get('story_http_checks'),
                      'cleanup_http_checks': journal.get('cleanup_http_checks'),
                      'observed_assertion_count': journal.get('observed_assertion_count'),
                      'assertion_scope': 'OWNED_LOCAL_JOURNAL',
                      'source_case_id': SOURCE_CASE_ID,
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False,
                      'author_cli_pair': _AUTHOR_CLI_DISCLOSURE},
            error_type=None)
            for requirement_id in REQUIREMENTS)
        return CaseExecution(
            case_id=CASE_ID, parent_id=PARENT, status='PASS', steps=steps,
            completed_count=len(REQUIREMENTS), total_count=len(REQUIREMENTS),
            error_type=None, ready_to_execute=True)
    error_class = (result.get('error_type') if isinstance(result, dict) else None) or _LOCAL_BLOCKED_ERROR
    steps, completed = _steps_from_journal(journal, error_class)
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status='BLOCKED', steps=tuple(steps),
        completed_count=completed, total_count=len(REQUIREMENTS),
        error_type=error_class, ready_to_execute=False)


def _preprod_execution(probe: dict, admission: dict, preflight: dict) -> CaseExecution:
    """Fail-closed whole-case execution: the preprod journey never executes."""
    probe_status = probe.get('status') if isinstance(probe, dict) else None
    gap_ids = list(DOCUMENTED_GAPS.keys())
    steps = []
    for index, requirement_id in enumerate(REQUIREMENTS):
        steps.append(StepOutcome(
            requirement_id=requirement_id, method=CASE_METHOD, status='NOT_RUN',
            data=None,
            evidence={'preprod_probe_status': probe_status,
                      'credential_admission_status': admission.get('status'),
                      'documented_gap': gap_ids[index % len(gap_ids)],
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False},
            error_type=_PREPROD_FULL_STORY_ERROR if index == 0 else None))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
        steps=tuple(steps), completed_count=0, total_count=len(REQUIREMENTS),
        error_type=_PREPROD_FULL_STORY_ERROR, ready_to_execute=False)


def _write_preprod_leaf(*, run_id: str, report: dict, leaf_root: Path | None) -> Path:
    root = Path(leaf_root) if leaf_root is not None else DEFAULT_LEAF_ROOT
    root = root / 'session-owner'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    slug = identifier(run_id, 'session owner run ID').replace('/', '_')
    write(root / (slug + '-stdout.json'), dict(report))
    verdict = {
        'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT,
        'case_id': CASE_ID, 'parent_id': PARENT, 'run_id': run_id,
        'status': report.get('status'),
        'error_type': (report.get('execution') or {}).get('error_type'),
        'reason': 'preprod Session owner journey is fail-closed by design; see blocker',
        'observed_status': None,
        'credential_admission': report.get('credential_admission', {}).get('status'),
        'credential_admission_reason_class': (
            report.get('credential_admission', {}).get('reason_class')),
        'preprod_probe_status': (report.get('preprod_probe') or {}).get('status'),
        'preflight_status': (report.get('remote_adapter_preflight') or {}).get('status'),
        'documented_gap_ids': list(DOCUMENTED_GAPS.keys()),
        'profile_digest': report.get('profile_digest'),
        'executor_digest': report.get('executor_digest'),
        'bcs_binary_sha256': report.get('bcs_binary_sha256'),
        'cli_binary_sha256': report.get('cli_binary_sha256'),
        'obligation_summary': report.get('obligation_summary', {}),
        'environment': report.get('environment'),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }
    write(root / (slug + '-verdict.json'), verdict)
    return root


def dual_session_owner_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                                allow_write: bool, confirm_run_id: str,
                                binary: Path, binary_sha256: str,
                                cli_binary: Path, cli_binary_sha256: str,
                                leaf_root: Path | None = None) -> dict:
    """Run the frozen Session owner journey through the admitted tier."""
    run_id = identifier(run_id, 'session owner run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError('session owner dual case requires explicit write permission and exact Run confirmation')
    profile = load_session_owner_profile(Path(profile_path))
    require_resolved(profile)
    tier = profile['environment']['tier']
    # Both frozen runtime roles enter through biscuit pairs; the in-process
    # family journey never invokes the product CLI, so the author pair is
    # admitted, digest fenced and disclosed but never consumed.
    binary = validate_session_owner_binary(Path(binary), binary_sha256)
    cli_binary = _validate_frozen_pair(Path(cli_binary), cli_binary_sha256, label='CLI')
    digest = fingerprint(profile)
    artifact = executor_digest()
    source_binding = session_owner_source_binding()
    state_dir = _fresh_state_dir(Path(state_dir))

    profile_file = state_dir / 'session-owner-dual-profile.json'
    binding_file = state_dir / 'session-owner-dual-binding.json'
    report_file = state_dir / 'session-owner-dual-run.json'
    write(profile_file, profile)
    binding = {
        'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT,
        'suite': SUITE, 'source_suite': SOURCE_SUITE, 'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID, 'parent_id': PARENT, 'run_id': run_id,
        'profile_digest': digest, 'executor_digest': artifact,
        'source_binding_digest': fingerprint(source_binding),
        'requirement_ids': list(REQUIREMENTS), 'story_checks': STORY_CHECKS,
        'cleanup_checks': CLEANUP_CHECKS,
        'bcs_binary_sha256': binary_sha256, 'cli_binary_sha256': cli_binary_sha256,
        'author_cli_pair_status': _AUTHOR_CLI_DISCLOSURE,
        'owned_local_executor': ('bcs_e2e.session_owner.local_session_owner_run.'
                                 'local_session_owner_run'),
        'local_backlog_commands': ['local-session-owner-run', 'local-session-owner-report'],
        'state_dir': str(state_dir),
    }
    write(binding_file, binding)

    local_result = None
    journal_value: dict = {}
    local_failure: dict | None = None
    probe: dict | None = None
    admission: dict | None = None
    preflight: dict | None = None
    blocker: dict | None = None
    try:
        if tier == 'local':
            try:
                local_result = local_session_owner_run(
                    binary=binary, binary_sha256=binary_sha256,
                    state_dir=state_dir / 'owned-session-owner', run_id=run_id,
                    confirm_run_id=run_id, allow_local_processes=True,
                    allow_fixture_write=True, allow_write=True)
                journal_value = local_result.get('journal', {}) or {}
            except SessionOwnerRunError as error:
                # The frozen executor raises after persisting its journal; the
                # retained per-obligation rows stay the honest step mapping.
                local_failure = {'error_type': type(error).__name__,
                                 'error_detail': str(error)[:800]}
                journal_path = state_dir / 'owned-session-owner' / JOURNAL_NAME
                if journal_path.is_file() and not journal_path.is_symlink():
                    try:
                        journal_value = json.loads(journal_path.read_text(encoding='utf-8'))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        journal_value = {}
                local_result = {'status': 'BLOCKED', 'journal': journal_value,
                                'canonical_summary': {},
                                'error_type': type(error).__name__}
            execution = _session_local_execution(local_result or {})
        else:
            admission = _credential_admission(profile['auth']['header_env'])
            try:
                resolved = _resolve_credential_values(profile['auth']['header_env'])
            except PreprodClosedError:
                resolved = {}
            probe = run_preprod_session_owner_route_probe(profile, credentials=resolved)
            try:
                preflight = RemoteSessionOwnerAdapter(profile).preflight()
            except (RunStateError, TypeError, ValueError) as error:
                preflight = {'status': 'BLOCKED', 'error_type': type(error).__name__,
                             'reason': str(error)[:512], 'remote_validation': 'NOT_RUN',
                             'grants_release_credit': False}
            execution = _preprod_execution(probe, admission, preflight)
            blocker = {
                'reason_class': _PREPROD_FULL_STORY_ERROR,
                'observed_status': None,
                'reason': ('The preprod leg is a read-only wrong-method route probe, a named '
                           'credential admission check and the offline adapter preflight; the '
                           'frozen 60-assertion journey cannot execute on the preprod '
                           'public-v1 gateway face for the four documented gaps'),
                'credential_admission': admission,
                'credential_refs': sorted(profile['auth']['header_env'].values()),
                'route_probe_status': probe.get('status'),
                'documented_gaps': dict(DOCUMENTED_GAPS),
            }
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(f'session owner dual case execution is unavailable: {error}') from None

    status = 'PASS' if execution.status == 'PASS' else 'BLOCKED'
    report = {
        'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT,
        'case_id': execution.case_id, 'source_case_id': SOURCE_CASE_ID,
        'parent_id': PARENT, 'run_id': run_id, 'status': status,
        'profile_digest': digest, 'executor_digest': artifact,
        'bcs_binary_sha256': binary_sha256, 'cli_binary_sha256': cli_binary_sha256,
        'source_binding_digest': binding['source_binding_digest'],
        'state_dir': str(state_dir),
        'environment': {'tier': tier, 'id': profile['environment']['id'],
                        'expected_bcs_env': profile['environment']['expected_bcs_env'],
                        'entrypoint_kind': profile['entrypoint']['kind']},
        'obligation_summary': {
            'total': len(REQUIREMENTS), 'requirement_ids': list(REQUIREMENTS),
            'story_checks': STORY_CHECKS, 'cleanup_checks': CLEANUP_CHECKS,
            'source_case_id': SOURCE_CASE_ID,
            'shell_step_map_ref': 'session-owner-permissions-migration-contract.json'},
        'case': execution.snapshot(),
        'execution': {'status': execution.status,
                      'completed_count': execution.completed_count,
                      'total_count': execution.total_count,
                      'error_type': execution.error_type},
        'resource_disposition': (journal_value.get('resource_disposition')
                                 if isinstance(journal_value, dict) else None),
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'credential_kind': ('owned_local_ephemeral_gateway_principals' if tier == 'local'
                            else 'preprod_human_cookie_route_probe'),
        'author_cli_pair': {
            'status': _AUTHOR_CLI_DISCLOSURE,
            'reason': ('The Session owner journey is an in-process HTTP authorization '
                       'matrix against one owned runtime; it never invokes the product CLI. '
                       'The author CLI biscuit pair is digest fenced on entry for the dual '
                       'surface contract and disclosed, not consumed.')},
        'family_adaptability': session_owner_dual_adaptability(),
        'dual_surface': session_owner_dual_adaptability_ext(),
        'serialization_sample': _serialization_sample(),
        'documented_gaps': dict(DOCUMENTED_GAPS),
    }
    if tier == 'local':
        report['local_owned_run'] = {
            'status': (local_result or {}).get('status')
                      or (local_failure or {}).get('error_type') or 'BLOCKED',
            'journal': {'status': journal_value.get('status'),
                        'story_http_checks': journal_value.get('story_http_checks'),
                        'cleanup_http_checks': journal_value.get('cleanup_http_checks'),
                        'observed_assertion_count': journal_value.get(
                            'observed_assertion_count'),
                        'requirement_ids': [row.get('id') for row in
                                            journal_value.get('requirements', [])],
                        'requirement_statuses': [row.get('status') for row in
                                                 journal_value.get('requirements', [])]},
            'canonical_summary_status': (local_result or {}).get('canonical_summary', {}).get(
                'status'),
            'state_dir': str(state_dir / 'owned-session-owner'),
        }
        if local_failure is not None:
            report['session_local_failure'] = local_failure
    else:
        report['preprod_probe'] = probe
        report['credential_admission'] = admission
        report['preprod_blocker'] = blocker
        report['remote_adapter_preflight'] = preflight
        report['environment']['api_face'] = 'public-v1'
        leaf = _write_preprod_leaf(run_id=run_id, report=report, leaf_root=leaf_root)
        report['preprod_evidence_leaf'] = str(leaf)
    write(report_file, report)
    return report


def register_session_owner_dual_case_commands(commands) -> None:
    """Drop-in parser body for the frozen registration seam (describe-only)."""
    case = commands.add_parser(
        COMMAND,
        help='Run the frozen Session owner permissions story through owned-local or the bounded preprod route probe')
    case.add_argument('--profile', type=Path, required=True)
    case.add_argument('--binary', type=Path, required=True)
    case.add_argument('--binary-sha256', required=True)
    case.add_argument('--cli-binary', type=Path, required=True)
    case.add_argument('--cli-binary-sha256', required=True)
    case.add_argument('--state-dir', type=Path, required=True)
    case.add_argument('--run-id', required=True)
    case.add_argument('--confirm-run-id', required=True)
    case.add_argument('--allow-write', action='store_true')
    case.add_argument('--leaf-root', type=Path)
