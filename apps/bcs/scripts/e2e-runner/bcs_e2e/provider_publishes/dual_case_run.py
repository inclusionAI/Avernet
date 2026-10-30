"""S04 dual entrypoint with an honest, read-only preprod admission surface.

The owned-local leg delegates unchanged to the frozen owned-local suite. The
preprod leg fail-closes honestly instead of statically refusing:

- a sealed v1 profile is a blocked-no-network seal without gateway material,
  so no probe can be constructed and the disclosed upgrade path is contract v2;
- a v2 gateway profile first admits every declared credential reference by
  NAME (presence plus bounded ASCII shape only; values never stored), then
  probes the frozen gateway faces read-only through the shared ReadClient
  (environment.read, identity.read and the driver Bot bot.read);
- an admitted probe still cannot execute the journey: no preprod
  provider-admin write journey child is published in this wave, so the leg
  discloses that seam with named gaps and keeps all 98 obligations NOT_RUN.

No branch stores a credential value, opens a socket on the sealed path, or
claims release credit.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from ..profile import (ConfigError, fingerprint, identifier, object_keys, origin,
                       unique_keys)
from ..run_state import RunStateError, write
from ..transport import ObservationError, ReadClient
from .contract import (CASE_ID, CLI_SHA256, PARENT, REQUIREMENTS, SERVER_SHA256,
                       SUITE, source_binding, validate_binary)
from .run import local_provider_publishes_run

COMMAND = 'dual-provider-publishes-run'
CASE_KEYS = ('provider-publishes',)
PROFILE_CONTRACT = 'dual-provider-publishes-profile-v1'
PROFILE_CONTRACT_V2 = 'dual-provider-publishes-profile-v2'
REPORT_CONTRACT = 'dual-provider-publishes-case-run-v1'
PARAMETERS = ('profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
              'state_dir', 'run_id', 'confirm_run_id', 'allow_write')

# v2 preprod admission profile contract. Auth values are environment-variable
# NAME references only; the shared ReadClient resolves them from the process
# environment at admission time and no value ever enters a binding or report.
ENV_REF = re.compile(r'^[A-Z][A-Z0-9_]{2,100}$')
GATEWAY_HEADER_ENV_KEYS = frozenset({'Authorization', 'Cookie'})
MANIFEST_FACES = frozenset({'legacy', 'internal-v1'})
STATION_BOT_ID = re.compile(r'^\d{8}_[a-z0-9]+:\d+$')
MAX_CREDENTIAL_BYTES = 8192
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
PROBE_OPERATIONS = ('environment.read', 'identity.read', 'bot.read')

PREPROD_PROFILE_SEALED_V1 = 'PREPROD_PROVIDER_PUBLISHES_PROFILE_SEALED_V1'
PREPROD_CREDENTIALS_REQUIRED = 'PREPROD_PROVIDER_CREDENTIALS_REQUIRED'
PREPROD_ADMISSION_PROFILE_DRIFT = 'PREPROD_PROVIDER_PUBLISHES_ADMISSION_PROFILE_DRIFT'
PREPROD_PROBE_FAILED = 'PREPROD_PROVIDER_PUBLISHES_ADMISSION_PROBE_FAILED'
PREPROD_JOURNEY_SEAM_PENDING = 'PREPROD_PROVIDER_PUBLISHES_JOURNEY_SEAM_PENDING'

SEALED_V1_DISCLOSURE = (
    'the frozen v1 preprod profile is a blocked-no-network seal: it carries no gateway '
    'origin, no auth refs and no driver Bot, so no live admission probe can be constructed; '
    're-source a dual-provider-publishes-profile-v2 profile (HTTPS gateway origin, '
    'Authorization/Cookie environment-name refs, dedicated-pool driver Bot) and re-run')

NAMED_GAPS = {
    'PREPROD_PROVIDER_ADMIN_WRITE_FACE_ABSENT': (
        'the 42 frozen publishing operations (registration, publish, binding, visibility, '
        'retire) are provider-admin mutations on the preprod gateway; this wave publishes '
        'no preprod provider-admin write journey, so even an admitted probe keeps all 98 '
        'obligations NOT_RUN'),
    'PREPROD_PROVIDER_REGISTRATION_PRECONDITIONS_PENDING': (
        'the frozen admission preconditions PROVIDER-PUBLISHES.register.* are minted only '
        'by the provider registration journey child (admin token, BCS callback token); '
        'that child is not published for preprod in this wave'),
    'PREPROD_PROVIDER_JOURNEY_CHILD_UNPUBLISHED': (
        'story_provider_operator_publishes_agent has no frozen preprod journey child in '
        'this wave; the dual surface admits the environment read-only and fails closed '
        'at the seam instead of fabricating release evidence'),
}


def profile_for(tier):
    return {'schema_version': 1, 'contract': PROFILE_CONTRACT,
            'environment': {'tier': tier, 'id': 's04-' + tier,
                            'expected_bcs_env': 'local' if tier == 'owned-local' else 'pre'},
            'entrypoint': {'kind': 'owned-local' if tier == 'owned-local' else 'blocked-no-network'}}


def _has_placeholder(value):
    if isinstance(value, str):
        return 'REPLACE_WITH_' in value
    if isinstance(value, dict):
        return any(_has_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_placeholder(item) for item in value)
    return False


def _validate_sealed_profile(raw):
    tier = raw['environment']['tier']
    if tier not in ('owned-local', 'preprod'):
        raise ValueError
    identifier(raw['environment']['id'], 'S04 environment ID')
    expected = profile_for(tier)
    expected['environment']['id'] = raw['environment']['id']
    if type(raw['schema_version']) is not int or raw != expected:
        raise ValueError
    return raw


def _validate_admission_profile(raw):
    """Validate the additive v2 preprod gateway admission profile."""
    profile = object_keys(raw, {'schema_version', 'contract', 'environment', 'entrypoint',
                                'auth', 'actors', 'policy'}, 'S04 preprod profile')
    if type(profile['schema_version']) is not int or profile['schema_version'] != 2:
        raise ConfigError('S04 preprod profile: unsupported schema_version')
    if profile['contract'] != PROFILE_CONTRACT_V2:
        raise ConfigError('S04 preprod profile: unsupported contract')
    environment = object_keys(profile['environment'], {'tier', 'id', 'expected_bcs_env'},
                              'S04 preprod environment')
    if environment['tier'] != 'preprod':
        raise ConfigError('S04 preprod profile: contract v2 admits preprod only')
    identifier(environment['id'], 'S04 environment ID')
    if environment['expected_bcs_env'] != 'pre':
        raise ConfigError('S04 preprod environment: expected_bcs_env binding mismatch')
    entrypoint = object_keys(profile['entrypoint'], {'kind', 'origin', 'manifest_face'},
                             'S04 preprod entrypoint')
    if entrypoint['kind'] != 'gateway':
        raise ConfigError('S04 preprod entrypoint: contract v2 admits gateway only')
    origin(entrypoint['origin'], 'preprod')
    if entrypoint['manifest_face'] not in MANIFEST_FACES:
        raise ConfigError('S04 preprod entrypoint: manifest_face must be legacy or internal-v1')
    auth = object_keys(profile['auth'], {'header_env'}, 'S04 preprod auth')
    header_env = object_keys(auth['header_env'], set(GATEWAY_HEADER_ENV_KEYS),
                             'S04 preprod auth.header_env')
    for header, reference in header_env.items():
        if not isinstance(reference, str) or ENV_REF.fullmatch(reference) is None:
            raise ConfigError('S04 preprod auth.header_env.' + header
                              + ': expected an uppercase environment reference name')
    actors = object_keys(profile['actors'], {'driver'}, 'S04 preprod actors')
    driver = object_keys(actors['driver'], {'bot_id'}, 'S04 preprod actors.driver')
    bot_id = identifier(driver['bot_id'], 'S04 actors.driver.bot_id')
    if STATION_BOT_ID.fullmatch(bot_id) is None:
        raise ConfigError('S04 preprod actors.driver.bot_id: dedicated-pool station shape '
                          '<bot_prefix>:<user_id> required')
    policy = object_keys(profile['policy'], {'timeout_seconds', 'max_response_bytes'},
                         'S04 preprod policy')
    if type(policy['timeout_seconds']) is not int or not 1 <= policy['timeout_seconds'] <= 60:
        raise ConfigError('S04 preprod policy.timeout_seconds is outside 1..60')
    if type(policy['max_response_bytes']) is not int \
            or not 1 <= policy['max_response_bytes'] <= MAX_RESPONSE_BYTES:
        raise ConfigError('S04 preprod policy.max_response_bytes is outside 1..16777216')
    if _has_placeholder(profile):
        raise ConfigError('S04 preprod profile: replace all REPLACE_WITH_* placeholders '
                          'before execution')
    return json.loads(json.dumps(profile))


def load_profile(path):
    if path.is_symlink() or not path.is_file():
        raise ConfigError('S04 profile must be a regular file')
    try:
        raw = json.loads(path.read_text(), object_pairs_hook=unique_keys)
    except ValueError:
        raise ConfigError('S04 profile must be readable JSON') from None
    if not isinstance(raw, dict):
        raise ConfigError('S04 profile must be an object')
    if raw.get('schema_version') == 2:
        return _validate_admission_profile(raw)
    try:
        return _validate_sealed_profile(raw)
    except (ValueError, TypeError, KeyError):
        raise ConfigError('S04 profile must match the frozen no-network boundary') from None


def family_digest():
    files = sorted(Path(__file__).parent.glob('*.py'))
    return fingerprint({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def _fresh(path):
    if (not path.is_absolute() or path.exists() or path.is_symlink()
            or not path.parent.is_dir() or any(p.is_symlink() for p in path.parents)):
        raise ConfigError('S04 requires a fresh private state directory under a real parent')
    path.mkdir(mode=0o700)
    return path


def _credential_admission(header_env):
    """Admit provider credential material by environment-reference NAME only.

    Presence and bounded ASCII shape are checked per declared reference; the
    resolved reference names are disclosed, never their values.
    """
    references = []
    for header in sorted(header_env):
        value = os.environ.get(header_env[header], '')
        shape_admitted = (bool(value) and value.isascii()
                          and not any(ord(char) < 32 or ord(char) == 127 for char in value)
                          and len(value) <= MAX_CREDENTIAL_BYTES)
        references.append({'header': header, 'environment_reference': header_env[header],
                           'present': bool(value), 'shape_admitted': shape_admitted})
    admitted = all(row['present'] and row['shape_admitted'] for row in references)
    return {
        'status': 'ADMITTED' if admitted else 'BLOCKED',
        'reason_class': None if admitted else PREPROD_CREDENTIALS_REQUIRED,
        'reason': ('' if admitted else
                   'named provider credential environment references are not loaded or fail '
                   'their bounded shape: '
                   + '; '.join(row['environment_reference'] + ' (' + row['header'] + ')'
                               for row in references
                               if not (row['present'] and row['shape_admitted']))
                   + '; source the preprod credential window, then re-run'),
        'resolved_references_by_name': sorted(header_env.values()) if admitted else [],
        'references': references,
        'source': 'process-environment-only',
        'credential_values_stored': False,
    }


def _project_evidence(record):
    if not isinstance(record, dict):
        return {}
    return {key: record.get(key) for key in
            ('operation', 'method', 'path', 'http_status', 'submitted_request_id',
             'request_id_echoed', 'elapsed_ms')}


def _preprod_leg(profile, *, client=None):
    """Preprod admission: named credential gate plus one live read-only probe.

    The probe drives the shared ReadClient over the frozen gateway faces
    (environment.read, identity.read and the driver Bot bot.read) and records
    sanitized evidence projections only. Every failure is disclosed with a
    machine-readable reason class; the unpublished journey seam is disclosed
    with named gaps instead of being fabricated into a pass.
    """
    if profile.get('schema_version') == 1:
        gaps = dict(NAMED_GAPS)
        gaps['PREPROD_PROFILE_SEALED_V1_NO_ADMISSION_MATERIAL'] = SEALED_V1_DISCLOSURE
        return {'status': 'BLOCKED', 'reason_class': PREPROD_PROFILE_SEALED_V1,
                'reason': SEALED_V1_DISCLOSURE, 'error_type': None, 'error_detail': None,
                'credential_admission': None, 'probe': None, 'network_requests': 0,
                'named_gaps': gaps, 'remote_entry_status': 'BLOCKED-at-entry',
                'observed_status': None, 'credential_refs': []}
    header_env = profile['auth']['header_env']
    admission = _credential_admission(header_env)
    credential_refs = sorted(header_env.values())
    if admission['status'] != 'ADMITTED':
        return {'status': 'NOT_RUN', 'reason_class': PREPROD_CREDENTIALS_REQUIRED,
                'reason': admission['reason'], 'error_type': None,
                'error_detail': ('no admission read is issued; the shared ReadClient '
                                 'fail-closes on an unresolved credential reference before '
                                 'any socket is opened'),
                'credential_admission': admission, 'probe': None, 'network_requests': 0,
                'named_gaps': dict(NAMED_GAPS),
                'remote_entry_status': 'NOT_RUN_CREDENTIALS_REQUIRED',
                'observed_status': None, 'credential_refs': credential_refs}
    probe_client = None
    rows = []
    try:
        probe_client = client if client is not None else ReadClient(profile)
        driver_bot_id = identifier(profile['actors']['driver']['bot_id'],
                                   'S04 actors.driver.bot_id')
        for operation, kwargs in (('environment.read', {}), ('identity.read', {}),
                                  ('bot.read', {'bot_id': driver_bot_id})):
            observation = probe_client.read(operation, **kwargs)
            rows.append(_project_evidence(observation.evidence))
    except ObservationError as error:
        detail = str(error)[:512]
        return {'status': 'BLOCKED', 'reason_class': PREPROD_PROBE_FAILED,
                'reason': 'the live preprod admission probe failed closed: ' + detail,
                'error_type': 'ObservationError', 'error_detail': detail,
                'credential_admission': admission,
                'probe': {'status': 'BLOCKED', 'operations': rows,
                          'network_requests': len(probe_client.evidence) if probe_client else 0},
                'network_requests': len(probe_client.evidence) if probe_client else 0,
                'named_gaps': dict(NAMED_GAPS),
                'remote_entry_status': 'BLOCKED-at-admission-probe',
                'observed_status': None, 'credential_refs': credential_refs}
    except (ConfigError, OSError, ValueError, TypeError) as error:
        detail = str(error)[:512] if isinstance(error, (ConfigError, ValueError)) \
            else type(error).__name__
        return {'status': 'BLOCKED', 'reason_class': PREPROD_ADMISSION_PROFILE_DRIFT,
                'reason': 'the admitted v2 profile fails shared transport admission: ' + detail,
                'error_type': type(error).__name__, 'error_detail': detail,
                'credential_admission': admission,
                'probe': {'status': 'BLOCKED', 'operations': rows,
                          'network_requests': len(probe_client.evidence) if probe_client else 0},
                'network_requests': len(probe_client.evidence) if probe_client else 0,
                'named_gaps': dict(NAMED_GAPS),
                'remote_entry_status': 'BLOCKED-at-admission-probe',
                'observed_status': None, 'credential_refs': credential_refs}
    network_requests = len(probe_client.evidence)
    statuses = [row.get('http_status') for row in rows]
    if any(status != 200 for status in statuses):
        return {'status': 'BLOCKED', 'reason_class': PREPROD_PROBE_FAILED,
                'reason': ('the live preprod admission probe did not observe HTTP 200 on '
                           'every frozen read: ' + repr(statuses)),
                'error_type': None, 'error_detail': 'observed admission statuses ' + repr(statuses),
                'credential_admission': admission,
                'probe': {'status': 'BLOCKED', 'operations': rows,
                          'network_requests': network_requests},
                'network_requests': network_requests, 'named_gaps': dict(NAMED_GAPS),
                'remote_entry_status': 'BLOCKED-at-admission-probe',
                'observed_status': next((status for status in statuses if isinstance(status, int)),
                                        None),
                'credential_refs': credential_refs}
    return {'status': 'BLOCKED', 'reason_class': PREPROD_JOURNEY_SEAM_PENDING,
            'reason': ('the live admission probe passed every frozen read and every named '
                       'credential reference is loaded; the frozen preprod provider-publishing '
                       'journey child is not published in this wave, so all 98 obligations '
                       'stay NOT_RUN at the seam and no release credit is granted'),
            'error_type': None, 'error_detail': None, 'credential_admission': admission,
            'probe': {'status': 'PASS', 'operations': rows,
                      'network_requests': network_requests,
                      'admitted_operations': list(PROBE_OPERATIONS)},
            'network_requests': network_requests, 'named_gaps': dict(NAMED_GAPS),
            'remote_entry_status': 'PROBE_ADMITTED_JOURNEY_SEAM_PENDING',
            'observed_status': None, 'credential_refs': credential_refs}


def dual_provider_publishes_case_run(*, profile_path, binary, binary_sha256, cli_binary,
                                     cli_binary_sha256, state_dir, run_id, confirm_run_id,
                                     allow_write):
    identifier(run_id, 'S04 Run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError('S04 requires explicit writes and exact Run confirmation')
    # Both pairs, profile and source are admitted before even mkdir is permitted.
    server = validate_binary(binary, binary_sha256, SERVER_SHA256, 'BCS')
    validate_binary(cli_binary, cli_binary_sha256, CLI_SHA256, 'CLI')
    profile = load_profile(Path(profile_path))
    contract = source_binding()
    digest = family_digest()
    state_dir = _fresh(Path(state_dir))
    binding = {'schema_version': 1, 'contract': REPORT_CONTRACT, 'suite': SUITE,
               'case_id': CASE_ID, 'parent_id': PARENT, 'run_id': run_id,
               'bcs_binary_sha256': binary_sha256, 'cli_binary_sha256': cli_binary_sha256,
               'profile_digest': fingerprint(profile), 'source_binding_digest': fingerprint(contract),
               'executor_digest': digest, 'requirement_ids': list(REQUIREMENTS),
               'author_cli_pair_status': 'ADMITTED_FROZEN_NOT_CONSUMED_BY_HTTP_JOURNEY'}
    write(state_dir / 'provider-publishes-dual-binding.json', binding)
    write(state_dir / 'provider-publishes-profile.json', profile)
    write(state_dir / 'provider-publishes-source-map.json', contract)
    result = None
    preprod = None
    if profile['environment']['tier'] == 'owned-local':
        result = local_provider_publishes_run(binary=server, binary_sha256=binary_sha256,
            state_dir=state_dir / 'owned-provider-publishes', run_id=run_id, contract=contract)
        actual = result.get('case', {}).get('assertions', [])
        passed = (result.get('status') == 'PASS' and [r['id'] for r in actual] == list(REQUIREMENTS)
                  and all(r.get('status') == 'PASS' for r in actual) and family_digest() == digest)
        assertions = actual or [{'id': r, 'status': 'NOT_RUN'} for r in REQUIREMENTS]
        reason = None if passed else 'LOCAL_PROVIDER_PUBLISHES_BLOCKED'
        remote_entry_status = 'BLOCKED-at-entry'
    else:
        preprod = _preprod_leg(profile)
        passed = False
        reason = preprod['reason_class']
        assertions = [{'id': r, 'status': 'NOT_RUN'} for r in REQUIREMENTS]
        remote_entry_status = preprod['remote_entry_status']
    case_status = 'PASS' if passed else ('NOT_RUN' if preprod is not None
                                         and preprod['status'] == 'NOT_RUN' else 'BLOCKED')
    report = dict(binding, status=case_status, environment=profile['environment'],
        reason_class=reason, assertions=assertions,
        obligation_summary={'baseline': 42, 'total': 98, 'field_obligations': 56,
                            'passed': sum(r['status'] == 'PASS' for r in assertions),
                            'failed': sum(r['status'] == 'FAIL' for r in assertions),
                            'not_run': sum(r['status'] == 'NOT_RUN' for r in assertions)},
        remote_validation='NOT_RUN', remote_entry_status=remote_entry_status,
        grants_release_credit=False, full_release_status='NOT_QUALIFIED',
        resource_disposition='RETAINED' if result else 'NO_REMOTE_RESOURCES_CREATED')
    if result is not None:
        report['local_owned_run'] = result
    if preprod is not None:
        report['preprod_admission'] = {
            'profile_schema_version': profile['schema_version'],
            'profile_contract': profile['contract'],
            'credential_admission': preprod['credential_admission'],
            'probe': preprod['probe'],
            'network_requests': preprod['network_requests'],
            'named_gaps': preprod['named_gaps'],
            'read_only': True, 'credential_values_stored': False,
            'grants_release_credit': False}
        report['preprod_blocker'] = {
            'reason_class': preprod['reason_class'], 'reason': preprod['reason'],
            'error_type': preprod['error_type'], 'error_detail': preprod['error_detail'],
            'observed_status': preprod['observed_status'],
            'network_requests': preprod['network_requests'],
            'credential_resolution': ('NOT_ATTEMPTED_PROFILE_SEALED'
                                      if preprod['credential_admission'] is None
                                      else preprod['credential_admission']['status']
                                      + '_BY_NAME_ONLY'),
            'credential_refs': preprod['credential_refs'],
            'credential_values_stored': False,
            'documented_named_gaps': preprod['named_gaps']}
    write(state_dir / 'provider-publishes-dual-run.json', report)
    verdict = {'status': case_status, 'run_id': run_id,
               'obligation_summary': report['obligation_summary'], 'reason_class': reason,
               'remote_validation': 'NOT_RUN', 'grants_release_credit': False}
    if preprod is not None:
        verdict['preprod_entry_status'] = preprod['remote_entry_status']
        verdict['network_requests'] = preprod['network_requests']
        verdict['credential_admission_status'] = ('NOT_ATTEMPTED_PROFILE_SEALED'
                                                  if preprod['credential_admission'] is None
                                                  else preprod['credential_admission']['status'])
        verdict['credential_refs'] = preprod['credential_refs']
        verdict['documented_named_gaps'] = list(preprod['named_gaps'])
        verdict['error_type'] = preprod['error_type']
    write(state_dir / 'verdict.json', verdict)
    return report


def _arguments(parser):
    for name in PARAMETERS:
        flag = '--' + name.replace('_', '-')
        if name == 'allow_write':
            parser.add_argument(flag, action='store_true')
        elif name in ('profile', 'binary', 'cli_binary', 'state_dir'):
            parser.add_argument(flag, required=True, type=Path)
        else:
            parser.add_argument(flag, required=True)
    return parser


def build_provider_publishes_dual_argv_parser():
    return _arguments(argparse.ArgumentParser(description='Frozen S04 Provider publishing executor'))


def register_provider_publishes_dual_case_commands(commands):
    """Drop-in seam helper; mainline owns registration and inventory edits."""
    _arguments(commands.add_parser(COMMAND, help='Run the 98-obligation Provider publishing journey'))


def main(argv=None):
    args = build_provider_publishes_dual_argv_parser().parse_args(argv)
    try:
        report = dual_provider_publishes_case_run(profile_path=args.profile,
            binary=args.binary, binary_sha256=args.binary_sha256, cli_binary=args.cli_binary,
            cli_binary_sha256=args.cli_binary_sha256, state_dir=args.state_dir,
            run_id=args.run_id, confirm_run_id=args.confirm_run_id, allow_write=args.allow_write)
    except (ConfigError, RunStateError, OSError, ValueError) as error:
        print(json.dumps({'status': 'BLOCKED', 'error_type': type(error).__name__,
                          'remote_validation': 'NOT_RUN', 'grants_release_credit': False}))
        return 2
    print(json.dumps({'status': report['status'], 'run_id': args.run_id,
                      'obligation_summary': report['obligation_summary'],
                      'state_dir': str(args.state_dir), 'remote_validation': 'NOT_RUN',
                      'grants_release_credit': False}, indent=2))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
