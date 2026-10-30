"""Deterministic builder for the Provider subbots 131-leaf density contract.

Wave D4 density backfill for roster case #20
(``story_provider_operator_manages_subbots``).  The legacy roster parsed one
shell wrapper embedding one self-checking Python child that executes 131 live
HTTP checks without obligation ids; the frozen owned-local contract carries
only 15 coarse obligations.  This builder obligationizes every executed live
check as exactly one density leaf obligation:

* 47 request-status checks (each ``Story.request`` execution outside
  ``self_view``, loop sites expanded by their frozen tuple multiplicity),
* 75 self-view checks (15 ``self_view`` executions: 9 successful projections
  x 7 checks and 6 rejected projections x 2 checks),
* 9 explicit semantic checks (direct ``Story.check`` calls in ``run``).

The builder never runs the story, never opens a socket and never invokes git:
it parses the frozen upstream source with ``ast``, validates the explicit
execution-trace enrichment table against that source, assigns each leaf to one
of the 15 frozen parent obligations by the documented projection rule, and
emits ``provider-subbots-density-obligations.json`` only when every invariant
holds.  Any upstream drift fails the build closed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from .contract import (
    ANCHORS,
    CASE_ID,
    DOC_RELATIVE,
    DOCUMENTED_GAPS,
    PRODUCT_DOC,
    PARENT,
    REQUIREMENTS,
    STORY_CHECKS,
    STORY_MARKERS,
    STORY_SOURCE,
    SHELL_SOURCE,
    SUITE,
)

ROOT = Path(__file__).resolve().parents[2]
FROZEN_CONTRACT = ROOT / 'provider-subbots-migration-contract.json'
DENSITY_CONTRACT = Path(__file__).with_name('provider-subbots-density-obligations.json')
UPSTREAM_RELATIVE = 'apps/bcs/scripts/e2e-test/agentpass_subbots_story.py'
WRAPPER_RELATIVE = 'apps/bcs/scripts/e2e-test/e2e.sh'

DENSITY_PREFIX = 'PROVIDER-SUBBOTS-DENSITY'
DENSITY_CONTRACT_ID = 'provider-subbots-density-obligations-v1'
STORY_CHECKS_ACCOUNTING = (47, 75, 9)
DESIGNED_FAMILY_COUNTS = {
    'provider-registration-discovery': 3,
    'admin-complete-ref-validation': 6,
    'admin-sibling-registration': 3,
    'scoped-complete-ref-validation': 6,
    'scoped-sibling-distinct-identity': 4,
    'duplicate-reference-conflicts': 2,
    'self-lookup-actor-binding': 34,
    'legacy-api-actor-binding': 4,
    'metadata-update-parent-immutability': 9,
    'onboard-parent-immutability': 8,
    'parent-discovery-fail-closed': 7,
    'callback-actor-binding': 4,
    'signed-principal-live-state': 1,
    'disable-enable-state': 23,
    'deletion-tombstone-retirement': 17,
}
# Boundary-overlap resolution: upstream L204 sits inside both the frozen
# admin-sibling-registration range [183,212] and the duplicate-reference range
# [204,268], and both frozen anchor sets retain the duplicate label as
# context.  The executed assertion is the HTTP 409 conflict, so the leaf
# projects to duplicate-reference-conflicts (primary-assertion ownership).
RANGE_EXCEPTION = {204: 'PROVIDER-SUBBOTS.duplicate-reference-conflicts'}
VIEW_SUCCESS_FIELDS = (
    ('status', 'status'),
    ('cache_control', 'self lookup disables caching'),
    ('registered', 'Bot remains registered'),
    ('parent_matches', 'metadata update preserves parent identity'),
    ('bot_id_matches', "self lookup selects the credential's Bot"),
    ('ref_matches', 'self lookup preserves composed ref'),
    ('token_absent', 'self projection contains no runtime token'),
)
VIEW_REJECTION_FIELDS = (
    ('status', 'status'),
    ('cache_control', 'self lookup disables caching'),
)
GAP_STORY_WIDE = 'PREPROD_PROVIDER_CONTROL_PLANE_ABSENT'
GAP_SIGNING_KEY = 'PREPROD_PROVIDER_SUBBOTS_PRINCIPAL_SIGNING_KEY_ABSENT'
GAP_ADMIN_CHANNEL = 'PREPROD_PROVIDER_SUBBOTS_ADMIN_CREDENTIAL_CHANNEL_ABSENT'
SIGNED_PRINCIPAL_LINES = frozenset({408, 414, 433, 448})
ADMIN_CHANNEL_LINES = frozenset(
    {175, 183, 204, 307, 411, 430, 440, 455, 469, 476})

# Execution-order plan: integers are single-execution sites, ('LOOP', line)
# marks a site whose frozen tuple iterable expands inline (all of its
# iterations execute consecutively), and ('GROUP', lines, arity) marks the
# sibling-pair registration loop whose five body sites interleave per
# iteration — exactly as the upstream child executes them.
EXECUTION_PLAN = (
    145, 160, 163, ('LOOP', 175), 183, 190, 194, 204, 213, 224, ('LOOP', 234),
    239, 245, 249, 259, 263,
    ('GROUP', (274, 275, 276, 277, 284), 2),
    292, 299, 307, 314, 318, 320, 328, 331, ('LOOP', 338), 345, 352, 366,
    375, 379, 388, 408, 411, 412, 413, 414, 421, 430, 431, 432, 433, 440,
    446, 447, 448, 455, 463, 469, 475, 476,
)

# ---------------------------------------------------------------------------
# Upstream execution model: derived from the frozen source with ast only.
# ---------------------------------------------------------------------------


def _extract_sites(text: str) -> dict:
    """Return the static call-site inventory of the frozen upstream story."""
    tree = ast.parse(text)
    run_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'run':
            run_node = node
            break
    if run_node is None:
        raise ValueError('upstream story run() method is missing')

    def multiplicity(node: ast.AST) -> int:
        total = 1
        for parent in ast.walk(run_node):
            if not isinstance(parent, ast.For):
                continue
            if not (parent.lineno <= node.lineno <= parent.end_lineno):
                continue
            iterable = parent.iter
            if isinstance(iterable, (ast.Tuple, ast.List)):
                total *= len(iterable.elts)
        return total

    requests, views, semantics = {}, {}, {}
    for node in ast.walk(run_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in {
                'request', 'self_view', 'check'}
                and isinstance(func.value, ast.Name) and func.value.id == 'self'):
            continue
        line = node.lineno
        if func.attr == 'request':
            status = 200
            if len(node.args) >= 4 and isinstance(node.args[3], ast.Constant):
                status = node.args[3].value
            for keyword in node.keywords:
                if keyword.arg == 'status' and isinstance(
                        keyword.value, ast.Constant):
                    status = keyword.value.value
            headers = None
            for keyword in node.keywords:
                if keyword.arg == 'headers':
                    headers = ast.unparse(keyword.value)
            requests[line] = {
                'line': line, 'end_line': node.end_lineno,
                'label': node.args[0].value, 'method': node.args[1].value,
                'status': status, 'multiplicity': multiplicity(node),
                'headers': headers,
                'slice': _slice(text, node.lineno, node.end_lineno),
            }
        elif func.attr == 'self_view':
            status = 200
            actor = False
            for keyword in node.keywords:
                if keyword.arg == 'status' and isinstance(
                        keyword.value, ast.Constant):
                    status = keyword.value.value
                if keyword.arg == 'actor':
                    actor = True
            views[line] = {
                'line': line, 'end_line': node.end_lineno,
                'status': status, 'actor': actor,
                'multiplicity': multiplicity(node),
                'slice': _slice(text, line, node.end_lineno),
            }
        else:
            semantics[line] = {
                'line': line, 'end_line': node.end_lineno,
                'label': node.args[1].value if len(node.args) > 1 else '',
                'multiplicity': multiplicity(node),
                'slice': _slice(text, line, node.end_lineno),
            }
    return {'request': requests, 'view': views, 'semantic': semantics}


def _slice(text: str, start: int, end: int) -> str:
    lines = text.splitlines()
    return '\n'.join(lines[start - 1:end])


# ---------------------------------------------------------------------------
# Semantic enrichment table (the human-readable obligation decisions).
# ---------------------------------------------------------------------------

REQUEST_ENRICHMENT = {
    145: dict(path='/providers', actor='human',
              segment='provider', field='registration_status',
              cross=[('provider', 'provider_id_present'),
                     ('provider', 'admin_credential_present')],
              title='Human creates the subbots Provider'),
    160: dict(path='/providers/by-slug/e2e-subbots-{suffix}', actor='human',
              segment='provider', field='discovery_status',
              title='Public slug discovery serves the Provider'),
    175: dict(path='/providers/{provider_id}/bots', actor='provider_admin',
              segment='admin_ref_validation', field='statuses',
              loop_values=('parent-without-slug', ':a', '{parent}:',
                           '{parent}:Uppercase', 'bad/parent:a',
                           '{parent}:' + 'x' * 33),
              loop_noun='malformed ref',
              title='Admin path rejects a malformed complete ref with 400'),
    183: dict(path='/providers/{provider_id}/bots', actor='provider_admin',
              segment='admin_registration', field='status',
              title='Admin path registers the reviewer sibling'),
    204: dict(path='/providers/{provider_id}/bots', actor='provider_admin',
              segment='duplicates', field='admin_status',
              title='Admin path rejects a duplicate complete ref with 409'),
    213: dict(path='/register/token?provider_id={provider_id}', actor='human',
              segment=None, field=None,
              title='Owner issues the scoped registration credential'),
    224: dict(path='/register', actor='scoped_token',
              segment='scoped_ref_validation', field='removed_slug_status',
              query='removed bot_slug cannot repair a malformed ref',
              title='Removed bot_slug cannot complete a malformed ref'),
    234: dict(path='/register', actor='scoped_token',
              segment='scoped_ref_validation', field='malformed_statuses',
              loop_values=(':a', '{parent}:', '{parent}:Uppercase',
                           'bad/parent:a'),
              loop_noun='malformed ref',
              query='scoped malformed complete ref',
              title='Scoped path rejects a malformed complete ref with 400'),
    239: dict(path='/register', actor='scoped_token',
              segment='scoped_registration', field='status',
              query='scoped registration',
              title='Scoped path registers the coder sibling'),
    263: dict(path='/register', actor='scoped_token',
              segment='duplicates', field='scoped_status',
              query='scoped duplicate registration',
              title='Scoped path rejects a duplicate complete ref with 409'),
    277: dict(path='/groups/my', actor='bot_runtime_matching_actor',
              segment='legacy_api', field='matching_statuses',
              title='Legacy API accepts the token Bot with matching Actor'),
    284: dict(path='/groups/my', actor='bot_runtime_sibling_actor',
              segment='legacy_api', field='sibling_statuses',
              title='Legacy API rejects a sibling Actor despite mock user'),
    292: dict(path='/groups/my', actor='actor_only',
              segment='self_lookup', field='actor_only_status',
              title='Actor header alone cannot fall back to the mock Human'),
    299: dict(path='/groups/my', actor='bot_runtime_malformed_actor',
              segment='self_lookup', field='malformed_actor_status',
              title='Malformed empty Actor header is rejected'),
    307: dict(path='/providers/{provider_id}/bots/{provider_bot_ref}',
              actor='provider_admin', segment='metadata_update', field='status',
              title='Admin path patches the reviewer metadata'),
    320: dict(path='/bots/onboard', actor='bot_runtime',
              segment='onboarding', field='status',
              title='Bot onboards with the exact Actor identity'),
    331: dict(path='/api/v1/collaboration/agents/me',
              actor='human_unauthenticated',
              segment='parent_discovery', field='unauthenticated_status',
              title='Parent discovery requires credentials'),
    338: dict(path='/api/v1/collaboration/agents/me?{query}', actor='bot_runtime',
              segment='parent_discovery', field='invalid_query_statuses',
              loop_values=('limit=0', 'limit=101', 'cursor=', 'unknown=value'),
              loop_noun='invalid query',
              title='Parent discovery rejects an invalid query with 400'),
    345: dict(path='/api/v1/collaboration/agents/me', actor='bot_runtime',
              segment='parent_discovery', field='unavailable_status',
              title='Public build fails closed without the AgentPass verifier'),
    366: dict(path='/bot/events', actor='bot_runtime',
              segment='callback', field='authenticated_status',
              title='Authenticated callback reaches the run lookup'),
    379: dict(path='/bot/events', actor='bot_runtime_sibling_actor',
              segment='callback', field='sibling_status',
              title='Callback rejects a sibling Actor'),
    388: dict(path='/bot/events', actor='token_only_bot',
              segment='callback', field='token_only_status',
              title='Callback accepts a Bot token without an Actor'),
    408: dict(path='/openapi/v1/collaboration/public-groups',
              actor='signed_principal', segment='signed_principal',
              field='accepted_status',
              title='Gateway accepts the signed Bot principal'),
    411: dict(path='/providers/{provider_id}/disable', actor='provider_admin',
              segment='disable_enable', field='disable_status',
              title='Admin path disables the Provider'),
    414: dict(path='/openapi/v1/collaboration/public-groups',
              actor='signed_principal', segment='disable_enable',
              field='disabled_signed_status',
              cross=[('signed_principal', 'disabled_status')],
              title='Disabled Provider invalidates the signed Bot'),
    421: dict(path='/bot/events', actor='bot_runtime',
              segment='disable_enable', field='disabled_callback_status',
              title='Disabled Provider rejects callbacks'),
    430: dict(path='/providers/{provider_id}/enable', actor='provider_admin',
              segment='disable_enable', field='enable_status',
              title='Admin path enables the Provider'),
    433: dict(path='/openapi/v1/collaboration/public-groups',
              actor='signed_principal', segment='disable_enable',
              field='enabled_signed_status',
              cross=[('signed_principal', 'enabled_status')],
              title='Enabled Provider restores the signed Bot'),
    440: dict(path='/providers/{provider_id}/bots/{provider_bot_ref}',
              actor='provider_admin', segment='deletion',
              field='delete_reviewer_status',
              title='Admin path deletes the reviewer Bot'),
    448: dict(path='/openapi/v1/collaboration/public-groups',
              actor='signed_principal', segment='deletion',
              field='deleted_signed_status',
              cross=[('signed_principal', 'deleted_status')],
              title='Deleted Bot invalidates the signed principal'),
    455: dict(path='/providers/{provider_id}/bots', actor='provider_admin',
              segment='deletion', field='admin_reclaim_status',
              title='Admin path cannot reclaim the deleted slug'),
    463: dict(path='/register', actor='scoped_token', segment='deletion',
              field='scoped_reclaim_status',
              query='scoped reclaim of deleted reviewer ref',
              title='Scoped path cannot reclaim the deleted slug'),
    469: dict(path='/providers/{provider_id}/bots/{provider_bot_ref}',
              actor='provider_admin', segment='deletion',
              field='delete_coder_status',
              title='Admin path deletes the coder Bot'),
    476: dict(path='/providers/{provider_id}/disable', actor='provider_admin',
              segment='deletion', field='retire_status',
              title='Admin path retires the test Provider'),
}
SEMANTIC_ENRICHMENT = {
    163: dict(segment='provider', fields=('auth_mode',),
              expectation='Provider discovery reports the agentpass-subbots auth mode',
              parent='PROVIDER-SUBBOTS.provider-registration-discovery'),
    190: dict(segment='admin_registration', fields=('agent_code', 'bot_slug'),
              expectation='Admin registration returns the exact parent and reviewer slug',
              parent='PROVIDER-SUBBOTS.admin-sibling-registration'),
    194: dict(segment='admin_registration', fields=('auth_mode', 'provider_bot_ref'),
              expectation='Server preserves the client-composed reviewer ref and auth mode',
              parent='PROVIDER-SUBBOTS.admin-sibling-registration'),
    245: dict(segment='scoped_registration', fields=('agent_code', 'bot_slug'),
              expectation='Scoped registration returns the exact parent and coder slug',
              parent='PROVIDER-SUBBOTS.scoped-sibling-distinct-identity'),
    249: dict(segment='scoped_registration', fields=('auth_mode', 'provider_bot_ref'),
              expectation='Both registration paths parse the same coder identity',
              parent='PROVIDER-SUBBOTS.scoped-sibling-distinct-identity'),
    259: dict(segment='scoped_registration',
              fields=('distinct_from_admin_uuid', 'distinct_from_admin_token'),
              expectation='Siblings receive distinct Bot UUIDs and runtime tokens',
              parent='PROVIDER-SUBBOTS.scoped-sibling-distinct-identity'),
    314: dict(segment='metadata_update',
              fields=('returned_bot_id', 'requested_bot_id', 'name',
                      'authorization_scopes'),
              expectation=('Metadata update retains the Bot UUID, applies the '
                           'name and keeps the scope set empty'),
              parent='PROVIDER-SUBBOTS.metadata-update-parent-immutability'),
    352: dict(segment='parent_discovery', fields=('error_code',),
              expectation='Missing verifier fails closed with agent_identity_unavailable',
              parent='PROVIDER-SUBBOTS.parent-discovery-fail-closed'),
    375: dict(segment='callback', fields=('authenticated_error',),
              expectation='Callback validates identity before resolving the run',
              parent='PROVIDER-SUBBOTS.callback-actor-binding'),
}
# view execution ordinals (true execution order) -> context
VIEW_EXECUTIONS = (
    dict(ordinal=1, line=274, bot='a', actor_kind='none', status=200,
         context='registration loop iteration 1, sibling a bare token'),
    dict(ordinal=2, line=275, bot='a', actor_kind='matching', status=200,
         context='registration loop iteration 1, sibling a matching Actor'),
    dict(ordinal=3, line=276, bot='a', actor_kind='sibling', status=401,
         context='registration loop iteration 1, sibling a with sibling Actor b'),
    dict(ordinal=4, line=274, bot='b', actor_kind='none', status=200,
         context='registration loop iteration 2, sibling b bare token'),
    dict(ordinal=5, line=275, bot='b', actor_kind='matching', status=200,
         context='registration loop iteration 2, sibling b matching Actor'),
    dict(ordinal=6, line=276, bot='b', actor_kind='sibling', status=401,
         context='registration loop iteration 2, sibling b with sibling Actor a'),
    dict(ordinal=7, line=318, bot='a', actor_kind='none', status=200,
         context='after reviewer metadata patch'),
    dict(ordinal=8, line=328, bot='a', actor_kind='none', status=200,
         context='after Bot onboarding'),
    dict(ordinal=9, line=412, bot='a', actor_kind='none', status=401,
         context='after Provider disable'),
    dict(ordinal=10, line=413, bot='b', actor_kind='none', status=401,
         context='after Provider disable'),
    dict(ordinal=11, line=431, bot='a', actor_kind='none', status=200,
         context='after Provider enable'),
    dict(ordinal=12, line=432, bot='b', actor_kind='none', status=200,
         context='after Provider enable'),
    dict(ordinal=13, line=446, bot='a', actor_kind='none', status=401,
         context='after reviewer deletion'),
    dict(ordinal=14, line=447, bot='b', actor_kind='none', status=200,
         context='after reviewer deletion, live sibling'),
    dict(ordinal=15, line=475, bot='b', actor_kind='none', status=401,
         context='after coder deletion'),
)
VIEW_CROSS_SEGMENTS = {
    7: [('metadata_update', 'readback_parent_matches'),
        ('metadata_update', 'readback_ref_matches')],
    8: [('onboarding', 'readback_status'),
        ('onboarding', 'readback_parent_matches'),
        ('onboarding', 'readback_ref_matches')],
    9: [('disable_enable', 'disabled_view_statuses')],
    10: [('disable_enable', 'disabled_view_statuses')],
    11: [('disable_enable', 'enabled_view_statuses')],
    12: [('disable_enable', 'enabled_view_statuses')],
    13: [('deletion', 'reviewer_view_status')],
    14: [('deletion', 'sibling_view_status')],
    15: [('deletion', 'coder_view_status')],
}
VIEW_ORDINAL_BY_LINE = {execution['line']: execution['ordinal']
                        for execution in VIEW_EXECUTIONS
                        if execution['line'] not in (274, 275, 276)}
VIEW_ORIDINALS_BY_LINE = {}
for _execution in VIEW_EXECUTIONS:
    VIEW_ORIDINALS_BY_LINE.setdefault(_execution['line'], []).append(
        _execution['ordinal'])

FROZEN_SOURCE_PATHS = {
    'agentpass_subbots_story.py': STORY_SOURCE,
    'e2e.sh': SHELL_SOURCE,
    'agentpass-subbots.md': PRODUCT_DOC,
}


# ---------------------------------------------------------------------------
# Part 2: frozen loading, projection, expansion, emission and fail-closed fences.
# ---------------------------------------------------------------------------

STATUS_NOT_RUN = 'NOT_RUN'
UPSTREAM_STORY = 'apps/bcs/scripts/e2e-test/agentpass_subbots_story.py'
CHECKS_MODULE = ROOT / 'bcs_e2e' / 'provider_subbots' / 'checks.py'
MIRROR_STORY = Path(__file__).with_name('story.py')
FROZEN_KEYS = {
    'schema_version', 'parent', 'suite', 'case_id', 'environment', 'sources', 'source_revision',
    'source_file_revisions', 'story_checks', 'story_markers', 'owned_runtime_binary',
    'config_fragment', 'redaction_tokens', 'requirements', 'documented_gaps', 'adaptability',
    'remote_validation', 'grants_release_credit', 'release_parent_denominator',
    'independent_slice', 'evidence_granularity',
}
REQUIREMENT_KEYS = {'id', 'expectation', 'story_lines', 'anchors', 'markers'}
WRAPPER_FUNCTION = 'story_provider_operator_manages_subbots'
WRAPPER_LINES = (97, 103)
WRAPPER_MARKER = 'assert_eq "subbots registration and runtime identity journey" "$result" "0"'
GROUP_ARITY = 2
GROUP_LOOP_VALUES = (('a', 'b'), ('b', 'a'))
VIEW_LABEL = 'Bot self lookup'
VIEW_PATH = '/api/v1/collaboration/bots/me'
LOCAL_EXECUTOR = 'owned-local-child-story'
LOCAL_DERIVATION = 'FROZEN_CHILD_RECEIPT_COLLECTIVE'
VIEW_ACTOR_HEADERS = {
    'none': 'X-BCN-Actor-Bot-Id: absent',
    'matching': 'X-BCN-Actor-Bot-Id: matching bot id',
    'sibling': 'X-BCN-Actor-Bot-Id: sibling bot id',
}
GAP_SIGNING_KEY_TEXT = 'The reviewed preprod credential set exposes no AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE channel, so the four signed Gateway principal leaves (upstream L408, L414, L433 and L448) cannot execute remotely; coverage material gap M5. They stay NOT_RUN with network_requests=0 and grant no release credit.'
GAP_ADMIN_CHANNEL_TEXT = 'The Provider admin credential is minted by the owned-local child at registration time and the reviewed preprod profile exposes no provider_admin_token credential channel (coverage material gap M5); the fifteen admin-path leaves stay NOT_RUN with network_requests=0 and grant no release credit.'

PROJECTION_RULES = (
    '1. A leaf whose site span carries frozen anchor literals of exactly one parent projects with basis frozen-anchor.',
    '2. Otherwise the leaf projects to the unique parent whose frozen story_lines range contains the site line (basis story-lines-range).',
    '3. Range overlaps resolve by frozen anchor literals first; the single remaining tie (upstream L204, anchored by both admin-sibling-registration and duplicate-reference-conflicts) resolves to duplicate-reference-conflicts because the executed assertion is the HTTP 409 conflict (basis documented-exception).',
    '3b. Semantic check sites whose frozen anchor set is silent (for example upstream L249, inside both the scoped-identity and duplicate ranges) project to the parent declared in the builder enrichment table, validated for range consistency (basis density-semantic-declaration).',
    '4. Frozen evidence matrices may re-consume one executed check across segments (for example signed-principal statuses re-asserted by disable/enable and deletion validators); each executed check is still counted exactly once and the re-consumption is disclosed as cross_segment_validation.',
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _load_frozen() -> dict:
    """Load and bound-validate the frozen migration contract without git."""
    story_text = STORY_SOURCE.read_text(encoding='utf-8')
    try:
        value = json.loads(FROZEN_CONTRACT.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('frozen provider-subbots migration contract is unreadable') from error
    if set(value) != FROZEN_KEYS or value['schema_version'] != 1:
        raise ValueError('frozen migration contract shape changed')
    if (value['parent'] != PARENT or value['case_id'] != CASE_ID
            or value['suite'] != SUITE or value['story_checks'] != STORY_CHECKS):
        raise ValueError('frozen migration contract identity changed')
    if not re.fullmatch('[0-9a-f]{40}', value['source_revision']):
        raise ValueError('frozen source revision is not a full commit digest')
    if set(value['sources']) != set(FROZEN_SOURCE_PATHS):
        raise ValueError('frozen source digest map changed')
    if any(not re.fullmatch('[0-9a-f]{64}', digest) for digest in value['sources'].values()):
        raise ValueError('frozen source digest map is malformed')
    if set(value['documented_gaps']) != set(DOCUMENTED_GAPS):
        raise ValueError('frozen documented gap set changed')
    requirements = value['requirements']
    if [row['id'] for row in requirements] != list(REQUIREMENTS):
        raise ValueError('frozen requirement order changed')
    for row in requirements:
        if set(row) != REQUIREMENT_KEYS:
            raise ValueError('frozen requirement shape changed')
        low, high = row['story_lines']
        if (not isinstance(low, int) or not isinstance(high, int)
                or not 1 <= low <= high <= len(story_text.splitlines())):
            raise ValueError('frozen story_lines range is invalid')
        for anchor in row['anchors']:
            if (set(anchor) != {'line', 'literal'} or type(anchor['line']) is not int
                    or not low <= anchor['line'] <= high):
                raise ValueError('frozen anchor shape changed')
            if anchor['literal'] not in story_text.splitlines()[anchor['line'] - 1]:
                raise ValueError('frozen anchor literal drifted: ' + row['id'])
    return value


def _frozen_indexes(frozen: dict) -> tuple[dict, dict]:
    anchors = {row['id']: {a['line'] for a in row['anchors']} for row in frozen['requirements']}
    ranges = {row['id']: (row['story_lines'][0], row['story_lines'][1])
              for row in frozen['requirements']}
    return anchors, ranges


def assign_parent(line: int, end_line: int, anchor_index: dict,
                  range_index: dict) -> tuple[str, str]:
    """Project one executed site onto exactly one frozen parent obligation."""
    anchored = sorted(parent for parent, rows in anchor_index.items()
                      if any(line <= anchor_line <= end_line for anchor_line in rows))
    if len(anchored) == 1:
        return anchored[0], 'frozen-anchor'
    ranged = sorted(parent for parent, (low, high) in range_index.items()
                    if low <= line <= high)
    if len(ranged) == 1:
        return ranged[0], 'story-lines-range'
    if line in RANGE_EXCEPTION:
        return RANGE_EXCEPTION[line], 'documented-exception'
    raise ValueError('no unique frozen projection for upstream line ' + str(line))


def _expand_execution_plan(sites: dict) -> list:
    """Expand the curated plan into the 71 true upstream executions."""
    executions = []

    def request(line: int, loop_index: object) -> None:
        executions.append({'kind': 'request-status', 'line': line,
                           'loop_index': loop_index, 'view_execution': None})

    def view(line: int, ordinal: int, loop_index: object) -> None:
        executions.append({'kind': 'self-view', 'line': line,
                           'loop_index': loop_index, 'view_execution': ordinal})

    for entry in EXECUTION_PLAN:
        if isinstance(entry, int):
            if entry in sites['request']:
                request(entry, None)
            elif entry in sites['view']:
                view(entry, VIEW_ORDINAL_BY_LINE[entry], None)
            elif entry in sites['semantic']:
                executions.append({'kind': 'semantic', 'line': entry,
                                   'loop_index': None, 'view_execution': None})
            else:
                raise ValueError('plan references an unknown upstream site: ' + str(entry))
        elif entry[0] == 'LOOP':
            for index in range(1, sites['request'][entry[1]]['multiplicity'] + 1):
                request(entry[1], index)
        else:
            lines, arity = entry[1], entry[2]
            for iteration in range(1, arity + 1):
                for line in lines:
                    if line in sites['view']:
                        view(line, VIEW_ORIDINALS_BY_LINE[line][iteration - 1], iteration)
                    else:
                        request(line, iteration)
    return executions


def _request_loop_values(line: int, enrich: dict) -> object:
    if line in (277, 284):
        return [list(pair) for pair in GROUP_LOOP_VALUES]
    values = enrich.get('loop_values')
    return list(values) if values else None


def _cross_entries(cross: object) -> object:
    if not cross:
        return None
    return [{'segment': segment, 'field': field} for segment, field in cross]


def _gap_refinements(line: int) -> list:
    refinements = []
    if line in SIGNED_PRINCIPAL_LINES:
        refinements.append(GAP_SIGNING_KEY)
    if line in ADMIN_CHANNEL_LINES:
        refinements.append(GAP_ADMIN_CHANNEL)
    return refinements


def _request_title(enrich: dict, loop_index: object) -> str:
    title = enrich['title']
    if loop_index is None:
        return title + (' [' + enrich['query'] + ']' if enrich.get('query') else '')
    values = enrich.get('loop_values')
    noun = enrich.get('loop_noun') or 'sibling pair iteration'
    if values:
        return f"{title} [{noun} {loop_index}/{len(values)}: {values[loop_index - 1]}]"
    return f'{title} [{noun} {loop_index}/{GROUP_ARITY}]'


def _produce_leaves(sites: dict, frozen: dict) -> list:
    anchor_index, range_index = _frozen_indexes(frozen)
    view_contexts = {execution['ordinal']: execution for execution in VIEW_EXECUTIONS}
    family_state: dict[str, int] = {}
    leaves = []

    def emit(kind: str, subkind: object, title: str, label: str, method: object,
             path: object, status: object, actor: str, headers: object, anchor: dict,
             evidence: dict, parent: str, basis: str) -> None:
        family = parent.split('.', 1)[1]
        family_state[family] = family_state.get(family, 0) + 1
        leaves.append({
            'id': f'{DENSITY_PREFIX}.{family}.{family_state[family]:03d}',
            'check_no': len(leaves) + 1,
            'family': family,
            'parent': parent,
            'kind': kind,
            'subkind': subkind,
            'title': title,
            'upstream_label': label,
            'expectation': title,
            'method': method,
            'path': path,
            'expected_status': status,
            'actor': actor,
            'actor_headers': headers,
            'anchor': anchor,
            'projection': {'basis': basis, 'story_lines': list(range_index[parent])},
            'evidence': evidence,
            'local': {'executable': True, 'executor': LOCAL_EXECUTOR,
                      'derivation': LOCAL_DERIVATION},
            'preprod': {'status': STATUS_NOT_RUN, 'network_requests': 0,
                        'gap': GAP_STORY_WIDE,
                        'gap_refinements': _gap_refinements(anchor['site_line'])},
        })

    for execution in _expand_execution_plan(sites):
        line = execution['line']
        if execution['kind'] == 'request-status':
            site, enrich = sites['request'][line], REQUEST_ENRICHMENT[line]
            parent, basis = assign_parent(line, site['end_line'], anchor_index, range_index)
            emit('request-status', None, _request_title(enrich, execution['loop_index']),
                 site['label'], site['method'], enrich['path'], site['status'],
                 enrich['actor'], site['headers'],
                 {'file': UPSTREAM_STORY, 'site_line': line, 'site_end_line': site['end_line'],
                  'label_literal': site['label'], 'site_slice': site['slice'],
                  'multiplicity': site['multiplicity'], 'loop_index': execution['loop_index'],
                  'loop_values': _request_loop_values(line, enrich), 'view_execution': None,
                  'view_context': None, 'view_bot': None, 'view_actor_kind': None,
                  'expansion': None},
                 {'collective_marker': STORY_MARKERS[0],
                  'primary_segment': enrich['segment'],
                  'primary_path': (None if enrich['segment'] is None
                                   else [enrich['segment'], enrich['field']]),
                  'cross_segment_validation': _cross_entries(enrich.get('cross'))},
                 parent, basis)
        elif execution['kind'] == 'self-view':
            site, context = sites['view'][line], view_contexts[execution['view_execution']]
            parent, basis = assign_parent(line, site['end_line'], anchor_index, range_index)
            fields = VIEW_SUCCESS_FIELDS if context['status'] == 200 else VIEW_REJECTION_FIELDS
            for subkind, label in fields:
                verb = 'succeeds' if context['status'] == 200 else (
                    'is rejected with ' + str(context['status']))
                emit('self-view', subkind,
                     f'{VIEW_LABEL} ({context["context"]}) {verb} — {label}',
                     VIEW_LABEL, 'GET', VIEW_PATH, context['status'], 'bot_runtime',
                     VIEW_ACTOR_HEADERS[context['actor_kind']],
                     {'file': UPSTREAM_STORY, 'site_line': line,
                      'site_end_line': site['end_line'], 'label_literal': VIEW_LABEL,
                      'site_slice': site['slice'], 'multiplicity': site['multiplicity'],
                      'loop_index': execution['loop_index'],
                      'loop_values': ([list(pair) for pair in GROUP_LOOP_VALUES]
                                      if execution['loop_index'] is not None else None),
                      'view_execution': context['ordinal'],
                      'view_context': context['context'], 'view_bot': context['bot'],
                      'view_actor_kind': context['actor_kind'], 'expansion': subkind},
                     {'collective_marker': STORY_MARKERS[0], 'primary_segment': 'self_lookup',
                      'primary_path': ['self_lookup', subkind],
                      'cross_segment_validation': _cross_entries(
                          VIEW_CROSS_SEGMENTS.get(context['ordinal']))},
                     parent, basis)
        else:
            site, enrich = sites['semantic'][line], SEMANTIC_ENRICHMENT[line]
            parent = enrich['parent']
            low, high = range_index[parent]
            if not low <= line <= high:
                raise ValueError('semantic declaration inconsistent with frozen range')
            emit('semantic', None, enrich['expectation'], site['label'], None, None, None,
                 'response-payload', None,
                 {'file': UPSTREAM_STORY, 'site_line': line, 'site_end_line': site['end_line'],
                  'label_literal': site['label'], 'site_slice': site['slice'],
                  'multiplicity': site['multiplicity'], 'loop_index': None,
                  'loop_values': None, 'view_execution': None, 'view_context': None,
                  'view_bot': None, 'view_actor_kind': None, 'expansion': None},
                 {'collective_marker': STORY_MARKERS[0],
                  'primary_segment': enrich['segment'],
                  'primary_path': [enrich['segment'], *enrich['fields']],
                  'cross_segment_validation': None},
                 parent, 'density-semantic-declaration')
    return leaves


def _validate_sites(sites: dict) -> None:
    """Fail closed unless the AST model matches the frozen accounting."""
    requests, views, semantics = sites['request'], sites['view'], sites['semantic']
    if len(requests) != 34:
        raise ValueError('request site inventory changed')
    if sum(site['multiplicity'] for site in requests.values()) != 47:
        raise ValueError('request execution count changed')
    if set(requests) != set(REQUEST_ENRICHMENT):
        raise ValueError('request enrichment no longer covers the request sites')
    if len(views) != 12 or sum(site['multiplicity'] for site in views.values()) != 15:
        raise ValueError('self-view site inventory changed')
    if any(site['multiplicity'] != (2 if line in (274, 275, 276) else 1)
           for line, site in views.items()):
        raise ValueError('self-view multiplicity drifted')
    if (len(semantics) != 9 or set(semantics) != set(SEMANTIC_ENRICHMENT)
            or any(site['multiplicity'] != 1 for site in semantics.values())):
        raise ValueError('semantic site inventory changed')


def _validate_plan_against_ast(executions: list, sites: dict) -> None:
    """Validate the curated execution plan against the AST execution model."""
    previous = 0
    for entry in EXECUTION_PLAN:
        if isinstance(entry, int):
            lines = [entry]
        else:
            lines = [entry[1]] if entry[0] == 'LOOP' else list(entry[1])
        if min(lines) <= previous:
            raise ValueError('execution plan order is not source-monotonic')
        previous = max(lines)
    expected = {}
    for execution in executions:
        key = (execution['kind'], execution['line'])
        expected[key] = expected.get(key, 0) + 1
    for kind, section in (('request-status', 'request'), ('self-view', 'view'),
                          ('semantic', 'semantic')):
        for line, site in sites[section].items():
            if expected.get((kind, line), 0) != site['multiplicity']:
                raise ValueError(kind + ' multiplicity drifted at line ' + str(line))
    planned = {line for _, line in expected}
    classified = set(sites['request']) | set(sites['view']) | set(sites['semantic'])
    if planned != classified:
        raise ValueError('execution plan does not cover the AST site inventory')


def _validate_leaves(leaves: list, story_text: str) -> None:
    static_multiplicity = {175: 6, 234: 4, 277: 2, 284: 2, 338: 4, 274: 2, 275: 2, 276: 2}
    """Validate the emitted 131 leaves against every frozen invariant."""
    leaf_keys = {
        'id', 'check_no', 'family', 'parent', 'kind', 'subkind', 'title',
        'upstream_label', 'expectation', 'method', 'path', 'expected_status',
        'actor', 'actor_headers', 'anchor', 'projection', 'evidence', 'local',
        'preprod',
    }
    kinds, families, bases, refinements, numbering, ids = {}, {}, {}, {}, {}, set()
    for leaf in leaves:
        if set(leaf) != leaf_keys:
            raise ValueError('density leaf shape changed: ' + leaf['id'])
        if not re.fullmatch(r'PROVIDER-SUBBOTS-DENSITY\.[a-z0-9-]+\.[0-9]{3}$', leaf['id']):
            raise ValueError('density leaf id is malformed: ' + leaf['id'])
        if leaf['id'] in ids:
            raise ValueError('density leaf id is not unique: ' + leaf['id'])
        ids.add(leaf['id'])
        if leaf['family'] != leaf['parent'].split('.', 1)[1]:
            raise ValueError('density family differs from its parent: ' + leaf['id'])
        kinds[leaf['kind']] = kinds.get(leaf['kind'], 0) + 1
        families[leaf['family']] = families.get(leaf['family'], 0) + 1
        bases[leaf['projection']['basis']] = bases.get(leaf['projection']['basis'], 0) + 1
        seen = numbering.get(leaf['family'], 0) + 1
        if leaf['id'] != f"{DENSITY_PREFIX}.{leaf['family']}.{seen:03d}":
            raise ValueError('family numbering is not emission order: ' + leaf['id'])
        numbering[leaf['family']] = seen
        local, preprod = leaf['local'], leaf['preprod']
        if (local['executable'] is not True or local['executor'] != LOCAL_EXECUTOR
                or local['derivation'] != LOCAL_DERIVATION):
            raise ValueError('local projection row is invalid: ' + leaf['id'])
        if (preprod['status'] != STATUS_NOT_RUN or preprod['network_requests'] != 0
                or preprod['gap'] != GAP_STORY_WIDE):
            raise ValueError('preprod receipt is not honest: ' + leaf['id'])
        for gap in preprod['gap_refinements']:
            refinements[gap] = refinements.get(gap, 0) + 1
        if leaf['anchor']['label_literal'] not in story_text:
            raise ValueError('leaf label literal left the upstream story: ' + leaf['id'])
        expected_static = static_multiplicity.get(leaf['anchor']['site_line'], 1)
        if leaf['anchor']['multiplicity'] != expected_static:
            raise ValueError('static multiplicity drifted: ' + leaf['id'])
        if expected_static == 1 and (leaf['anchor']['loop_index'] is not None
                                     or leaf['anchor']['loop_values'] is not None):
            raise ValueError('non-loop leaf carries loop state: ' + leaf['id'])
    if kinds != {'request-status': 47, 'self-view': 75, 'semantic': 9}:
        raise ValueError('density kind accounting drifted')
    if families != DESIGNED_FAMILY_COUNTS:
        raise ValueError('density family counts drifted')
    if set(families) != {name.split('.', 1)[1] for name in REQUIREMENTS}:
        raise ValueError('density projection is not surjective onto the 15 parents')
    if bases != {'frozen-anchor': 66, 'story-lines-range': 55, 'documented-exception': 1,
                 'density-semantic-declaration': 9}:
        raise ValueError('density projection basis accounting drifted')
    if refinements != {GAP_SIGNING_KEY: 4, GAP_ADMIN_CHANNEL: 15}:
        raise ValueError('density gap refinement accounting drifted')
    exceptions = [leaf for leaf in leaves if leaf['projection']['basis'] == 'documented-exception']
    if (len(exceptions) != 1 or exceptions[0]['anchor']['site_line'] != 204
            or exceptions[0]['parent'] != 'PROVIDER-SUBBOTS.duplicate-reference-conflicts'):
        raise ValueError('documented-exception projection drifted')
    null_primary = [leaf for leaf in leaves if leaf['evidence']['primary_segment'] is None]
    if (len(null_primary) != 1 or null_primary[0]['anchor']['site_line'] != 213
            or null_primary[0]['evidence']['primary_path'] is not None):
        raise ValueError('only upstream L213 may carry a null evidence primary')
    checkpoints = {}
    for leaf in leaves:
        line = leaf['anchor']['site_line']
        if line in (145, 204, 274, 408, 476) and line not in checkpoints:
            checkpoints[line] = leaf['check_no']
    if checkpoints != {145: 1, 204: 13, 274: 25, 408: 91, 476: 131}:
        raise ValueError('execution-order checkpoints drifted')
    view_leaves = [leaf for leaf in leaves if leaf['kind'] == 'self-view']
    if (sum(leaf['expected_status'] == 200 for leaf in view_leaves),
            sum(leaf['expected_status'] == 401 for leaf in view_leaves)) != (63, 12):
        raise ValueError('self-view success/rejection projection drifted')


def _validate_evidence_vocabulary(leaves: list) -> None:
    """Every evidence segment/field name must exist in checks.py text."""
    text = CHECKS_MODULE.read_text(encoding='utf-8')
    segments = set(re.findall(r"evidence\.get\('([a-z_]+)'\)", text))
    vocabulary = set(re.findall(r"'([a-z][a-z0-9_]*)'", text))
    for leaf in leaves:
        evidence = leaf['evidence']
        segment = evidence['primary_segment']
        if segment is not None and segment not in segments:
            raise ValueError('evidence segment is unknown to checks.py: ' + segment)
        path = evidence['primary_path']
        if path is not None and any(item not in vocabulary for item in path):
            raise ValueError('evidence path is unknown to checks.py: ' + str(path))
        for entry in evidence['cross_segment_validation'] or ():
            if entry['segment'] not in segments or entry['field'] not in vocabulary:
                raise ValueError('cross segment validation is unknown to checks.py')


def _validate_wrapper(shell_text: str) -> None:
    if shell_text.count(WRAPPER_FUNCTION + '() {') != 1:
        raise ValueError('wrapper function is not unique in e2e.sh')
    wrapper_slice = _slice(shell_text, WRAPPER_LINES[0], WRAPPER_LINES[1])
    if 'agentpass_subbots_story.py' not in wrapper_slice or WRAPPER_MARKER not in wrapper_slice:
        raise ValueError('wrapper slice drifted from the frozen roster shape')


def build_density_contract() -> dict:
    """Build and fully validate the 131-leaf density contract in memory."""
    frozen = _load_frozen()
    story_text = STORY_SOURCE.read_text(encoding='utf-8')
    shell_text = SHELL_SOURCE.read_text(encoding='utf-8')
    for name, path in FROZEN_SOURCE_PATHS.items():
        if _sha256_text(path.read_text(encoding='utf-8')) != frozen['sources'][name]:
            raise ValueError('upstream source digest drifted: ' + name)
    if not story_text.startswith('#!/usr/bin/env python3'):
        raise ValueError('upstream story shebang changed')
    if 'PASS: disable/re-enable and deletion enforce persisted identity' not in story_text:
        raise ValueError('upstream completion marker changed')
    for literals in ANCHORS.values():
        for literal in literals:
            if literal not in story_text:
                raise ValueError('frozen anchor literal left the upstream story')
    _validate_wrapper(shell_text)
    mirror = MIRROR_STORY.read_text(encoding='utf-8')
    if 'MARKER_PREFIX + str(story.checks) + MARKER_SUFFIX' not in mirror:
        raise ValueError('owned mirror no longer emits the collective marker')
    sites = _extract_sites(story_text)
    _validate_sites(sites)
    executions = _expand_execution_plan(sites)
    _validate_plan_against_ast(executions, sites)
    leaves = _produce_leaves(sites, frozen)
    _validate_leaves(leaves, story_text)
    _validate_evidence_vocabulary(leaves)
    view_leaves = [leaf for leaf in leaves if leaf['kind'] == 'self-view']
    return {
        'schema_version': 1,
        'contract': DENSITY_CONTRACT_ID,
        'density_prefix': DENSITY_PREFIX,
        'parent': PARENT,
        'source_case_id': CASE_ID,
        'suite': SUITE,
        'story_checks': STORY_CHECKS,
        'source_revision': frozen['source_revision'],
        'sources': dict(frozen['sources']),
        'sources_relative': {'story': UPSTREAM_STORY, 'wrapper': WRAPPER_RELATIVE,
                             'product_doc': DOC_RELATIVE},
        'wrapper': {
            'source': WRAPPER_RELATIVE,
            'function': WRAPPER_FUNCTION,
            'lines': list(WRAPPER_LINES),
            'child': UPSTREAM_STORY,
            'marker_assertion': WRAPPER_MARKER,
            'parsed_wrappers': 1,
            'note': ('One legacy shell wrapper embeds one self-checking Python child; the child '
                     'executes the 131 live HTTP checks that this contract obligationizes.'),
        },
        'roster': {
            'upstream_parsed_wrappers': 1,
            'embedded_live_checks': STORY_CHECKS,
            'local_obligations_before': len(REQUIREMENTS),
            'density_leaves': len(leaves),
            'density_delta': len(leaves) - len(REQUIREMENTS),
            'density_rule': ('One executed live check maps to exactly one density leaf '
                             'obligation: no overlap, no gap. The frozen 15 obligations remain '
                             'the parent projection layer; density leaves never replace or '
                             're-anchor them.'),
        },
        'accounting': {
            'request_status_checks': sum(1 for leaf in leaves
                                         if leaf['kind'] == 'request-status'),
            'self_view_executions': len(VIEW_EXECUTIONS),
            'self_view_success_executions': sum(1 for execution in VIEW_EXECUTIONS
                                                if execution['status'] == 200),
            'self_view_rejection_executions': sum(1 for execution in VIEW_EXECUTIONS
                                                  if execution['status'] == 401),
            'self_view_checks': len(view_leaves),
            'self_view_breakdown': {
                'status': sum(1 for leaf in view_leaves if leaf['subkind'] == 'status'),
                'cache_control': sum(1 for leaf in view_leaves
                                     if leaf['subkind'] == 'cache_control'),
                'projection': sum(1 for leaf in view_leaves
                                  if leaf['subkind'] not in ('status', 'cache_control')),
            },
            'semantic_checks': sum(1 for leaf in leaves if leaf['kind'] == 'semantic'),
            'total': len(leaves),
            'story_checks_frozen': STORY_CHECKS,
            'isomorphic': True,
        },
        'parent_projection': [
            {'parent': requirement, 'family': requirement.split('.', 1)[1],
             'leaf_count': sum(1 for leaf in leaves if leaf['parent'] == requirement),
             'story_lines': [row['story_lines'] for row in frozen['requirements']
                             if row['id'] == requirement][0]}
            for requirement in REQUIREMENTS
        ],
        'projection_rules': list(PROJECTION_RULES),
        'preprod': {
            'status': 'BLOCKED_INVENTORIED',
            'network_requests': 0,
            'remote_validation': 'NOT_RUN',
            'grants_release_credit': False,
            'documented_gaps': {
                GAP_STORY_WIDE: frozen['documented_gaps'][GAP_STORY_WIDE],
                GAP_SIGNING_KEY: GAP_SIGNING_KEY_TEXT,
                GAP_ADMIN_CHANNEL: GAP_ADMIN_CHANNEL_TEXT,
            },
            'gap_refinement_constants': [GAP_SIGNING_KEY, GAP_ADMIN_CHANNEL],
            'leaf_counts_by_gap_refinement': {
                GAP_SIGNING_KEY: sum(1 for leaf in leaves
                                     if GAP_SIGNING_KEY in leaf['preprod']['gap_refinements']),
                GAP_ADMIN_CHANNEL: sum(1 for leaf in leaves
                                       if GAP_ADMIN_CHANNEL in leaf['preprod']['gap_refinements']),
            },
        },
        'local': {
            'executor': LOCAL_EXECUTOR,
            'derivation': LOCAL_DERIVATION,
            'pass_rule': ('Result/assertion accounting is exactly equal: 131 density leaves '
                          'equal the frozen STORY_CHECKS denominator, and a leaf is PASS only '
                          'through the frozen child receipt collective (observed_checks == 131, '
                          'checks.status == PASS, checked == the 15 frozen requirements). Any '
                          'partial, fused or failing child receipt leaves every leaf honestly '
                          'NOT_RUN instead of fabricating remote PASS.'),
        },
        'obligations': leaves,
    }


def main() -> None:
    value = build_density_contract()
    DENSITY_CONTRACT.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n',
        encoding='utf-8')
    families = {}
    for leaf in value['obligations']:
        families[leaf['family']] = families.get(leaf['family'], 0) + 1
    print('wrote ' + str(DENSITY_CONTRACT))
    print('leaves: ' + str(len(value['obligations']))
          + ' (request-status 47, self-view 75, semantic 9)')
    print('families: ' + json.dumps(families, sort_keys=True))


if __name__ == '__main__':
    main()
