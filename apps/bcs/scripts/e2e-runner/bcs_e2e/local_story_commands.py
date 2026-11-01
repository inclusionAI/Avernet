"""Dispatch and owned-local Story execution; roster/registration/evidence planes live in local_story_catalog."""


from __future__ import annotations

import re
import signal
import time

from argparse import Namespace
from collections import Counter
from importlib import import_module
from pathlib import Path
from typing import Callable

from .errors import has_cancellation
from .inventory import reviewed_release_inventory
from .local_backlog_story_commands import BACKLOG_STORY_COMMANDS, dispatch_backlog_story_command
from .profile import identifier
from .run_state import RunStateError, write


from .local_story_catalog import (
    CASES,
    CaseBinding,
    DEFAULT_PANEL,
    DEFAULT_TEMPLATES,
    LOCAL_STORY_COMMANDS,
    REPORT_NAME,
    SUMMARY_NAME,
    _binary_evidence,
    _sha256,
    list_cases,
    register_coverage_command,
    register_local_story_commands,
)





# Re-exported above: cli.py and the tests address these names through local_story_commands.


def dispatch_local_story_command(args, *, write_reports: Callable) -> tuple[dict, int]:
    if args.command in BACKLOG_STORY_COMMANDS:
        return dispatch_backlog_story_command(args, write_reports=write_reports)
    if args.command == 'local-coverage-suite':
        return dispatch_coverage_command(args)
    if args.command == 'local-event-run':
        from .local_event_run import local_event_run
        report=local_event_run(binary=args.binary,binary_sha256=args.binary_sha256,
            state_dir=args.state_dir,run_id=args.run_id,confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes,allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc=0 if report['status']=='LOCAL_EVENT_PASS' else 130 if report['status']=='CANCELLED' else 2
    elif args.command == 'local-event-report':
        from .local_event_reporting import build_event_report
        summary=build_event_report(args.state_dir)
        if args.output_dir is not None:
            source_root,destination=args.state_dir.resolve(),args.output_dir.resolve()
            if destination==source_root or source_root in destination.parents:
                raise RunStateError('Offline Event rendering cannot overwrite original evidence')
        outputs=write_reports(summary,args.output_dir,{'all'}) if args.output_dir is not None else {}
        report={'status':summary['status'],'summary':summary,'outputs':outputs,'ready_to_execute':False}
        rc=0 if summary['status']=='LOCAL_SUITE_PASS' else 130 if summary['status']=='CANCELLED' else 2
    elif args.command == 'local-structured-run':
        from .local_structured_run import local_structured_run
        report = local_structured_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write, template_dir=args.template_dir, template_manifest=args.template_manifest,
            template_manifest_sha256=args.template_manifest_sha256)
        rc = 0 if report['status'] == 'LOCAL_STRUCTURED_PASS' else 130 if report['status']=='CANCELLED' else 2
    elif args.command == 'local-structured-report':
        from .local_structured_reporting import build_structured_report
        summary = build_structured_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Structured rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status':summary['status'],'summary':summary,'outputs':outputs,'ready_to_execute':False}
        rc = 0 if summary['status']=='LOCAL_SUITE_PASS' else 130 if summary['status']=='CANCELLED' else 2
    elif args.command == 'local-direct-chat-run':
        from .local_direct_chat_run import local_direct_chat_run
        report = local_direct_chat_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_DIRECT_CHAT_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-direct-chat-report':
        from .local_direct_chat_reporting import build_direct_chat_report
        summary = build_direct_chat_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline direct-chat rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-slow-judge-run':
        from .local_slow_judge_run import local_slow_judge_run
        report = local_slow_judge_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_SLOW_JUDGE_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-slow-judge-report':
        from .local_slow_judge_reporting import build_slow_judge_report
        summary = build_slow_judge_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline slow-Judge rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-fixed-loop-run':
        from .local_fixed_loop_run import local_fixed_loop_run
        report = local_fixed_loop_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_FIXED_LOOP_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-fixed-loop-report':
        from .local_fixed_loop_reporting import build_fixed_loop_report
        summary = build_fixed_loop_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline fixed-Loop rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-edge-friend-mutations-run':
        from .local_edge_friend_mutations_run import local_edge_friend_mutations_run
        report = local_edge_friend_mutations_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_EDGE_FRIEND_MUTATIONS_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-edge-friend-mutations-report':
        from .local_edge_friend_mutations_reporting import build_edge_friend_mutations_report
        summary = build_edge_friend_mutations_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Edge friend-mutation rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-edge-read-run':
        from .local_edge_read_run import local_edge_read_run
        report = local_edge_read_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_EDGE_READ_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-edge-read-report':
        from .local_edge_read_reporting import build_edge_read_report
        summary = build_edge_read_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Edge read rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-channel-cli-run':
        from .local_channel_cli_run import local_channel_cli_run
        report = local_channel_cli_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_CHANNEL_CLI_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-channel-cli-report':
        from .local_channel_cli_reporting import build_channel_cli_report
        summary = build_channel_cli_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Channel CLI rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-channel-http-run':
        from .local_channel_run import local_channel_http_run
        report = local_channel_http_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_CHANNEL_HTTP_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-channel-http-report':
        from .local_channel_reporting import build_channel_http_report
        summary = build_channel_http_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Channel rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-delivery-policy-run':
        from .local_delivery_policy_run import local_delivery_policy_run
        report = local_delivery_policy_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_DELIVERY_POLICY_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-delivery-policy-report':
        from .local_delivery_policy_reporting import build_delivery_policy_report
        summary = build_delivery_policy_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Delivery policy rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2

    elif args.command == 'local-operator-cli-run':
        from .local_operator_cli_run import local_operator_cli_run
        report = local_operator_cli_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_OPERATOR_CLI_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-operator-cli-report':
        from .local_operator_cli_reporting import build_operator_cli_report
        summary = build_operator_cli_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Operator CLI rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-collaboration-team-run':
        from .local_collaboration_team_run import local_collaboration_team_run
        report = local_collaboration_team_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_COLLABORATION_TEAM_PASS' else 130 if report['status']=='CANCELLED' else 2
    elif args.command == 'local-collaboration-team-report':
        from .local_collaboration_team_reporting import build_collaboration_team_report
        summary = build_collaboration_team_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Collaboration-team rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status']=='CANCELLED' else 2
    elif args.command == 'local-provider-run':
        from .local_provider_run import local_provider_run
        report = local_provider_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_PROVIDER_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-provider-report':
        from .local_provider_reporting import build_provider_report
        summary = build_provider_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Provider rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command in ('local-session-public-run', 'local-session-sharing-run', 'local-session-run'):
        from .local_session_run import local_session_public_run
        report = local_session_public_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write, with_completion=args.command == 'local-session-run',
            **({'cli_binary': args.cli_binary, 'cli_binary_sha256': args.cli_binary_sha256}
               if args.command in ('local-session-sharing-run', 'local-session-run') else {}))
        rc = 0 if report['status'] in ('LOCAL_SESSION_PUBLIC_PASS', 'LOCAL_SESSION_SHARING_PASS', 'LOCAL_SESSION_FLOW_PASS') else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-session-report':
        from .local_session_flow_reporting import build_session_flow_report
        summary = build_session_flow_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Session flow rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-session-sharing-report':
        from .local_session_sharing_reporting import build_sharing_report
        summary = build_sharing_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Session sharing cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SLICE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-session-public-report':
        from .local_session_reporting import build_session_public_report
        summary = build_session_public_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Session rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SLICE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-network-run':
        from .local_network_run import local_network_run
        report = local_network_run(binary=args.binary, binary_sha256=args.binary_sha256,
            panel_asset=args.panel_asset, panel_sha256=args.panel_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_NETWORK_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-network-report':
        from .local_network_reporting import build_network_report
        summary = build_network_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Agent-network rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-workspace-report':
        from .local_workspace_reporting import build_workspace_report
        summary = build_workspace_report(args.state_dir)
        if args.output_dir is not None:
            source_root = args.state_dir.resolve()
            destination = args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Workspace rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-file-report':
        from .local_file_reporting import build_file_report
        summary = build_file_report(args.state_dir.resolve())
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 2
    elif args.command in ('local-workspace-api', 'local-workspace-run'):
        from .local_workspace_run import local_workspace_api_run
        report = local_workspace_api_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write,
            **({'cli_binary': args.cli_binary, 'cli_binary_sha256': args.cli_binary_sha256,
                'with_incident': args.with_incident_config, 'with_messages': args.with_messages, 'with_delete': args.with_incident_delete}
               if args.command == 'local-workspace-run' else {}))
        rc = 0 if report['status'] in ('LOCAL_WORKSPACE_API_PASS', 'LOCAL_WORKSPACE_API_CLI_PASS', 'LOCAL_WORKSPACE_CONFIG_PASS', 'LOCAL_WORKSPACE_MESSAGING_PASS', 'LOCAL_WORKSPACE_INCIDENT_ROUNDTRIP_PASS') else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-file-run':
        from .local_file_story import local_file_run
        report = local_file_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_SUITE_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-caller-execution-run':
        from .local_caller_execution_run import local_caller_execution_run
        report = local_caller_execution_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_CALLER_EXECUTION_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-caller-execution-report':
        from .local_caller_execution_reporting import build_caller_execution_report
        summary = build_caller_execution_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Caller execution rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-draft-group-run':
        from .local_draft_group_run import local_draft_group_run
        report = local_draft_group_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_DRAFT_GROUP_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-draft-group-report':
        from .local_draft_group_reporting import build_draft_group_report
        summary = build_draft_group_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Draft group rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-result-queue-run':
        from .local_result_queue_run import local_result_queue_run
        report = local_result_queue_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_RESULT_QUEUE_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-result-queue-report':
        from .local_result_queue_reporting import build_result_queue_report
        summary = build_result_queue_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Result queue rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-nested-fixed-loop-run':
        from .local_nested_fixed_loop_run import local_nested_fixed_loop_run
        report = local_nested_fixed_loop_run(binary=args.binary, binary_sha256=args.binary_sha256,
            cli_binary=args.cli_binary, driver=args.driver,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_NESTED_FIXED_LOOP_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-nested-fixed-loop-report':
        from .local_nested_fixed_loop_reporting import build_nested_fixed_loop_report
        summary = build_nested_fixed_loop_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline Nested fixed-loop rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-service-policy-run':
        from .local_service_policy_run import local_service_policy_run
        report = local_service_policy_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_SERVICE_POLICY_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-service-policy-report':
        from .local_service_policy_reporting import build_service_policy_report
        summary = build_service_policy_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline service policy rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    elif args.command == 'local-authority-run':
        from .local_authority_run import local_authority_run
        report = local_authority_run(binary=args.binary, binary_sha256=args.binary_sha256,
            state_dir=args.state_dir, run_id=args.run_id, confirm_run_id=args.confirm_run_id,
            allow_local_processes=args.allow_local_processes, allow_fixture_write=args.allow_fixture_write,
            allow_write=args.allow_write)
        rc = 0 if report['status'] == 'LOCAL_AUTHORITY_PASS' else 130 if report['status'] == 'CANCELLED' else 2
    elif args.command == 'local-authority-report':
        from .local_authority_reporting import build_authority_report
        summary = build_authority_report(args.state_dir)
        if args.output_dir is not None:
            source_root, destination = args.state_dir.resolve(), args.output_dir.resolve()
            if destination == source_root or source_root in destination.parents:
                raise RunStateError('Offline authority rendering cannot overwrite original evidence')
        outputs = write_reports(summary, args.output_dir, {'all'}) if args.output_dir is not None else {}
        report = {'status': summary['status'], 'summary': summary, 'outputs': outputs, 'ready_to_execute': False}
        rc = 0 if summary['status'] == 'LOCAL_SUITE_PASS' else 130 if summary['status'] == 'CANCELLED' else 2
    return report, rc


def _edge_run(case: CaseBinding, kwargs: dict) -> dict:
    """Supply one fresh runtime to existing Edge logic, not the batch wrappers."""
    from .case_executor import execute_case
    from .edge_friend_mutation_case import edge_friend_mutation_case_plan
    from .edge_read_case import execute_edge_read_case
    from .edge_search_case import execute_edge_search_case
    from .inventory import executor_digest
    from .local_edge_friend_mutations_case import (
        OwnedEdgeFriendFixtureAdmission, OwnedEdgeFriendMutationsAdapter,
    )
    from .local_fixtures import OwnedLocalFixtures
    from .local_role_identity import OwnedLocalRoleIdentity
    from .owned_bcs import OwnedBcsProcess
    from .owned_edge_read_adapter import (
        OwnedBotSearchFixtureAdmission, OwnedEdgeReadAdapter, OwnedEdgeSearchAdapter,
    )

    mutation = case.command == 'local-edge-friend-mutations-run'
    roles = ('CEO', 'PM', 'ENG', 'QA') if mutation else ('CEO',)
    source = executor_digest()
    with OwnedBcsProcess(binary=kwargs['binary'], binary_sha256=kwargs['binary_sha256'],
                         root=kwargs['state_dir'], run_id=kwargs['run_id'],
                         allow_ambient_local_identity=False) as runtime:
        fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
        with OwnedLocalRoleIdentity(fixtures, roles=roles) as identities:
            fixtures.apply(allow_write=True, role_identity=identities)
            for role in roles:
                identities.verify(role)
            if mutation:
                admission = OwnedEdgeFriendFixtureAdmission(runtime, bot_id=identities.actor_id('CEO'))
                adapter = OwnedEdgeFriendMutationsAdapter(
                    runtime, identities, admission, case_id=case.case_id, allow_write=True)
                execution = execute_case(edge_friend_mutation_case_plan(case.case_id), adapter)
            elif case.function == 'edge_search_case.execute_edge_search_case':
                admission = OwnedBotSearchFixtureAdmission(runtime)
                admission.admit(allow_write=True)
                execution = execute_edge_search_case(OwnedEdgeSearchAdapter(runtime, identities, admission))
            else:
                execution = execute_edge_read_case(case.case_id, OwnedEdgeReadAdapter(runtime, identities, case.case_id))
            snapshot = execution.snapshot()
            write(runtime.root / 'coverage-case-execution.json', {
                'schema_version': 1, 'run_id': kwargs['run_id'], 'executor_digest': source,
                'binary_sha256': kwargs['binary_sha256'], **snapshot,
            })
    shutdown = runtime._record()
    passed = (snapshot['status'] == 'PASS' and snapshot['error_type'] is None
              and snapshot['completed_count'] == snapshot['total_count']
              and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
              and shutdown.get('forced_stop') is False
              and identities.summary().get('credential_memory') == 'CLEARED'
              and executor_digest() == source)
    return {'status': 'PASS' if passed else 'BLOCKED', 'case': snapshot,
            'shutdown': shutdown, 'credentials': identities.summary()}

def _execute(case: CaseBinding, kwargs: dict, panel: dict) -> tuple[dict, int]:
    if case.command in ('local-edge-read-run', 'local-edge-friend-mutations-run'):
        report = _edge_run(case, kwargs)
        return report, 0 if report['status'] == 'PASS' else 2
    if case.command == 'local-trust-run':
        from .local_trust_cli import OwnedLocalTrustCli
        from .owned_bcs import local_runtime_smoke
        report = local_runtime_smoke(
            with_fixtures=True, fixture_set='trusted-team', verify_bot_identities=True,
            check_trust_baseline=True, run_trust_api=True,
            trust_cli=OwnedLocalTrustCli(kwargs['cli_binary'], kwargs['cli_binary_sha256']),
            binary=kwargs['binary'], binary_sha256=kwargs['binary_sha256'],
            root=kwargs['state_dir'], run_id=kwargs['run_id'])
        return report, 0 if report['status'] == 'LOCAL_TRUST_API_CLI_PASS' else 2
    if case.function:
        module, name = case.function.split('.')
        report = getattr(import_module('.' + module, __package__), name)(**kwargs)
        expected = {'run_custom_collaboration_case': 'LOCAL_CUSTOM_COLLABORATION_LIFECYCLE_PASS',
                    'execute_local_session_service_parent_case': 'LOCAL_SESSION_SERVICE_PARENT_PASS'}
        return report, 0 if report['status'] == expected[case.command] else 2
    from .reporting import write_reports
    args = Namespace(command=case.command, **kwargs)
    if case.command == 'local-workspace-run':
        for flag in ('with_incident_config', 'with_messages', 'with_incident_delete'):
            setattr(args, flag, flag in case.flags)
    if case.command == 'local-network-run':
        args.panel_asset, args.panel_sha256 = Path(panel['path']), panel['sha256']
    return dispatch_local_story_command(args, write_reports=write_reports)

def _cancelled(report: dict) -> bool:
    # Only outer/canonical cancellation is suite cancellation.  A successful
    # structured Case may intentionally contain CANCELLED state-machine effects.
    case = report.get('case')
    canonical = report.get('canonical_summary', report.get('canonical_report'))
    return (report.get('status') == 'CANCELLED'
            or report.get('error_type') == 'KeyboardInterrupt'
            or (isinstance(case, dict)
                and (case.get('status') == 'CANCELLED'
                     or case.get('error_type') == 'KeyboardInterrupt'))
            or (isinstance(canonical, dict) and canonical.get('status') == 'CANCELLED'))

def _outcome(report: dict, rc: int) -> dict:
    if rc == 130 or _cancelled(report):
        return {'status': 'CANCELLED', 'failure_reason': 'executor_cancelled'}
    if rc == 0 and (report.get('status') == 'PASS' or re.fullmatch(r'LOCAL_[A-Z_]+_PASS', str(report.get('status')))):
        canonical = report.get('canonical_summary', report.get('canonical_report', {}))
        if not canonical or canonical.get('status') == 'LOCAL_SUITE_PASS':
            return {'status': 'PASS', 'failure_reason': None}
    # Never echo exception messages, response bodies, tokens, or raw CLI output.
    status = report.get('status')
    error = report.get('error_type') or report.get('case', {}).get('error_type')
    reason = 'executor_returned_non_success'
    if isinstance(status, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', status):
        reason += ':' + status
    if isinstance(error, str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}', error):
        reason += ':' + error
    return {'status': 'BLOCKED' if status == 'BLOCKED' else 'FAILED', 'failure_reason': reason}

def _publish(root: Path, summary: dict, *, finished: bool) -> None:
    counts = Counter(row['status'] for row in summary['cases'])
    summary['counts'] = {status: counts[status] for status in ('PASS', 'FAILED', 'BLOCKED', 'CANCELLED', 'PENDING', 'RUNNING')}
    if finished:
        cancelled = counts['CANCELLED'] or summary.get('cancellation_requested', False)
        summary['status'] = ('CANCELLED' if cancelled else
                             'LOCAL_COVERAGE_SUITE_PASS' if counts['PASS'] == len(reviewed_release_inventory()) else
                             'BLOCKED' if counts['BLOCKED'] else 'LOCAL_COVERAGE_SUITE_FAIL')
        summary['exit_code'] = 130 if cancelled else 0 if counts['PASS'] == len(reviewed_release_inventory()) else 2
    write(root / SUMMARY_NAME, summary)
    lines = ['# Owned-local coverage suite', '', f"Run: `{summary['run_id']}`",
             f"Status: **{summary['status']}**", '',
             'Remote validation: NOT_RUN. Release credit: false. Failed state is retained.', '',
             f"BCS SHA256: `{summary['binaries']['bcs']['sha256']}`",
             f"CLI SHA256: `{summary['binaries']['cli']['sha256']}`", '',
             '| Case | Run | Status | Reason | State directory |', '|---|---|---|---|---|']
    for row in summary['cases']:
        values = (row['case_id'], row['run_id'], row['status'], row['failure_reason'] or '', row['state_dir'])
        lines.append('| ' + ' | '.join(str(value).replace('|', '\\|').replace('\n', ' ') for value in values) + ' |')
    temporary = root / '.suite-report.tmp'
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write('\n'.join(lines) + '\n')
    temporary.replace(root / REPORT_NAME)


# Sequential owned-local coverage aggregation. Kept here to preserve the
# artifact source-file budget without weakening the executor digest gate.


def local_coverage_suite(*, binary: Path, binary_sha256: str, state_dir: Path,
                         run_id: str, confirm_run_id: str,
                         allow_local_processes: bool, allow_fixture_write: bool, allow_write: bool,
                         cli_binary: Path | None = None, cli_binary_sha256: str | None = None,
                         panel_asset: Path | None = None, panel_sha256: str | None = None,
                         template_dir: Path = DEFAULT_TEMPLATES, template_manifest: Path | None = None,
                         template_manifest_sha256: str | None = None) -> dict:
    """Run all parents despite ordinary failures; cancellation stops dispatch."""
    identifier(run_id, 'coverage suite Run ID')
    if len(run_id) > 120:
        raise RunStateError('suite Run ID must leave room for the per-case suffix')
    if not (allow_local_processes is True and allow_fixture_write is True
            and allow_write is True and confirm_run_id == run_id):
        raise RunStateError('suite requires process/fixture/write permissions and exact Run confirmation')
    if state_dir.exists() or state_dir.is_symlink():
        raise RunStateError('suite requires a fresh state directory; existing evidence is retained')
    listing = list_cases()
    if listing['status'] != 'READY':
        return {**listing, 'status': 'BLOCKED', 'run_id': run_id, 'exit_code': 2,
                'failure_reason': 'inventory_mapping_drift', 'state_dir': str(state_dir.absolute())}
    root = state_dir.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / 'cases').mkdir(mode=0o700)
    binary, cli_binary = binary.absolute(), cli_binary.absolute() if cli_binary else None
    panel_path = (panel_asset or DEFAULT_PANEL).absolute()
    summary = {
        'schema_version': 1, 'suite': 'owned-local-coverage-suite-v1', 'status': 'RUNNING',
        'run_id': run_id, 'state_dir': str(root), 'case_count': len(reviewed_release_inventory()),
        'binaries': {'bcs': _binary_evidence(binary, binary_sha256),
                     'cli': _binary_evidence(cli_binary, cli_binary_sha256)},
        'panel_asset': {'path': str(panel_path), 'sha256': None, 'status': 'PENDING'},
        'templates': {'directory': str(template_dir.absolute()),
                      'manifest': str(template_manifest.absolute()) if template_manifest else None,
                      'sha256': template_manifest_sha256},
        'remote_validation': 'NOT_RUN', 'grants_release_credit': False,
        'full_release_status': 'NOT_QUALIFIED', 'entry_switch': 'NOT_PERFORMED',
        'automatic_cleanup': False, 'ready_to_execute': False,
        'outputs': {'summary': str(root / SUMMARY_NAME), 'report': str(root / REPORT_NAME)},
        'cases': [{**row, 'status': 'PENDING', 'run_id': f'{run_id}-{index:02d}',
                   'state_dir': str(root / 'cases' / row['case_id']),
                   'attempted': False, 'failure_reason': None}
                  for index, row in enumerate(listing['cases'], 1)],
    }
    bindings = {case.case_id: case for case in CASES}
    cancellation = False
    accepting_cancellation = True
    active = False
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def cancel(_signum, _frame):
        nonlocal cancellation
        if not accepting_cancellation:
            return
        summary['cancellation_requested'] = True
        first = not cancellation
        cancellation = True
        if first and active:
            raise KeyboardInterrupt()

    try:
        for sig in previous:
            signal.signal(sig, cancel)
        _publish(root, summary, finished=False)
        for row in summary['cases']:
            case = bindings[row['case_id']]
            if cancellation:
                row.update(status='CANCELLED', failure_reason='not_started_after_suite_cancellation')
                continue
            row['status'] = 'RUNNING'
            _publish(root, summary, finished=False)
            started = time.monotonic()
            try:
                active = True
                if cancellation:
                    raise KeyboardInterrupt()
                child = Path(row['state_dir'])
                if child.exists() or child.is_symlink():
                    row.update(status='BLOCKED', failure_reason='case_state_dir_already_exists')
                    continue
                invalid = next((name for name in ('bcs', 'cli') if (name == 'bcs' or case.cli)
                                and summary['binaries'][name]['status'] != 'READY'), None)
                if invalid:
                    row.update(status='BLOCKED', failure_reason=invalid + ':' + summary['binaries'][invalid]['reason'])
                    continue
                if case.command == 'local-network-run':
                    from .local_panel_asset import LocalPanelAsset
                    # Explicit path or actual default bundle; never generate a placeholder.
                    try:
                        digest = panel_sha256 if panel_sha256 is not None else _sha256(panel_path)
                        LocalPanelAsset(panel_path, digest).validate()
                        summary['panel_asset'].update(sha256=digest, status='READY')
                    except Exception as error:
                        summary['panel_asset'].update(status='BLOCKED')
                        row.update(status='BLOCKED', failure_reason='panel_asset_unavailable_or_invalid:' + type(error).__name__)
                        continue
                kwargs = dict(binary=binary, binary_sha256=binary_sha256, state_dir=child,
                              run_id=row['run_id'], confirm_run_id=row['run_id'],
                              allow_local_processes=True, allow_fixture_write=True, allow_write=True)
                if case.cli:
                    kwargs.update(cli_binary=cli_binary, cli_binary_sha256=cli_binary_sha256)
                if case.command == 'local-structured-run':
                    from .local_template_asset import LocalTemplateAsset
                    if template_manifest is None or template_manifest_sha256 is None:
                        row.update(status='BLOCKED', failure_reason='approved_template_manifest_and_sha256_required')
                        continue
                    try:
                        LocalTemplateAsset(template_dir, template_manifest, template_manifest_sha256).validate()
                    except Exception as error:
                        row.update(status='BLOCKED', failure_reason='template_asset_invalid:' + type(error).__name__)
                        continue
                    kwargs.update(template_dir=template_dir, template_manifest=template_manifest,
                                  template_manifest_sha256=template_manifest_sha256)
                row['attempted'] = True
                report, rc = _execute(case, kwargs, summary['panel_asset'])
                row.update(_outcome(report, rc))
                cancellation = cancellation or row['status'] == 'CANCELLED'
            except (Exception, KeyboardInterrupt) as error:
                cancellation = cancellation or has_cancellation(error)
                row.update(status='CANCELLED' if cancellation else 'BLOCKED' if isinstance(error, (ImportError, FileNotFoundError, RunStateError)) else 'FAILED',
                           failure_reason='executor_exception:' + type(error).__name__)
            finally:
                active = False
                if cancellation:
                    row.update(status='CANCELLED', failure_reason=row['failure_reason'] or 'suite_cancelled')
                row['duration_seconds'] = round(time.monotonic() - started, 3)
                _publish(root, summary, finished=False)
        summary['cancellation_requested'] = cancellation
        accepting_cancellation = False  # Seal the durable terminal result.
        _publish(root, summary, finished=True)
        return summary
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def dispatch_coverage_command(args) -> tuple[dict, int]:
    if args.list_cases:
        report = list_cases()
        return report, 0 if report['status'] == 'READY' else 2
    for name in ('binary', 'binary_sha256', 'state_dir', 'run_id', 'confirm_run_id'):
        if getattr(args, name) is None:
            raise RunStateError('local-coverage-suite requires --' + name.replace('_', '-'))
    report = local_coverage_suite(**{name: value for name, value in vars(args).items()
                                   if name not in ('command', 'list_cases')})
    return report, report['exit_code']
