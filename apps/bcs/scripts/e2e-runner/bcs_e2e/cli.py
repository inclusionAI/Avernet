"""CLI for the first, deliberately read-only implementation increment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters import AdapterCapabilityError, adapter_for
from .cleanup import execute_cleanup
from .recovery import recover_local_mock
from .operation_authority import AuthorizedAdapter
from .execution_safety import require_execution_safe
from .context import create_ephemeral_context, remove_ephemeral_context
from .fixtures import apply_fixture_plan, build_fixture_plan
from .local_fixtures import LocalFixtureError, FIXTURE_SETS
from .local_role_identity import LocalRoleIdentityError
from .trust_baseline import TrustBaselineError
from .local_trust_api import LocalTrustApiError
from .evidence import EvidenceError
from .local_run import local_run
from .local_cli import LocalCliError
from .local_story_commands import (LOCAL_STORY_COMMANDS, dispatch_local_story_command,
                                   register_local_story_commands)
from .ledger import Ledger, LedgerError
from .mock_state import MockStateError, validate_snapshot
from .mock_cleanup import verify_snapshot_scope
from .pilot import PilotError, run_case, run_pre01, run_pre02, run_pre03
from .remote_observer import remote_dependencies
from .remote_check import check_remote
from .remote_run import remote_run
from .workspace_cli_case_run import dual_workspace_cli_case_run
from .workspace_cli_status_run import dual_workspace_cli_status_run
from .workspace_group_case_run import dual_workspace_group_case_run
from .workspace_story_case_run import dual_workspace_story_case_run
from .session_cli_case_run import dual_session_cli_case_run
from .session_public_case_run import dual_session_public_case_run
from .session_file_core_case_run import dual_session_file_core_case_run
from .session_service_cli_case_run import dual_session_service_case_run
from .friend_cli_case_run import dual_friend_cli_case_run
from .channel_cli_case_run import dual_channel_cli_case_run
from .operator_cli_case_run import dual_operator_cli_case_run
from .direct_chat_cli_case_run import dual_direct_chat_case_run
from .delivery_policy_cli_case_run import dual_delivery_policy_case_run
from .draft_delivery.dual_case_run import dual_draft_delivery_case_run
from .service_policy.dual_case_run import dual_service_policy_case_run
from .provider_publishes.dual_case_run import dual_provider_publishes_case_run
from .slow_judge.dual_case_run import dual_slow_judge_case_run
from .provider_owner_dual_case_run import dual_provider_owner_case_run
from .session_owner.dual_case_run import dual_session_owner_case_run
from .authority.dual_case_run import dual_authority_case_run
from .provider_subbots_case_run import dual_provider_subbots_case_run
from .collaboration_team_case_run import dual_collaboration_team_cli_case_run
from .custom_collaboration_cli_case_run import dual_custom_collaboration_case_run
from .group_context_case_run import dual_group_context_cli_case_run
from .channel_bindings_case_run import dual_channel_bindings_case_run
from .network_register_case_run import dual_network_register_case_run
from .network_status_case_run import dual_network_actor_status_case_run
from .network_read_case_run import dual_network_read_case_run
from .structured_core_case_run import dual_structured_core_case_run
from .structured_templates_case_run import dual_structured_templates_case_run
from .webhook_subscription_case_run import dual_webhook_subscription_case_run
from .friend_connection_decision_case_run import dual_friend_connection_decision_case_run
from .friend_public_accept_case_run import dual_friend_public_accept_case_run
from .edge_friend_mutation_case_run import dual_edge_friend_mutation_case_run
from .edge_permission_backlog.case_run import dual_edge_permission_backlog_case_run
from .edge_read_case_run import dual_edge_read_case_run
from .case_command_registration import CASE_COMMANDS, register_case_commands
from .remote_reconcile import remote_reconcile
from .delivery_policy_check import policy_check
from .docker_binding import review_docker_binding_file
from .retained import retained_run
from .retained_recheck import retained_recheck
from .recheck_review import recheck_plan
from .transport_plan import transport_plan
from .transport_projection import match_transport
from .observ_transport import observe_retained
from .fixture_cron_check import fixture_cron_check
from .sessionless_run import sessionless_run
from .owned_bcs import local_runtime_smoke
from .pool import (PoolBusyError, PoolLease, PoolLeaseError, bound_pool_path,
                    inspect_pool, pool_path, pool_scope)
from .probe import http_probe, ws_probe
from .metadata_probe import probe_db_metadata
from .effect_policy import policy_summary
from .remote_effects import remote_effect_admission
from .identity_binding import (identity_request, pilot_execution_blocker, require_pilot_execution_binding,
                               human_actor_path)
from .inventory import current_manifest
from .migration_inventory import migration_plan
from .alisd_wire import AlisdWireError
from .reporting import build_report, write_reports
from .preflight import check
from .prepare import prepare_run
from .profile import ConfigError, fingerprint, load, require_resolved
from .pilot import evaluate
from .run_state import RunStateError, read as read_state, require_profile_binding, write as write_state
from .runtime import LocalSingleboxRuntime, RuntimeContractError, require_no_legacy_runtime
from .transport import ObservationError, ReadClient

ROOT = Path(__file__).resolve().parent.parent


def plan(profile: dict) -> dict:
    manifest = current_manifest()
    manifest_path = ("/api/v1/collaboration/manifest"
                     if profile["entrypoint"]["manifest_face"] == "internal-v1" else "/manifest")
    identity = identity_request(profile, owned_local=profile["auth"] == {"kind": "owned-local"})
    return {
        "schema_version": 1, "status": "PLANNED", "ready_to_execute": False,
        "profile_digest": fingerprint(profile), "suite": manifest["suite"],
        "baseline_commit": manifest["baseline_commit"],
        "entrypoint": profile["entrypoint"], "environment": profile["environment"],
        "cases": manifest["cases"], "legacy_inventory_count": len(manifest["legacy_inventory"]),
        "preflight_routes": [
            {"operation": "environment.read", "method": "GET", "path": manifest_path},
            *([{"operation": "identity.actor.read", "method": "GET", "path": human_actor_path(profile)}]
              if identity["response"] == "authorized-human-view" else []),
            {"operation": "identity.read", **identity},
            {"operation": "bot.read", "method": "GET", "path": "/openapi/v1/collaboration/bots/{bot_id}"},
        ],
        "execution_admission": {
            "status": "BLOCKED" if pilot_execution_blocker(profile) else "NOT_EVALUATED",
            "reason": pilot_execution_blocker(profile) or "offline plan does not admit execution",
        },
        "friendship": "not required, not forbidden, never mutated by pilot",
        "effect_policy": policy_summary(),
        "next_story_migrations": migration_plan(),
        "effect_admission": remote_effect_admission(profile),
        "remaining_before_qualification": ["approved remote identity/fixture/route bindings",
                                               "independent physical observer binding",
                                               "retained-data ownership and reclamation procedures",
                                               "preproduction pilot verification", "full release qualification"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BCS E2E pilot: offline plan and read-only discovery")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "check"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--profile", type=Path, required=True)
        if name == "check":
            cmd.add_argument("--allow-read", action="store_true",
                             help="Explicitly allow authenticated GETs to the profile origin")
            cmd.add_argument("--observer-binding", type=Path, help="Read an operator-reviewed preprod mapping receipt")
            cmd.add_argument("--mcporter-binary", type=Path)
            cmd.add_argument("--allow-observer-read", action="store_true", help="Allow metadata only; no business rows")
    cron = commands.add_parser('fixture-cron-check', help='Read exact fixture cron lists via the configured frontend; no absence/closure claim')
    cron.add_argument('--profile', type=Path, required=True)
    cron.add_argument('--allow-read', action='store_true')
    cron.add_argument('--confirm-human-id', required=True)
    cron.add_argument('--confirm-frontend-origin', required=True)
    policy = commands.add_parser('policy-check', help='Explicit read-only native BCN effective-policy discovery; no admission or writes')
    binding_review = commands.add_parser(
        "docker-binding-review",
        help="Offline 7.2 Aliyun target binding review; no container or network execution",
    )
    binding_review.add_argument("--binding", type=Path, required=True)
    policy.add_argument('--profile', type=Path, required=True)
    policy.add_argument('--review', type=Path, required=True)
    policy.add_argument('--allow-read', action='store_true')
    transport = commands.add_parser('retained-transport', help='Offline retained transport selectors or supplied-log matching; no network/credentials/release')
    transport.add_argument('--profile', type=Path, required=True)
    transport.add_argument('--state-dir', type=Path, required=True)
    transport.add_argument('--run-id', required=True)
    transport.add_argument('--case', choices=('RETAIN-GROUP-01', 'RETAIN-SESSION-01'), required=True)
    transport.add_argument('--observation-id', required=True)
    transport.add_argument('--start-epoch', type=int, required=True)
    transport.add_argument('--end-epoch', type=int, required=True)
    transport.add_argument('--projection', type=Path, help='Optional normalized log projection, not raw platform output')
    transport = commands.add_parser('retained-observ-ws', help='Explicit bounded Observ WS reads with fresh instance discovery; no business writes/release')
    transport.add_argument('--profile', type=Path, required=True)
    transport.add_argument('--state-dir', type=Path, required=True)
    transport.add_argument('--run-id', required=True)
    transport.add_argument('--case', choices=('RETAIN-GROUP-01', 'RETAIN-SESSION-01'), required=True)
    transport.add_argument('--observation-id', required=True)
    transport.add_argument('--start-epoch', type=int, required=True)
    transport.add_argument('--end-epoch', type=int, required=True)
    transport.add_argument('--log-binding', type=Path, required=True)
    transport.add_argument('--observ-binary', type=Path, required=True)
    transport.add_argument('--output-dir', type=Path, required=True)
    transport.add_argument('--allow-observer-read', action='store_true')
    transport.add_argument('--confirm-run-id', required=True)
    pool = commands.add_parser("pool", help="Inspect a local fixture-pool reservation without changing it")
    pool_commands = pool.add_subparsers(dest="pool_action", required=True)
    pool_inspect = pool_commands.add_parser("inspect")
    pool_inspect.add_argument("--profile", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="Create a private local run ledger and fixture-pool lease")
    prepare.add_argument("--profile", type=Path, required=True)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--state-dir", type=Path, required=True)
    prepare.add_argument("--observer-binding", type=Path, help="Freeze a private operator-reviewed preprod mapping receipt")
    release = commands.add_parser("release", help="Release only the matching local fixture-pool lease")
    release.add_argument("--state-dir", type=Path, required=True)
    run = commands.add_parser("run", help="Execute an explicitly allowed pilot Story")
    run.add_argument("--profile", type=Path, required=True)
    run.add_argument("--state-dir", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--case", choices=("PRE-01", "PRE-02", "PRE-03"), required=True)
    run.add_argument("--allow-write", action="store_true")
    run.add_argument("--confirm-run-id", required=True)
    run.add_argument("--mcporter-binary", type=Path, help="Explicit MCP executable for a reviewed preprod Run")
    run.add_argument("--allow-observer-read", action="store_true", help="Allow MCP metadata and current-Case row queries")
    retained = commands.add_parser("retained-run", help="Run one retained slice locally or through explicitly reviewed preprod attachment")
    retained.add_argument("--profile", type=Path, required=True)
    retained.add_argument("--state-dir", type=Path, required=True)
    retained.add_argument("--run-id", required=True)
    retained.add_argument("--case", choices=("RETAIN-GROUP-01", "RETAIN-SESSION-01", "RETAIN-FILE-01"), required=True)
    retained.add_argument("--allow-write", action="store_true")
    retained.add_argument("--confirm-run-id", required=True)
    retained.add_argument("--retained-review", type=Path)
    retained.add_argument("--observer-binding", type=Path)
    retained.add_argument("--mcporter-binary", type=Path)
    retained.add_argument("--allow-read", action="store_true")
    retained.add_argument("--allow-observer-read", action="store_true")
    retained.add_argument('--serial-preflight', action='store_true',
                          help='Hold one trigger guard through fresh checks and execution; no historical closure prerequisite')
    retained.add_argument('--previous-state-dir', type=Path,
                          help='Exact prior retained Run for scheduling handoff, not cleanup or replay')
    retained.add_argument('--previous-profile', type=Path, help='Exact old Profile for a reviewed storage-origin-only handoff')
    retained.add_argument('--profile-transition-review', type=Path, help='Current Run-bound storage-origin transition receipt')
    gate = commands.add_parser("gate", help="Evaluate full release qualification without releasing the pool")
    gate.add_argument("--profile", type=Path, required=True)
    gate.add_argument("--state-dir", type=Path, required=True)

    report = commands.add_parser("report", help="Render JSON/JUnit/Markdown results for one Run")
    report.add_argument("--profile", type=Path, required=True)
    report.add_argument("--state-dir", type=Path, required=True)
    report.add_argument("--output-dir", type=Path)
    report.add_argument("--format", choices=("all", "json", "junit", "markdown"), default="all")
    fixture = commands.add_parser("fixture", help="Plan or explicitly apply one-time fixture initialization")
    fixture.add_argument("action", choices=("plan", "apply"))
    fixture.add_argument("--profile", type=Path, required=True)
    fixture.add_argument("--state-dir", type=Path, required=True)
    fixture.add_argument("--run-id", default="fixture-plan")
    fixture.add_argument("--allow-write", action="store_true")
    fixture.add_argument("--confirm-run-id")
    reconcile = commands.add_parser("remote-reconcile", help="Reconcile only a failed reviewed Run's exact live resources before standard cleanup")
    reconcile.add_argument("--profile", type=Path, required=True)
    reconcile.add_argument("--state-dir", type=Path, required=True)
    reconcile.add_argument("--run-id", required=True)
    reconcile.add_argument("--allow-read", action="store_true")
    reconcile.add_argument("--allow-write", action="store_true")
    reconcile.add_argument("--confirm-run-id", required=True)
    cleanup = commands.add_parser("cleanup", help="Dry-run or execute exact current-Run resource cleanup")
    cleanup.add_argument("--profile", type=Path, required=True)
    cleanup.add_argument("--state-dir", type=Path, required=True)
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--allow-write", action="store_true")
    cleanup.add_argument("--confirm-run-id")
    recover = commands.add_parser("recover-local", help="Explicit local mock cleanup using existing terminal proof")
    recover.add_argument("--profile", type=Path, required=True)
    recover.add_argument("--state-dir", type=Path, required=True)
    recover.add_argument("--allow-write", action="store_true")
    recover.add_argument("--confirm-run-id", required=True)
    recheck = commands.add_parser("retained-recheck", help="Read-only evidence for an existing quarantined retained Run; never replay or release")
    recheck.add_argument("--profile", type=Path, required=True)
    recheck.add_argument("--state-dir", type=Path, required=True)
    recheck.add_argument("--run-id", required=True)
    recheck.add_argument("--case", choices=("RETAIN-GROUP-01", "RETAIN-SESSION-01"), required=True)
    recheck.add_argument("--dry-run", action="store_true", help="Offline source digests and non-authorizing review template")
    recheck.add_argument("--review", "--read-plan", dest="review", type=Path)
    recheck.add_argument("--mapping-confirmation", type=Path, help="Persistent environment facts for the separate v2 source-bound read plan")
    recheck.add_argument("--observer-binding", type=Path)
    recheck.add_argument("--mcporter-binary", type=Path)
    recheck.add_argument("--allow-observer-read", action="store_true")
    recheck.add_argument("--confirm-run-id")
    context = commands.add_parser("context", help="Create or remove an isolated non-home runner context")
    context_commands = context.add_subparsers(dest="context_action", required=True)
    context_create = context_commands.add_parser("create")
    context_create.add_argument("--profile", type=Path, required=True)
    context_create.add_argument("--context-dir", type=Path, required=True)
    context_remove = context_commands.add_parser("remove")
    context_remove.add_argument("--context-dir", type=Path, required=True)
    probe = commands.add_parser("probe", help="Run an explicit read-only transport probe")
    probe_commands = probe.add_subparsers(dest="probe_action", required=True)
    probe_metadata = probe_commands.add_parser("db-metadata", help="Read fixed DB/table/column metadata; never admit a remote environment")
    probe_metadata.add_argument("--scope", choices=("pilot", "execution"), default="pilot",
                                help="Select fixed resource or execution-state metadata; never query rows")
    probe_metadata.add_argument("--server", required=True)
    probe_metadata.add_argument("--database", required=True)
    probe_metadata.add_argument("--mcporter-binary", type=Path, required=True)
    probe_metadata.add_argument("--timeout-seconds", type=int, default=30)
    probe_metadata.add_argument("--allow-read", action="store_true")
    probe_http = probe_commands.add_parser("http")
    probe_http.add_argument("--profile", type=Path, required=True)
    probe_http.add_argument("--operation", choices=("environment.read", "identity.read", "bot.read"), required=True)
    probe_http.add_argument("--bot-id")
    probe_http.add_argument("--allow-read", action="store_true")
    probe_ws = probe_commands.add_parser("ws")
    probe_ws.add_argument("--profile", type=Path, required=True)
    probe_ws.add_argument("--state-dir", type=Path, required=True)
    probe_ws.add_argument("--run-id", required=True)
    probe_ws.add_argument("--token-env", required=True)
    probe_ws.add_argument("--group-id", required=True)
    probe_ws.add_argument("--session-id", required=True)
    probe_ws.add_argument("--allow-read", action="store_true")
    runtime = commands.add_parser("runtime", help="Inspect LocalSinglebox boundaries or run an explicitly owned local smoke")
    runtime.add_argument("action", choices=("check", "start", "stop", "status", "smoke", "fixture-smoke"))
    runtime.add_argument("--profile", type=Path, required=True)
    runtime.add_argument("--state-dir", type=Path, required=True)
    runtime.add_argument("--runtime-dir", type=Path)
    runtime.add_argument("--binary", type=Path)
    runtime.add_argument("--binary-sha256")
    runtime.add_argument("--run-id")
    runtime.add_argument("--panel-asset", type=Path, help="Opt-in real local manifest bundle for owned smoke only")
    runtime.add_argument("--panel-sha256", help="Approved panel artifact SHA256; paired with --panel-asset")
    runtime.add_argument("--port", type=int, default=0)
    runtime.add_argument("--allow-local-processes", action="store_true")
    runtime.add_argument("--allow-fixture-write", action="store_true")
    runtime.add_argument("--fixture-set", choices=tuple(FIXTURE_SETS), default="pilot",
                         help="Owned-local fixture-smoke only; does not execute or admit a Story")
    runtime.add_argument("--verify-bot-identities", action="store_true",
                         help="Verify generated expanded-set Bot credentials using local read-only operations")
    runtime.add_argument("--check-trust-baseline", action="store_true",
                         help="Read-only relationship and visibility baseline for verified local trusted-team roles")
    local = commands.add_parser("local-run", help="Run the three pilot Stories in a fresh owned local BCS; no profile required")
    local.add_argument("--state-dir", type=Path, required=True)
    local.add_argument("--binary", type=Path, required=True)
    local.add_argument("--binary-sha256", required=True)
    local.add_argument("--run-id", required=True)
    local.add_argument("--local-logs", action="store_true", help="Collect optional request-log diagnostics from this owned runtime")
    local.add_argument("--cli-binary", type=Path, help="Opt in to the separate local product CLI session-read check")
    local.add_argument("--cli-binary-sha256", help="Approved product CLI SHA256; required with --cli-binary")
    local.add_argument("--port", type=int, default=0)
    local.add_argument("--allow-local-processes", action="store_true")
    local.add_argument("--allow-fixture-write", action="store_true")
    local.add_argument("--allow-write", action="store_true")
    local.add_argument("--confirm-run-id", required=True)
    register_case_commands(commands)
    remote = commands.add_parser("remote-run", help="Check, prepare, execute and report the reviewed preprod pilot; never manage remote services")
    remote.add_argument("--profile", type=Path, required=True)
    remote.add_argument("--state-dir", type=Path, required=True)
    remote.add_argument("--run-id", required=True)
    remote.add_argument("--observer-binding", type=Path, required=True)
    remote.add_argument("--mcporter-binary", type=Path, required=True)
    remote.add_argument("--allow-read", action="store_true")
    remote.add_argument("--allow-observer-read", action="store_true")
    remote.add_argument("--allow-write", action="store_true")
    remote.add_argument("--confirm-run-id", required=True)
    trust = commands.add_parser('local-trust-run', help='Run the owned-local legacy friendship API slice; CLI parent remains unmigrated')
    trust.add_argument('--state-dir', type=Path, required=True)
    trust.add_argument('--binary', type=Path, required=True)
    trust.add_argument('--binary-sha256', required=True)
    trust.add_argument('--run-id', required=True)
    trust.add_argument('--cli-binary', type=Path, help='Opt in to the actual trusted-team CLI friendship sequence')
    trust.add_argument('--cli-binary-sha256', help='Explicit approved CLI binary SHA256')
    trust.add_argument('--allow-local-processes', action='store_true')
    trust.add_argument('--allow-fixture-write', action='store_true')
    trust.add_argument('--allow-write', action='store_true')
    trust.add_argument('--confirm-run-id', required=True)
    file_setup = commands.add_parser(
        'local-file-setup',
        help='Create the owned PM/ENG group and two Sessions for the unfinished CLI-file Story')
    file_setup.add_argument('--state-dir', type=Path, required=True)
    file_setup.add_argument('--binary', type=Path, required=True)
    file_setup.add_argument('--binary-sha256', required=True)
    file_setup.add_argument('--cli-binary', type=Path, required=True)
    file_setup.add_argument('--cli-binary-sha256', required=True)
    file_setup.add_argument('--run-id', required=True)
    file_setup.add_argument('--allow-local-processes', action='store_true')
    file_setup.add_argument('--allow-fixture-write', action='store_true')
    file_setup.add_argument('--allow-write', action='store_true')
    file_setup.add_argument('--confirm-run-id', required=True)
    register_local_story_commands(commands)
    native = commands.add_parser("sessionless-run", help="Independent native group CRUD; not PRE-01/02/03")
    native.add_argument("--profile", type=Path, required=True)
    native.add_argument("--state-dir", type=Path, required=True)
    native.add_argument("--run-id", required=True)
    native.add_argument("--source-binding", type=Path, required=True)
    native.add_argument("--source-review", type=Path, required=True)
    native.add_argument("--observer-binding", type=Path, required=True)
    native.add_argument("--mcporter-binary", type=Path, required=True)
    native.add_argument("--allow-read", action="store_true")
    native.add_argument("--allow-observer-read", action="store_true")
    native.add_argument("--allow-write", action="store_true")
    native.add_argument("--confirm-run-id", required=True)
    args = parser.parse_args(argv)
    try:
        profile = None
        metadata_probe = args.command == "probe" and args.probe_action == "db-metadata"
        commands_without_profile = {"release", "context", "local-run",
                                     "local-trust-run", "local-file-setup",
                                     *CASE_COMMANDS,
                                     "docker-binding-review", *LOCAL_STORY_COMMANDS}
        if not metadata_probe and args.command not in commands_without_profile:
            profile = load(args.profile)
        elif args.command == "context" and args.context_action == "create":
            profile = load(args.profile)
        if profile is not None and profile.get('schema_version') == 2 and args.command != 'report':
            raise ConfigError('trusted-team Profile command integration is not yet available; use its owned local-trust-run lifecycle')
        if metadata_probe:
            report = probe_db_metadata(server=args.server, database=args.database,
                                       executable=args.mcporter_binary, allow_read=args.allow_read,
                                       timeout_seconds=args.timeout_seconds, scope=args.scope)
            rc = 0 if report['status'] == 'METADATA_COMPATIBLE' else 2
        elif args.command == 'fixture-cron-check':
            report = fixture_cron_check(profile, allow_read=args.allow_read, confirm_human_id=args.confirm_human_id,
                                        confirm_frontend_origin=args.confirm_frontend_origin)
            rc = 0 if report['status'] == 'OBSERVED' else 130 if report['status'] == 'CANCELLED' else 2
        elif args.command == 'policy-check':
            report = policy_check(profile, review_path=args.review, allow_read=args.allow_read)
            rc = 0 if report['status'] == 'POLICY_OBSERVED' else 130 if report['status'] == 'CANCELLED' else 2
        elif args.command == "docker-binding-review":
            report = review_docker_binding_file(args.binding)
            rc = 0 if report["ready_to_execute"] else 2
        elif args.command == 'retained-observ-ws':
            report = observe_retained(profile, state_dir=args.state_dir, run_id=args.run_id,
                case_id=args.case, observation_id=args.observation_id,
                start_epoch=args.start_epoch, end_epoch=args.end_epoch,
                binding_path=args.log_binding, executable=args.observ_binary, output_dir=args.output_dir,
                allow_read=args.allow_observer_read, confirm_run_id=args.confirm_run_id)
            rc = 0 if report['status'] == 'TRANSPORT_OBSERVED' else 130 if report['status'] == 'CANCELLED' else 2
        elif args.command == 'retained-transport':
            report = transport_plan(profile, args.state_dir, args.run_id, args.case,
                                    args.observation_id, args.start_epoch, args.end_epoch)
            if args.projection is not None:
                report = match_transport(report, read_state(args.projection))
            rc = 0 if report.get('status') == 'PLANNED_OFFLINE' or report.get('matching_result') == 'MATCHED' else 2
        elif args.command == "retained-recheck":
            if args.dry_run:
                if args.observer_binding is not None and args.mapping_confirmation is not None:
                    raise ConfigError('choose a legacy observer receipt or persistent mapping, never both')
                report = recheck_plan(profile, args.state_dir, args.run_id, args.case,
                                      mapping_confirmation=args.mapping_confirmation)
                rc = 0
            else:
                if args.review is None or args.mcporter_binary is None or (args.observer_binding is None) == (args.mapping_confirmation is None):
                    raise ConfigError("retained-recheck requires a read plan/review, exactly one environment binding and an explicit MCP executable")
                report = retained_recheck(profile, state_dir=args.state_dir, run_id=args.run_id, case_id=args.case,
                    review_path=args.review, observer_binding=args.observer_binding, executable=args.mcporter_binary,
                    allow_observer_read=args.allow_observer_read, confirm_run_id=args.confirm_run_id,
                    mapping_confirmation=args.mapping_confirmation)
                rc = 130 if report['status'] == 'CANCELLED' else 2
        elif args.command == "retained-run":
            report = retained_run(profile, state_dir=args.state_dir, run_id=args.run_id,
                                  case_id=args.case, allow_write=args.allow_write,
                                  confirm_run_id=args.confirm_run_id, retained_review=args.retained_review,
                                  observer_binding=args.observer_binding, executable=args.mcporter_binary,
                                  allow_http_read=args.allow_read, allow_observer_read=args.allow_observer_read,
                                  serial_preflight=args.serial_preflight, previous_state_dir=args.previous_state_dir,
                                  previous_profile=args.previous_profile, profile_transition_review=args.profile_transition_review)
            rc = (130 if report['status'] == 'CANCELLED' else 0
                  if report.get('canonical_summary_status') == 'RETAINED_CASE_PASS' else 2)
        elif args.command in LOCAL_STORY_COMMANDS:
            report, rc = dispatch_local_story_command(args, write_reports=write_reports)
        elif args.command == 'local-file-setup':
            from .local_file_setup import local_file_setup
            report = local_file_setup(
                binary=args.binary, binary_sha256=args.binary_sha256,
                cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
                state_dir=args.state_dir, run_id=args.run_id,
                allow_local_processes=args.allow_local_processes,
                allow_fixture_write=args.allow_fixture_write, allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id)
            rc = 0 if report['status'] == 'LOCAL_CLI_FILE_SETUP_PASS' else 2
        elif args.command == 'local-trust-run':
            if (not args.allow_local_processes or not args.allow_fixture_write or not args.allow_write
                    or args.confirm_run_id != args.run_id):
                raise ConfigError('local-trust-run requires process, fixture and business write permission plus exact Run confirmation')
            if args.state_dir.exists() or args.state_dir.is_symlink():
                raise ConfigError('local-trust-run requires a fresh state directory')
            from .local_trust_cli import OwnedLocalTrustCli
            if (args.cli_binary is None) != (args.cli_binary_sha256 is None):
                raise ConfigError('CLI trust execution requires both binary and SHA256')
            trust_cli = OwnedLocalTrustCli(args.cli_binary, args.cli_binary_sha256) if args.cli_binary is not None else None
            report = local_runtime_smoke(with_fixtures=True, fixture_set='trusted-team', trust_cli=trust_cli,
                verify_bot_identities=True, check_trust_baseline=True, run_trust_api=True,
                binary=args.binary, binary_sha256=args.binary_sha256, root=args.state_dir.resolve(), run_id=args.run_id)
            rc = 0 if report['status'] in ('LOCAL_TRUST_API_SLICE_PASS', 'LOCAL_TRUST_API_CLI_PASS') else 2
        elif args.command == "local-run":
            report = local_run(binary=args.binary, binary_sha256=args.binary_sha256,
                               state_dir=args.state_dir, run_id=args.run_id, port=args.port,
                               allow_local_processes=args.allow_local_processes,
                               allow_fixture_write=args.allow_fixture_write, allow_write=args.allow_write,
                               confirm_run_id=args.confirm_run_id,
                               cli_binary=args.cli_binary, cli_binary_sha256=args.cli_binary_sha256,
                               collect_local_logs=args.local_logs)
            rc = 0 if report["status"] == "LOCAL_PILOT_PASS" else 130 if report["status"] == "CANCELLED" else 2
        elif args.command == "sessionless-run":
            report = sessionless_run(profile, state_dir=args.state_dir, run_id=args.run_id,
                review_path=args.source_binding, source_review=args.source_review,
                receipt=args.observer_binding, executable=args.mcporter_binary,
                allow_read=args.allow_read, allow_observer_read=args.allow_observer_read,
                allow_write=args.allow_write, confirm_run_id=args.confirm_run_id)
            rc = 0 if report['status'] == 'PASS' else 130 if report['status'] == 'CANCELLED' else 2
        elif args.command == "remote-run":
            report = remote_run(profile, state_dir=args.state_dir, run_id=args.run_id,
                                receipt=args.observer_binding, executable=args.mcporter_binary,
                                allow_http_read=args.allow_read, allow_observer_read=args.allow_observer_read,
                                allow_write=args.allow_write, confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "REMOTE_PILOT_PASS" else 130 if report["status"] == "CANCELLED" else 2
        elif args.command == "dual-workspace-cli-status-run":
            report = dual_workspace_cli_status_run(profile_path=args.profile, cli_binary=args.cli_binary,
                                                   cli_binary_sha256=args.cli_binary_sha256,
                                                   state_dir=args.state_dir, run_id=args.run_id,
                                                   allow_write=args.allow_write, confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-workspace-run":
            report = dual_workspace_cli_case_run(profile_path=args.profile,
                                                 cli_binary=args.cli_binary,
                                                 cli_binary_sha256=args.cli_binary_sha256,
                                                 case_key=args.case, state_dir=args.state_dir,
                                                 run_id=args.run_id, allow_write=args.allow_write,
                                                 confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-workspace-group-run":
            report = dual_workspace_group_case_run(profile_path=args.profile, case_key=args.case,
                                                  state_dir=args.state_dir, run_id=args.run_id,
                                                  allow_write=args.allow_write, confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-workspace-story-run":
            report = dual_workspace_story_case_run(profile_path=args.profile,
                                                   case_key=args.case,
                                                   state_dir=args.state_dir,
                                                   run_id=args.run_id,
                                                   allow_write=args.allow_write,
                                                   confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-session-run":
            report = dual_session_cli_case_run(profile_path=args.profile,
                                               cli_binary=args.cli_binary,
                                               cli_binary_sha256=args.cli_binary_sha256,
                                               case_key=args.case, state_dir=args.state_dir,
                                               run_id=args.run_id, allow_write=args.allow_write,
                                               confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-session-service-run":
            report = dual_session_service_case_run(profile_path=args.profile,
                                                   cli_binary=args.cli_binary,
                                                   cli_binary_sha256=args.cli_binary_sha256,
                                                   case_key=args.case,
                                                   state_dir=args.state_dir,
                                                   run_id=args.run_id,
                                                   allow_write=args.allow_write,
                                                   confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-session-public-run":
            report = dual_session_public_case_run(
                profile_path=args.profile,
                binary=args.binary,
                binary_sha256=args.binary_sha256,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-collaboration-team-run":
            report = dual_collaboration_team_cli_case_run(
                profile_path=args.profile,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-custom-collaboration-run":
            report = dual_custom_collaboration_case_run(
                profile_path=args.profile,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-group-context-run":
            report = dual_group_context_cli_case_run(
                profile_path=args.profile,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-channel-bindings-run":
            report = dual_channel_bindings_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-network-register-run":
            report = dual_network_register_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-structured-core-run":
            report = dual_structured_core_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-structured-templates-run":
            report = dual_structured_templates_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-webhook-subscription-run":
            report = dual_webhook_subscription_case_run(
                profile_path=args.profile,
                receiver_origin=args.receiver_origin,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-friend-connection-decisions-run":
            report = dual_friend_connection_decision_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-friend-public-accept-run":
            report = dual_friend_public_accept_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-edge-friend-mutation-run":
            report = dual_edge_friend_mutation_case_run(
                profile_path=args.profile,
                station_profile_path=args.station_profile,
                case_key=args.case,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-edge-permission-backlog-run":
            report = dual_edge_permission_backlog_case_run(
                profile_path=args.profile,
                case_key=args.case,
                state_dir=args.state_dir,
                run_id=args.run_id,
                confirm_run_id=args.confirm_run_id,
                allow_write=args.allow_write,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-network-status-run":
            report = dual_network_actor_status_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-edge-read-run":
            report = dual_edge_read_case_run(
                profile_path=args.profile,
                case_key=args.case,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-network-read-run":
            report = dual_network_read_case_run(
                profile_path=args.profile,
                case_key=args.case,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-channel-run":
            report = dual_channel_cli_case_run(profile_path=args.profile,
                                               cli_binary=args.cli_binary,
                                               cli_binary_sha256=args.cli_binary_sha256,
                                               state_dir=args.state_dir, run_id=args.run_id,
                                               allow_write=args.allow_write,
                                               confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-operator-cli-run":
            report = dual_operator_cli_case_run(profile_path=args.profile,
                                                cli_binary=args.cli_binary,
                                                cli_binary_sha256=args.cli_binary_sha256,
                                                state_dir=args.state_dir, run_id=args.run_id,
                                                allow_write=args.allow_write,
                                                confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-direct-chat-run":
            report = dual_direct_chat_case_run(profile_path=args.profile,
                                               cli_binary=args.cli_binary,
                                               cli_binary_sha256=args.cli_binary_sha256,
                                               case_key=args.case, state_dir=args.state_dir,
                                               run_id=args.run_id, allow_write=args.allow_write,
                                               confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-delivery-policy-run":
            report = dual_delivery_policy_case_run(profile_path=args.profile,
                                                   cli_binary=args.cli_binary,
                                                   cli_binary_sha256=args.cli_binary_sha256,
                                                   case_key=args.case, state_dir=args.state_dir,
                                                   run_id=args.run_id, allow_write=args.allow_write,
                                                   confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-provider-subbots-run":
            report = dual_provider_subbots_case_run(
                profile_path=args.profile,
                case_key=args.case,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
                binary=getattr(args, "binary", None),
                binary_sha256=getattr(args, "binary_sha256", None),
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-draft-delivery-run":
            report = dual_draft_delivery_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
                binary=args.binary,
                binary_sha256=args.binary_sha256,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-service-policy-run":
            report = dual_service_policy_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
                binary=args.binary,
                binary_sha256=args.binary_sha256,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-provider-publishes-run":
            report = dual_provider_publishes_case_run(
                profile_path=args.profile,
                binary=args.binary,
                binary_sha256=args.binary_sha256,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
                state_dir=args.state_dir,
                run_id=args.run_id,
                confirm_run_id=args.confirm_run_id,
                allow_write=args.allow_write,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-slow-judge-run":
            report = dual_slow_judge_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
                binary=args.binary,
                binary_sha256=args.binary_sha256,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
                leaf_root=args.leaf_root,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-provider-owner-run":
            report = dual_provider_owner_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
                binary=args.binary,
                binary_sha256=args.binary_sha256,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
                leaf_root=getattr(args, "leaf_root", None),
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-session-owner-run":
            report = dual_session_owner_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
                binary=args.binary,
                binary_sha256=args.binary_sha256,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
                leaf_root=getattr(args, "leaf_root", None),
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-authority-run":
            report = dual_authority_case_run(
                profile_path=args.profile,
                state_dir=args.state_dir,
                run_id=args.run_id,
                allow_write=args.allow_write,
                confirm_run_id=args.confirm_run_id,
                binary=args.binary,
                binary_sha256=args.binary_sha256,
                cli_binary=args.cli_binary,
                cli_binary_sha256=args.cli_binary_sha256,
            )
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-session-file-run":
            report = dual_session_file_core_case_run(profile_path=args.profile,
                                                    cli_binary=args.cli_binary,
                                                    cli_binary_sha256=args.cli_binary_sha256,
                                                    state_dir=args.state_dir,
                                                    run_id=args.run_id,
                                                    allow_write=args.allow_write,
                                                    confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "dual-friend-run":
            report = dual_friend_cli_case_run(profile_path=args.profile,
                                              cli_binary=args.cli_binary,
                                              cli_binary_sha256=args.cli_binary_sha256,
                                              state_dir=args.state_dir,
                                              run_id=args.run_id,
                                              allow_write=args.allow_write,
                                              confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "probe":
            if args.probe_action == "http" and args.operation == "identity.read":
                # Identity discovery needs no Bot or physical DB binding. Keep
                # unresolved unrelated inputs out of this one-operation probe;
                # check/prepare/run still require the complete Profile.
                require_resolved({key: profile[key] for key in
                                  ("entrypoint", "auth", "human", "browser_origin") if key in profile})
            else:
                require_resolved(profile)
            if args.probe_action == "http":
                if not args.allow_read:
                    raise ConfigError("probe http requires --allow-read")
                report = http_probe(profile, args.operation, bot_id=args.bot_id or "")
            else:
                if not args.allow_read:
                    raise ConfigError("probe ws requires --allow-read")
                report = ws_probe(profile, args.state_dir, args.run_id,
                                  token_env=args.token_env, group_id=args.group_id,
                                  session_id=args.session_id)
            rc = 0 if report["status"] == "PROBE_PASSED" else 130 if report["status"] == "CANCELLED" else 2
        elif args.command == "context":
            if args.context_action == "create":
                report = create_ephemeral_context(profile, args.context_dir)
            else:
                report = remove_ephemeral_context(args.context_dir)
            rc = 0
        elif args.command == "pool":
            report = inspect_pool(pool_path(pool_scope(profile)))
            rc = 0 if report["status"] == "AVAILABLE" else 2
        elif args.command == "plan":
            report = plan(profile)
            rc = 0
        elif args.command == "check":
            require_resolved(profile)
            if not args.allow_read:
                raise ConfigError("check requires --allow-read; plan is fully offline")
            if args.observer_binding is not None or args.mcporter_binary is not None or args.allow_observer_read:
                if args.observer_binding is None or args.mcporter_binary is None:
                    raise ConfigError("reviewed check requires --observer-binding, --mcporter-binary and --allow-observer-read")
                report = check_remote(profile, receipt=args.observer_binding, executable=args.mcporter_binary,
                                      allow_http_read=args.allow_read, allow_observer_read=args.allow_observer_read)
            else:
                report = adapter_for(profile).preflight()
            # Discovery is not release qualification; never return a runnable PASS.
            rc = 0 if report["status"] == "DISCOVERY_PASSED" else 2
        elif args.command == "prepare":
            report = prepare_run(profile, args.state_dir, args.run_id, observer_binding=args.observer_binding)
            rc = 0
        elif args.command == "run":
            require_pilot_execution_binding(profile)
            require_resolved(profile)
            if not args.allow_write or args.confirm_run_id != args.run_id:
                raise ConfigError("run requires --allow-write and an exact --confirm-run-id")
            state = read_state(args.state_dir / "run.json")
            if args.mcporter_binary is not None or args.allow_observer_read or "observer_binding" in state:
                if args.mcporter_binary is None:
                    raise ConfigError("reviewed remote run requires --mcporter-binary and --allow-observer-read")
                client, binding = remote_dependencies(profile, args.state_dir, args.run_id,
                    executable=args.mcporter_binary, allow_read=args.allow_observer_read)
                report = run_case(profile, args.state_dir, args.run_id, args.case,
                                  observer_client=client, physical_binding_probe=binding)
            elif args.case == "PRE-01":
                report = run_pre01(profile, args.state_dir, args.run_id)
            elif args.case == "PRE-02":
                report = run_pre02(profile, args.state_dir, args.run_id)
            else:
                report = run_pre03(profile, args.state_dir, args.run_id)
            rc = 0 if report["status"] == "PASS" else 2
        elif args.command == "gate":
            state = read_state(args.state_dir / "run.json")
            require_profile_binding(state, profile)
            summary = build_report(profile, args.state_dir)
            report = evaluate(summary)
            rc = 0 if report["status"] == "QUALIFIED" else 2
        elif args.command == "report":
            state = read_state(args.state_dir / "run.json")
            require_profile_binding(state, profile)
            summary = build_report(profile, args.state_dir)
            formats = {args.format}
            if profile.get('schema_version') == 2:
                from .trust_reporting import render_trust_report
                report = render_trust_report(summary, args.state_dir, args.output_dir, formats)
            else:
                report = write_reports(summary, args.output_dir or args.state_dir / "reports", formats)
            rc = 0 if summary["status"] in {"PILOT_PASS", "LOCAL_SUITE_PASS", "RETAINED_CASE_PASS"} else 2
        elif args.command == "fixture":
            if args.action == "plan":
                report = build_fixture_plan(profile)
                rc = 0 if report["status"] == "READY" else 2
            else:
                if not args.allow_write or args.confirm_run_id != args.run_id:
                    raise ConfigError("fixture apply requires --allow-write and an exact --confirm-run-id")
                report = apply_fixture_plan(profile, args.state_dir, args.run_id)
                rc = 0 if report["status"] == "APPLIED" else 2
        elif args.command == "recover-local":
            report = recover_local_mock(profile, args.state_dir, allow_write=args.allow_write,
                                        confirm_run_id=args.confirm_run_id)
            rc = 0 if report["status"] == "RECOVERED" else 2
        elif args.command == "remote-reconcile":
            report = remote_reconcile(profile, state_dir=args.state_dir, run_id=args.run_id,
                                       confirm_run_id=args.confirm_run_id, allow_read=args.allow_read,
                                       allow_write=args.allow_write)
            rc = 0 if report["status"] == "RECONCILED" else 2
        elif args.command == "cleanup":
            state = read_state(args.state_dir / "run.json")
            digest = require_profile_binding(state, profile)
            if not args.dry_run:
                require_no_legacy_runtime(args.state_dir)
                require_resolved(profile)
                if not args.allow_write or args.confirm_run_id != state.get("run_id"):
                    raise ConfigError("cleanup requires --allow-write and an exact --confirm-run-id")
            run_id = state.get("run_id", "")
            ledger_path = Path(state.get("ledger_path", ""))
            lease = None
            try:
                if not args.dry_run:
                    if state.get("status") in {"RUNNING", "QUARANTINED"} or state.get("quiescence") == "UNKNOWN":
                        raise ConfigError("cleanup: in-flight activity is unverified; explicit reconciliation required")
                    path = bound_pool_path(state)
                    lease = (PoolLease.from_file(path, run_id, digest)
                             if path.exists() or path.is_symlink()
                             else PoolLease.acquire(path, run_id, digest))
                    state = read_state(args.state_dir / "run.json")
                    require_profile_binding(state, profile)
                    if state.get("status") in {"RUNNING", "QUARANTINED"} or state.get("quiescence") == "UNKNOWN":
                        raise ConfigError("cleanup: in-flight activity is unverified; explicit reconciliation required")
                if not args.dry_run and profile["entrypoint"]["kind"] != "mock":
                    if remote_effect_admission(profile)["status"] != "ADMITTED":
                        raise ConfigError("destructive remote cleanup requires an independent reviewed scope verifier")
                with Ledger(ledger_path, run_id, digest, read_only=args.dry_run) as ledger:
                    if state.get("execution_contract_required"):
                        require_execution_safe(ledger, required=True)
                    authority = None if args.dry_run else AuthorizedAdapter(
                        adapter_for(profile, state_dir=args.state_dir), lease)
                    report = execute_cleanup(ledger, authority, dry_run=args.dry_run)
                    if authority is not None and authority.invalidated:
                        report["operation_authority"] = "INVALIDATED"
                        state.update(status="QUARANTINED", quiescence="UNKNOWN")
                report["profile_digest"] = digest
                write_state(args.state_dir / ("cleanup-plan.json" if args.dry_run else "cleanup.json"), report)
                if not args.dry_run:
                    state["cleanup_status"] = report["status"]
                    write_state(args.state_dir / "run.json", state)
                    if report["status"] == "PASS":
                        try:
                            authority.require_active()
                            lease.release()
                        except (PoolLeaseError, OSError):
                            state.update(status="QUARANTINED", quiescence="UNKNOWN")
                            write_state(args.state_dir / "run.json", state)
                            raise
            finally:
                if lease is not None:
                    lease.close()
            rc = 0 if report["status"] in {"DRY_RUN", "PASS"} else 2
        elif args.command == "runtime":
            panel_asset = None
            if args.panel_asset is not None or args.panel_sha256 is not None:
                if args.action not in {'smoke', 'fixture-smoke'} or args.panel_asset is None or args.panel_sha256 is None:
                    raise ConfigError('panel artifact/path SHA256 pair is restricted to explicit owned smoke')
                from .local_panel_asset import LocalPanelAsset
                panel_asset = LocalPanelAsset(args.panel_asset, args.panel_sha256)
            if args.check_trust_baseline and (not args.verify_bot_identities or args.fixture_set != 'trusted-team'
                                              or args.action != 'fixture-smoke'):
                raise ConfigError('--check-trust-baseline requires verified local trusted-team fixture-smoke')
            if args.verify_bot_identities and (args.action != 'fixture-smoke' or args.fixture_set == 'pilot'):
                raise ConfigError('--verify-bot-identities requires expanded local fixture-smoke')
            if args.fixture_set != "pilot" and args.action != "fixture-smoke":
                raise ConfigError("--fixture-set is only available for explicit fixture-smoke")
            if profile["entrypoint"]["kind"] != "singlebox":
                raise ConfigError("runtime: profile entrypoint.kind must be singlebox")
            if args.action in {"start", "stop"}:
                require_resolved(profile)
            runtime = LocalSingleboxRuntime(profile, args.state_dir, runtime_dir=args.runtime_dir)
            if args.action in {"smoke", "fixture-smoke"}:
                if (profile["environment"]["tier"] != "local" or not args.allow_local_processes
                        or args.binary is None or not args.binary_sha256 or not args.run_id):
                    raise ConfigError("runtime smoke requires a local profile, --allow-local-processes, binary/SHA256 and run ID")
                if args.action == "fixture-smoke" and not args.allow_fixture_write:
                    raise ConfigError("runtime fixture-smoke requires --allow-fixture-write")
                require_no_legacy_runtime(args.state_dir)
                if (args.state_dir / "run.json").exists() or (args.state_dir / "run.json").is_symlink():
                    raise ConfigError("runtime smoke requires a separate state directory, not a prepared E2E Run")
                report = local_runtime_smoke(with_fixtures=args.action == "fixture-smoke", fixture_set=args.fixture_set,
                    verify_bot_identities=args.verify_bot_identities, check_trust_baseline=args.check_trust_baseline,
                    binary=args.binary, binary_sha256=args.binary_sha256,
                    root=runtime.runtime_dir, run_id=args.run_id, port=args.port, panel_asset=panel_asset,
                    startup_timeout=profile["policy"]["timeout_seconds"])
                report["profile_digest"] = fingerprint(profile)
                write_state(Path(report["runtime_dir"]) / "smoke-report.json", report)
            elif args.action == "check":
                report = runtime.check()
            elif args.action == "status":
                report = runtime.status()
            elif args.action == "start":
                report = runtime.start()
            else:
                report = runtime.stop()
            rc = 0 if report.get("status") in {"READY", "RUNNING", "STOPPED", "LOCAL_RUNTIME_SMOKE_PASS", "LOCAL_FIXTURE_SMOKE_PASS"} else 2
        else:  # release
            state = read_state(args.state_dir / "run.json")
            require_no_legacy_runtime(args.state_dir)
            profile_digest = state.get("profile_digest", "")
            run_id = state.get("run_id", "")
            lock_path = bound_pool_path(state)
            lease = PoolLease.from_file(lock_path, run_id, profile_digest)
            try:
                state = read_state(args.state_dir / "run.json")
                if state.get("status") in {"RUNNING", "QUARANTINED"} or state.get("quiescence") == "UNKNOWN":
                    raise ConfigError("release: in-flight activity requires explicit reconciliation")
                with Ledger(Path(state["ledger_path"]), run_id, profile_digest) as ledger:
                    require_execution_safe(ledger, required=state.get("execution_contract_required", False))
                    pending = ledger.cleanup_plan()
                    mock_path = args.state_dir / "local-mock.json"
                    if state.get("adapter_kind") == "mock" or mock_path.exists() or mock_path.is_symlink():
                        snapshot = validate_snapshot(read_state(mock_path), run_id, profile_digest)
                        verify_snapshot_scope(snapshot, run_id, ledger.resource_snapshot())
                if (pending["resources"] or pending["unresolved_handles"] or pending["retained_handles"]
                        or state.get("cleanup_status") == "BLOCKED"):
                    raise ConfigError("release: unresolved resources or cleanup failure quarantine this pool")
                state["status"] = "RELEASED"
                write_state(args.state_dir / "run.json", state)
                try:
                    lease.release()
                except (PoolLeaseError, OSError):
                    state.update(status="QUARANTINED", quiescence="UNKNOWN")
                    write_state(args.state_dir / "run.json", state)
                    raise
            finally:
                lease.close()
            report = {"schema_version": 1, "status": "RELEASED", "ready_to_execute": False,
                      "run_id": run_id, "state_dir": str(args.state_dir)}
            rc = 0
    except (ConfigError, AdapterCapabilityError, MockStateError, PilotError, LedgerError, PoolBusyError, PoolLeaseError, RunStateError,
            RuntimeContractError, LocalFixtureError, LocalRoleIdentityError, TrustBaselineError, LocalTrustApiError, EvidenceError, LocalCliError, AlisdWireError, ObservationError, OSError) as error:
        report = {"status": "BLOCKED", "ready_to_execute": False, "reason": str(error)}
        if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
            report["child_shutdown_status"] = "UNCONFIRMED"
        rc = 2
    except KeyboardInterrupt as error:
        report = {"status": "CANCELLED", "ready_to_execute": False}
        if getattr(error, "child_shutdown_status", None) == "UNCONFIRMED":
            report["child_shutdown_status"] = "UNCONFIRMED"
        rc = 130
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return rc
