"""Owned-local Story command roster, parser registration and offline evidence probes."""


from __future__ import annotations

import hashlib

from dataclasses import dataclass
from pathlib import Path

from .inventory import reviewed_release_inventory
from .local_backlog_story_commands import BACKLOG_STORY_COMMANDS, register_backlog_story_commands
from .migration_inventory import migration_plan


LOCAL_STORY_COMMANDS = frozenset({
    'local-coverage-suite',
    'local-event-run',
    'local-event-report',
    'local-structured-run',
    'local-structured-report',
    'local-direct-chat-run',
    'local-direct-chat-report',
    'local-slow-judge-run',
    'local-slow-judge-report',
    'local-fixed-loop-run',
    'local-fixed-loop-report',
    'local-edge-friend-mutations-run',
    'local-edge-friend-mutations-report',
    'local-edge-read-run',
    'local-edge-read-report',
    'local-channel-cli-run',
    'local-channel-cli-report',
    'local-channel-http-run',
    'local-channel-http-report',
    'local-delivery-policy-run',
    'local-delivery-policy-report',
    'local-operator-cli-run',
    'local-operator-cli-report',
    'local-collaboration-team-run',
    'local-collaboration-team-report',
    'local-provider-run',
    'local-provider-report',
    'local-session-public-run',
    'local-session-public-report',
    'local-session-sharing-run',
    'local-session-sharing-report',
    'local-session-run',
    'local-session-report',
    'local-network-run',
    'local-network-report',
    'local-workspace-api',
    'local-workspace-run',
    'local-workspace-report',
    'local-file-run',
    'local-file-report',
    'local-caller-execution-run',
    'local-caller-execution-report',
    'local-draft-group-run',
    'local-draft-group-report',
    'local-result-queue-run',
    'local-result-queue-report',
    'local-nested-fixed-loop-run',
    'local-nested-fixed-loop-report',
    'local-service-policy-run',
    'local-service-policy-report',
    'local-authority-run',
    'local-authority-report',
}) | BACKLOG_STORY_COMMANDS


def register_local_story_commands(commands) -> None:
    register_coverage_command(commands)
    event = commands.add_parser('local-event-run', help='Run complete owned-local Event Webhook Story')
    for name in ('binary','state-dir'):
        event.add_argument('--'+name,type=Path,required=True)
    for name in ('binary-sha256','run-id','confirm-run-id'):
        event.add_argument('--'+name,required=True)
    for name in ('allow-local-processes','allow-fixture-write','allow-write'):
        event.add_argument('--'+name,action='store_true')
    event_report=commands.add_parser('local-event-report',help='Verify frozen local Event Webhook evidence offline')
    event_report.add_argument('--state-dir',type=Path,required=True)
    event_report.add_argument('--output-dir',type=Path)
    structured = commands.add_parser('local-structured-run', help='Run original17-step Structured collaboration locally')
    for name in ('binary','state-dir','template-dir','template-manifest'):
        structured.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256','run-id','confirm-run-id','template-manifest-sha256'):
        structured.add_argument('--'+name, required=True)
    for name in ('allow-local-processes','allow-fixture-write','allow-write'):
        structured.add_argument('--'+name, action='store_true')
    structured_report = commands.add_parser('local-structured-report', help='Verify frozen Structured results offline')
    structured_report.add_argument('--state-dir', type=Path, required=True)
    structured_report.add_argument('--output-dir', type=Path)
    direct = commands.add_parser('local-direct-chat-run', help='Run complete original direct-chat Story in an owned local runtime')
    for name in ('binary', 'state-dir'):
        direct.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        direct.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        direct.add_argument('--'+name, action='store_true')
    direct_report = commands.add_parser('local-direct-chat-report', help='Verify frozen direct-chat results offline')
    direct_report.add_argument('--state-dir', type=Path, required=True)
    direct_report.add_argument('--output-dir', type=Path)
    slow_judge = commands.add_parser('local-slow-judge-run', help='Run the complete owned-local slow-Judge Story')
    for name in ('binary', 'state-dir'):
        slow_judge.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        slow_judge.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        slow_judge.add_argument('--' + name, action='store_true')
    slow_judge_report = commands.add_parser('local-slow-judge-report', help='Verify frozen local slow-Judge evidence offline')
    slow_judge_report.add_argument('--state-dir', type=Path, required=True)
    slow_judge_report.add_argument('--output-dir', type=Path)
    fixed_loop = commands.add_parser('local-fixed-loop-run', help='Run the complete owned-local fixed-Loop Story with both scripted Runs')
    for name in ('binary', 'state-dir'):
        fixed_loop.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        fixed_loop.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        fixed_loop.add_argument('--' + name, action='store_true')
    fixed_loop_report = commands.add_parser('local-fixed-loop-report', help='Verify frozen owned-local fixed-Loop evidence offline')
    fixed_loop_report.add_argument('--state-dir', type=Path, required=True)
    fixed_loop_report.add_argument('--output-dir', type=Path)
    edge_friend_mutations = commands.add_parser('local-edge-friend-mutations-run', help='Run all nine owned-local Edge friend-mutation cases in one owned runtime')
    for name in ('binary', 'state-dir'):
        edge_friend_mutations.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        edge_friend_mutations.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        edge_friend_mutations.add_argument('--'+name, action='store_true')
    edge_friend_mutations_report = commands.add_parser('local-edge-friend-mutations-report', help='Verify frozen owned-local Edge friend-mutation evidence offline')
    edge_friend_mutations_report.add_argument('--state-dir', type=Path, required=True)
    edge_friend_mutations_report.add_argument('--output-dir', type=Path)
    edge_read = commands.add_parser('local-edge-read-run', help='Run both dual-environment read-only Edge cases in one owned runtime')
    for name in ('binary', 'state-dir'):
        edge_read.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        edge_read.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        edge_read.add_argument('--'+name, action='store_true')
    edge_read_report = commands.add_parser('local-edge-read-report', help='Verify frozen owned-local Edge read evidence offline')
    edge_read_report.add_argument('--state-dir', type=Path, required=True)
    edge_read_report.add_argument('--output-dir', type=Path)
    file_story = commands.add_parser('local-file-run', help='Run the actual owned-local six-leaf CLI File Story')
    for name in ('binary', 'cli-binary', 'state-dir'):
        file_story.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        file_story.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        file_story.add_argument('--'+name, action='store_true')
    channel_cli = commands.add_parser('local-channel-cli-run', help='Run the complete Channel CLI Story locally')
    for name in ('binary', 'cli-binary', 'state-dir'):
        channel_cli.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        channel_cli.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        channel_cli.add_argument('--'+name, action='store_true')
    channel_cli_report = commands.add_parser('local-channel-cli-report', help='Verify local Channel CLI evidence offline')
    channel_cli_report.add_argument('--state-dir', type=Path, required=True)
    channel_cli_report.add_argument('--output-dir', type=Path)
    channel = commands.add_parser('local-channel-http-run', help='Run the complete disabled-channel HTTP Story locally')
    for name in ('binary', 'state-dir'):
        channel.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        channel.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        channel.add_argument('--'+name, action='store_true')
    channel_report = commands.add_parser('local-channel-http-report', help='Verify local disabled-channel HTTP evidence offline')
    channel_report.add_argument('--state-dir', type=Path, required=True)
    channel_report.add_argument('--output-dir', type=Path)
    delivery_policy = commands.add_parser('local-delivery-policy-run', help='Run the complete human delivery-policy CAS Story locally')
    for name in ('binary', 'state-dir'):
        delivery_policy.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        delivery_policy.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        delivery_policy.add_argument('--'+name, action='store_true')
    delivery_policy_report = commands.add_parser('local-delivery-policy-report', help='Verify local human delivery-policy CAS evidence offline')
    delivery_policy_report.add_argument('--state-dir', type=Path, required=True)
    delivery_policy_report.add_argument('--output-dir', type=Path)
    operator_cli = commands.add_parser('local-operator-cli-run', help='Run the complete operator CLI parent Story locally')
    for name in ('binary', 'cli-binary', 'state-dir'):
        operator_cli.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        operator_cli.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        operator_cli.add_argument('--'+name, action='store_true')
    operator_cli_report = commands.add_parser('local-operator-cli-report', help='Verify local operator CLI evidence offline')
    operator_cli_report.add_argument('--state-dir', type=Path, required=True)
    operator_cli_report.add_argument('--output-dir', type=Path)
    collaboration_team = commands.add_parser('local-collaboration-team-run', help='Run the complete collaboration-team CLI parent Story locally')
    for name in ('binary', 'cli-binary', 'state-dir'):
        collaboration_team.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        collaboration_team.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        collaboration_team.add_argument('--'+name, action='store_true')
    collaboration_team_report = commands.add_parser('local-collaboration-team-report', help='Verify local collaboration-team evidence offline')
    collaboration_team_report.add_argument('--state-dir', type=Path, required=True)
    collaboration_team_report.add_argument('--output-dir', type=Path)
    provider = commands.add_parser('local-provider-run', help='Run the complete Provider/Organization Story in an owned local runtime')
    for name in ('binary', 'state-dir'):
        provider.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        provider.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        provider.add_argument('--'+name, action='store_true')
    provider_report = commands.add_parser('local-provider-report', help='Verify the complete local Provider Story offline')
    provider_report.add_argument('--state-dir', type=Path, required=True)
    provider_report.add_argument('--output-dir', type=Path)
    session_flow = commands.add_parser('local-session-run', help='Run all nine original Session helpers in one owned local runtime')
    for name in ('binary', 'cli-binary', 'state-dir'):
        session_flow.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        session_flow.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        session_flow.add_argument('--'+name, action='store_true')
    flow_report = commands.add_parser('local-session-report', help='Verify complete original local Session flow offline')
    flow_report.add_argument('--state-dir', type=Path, required=True)
    flow_report.add_argument('--output-dir', type=Path)
    sharing = commands.add_parser('local-session-sharing-run', help='Run first six original Session helpers with actual CLI and invitation closure')
    for name in ('binary', 'cli-binary', 'state-dir'):
        sharing.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        sharing.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        sharing.add_argument('--'+name, action='store_true')
    sharing_report = commands.add_parser('local-session-sharing-report', help='Verify original six-helper Session evidence offline')
    sharing_report.add_argument('--state-dir', type=Path, required=True)
    sharing_report.add_argument('--output-dir', type=Path)
    session_public = commands.add_parser('local-session-public-run', help='Run complete public Session/JWT/WS helper; eight parent helpers remain unmigrated')
    for name in ('binary', 'state-dir'):
        session_public.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        session_public.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        session_public.add_argument('--'+name, action='store_true')
    session_report = commands.add_parser('local-session-public-report', help='Verify original local Session public slice offline')
    session_report.add_argument('--state-dir', type=Path, required=True)
    session_report.add_argument('--output-dir', type=Path)
    network = commands.add_parser('local-network-run', help='Execute complete owned-local Agent-network Story; no remote qualification')
    for name in ('binary', 'panel-asset', 'state-dir'):
        network.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'panel-sha256', 'run-id', 'confirm-run-id'):
        network.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        network.add_argument('--'+name, action='store_true')
    network_report = commands.add_parser('local-network-report', help='Verify original Agent-network evidence offline')
    network_report.add_argument('--state-dir', type=Path, required=True)
    network_report.add_argument('--output-dir', type=Path)
    workspace = commands.add_parser('local-workspace-api', help='Run owned-local Workspace API obligations; full Story remains unmigrated')
    for name in ('binary', 'state-dir'):
        workspace.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        workspace.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        workspace.add_argument('--'+name, action='store_true')
    workspace_cli = commands.add_parser('local-workspace-run', help='Run owned Workspace API and actual CLI obligations; incident/messaging/deletion remain unmigrated')
    for name in ('binary', 'cli-binary', 'state-dir'):
        workspace_cli.add_argument('--'+name, type=Path, required=True)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id'):
        workspace_cli.add_argument('--'+name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        workspace_cli.add_argument('--'+name, action='store_true')
    workspace_cli.add_argument('--with-incident-config', action='store_true',
                               help='Also run owned incident configuration; messages/deletion remain NOT_RUN')
    workspace_cli.add_argument('--with-messages', action='store_true',
                               help='Include incident configuration and actual local Bot transport reception; no deletion')
    workspace_cli.add_argument('--with-incident-delete', action='store_true',
                               help='Include message stage and exact owned-local Incident deletion; no remote cleanup')
    workspace_report = commands.add_parser('local-workspace-report', help='Verify frozen complete Workspace evidence offline')
    workspace_report.add_argument('--state-dir', type=Path, required=True)
    workspace_report.add_argument('--output-dir', type=Path)
    file_report = commands.add_parser('local-file-report', help='Render bound local CLI File evidence offline')
    file_report.add_argument('--state-dir', type=Path, required=True)
    file_report.add_argument('--output-dir', type=Path)

    # Independent local-only slice; intentionally outside the reviewed release coverage registry.
    caller_run = commands.add_parser('local-caller-execution-run', help='Run the complete owned-local caller-execution Story as one black-box child')
    for name in ('binary', 'state-dir'):
        caller_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        caller_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        caller_run.add_argument('--' + name, action='store_true')
    caller_report = commands.add_parser('local-caller-execution-report', help='Verify frozen owned-local caller-execution evidence offline')
    caller_report.add_argument('--state-dir', type=Path, required=True)
    caller_report.add_argument('--output-dir', type=Path)

    # Independent local-only slice; intentionally outside the reviewed release coverage registry.
    draft_run = commands.add_parser('local-draft-group-run', help='Run the complete owned-local Draft group Story as black-box children beside one owned BCS runtime')
    for name in ('binary', 'state-dir'):
        draft_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        draft_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        draft_run.add_argument('--' + name, action='store_true')
    draft_report = commands.add_parser('local-draft-group-report', help='Verify frozen owned-local Draft group evidence offline')
    draft_report.add_argument('--state-dir', type=Path, required=True)
    draft_report.add_argument('--output-dir', type=Path)

    # Independent local-only slice; intentionally outside the reviewed release coverage registry.
    result_run = commands.add_parser('local-result-queue-run', help='Run the complete owned-local Result queue Story as black-box children beside one owned BCS runtime')
    for name in ('binary', 'state-dir'):
        result_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        result_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        result_run.add_argument('--' + name, action='store_true')
    result_report = commands.add_parser('local-result-queue-report', help='Verify frozen owned-local Result queue evidence offline')
    result_report.add_argument('--state-dir', type=Path, required=True)
    result_report.add_argument('--output-dir', type=Path)

    # Independent local-only slice; intentionally outside the reviewed release coverage registry.
    nested_run = commands.add_parser('local-nested-fixed-loop-run', help='Run the complete owned-local Nested fixed-loop Story as black-box children beside one owned BCS runtime')
    for name in ('binary', 'cli-binary', 'state-dir'):
        nested_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'driver', 'run-id', 'confirm-run-id'):
        nested_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        nested_run.add_argument('--' + name, action='store_true')
    nested_report = commands.add_parser('local-nested-fixed-loop-report', help='Verify frozen owned-local Nested fixed-loop evidence offline')
    nested_report.add_argument('--state-dir', type=Path, required=True)
    nested_report.add_argument('--output-dir', type=Path)

    # Promoted 30th reviewed release parent slice: the retired service policy
    # Story runs as one black-box child beside one owned BCS runtime.
    policy_run = commands.add_parser('local-service-policy-run', help='Run the complete upstream Retired service policy Story as one black-box child beside one owned BCS runtime')
    for name in ('binary', 'state-dir'):
        policy_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        policy_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        policy_run.add_argument('--' + name, action='store_true')
    policy_report = commands.add_parser('local-service-policy-report', help='Verify frozen retired service policy evidence offline')
    policy_report.add_argument('--state-dir', type=Path, required=True)
    policy_report.add_argument('--output-dir', type=Path)

    # Promoted 31st reviewed release parent slice: the Bot-authority user
    # journey runs as one black-box child beside one owned BCS runtime.
    authority_run = commands.add_parser('local-authority-run', help='Run the complete Bot-authority user journey as one black-box child beside one owned BCS runtime')
    for name in ('binary', 'state-dir'):
        authority_run.add_argument('--' + name, type=Path, required=True)
    for name in ('binary-sha256', 'run-id', 'confirm-run-id'):
        authority_run.add_argument('--' + name, required=True)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        authority_run.add_argument('--' + name, action='store_true')
    authority_report = commands.add_parser('local-authority-report', help='Verify frozen Bot-authority journey evidence offline')
    authority_report.add_argument('--state-dir', type=Path, required=True)
    authority_report.add_argument('--output-dir', type=Path)
    register_backlog_story_commands(commands)


@dataclass(frozen=True)
class CaseBinding:
    case_id: str
    command: str
    cli: bool = False
    flags: tuple[str, ...] = ()
    function: str = ''


# Deliberately explicit: inventory additions must be reviewed, never auto-dispatched.
# Edge batch commands are decomposed only at their existing case/adapter seam.
CASES = (
    CaseBinding('LOCAL-TRUST-API-CLI-01', 'local-trust-run', True,
                function='owned_bcs.local_runtime_smoke'),
    CaseBinding('LOCAL-CLI-FILE-01', 'local-file-run', True),
    CaseBinding('LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01', 'local-workspace-run', True, ('with_incident_delete',)),
    CaseBinding('LOCAL-NETWORK-01', 'local-network-run'),
    CaseBinding('LOCAL-SESSION-FLOW-01', 'local-session-run', True),
    CaseBinding('LOCAL-PROVIDER-01', 'local-provider-run'),
    CaseBinding('LOCAL-CHANNEL-HTTP-01', 'local-channel-http-run'),
    CaseBinding('LOCAL-DELIVERY-POLICY-01', 'local-delivery-policy-run'),
    CaseBinding('LOCAL-CHANNEL-CLI-01', 'local-channel-cli-run', True),
    CaseBinding('LOCAL-OPERATOR-CLI-01', 'local-operator-cli-run', True),
    CaseBinding('DUAL-COLLABORATION-TEAM-CLI-01', 'local-collaboration-team-run', True),
    CaseBinding('LOCAL-CUSTOM-COLLABORATION-LIFECYCLE-01', 'run_custom_collaboration_case', True,
                function='local_custom_collaboration_run.local_custom_collaboration_run'),
    CaseBinding('LOCAL-SESSION-SERVICE-PARENT-01', 'execute_local_session_service_parent_case', True,
                function='local_session_service_parent_run.local_session_service_parent_run'),
    CaseBinding('LOCAL-DIRECT-CHAT-01', 'local-direct-chat-run'),
    CaseBinding('LOCAL-STRUCTURED-01', 'local-structured-run'),
    CaseBinding('LOCAL-EVENT-WEBHOOK-01', 'local-event-run'),
    CaseBinding('LOCAL-SLOW-JUDGE-01', 'local-slow-judge-run'),
    CaseBinding('LOCAL-EDGE-ADMISSION-01', 'local-edge-read-run', function='edge_read_case.execute_edge_read_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-REQUESTS-LIST-01', 'local-edge-read-run', function='edge_read_case.execute_edge_read_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-CONNECTIONS-LIST-01', 'local-edge-read-run', function='edge_read_case.execute_edge_read_case'),
    CaseBinding('LOCAL-EDGE-BOT-SEARCH-READ-01', 'local-edge-read-run', function='edge_search_case.execute_edge_search_case'),
    CaseBinding('LOCAL-FIXED-LOOP-01', 'local-fixed-loop-run'),
    CaseBinding('LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-REVOKE-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-ENSURE-BOT-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-FULL-LIFECYCLE-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01', 'local-edge-friend-mutations-run', function='case_executor.execute_case'),
    CaseBinding('LOCAL-SERVICE-POLICY-01', 'local-service-policy-run'),
    CaseBinding('LOCAL-AUTHORITY-JOURNEY-01', 'local-authority-run'),
)
DEFAULT_PANEL = Path(__file__).resolve().parents[3] / 'assets/panel/dist/index.umd.js'
DEFAULT_TEMPLATES = Path(__file__).resolve().parents[3] / 'seeds/collaboration-templates'
SUMMARY_NAME = 'suite-summary.json'
REPORT_NAME = 'suite-report.md'

def list_cases() -> dict:
    """Read-only inventory/mapping check; no binaries or state directory needed."""
    inventory = migration_plan()['local_registered_executors']
    bindings = {case.case_id: case for case in CASES}
    rows = []
    for item in inventory:
        case = bindings.get(item['case_id'])
        expected = (case.command + ''.join(' --' + flag.replace('_', '-') for flag in case.flags)
                    if case else '')
        source = item.get('execution_command', item.get('programmatic_execution_contract', ''))
        matches = bool(case) and (source == expected or (
            case.case_id == 'LOCAL-TRUST-API-CLI-01' and source == 'owned-local-trust-api-cli-v1'))
        rows.append({
            'case_id': item['case_id'], 'legacy_id': item['legacy_id'], 'suite': item['suite'],
            'inventory_binding': source, 'command': expected,
            'function': case.function if case else '',
            'required_parameters': ['binary', 'binary_sha256', 'state_dir', 'run_id',
                                    'confirm_run_id', 'allow_local_processes',
                                    'allow_fixture_write', 'allow_write']
                + (['cli_binary', 'cli_binary_sha256'] if case and case.cli else [])
                + (['panel_asset', 'panel_sha256'] if case and case.command == 'local-network-run' else [])
                + (['template_dir', 'template_manifest', 'template_manifest_sha256'] if case and case.command == 'local-structured-run' else []),
            'status': 'READY' if matches else 'BLOCKED',
        })
    ids = [row['case_id'] for row in rows]
    reviewed_parent_count = len(reviewed_release_inventory())
    aligned = (len(ids) == len(set(ids)) == len(CASES) == reviewed_parent_count
               and set(ids) == set(bindings) and all(row['status'] == 'READY' for row in rows))
    return {'schema_version': 1, 'status': 'READY' if aligned else 'BLOCKED',
            'case_count': len(rows), 'cases': rows, 'remote_validation': 'NOT_RUN',
            'grants_release_credit': False, 'ready_to_execute': False}

def _sha256(path: Path) -> str:
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()

def _binary_evidence(path: Path | None, expected: str | None) -> dict:
    result = {'path': str(path.resolve()) if path else None,
              'expected_sha256': expected, 'sha256': None, 'status': 'BLOCKED'}
    if path is None or expected is None:
        result['reason'] = 'binary_and_sha256_required'
        return result
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError()
        result['sha256'] = _sha256(path)
        if result['sha256'] == expected:
            result['status'] = 'READY'
        else:
            result['reason'] = 'binary_sha256_mismatch'
    except OSError:
        result['reason'] = 'binary_not_readable_regular_file'
    return result


def register_coverage_command(commands) -> None:
    parser = commands.add_parser('local-coverage-suite', help='Run all reviewed-release owned-local parents, sequentially and in isolated state directories')
    parser.add_argument('--template-dir', type=Path, default=DEFAULT_TEMPLATES)
    parser.add_argument('--list-cases', action='store_true', help='List explicit bindings without starting processes or creating state')
    for name in ('binary', 'cli-binary', 'state-dir', 'panel-asset', 'template-manifest'):
        parser.add_argument('--' + name, type=Path)
    for name in ('binary-sha256', 'cli-binary-sha256', 'run-id', 'confirm-run-id', 'panel-sha256', 'template-manifest-sha256'):
        parser.add_argument('--' + name)
    for name in ('allow-local-processes', 'allow-fixture-write', 'allow-write'):
        parser.add_argument('--' + name, action='store_true')
