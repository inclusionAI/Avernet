"""Frozen source and obligation contract for the Bot-authority dual slice.

The upstream entry is the shell story ``apps/bcs/scripts/e2e-test/authority.sh``
(git tip ``c6f00120949078f14cb0d3602fb8301f2ccb1803``, content sha256 frozen
below). It drives the trusted registration plane and the unified Bot-authority
surface end to end: two disposable fixture Bots are registered, the owner reads
ownership, exercises the manager single-item surface with the
``owner_not_manager`` polarity, adds the mine-ensured human (created only by
the public ``bots/mine`` read) while a never-seen user stays unknown, walks the
renewal-window ownership transfer chain, and reads both transfer inboxes.

Unlike the legacy 16-obligation parent journal map, this fine-grained map keeps
one machine obligation per parsed upstream shell assertion (42 total).
``story_lines`` ranges are minimal per-obligation windows: a status obligation
cites the upstream call line plus its ``require_status`` assertion, and each
value obligation cites only its own ``assert_json_eq``/
``assert_json_array_contains`` line(s). Sibling obligations that share one
upstream response keep consecutive, strictly ordered, disjoint windows; the
shared upstream call stays traceable through the anchored lines. Drift in any
frozen fact fails closed with ``RunStateError``
before any executor, binding or runtime exists; migration review is the only
path forward.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

from ..run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[2]
PARENT = 'story_authority_user_journey'
CASE_ID = 'LOCAL-AUTHORITY-JOURNEY-02'
SUITE = 'owned-local-authority-dual-v1'
STATUS = 'LOCAL_AUTHORITY_DUAL_PASS'
DUAL_CASE_ID = 'DUAL-AUTHORITY-JOURNEY-01'
DUAL_SUITE = 'dual-authority-v1'
OBLIGATION_PREFIX = 'AUTHORITY-JOURNEY.'
CONTRACT_NAME = 'authority-dual-migration-contract.json'
STORY_SOURCE = ROOT.parent / 'e2e-test' / 'authority.sh'
REPO_ROOT = ROOT.parents[3]
SOURCE_REVISION = 'c6f00120949078f14cb0d3602fb8301f2ccb1803'
SOURCE_SHA256 = '62c1f09fd74fc881edb191a827c9e2d91e11afd38980fd83c5333828371dd69a'
BCS_BINARY_SHA256 = '899092e39ca461f04a59dce989d1a13bc85e61791099c532b98d7b3882cb745c'
CLI_BINARY_SHA256 = '5a0f3fc595f97d71587af13b2ab5d0267c74c99f7540333b3249554276e97f77'
TARGET_USER_ID = '002'
MINE_USER_ID = '137'
INTERNAL_PREFIX = '/api/v1/collaboration'
PUBLIC_PREFIX = '/openapi/v1/collaboration'
DISPOSITION = 'AUTHORITY_TWO_BOTS_CREATED_AND_RETAINED'
STORY_CHECKS = 42
HTTP_CALL_COUNT = 24
BOOTSTRAP_HTTP_CALLS = 4
BUSINESS_HTTP_CALLS = 20
STRICT_AUTH = {'chain': ['local', 'session'], 'require_authentication': False,
               'allow_mock_headers': True}
ENVIRONMENT = {'local': 'LOCAL_EXECUTOR_ONLY', 'preprod': 'REMOTE_ADMISSION_GATED_READ_ONLY'}
MARKER_KEYS = ('authority-journey-compliance',)
MARKER_PATTERNS = (
    re.compile(r'^Bot authority journey: ([0-9]+) live HTTP assertions passed$', re.M),
)
STORY_MARKERS = ('Bot authority journey: {checks} live HTTP assertions passed',)
REDACTION_TOKENS = ('bot_token', 'X-Avernet-Principal', 'register?token', 'Bearer ')
PRINCIPAL_SIGNING_KEY_ENV = 'AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE'
CLI_SEPARATION_NOTE = (
    'The frozen journey splits runtime roles: bcs owns the server leg; '
    'bcs-cli owns only the child readiness gate because the frozen '
    'bcs-cli exposes no authority business verbs.')
OWNED_PROVENANCE = (
    'sheltered wpe3 owned-local BCS server binary reused without '
    'rebuild; sha256 verified before every run; no cargo build '
    'in this slice')
CLI_PROVENANCE = (
    'sheltered wpe3 bcs-cli binary reused without rebuild; sha256 '
    'verified before every run; no cargo build in this slice')

_NAMES = (
    'owner-ownership-read-status', 'ownership-keeps-bot-id',
    'ownership-identifies-registered-owner', 'manager-page-empty-status',
    'manager-page-starts-empty', 'owner-manager-add-rejected-status',
    'owner-manager-add-error-code', 'manager-add-status',
    'manager-add-added-one', 'manager-page-populated-status',
    'manager-page-keeps-manager', 'manager-page-exposes-added-manager',
    'manager-remove-status', 'manager-remove-removed-one',
    'manager-absent-remove-status', 'manager-absent-remove-removed-zero',
    'manager-restore-status', 'mine-ensured-human-mine-status',
    'mine-manager-add-status', 'mine-manager-add-added-one',
    'never-seen-manager-add-status', 'never-seen-manager-error-code',
    'mine-manager-remove-status', 'mine-manager-remove-removed-one',
    'transfer-initiate-status', 'transfer-initiate-pending',
    'transfer-initiate-target-owner', 'transfer-refresh-status',
    'transfer-refresh-window-renewed', 'transfer-target-read-status',
    'transfer-target-read-pending', 'transfer-accept-status',
    'transfer-accept-accepted', 'transferred-ownership-status',
    'transferred-ownership-owner-moved', 'transferred-ledger-status',
    'transferred-ledger-owner-names-target',
    'transferred-ledger-original-owner-plain-manager',
    'received-inbox-status', 'received-inbox-contains-bot',
    'sent-inbox-status', 'sent-inbox-contains-bot',
)
REQUIREMENTS = tuple(OBLIGATION_PREFIX + name for name in _NAMES)

# Primary (method, path template, expected status, actor, query) of every
# obligation, mirroring the first replayed call of each obligation in the
# frozen shell story.
PRIMARY = {
    'owner-ownership-read-status': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 200, 'owner', None),
    'ownership-keeps-bot-id': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 200, 'owner', None),
    'ownership-identifies-registered-owner': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 200, 'owner', None),
    'manager-page-empty-status': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'owner', None),
    'manager-page-starts-empty': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'owner', None),
    'owner-manager-add-rejected-status': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{owner_user_id}', 409, 'owner', None),
    'owner-manager-add-error-code': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{owner_user_id}', 409, 'owner', None),
    'manager-add-status': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    'manager-add-added-one': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    'manager-page-populated-status': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'owner', None),
    'manager-page-keeps-manager': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'owner', None),
    'manager-page-exposes-added-manager': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'owner', None),
    'manager-remove-status': ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    'manager-remove-removed-one': ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    'manager-absent-remove-status': ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    'manager-absent-remove-removed-zero': ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    'manager-restore-status': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{target_user_id}', 200, 'owner', None),
    'mine-ensured-human-mine-status': ('GET', PUBLIC_PREFIX + '/bots/mine', 200, 'mine', {'limit': '20'}),
    'mine-manager-add-status': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None),
    'mine-manager-add-added-one': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None),
    'never-seen-manager-add-status': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{never_user_id}', 400, 'owner', None),
    'never-seen-manager-error-code': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{never_user_id}', 400, 'owner', None),
    'mine-manager-remove-status': ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None),
    'mine-manager-remove-removed-one': ('DELETE', INTERNAL_PREFIX + '/bots/{bot_id}/managers/{mine_user_id}', 200, 'owner', None),
    'transfer-initiate-status': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 201, 'owner', None),
    'transfer-initiate-pending': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 201, 'owner', None),
    'transfer-initiate-target-owner': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 201, 'owner', None),
    'transfer-refresh-status': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'owner', None),
    'transfer-refresh-window-renewed': ('PUT', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'owner', None),
    'transfer-target-read-status': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'target', None),
    'transfer-target-read-pending': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}', 200, 'target', None),
    'transfer-accept-status': ('POST', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/accept', 200, 'target', None),
    'transfer-accept-accepted': ('POST', INTERNAL_PREFIX + '/bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/accept', 200, 'target', None),
    'transferred-ownership-status': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 200, 'target', None),
    'transferred-ownership-owner-moved': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/ownership', 200, 'target', None),
    'transferred-ledger-status': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'target', None),
    'transferred-ledger-owner-names-target': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'target', None),
    'transferred-ledger-original-owner-plain-manager': ('GET', INTERNAL_PREFIX + '/bots/{bot_id}/managers', 200, 'target', None),
    'received-inbox-status': ('GET', INTERNAL_PREFIX + '/ownership-transfers', 200, 'target', {'direction': 'received'}),
    'received-inbox-contains-bot': ('GET', INTERNAL_PREFIX + '/ownership-transfers', 200, 'target', {'direction': 'received'}),
    'sent-inbox-status': ('GET', INTERNAL_PREFIX + '/ownership-transfers', 200, 'owner', {'direction': 'sent'}),
    'sent-inbox-contains-bot': ('GET', INTERNAL_PREFIX + '/ownership-transfers', 200, 'owner', {'direction': 'sent'}),
}

_EXPECTED_CONSTANTS = {
    'target_user_id': TARGET_USER_ID, 'mine_user_id': MINE_USER_ID,
    'never_user_id_shape': 'never-seen-<uuid4 hex 12>',
    'internal_prefix': INTERNAL_PREFIX, 'public_prefix': PUBLIC_PREFIX,
    'owner_not_manager_error_code': 'owner_not_manager',
    'human_unknown_error_code': 'bot_authority_human_unknown',
    'transfer_window_max_renewal_ms': 300000,
    'expires_at_unit': 'epoch-milliseconds',
    'disposition': DISPOSITION, 'http_call_count': HTTP_CALL_COUNT,
    'bootstrap_http_calls': BOOTSTRAP_HTTP_CALLS,
    'business_http_calls': BUSINESS_HTTP_CALLS,
}


def _line_text(path: Path, line_number: int) -> str:
    lines = path.read_text(encoding='utf-8').splitlines()
    if line_number < 1 or line_number > len(lines):
        raise ValueError('anchor line is outside the upstream source')
    return lines[line_number - 1]


def _git_tip_revision() -> str:
    try:
        result = subprocess.run(
            ['git', '-C', str(REPO_ROOT), 'log', '-1', '--format=%H', '--',
             'apps/bcs/scripts/e2e-test/authority.sh'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
            check=False)
    except (OSError, subprocess.SubprocessError):
        raise RunStateError('authority git tip revision is unavailable; drift gate cannot run') from None
    value = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch('[0-9a-f]{40}', value) is None:
        raise RunStateError('authority git tip revision is not a full commit digest')
    return value


def _validate_requirements(rows: list, source: Path) -> None:
    if [row['id'] for row in rows] != list(REQUIREMENTS):
        raise ValueError('requirement order or count differs from the frozen map')
    text = source.read_text(encoding='utf-8')
    total_lines = len(text.splitlines())
    for row in rows:
        if set(row) != {'id', 'expectation', 'story_lines', 'anchors', 'markers', 'primary'}:
            raise ValueError('requirement shape changed')
        slug = row['id'][len(OBLIGATION_PREFIX):]
        if not isinstance(row['expectation'], str) or not 1 <= len(row['expectation']) <= 1024:
            raise ValueError('requirement expectation is invalid')
        span = row['story_lines']
        if (not isinstance(span, list) or len(span) != 2
                or any(type(item) is not int for item in span)
                or not 1 <= span[0] <= span[1] <= total_lines):
            raise ValueError('requirement story_lines are invalid')
        if row['markers'] != list(MARKER_KEYS):
            raise ValueError('requirement marker changed')
        primary = row['primary']
        if set(primary) != {'method', 'path_template', 'status', 'actor', 'query'}:
            raise ValueError('requirement primary shape changed')
        expected = PRIMARY[slug]
        if (primary['method'] != expected[0] or primary['path_template'] != expected[1]
                or type(primary['status']) is not int or primary['status'] != expected[2]
                or primary['actor'] != expected[3] or primary['query'] != expected[4]):
            raise ValueError('requirement primary call differs from the frozen map')
        anchors = row['anchors']
        if not isinstance(anchors, list) or not anchors:
            raise ValueError('requirement anchors are missing')
        for anchor in anchors:
            if set(anchor) != {'line', 'literal'} or type(anchor['line']) is not int:
                raise ValueError('anchor shape changed')
            if anchor['line'] < span[0] or anchor['line'] > span[1]:
                raise ValueError('anchor line is outside its requirement range')
            if anchor['literal'] not in _line_text(source, anchor['line']):
                raise RunStateError('authority obligation anchor changed: ' + row['id'])


def authority_source_binding() -> dict:
    """Freeze and validate the shell story, binaries and 42 obligations."""
    try:
        value = read(ROOT / CONTRACT_NAME)
        if (set(value) != {'schema_version', 'parent', 'suite', 'case_id', 'dual_case_id',
                           'dual_suite', 'obligation_prefix', 'environment', 'sources',
                           'source_revision', 'source_file_revisions', 'story_checks',
                           'story_markers', 'business_envelope', 'registration_plane',
                           'principal', 'authority_constants', 'owned_runtime_binary',
                           'cli_runtime_binary', 'authorization_note', 'redaction_tokens',
                           'bootstrap_preconditions', 'requirements', 'remote_validation',
                           'grants_release_credit', 'release_parent_denominator',
                           'independent_slice', 'evidence_granularity', 'legacy_observer'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or value['suite'] != SUITE or value['case_id'] != CASE_ID
                or value['dual_case_id'] != DUAL_CASE_ID or value['dual_suite'] != DUAL_SUITE
                or value['obligation_prefix'] != OBLIGATION_PREFIX
                or value['environment'] != ENVIRONMENT
                or set(value['sources']) != {'authority.sh'}
                or Path(value['sources']['authority.sh']).resolve() != STORY_SOURCE.resolve()
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or value['source_revision'] != SOURCE_REVISION
                or value['source_file_revisions'] != {'authority.sh': SOURCE_REVISION}
                or type(value['story_checks']) is not int or value['story_checks'] != STORY_CHECKS
                or value['story_markers'] != list(STORY_MARKERS)
                or value['redaction_tokens'] != list(REDACTION_TOKENS)
                or value['remote_validation'] != 'NOT_RUN'
                or value['grants_release_credit'] is not False
                or value['release_parent_denominator'] is not False
                or value['independent_slice'] is not True
                or value['evidence_granularity'] != 'OWNED_CHILD_RECEIPT'):
            raise ValueError
        if STORY_SOURCE.is_symlink() or not STORY_SOURCE.is_file() \
                or STORY_SOURCE.stat().st_size > 1_048_576:
            raise ValueError
        if hashlib.sha256(STORY_SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
            raise RunStateError('authority upstream source drift requires review')
        if not STORY_SOURCE.read_text(encoding='utf-8').startswith('#!/bin/bash'):
            raise ValueError
        if _git_tip_revision() != SOURCE_REVISION:
            raise RunStateError('authority upstream git tip drift requires review')
        source_text = STORY_SOURCE.read_text(encoding='utf-8')
        if len(re.findall(r'^story_authority_user_journey\(\) \{$', source_text, re.M)) != 1:
            raise RunStateError('authority parent story function disappeared')
        envelope = value['business_envelope']
        if (set(envelope) != {'required_keys', 'data_shapes', 'counted', 'note'}
                or envelope['required_keys'] != ['code', 'message', 'request_id', 'data']
                or sorted(envelope['data_shapes']) != ['dict', 'list']
                or envelope['counted'] is not False):
            raise ValueError
        plane = value['registration_plane']
        if (set(plane) != {'admission_headers', 'mint_path', 'register_path', 'register_query',
                           'token_policy', 'mock_headers_forbidden_on_business_paths',
                           'bots_committed', 'committed_id_shape', 'distinct_bot_ids'}
                or plane['admission_headers'] != ['X-Mock-User-Id', 'X-Mock-Nick-Name']
                or plane['mint_path'] != '/register/token'
                or plane['register_path'] != '/register'
                or plane['register_query'] != ['token', 'bot-name']
                or plane['mock_headers_forbidden_on_business_paths'] is not True
                or plane['bots_committed'] != 2 or plane['distinct_bot_ids'] is not True):
            raise ValueError
        principal = value['principal']
        if (set(principal) != {'header', 'algorithm', 'key_id', 'type', 'issuer', 'audience',
                               'tenant', 'signing_key_env', 'minted_fresh_per_business_request',
                               'subject_keys', 'expiry_window_seconds'}
                or principal['header'] != 'X-Avernet-Principal'
                or principal['algorithm'] != 'HS256' or principal['key_id'] != 'bare'
                or principal['type'] != 'JWT' or principal['issuer'] != 'gateway'
                or principal['audience'] != 'bcs' or principal['tenant'] != 'bcs-e2e'
                or principal['signing_key_env'] != PRINCIPAL_SIGNING_KEY_ENV
                or principal['minted_fresh_per_business_request'] is not True
                or principal['expiry_window_seconds'] != 60):
            raise ValueError
        if value['authority_constants'] != _EXPECTED_CONSTANTS:
            raise ValueError
        owned = value['owned_runtime_binary']
        if (set(owned) != {'package', 'hash_algorithm', 'sha256', 'auto_build',
                           'required_arguments', 'role', 'reuse_provenance'}
                or owned['package'] != 'bcs' or owned['hash_algorithm'] != 'sha256'
                or owned['sha256'] != BCS_BINARY_SHA256 or owned['auto_build'] is not False
                or owned['required_arguments'] != ['--binary', '--binary-sha256']
                or owned['role'] != 'owned-bcs-server'
                or owned['reuse_provenance'] != OWNED_PROVENANCE):
            raise ValueError
        cli = value['cli_runtime_binary']
        if (set(cli) != {'package', 'hash_algorithm', 'sha256', 'auto_build',
                         'required_arguments', 'role', 'authority_business_verbs',
                         'separation_note', 'reuse_provenance'}
                or cli['package'] != 'bcs-cli' or cli['hash_algorithm'] != 'sha256'
                or cli['sha256'] != CLI_BINARY_SHA256 or cli['auto_build'] is not False
                or cli['required_arguments'] != ['--cli-binary', '--cli-binary-sha256']
                or cli['role'] != 'product-cli-readiness-gate-only'
                or cli['authority_business_verbs'] != 'none-frozen'
                or cli['separation_note'] != CLI_SEPARATION_NOTE
                or cli['reuse_provenance'] != CLI_PROVENANCE):
            raise ValueError
        bootstrap = value['bootstrap_preconditions']
        if not isinstance(bootstrap, list) or len(bootstrap) < 8:
            raise ValueError
        for row in bootstrap:
            if set(row) != {'line', 'literal'} or type(row['line']) is not int:
                raise ValueError
            if row['literal'] not in _line_text(STORY_SOURCE, row['line']):
                raise RunStateError('authority bootstrap precondition anchor changed')
        if value['redaction_tokens'] != list(REDACTION_TOKENS):
            raise ValueError
        _validate_requirements(value['requirements'], STORY_SOURCE)
        legacy = value['legacy_observer']
        if (set(legacy) != {'suite', 'case_id', 'obligations', 'journal', 'binding', 'flow',
                            'status', 'relation'}
                or legacy['suite'] != 'owned-local-authority-v1'
                or legacy['case_id'] != 'LOCAL-AUTHORITY-JOURNEY-01'
                or legacy['obligations'] != 16
                or legacy['status'] != 'LOCAL_AUTHORITY_PASS'):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise RunStateError('authority dual source binding changed') from None
