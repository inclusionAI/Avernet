"""Traceable dual contracts for the dynamic fixed-loop families.

The dual surface deliberately separates the original parent obligation count
from the frozen upstream assertion-site count.  A family contract therefore
keeps both layers and maps every source assertion site to its owning parent
obligation.  Sites shared by the approved and exhausted fixed-Loop journeys can
project to both obligations without changing the 66 unique upstream sites.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path

from ..local_fixed_loop_contract import (
    ASSERTION_IDS as FIXED_ASSERTION_IDS,
    REQUIREMENTS as FIXED_REQUIREMENTS,
)
from ..local_nested_fixed_loop_contract import (
    ASSERTION_IDS as NESTED_ASSERTION_IDS,
    REQUIREMENTS as NESTED_REQUIREMENTS,
)
from ..local_result_queue_contract import (
    ASSERTION_IDS as RESULT_ASSERTION_IDS,
    REQUIREMENTS as RESULT_REQUIREMENTS,
)
from ..local_draft_group_contract import (
    ASSERTION_IDS as DRAFT_ASSERTION_IDS,
    REQUIREMENTS as DRAFT_REQUIREMENTS,
)

ROOT = Path(__file__).resolve().parents[2]
_LINE_RE = re.compile(r'-(\d{3})-')


@dataclass(frozen=True)
class DynamicFamilyContract:
    family: str
    command: str
    legacy_id: str
    case_id: str
    source_case_id: str
    suite: str
    source_suite: str
    profile_contract: str
    report_contract: str
    requirement_ids: tuple[str, ...]
    assertion_ids: tuple[str, ...]
    assertion_requirement_ids: dict[str, tuple[str, ...]]
    local_executor: str
    local_status: str
    canonical_status: str
    preprod_initial_verdict: str
    preprod_gaps: tuple[str, ...]

    def sites_for(self, requirement_id: str) -> tuple[str, ...]:
        return tuple(
            site for site, owners in self.assertion_requirement_ids.items()
            if requirement_id in owners)

    def traceability(self) -> dict:
        return {
            requirement_id: {
                'assertion_site_count': len(self.sites_for(requirement_id)),
                'assertion_site_ids': list(self.sites_for(requirement_id)),
            }
            for requirement_id in self.requirement_ids
        }


def _line_number(site: str) -> int:
    match = _LINE_RE.search(site)
    if match is None:
        raise ValueError('dynamic family assertion site lacks a source line')
    return int(match.group(1))


def _map_by_story_lines(assertion_ids: tuple[str, ...],
                        source_map: str,
                        requirement_ids: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    rows = json.loads((ROOT / source_map).read_text(encoding='utf-8'))['requirements']
    result = {}
    for site in assertion_ids:
        line = _line_number(site)
        owners = tuple(
            row['id'] for row in rows
            if row['id'] in requirement_ids
            and row['story_lines'][0] <= line <= row['story_lines'][1])
        if not owners:
            raise ValueError('dynamic family assertion site has no owning obligation')
        result[site] = owners
    return result


def _fixed_loop_map(assertion_ids: tuple[str, ...],
                    requirement_ids: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    sandbox, fixture = requirement_ids[:2]
    approved, exhausted, publication, history = requirement_ids[2:6]
    exact_cleanup, reset_replacement = requirement_ids[6:8]
    mapped = {}
    for site in assertion_ids:
        line = _line_number(site)
        if line == 136:
            owners = (sandbox, reset_replacement)
        elif 137 <= line <= 159:
            owners = (fixture,)
        elif line in (162, 163, 208, 297):
            owners = (reset_replacement,)
        elif 167 <= line <= 168 or line == 179:
            owners = (approved, exhausted)
        elif line in (183,):
            owners = (approved,)
        elif 185 <= line <= 188:
            owners = (exhausted,)
        elif 193 <= line <= 240:
            owners = (approved, exhausted)
        elif 245 <= line <= 259:
            owners = (history, approved, exhausted)
        elif 260 <= line <= 281:
            owners = (history,)
        elif 284 <= line <= 289:
            owners = (publication,)
        elif 292 <= line <= 296:
            owners = (exact_cleanup,)
        else:
            raise ValueError('fixed-Loop assertion site is outside its source map')
        mapped[site] = owners
    return mapped


def _validate_traceability(contract: DynamicFamilyContract) -> None:
    if (len(contract.requirement_ids) != len(set(contract.requirement_ids))
            or len(contract.assertion_ids) != len(set(contract.assertion_ids))
            or set(contract.assertion_requirement_ids) != set(contract.assertion_ids)):
        raise ValueError('dynamic family contract has duplicate or missing assertion sites')
    for site, owners in contract.assertion_requirement_ids.items():
        if not owners or any(owner not in contract.requirement_ids for owner in owners):
            raise ValueError('dynamic family assertion site has invalid obligation owners')


_FIXED = DynamicFamilyContract(
    family='fixed_loop', command='dual-fixed-loop-run',
    legacy_id='story_fixed_loop_human_editor_workflow',
    case_id='DUAL-FIXED-LOOP-01', source_case_id='LOCAL-FIXED-LOOP-01',
    suite='dual-fixed-loop-v1', source_suite='owned-local-fixed-loop-v1',
    profile_contract='dual-fixed-loop-profile-v1',
    report_contract='dual-fixed-loop-case-run-v1',
    requirement_ids=FIXED_REQUIREMENTS, assertion_ids=FIXED_ASSERTION_IDS,
    assertion_requirement_ids=_fixed_loop_map(FIXED_ASSERTION_IDS, FIXED_REQUIREMENTS),
    local_executor='bcs_e2e.local_fixed_loop_run.local_fixed_loop_run',
    local_status='LOCAL_FIXED_LOOP_PASS', canonical_status='LOCAL_SUITE_PASS',
    preprod_initial_verdict='NOT_RUN',
    preprod_gaps=(
        'PREPROD_PROVIDER_JUDGE_SANDBOX_UNMOUNTED',
        'PREPROD_SHARED_CONTROL_FACE_UNMOUNTED',
        'PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE',
    ))

_NESTED = DynamicFamilyContract(
    family='nested_fixed_loop', command='dual-nested-fixed-loop-run',
    legacy_id='story_nested_fixed_loop_workflows',
    case_id='DUAL-NESTED-FIXED-LOOP-01', source_case_id='LOCAL-NESTED-FIXED-LOOP-01',
    suite='dual-nested-fixed-loop-v1', source_suite='owned-local-nested-fixed-loop-v1',
    profile_contract='dual-nested-fixed-loop-profile-v1',
    report_contract='dual-nested-fixed-loop-case-run-v1',
    requirement_ids=NESTED_REQUIREMENTS, assertion_ids=NESTED_ASSERTION_IDS,
    assertion_requirement_ids=_map_by_story_lines(
        NESTED_ASSERTION_IDS, 'nested-fixed-loop-migration-contract.json', NESTED_REQUIREMENTS),
    local_executor='bcs_e2e.local_nested_fixed_loop_run.local_nested_fixed_loop_run',
    local_status='LOCAL_NESTED_FIXED_LOOP_PASS', canonical_status='LOCAL_SUITE_PASS',
    preprod_initial_verdict='NOT_RUN',
    preprod_gaps=(
        'PREPROD_NESTED_JUDGE_SEQUENCE_UNMOUNTED',
        'PREPROD_STALE_CALLBACK_HARNESS_UNMOUNTED',
        'PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE',
    ))

_RESULT = DynamicFamilyContract(
    family='result_queue', command='dual-result-queue-run',
    legacy_id='story_state_machine_result_queue',
    case_id='DUAL-RESULT-QUEUE-01', source_case_id='LOCAL-RESULT-QUEUE-01',
    suite='dual-result-queue-v1', source_suite='owned-local-result-queue-v1',
    profile_contract='dual-result-queue-profile-v1',
    report_contract='dual-result-queue-case-run-v1',
    requirement_ids=RESULT_REQUIREMENTS, assertion_ids=RESULT_ASSERTION_IDS,
    assertion_requirement_ids=_map_by_story_lines(
        RESULT_ASSERTION_IDS, 'result-queue-migration-contract.json', RESULT_REQUIREMENTS),
    local_executor='bcs_e2e.local_result_queue_run.local_result_queue_run',
    local_status='LOCAL_RESULT_QUEUE_PASS', canonical_status='LOCAL_SUITE_PASS',
    preprod_initial_verdict='NOT_RUN',
    preprod_gaps=(
        'PREPROD_ADMIN_MESSAGE_DELIVERY_POLICY_ROUTE_UNREGISTERED',
        'PREPROD_PROVIDER_CALLBACK_HARNESS_UNREGISTERED',
        'PREPROD_RESULT_DELIVERY_LEDGER_FACE_VERIFICATION_NOT_RUN',
        'PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE',
    ))

_DRAFT = DynamicFamilyContract(
    family='draft_group', command='dual-draft-group-run',
    legacy_id='story_collaboration_draft_group_launch',
    case_id='DUAL-DRAFT-GROUP-01', source_case_id='LOCAL-DRAFT-GROUP-01',
    suite='dual-draft-group-v1', source_suite='owned-local-draft-group-v1',
    profile_contract='dual-draft-group-profile-v1',
    report_contract='dual-draft-group-case-run-v1',
    requirement_ids=DRAFT_REQUIREMENTS, assertion_ids=DRAFT_ASSERTION_IDS,
    assertion_requirement_ids=_map_by_story_lines(
        DRAFT_ASSERTION_IDS, 'draft-group-migration-contract.json', DRAFT_REQUIREMENTS),
    local_executor='bcs_e2e.local_draft_group_run.local_draft_group_run',
    local_status='LOCAL_DRAFT_GROUP_PASS', canonical_status='LOCAL_SUITE_PASS',
    preprod_initial_verdict='NOT_RUN',
    preprod_gaps=(
        'PREPROD_DRAFT_GROUP_CLI_FACE_NOT_EXECUTED_OFFLINE',
        'PREPROD_BROWSER_IDENTITY_CHAIN_UNVERIFIED',
        'PREPROD_THREE_BOT_AUTHORIZATION_MATRIX_UNVERIFIED',
        'PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE',
    ))

DYNAMIC_FAMILIES = (_FIXED, _NESTED, _RESULT, _DRAFT)
for _contract in DYNAMIC_FAMILIES:
    _validate_traceability(_contract)


def dynamic_families() -> tuple[DynamicFamilyContract, ...]:
    return DYNAMIC_FAMILIES


def dynamic_family_contract(family: str) -> DynamicFamilyContract:
    for contract in DYNAMIC_FAMILIES:
        if contract.family == family:
            return contract
    raise ValueError('unknown dynamic family: ' + family)
