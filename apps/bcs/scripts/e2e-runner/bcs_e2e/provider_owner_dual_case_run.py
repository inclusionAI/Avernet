"""Dual owned-local/preprod execution surface for Provider owner isolation.

Migrates the frozen ``story_provider_owner_isolation`` slice (24 obligations,
45 live HTTP+WS checks, upstream shell parsed to one minimal story) from the
owned-local-only backlog surface to a dual-environment case run. The owned-local
leg delegates unchanged to the frozen backlog executor
:func:`bcs_e2e.local_provider_owner_run.local_provider_owner_run`. The preprod
leg is deliberately a bounded wrong-method route-surface probe plus a named
credential-admission check: the reviewed preprod gateway exposes no Provider
admin face and no ``provider_admin_token`` channel, so the journey can never
execute there honestly. That fail-closed disclosure is the machine-readable
BLOCKED leaf under ``~/.codex/bcs-e2e/20261031-migration-wp2-owner-isolation-preprod``.

Both frozen runtime roles enter through biscuit pairs: the server pair
(``--binary``/``--binary-sha256``) is consumed by the owned-local leg, and the
author CLI pair (``--cli-binary``/``--cli-binary-sha256``) is admitted, digest
fenced and disclosed but never consumed by this HTTP/WebSocket family journey.
No leg stores a credential; preprod credentials resolve only from named
process-environment references at run time.
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

from .case_executor import CaseExecution, StepOutcome
from .inventory import executor_digest
from .local_provider_owner_run import (
    MARKER_KEYS,
    local_provider_owner_run,
    validate_provider_owner_binary,
)
from .owned_bcs import file_sha256
from .profile import (
    ConfigError,
    fingerprint,
    identifier,
    object_keys,
    origin,
    parsed_origin,
    require_resolved,
    unique_keys,
)
from .provider_owner_contract import (
    CASE_ID as SOURCE_CASE_ID,
    DOCUMENTED_GAPS,
    PARENT,
    REQUIREMENTS,
    STATUS as LOCAL_STATUS,
    STORY_CHECKS,
    SUITE as SOURCE_SUITE,
    provider_owner_adaptability,
    provider_owner_source_binding,
)
from .run_state import RunStateError, write
from .transport import tls_context

SCHEMA_VERSION = 1
PROFILE_CONTRACT = 'dual-provider-owner-profile-v1'
REPORT_CONTRACT = 'dual-provider-owner-case-run-v1'
CASE_ID = 'DUAL-PROVIDER-OWNER-01'
SUITE = 'dual-provider-owner-v1'
CASE_KEYS = ('provider-owner',)
COMMAND = 'dual-provider-owner-run'
CASE_METHOD = 'execute_provider_owner_obligation'
LEAF_ROOT_NAME = '20261031-migration-wp2-owner-isolation-preprod'
DEFAULT_LEAF_ROOT = Path.home() / '.codex' / 'bcs-e2e' / LEAF_ROOT_NAME
COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
HEADER_ENV = {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}
_MAX_CREDENTIAL_BYTES = 8192
_LOCAL_BLOCKED_ERROR = 'LOCAL_PROVIDER_OWNER_BLOCKED'
_PREPROD_FULL_STORY_ERROR = 'PREPROD_PROVIDER_OWNER_FULL_STORY_NOT_EXECUTED'
_CREDENTIALS_UNAVAILABLE = 'PREPROD_PROVIDER_OWNER_CREDENTIALS_UNAVAILABLE'
_TRANSPORT_ERROR = 'PREPROD_PROVIDER_OWNER_ROUTE_PROBE_TRANSPORT'
_AUTHOR_CLI_DISCLOSURE = 'author_cli_pair_admitted_frozen_not_consumed_by_family_journey'
_FIFO_SENTINEL = '__bcs_e2e_provider_owner_probe__'
_BASELINE_PATH = '/__bcs_e2e_absent_provider_owner_probe__'
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
        "cli.py: import block: from .provider_owner_dual_case_run import (CASE_KEYS as "
        "PROVIDER_OWNER_CASE_KEYS, COMMAND as PROVIDER_OWNER_COMMAND, "
        "dual_provider_owner_case_run); dispatch block mirroring the "
        "dual-draft-delivery-run shape: report = dual_provider_owner_case_run("
        "profile_path=args.profile, state_dir=args.state_dir, run_id=args.run_id, "
        "allow_write=args.allow_write, confirm_run_id=args.confirm_run_id, binary=args.binary, "
        "binary_sha256=args.binary_sha256, cli_binary=args.cli_binary, "
        "cli_binary_sha256=args.cli_binary_sha256, leaf_root=getattr(args, 'leaf_root', None)); "
        "rc = 0 if report['status'] == 'PASS' else 2"),
    'case_command_registration_py': (
        "case_command_registration.py: DUAL_CASE_COMMANDS frozenset gains 'dual-provider-owner-run'; parser block "
        "mirrors the dual-draft-delivery-run block with --profile/--state-dir/--run-id/"
        "--confirm-run-id/--allow-write plus BOTH required biscuit pairs "
        "--binary/--binary-sha256 and --cli-binary/--cli-binary-sha256 and optional "
        "--leaf-root; the in-module register_provider_owner_dual_case_commands helper "
        "is the drop-in parser body"),
    'migration_inventory_py': (
        "migration_inventory.py: provider_owner_slice flips status LOCAL_EXECUTOR_ONLY_BACKLOG -> "
        "DUAL_CASE_RECEIPT, execution_command 'dual-provider-owner-run', "
        "required_parameters ['profile', 'binary', 'binary_sha256', 'cli_binary', "
        "'cli_binary_sha256', 'state_dir', 'run_id', 'confirm_run_id', 'allow_write'], "
        "source_map 'provider-owner-isolation-migration-contract.json'"),
    'executor_migration_json': (
        "executor-migration.json: story_provider_owner_isolation row flips mode LOCAL_ONLY -> DUAL with "
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
                                 'failure_retention_hours'}, 'provider owner policy')
    identifier(policy['fixture_pool'], 'provider owner policy.fixture_pool')
    if policy['exclusive_use_confirmed'] is not True:
        raise ConfigError('provider owner policy requires exclusive use of the story-scoped runtime')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= 30:
        raise ConfigError('provider owner policy.timeout_seconds is outside 1..30')
    if type(policy['max_response_bytes']) is not int or not 1 <= policy['max_response_bytes'] <= 65536:
        raise ConfigError('provider owner policy.max_response_bytes is outside 1..65536')
    if type(policy['failure_retention_hours']) is not int or not 1 <= policy['failure_retention_hours'] <= 168:
        raise ConfigError('provider owner policy.failure_retention_hours is outside 1..168')
    return policy


def validate_provider_owner_profile(raw: object) -> dict:
    """Validate one tier-admitted provider owner execution profile."""
    if not isinstance(raw, dict):
        raise ConfigError('provider owner profile must be an object')
    profile = object_keys(raw, {'schema_version', 'contract', 'environment',
                                'entrypoint', 'human', 'auth', 'cli', 'policy'},
                          'provider owner profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 1:
        raise ConfigError('provider owner profile: unsupported version')
    if profile['contract'] != PROFILE_CONTRACT:
        raise ConfigError('provider owner profile: unsupported contract')
    environment = object_keys(profile['environment'], {'tier', 'id', 'expected_bcs_env'},
                              'provider owner environment')
    tier = environment['tier']
    if tier not in ('local', 'preprod'):
        raise ConfigError('provider owner environment.tier: only owned-local and preprod are admitted')
    identifier(environment['id'], 'provider owner environment.id')
    if environment['expected_bcs_env'] != ('local' if tier == 'local' else 'pre'):
        raise ConfigError('provider owner environment: expected_bcs_env binding mismatch')
    human = object_keys(profile['human'], {'user_id'}, 'provider owner human')
    identifier(human['user_id'], 'provider owner human.user_id')
    cli = object_keys(profile['cli'], {'transport', 'command_family', 'api_face'},
                      'provider owner cli')
    if (cli['command_family'] != 'provider-owner'
            or cli['api_face'] != 'legacy'):
        raise ConfigError('provider owner binds the station legacy Provider family')
    if tier == 'local':
        entrypoint = object_keys(profile['entrypoint'], {'kind'}, 'provider owner entrypoint')
        if entrypoint['kind'] != 'owned-local':
            raise ConfigError('provider owner local entrypoint must be owned-local')
        auth = object_keys(profile['auth'], {'kind'}, 'provider owner auth')
        if auth['kind'] != 'owned-local':
            raise ConfigError('provider owner local auth must be owned-local')
        if cli['transport'] != 'owned-local-story':
            raise ConfigError('provider owner local cli.transport must be owned-local-story')
    else:
        entrypoint = object_keys(
            profile['entrypoint'], {'kind', 'origin', 'api_face', 'manifest_face'},
            'provider owner entrypoint')
        if entrypoint['kind'] != 'bcs-direct':
            raise ConfigError('provider owner preprod entrypoint must be bcs-direct')
        if entrypoint['api_face'] != 'legacy' or entrypoint['manifest_face'] != 'legacy':
            raise ConfigError('provider owner preprod journey binds the station legacy face')
        parsed_origin(entrypoint['origin'], 'preprod')
        auth = object_keys(profile['auth'], {'kind', 'header_env'}, 'provider owner auth')
        if auth['kind'] != 'preprod-human-cookie':
            raise ConfigError('provider owner preprod auth must bind the Human Cookie')
        header_env = object_keys(auth['header_env'], {'Authorization', 'Cookie'},
                                 'provider owner auth.header_env')
        if (_env_reference(header_env['Authorization'], 'auth.header_env.Authorization') != DRIVER_ENV
                or _env_reference(header_env['Cookie'], 'auth.header_env.Cookie') != COOKIE_ENV):
            raise ConfigError(
                'provider owner preprod auth.header_env must reference BCS_E2E_DRIVER_TOKEN '
                'and BCS_E2E_HUMAN_COOKIE by name')
        if cli['transport'] != 'product-cli-http':
            raise ConfigError('provider owner preprod cli.transport must be product-cli-http')
    _policy(profile['policy'])
    return json.loads(json.dumps(profile, allow_nan=False), object_pairs_hook=unique_keys)


def load_provider_owner_profile(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ConfigError('provider owner profile must be a regular file')
    try:
        raw = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError('provider owner profile is unreadable JSON') from None
    return validate_provider_owner_profile(raw)


def provider_owner_dual_adaptability() -> dict:
    """Describe the dual surface honestly; neither leg grants release credit."""
    family = provider_owner_adaptability()
    return {
        'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID,
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'family_adaptability': family,
        'dual_legs': {
            'owned_local': 'local-provider-owner-run executor via dual-provider-owner-run',
            'preprod': 'bounded wrong-method route-surface probe plus named credential admission',
        },
        'biscuit_pairs': {
            'server': ['--binary', '--binary-sha256'],
            'author_cli': ['--cli-binary', '--cli-binary-sha256'],
        },
        'author_cli_pair_status': _AUTHOR_CLI_DISCLOSURE,
        'environments': ['owned-local-full-story', 'preprod-route-probe-admission-only'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'preprod_executor_status': 'ROUTE_PROBE_AND_ADMISSION_ONLY_AWAITING_PROVIDER_ADMIN_FACE',
        'required_assertion_count': STORY_CHECKS,
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': (
            'The preprod public-v1 gateway exposes no Provider registration/admin face and '
            'no provider_admin_token credential channel (PREPROD_PROVIDER_ADMIN_FACE_ABSENT); '
            'the owned-local live replay blocks at story request 8 because provider-registered '
            'Bots receive no bcs-edge-permission owner edges '
            '(OWNED_LOCAL_PROVIDER_BOT_OWNER_EDGE_DRIFT). Dual execution is a migration '
            'receipt, not release credit.'),
    }


def dual_provider_owner_case_command() -> dict:
    """Return the frozen dual command declaration for registration接缝."""
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
                          'executor; preprod runs the read-only route probe and the named '
                          'credential admission check and always fail-closes BLOCKED',
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
        raise ConfigError('provider owner dual case requires a fresh private state directory')
    requested.mkdir(mode=0o700, parents=False)
    return requested


def _probe_path(path: str) -> str:
    value = path.replace('{provider_id}', _FIFO_SENTINEL)
    value = value.replace('{provider_bot_ref}', _FIFO_SENTINEL)
    value = value.replace('{bot_uuid}', _FIFO_SENTINEL)
    value = value.replace('{group}', _FIFO_SENTINEL)
    value = value.replace('{invite}', _FIFO_SENTINEL)
    value = value.replace('{session}', _FIFO_SENTINEL)
    if '{' in value or '}' in value:
        raise ConfigError('provider owner probe route has an unresolved path parameter')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# One wrong-method request per reviewed station-legacy surface of the frozen
# provider journey, plus two public-v1 calibration rows proving the 404 oracle.
# The probe method always differs from the canonical method and is never a
# story write on the reviewed binding set, so the probe cannot create, patch,
# delete or disclose a Provider, Bot, Group or Session.
PROBE_ROWS = (
    {'operation': 'provider.register', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/providers', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Provider registration route is absent from the preprod gateway face'},
    {'operation': 'provider.read', 'canonical_method': 'GET', 'probe_method': 'POST',
     'path': '/providers/{provider_id}', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Provider metadata read route is absent from the preprod gateway face'},
    {'operation': 'provider.same_owner_patch', 'canonical_method': 'PATCH', 'probe_method': 'GET',
     'path': '/providers/{provider_id}', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Provider collaboration-scope patch route is absent on the gateway face'},
    {'operation': 'provider.bot.create', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/providers/{provider_id}/bots', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Provider Bot mint route is absent from the preprod gateway face'},
    {'operation': 'provider.bot.delete', 'canonical_method': 'DELETE', 'probe_method': 'GET',
     'path': '/providers/{provider_id}/bots/{provider_bot_ref}', 'face': 'station-legacy',
     'expected_present': False,
     'expectation': 'Provider Bot delete route is absent from the preprod gateway face'},
    {'operation': 'bot.visibility', 'canonical_method': 'PUT', 'probe_method': 'GET',
     'path': '/bots/{bot_uuid}/visibility', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bot visibility write route is absent from the preprod gateway face'},
    {'operation': 'group.create', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/groups', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bare legacy Group create route is absent from the public-v1 gateway'},
    {'operation': 'group.patch', 'canonical_method': 'PATCH', 'probe_method': 'GET',
     'path': '/groups/{group}', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bare legacy Group visibility patch route is absent on the gateway face'},
    {'operation': 'group.member.add', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/groups/{group}/members', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bare legacy Group member-add route is absent on the gateway face'},
    {'operation': 'group.member.remove', 'canonical_method': 'DELETE', 'probe_method': 'GET',
     'path': '/groups/{group}/members/{bot_uuid}', 'face': 'station-legacy',
     'expected_present': False,
     'expectation': 'Bare legacy Group member-remove route is absent on the gateway face'},
    {'operation': 'group.invite_link', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/groups/{group}/invite-link', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bare legacy invite-link mint route is absent on the gateway face'},
    {'operation': 'group.join', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/groups/join/{invite}', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bare legacy invite join route is absent on the gateway face'},
    {'operation': 'session.create', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/groups/{group}/sessions', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bare legacy Session create route is absent from the public-v1 gateway'},
    {'operation': 'session.messages', 'canonical_method': 'GET', 'probe_method': 'POST',
     'path': '/sessions/{session}/messages', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bare legacy Session messages read route is absent on the gateway face'},
    {'operation': 'session.chat', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/sessions/{session}/chat', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Bare legacy Session chat write route is absent on the gateway face'},
    {'operation': 'station.ensure_human', 'canonical_method': 'POST', 'probe_method': 'GET',
     'path': '/me/ensure-human', 'face': 'station-legacy', 'expected_present': False,
     'expectation': 'Station ensure-human bootstrap route is absent on the gateway face'},
    {'operation': 'gateway.public_v1.group_collection', 'canonical_method': 'GET',
     'probe_method': 'PUT',
     'path': '/openapi/v1/collaboration/groups', 'face': 'gateway-public-v1',
     'expected_present': True,
     'expectation': 'Public-v1 Group collection is the calibration row proving the 404 oracle'},
    {'operation': 'gateway.public_v1.group_item', 'canonical_method': 'GET',
     'probe_method': 'POST', 'path': '/openapi/v1/collaboration/groups/{group}',
     'face': 'gateway-public-v1', 'expected_present': True,
     'expectation': 'Public-v1 Group item read is the calibration row for present routing'},
)
PROBE_BY_OPERATION = {row['operation']: row for row in PROBE_ROWS}


class ProviderOwnerProbeClient:
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
                        'provider owner probe response exceeded its byte budget')
                record['response_bytes'] = len(raw)
        except (OSError, URLError, ValueError, RunStateError) as error:
            record['transport_error'] = type(error).__name__
        return record


def run_preprod_provider_owner_route_probe(profile: dict, *, credentials: dict,
                                           client: object | None = None) -> dict:
    """Probe the reviewed preprod paths with wrong methods; no writes occur."""
    probe_client = client if client is not None else ProviderOwnerProbeClient(profile, credentials)
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
        'error_type': None if passed else 'PREPROD_PROVIDER_OWNER_ROUTE_UNEXPECTED',
        'baseline': baseline, 'checks': checks, 'present_count': present_count,
        'absent_count': absent_count, 'match_count': matched,
        'required_count': len(PROBE_ROWS), 'credentialed': bool(credentials),
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'note': ('Wrong-method read-only reconnaissance only; the Provider owner journey '
                 'itself is NOT_RUN on preprod because the Provider admin face is absent '
                 '(PREPROD_PROVIDER_ADMIN_FACE_ABSENT).'),
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
            'spelling_selected': spelling,
            'decision_use': 'none_logging_only',
            'note': ('Gateway public-v1 error envelopes may spell request identifiers '
                     'camelCase while station legacy envelopes spell snake_case; the '
                     'reader is logging-only and no case decision reads it.')}


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


def _local_execution(result: dict) -> CaseExecution:
    observed = result.get('child_receipt', {}).get('observed_checks', 0)
    marker_ok = result.get('child_receipt', {}).get('markers', {}).get(MARKER_KEYS[0]) is True
    canonical = result.get('canonical_summary', {}).get('status')
    passed = (result.get('status') == LOCAL_STATUS and marker_ok
              and observed == STORY_CHECKS and canonical == 'LOCAL_SUITE_PASS')
    # The case-level receipt fences any child failure under the family BLOCKED
    # class; the raw child error_type stays visible on local_owned_run instead
    # of leaking an executor-internal class into the dual case snapshot.
    error_type = None if passed else _LOCAL_BLOCKED_ERROR
    steps = []
    for index, requirement_id in enumerate(REQUIREMENTS):
        steps.append(StepOutcome(
            requirement_id=requirement_id,
            method=CASE_METHOD,
            status='PASS' if passed else 'NOT_RUN',
            data={'source_case_id': SOURCE_CASE_ID} if passed else None,
            evidence={'story_checks': observed,
                      'marker_observed': marker_ok,
                      'assertion_scope': 'FROZEN_CASE_LEVEL_CHILD',
                      'source_case_id': SOURCE_CASE_ID,
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False,
                      'author_cli_pair': _AUTHOR_CLI_DISCLOSURE} if passed else {},
            error_type=None if passed else (error_type if index == 0 else None)))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT,
        status='PASS' if passed else 'BLOCKED',
        steps=tuple(steps), completed_count=len(REQUIREMENTS) if passed else 0,
        total_count=len(REQUIREMENTS), error_type=None if passed else error_type,
        ready_to_execute=passed)


def _preprod_execution(probe: dict, admission: dict) -> CaseExecution:
    """Fail-closed whole-case execution: the preprod journey never executes."""
    probe_status = probe.get('status') if isinstance(probe, dict) else None
    steps = []
    for index, requirement_id in enumerate(REQUIREMENTS):
        steps.append(StepOutcome(
            requirement_id=requirement_id, method=CASE_METHOD, status='NOT_RUN',
            data=None,
            evidence={'preprod_probe_status': probe_status,
                      'credential_admission_status': admission.get('status'),
                      'documented_gap': DOCUMENTED_GAPS[index % len(DOCUMENTED_GAPS)],
                      'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False},
            error_type=_PREPROD_FULL_STORY_ERROR if index == 0 else None))
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
        steps=tuple(steps), completed_count=0, total_count=len(REQUIREMENTS),
        error_type=_PREPROD_FULL_STORY_ERROR, ready_to_execute=False)


def _blocked_case_execution(error_class: str) -> CaseExecution:
    steps = [StepOutcome(requirement_id=REQUIREMENTS[0], method=CASE_METHOD,
                         status='NOT_RUN', data=None, evidence={}, error_type=error_class)]
    steps += [StepOutcome(requirement_id=requirement_id, method=CASE_METHOD,
                          status='NOT_RUN', data=None, evidence={}, error_type=None)
              for requirement_id in REQUIREMENTS[1:]]
    return CaseExecution(
        case_id=CASE_ID, parent_id=PARENT, status='BLOCKED',
        steps=tuple(steps), completed_count=0, total_count=len(REQUIREMENTS),
        error_type=error_class, ready_to_execute=False)


def _write_preprod_leaf(*, run_id: str, report: dict, leaf_root: Path | None) -> Path:
    root = Path(leaf_root) if leaf_root is not None else DEFAULT_LEAF_ROOT
    root = root / 'provider-owner'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    slug = identifier(run_id, 'provider owner run ID').replace('/', '_')
    write(root / (slug + '-stdout.json'), dict(report))
    verdict = {
        'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT,
        'case_id': CASE_ID, 'parent_id': PARENT, 'run_id': run_id,
        'status': report.get('status'),
        'error_type': (report.get('execution') or {}).get('error_type'),
        'reason': 'preprod Provider owner journey is fail-closed by design; see blocker',
        'observed_status': None,
        'credential_admission': report.get('credential_admission', {}).get('status'),
        'credential_admission_reason_class': (
            report.get('credential_admission', {}).get('reason_class')),
        'preprod_probe_status': (report.get('preprod_probe') or {}).get('status'),
        'profile_digest': report.get('profile_digest'),
        'executor_digest': report.get('executor_digest'),
        'bcs_binary_sha256': report.get('bcs_binary_sha256'),
        'cli_binary_sha256': report.get('cli_binary_sha256'),
        'documented_gaps': list(DOCUMENTED_GAPS),
        'obligation_summary': report.get('obligation_summary', {}),
        'environment': report.get('environment'),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }
    write(root / (slug + '-verdict.json'), verdict)
    return root


def dual_provider_owner_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                                 allow_write: bool, confirm_run_id: str,
                                 binary: Path, binary_sha256: str,
                                 cli_binary: Path, cli_binary_sha256: str,
                                 leaf_root: Path | None = None) -> dict:
    """Run the frozen Provider owner journey through the admitted tier."""
    run_id = identifier(run_id, 'provider owner run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError('provider owner dual case requires explicit write permission and exact Run confirmation')
    profile = load_provider_owner_profile(Path(profile_path))
    require_resolved(profile)
    tier = profile['environment']['tier']
    # Both frozen runtime roles enter through biscuit pairs; the family
    # journey never invokes the product CLI, so the author pair is admitted,
    # digest fenced and disclosed but never consumed.
    binary = validate_provider_owner_binary(Path(binary), binary_sha256)
    cli_binary = _validate_frozen_pair(Path(cli_binary), cli_binary_sha256, label='CLI')
    digest = fingerprint(profile)
    artifact = executor_digest()
    source_binding = provider_owner_source_binding()
    state_dir = _fresh_state_dir(Path(state_dir))

    profile_file = state_dir / 'provider-owner-dual-profile.json'
    binding_file = state_dir / 'provider-owner-dual-binding.json'
    report_file = state_dir / 'provider-owner-dual-run.json'
    write(profile_file, profile)
    binding = {
        'schema_version': SCHEMA_VERSION, 'contract': REPORT_CONTRACT,
        'suite': SUITE, 'source_suite': SOURCE_SUITE, 'case_id': CASE_ID,
        'source_case_id': SOURCE_CASE_ID, 'parent_id': PARENT, 'run_id': run_id,
        'profile_digest': digest, 'executor_digest': artifact,
        'source_binding_digest': fingerprint(source_binding),
        'requirement_ids': list(REQUIREMENTS), 'story_checks': STORY_CHECKS,
        'bcs_binary_sha256': binary_sha256, 'cli_binary_sha256': cli_binary_sha256,
        'author_cli_pair_status': _AUTHOR_CLI_DISCLOSURE,
        'owned_local_executor': 'bcs_e2e.local_provider_owner_run.local_provider_owner_run',
        'local_backlog_commands': ['local-provider-owner-run', 'local-provider-owner-report'],
        'state_dir': str(state_dir),
    }
    write(binding_file, binding)

    local_result = None
    probe: dict | None = None
    admission: dict | None = None
    blocker: dict | None = None
    try:
        if tier == 'local':
            local_result = local_provider_owner_run(
                binary=binary, binary_sha256=binary_sha256,
                state_dir=state_dir / 'owned-provider-owner', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
            execution = _local_execution(local_result)
        else:
            admission = _credential_admission(profile['auth']['header_env'])
            try:
                resolved = _resolve_credential_values(profile['auth']['header_env'])
            except PreprodClosedError:
                resolved = {}
            probe = run_preprod_provider_owner_route_probe(profile, credentials=resolved)
            execution = _preprod_execution(probe, admission)
            blocker = {
                'reason_class': _PREPROD_FULL_STORY_ERROR,
                'observed_status': None,
                'reason': ('The preprod leg is a read-only wrong-method route probe and a '
                           'named credential admission check; the frozen 24-obligation journey '
                           'cannot execute on the preprod gateway because the Provider admin '
                           'face and its token channel are absent'),
                'credential_admission': admission,
                'credential_refs': sorted(profile['auth']['header_env'].values()),
                'route_probe_status': probe.get('status'),
                'documented_gaps': list(DOCUMENTED_GAPS),
            }
    except (ConfigError, RunStateError, OSError) as error:
        raise ConfigError(f'provider owner dual case execution is unavailable: {error}') from None

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
            'story_checks': STORY_CHECKS, 'source_case_id': SOURCE_CASE_ID,
            'shell_step_map_ref': 'provider-owner-isolation-migration-contract.json'},
        'case': execution.snapshot(),
        'execution': {'status': execution.status,
                      'completed_count': execution.completed_count,
                      'total_count': execution.total_count,
                      'error_type': execution.error_type},
        'resource_disposition': 'STORY_SCOPED_TEMPORARY_DIRECTORY',
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'credential_kind': ('owned_local_ephemeral_principal' if tier == 'local'
                            else 'preprod_human_cookie_route_probe'),
        'author_cli_pair': {
            'status': _AUTHOR_CLI_DISCLOSURE,
            'reason': ('The Provider owner journey is a station-legacy HTTP plus '
                       'Workbench WebSocket story; it never invokes the product CLI. The '
                       'author CLI biscuit pair is digest fenced on entry for the dual '
                       'surface contract and disclosed, not consumed.')},
        'family_adaptability': provider_owner_adaptability(),
        'dual_surface': provider_owner_dual_adaptability(),
        'serialization_sample': _serialization_sample(),
        'documented_gaps': {name: source_binding['documented_gaps'][name]
                            for name in DOCUMENTED_GAPS},
    }
    if tier == 'local':
        report['local_owned_run'] = {
            'status': local_result.get('status'),
            'error_type': local_result.get('error_type'),
            'child_receipt': local_result.get('child_receipt'),
            'canonical_summary_status': (
                local_result.get('canonical_summary', {}).get('status')),
            'state_dir': str(state_dir / 'owned-provider-owner'),
        }
    else:
        report['preprod_probe'] = probe
        report['credential_admission'] = admission
        report['preprod_blocker'] = blocker
        report['environment']['api_face'] = 'legacy'
        leaf = _write_preprod_leaf(run_id=run_id, report=report, leaf_root=leaf_root)
        report['preprod_evidence_leaf'] = str(leaf)
    write(report_file, report)
    return report


def register_provider_owner_dual_case_commands(commands) -> None:
    """Drop-in parser body for the frozen registration seam (describe-only)."""
    case = commands.add_parser(
        COMMAND,
        help='Run the frozen Provider owner-isolation story through owned-local or the bounded preprod route probe')
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
