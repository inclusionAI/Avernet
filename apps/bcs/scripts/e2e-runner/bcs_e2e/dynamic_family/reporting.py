"""Fail-closed case snapshots for dual dynamic family runs."""
from __future__ import annotations

from ..case_executor import CaseExecution, StepOutcome
from ..profile import fingerprint
from .contract import DynamicFamilyContract

PREPROD_NOT_RUN_ERROR = 'PREPROD_DYNAMIC_FAMILY_FULL_STORY_NOT_RUN'


def local_execution(contract: DynamicFamilyContract, result: dict) -> CaseExecution:
    case = result.get('case', {}) if isinstance(result, dict) else {}
    assertions = case.get('assertions', []) if isinstance(case, dict) else []
    if isinstance(assertions, list):
        assertion_ids = tuple(row.get('id') for row in assertions)
        assertion_statuses = tuple(row.get('status') for row in assertions)
    else:
        assertion_ids, assertion_statuses = (), ()
    passed = (
        result.get('status') == contract.local_status
        and result.get('canonical_summary', {}).get('status') == contract.canonical_status
        and case.get('status') == 'PASS'
        and case.get('case_id') == contract.source_case_id
        and case.get('required_assertion_count') == len(contract.assertion_ids)
        and assertion_ids == contract.assertion_ids
        and all(status == 'PASS' for status in assertion_statuses))
    error_type = None if passed else 'LOCAL_DYNAMIC_FAMILY_BLOCKED'
    steps = []
    for index, requirement_id in enumerate(contract.requirement_ids):
        sites = contract.sites_for(requirement_id)
        steps.append(StepOutcome(
            requirement_id=requirement_id,
            method='execute_' + contract.family + '_obligation',
            status='PASS' if passed else 'NOT_RUN',
            data={'source_case_id': contract.source_case_id} if passed else None,
            evidence={
                'source_case_id': contract.source_case_id,
                'assertion_scope': 'FROZEN_UPSTREAM_ASSERTION_SITES',
                'assertion_site_count': len(sites),
                'assertion_site_ids': list(sites),
                'observed_assertion_count': len(assertion_ids),
                'remote_validation': 'NOT_RUN',
                'grants_release_credit': False,
            } if passed else {},
            error_type=None if passed else (error_type if index == 0 else None)))
    return CaseExecution(
        case_id=contract.case_id, parent_id=contract.legacy_id,
        status='PASS' if passed else 'BLOCKED', steps=tuple(steps),
        completed_count=len(contract.requirement_ids) if passed else 0,
        total_count=len(contract.requirement_ids),
        error_type=error_type, ready_to_execute=passed)


def preprod_execution(contract: DynamicFamilyContract, gaps: tuple[str, ...]) -> CaseExecution:
    steps = []
    for index, requirement_id in enumerate(contract.requirement_ids):
        sites = contract.sites_for(requirement_id)
        steps.append(StepOutcome(
            requirement_id=requirement_id,
            method='execute_' + contract.family + '_obligation',
            status='NOT_RUN', data=None,
            evidence={
                'assertion_scope': 'FROZEN_UPSTREAM_ASSERTION_SITES',
                'assertion_site_count': len(sites),
                'assertion_site_ids': list(sites),
                'preprod_verdict': 'NOT_RUN',
                'documented_gaps': list(gaps),
                'remote_validation': 'NOT_RUN',
                'grants_release_credit': False,
            },
            error_type=PREPROD_NOT_RUN_ERROR if index == 0 else None))
    return CaseExecution(
        case_id=contract.case_id, parent_id=contract.legacy_id,
        status='BLOCKED', steps=tuple(steps),
        completed_count=0, total_count=len(contract.requirement_ids),
        error_type=PREPROD_NOT_RUN_ERROR, ready_to_execute=False)


def preprod_gap_receipt(contract: DynamicFamilyContract, profile: dict,
                        report: dict) -> dict:
    return {
        'schema_version': 1,
        'contract': contract.report_contract + '-gap-v1',
        'family': contract.family,
        'legacy_id': contract.legacy_id,
        'case_id': contract.case_id,
        'run_id': report['run_id'],
        'preprod_verdict': 'NOT_RUN',
        'status': 'BLOCKED_INVENTORIED',
        'documented_gaps': list(contract.preprod_gaps),
        'station_routes': list(profile['station_routes']),
        'entrypoint': dict(profile['entrypoint']),
        'auth_face': {
            'kind': profile['auth']['kind'],
            'header_reference_names_only': sorted(profile['auth']['header_env'].values()),
        },
        'profile_digest': fingerprint(profile),
        'executor_digest': report['executor_digest'],
        'expected_assertion_count': len(contract.assertion_ids),
        'assertion_projection': contract.traceability(),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
    }
