"""Offline, source-bound migration obligations; never execution admission.

This finite inventory freezes the original parent Stories and every direct
business helper. Source drift requires review, not automatic hash regeneration.
It does not source Shell, resolve credentials, or count declarations as passes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .local_structured_core_contract import structured_core_adaptability
from .run_state import RunStateError, read
from .trust_registration import PROGRAMMATIC_EXECUTION_CONTRACT
from .inventory import current_manifest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {'stories.sh', 'group.sh', 'friends.sh', 'session_files.sh', 'common.sh'}
STORIES = {
    'story_user_operates_group_workspace': 'WORKSPACE',
    'story_user_builds_trusted_team': 'FRIEND',
    'story_session_file_workspace': 'CLI-FILE',
}
# Reviewed business obligations are finite. Dropping one must not be hidden by
# another requirement pointing to the same large legacy function.
# 2026-10-26 migration review: upstream dev 72f9dff36 removed the obsolete group
# proposal/confirmation product surface end to end; WORKSPACE.proposal and its
# stories.sh sub-story are retired from this frozen map (evidence: Avernet #3114).
REQUIRED_IDS = {
    'WORKSPACE': ('create', 'detail', 'patch', 'list', 'members', 'label', 'visibility',
                  'cli-create-get', 'cli-add-member', 'cli-fuse', 'cli-status', 'incident-group',
                  'participant-mode', 'workspace', 'routing-service', 'messages', 'incident-delete'),
    'FRIEND': ('public-accept', 'protected-accept', 'protected-reject', 'list', 'cli-accept'),
    'CLI-FILE': ('setup', 'upload', 'list', 'capabilities', 'metadata', 'download', 'share',
                 'shared-metadata', 'shared-content', 'delete'),
}
REQUIRED_ROLES = {'WORKSPACE': {'CEO', 'PM', 'ENG', 'QA'},
                  'FRIEND': {'CEO', 'PM', 'ENG', 'QA', 'CS'}, 'CLI-FILE': {'PM', 'ENG'}}
_FUNCTION = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def validate_contract(value: dict, *, source_root: Path) -> dict:
    """Validate the finite planning contract and its whole-file source binding."""
    try:
        if (set(value) != {'schema_version', 'source_revision', 'sources', 'stories'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['source_revision'], str)
                or re.fullmatch('[0-9a-f]{40}', value['source_revision']) is None
                or not isinstance(value['sources'], dict) or set(value['sources']) != SOURCES
                or not isinstance(value['stories'], list)
                or [s['legacy_id'] for s in value['stories']] != list(STORIES)):
            raise ValueError
        texts = {}
        if source_root.is_symlink() or not source_root.is_dir():
            raise ValueError
        for name, digest in value['sources'].items():
            path = source_root / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise RunStateError('legacy Story source drift requires an explicit migration review')
            texts[name] = raw.decode('utf-8')
        for story in value['stories']:
            if (set(story) != {'legacy_id', 'migration_status', 'required_bot_roles', 'steps',
                              'requirements', 'capability_gaps', 'corrections'}
                    or story['migration_status'] != 'NOT_MIGRATED'):
                raise ValueError
            for key in ('required_bot_roles', 'capability_gaps', 'corrections'):
                items = story[key]
                if (not isinstance(items, list) or not 1 <= len(items) <= 40
                        or any(not isinstance(item, str) or not 1 <= len(item) <= 512 for item in items)
                        or len(set(items)) != len(items)):
                    raise ValueError
            steps = story['steps']
            if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
                raise ValueError
            names = []
            for step in steps:
                if (set(step) != {'function', 'source'} or step['source'] not in SOURCES
                        or not isinstance(step['function'], str) or not _FUNCTION.fullmatch(step['function'])):
                    raise ValueError
                name = step['function']
                if len(re.findall(r'^' + re.escape(name) + r'\(\) \{$', texts[step['source']], re.M)) != 1:
                    raise ValueError
                names.append(name)
            if len(set(names)) != len(names):
                raise ValueError
            # These two reviewed roots contain plain direct helper calls. This
            # is not a general Shell parser; whole-file hashes also cover nested
            # helpers, wrappers, conditional assertions and manual counters.
            if story['legacy_id'] != 'story_session_file_workspace':
                text = texts['stories.sh']
                start = text.index(story['legacy_id'] + '() {')
                end = text.index('\n}', start)
                actual = re.findall(r'^    ((?:test_|_story_)[A-Za-z0-9_]+)(?:\s|$)', text[start:end], re.M)
                if actual != names:
                    raise RunStateError('migration map omits, reorders or substitutes a parent Story step')
            elif names != ['story_session_file_workspace']:
                raise ValueError
            requirements = story['requirements']
            if not isinstance(requirements, list) or not 1 <= len(requirements) <= 100:
                raise ValueError
            ids = []
            for item in requirements:
                if (set(item) != {'id', 'expectation', 'functions'}
                        or not isinstance(item['id'], str)
                        or not re.fullmatch(STORIES[story['legacy_id']] + r'\.[A-Za-z0-9_-]+', item['id'])
                        or not isinstance(item['expectation'], str) or not 1 <= len(item['expectation']) <= 1024
                        or not isinstance(item['functions'], list) or not item['functions']
                        or any(not isinstance(fn, str) or fn not in names for fn in item['functions'])
                        or len(set(item['functions'])) != len(item['functions'])):
                    raise ValueError
                ids.append(item['id'])
            prefix = STORIES[story['legacy_id']]
            if (set(ids) != {prefix + '.' + name for name in REQUIRED_IDS[prefix]}
                    or set(story['required_bot_roles']) != REQUIRED_ROLES[prefix]):
                raise RunStateError('migration plan omits or invents required business obligations or roles')
            if len(set(ids)) != len(ids) or set(names) != {fn for item in requirements for fn in item['functions']}:
                raise RunStateError('every original business helper needs a unique scoped migration obligation')
        return json.loads(json.dumps(value))
    except (KeyError, TypeError, ValueError, OSError, UnicodeError):
        raise RunStateError('migration contract is malformed or source binding is unavailable') from None


def migration_plan() -> dict:
    """Normal plan output must not mistake a pilot slice for a whole Story."""
    contract = validate_contract(read(ROOT / 'migration-contracts.json'), source_root=ROOT.parent / 'e2e-test')
    from .inventory import current_manifest
    inventory = current_manifest()['legacy_inventory']
    for story in contract['stories']:
        item = next((item for item in inventory if item['id'] == story['legacy_id']), None)
        if item is None or item.get('migration_status') != 'NOT_MIGRATED':
            raise RunStateError('migration plan and release inventory disagree; review actual executor coverage')
    local_manifest = current_manifest('owned-local-trusted-team-v1')
    local_case = local_manifest['cases'][0]
    from .local_file_story import SUITE as FILE_SUITE, CASE_ID as FILE_CASE, REQUIREMENTS as FILE_REQUIREMENTS
    from .local_workspace_run import CASE_ID as WORKSPACE_CASE, REQUIREMENTS as WORKSPACE_REQUIREMENTS, CLI_REQUIREMENTS as WORKSPACE_CLI_REQUIREMENTS, INCIDENT_REQUIREMENTS as WORKSPACE_INCIDENT_REQUIREMENTS, MESSAGE_REQUIREMENTS as WORKSPACE_MESSAGE_REQUIREMENTS, DELETE_REQUIREMENTS as WORKSPACE_DELETE_REQUIREMENTS
    from .local_channel_public_contract import channel_bindings_adaptability
    from .local_network_actor_status_contract import network_actor_status_adaptability
    from .local_friend_connection_decision_contract import friend_connection_decision_adaptability
    from .local_friend_public_accept_contract import friend_public_accept_adaptability
    from .local_friend_cli_accept_contract import friend_cli_accept_adaptability
    from .workspace_cli_create_get_contract import workspace_cli_create_get_adaptability
    from .workspace_cli_status_contract import workspace_cli_status_adaptability
    from .workspace_cli_list_contract import workspace_cli_list_adaptability
    from .workspace_cli_detail_contract import workspace_cli_detail_adaptability
    from .workspace_cli_fuse_contract import workspace_cli_fuse_adaptability
    from .workspace_cli_member_contract import (
        workspace_cli_member_adaptability,
    )
    from .workspace_group_lifecycle_contract import (
        workspace_group_lifecycle_adaptability,
    )
    from .workspace_group_governance_contract import (
        workspace_group_governance_adaptability,
    )
    from .workspace_incident_core_contract import (
        workspace_incident_core_adaptability,
    )
    from .workspace_roundtrip_contract import (
        workspace_roundtrip_adaptability,
    )
    from .session_file_core_contract import (
    session_cli_bot_groups_adaptability,
    session_cli_create_get_adaptability,
    session_file_core_adaptability,
)
    from .session_cli_collection import (
        session_cli_collection_adaptability,
    )
    from .session_cli_complete_invoke import (
        session_cli_complete_invoke_adaptability,
    )
    from .local_session_service_cli import (
        SESSION_SERVICE_LIFECYCLE_CASE_ID as SESSION_SERVICE_LIFECYCLE_CASE,
        local_session_service_lifecycle_adaptability,
        SESSION_SERVICE_LIFECYCLE_PARENT as SESSION_SERVICE_LIFECYCLE_PARENT,
        SESSION_SERVICE_LIFECYCLE_REQUIREMENT as SESSION_SERVICE_LIFECYCLE_REQUIREMENT,
        SESSION_SERVICE_MEMBER_CHAT_CASE_ID as SESSION_SERVICE_MEMBER_CHAT_CASE,
        SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT as SESSION_SERVICE_MEMBER_CHAT_REQUIREMENT,
        local_session_service_member_chat_adaptability,
    )
    from .local_session_service_completion import (
        SESSION_SERVICE_ASSERTION_IDS,
        SESSION_SERVICE_COMPLETE_CASE_ID as SESSION_SERVICE_COMPLETE_CASE,
        SESSION_SERVICE_COMPLETE_REQUIREMENT as SESSION_SERVICE_COMPLETE_REQUIREMENT,
        SESSION_SERVICE_ROUNDTRIP_CASE_ID as SESSION_SERVICE_ROUNDTRIP_CASE,
        SESSION_SERVICE_ROUNDTRIP_REQUIREMENT as SESSION_SERVICE_ROUNDTRIP_REQUIREMENT,
        local_session_service_complete_adaptability,
        local_session_service_roundtrip_adaptability,
    )
    from .local_session_service_parent_case import (
        PARENT_CASE_ID as SESSION_SERVICE_PARENT_CASE,
        PARENT_CASE_SUITE as SESSION_SERVICE_PARENT_CASE_SUITE,
        session_service_parent_adaptability,
    )
    from .session_service_cli_case_run import (
        dual_session_service_lifecycle_adaptability,
        dual_session_service_member_chat_adaptability,
        dual_session_service_complete_adaptability,
        dual_session_service_roundtrip_adaptability,
    )
    from .session_cli_shared import (
        session_cli_invite_join_adaptability,
        session_cli_lifecycle_adaptability,
    )
    from .workspace_cli_runner import session_cli_group_invite_adaptability, session_cli_session_invite_adaptability
    from .local_network_contract import (
        network_actor_list_adaptability, network_actor_search_adaptability,
        network_bots_mine_adaptability, network_bots_paged_adaptability,
        network_bots_query_adaptability,
        network_platform_asset_adaptability,
        network_source_binding,
        SUITE as NETWORK_SUITE, CASE_ID as NETWORK_CASE,
    )
    from .local_network_register_contract import network_register_adaptability
    from .remote_channel_cli_adapter import channel_cli_adaptability
    from .remote_operator_cli_adapter import operator_cli_adaptability
    from .direct_chat_cli_case_run import dual_direct_chat_adaptability
    from .delivery_policy_cli_case_run import delivery_policy_adaptability
    from .provider_subbots_case_run import provider_subbots_adaptability
    from .local_webhook_subscription_contract import webhook_subscription_adaptability
    from .local_structured_public_contract import structured_templates_adaptability
    structured_templates = structured_templates_adaptability()
    network = network_source_binding()
    from .local_session_contract import session_source_binding, PUBLIC_CASE, PUBLIC_SUITE
    sessions = session_source_binding()
    from .local_provider_contract import provider_source_binding
    provider = provider_source_binding()
    from .local_channel_contract import channel_source_binding
    from .local_delivery_policy_contract import delivery_policy_source_binding
    channel = channel_source_binding()
    delivery_policy = delivery_policy_source_binding()
    from .local_channel_cli_contract import channel_cli_source_binding
    channel_cli = channel_cli_source_binding()
    from .local_operator_cli_contract import (collaboration_team_source_binding,
                                                 custom_collaboration_source_binding,
                                                 operator_cli_source_binding,
                                                 sessions_services_source_binding)
    operator_cli = operator_cli_source_binding()
    custom_collaboration = custom_collaboration_source_binding()
    from .local_custom_collaboration_case import (CASE_ID as CUSTOM_COLLABORATION_CASE,
                                                  REQUIREMENTS as CUSTOM_COLLABORATION_REQUIREMENT_IDS,
                                                  SUITE as CUSTOM_COLLABORATION_SUITE,
                                                  custom_collaboration_case_adaptability)
    from .custom_collaboration_cli_case_run import custom_collaboration_cli_adaptability
    collaboration_team = collaboration_team_source_binding()
    sessions_services = sessions_services_source_binding()
    from .local_direct_chat_contract import direct_chat_source_binding
    direct_chat = direct_chat_source_binding()
    from .local_structured_contract import structured_source_binding
    structured = structured_source_binding()
    from .local_session_public_case import session_public_adaptability
    from .local_event_contract import event_source_binding
    webhook = event_source_binding()
    from .local_judge_contract import CASE_ID as SLOW_JUDGE_CASE, SUITE as SLOW_JUDGE_SUITE, judge_source_binding
    judge = judge_source_binding()
    from .case_rewrite import fixed_loop_case_rewrite
    fixed_loop_rewrite = fixed_loop_case_rewrite()
    from .case_rewrite import slow_judge_case_rewrite
    slow_judge_rewrite = slow_judge_case_rewrite()
    from .local_fixed_loop_contract import (fixed_loop_source_binding,
                                              CASE_ID as FIXED_LOOP_CASE,
                                              SUITE as FIXED_LOOP_SUITE)
    fixed_loop = fixed_loop_source_binding()
    from .edge_read_contract import (admission_source_binding, edge_read_adaptability,
                                     friend_requests_list_source_binding,
                                     friend_connections_list_source_binding)
    from .edge_read_case import (
        admission_public_adaptability,
        friend_connections_public_adaptability,
        friend_requests_public_adaptability,
        edge_read_case_plans,
    )
    from .collaboration_team_contract import CASE_ID as COLLABORATION_TEAM_CASE, SUITE as COLLABORATION_TEAM_SUITE, collaboration_team_adaptability
    from .group_context_contract import (
        CASE_ID as GROUP_CONTEXT_CASE_ID,
        PARENT as GROUP_CONTEXT_PARENT,
        SUITE as GROUP_CONTEXT_SUITE,
        group_context_adaptability,
        group_context_source_binding,
    )
    from .edge_search_case import (
        CASE_ID as EDGE_SEARCH_CASE_ID,
        edge_bot_search_candidates_adaptability,
        edge_search_adaptability,
        edge_search_case_plan,
    )
    from .edge_search_contract import bot_search_source_binding
    bot_search = bot_search_source_binding()
    from .edge_friend_mutation_case import (BACKLOG_CASE_IDS as EDGE_FRIEND_BACKLOG_CASE_IDS,
                                             RELEASE_CASE_IDS as EDGE_FRIEND_RELEASE_CASE_IDS,
                                             CASE_IDS as EDGE_FRIEND_MUTATION_CASE_IDS,
                                             edge_friend_mutation_case_plan,
                                             edge_friend_mutation_source_binding)
    edge_friend_mutation_maps = [edge_friend_mutation_source_binding(case_id)
                                 for case_id in EDGE_FRIEND_MUTATION_CASE_IDS]
    edge_friend_release_plans = {case_id: edge_friend_mutation_case_plan(case_id)
                                 for case_id in EDGE_FRIEND_RELEASE_CASE_IDS}
    edge_friend_backlog_plans = {case_id: edge_friend_mutation_case_plan(case_id)
                                 for case_id in EDGE_FRIEND_BACKLOG_CASE_IDS}
    edge_search_plan = edge_search_case_plan()
    from .local_caller_execution_contract import (CASE_ID as CALLER_CASE, REQUIREMENTS as CALLER_REQUIREMENTS,
                                                   SUITE as CALLER_SUITE, caller_execution_source_binding)
    caller_execution = caller_execution_source_binding()
    from .local_draft_group_contract import (CASE_ID as DRAFT_CASE_ID, REQUIREMENTS as DRAFT_REQUIREMENTS,
                                               SUITE as DRAFT_SUITE, draft_group_source_binding)
    from .local_result_queue_contract import (CASE_ID as RESULT_QUEUE_CASE_ID,
                                               REQUIREMENTS as RESULT_QUEUE_REQUIREMENTS,
                                               SUITE as RESULT_QUEUE_SUITE,
                                               result_queue_source_binding)
    from .local_nested_fixed_loop_contract import (CASE_ID as NESTED_FIXED_LOOP_CASE_ID,
                                                   REQUIREMENTS as NESTED_FIXED_LOOP_REQUIREMENTS,
                                                   SUITE as NESTED_FIXED_LOOP_SUITE,
                                                   nested_fixed_loop_source_binding)
    from .local_service_policy_contract import (CASE_ID as POLICY_CASE_ID,
                                                REQUIREMENTS as POLICY_REQUIREMENTS,
                                                SUITE as POLICY_SUITE,
                                                service_policy_source_binding)
    from .local_authority_contract import (CASE_ID as AUTHORITY_CASE_ID,
                                            REQUIREMENTS as AUTHORITY_REQUIREMENTS,
                                            SUITE as AUTHORITY_SUITE,
                                            authority_source_binding)
    from .session_owner.session_owner_contract import (
        CASE_ID as SESSION_OWNER_CASE_ID,
        REQUIREMENTS as SESSION_OWNER_REQUIREMENTS,
        SUITE as SESSION_OWNER_SUITE,
        session_owner_source_binding,
    )
    from .draft_delivery.contract import (
        CASE_ID as DRAFT_DELIVERY_CASE_ID,
        REQUIREMENTS as DRAFT_DELIVERY_REQUIREMENTS,
        SUITE as DRAFT_DELIVERY_SUITE,
        draft_delivery_source_binding,
    )
    from .draft_delivery.dual_case_run import draft_delivery_adaptability
    from .provider_owner_contract import (
        CASE_ID as PROVIDER_OWNER_CASE_ID,
        REQUIREMENTS as PROVIDER_OWNER_REQUIREMENTS,
        SUITE as PROVIDER_OWNER_SUITE,
        provider_owner_source_binding,
    )
    draft_group = draft_group_source_binding()
    caller_execution_slice = {'suite': CALLER_SUITE, 'case_id': CALLER_CASE, 'case_version': 1,
        'legacy_id': 'caller_execution_story', 'status': 'LOCAL_EXECUTOR_ONLY',
        'required_assertion_count': 9, 'requirement_ids': list(CALLER_REQUIREMENTS),
        'canonical_reporting': 'LOCAL_RUNTIME_BLACKBOX',
        'execution_command': 'local-caller-execution-run', 'report_command': 'local-caller-execution-report',
        'required_parameters': ['binary', 'binary_sha256', 'state_dir', 'run_id', 'confirm_run_id',
                                'allow_local_processes', 'allow_fixture_write', 'allow_write'],
        'binary_kind': 'bcs-caller-e2e-test-utils', 'auto_build': False,
        'environment': {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'},
        'release_parent_denominator': False, 'independent_slice': True,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'caller-execution-migration-contract.json'}
    draft_group_slice = {'suite': DRAFT_SUITE, 'case_id': DRAFT_CASE_ID, 'case_version': 1,
        'legacy_id': 'collaboration_draft_group_story', 'status': 'LOCAL_EXECUTOR_ONLY',
        'required_assertion_count': 17, 'requirement_ids': list(DRAFT_REQUIREMENTS),
        'canonical_reporting': 'LOCAL_RUNTIME_BLACKBOX',
        'execution_command': 'local-draft-group-run', 'report_command': 'local-draft-group-report',
        'required_parameters': ['binary', 'binary_sha256', 'state_dir', 'run_id', 'confirm_run_id',
                                'allow_local_processes', 'allow_fixture_write', 'allow_write'],
        'binary_kind': 'bcs', 'auto_build': False,
        'environment': {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'},
        'release_parent_denominator': False, 'independent_slice': True,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'draft-group-migration-contract.json'}
    result_queue_slice = {'suite': RESULT_QUEUE_SUITE, 'case_id': RESULT_QUEUE_CASE_ID, 'case_version': 1,
        'legacy_id': 'story_state_machine_result_queue', 'status': 'LOCAL_EXECUTOR_ONLY',
        'required_assertion_count': 18, 'requirement_ids': list(RESULT_QUEUE_REQUIREMENTS),
        'canonical_reporting': 'LOCAL_RUNTIME_BLACKBOX',
        'execution_command': 'local-result-queue-run', 'report_command': 'local-result-queue-report',
        'required_parameters': ['binary', 'binary_sha256', 'state_dir', 'run_id', 'confirm_run_id',
                                'allow_local_processes', 'allow_fixture_write', 'allow_write'],
        'binary_kind': 'bcs', 'auto_build': False,
        'environment': {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'},
        'release_parent_denominator': False, 'independent_slice': True,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'result-queue-migration-contract.json'}
    nested_fixed_loop_slice = {'suite': NESTED_FIXED_LOOP_SUITE,
        'case_id': NESTED_FIXED_LOOP_CASE_ID, 'case_version': 1,
        'legacy_id': 'story_nested_fixed_loop_workflows', 'status': 'LOCAL_EXECUTOR_ONLY',
        'required_assertion_count': 16,
        'requirement_ids': list(NESTED_FIXED_LOOP_REQUIREMENTS),
        'canonical_reporting': 'LOCAL_RUNTIME_BLACKBOX',
        'execution_command': 'local-nested-fixed-loop-run',
        'report_command': 'local-nested-fixed-loop-report',
        'required_parameters': ['binary', 'binary_sha256', 'cli_binary', 'driver',
                                'state_dir', 'run_id', 'confirm_run_id',
                                'allow_local_processes', 'allow_fixture_write', 'allow_write'],
        'binary_kind': 'bcs', 'auto_build': False,
        'environment': {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'},
        'release_parent_denominator': False, 'independent_slice': True,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'nested-fixed-loop-migration-contract.json'}
    service_policy_slice = {'suite': POLICY_SUITE, 'case_id': POLICY_CASE_ID, 'case_version': 1,
        'legacy_id': 'story_retired_service_policy', 'status': 'LOCAL_EXECUTOR_ONLY',
        'required_assertion_count': 8, 'requirement_ids': list(POLICY_REQUIREMENTS),
        'canonical_reporting': 'INTEGRATED_LOCAL',
        'execution_command': 'local-service-policy-run',
        'report_command': 'local-service-policy-report',
        'required_parameters': ['binary', 'binary_sha256', 'state_dir', 'run_id', 'confirm_run_id',
                                'allow_local_processes', 'allow_fixture_write', 'allow_write'],
        'binary_kind': 'bcs', 'auto_build': False,
        'environment': {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'},
        'release_parent_denominator': True, 'independent_slice': False,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'service-policy-migration-contract.json'}
    authority_slice = {'suite': AUTHORITY_SUITE, 'case_id': AUTHORITY_CASE_ID, 'case_version': 1,
        'legacy_id': 'story_authority_user_journey', 'status': 'LOCAL_EXECUTOR_ONLY',
        'required_assertion_count': 16, 'requirement_ids': list(AUTHORITY_REQUIREMENTS),
        'canonical_reporting': 'INTEGRATED_LOCAL',
        'execution_command': 'local-authority-run',
        'report_command': 'local-authority-report',
        'required_parameters': ['binary', 'binary_sha256', 'state_dir', 'run_id', 'confirm_run_id',
                                'allow_local_processes', 'allow_fixture_write', 'allow_write'],
        'binary_kind': 'bcs', 'auto_build': False,
        'environment': {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_UNSUPPORTED'},
        'release_parent_denominator': True, 'independent_slice': False,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'authority-migration-contract.json'}
    service_policy = service_policy_source_binding()
    authority = authority_source_binding()
    session_owner = session_owner_source_binding()
    draft_delivery = draft_delivery_source_binding()
    provider_owner = provider_owner_source_binding()
    group_context = group_context_source_binding()
    session_owner_slice = {
        'suite': SESSION_OWNER_SUITE, 'case_id': SESSION_OWNER_CASE_ID, 'case_version': 1,
        'legacy_id': session_owner['parent'], 'status': 'LOCAL_EXECUTOR_ONLY_BACKLOG',
        'required_assertion_count': len(SESSION_OWNER_REQUIREMENTS),
        'requirement_ids': list(SESSION_OWNER_REQUIREMENTS),
        'canonical_reporting': 'DUAL_CASE_RECEIPT',
        'execution_command': 'dual-session-owner-run',
        'report_command': '',
        'required_parameters': ['profile', 'binary', 'binary_sha256', 'cli_binary',
                                'cli_binary_sha256', 'state_dir', 'run_id',
                                'confirm_run_id', 'allow_write'],
        'binary_kind': 'bcs', 'auto_build': False,
        'environment': session_owner['environment'],
        'release_parent_denominator': False, 'independent_slice': True,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'session-owner-permissions-migration-contract.json',
    }
    draft_delivery_slice = {
        'suite': DRAFT_DELIVERY_SUITE, 'case_id': DRAFT_DELIVERY_CASE_ID,
        'case_version': 1, 'legacy_id': draft_delivery['parent'],
        'status': 'LOCAL_EXECUTOR_ONLY_BACKLOG',
        'required_assertion_count': len(DRAFT_DELIVERY_REQUIREMENTS),
        'requirement_ids': list(DRAFT_DELIVERY_REQUIREMENTS),
        'canonical_reporting': 'DUAL_CASE_RECEIPT',
        'execution_command': 'dual-draft-delivery-run',
        'report_command': '',
        'required_parameters': ['profile', 'binary', 'binary_sha256', 'cli_binary',
                                'cli_binary_sha256', 'state_dir',
                                'run_id', 'confirm_run_id', 'allow_write'],
        'binary_kind': 'bcs', 'auto_build': False,
        'environment': draft_delivery['environment'],
        'release_parent_denominator': False, 'independent_slice': True,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'draft-delivery-migration-contract.json',
    }
    provider_owner_slice = {
        'suite': PROVIDER_OWNER_SUITE, 'case_id': PROVIDER_OWNER_CASE_ID,
        'case_version': 1, 'legacy_id': provider_owner['parent'],
        'status': 'LOCAL_EXECUTOR_ONLY_BACKLOG',
        'required_assertion_count': len(PROVIDER_OWNER_REQUIREMENTS),
        'requirement_ids': list(PROVIDER_OWNER_REQUIREMENTS),
        'canonical_reporting': 'DUAL_CASE_RECEIPT',
        'execution_command': 'dual-provider-owner-run',
        'report_command': '',
        'required_parameters': ['profile', 'binary', 'binary_sha256', 'cli_binary',
                                'cli_binary_sha256', 'state_dir', 'run_id',
                                'confirm_run_id', 'allow_write'],
        'binary_kind': 'bcs', 'auto_build': False,
        'environment': provider_owner['environment'],
        'release_parent_denominator': False, 'independent_slice': True,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'provider-owner-isolation-migration-contract.json',
    }
    group_context_slice = {
        'suite': GROUP_CONTEXT_SUITE, 'case_id': GROUP_CONTEXT_CASE_ID,
        'case_version': 1, 'legacy_id': GROUP_CONTEXT_PARENT,
        'status': 'LOCAL_EXECUTOR_ONLY_BACKLOG',
        'required_assertion_count': len(group_context['requirements']),
        'requirement_ids': [row['id'] for row in group_context['requirements']],
        'canonical_reporting': 'DUAL_CASE_RECEIPT',
        'execution_command': 'dual-group-context-run',
        'report_command': '',
        'required_parameters': ['profile', 'cli_binary', 'cli_binary_sha256',
                                'state_dir', 'run_id', 'confirm_run_id',
                                'allow_write'],
        'binary_kind': 'bcs-cli', 'auto_build': False,
        'environment': {'local': 'LOCAL_EXECUTOR_ONLY',
                        'preprod': 'PROFILE_ASSET_PRESENT_NOT_RUN'},
        'release_parent_denominator': False, 'independent_slice': True,
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'source_map': 'group-context-migration-contract.json',
    }
    admission = admission_source_binding()
    friend_requests = friend_requests_list_source_binding()
    friend_connections = friend_connections_list_source_binding()
    edge_cases = edge_read_case_plans()
    return {'local_registered_executors': [{'suite':local_manifest['suite'], 'case_id':local_case['id'],
            'case_version':local_case['version'], 'legacy_id':local_case['legacy_id'],
            'status':'LOCAL_EXECUTOR_ONLY', 'required_assertion_count':len(local_case['assertions']),
            'requirement_ids':list(local_case['requirements']), 'canonical_reporting':'INTEGRATED_LOCAL',
            'programmatic_execution_contract':PROGRAMMATIC_EXECUTION_CONTRACT,
            'remote_validation':'NOT_RUN', 'grants_release_credit':False},
            {'suite':FILE_SUITE, 'case_id':FILE_CASE, 'case_version':1,
             'legacy_id':'story_session_file_workspace', 'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(FILE_REQUIREMENTS),
             'requirement_ids':['CLI-FILE.'+name for name in FILE_REQUIREMENTS],
             'canonical_reporting':'INTEGRATED_LOCAL', 'execution_command':'local-file-run',
             'report_command':'local-file-report', 'remote_validation':'NOT_RUN', 'grants_release_credit':False},
            {'suite':'owned-local-workspace-v1','case_id':'LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01','case_version':1,
             'legacy_id':'story_user_operates_group_workspace','status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':17,
             'requirement_ids':list(WORKSPACE_REQUIREMENTS+WORKSPACE_CLI_REQUIREMENTS+WORKSPACE_INCIDENT_REQUIREMENTS+WORKSPACE_MESSAGE_REQUIREMENTS+WORKSPACE_DELETE_REQUIREMENTS),
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-workspace-run --with-incident-delete',
             'report_command':'local-workspace-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':NETWORK_SUITE,'case_id':NETWORK_CASE,'case_version':1,
             'legacy_id':network['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(network['requirements']),
             'requirement_ids':[row['id'] for row in network['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-network-run',
             'report_command':'local-network-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-session-flow-v1','case_id':'LOCAL-SESSION-FLOW-01','case_version':1,
             'legacy_id':sessions['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':87,'requirement_ids':[row['id'] for row in sessions['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-session-run',
             'report_command':'local-session-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-provider-v1','case_id':'LOCAL-PROVIDER-01','case_version':1,
             'legacy_id':provider['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':32,'requirement_ids':[row['id'] for row in provider['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-provider-run',
             'report_command':'local-provider-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-channel-http-v1','case_id':'LOCAL-CHANNEL-HTTP-01','case_version':1,
             'legacy_id':channel['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':6,'requirement_ids':[row['id'] for row in channel['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-channel-http-run',
             'report_command':'local-channel-http-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-delivery-policy-v1','case_id':'LOCAL-DELIVERY-POLICY-01','case_version':1,
             'legacy_id':delivery_policy['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(delivery_policy['requirements']),
             'requirement_ids':[row['id'] for row in delivery_policy['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-delivery-policy-run',
             'report_command':'local-delivery-policy-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-channel-cli-v1','case_id':'LOCAL-CHANNEL-CLI-01','case_version':1,
             'legacy_id':channel_cli['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':9,'requirement_ids':[row['id'] for row in channel_cli['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-channel-cli-run',
             'report_command':'local-channel-cli-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-operator-cli-v1','case_id':'LOCAL-OPERATOR-CLI-01','case_version':1,
             'legacy_id':operator_cli['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(operator_cli['requirements']),
             'requirement_ids':[row['id'] for row in operator_cli['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-operator-cli-run',
             'report_command':'local-operator-cli-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':COLLABORATION_TEAM_SUITE,'case_id':COLLABORATION_TEAM_CASE,'case_version':1,
             'legacy_id':collaboration_team['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(collaboration_team['requirements']),
             'requirement_ids':[row['id'] for row in collaboration_team['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-collaboration-team-run',
             'report_command':'local-collaboration-team-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':CUSTOM_COLLABORATION_SUITE,'case_id':CUSTOM_COLLABORATION_CASE,'case_version':1,
             'legacy_id':custom_collaboration['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(CUSTOM_COLLABORATION_REQUIREMENT_IDS),
             'requirement_ids':list(CUSTOM_COLLABORATION_REQUIREMENT_IDS),
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'run_custom_collaboration_case',
             'remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':SESSION_SERVICE_PARENT_CASE_SUITE,'case_id':SESSION_SERVICE_PARENT_CASE,'case_version':1,
             'legacy_id':sessions_services['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(SESSION_SERVICE_ASSERTION_IDS),
             'requirement_ids':[row['id'] for row in sessions_services['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'execute_local_session_service_parent_case',
             'remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-direct-chat-v1','case_id':'LOCAL-DIRECT-CHAT-01','case_version':1,
             'legacy_id':direct_chat['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':5,'requirement_ids':[row['id'] for row in direct_chat['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-direct-chat-run',
             'report_command':'local-direct-chat-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-structured-v1','case_id':'LOCAL-STRUCTURED-01','case_version':1,
             'legacy_id':structured['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':17,'requirement_ids':[row['id'] for row in structured['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-structured-run',
             'report_command':'local-structured-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-event-webhook-v1','case_id':'LOCAL-EVENT-WEBHOOK-01','case_version':1,
             'legacy_id':webhook['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':20,'requirement_ids':[row['id'] for row in webhook['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-event-run',
             'report_command':'local-event-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':SLOW_JUDGE_SUITE,'case_id':SLOW_JUDGE_CASE,'case_version':1,
             'legacy_id':judge['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(slow_judge_rewrite.requirements),
             'requirement_ids':[row.requirement_id for row in slow_judge_rewrite.requirements],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-slow-judge-run',
             'report_command':'local-slow-judge-report','remote_validation':'NOT_RUN',
             'grants_release_credit':False},
            {'suite':'owned-local-edge-read-v1','case_id':'LOCAL-EDGE-ADMISSION-01','case_version':1,
             'legacy_id':admission['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(admission['requirements']),
             'requirement_ids':[row['id'] for row in admission['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL_SHARED_RUNTIME','execution_command':'local-edge-read-run',
             'report_command':'local-edge-read-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-edge-read-v1','case_id':'LOCAL-EDGE-FRIEND-REQUESTS-LIST-01','case_version':1,
             'legacy_id':friend_requests['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(friend_requests['requirements']),
             'requirement_ids':[row['id'] for row in friend_requests['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL_SHARED_RUNTIME','execution_command':'local-edge-read-run',
             'report_command':'local-edge-read-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-edge-read-v1',
             'case_id':'LOCAL-EDGE-FRIEND-CONNECTIONS-LIST-01','case_version':1,
             'legacy_id':friend_connections['parent'],'status':'LOCAL_READ_SLICE_ONLY',
             'required_assertion_count':len(friend_connections['requirements']),
             'requirement_ids':[row['id'] for row in friend_connections['requirements']],
             'canonical_reporting':'INTEGRATED_LOCAL_SHARED_RUNTIME','execution_command':'local-edge-read-run',
             'report_command':'local-edge-read-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':'owned-local-edge-read-v1','case_id':EDGE_SEARCH_CASE_ID,'case_version':1,
             'legacy_id':bot_search['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(edge_search_plan.requirements),
             'requirement_ids':list(edge_search_plan.requirement_ids),
             'canonical_reporting':'INTEGRATED_LOCAL_SHARED_RUNTIME','execution_command':'local-edge-read-run',
             'report_command':'local-edge-read-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            {'suite':FIXED_LOOP_SUITE,'case_id':FIXED_LOOP_CASE,'case_version':1,
             'legacy_id':fixed_loop['parent'],'status':'LOCAL_EXECUTOR_ONLY',
             'required_assertion_count':len(fixed_loop_rewrite.requirements),
             'requirement_ids':[row.requirement_id for row in fixed_loop_rewrite.requirements],
             'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-fixed-loop-run',
             'report_command':'local-fixed-loop-report','remote_validation':'NOT_RUN','grants_release_credit':False},
            *[{'suite':'owned-local-edge-friend-mutations-v1',
               'case_id':plan.case_id,'case_version':1,
               'legacy_id':plan.parent_id,'status':'LOCAL_EXECUTOR_ONLY',
               'required_assertion_count':len(plan.requirements),
               'requirement_ids':list(plan.requirement_ids),
               'canonical_reporting':'INTEGRATED_LOCAL','execution_command':'local-edge-friend-mutations-run',
               'report_command':'local-edge-friend-mutations-report','remote_validation':'NOT_RUN','grants_release_credit':False}
              for plan in edge_friend_release_plans.values()],
            service_policy_slice, authority_slice],
            'local_backlog_case_slices': [
                *[{'suite': 'owned-local-edge-friend-mutations-v1',
                   'case_id': plan.case_id, 'case_version': 1,
                   'legacy_id': plan.parent_id, 'status': 'LOCAL_EXECUTOR_ONLY_BACKLOG',
                   'required_assertion_count': len(plan.requirements),
                   'requirement_ids': list(plan.requirement_ids),
                   'canonical_reporting': 'INTEGRATED_LOCAL',
                   'execution_command': 'local-edge-friend-mutations-run',
                   'report_command': 'local-edge-friend-mutations-report',
                   'remote_validation': 'NOT_RUN',
                   'release_parent_denominator': False,
                   'independent_slice': True,
                   'grants_release_credit': False}
                  for plan in edge_friend_backlog_plans.values()],
                session_owner_slice, draft_delivery_slice, provider_owner_slice,
                group_context_slice],
            'local_partial_executors': [],
            'status': 'PLANNED_NOT_EXECUTABLE', 'ready_to_execute': False,
            'source_revision': contract['source_revision'], 'source_integrity': 'MATCHED',
            'legacy_inventory_count': len(inventory), 'mapped_story_count': len(contract['stories']),
            'migrated_story_count': 0, 'passed_assertion_count': 0,
            'scope': 'next three parent Stories only; requirements are not executed assertions',
            'stories': contract['stories'], 'additional_source_maps': [network, sessions, provider, channel, channel_cli, direct_chat, structured, webhook, judge, fixed_loop, admission, friend_requests, friend_connections, bot_search, delivery_policy, operator_cli, collaboration_team, sessions_services, service_policy, authority, session_owner, draft_delivery, provider_owner, group_context, *edge_friend_mutation_maps],
            'cross_environment_source_candidates': {
                **edge_read_adaptability(),
                'cases': edge_read_adaptability()['cases'] + edge_search_adaptability()['cases'],
            },
            'cross_environment_case_rewrites': [{
                'case_id': row.case_id, 'parent_id': row.parent_id, 'status': 'PENDING',
                'requirement_ids': list(row.requirement_ids),
                'adapter_methods': list(row.adapters.values()),
                'environments': [{'name': env.name, 'adapter_family': env.adapter_family,
                                  'status': env.status, 'blocker': env.blocker}
                                 for env in row.environments],
                'ready_to_execute': False, 'grants_release_credit': False,
            } for row in (*edge_cases, edge_search_plan)],
            'public_v1_case_slices': [network_bots_query_adaptability(),
                                    network_bots_mine_adaptability(),
                                    network_actor_list_adaptability(),
                                    network_actor_search_adaptability(),
                                    network_bots_paged_adaptability(),
                                    network_platform_asset_adaptability(),
                                    network_actor_status_adaptability(),
                                    network_register_adaptability(),
                                    webhook_subscription_adaptability(),
                                    channel_bindings_adaptability(),
                                    structured_templates,
                                    structured_core_adaptability(),
                                    session_public_adaptability(),
                                    admission_public_adaptability(),
                                    edge_bot_search_candidates_adaptability(),
                                    friend_requests_public_adaptability(),
                                    friend_connections_public_adaptability(),
                                    friend_connection_decision_adaptability(),
                                    friend_public_accept_adaptability()],
            'cross_environment_product_cli_case_slices': [
                friend_cli_accept_adaptability(),
                collaboration_team_adaptability(),
                group_context_adaptability(),
                custom_collaboration_case_adaptability(),
                custom_collaboration_cli_adaptability(),
                session_service_parent_adaptability(),
                local_session_service_lifecycle_adaptability(),
                local_session_service_member_chat_adaptability(),
                local_session_service_complete_adaptability(),
                local_session_service_roundtrip_adaptability(),
                dual_session_service_lifecycle_adaptability(),
                dual_session_service_member_chat_adaptability(),
                dual_session_service_complete_adaptability(),
                dual_session_service_roundtrip_adaptability(),
                workspace_cli_create_get_adaptability(),
                workspace_cli_status_adaptability(),
                workspace_cli_list_adaptability(),
                workspace_cli_detail_adaptability(),
                workspace_cli_fuse_adaptability(),
                session_file_core_adaptability(),
                session_cli_create_get_adaptability(),
                session_cli_bot_groups_adaptability(),
                session_cli_group_invite_adaptability(),
                session_cli_session_invite_adaptability(),
                session_cli_invite_join_adaptability(),
                session_cli_lifecycle_adaptability(),
                session_cli_complete_invoke_adaptability(),
                session_cli_collection_adaptability(),
                workspace_cli_member_adaptability(),
                workspace_group_lifecycle_adaptability(),
                workspace_group_governance_adaptability(),
                workspace_incident_core_adaptability(),
                workspace_roundtrip_adaptability(),
                channel_cli_adaptability(),
                operator_cli_adaptability(),
                dual_direct_chat_adaptability(),
                delivery_policy_adaptability(),
                provider_subbots_adaptability(),
                draft_delivery_adaptability()],
            'case_rewrite_plans': [{
                'case_id': slow_judge_rewrite.case_id,
                'parent_id': slow_judge_rewrite.parent_id,
                'status': 'PENDING',
                'requirement_ids': list(slow_judge_rewrite.requirement_ids),
                'requirements': [{
                    'id': row.requirement_id, 'kind': row.kind, 'method': row.method,
                    'path': row.path, 'actor': row.actor,
                    'adapter_method': row.adapter_method, 'expectation': row.expectation}
                    for row in slow_judge_rewrite.requirements],
                'adapter_method_count': len(slow_judge_rewrite.adapters),
                'environments': [{'name': row.name, 'adapter_family': row.adapter_family,
                                   'status': row.status, 'blocker': row.blocker}
                                   for row in slow_judge_rewrite.environments],
                'ready_to_execute': False,
                'grants_release_credit': False},
               {'case_id': fixed_loop_rewrite.case_id,
                'parent_id': fixed_loop_rewrite.parent_id,
                'status': 'PENDING',
                'requirement_ids': list(fixed_loop_rewrite.requirement_ids),
                'requirements': [{
                    'id': row.requirement_id, 'kind': row.kind, 'method': row.method,
                    'path': row.path, 'actor': row.actor,
                    'adapter_method': row.adapter_method, 'expectation': row.expectation}
                    for row in fixed_loop_rewrite.requirements],
                'adapter_method_count': len(fixed_loop_rewrite.adapters),
                'environments': [{'name': row.name, 'adapter_family': row.adapter_family,
                                   'status': row.status, 'blocker': row.blocker}
                                   for row in fixed_loop_rewrite.environments],
                'ready_to_execute': False,
                'grants_release_credit': False}],
            'local_independent_slices': [caller_execution_slice, draft_group_slice, result_queue_slice, nested_fixed_loop_slice]}
