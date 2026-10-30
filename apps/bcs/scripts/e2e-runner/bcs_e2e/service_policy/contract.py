"""Dual-slice contract for the Retired service policy parent Story.

The owned-local leg stays exactly the frozen black-box executor
(``local_service_policy_run`` bound to ``service-policy-migration-contract.json``);
this module freezes only the dual admission surface for the same parent:

- the same eight ``SERVICE-POLICY.*`` obligations in the same order;
- the station-wire route truths verified against ``apps/bcs`` ``router.rs``
  (the internal preprod deployment serves the same bcs-http router);
- the bearer-mode story check count (20 live HTTP checks: 1 driver identity
  confirmation + 19 business checks), distinct from the owned-local count 23;
- the disposable-resource policy (two Groups, three Sessions) and the
  never-register / never-delete-the-shared-driver-bot fences.

Dual execution is a migration receipt. Neither leg grants release credit and
neither leg claims remote validation until a live preprod bearer receipt exists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from ..local_service_policy_contract import (MARKER_PREFIX, MARKER_SUFFIX,
                                              PARENT, REQUIREMENTS, SUITE as SOURCE_SUITE,
                                              service_policy_source_binding)
from ..run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[2]
DUAL_SUITE = 'dual-service-policy-v1'
DUAL_CASE_ID = 'DUAL-SERVICE-POLICY-01'
PROFILE_CONTRACT = 'dual-service-policy-profile-v1'
REPORT_CONTRACT = 'dual-service-policy-case-run-v1'
DUAL_CONTRACT_NAME = 'service-policy-dual-migration-contract.json'
BEARER_STORY_CHECKS = 20
BEARER_IDENTITY_REQUIREMENT = REQUIREMENTS[0]
NOT_APPLICABLE_REQUIREMENT = REQUIREMENTS[1]
APPLICABLE_REQUIREMENTS = tuple(row for row in REQUIREMENTS if row != NOT_APPLICABLE_REQUIREMENT)
LEAF_ROOT_NAME = '20261031-migration-wps-service-policy-preprod'
DEFAULT_LEAF_ROOT = Path.home() / '.codex' / 'bcs-e2e' / LEAF_ROOT_NAME
DRIVER_ENV = 'BCS_E2E_DRIVER_TOKEN'
COOKIE_ENV = 'BCS_E2E_HUMAN_COOKIE'
HEADER_ENV = {'Authorization': DRIVER_ENV, 'Cookie': COOKIE_ENV}
ENV_REF = re.compile(r'^[A-Z][A-Z0-9_]{2,100}$')
MAX_CREDENTIAL_BYTES = 8192
REDACTION_CANARIES = ('bot_token', 'X-BCS-Bot-Token', 'Bearer ', 'starpoint-data',
                      'x-proxypass-token')
DRIVER_BOT_DELETE_DISPOSITION = 'SKIPPED_SHARED_DRIVER_BOT'
REGISTRATION_DISPOSITION = 'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER'
DRIVER_BOT_ID = re.compile(r'^\d{8}_[a-z0-9]+:\d+$')

# Station-wire truths, one row per frozen HTTP face exercised by the bearer
# leg. Verified against apps/bcs/crates/adapters/http/bcs-http/src/router.rs:
# /bots/{id} (get_bot), /groups (create_group), /groups/{id} (get_group),
# /groups/{id}/settings (patch_group_settings), /groups/{id}/sessions
# (create_session_for_group), /sessions/{sid} (get/delete),
# /sessions/{sid}/complete (complete_session), /services/{group_id}/sessions
# (post_invocation), /services/{group_id}/sessions/{session_id}
# (get_service_session).
STATION_ROUTES = (
    {'method': 'GET', 'path': '/bots/{driver_bot_id}', 'station_status': 200,
     'actor': 'driver', 'note': 'bearer identity confirmation for the pre-admitted driver Bot'},
    {'method': 'POST', 'path': '/groups', 'station_status': 200,
     'accepted_status': [200, 201], 'actor': 'driver',
     'note': 'legacy service_spec payload accepted while persisting nothing'},
    {'method': 'GET', 'path': '/groups/{group_id}', 'station_status': 200, 'actor': 'driver',
     'note': 'read-back proves the retired policy never persisted'},
    {'method': 'PATCH', 'path': '/groups/{group_id}/settings', 'station_status': 410,
     'actor': 'driver', 'error': 'service_policy_retired',
     'note': 'retired settings surface stays Gone'},
    {'method': 'POST', 'path': '/services/{group_id}/sessions', 'station_status': 202,
     'actor': 'driver', 'note': 'service_invocation Session creation'},
    {'method': 'GET', 'path': '/services/{group_id}/sessions/{session_id}',
     'station_status': 200, 'actor': 'driver', 'note': 'invocation input round-trip read'},
    {'method': 'POST', 'path': '/services/{group_id}/sessions', 'station_status': 409,
     'actor': 'driver', 'note': 'running-invocation reactivation guard'},
    {'method': 'POST', 'path': '/groups/{group_id}/sessions', 'station_status': 201,
     'actor': 'driver', 'note': 'chat Session creation for the conversion guard'},
    {'method': 'POST', 'path': '/sessions/{session_id}/complete', 'station_status': 200,
     'actor': 'driver', 'note': 'chat Session completion'},
    {'method': 'GET', 'path': '/sessions/{session_id}', 'station_status': 200,
     'actor': 'driver', 'note': 'completed chat Session read-back'},
    {'method': 'POST', 'path': '/services/{group_id}/sessions', 'station_status': 400,
     'actor': 'driver', 'error': 'not service_invocation',
     'note': 'completed chat Session conversion refusal'},
    {'method': 'DELETE', 'path': '/sessions/{session_id}?bot_id={driver_bot_id}',
     'station_status': 200, 'accepted_status': [200, 204], 'actor': 'driver',
     'note': 'disposable Session teardown'},
    {'method': 'DELETE', 'path': '/groups/{group_id}?bot_id={driver_bot_id}',
     'station_status': 200, 'accepted_status': [200, 204], 'actor': 'driver',
     'note': 'disposable Group teardown'},
)

DUAL_ENVIRONMENT = {'local': 'LOCAL_EXECUTOR_ONLY',
                    'preprod': 'REMOTE_BEARER_MUTATING_ADMITTED'}
EFFECTS = {
    'disposable_groups': 2,
    'disposable_sessions': 3,
    'bot_registration': 'FORBIDDEN',
    'shared_driver_bot_deletion': 'FORBIDDEN',
    'implicit_effects': 'PREPROD_FULL_PILOT_EFFECT_ADMISSION_20261030',
    'teardown_contract': 'DELETE every disposable Group and Session; any teardown failure fails the story',
}
OWNED_RUNTIME_BINARY = {
    'package': 'bcs',
    'hash_algorithm': 'sha256',
    'sha256': '899092e39ca461f04a59dce989d1a13bc85e61791099c532b98d7b3882cb745c',
    'auto_build': False,
    'required_arguments': ['--binary', '--binary-sha256'],
    'role': 'owned-bcs-server',
    'reuse_provenance': (
        'sheltered 20261031-wpe3 frozen owned-local BCS server binary; sha256 '
        'verified before every run; no cargo build in this slice'),
}
PREPROD_OBLIGATION_MODES = {
    REQUIREMENTS[0]: 'BEARER_IDENTITY_CONFIRM',
    REQUIREMENTS[1]: 'REMOTE_NOT_APPLICABLE_PRE_ADMITTED_DRIVER',
}
for _requirement in REQUIREMENTS[2:]:
    PREPROD_OBLIGATION_MODES[_requirement] = 'LIVE_BEARER_REPLAY'


def frozen_dual_contract() -> dict:
    """The exact content frozen in the repository dual contract JSON."""
    flat = service_policy_source_binding()
    return {
        'schema_version': 1,
        'parent': PARENT,
        'suite': SOURCE_SUITE,
        'case_id': 'LOCAL-SERVICE-POLICY-01',
        'dual_suite': DUAL_SUITE,
        'dual_case_id': DUAL_CASE_ID,
        'obligation_prefix': 'SERVICE-POLICY.',
        'environment': dict(DUAL_ENVIRONMENT),
        'local_observer': {
            'suite': flat['suite'],
            'case_id': flat['case_id'],
            'status': flat['environment'],
            'relation': ('the promoted reviewed-release parent keeps its frozen owned-local '
                         'black-box executor; this dual contract only adds the preprod '
                         'bearer leg beside it'),
        },
        'sources': dict(flat['sources']),
        'source_revision': flat['source_revision'],
        'source_file_revisions': dict(flat['source_file_revisions']),
        'story_checks': {'local': flat['story_checks'], 'bearer': BEARER_STORY_CHECKS},
        'story_markers': list(flat['story_markers']),
        'requirements': [
            {'id': row['id'], 'expectation': row['expectation'],
             'story_lines': list(row['story_lines']),
             'preprod_mode': PREPROD_OBLIGATION_MODES[row['id']]}
            for row in flat['requirements']
        ],
        'station_routes': [dict(row) for row in STATION_ROUTES],
        'identity_modes': {'local': 'register', 'preprod': 'bearer'},
        'bearer_identity': {
            'route': '/bots/{driver_bot_id}',
            'method': 'GET',
            'expected_status': 200,
            'match_field': 'bot_uuid',
            'actor': 'driver',
            'cookie_reference': 'admitted but never attached to any request',
        },
        'registration_plane': {
            'local': 'mock-admission headers plus one-time token registration',
            'preprod': REGISTRATION_DISPOSITION,
            'note': ('the preprod driver Bot is pre-admitted in the dedicated pool; the '
                     'runner never registers Bots on shared preprod'),
        },
        'owned_runtime_binary': dict(OWNED_RUNTIME_BINARY),
        'effects': dict(EFFECTS),
        'redaction_tokens': list(REDACTION_CANARIES),
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'release_parent_denominator': False,
        'independent_slice': True,
        'evidence_granularity': 'OWNED_CHILD_RECEIPT',
    }


def service_policy_dual_source_binding() -> dict:
    """Freeze and validate the dual contract JSON against this module."""
    try:
        value = read(ROOT / DUAL_CONTRACT_NAME)
        frozen = frozen_dual_contract()
        if value != frozen:
            raise ValueError
        flat = service_policy_source_binding()
        if flat['environment'] != {'local': 'LOCAL_EXECUTOR_ONLY',
                                   'preprod': 'REMOTE_UNSUPPORTED'}:
            # The owned-local black-box binding stays frozen; dual admission
            # lives only in this contract.
            raise ValueError
        if set(value['sources']) != {'service_policy_story.py', 'service_policy_story.sh'}:
            raise ValueError
        marker = MARKER_PREFIX + '{checks}' + MARKER_SUFFIX
        if list(value['story_markers']) != [marker.format(checks='{checks}')] and \
                value['story_markers'] != ['Retired service policy story: {checks} '
                                           'HTTP/response checks passed']:
            raise ValueError
        if value['story_checks'] != {'local': 23, 'bearer': BEARER_STORY_CHECKS}:
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError, SyntaxError):
        raise RunStateError('service-policy dual source binding changed') from None


def service_policy_dual_adaptability() -> dict:
    """Describe the dual surface without granting remote release credit."""
    return {
        'case_id': DUAL_CASE_ID,
        'source_case_id': 'LOCAL-SERVICE-POLICY-01',
        'legacy_id': PARENT,
        'requirement_ids': list(REQUIREMENTS),
        'applicable_requirement_ids': list(APPLICABLE_REQUIREMENTS),
        'not_applicable_requirement': NOT_APPLICABLE_REQUIREMENT,
        'not_applicable_disposition': REGISTRATION_DISPOSITION,
        'environments': ['owned-local-full-story', 'preprod-full-story-bcs-direct-bearer'],
        'local_executor_status': 'LOCAL_FULL_EXECUTOR',
        'preprod_executor_status': 'PREPROD_BEARER_STORY_CHILD',
        'required_assertion_count': len(REQUIREMENTS),
        'applicable_assertion_count': len(APPLICABLE_REQUIREMENTS),
        'local_story_checks': 23,
        'bearer_story_checks': BEARER_STORY_CHECKS,
        'remote_validation': 'NOT_RUN',
        'grants_release_credit': False,
        'blocker': (
            'Both legs replay the frozen Retired service policy journey; the owned-local '
            'leg registers an ephemeral driver principal, the preprod leg replays the '
            'business surface with a pre-admitted driver Bot bearer identity while the '
            'registration obligation maps to an honest REMOTE_NOT_APPLICABLE disposition '
            'and the bootstrap obligation maps to a live driver identity confirmation. '
            'Dual execution is a migration receipt, not release credit.'),
    }
