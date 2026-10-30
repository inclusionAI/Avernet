"""Frozen dual admission contract for the slow-Judge provider callback slice.

This module freezes only the dual admission surface for
``story_provider_callback_survives_slow_judge``.  The owned-local suite id
(``owned-local-slow-judge-v1``) and every flat module stay unchanged; the loopback
Provider/Judge receiver is reused from ``bcs_e2e/local_judge_sandbox.py`` and never
rewritten.  The ledger MOCK gate row stays in ``executor-migration.json`` under
mainline ownership; this family only inherits it honestly.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..local_judge_contract import (PARENT, REQUIREMENTS, SUITE as SOURCE_SUITE,
                                    CASE_ID as SOURCE_CASE_ID, judge_source_binding)
from ..run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[2]
DUAL_SUITE = 'dual-slow-judge-v1'
DUAL_CASE_ID = 'DUAL-SLOW-JUDGE-01'
PROFILE_CONTRACT = 'dual-slow-judge-profile-v1'
REPORT_CONTRACT = 'dual-slow-judge-case-run-v1'
DUAL_CONTRACT_NAME = 'slow-judge-dual-migration-contract.json'
COMPLETION_CONTRACT = 'dual-slow-judge-completion-v1'
CASE_KEYS = ('slow-judge',)
COMMAND = 'dual-slow-judge-run'
OBLIGATION_COUNT = 22
MOCK_RECEIVER_LOCAL_ONLY = 'MOCK_RECEIVER_LOCAL_ONLY'
REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER = (
    'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER')
LIVE_BEARER_REPLAY = 'LIVE_BEARER_REPLAY'
PREPROD_MODES = (MOCK_RECEIVER_LOCAL_ONLY, REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER,
                 LIVE_BEARER_REPLAY)
MOCK_RECEIVER_ONLY_IDS = (
    'JUDGE.sandbox-empty', 'JUDGE.explicit-receipt-boundary',
    'JUDGE.publish-receipt-boundary', 'JUDGE.judge-started', 'JUDGE.judge-release')
PRE_ADMITTED_PROVIDER_IDS = (
    'JUDGE.provider-register', 'JUDGE.bot-publish', 'JUDGE.bot-public',
    'JUDGE.bot-retire')
LEAF_ROOT_NAME = '20261031-migration-w3a-s28-slow-judge-preprod'
DEFAULT_LEAF_ROOT = Path.home() / '.codex' / 'bcs-e2e' / LEAF_ROOT_NAME
DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
HEADER_ENV = {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}
ENV_REF = re.compile(r'^[A-Z][A-Z0-9_]{2,100}$')
MAX_CREDENTIAL_BYTES = 8192
STATION_BOT_ID = re.compile(r'^\d{8}_[a-z0-9]+:\d+$')
REDACTION_CANARIES = ('bot_token', 'X-BCS-Bot-Token', 'Bearer ', 'starpoint-data',
                      'x-proxypass-token')
REQUIREMENT_METHODS = (
    'ensure_empty_provider_sandbox', 'register_provider', 'publish_provider_bot',
    'publish_provider_bot_visibility', 'request_provider_bot_friendship',
    'provider_bot_friendship_list', 'create_judged_group',
    'bind_explicit_run_receipt_boundary', 'start_judged_run',
    'deliver_review_request', 'await_review_response',
    'bind_publish_receipt_boundary', 'submit_review_callback_before_deadline',
    'observe_judge_started', 'await_judging_state', 'read_judging_review_node',
    'release_owned_judge', 'deliver_publish_request', 'submit_publish_callback',
    'await_judged_run_completion', 'delete_judged_group', 'retire_provider_bot')
LOCAL_PROFILE_KEYS = frozenset({
    'schema_version', 'contract', 'environment', 'entrypoint', 'human', 'auth',
    'cli', 'policy', 'judge', 'effects'})
PREPROD_ACTOR_KEYS = frozenset({'driver', 'member'})
ENVIRONMENT = {'local': 'LOCAL_EXECUTOR_ONLY',
               'preprod': 'REMOTE_JOURNEY_SEAM_PENDING'}
IDENTITY_MODES = {'local': 'register', 'preprod': 'bearer'}
JUDGE_BLOCKS = {
    'local': {'kind': 'owned-loopback-receiver'},
    'preprod': {'kind': 'preprod-admitted-real-judge'},
}
EFFECTS_BLOCKS = {
    'local': {'disposable_groups': 1, 'disposable_provider_bots': 1,
              'provider_registration': 'OWNED_EPHEMERAL_REGISTERED_BY_RUN',
              'teardown': 'BUSINESS_DELETE_GROUP_AND_PROVIDER_BOT_RETIRE',
              'implicit_run': 'PRESERVED_EXPLICIT_RECEIPT_BOUNDARY'},
    'preprod': {'disposable_groups': 'SEAM_PENDING', 'disposable_provider_bots': 0,
                'provider_registration': 'FORBIDDEN_ON_SHARED_PREPROD',
                'teardown': 'PREPROD_JOURNEY_SEAM_PENDING',
                'implicit_run': 'PRESERVED_EXPLICIT_RECEIPT_BOUNDARY'},
}
CLI_COMMAND_FAMILY = 'judged-provider-callback'
CLI_BLOCKS = {
    'local': {'transport': 'owned-local-story',
              'command_family': CLI_COMMAND_FAMILY,
              'api_face': 'current'},
    'preprod': {'transport': 'product-http',
                'command_family': CLI_COMMAND_FAMILY,
                'api_face': 'current'},
}
ENTRYPOINT_KINDS = {'local': 'owned-local-story', 'preprod': 'bcs-direct'}
API_FACES = {'local': 'current', 'preprod': 'current'}
CLI_PAIR_STATUS = 'ADMITTED_FROZEN_NOT_CONSUMED_BY_THIS_WAVE'
PREPROD_JOURNEY = {
    'status': 'SEAM_PENDING_MAINLINE',
    'story_child': 'the frozen bearer slow-Judge journey child is not published in this wave',
    'credential_window': ('awaiting BCS_E2E_DRIVER_TOKEN and BCS_E2E_HUMAN_COOKIE '
                          'admission by the operator'),
    'network_requests_this_wave': 0,
    'release_credit': False,
}
REGISTRATION_PLANE = {
    'local': 'mock-admission headers plus one-time token registration',
    'preprod': REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER,
    'note': ('the preprod Provider pair and its provider-backed driver/member Bots are '
             'pre-admitted in the dedicated pool; the runner never registers Providers, '
             'Provider Bots or Humans on shared preprod'),
}
MOCK_GATE_INHERITANCE = {
    'ledger_gate': 'MOCK',
    'ledger_row': PARENT,
    'ledger_file': 'executor-migration.json',
    'gate_owner': ('the mainline owns the executor-migration row; this wave never edits '
                   'executor-migration.json and never flips the ledger row'),
    'local_leg': ('the owned-local leg executes under the inherited MOCK gate through the '
                  'reused owned loopback Provider/Judge receiver'),
    'receiver_module': 'bcs_e2e/local_judge_sandbox.py',
    'receiver_class': 'OwnedLocalJudgeSandbox',
    'receiver_reuse': 'reused-not-rewritten',
    'receiver_surfaces': ('provider-chat-send', 'provider-chat-inject',
                          'judge-openai-compatible-hold-release'),
    'preprod_leg': ('the preprod leg never consumes the receiver, resolves only named '
                    'credential references and opens zero sockets this wave'),
    'not_proven': ('remote authentication and authorization', 'real model reasoning',
                   'external engine termination', 'physical history erasure',
                   'remote release qualification'),
    'grants_release_credit': False,
}
OWNED_RUNTIME_BINARY = {
    'package': 'bcs',
    'hash_algorithm': 'sha256',
    'auto_build': False,
    'opt_in_env': ['BCS_E2E_TEST_BINARY', 'BCS_E2E_TEST_BINARY_SHA256'],
    'required_arguments': ['--binary', '--binary-sha256'],
    'role': 'owned-bcs-server',
    'admission': ('caller-supplied self-consistent executable/sha256 pair verified before '
                  'any state write; this family freezes no binary digest'),
}
CLI_BINARY_PAIR = {
    'status': CLI_PAIR_STATUS,
    'required_arguments': ['--cli-binary', '--cli-binary-sha256'],
    'consumption': ('admitted and digest-fenced when supplied; no leg consumes the author '
                    'CLI pair in this wave because the preprod journey child is pending'),
}
REQUIRED_PARAMETERS = ['profile', 'binary', 'binary_sha256', 'state_dir', 'run_id',
                       'confirm_run_id', 'allow_write']
OPTIONAL_PARAMETERS = ['cli_binary', 'cli_binary_sha256', 'leaf_root']
DUAL_ENTRY = 'bcs_e2e.slow_judge.dual_case_run.dual_slow_judge_case_run'
ADAPTABILITY_BLOCKER = (
    'Both legs share one frozen 22-obligation map: the owned-local leg replays the full '
    'journey on an owned runtime under the inherited ledger MOCK gate with the reused '
    'loopback Provider/Judge receiver, while the preprod leg is profile-admitted in bearer '
    'identity mode but fail-closes at journey admission with zero network requests until '
    'the mainline publishes the frozen bearer journey child and the operator admits the '
    'credential window. Dual execution is a migration receipt, not release credit.'
)


def preprod_obligation_modes() -> dict:
    """Freeze the honest preprod disposition of all 22 obligations."""
    modes = {requirement: MOCK_RECEIVER_LOCAL_ONLY for requirement in MOCK_RECEIVER_ONLY_IDS}
    modes.update({requirement: REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER
                  for requirement in PRE_ADMITTED_PROVIDER_IDS})
    for requirement in REQUIREMENTS:
        modes.setdefault(requirement, LIVE_BEARER_REPLAY)
    if (len(modes) != OBLIGATION_COUNT or set(modes) != set(REQUIREMENTS)
            or sorted(modes.values()).count(MOCK_RECEIVER_LOCAL_ONLY) != 5
            or sorted(modes.values()).count(REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER) != 4
            or sorted(modes.values()).count(LIVE_BEARER_REPLAY) != 13):
        raise RunStateError('slow-judge dual obligation mode map is invalid')
    return modes


def frozen_dual_contract() -> dict:
    """The exact content frozen in the repository dual contract JSON."""
    from ..case_rewrite import slow_judge_case_rewrite

    flat = judge_source_binding()
    plan = slow_judge_case_rewrite()
    modes = preprod_obligation_modes()
    if tuple(plan.adapters[row['id']] for row in flat['requirements']) != REQUIREMENT_METHODS:
        raise RunStateError('slow-judge adapter method map differs from the frozen methods')
    requirements = [
        {'id': row['id'], 'kind': row['kind'], 'method': row['method'],
         'path': row['path'], 'actor': row['actor'], 'expectation': row['expectation'],
         'adapter_method': plan.adapters[row['id']],
         'preprod_mode': modes[row['id']]}
        for row in flat['requirements']
    ]
    return {
        'schema_version': 1,
        'parent': flat['parent'],
        'source_suite': SOURCE_SUITE,
        'source_case_id': SOURCE_CASE_ID,
        'dual_suite': DUAL_SUITE,
        'dual_case_id': DUAL_CASE_ID,
        'obligation_prefix': 'JUDGE.',
        'environment': dict(ENVIRONMENT),
        'identity_modes': dict(IDENTITY_MODES),
        'story_checks': {'local': OBLIGATION_COUNT, 'preprod_bearer': 'SEAM_PENDING'},
        'requirements': requirements,
        'profile': {
            'contract': PROFILE_CONTRACT,
            'local_keys': sorted(LOCAL_PROFILE_KEYS),
            'local_actors': 'forbidden',
            'preprod_extra_keys': ['actors'],
            'preprod_actor_roles': sorted(PREPROD_ACTOR_KEYS),
            'actor_station_shape': 'dedicated-pool bot id <bot_prefix>:<user_id>',
        },
        'auth': {
            'local': 'owned-local',
            'preprod': 'preprod-driver-bot-bearer',
            'header_env': dict(HEADER_ENV),
            'cookie_reference': 'presence-validated and attached beside the driver Bot bearer on every station request (duo credential: Human cookie for station ACE admission; the value is never journaled)',
        },
        'judge_gate': {
            'local': dict(JUDGE_BLOCKS['local']),
            'preprod': dict(JUDGE_BLOCKS['preprod']),
            'note': ('only the owned-local leg admits the loopback receiver; the preprod '
                     'leg never creates, blocks or releases a judge'),
        },
        'registration_plane': dict(REGISTRATION_PLANE),
        'effects': {'local': dict(EFFECTS_BLOCKS['local']),
                    'preprod': dict(EFFECTS_BLOCKS['preprod'])},
        'mock_gate_inheritance': {
            **MOCK_GATE_INHERITANCE,
            'receiver_surfaces': list(MOCK_GATE_INHERITANCE['receiver_surfaces']),
            'not_proven': list(MOCK_GATE_INHERITANCE['not_proven']),
        },
        'owned_runtime_binary': dict(OWNED_RUNTIME_BINARY),
        'cli_binary_pair': dict(CLI_BINARY_PAIR),
        'preprod_journey': dict(PREPROD_JOURNEY),
        'dual_entry': {
            'module': DUAL_ENTRY,
            'required_parameters': list(REQUIRED_PARAMETERS),
            'optional_parameters': list(OPTIONAL_PARAMETERS),
            'state_dir_contract': ('parent exists, leaf is absent and neither is a '
                                   'symlink; the leaf is created by the entry'),
            'bounded_environment': 'fails closed before any socket',
            'placeholders': 'REPLACE_WITH_* is rejected before the state directory',
        },
        'redaction_tokens': list(REDACTION_CANARIES),
        'remote_validation': 'NOT_RUN',
        'preprod_verdict': 'NOT_RUN',
        'grants_release_credit': False,
        'release_parent_denominator': False,
        'independent_slice': True,
        'evidence_granularity': 'OWNED_CHILD_RECEIPT',
        'source_revision': flat['source_revision'],
        'sources': dict(flat['sources']),
    }


def slow_judge_dual_source_binding() -> dict:
    """Freeze and validate the dual contract JSON against this module."""
    try:
        value = read(ROOT / DUAL_CONTRACT_NAME)
        frozen = frozen_dual_contract()
        if value != frozen:
            raise ValueError
        flat = judge_source_binding()
        if (value['parent'] != flat['parent']
                or value['source_revision'] != flat['source_revision']
                or value['sources'] != flat['sources']):
            raise ValueError
        if [row['id'] for row in value['requirements']] != list(REQUIREMENTS):
            raise ValueError
        modes = preprod_obligation_modes()
        if [row['preprod_mode'] for row in value['requirements']] != [
                modes[row['id']] for row in value['requirements']]:
            raise ValueError
        if [row['adapter_method'] for row in value['requirements']] != list(REQUIREMENT_METHODS):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError('slow-judge dual source binding changed') from None


def slow_judge_dual_adaptability() -> dict:
    """Describe the dual surface without granting remote release credit."""
    modes = preprod_obligation_modes()
    return {
        'case_id': DUAL_CASE_ID,
        'source_case_id': SOURCE_CASE_ID,
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'requirement_count': OBLIGATION_COUNT,
        'preprod_obligation_modes': {
            'MOCK_RECEIVER_LOCAL_ONLY': sorted(requirement for requirement, mode
                                               in modes.items()
                                               if mode == MOCK_RECEIVER_LOCAL_ONLY),
            'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER': sorted(
                requirement for requirement, mode in modes.items()
                if mode == REMOTE_NOT_APPLICABLE_PRE_ADMITTED_PROVIDER),
            'LIVE_BEARER_REPLAY': sorted(requirement for requirement, mode in modes.items()
                                         if mode == LIVE_BEARER_REPLAY),
        },
        'environments': ['owned-local-full-story', 'preprod-bearer-journey-seam-pending'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'preprod_executor_status': 'PREPROD_JOURNEY_SEAM_PENDING',
        'required_assertion_count': OBLIGATION_COUNT,
        'local_story_checks': OBLIGATION_COUNT,
        'identity_modes': dict(IDENTITY_MODES),
        'mock_gate': dict(MOCK_GATE_INHERITANCE),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': ADAPTABILITY_BLOCKER,
    }
