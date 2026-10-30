"""Offline dual entrypoints for four dynamic owned-local Story families."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..inventory import executor_digest
from ..local_draft_group_run import local_draft_group_run, validate_draft_group_binary
from ..local_fixed_loop_run import local_fixed_loop_run, validate_fixed_loop_binary
from ..local_nested_fixed_loop_run import (
    local_nested_fixed_loop_run,
    validate_nested_fixed_loop_binary,
    validate_nested_fixed_loop_cli_binary,
)
from ..local_result_queue_run import local_result_queue_run, validate_result_queue_binary
from ..profile import ConfigError, fingerprint, identifier
from ..run_state import RunStateError, write
from .contract import dynamic_family_contract
from .profile import load_dynamic_family_profile
from .reporting import local_execution, preprod_execution, preprod_gap_receipt

DUAL_FIXED_LOOP_COMMAND = 'dual-fixed-loop-run'
DUAL_NESTED_FIXED_LOOP_COMMAND = 'dual-nested-fixed-loop-run'
DUAL_RESULT_QUEUE_COMMAND = 'dual-result-queue-run'
DUAL_DRAFT_GROUP_COMMAND = 'dual-draft-group-run'
DUAL_DYNAMIC_FAMILY_CASE_KEYS = frozenset({
    'fixed-loop', 'nested-fixed-loop', 'result-queue', 'draft-group'})


def _fresh_root(path: Path) -> Path:
    requested = Path(path)
    if (not requested.is_absolute() or requested.is_symlink() or requested.exists()
            or not requested.parent.is_dir() or requested.parent.is_symlink()):
        raise ConfigError('dynamic family requires a fresh private state directory')
    root = requested.resolve()
    if root.is_symlink() or root.exists():
        raise ConfigError('dynamic family requires a fresh private state directory')
    root.mkdir(mode=0o700, parents=False)
    return root


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binary(value: object, digest: object, validator, label: str) -> Path:
    if not isinstance(value, Path):
        value = Path(value)
    if not isinstance(digest, str) or len(digest) != 64:
        raise ConfigError('dynamic family ' + label + ' digest is invalid')
    try:
        validated = validator(Path(value), digest)
    except Exception as error:
        raise ConfigError(
            'dynamic family ' + label + ' biscuit failed offline admission: ' + str(error)) from None
    if _hash(validated) != digest:
        raise ConfigError('dynamic family ' + label + ' digest differs from file content')
    return validated


def _station_routes(report: dict, contract, profile: dict) -> list[dict]:
    return [
        {'name': route, 'status': 'NOT_RUN_OFFLINE',
         'gate': 'MAINLINE_STATION_ROUTE_VALIDATION',
         'contract': contract.profile_contract}
        for route in profile['station_routes']
    ]


def dual_dynamic_family_case_run(*, family: str, profile_path: Path,
                                state_dir: Path, run_id: str,
                                allow_write: bool, confirm_run_id: str,
                                binary: Path, binary_sha256: str,
                                cli_binary: Path = Path(),
                                driver: str = '') -> dict:
    if family not in {'fixed_loop', 'nested_fixed_loop', 'result_queue', 'draft_group'}:
        raise ConfigError('dynamic family is unsupported')
    run_id = identifier(run_id, 'dynamic family Run ID')
    if allow_write is not True or confirm_run_id != run_id:
        raise ConfigError(
            'dynamic family requires explicit write permission and exact Run confirmation')
    contract = dynamic_family_contract(family)
    profile = load_dynamic_family_profile(Path(profile_path))
    tier = profile['environment']['tier']
    root = _fresh_root(Path(state_dir))
    validators = {
        'fixed_loop': validate_fixed_loop_binary,
        'nested_fixed_loop': validate_nested_fixed_loop_binary,
        'result_queue': validate_result_queue_binary,
        'draft_group': validate_draft_group_binary,
    }
    binary = _binary(binary, binary_sha256, validators[family], 'BCS binary')
    if tier == 'local' and family == 'nested_fixed_loop':
        if not driver or not Path(cli_binary).is_file() or Path(cli_binary).is_symlink():
            raise ConfigError('local nested fixed-Loop requires its driver Bot ID and product CLI binary')
        cli_binary = validate_nested_fixed_loop_cli_binary(Path(cli_binary))
    artifact = executor_digest()

    profile_file = root / ('dynamic-family-' + family + '-profile.json')
    binding_file = root / ('dynamic-family-' + family + '-binding.json')
    report_file = root / ('dynamic-family-' + family + '-dual-run.json')
    gap_file = root / ('dynamic-family-' + family + '-preprod-gap.json')
    write(profile_file, profile)
    binding = {
        'schema_version': 1,
        'contract': contract.report_contract,
        'family': contract.family,
        'suite': contract.suite,
        'source_suite': contract.source_suite,
        'case_id': contract.case_id,
        'source_case_id': contract.source_case_id,
        'legacy_id': contract.legacy_id,
        'run_id': run_id,
        'profile_digest': fingerprint(profile),
        'executor_digest': artifact,
        'requirement_ids': list(contract.requirement_ids),
        'expected_assertion_count': len(contract.assertion_ids),
        'assertion_requirement_ids': contract.assertion_requirement_ids,
        'owned_local_executor': contract.local_executor,
        'bcs_binary_sha256': binary_sha256,
        'state_dir': str(root),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'preprod_verdict': 'NOT_RUN',
    }
    write(binding_file, binding)

    local_result = None
    gaps = contract.preprod_gaps if tier == 'preprod' else ()
    if tier == 'local':
        if family == 'fixed_loop':
            local_result = local_fixed_loop_run(
                binary=binary, binary_sha256=binary_sha256,
                state_dir=root / 'owned-local', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
        elif family == 'nested_fixed_loop':
            local_result = local_nested_fixed_loop_run(
                binary=binary, binary_sha256=binary_sha256,
                cli_binary=cli_binary, driver=driver,
                state_dir=root / 'owned-local', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
        elif family == 'result_queue':
            local_result = local_result_queue_run(
                binary=binary, binary_sha256=binary_sha256,
                state_dir=root / 'owned-local', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
        else:
            local_result = local_draft_group_run(
                binary=binary, binary_sha256=binary_sha256,
                state_dir=root / 'owned-local', run_id=run_id,
                confirm_run_id=run_id, allow_local_processes=True,
                allow_fixture_write=True, allow_write=True)
        execution = local_execution(contract, local_result)
        status = 'PASS' if execution.status == 'PASS' else 'BLOCKED'
    else:
        execution = preprod_execution(contract, gaps)
        status = 'BLOCKED_INVENTORIED'

    report = {
        'schema_version': 1,
        'contract': contract.report_contract,
        'case_id': execution.case_id,
        'source_case_id': contract.source_case_id,
        'legacy_id': contract.legacy_id,
        'family': contract.family,
        'run_id': run_id,
        'status': status,
        'profile_digest': fingerprint(profile),
        'executor_digest': artifact,
        'bcs_binary_sha256': binary_sha256,
        'state_dir': str(root),
        'environment': {
            'tier': tier,
            'id': profile['environment']['id'],
            'expected_bcs_env': profile['environment']['expected_bcs_env'],
            'entrypoint_kind': profile['entrypoint']['kind'],
            'api_face': profile['cli']['api_face'],
        },
        'obligation_summary': {
            'requirement_count': len(contract.requirement_ids),
            'requirement_ids': list(contract.requirement_ids),
            'expected_assertion_count': len(contract.assertion_ids),
            'assertion_projection': contract.traceability(),
            'source_case_id': contract.source_case_id,
        },
        'case': execution.snapshot(),
        'execution': {
            'status': execution.status,
            'completed_count': execution.completed_count,
            'total_count': execution.total_count,
            'error_type': execution.error_type,
        },
        'station_route_gates': _station_routes(report={}, contract=contract, profile=profile),
        'preprod_verdict': 'NOT_RUN',
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'resource_disposition': 'STORY_SCOPED_TEMPORARY_DIRECTORY',
        'credential_kind': ('owned-local-ephemeral' if tier == 'local'
                            else 'environment-reference-name-only'),
        'documented_gaps': list(gaps),
    }
    if tier == 'local':
        report['local_owned_run'] = {
            'status': local_result.get('status'),
            'error_type': local_result.get('error_type'),
            'canonical_summary_status': local_result.get('canonical_summary', {}).get('status'),
            'observed_assertion_count': local_result.get('case', {}).get(
                'required_assertion_count', 0),
            'state_dir': str(root / 'owned-local'),
        }
    else:
        gap = preprod_gap_receipt(contract, profile, report)
        write(gap_file, gap)
        report['preprod_gap_receipt'] = str(gap_file)
    write(report_file, report)
    return report


def dual_fixed_loop_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                             allow_write: bool, confirm_run_id: str,
                             binary: Path, binary_sha256: str) -> dict:
    return dual_dynamic_family_case_run(
        family='fixed_loop', profile_path=profile_path, state_dir=state_dir,
        run_id=run_id, allow_write=allow_write, confirm_run_id=confirm_run_id,
        binary=binary, binary_sha256=binary_sha256)


def dual_nested_fixed_loop_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                                    allow_write: bool, confirm_run_id: str,
                                    binary: Path, binary_sha256: str,
                                    cli_binary: Path, driver: str) -> dict:
    return dual_dynamic_family_case_run(
        family='nested_fixed_loop', profile_path=profile_path, state_dir=state_dir,
        run_id=run_id, allow_write=allow_write, confirm_run_id=confirm_run_id,
        binary=binary, binary_sha256=binary_sha256,
        cli_binary=cli_binary, driver=driver)


def dual_result_queue_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                               allow_write: bool, confirm_run_id: str,
                               binary: Path, binary_sha256: str) -> dict:
    return dual_dynamic_family_case_run(
        family='result_queue', profile_path=profile_path, state_dir=state_dir,
        run_id=run_id, allow_write=allow_write, confirm_run_id=confirm_run_id,
        binary=binary, binary_sha256=binary_sha256)


def dual_draft_group_case_run(*, profile_path: Path, state_dir: Path, run_id: str,
                              allow_write: bool, confirm_run_id: str,
                              binary: Path, binary_sha256: str) -> dict:
    return dual_dynamic_family_case_run(
        family='draft_group', profile_path=profile_path, state_dir=state_dir,
        run_id=run_id, allow_write=allow_write, confirm_run_id=confirm_run_id,
        binary=binary, binary_sha256=binary_sha256)
