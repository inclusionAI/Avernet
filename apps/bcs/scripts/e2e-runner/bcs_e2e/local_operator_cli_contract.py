"""Source binding for the operator CLI parent Story.

The original shell story drives thirteen ordered product-CLI
operations against the seeded trusted-team fixture: health, the
complete five-bot listing, targeted reads, discovery, the
visibility round-trip, session connect, non-mutating web onboard,
a complete direct-onboard lifecycle, the status round-trip, the
group-help proposal flow with exact creator-scoped cleanup, a
detached chat handle, the paginated group listing and the friend
listing after an in-case friendship admission. This module binds
each obligation to its replayed CLI command; drift in the parent
function, its helpers or the frozen map requires an explicit
migration review, never silent regeneration.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .run_state import RunStateError, read

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = 'LOCAL-OPERATOR-CLI-01'
SUITE = 'owned-local-operator-cli-v1'
PARENT = 'story_operator_coordinates_with_cli'
REQUIREMENTS = tuple('OPERATOR-CLI.'+name for name in (
    'health', 'list', 'get', 'discover', 'visibility', 'connect',
    'onboard-web', 'direct-onboard', 'update-status', 'create-group',
    'chat', 'list-groups', 'friend-list'))
COMMANDS = ('bcs-cli health', 'bcs-cli list', 'bcs-cli get <uuid>',
            'bcs-cli discover --query', 'bcs-cli visibility get/set',
            'bcs-cli connect', 'bcs-cli onboard --web', 'bcs-cli onboard',
            'bcs-cli update-status',
            'bcs-cli create-group + get-group',
            'bcs-cli chat', 'bcs-cli list-groups', 'bcs-cli friend list')
# The parent story (stories.sh) calls these helpers in order. The
# first twelve live in cli.sh; the direct-onboard helper lives in
# cli-stories.sh because it is shared by the CLI journey stories.
HELPERS = ('test_cli_health', 'test_cli_list', 'test_cli_get',
           'test_cli_discover', 'test_cli_visibility_get_set',
           'test_cli_connect', 'test_cli_onboard',
           '_story_cli_direct_onboard', 'test_cli_update_status',
           'test_group_create_via_cli', 'test_cli_chat',
           'test_cli_list_groups', 'test_cli_friend')
CLI_SH_HELPERS = tuple(name for name in HELPERS
                        if name not in {'_story_cli_direct_onboard', 'test_group_create_via_cli'})
SOURCE_FILES = ('stories.sh', 'group.sh', 'cli.sh', 'cli-stories.sh', 'common.sh')


def _function_body(text: str, name: str) -> str:
    if len(re.findall('^'+re.escape(name)+r'\(\) \{$', text, re.M)) != 1:
        raise ValueError
    start = text.index(name+'() {')
    end = text.index('\n}', start)
    return text[start:end+1]


def _called_in_order(parent_body: str) -> None:
    positions = []
    for name in HELPERS:
        anchor = '    '+name
        pattern = ('(?m)^'+re.escape(anchor)
                   +r'(?: \|\| return)?$\n')
        found = [match.start() for match in re.finditer(pattern, parent_body)]
        if len(found) != 1:
            raise ValueError
        positions.append(found[0])
    if positions != sorted(positions):
        raise ValueError


def operator_cli_source_binding():
    try:
        value = read(ROOT/'operator-cli-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent',
                           'sources', 'requirements'}
                or value['schema_version'] != 1 or value['parent'] != PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != set(SOURCE_FILES)):
            raise ValueError
        texts = {}
        for name, digest in value['sources'].items():
            path = ROOT.parent/'e2e-test'/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
                raise ValueError
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError
            texts[name] = raw.decode()
        _called_in_order(_function_body(texts['stories.sh'], PARENT))
        for name in CLI_SH_HELPERS:
            _function_body(texts['cli.sh'], name)
        _function_body(texts['cli-stories.sh'], '_story_cli_direct_onboard')
        if '--query "客服"' not in _function_body(texts['cli.sh'], 'test_cli_discover'):
            raise ValueError
        if 'visibility get' not in _function_body(texts['cli.sh'], 'test_cli_visibility_get_set'):
            raise ValueError
        creator = _function_body(texts['group.sh'], '_cli_create_group')
        for anchor in ('bcs_cli PM create-group --driver "$BOT_PM_UUID"',
                       '--participants "$BOT_PM_UUID,$BOT_ENG_UUID"',
                       'grep -oE \'ID: [A-Za-z0-9_-]+\''):
            if anchor not in creator:
                raise ValueError
        created = _function_body(texts['group.sh'], 'test_group_create_via_cli')
        for anchor in ('gid="$(_cli_create_group)"',
                       'bcs_cli PM get-group --id "$gid"',
                       '_cli_contains "$BCS_CLI_STDOUT" "$gid"',
                       'api_delete "/groups/$gid"'):
            if anchor not in created:
                raise ValueError
        direct = _function_body(texts['cli-stories.sh'], '_story_cli_direct_onboard')
        for anchor in ('api_get "/register/token"', 'api_post "$register_path"',
                       'onboard', 'api_delete "/bots/${bot_uuid}"'):
            if anchor not in direct:
                raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id', 'command', 'expectation'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 1024):
                raise ValueError
        if [row['id'] for row in value['requirements']] != list(REQUIREMENTS):
            raise ValueError
        if [row['command'] for row in value['requirements']] != list(COMMANDS):
            raise ValueError
        return value
    except (ValueError, OSError, TypeError, KeyError):
        raise RunStateError('CLI operator complete source or obligation map changed') from None


CUSTOM_COLLABORATION_PARENT = 'story_cli_operator_creates_custom_collaboration'
CUSTOM_COLLABORATION_REQUIREMENTS = (
    'CUSTOM-COLLABORATION.validate-yaml',
    'CUSTOM-COLLABORATION.create-persistent-group',
    'CUSTOM-COLLABORATION.trial-one-shot-lifecycle',
)
CUSTOM_COLLABORATION_COMMANDS = (
    'bcs-cli collaboration validate <yaml>',
    'bcs-cli collaboration create <yaml> --driver <driver> --binding planner=<driver> '
    '--binding reviewer=<reviewer> --context <context> --topic <topic>',
    'bcs-cli create-group + session add-member + collaborate permission + collaborate run '
    '+ collaborate query --graph/--pending + collaborate respond guard + session messages '
    '+ run cancel/session read/group delete',
)


def custom_collaboration_source_binding():
    """Freeze all three reviewed custom-collaboration obligations."""
    try:
        value = read(ROOT/'custom-collaboration-migration-contract.json')
        if (not {'schema_version', 'source_revision', 'parent', 'sources',
                 'requirements'} <= set(value)
                or not set(value) <= {'schema_version', 'source_revision', 'parent',
                                      'sources', 'requirements',
                                      'convergence_decisions', 'density_requirements'}
                or value['schema_version'] != 1
                or value['parent'] != CUSTOM_COLLABORATION_PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'cli-stories.sh'}):
            raise ValueError
        decisions = value.get('convergence_decisions')
        if decisions is not None:
            if (not isinstance(decisions, list)
                    or any(not isinstance(row, dict)
                           or not isinstance(row.get('decision_id'), str)
                           or not row['decision_id']
                           or not isinstance(row.get('date'), str) or not row['date']
                           or not isinstance(row.get('decision'), str) or not row['decision']
                           for row in decisions)):
                raise ValueError
        path = ROOT.parent/'e2e-test'/'cli-stories.sh'
        if path.is_symlink() or not path.is_file():
            raise ValueError
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value['sources']['cli-stories.sh']:
            raise ValueError
        from .local_custom_collaboration_case import validate_density_contract
        validate_density_contract(value.get('density_requirements'), raw.decode())
        body = _function_body(raw.decode(), value['parent'])
        anchors = (
            '_cli_story_run "operator validates custom collaboration YAML" ""',
            'collaboration validate "$yaml_file"',
            'assert_json_eq "custom collaboration YAML is valid" "$BCS_CLI_STDOUT" "valid" "true"',
            'assert_json_eq "custom collaboration exposes two roles" "$BCS_CLI_STDOUT" "summary.participants" "2"',
            'assert_json_eq "custom collaboration exposes two nodes" "$BCS_CLI_STDOUT" "summary.nodes" "2"',
            '_cli_story_run "operator creates the custom collaboration group" PM',
            'collaboration create "$yaml_file" --driver "$BOT_PM_UUID"',
            '--context "Review release readiness through a custom workflow"',
            '--topic "CLI custom release workflow"',
            'assert_contains "custom collaboration group contains its driver" "$BCS_CLI_STDOUT" "$BOT_PM_UUID"',
            'assert_contains "custom collaboration group contains its reviewer" "$BCS_CLI_STDOUT" "$BOT_ENG_UUID"',
            'api_get "/groups/${group_id}"',
            'assert_json_eq "custom collaboration group uses state-machine strategy" "$RESPONSE" "group_strategy" "state_machine"',
            '_cli_story_create_pm_group "CLI one-shot custom collaboration trial"',
            'session add-member "$trial_session_id"',
            '--bot-uuid "human_${BCS_MOCK_USER_ID}" --role consultant',
            'collaborate permission --session "$trial_session_id"',
            'collaborate run "$yaml_file" --session "$trial_session_id"',
            'collaborate run "$yaml_file" --session "$trial_session_id" \\\n'
            '        --binding "planner=$BOT_PM_UUID" --binding "reviewer=$BOT_ENG_UUID" \\\n'
            '        --input \'{"question":"Review the release workflow before persisting it."}\'',
            'collaborate query --run "$run_id" --graph',
            'collaborate query --run "$run_id" --pending',
            'collaborate respond --run "$run_id" --node plan --content "invalid Human reply"',
            'session messages "$trial_session_id" --view-bot "$BOT_PM_UUID" --limit 50',
            'bot_post "/state-machine-runs/${run_id}/cancel" PM \\\n'
            '        \'{"reason":"E2E one-shot trial completed"}\'',
            'api_get "/sessions/${trial_session_id}"',
            'api_delete "/groups/${trial_group_id}?bot_id=${BOT_PM_UUID}"\n'
            '    require_status "one-shot custom collaboration fixture is cleaned up" "200"',
        )
        if any(body.count(anchor) != 1 for anchor in anchors):
            raise ValueError
        if [row['id'] for row in value['requirements']] != list(CUSTOM_COLLABORATION_REQUIREMENTS):
            raise ValueError
        if [row['command'] for row in value['requirements']] != list(CUSTOM_COLLABORATION_COMMANDS):
            raise ValueError
        validate_expectation = (value['requirements'][0]['expectation']
            == 'Product CLI validation reports valid=true with exactly two '
               'participants and two nodes, without persisting resources or '
               'exposing YAML contents.')
        create_expectation = (value['requirements'][1]['expectation']
            == 'Product CLI creates the exact two-role custom collaboration Group '
               'with its initial Session handle; CLI readback reports driver, '
               'participants, and group_strategy=state_machine before cleanup.')
        if not (validate_expectation and create_expectation):
            raise ValueError
        return value
    except (ValueError, OSError, TypeError, KeyError):
        raise RunStateError('Custom collaboration YAML validation source mapping changed') from None


COLLABORATION_TEAM_PARENT = 'story_cli_operator_builds_collaboration_team'
COLLABORATION_TEAM_REQUIREMENTS = (
    'CLI-COLLAB-TEAM.protect-visibility',
    'CLI-COLLAB-TEAM.friend-request-reject',
    'CLI-COLLAB-TEAM.group-create-read',
    'CLI-COLLAB-TEAM.group-add-member',
    'CLI-COLLAB-TEAM.group-list',
    'CLI-COLLAB-TEAM.group-fuse',
    'CLI-COLLAB-TEAM.group-complete',
    'CLI-COLLAB-TEAM.group-terminate',
)
COLLABORATION_TEAM_COMMANDS = (
    'bcs-cli visibility set --value protected + bcs-cli visibility set --value public',
    'bcs-cli friend request --bot-uuid <CS> + friend requests --direction received --status pending + friend reject --request-id <ID> + friend list',
    'bcs-cli group create + bcs-cli get-group --id <group_id>',
    'bcs-cli add-member --group <group_id> --bot-uuid <QA> + bcs-cli get-group --id <group_id>',
    'bcs-cli list-groups --batch-size 20',
    'bcs-cli fuse --group <group_id>',
    'bcs-cli group-status --group <group_id> --status completed',
    'bcs-cli terminate-group --group <group_id>',
)


def collaboration_team_source_binding():
    """Freeze the full CLI collaboration-team parent obligation set."""
    try:
        value = read(ROOT/'collaboration-team-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent',
                           'sources', 'requirements'}
                or value['schema_version'] != 1
                or value['parent'] != COLLABORATION_TEAM_PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'cli-stories.sh'}):
            raise ValueError
        path = ROOT.parent/'e2e-test'/'cli-stories.sh'
        if path.is_symlink() or not path.is_file():
            raise ValueError
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value['sources']['cli-stories.sh']:
            raise ValueError
        body = _function_body(raw.decode(), value['parent'])
        anchors = (
            '_cli_story_run "customer-service agent protects incoming friendship requests" CS visibility set --value protected',
            '_cli_story_run "engineering requests a protected friendship" ENG friend request --bot-uuid "$BOT_CS_UUID"',
            '_cli_story_run "customer-service rejects the friendship request" CS friend reject --request-id "$request_id"',
            '_cli_story_run "customer-service restores public visibility" CS visibility set --value public',
            '_cli_story_create_pm_group "CLI release coordination"',
            '_cli_story_run "operator reads the CLI-managed group" PM get-group --id "$group_id"',
            '_cli_story_run "operator adds verification to the group" PM add-member',
            '_cli_story_run "operator lists groups containing itself" PM list-groups --batch-size 20',
            '_cli_story_run "operator fuses team perspectives" PM fuse',
            '_cli_story_run "operator marks the coordinated group completed" PM group-status',
            '_cli_story_run "operator terminates a CLI-managed group" PM terminate-group',
        )
        if any(body.count(anchor) != 1 for anchor in anchors):
            raise ValueError
        if [row['id'] for row in value['requirements']] != list(COLLABORATION_TEAM_REQUIREMENTS):
            raise ValueError
        if [row['command'] for row in value['requirements']] != list(COLLABORATION_TEAM_COMMANDS):
            raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id', 'command', 'expectation'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 1024):
                raise ValueError
        return value
    except (ValueError, OSError, TypeError, KeyError):
        raise RunStateError('CLI collaboration-team source or obligation map changed') from None


SESSIONS_SERVICES_PARENT = 'story_cli_operator_runs_sessions_and_services'
SESSIONS_SERVICES_REQUIREMENTS = (
    'CLI-SESSION-SERVICE.session-lifecycle',
    'CLI-SESSION-SERVICE.member-chat-lifecycle',
    'CLI-SESSION-SERVICE.session-complete',
    'CLI-SESSION-SERVICE.service-roundtrip',
)
SESSIONS_SERVICES_COMMANDS = (
    'bcs-cli session create + session list + session get + session patch',
    'bcs-cli session add-member + set-member-mode + get + chat + messages + invite-link + remove-member + get',
    'bcs-cli session complete --output <payload>',
    'bcs-cli service invoke --detach + service status + service wait',
)


def sessions_services_source_binding():
    """Freeze the four business obligations of the CLI session/service Story."""
    try:
        value = read(ROOT/'sessions-services-migration-contract.json')
        if (set(value) != {'schema_version', 'source_revision', 'parent',
                           'sources', 'requirements'}
                or value['schema_version'] != 1
                or value['parent'] != SESSIONS_SERVICES_PARENT
                or not re.fullmatch('[0-9a-f]{40}', value['source_revision'])
                or set(value['sources']) != {'cli-stories.sh'}):
            raise ValueError
        path = ROOT.parent/'e2e-test'/'cli-stories.sh'
        if path.is_symlink() or not path.is_file():
            raise ValueError
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != value['sources']['cli-stories.sh']:
            raise ValueError
        body = _function_body(raw.decode(), value['parent'])
        anchors = (
            '_cli_story_run "operator creates a release session" PM session create',
            '_cli_story_run "operator lists the created release session" PM session list',
            '_cli_story_run "operator reads the release session" PM session get "$session_id"',
            '_cli_story_run "operator renames the release session" PM session patch "$session_id"',
            '_cli_story_run "operator adds verification to the session" PM session add-member "$session_id"',
            '_cli_story_run "operator mutes verification in the session" PM session set-member-mode',
            '_cli_story_run "operator reads the updated session membership" PM session get "$session_id"',
            '_cli_story_run "operator chats in the release session" PM session chat',
            '_cli_story_run "operator reads release-session messages" PM session messages "$session_id"',
            '_cli_story_run "operator creates a session invite" PM session invite-link "$session_id"',
            '_cli_story_run "operator removes verification from the session" PM session remove-member',
            '_cli_story_run "operator verifies session membership removal" PM session get "$session_id"',
            '_cli_story_run "operator completes the release session" PM session complete "$session_id"',
            '_cli_story_run "release pipeline invokes the group service" PM service invoke',
            '_cli_story_run "release pipeline checks service status" PM service status "$service_session_id"',
            'bcs_cli_json PM service wait "$service_session_id" --timeout-ms 1',
            'api_delete "/sessions/${session_id}?bot_id=${BOT_PM_UUID}"',
            'api_delete "/sessions/${service_session_id}?bot_id=$(urlencode "bot:${BOT_PM_UUID}")"',
            'api_delete "/groups/${group_id}?bot_id=${BOT_PM_UUID}"',
        )
        if any(body.count(anchor) != 1 for anchor in anchors):
            raise ValueError
        if [row['id'] for row in value['requirements']] != list(SESSIONS_SERVICES_REQUIREMENTS):
            raise ValueError
        if [row['command'] for row in value['requirements']] != list(SESSIONS_SERVICES_COMMANDS):
            raise ValueError
        for row in value['requirements']:
            if (set(row) != {'id', 'command', 'expectation'}
                    or not isinstance(row['expectation'], str)
                    or not 1 <= len(row['expectation']) <= 1024):
                raise ValueError
        return value
    except (ValueError, OSError, TypeError, KeyError):
        raise RunStateError('CLI session/service source or obligation map changed') from None
