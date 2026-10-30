"""S04 dual entrypoint. Preprod is blocked before credentials or network access."""
import argparse
import hashlib
import json
from pathlib import Path

from ..profile import ConfigError, fingerprint, identifier, unique_keys
from ..run_state import RunStateError, write
from .contract import (CASE_ID, CLI_SHA256, PARENT, REQUIREMENTS, SERVER_SHA256,
                       SUITE, source_binding, validate_binary)
from .run import local_provider_publishes_run

COMMAND = 'dual-provider-publishes-run'
CASE_KEYS = ('provider-publishes',)
PROFILE_CONTRACT = 'dual-provider-publishes-profile-v1'
REPORT_CONTRACT = 'dual-provider-publishes-case-run-v1'
PREPROD_REASON = 'PREPROD_PROVIDER_PUBLISHES_CREDENTIALS_UNAVAILABLE'
PARAMETERS = ('profile', 'binary', 'binary_sha256', 'cli_binary', 'cli_binary_sha256',
              'state_dir', 'run_id', 'confirm_run_id', 'allow_write')


def profile_for(tier):
    return {'schema_version': 1, 'contract': PROFILE_CONTRACT,
            'environment': {'tier': tier, 'id': 's04-' + tier,
                            'expected_bcs_env': 'local' if tier == 'owned-local' else 'pre'},
            'entrypoint': {'kind': 'owned-local' if tier == 'owned-local' else 'blocked-no-network'}}


def load_profile(path):
    if path.is_symlink() or not path.is_file():
        raise ConfigError('S04 profile must be a regular file')
    try:
        p = json.loads(path.read_text(), object_pairs_hook=unique_keys)
        tier = p['environment']['tier']
        if tier not in ('owned-local', 'preprod'):
            raise ValueError
        identifier(p['environment']['id'], 'S04 environment ID')
        expected = profile_for(tier)
        expected['environment']['id'] = p['environment']['id']
        if type(p['schema_version']) is not int or p != expected:
            raise ValueError
        return p
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
    if profile['environment']['tier'] == 'owned-local':
        result = local_provider_publishes_run(binary=server, binary_sha256=binary_sha256,
            state_dir=state_dir / 'owned-provider-publishes', run_id=run_id, contract=contract)
        actual = result.get('case', {}).get('assertions', [])
        passed = (result.get('status') == 'PASS' and [r['id'] for r in actual] == list(REQUIREMENTS)
                  and all(r.get('status') == 'PASS' for r in actual) and family_digest() == digest)
        assertions = actual or [{'id': r, 'status': 'NOT_RUN'} for r in REQUIREMENTS]
        reason = None if passed else 'LOCAL_PROVIDER_PUBLISHES_BLOCKED'
    else:
        passed = False
        reason = PREPROD_REASON
        assertions = [{'id': r, 'status': 'NOT_RUN'} for r in REQUIREMENTS]
    report = dict(binding, status='PASS' if passed else 'BLOCKED', environment=profile['environment'],
        reason_class=reason, assertions=assertions,
        obligation_summary={'baseline': 42, 'total': 98, 'field_obligations': 56,
                            'passed': sum(r['status'] == 'PASS' for r in assertions),
                            'failed': sum(r['status'] == 'FAIL' for r in assertions),
                            'not_run': sum(r['status'] == 'NOT_RUN' for r in assertions)},
        remote_validation='NOT_RUN', remote_entry_status='BLOCKED-at-entry',
        preprod_blocker={'reason_class': PREPROD_REASON, 'network_requests': 0,
            'observed_status': None, 'credential_resolution': 'NOT_ATTEMPTED',
            'reason': 'Operator reports canonical 2026-10-29 cookie expired/USER_NOT_LOGIN. '
                      'No re-probe or credential refresh authorized; preprod remains blocked.'},
        grants_release_credit=False, full_release_status='NOT_QUALIFIED',
        resource_disposition='RETAINED' if result else 'NO_REMOTE_RESOURCES_CREATED')
    if result is not None:
        report['local_owned_run'] = result
    write(state_dir / 'provider-publishes-dual-run.json', report)
    write(state_dir / 'verdict.json', {'status': report['status'], 'run_id': run_id,
          'obligation_summary': report['obligation_summary'], 'reason_class': reason,
          'remote_validation': 'NOT_RUN', 'grants_release_credit': False})
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
