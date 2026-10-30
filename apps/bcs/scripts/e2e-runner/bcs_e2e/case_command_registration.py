"""Command registration helpers for the cross-environment E2E runner."""

from __future__ import annotations

from pathlib import Path

from .edge_read_case_run import CASE_KEYS as EDGE_CASE_KEYS
from .network_read_case_run import CASE_KEYS as NETWORK_READ_CASE_KEYS
from .edge_friend_mutation_case_run import CASE_KEYS as EDGE_FRIEND_MUTATION_CASE_KEYS
from .edge_permission_backlog.case_run import CASE_KEYS as EDGE_PERMISSION_BACKLOG_CASE_KEYS
from .session_service_cli_case_run import CASE_KEYS as SESSION_SERVICE_CASE_KEYS
from .direct_chat_cli_case_run import CASE_KEYS as DIRECT_CHAT_CASE_KEYS
from .delivery_policy_cli_case_run import CASE_KEYS as DELIVERY_POLICY_CASE_KEYS
from .draft_delivery.dual_case_run import CASE_KEYS as DRAFT_DELIVERY_CASE_KEYS
from .service_policy.dual_case_run import CASE_KEYS as SERVICE_POLICY_CASE_KEYS
from .provider_subbots_case_run import CASE_KEYS as PROVIDER_SUBBOTS_CASE_KEYS
from .provider_owner_dual_case_run import (COMMAND as PROVIDER_OWNER_COMMAND,
                                            register_provider_owner_dual_case_commands)
from .session_owner.dual_case_run import (COMMAND as SESSION_OWNER_COMMAND,
                                          register_session_owner_dual_case_commands)
from .authority.dual_case_run import (COMMAND as AUTHORITY_COMMAND,
                                      register_authority_dual_case_commands)
from .provider_publishes.dual_case_run import (COMMAND as PROVIDER_PUBLISHES_COMMAND,
                                               register_provider_publishes_dual_case_commands)
from .slow_judge.dual_case_run import (COMMAND as SLOW_JUDGE_COMMAND,
                                       register_slow_judge_dual_case_commands)
from .dynamic_family.case_run import (DUAL_DRAFT_GROUP_COMMAND,
                                       DUAL_FIXED_LOOP_COMMAND,
                                       DUAL_NESTED_FIXED_LOOP_COMMAND,
                                       DUAL_RESULT_QUEUE_COMMAND)
from .workspace_field.dual_case_run import DUAL_WORKSPACE_FIELD_COMMAND
from .delivery_density.contract import DUAL_DELIVERY_POLICY_DENSITY_COMMAND
from .service_policy_density.contract import DUAL_SERVICE_POLICY_DENSITY_COMMAND


CASE_COMMANDS = frozenset({
    "dual-workspace-cli-status-run",
    "dual-workspace-run",
    "dual-workspace-group-run",
    "dual-workspace-story-run",
    "dual-session-run",
    "dual-session-public-run",
    "dual-channel-bindings-run",
    "dual-channel-run",
    "dual-structured-core-run",
    "dual-structured-templates-run",
    "dual-structured-density-run",
    DUAL_FIXED_LOOP_COMMAND,
    DUAL_NESTED_FIXED_LOOP_COMMAND,
    DUAL_RESULT_QUEUE_COMMAND,
    DUAL_DRAFT_GROUP_COMMAND,
    DUAL_WORKSPACE_FIELD_COMMAND,
    DUAL_DELIVERY_POLICY_DENSITY_COMMAND,
    DUAL_SERVICE_POLICY_DENSITY_COMMAND,
    "dual-webhook-subscription-run",
    "dual-friend-connection-decisions-run",
    "dual-friend-public-accept-run",
    "dual-session-file-run",
    "dual-collaboration-team-run",
    "dual-group-context-run",
    "dual-edge-read-run",
    "dual-network-read-run",
    "dual-network-register-run",
    "dual-network-status-run",
    "dual-friend-run",
    "dual-edge-friend-mutation-run",
    "dual-edge-permission-backlog-run",
    "dual-session-service-run",
    "dual-operator-cli-run",
    "dual-direct-chat-run",
    "dual-delivery-policy-run",
    "dual-provider-subbots-run",
    PROVIDER_PUBLISHES_COMMAND,
    SLOW_JUDGE_COMMAND,
    "dual-draft-delivery-run",
    "dual-service-policy-run",
    "dual-custom-collaboration-run",
    PROVIDER_OWNER_COMMAND,
    SESSION_OWNER_COMMAND,
    AUTHORITY_COMMAND,
})


def register_case_commands(commands) -> None:
    dual = commands.add_parser("dual-workspace-cli-status-run", help="Run the reviewed Workspace status/termination story through Singlebox or preprod product CLI")
    dual.add_argument("--profile", type=Path, required=True)
    dual.add_argument("--cli-binary", type=Path, required=True)
    dual.add_argument("--cli-binary-sha256", required=True)
    dual.add_argument("--state-dir", type=Path, required=True)
    dual.add_argument("--run-id", required=True)
    dual.add_argument("--confirm-run-id", required=True)
    dual.add_argument("--allow-write", action="store_true")
    cli_case = commands.add_parser("dual-workspace-run", help="Run the reviewed dual Workspace create/detail/list/fuse/member stories through Singlebox or preprod product CLI")
    cli_case.add_argument("--profile", type=Path, required=True)
    cli_case.add_argument("--cli-binary", type=Path, required=True)
    cli_case.add_argument("--cli-binary-sha256", required=True)
    cli_case.add_argument("--case", choices=("create-get", "detail", "list", "fuse", "member"), required=True)
    cli_case.add_argument("--state-dir", type=Path, required=True)
    cli_case.add_argument("--run-id", required=True)
    cli_case.add_argument("--confirm-run-id", required=True)
    cli_case.add_argument("--allow-write", action="store_true")
    group_case = commands.add_parser("dual-workspace-group-run", help="Run the reviewed Workspace Group lifecycle/governance story through Singlebox or preprod HTTP")
    group_case.add_argument("--profile", type=Path, required=True)
    group_case.add_argument("--case", choices=("lifecycle", "governance"), required=True)
    group_case.add_argument("--state-dir", type=Path, required=True)
    group_case.add_argument("--run-id", required=True)
    group_case.add_argument("--confirm-run-id", required=True)
    group_case.add_argument("--allow-write", action="store_true")
    session_case = commands.add_parser(
        "dual-session-run",
        help="Run the reviewed dual Session product-CLI stories through Singlebox or preprod")
    session_case.add_argument("--profile", type=Path, required=True)
    session_case.add_argument("--cli-binary", type=Path, required=True)
    session_case.add_argument("--cli-binary-sha256", required=True)
    session_case.add_argument("--case", choices=("create-get", "bot-groups", "group-invite",
                                                "session-invite", "invite-join", "lifecycle",
                                                "complete-invoke", "collection"), required=True)
    session_case.add_argument("--state-dir", type=Path, required=True)
    session_case.add_argument("--run-id", required=True)
    session_case.add_argument("--confirm-run-id", required=True)
    session_case.add_argument("--allow-write", action="store_true")
    session_public_case = commands.add_parser(
        "dual-session-public-run",
        help="Run the reviewed public-v1 Session JWT/WS slice through owned-local Singlebox; Gateway remains admission-blocked")
    session_public_case.add_argument("--profile", type=Path, required=True)
    session_public_case.add_argument("--binary", type=Path, required=True)
    session_public_case.add_argument("--binary-sha256", required=True)
    session_public_case.add_argument("--state-dir", type=Path, required=True)
    session_public_case.add_argument("--run-id", required=True)
    session_public_case.add_argument("--confirm-run-id", required=True)
    session_public_case.add_argument("--allow-write", action="store_true")
    story_case = commands.add_parser(
        "dual-workspace-story-run",
        help="Run the reviewed dual Workspace incident/roundtrip stories through Singlebox or preprod HTTP")
    story_case.add_argument("--profile", type=Path, required=True)
    story_case.add_argument("--case", choices=("incident-core", "roundtrip"), required=True)
    story_case.add_argument("--state-dir", type=Path, required=True)
    story_case.add_argument("--run-id", required=True)
    story_case.add_argument("--confirm-run-id", required=True)
    story_case.add_argument("--allow-write", action="store_true")
    channel_bindings_case = commands.add_parser(
        "dual-channel-bindings-run",
        help="Run the reviewed Channel-binding quiet-state slice through Singlebox or preprod")
    channel_bindings_case.add_argument("--profile", type=Path, required=True)
    channel_bindings_case.add_argument("--state-dir", type=Path, required=True)
    channel_bindings_case.add_argument("--run-id", required=True)
    channel_bindings_case.add_argument("--confirm-run-id", required=True)
    channel_bindings_case.add_argument("--allow-write", action="store_true")
    channel_case = commands.add_parser(
        "dual-channel-run",
        help="Run the reviewed dual Channel product-CLI story through Singlebox or preprod")
    channel_case.add_argument("--profile", type=Path, required=True)
    channel_case.add_argument("--cli-binary", type=Path, required=True)
    channel_case.add_argument("--cli-binary-sha256", required=True)
    channel_case.add_argument("--state-dir", type=Path, required=True)
    channel_case.add_argument("--run-id", required=True)
    channel_case.add_argument("--confirm-run-id", required=True)
    channel_case.add_argument("--allow-write", action="store_true")
    structured_core_case = commands.add_parser(
        "dual-structured-core-run",
        help="Run the reviewed dual structured-collaboration core through Singlebox or preprod")
    structured_core_case.add_argument("--profile", type=Path, required=True)
    structured_core_case.add_argument("--state-dir", type=Path, required=True)
    structured_core_case.add_argument("--run-id", required=True)
    structured_core_case.add_argument("--confirm-run-id", required=True)
    structured_core_case.add_argument("--allow-write", action="store_true")
    structured_templates_case = commands.add_parser(
        "dual-structured-templates-run",
        help="Run the reviewed dual structured-template catalog slice through Singlebox or preprod")
    structured_templates_case.add_argument("--profile", type=Path, required=True)
    structured_templates_case.add_argument("--state-dir", type=Path, required=True)
    structured_templates_case.add_argument("--run-id", required=True)
    structured_templates_case.add_argument("--confirm-run-id", required=True)
    structured_templates_case.add_argument("--allow-write", action="store_true")
    structured_density_case = commands.add_parser(
        "dual-structured-density-run",
        help="Run the reviewed dual structured-collaboration density journey through Singlebox or preprod")
    structured_density_case.add_argument("--profile", type=Path, required=True)
    structured_density_case.add_argument("--templates-profile", type=Path, required=False)
    structured_density_case.add_argument("--state-dir", type=Path, required=True)
    structured_density_case.add_argument("--run-id", required=True)
    structured_density_case.add_argument("--confirm-run-id", required=True)
    structured_density_case.add_argument("--allow-write", action="store_true")
    fixed_loop_case = commands.add_parser(
        "dual-fixed-loop-run",
        help="Run the reviewed dual fixed-Loop human-editor workflow through owned-local replay or preprod gap receipt")
    fixed_loop_case.add_argument("--profile", type=Path, required=True)
    fixed_loop_case.add_argument("--state-dir", type=Path, required=True)
    fixed_loop_case.add_argument("--run-id", required=True)
    fixed_loop_case.add_argument("--confirm-run-id", required=True)
    fixed_loop_case.add_argument("--allow-write", action="store_true")
    fixed_loop_case.add_argument("--binary", type=Path, required=True)
    fixed_loop_case.add_argument("--binary-sha256", required=True)
    nested_fixed_loop_case = commands.add_parser(
        "dual-nested-fixed-loop-run",
        help="Run the reviewed dual nested fixed-Loop workflows through owned-local replay or preprod gap receipt")
    nested_fixed_loop_case.add_argument("--profile", type=Path, required=True)
    nested_fixed_loop_case.add_argument("--state-dir", type=Path, required=True)
    nested_fixed_loop_case.add_argument("--run-id", required=True)
    nested_fixed_loop_case.add_argument("--confirm-run-id", required=True)
    nested_fixed_loop_case.add_argument("--allow-write", action="store_true")
    nested_fixed_loop_case.add_argument("--binary", type=Path, required=True)
    nested_fixed_loop_case.add_argument("--binary-sha256", required=True)
    nested_fixed_loop_case.add_argument("--cli-binary", type=Path, required=False)
    nested_fixed_loop_case.add_argument("--driver", required=False)
    result_queue_case = commands.add_parser(
        "dual-result-queue-run",
        help="Run the reviewed dual state-machine result-queue story through owned-local replay or preprod gap receipt")
    result_queue_case.add_argument("--profile", type=Path, required=True)
    result_queue_case.add_argument("--state-dir", type=Path, required=True)
    result_queue_case.add_argument("--run-id", required=True)
    result_queue_case.add_argument("--confirm-run-id", required=True)
    result_queue_case.add_argument("--allow-write", action="store_true")
    result_queue_case.add_argument("--binary", type=Path, required=True)
    result_queue_case.add_argument("--binary-sha256", required=True)
    draft_group_case = commands.add_parser(
        "dual-draft-group-run",
        help="Run the reviewed dual collaboration draft-group launch story through owned-local replay or preprod gap receipt")
    draft_group_case.add_argument("--profile", type=Path, required=True)
    draft_group_case.add_argument("--state-dir", type=Path, required=True)
    draft_group_case.add_argument("--run-id", required=True)
    draft_group_case.add_argument("--confirm-run-id", required=True)
    draft_group_case.add_argument("--allow-write", action="store_true")
    draft_group_case.add_argument("--binary", type=Path, required=True)
    draft_group_case.add_argument("--binary-sha256", required=True)
    workspace_field_case = commands.add_parser(
        "dual-workspace-field-run",
        help="Run the reviewed dual workspace-field density journey through Singlebox or preprod")
    workspace_field_case.add_argument("--profile", type=Path, required=True)
    workspace_field_case.add_argument("--state-dir", type=Path, required=True)
    workspace_field_case.add_argument("--run-id", required=True)
    workspace_field_case.add_argument("--confirm-run-id", required=True)
    workspace_field_case.add_argument("--allow-write", action="store_true")
    delivery_policy_density_case = commands.add_parser(
        "dual-delivery-policy-density-run",
        help="Run the reviewed dual delivery-policy density projection through owned-local replay or preprod gap receipt")
    delivery_policy_density_case.add_argument("--profile", type=Path, required=True)
    delivery_policy_density_case.add_argument("--state-dir", type=Path, required=True)
    delivery_policy_density_case.add_argument("--run-id", required=True)
    delivery_policy_density_case.add_argument("--confirm-run-id", required=True)
    delivery_policy_density_case.add_argument("--allow-write", action="store_true")
    service_policy_density_case = commands.add_parser(
        "dual-service-policy-density-run",
        help="Run the reviewed dual service policy density projection through owned-local replay or preprod gap receipt")
    service_policy_density_case.add_argument("--profile", type=Path, required=True)
    service_policy_density_case.add_argument("--state-dir", type=Path, required=True)
    service_policy_density_case.add_argument("--run-id", required=True)
    service_policy_density_case.add_argument("--confirm-run-id", required=True)
    service_policy_density_case.add_argument("--allow-write", action="store_true")
    webhook_subscription_case = commands.add_parser(
        "dual-webhook-subscription-run",
        help="Run the reviewed dual Event Webhook subscription lifecycle through Singlebox or preprod")
    webhook_subscription_case.add_argument("--profile", type=Path, required=True)
    webhook_subscription_case.add_argument("--receiver-origin", required=True)
    webhook_subscription_case.add_argument("--state-dir", type=Path, required=True)
    webhook_subscription_case.add_argument("--run-id", required=True)
    webhook_subscription_case.add_argument("--confirm-run-id", required=True)
    webhook_subscription_case.add_argument("--allow-write", action="store_true")
    friend_decision_case = commands.add_parser(
        "dual-friend-connection-decisions-run",
        help="Run the reviewed dual friend-connection decisions through Singlebox or preprod")
    friend_decision_case.add_argument("--profile", type=Path, required=True)
    friend_decision_case.add_argument("--state-dir", type=Path, required=True)
    friend_decision_case.add_argument("--run-id", required=True)
    friend_decision_case.add_argument("--confirm-run-id", required=True)
    friend_decision_case.add_argument("--allow-write", action="store_true")
    friend_public_accept_case = commands.add_parser(
        "dual-friend-public-accept-run",
        help="Run the reviewed dual public friendship auto-acceptance through Singlebox or preprod")
    friend_public_accept_case.add_argument("--profile", type=Path, required=True)
    friend_public_accept_case.add_argument("--state-dir", type=Path, required=True)
    friend_public_accept_case.add_argument("--run-id", required=True)
    friend_public_accept_case.add_argument("--confirm-run-id", required=True)
    friend_public_accept_case.add_argument("--allow-write", action="store_true")
    session_file_case = commands.add_parser(
        "dual-session-file-run",
        help="Run the reviewed dual Session-file core story through Singlebox or preprod product CLI")
    session_file_case.add_argument("--profile", type=Path, required=True)
    session_file_case.add_argument("--cli-binary", type=Path, required=True)
    session_file_case.add_argument("--cli-binary-sha256", required=True)
    session_file_case.add_argument("--state-dir", type=Path, required=True)
    session_file_case.add_argument("--run-id", required=True)
    session_file_case.add_argument("--confirm-run-id", required=True)
    session_file_case.add_argument("--allow-write", action="store_true")
    collaboration_team_case = commands.add_parser(
        "dual-collaboration-team-run",
        help="Run the reviewed dual collaboration-team product-CLI story through Singlebox or preprod")
    collaboration_team_case.add_argument("--profile", type=Path, required=True)
    collaboration_team_case.add_argument("--cli-binary", type=Path, required=True)
    collaboration_team_case.add_argument("--cli-binary-sha256", required=True)
    collaboration_team_case.add_argument("--state-dir", type=Path, required=True)
    collaboration_team_case.add_argument("--run-id", required=True)
    collaboration_team_case.add_argument("--confirm-run-id", required=True)
    collaboration_team_case.add_argument("--allow-write", action="store_true")
    group_context_case = commands.add_parser(
        "dual-group-context-run",
        help="Run the reviewed dual Group Context product-CLI story through Singlebox or preprod")
    group_context_case.add_argument("--profile", type=Path, required=True)
    group_context_case.add_argument("--cli-binary", type=Path, required=True)
    group_context_case.add_argument("--cli-binary-sha256", required=True)
    group_context_case.add_argument("--state-dir", type=Path, required=True)
    group_context_case.add_argument("--run-id", required=True)
    group_context_case.add_argument("--confirm-run-id", required=True)
    group_context_case.add_argument("--allow-write", action="store_true")
    edge_case = commands.add_parser(
        "dual-edge-read-run",
        help="Run the reviewed dual Edge read slices through Singlebox or preprod")
    edge_case.add_argument("--profile", type=Path, required=True)
    edge_case.add_argument("--case", choices=EDGE_CASE_KEYS, required=True)
    edge_case.add_argument("--state-dir", type=Path, required=True)
    edge_case.add_argument("--run-id", required=True)
    edge_case.add_argument("--confirm-run-id", required=True)
    edge_case.add_argument("--allow-write", action="store_true")
    network_read_case = commands.add_parser(
        "dual-network-read-run",
        help="Run the reviewed dual network read slices through Singlebox or preprod")
    network_read_case.add_argument("--profile", type=Path, required=True)
    network_read_case.add_argument("--case", choices=NETWORK_READ_CASE_KEYS, required=True)
    network_read_case.add_argument("--state-dir", type=Path, required=True)
    network_read_case.add_argument("--run-id", required=True)
    network_read_case.add_argument("--confirm-run-id", required=True)
    network_read_case.add_argument("--allow-write", action="store_true")
    network_register_case = commands.add_parser(
        "dual-network-register-run",
        help="Run the reviewed public-v1 register-onboard core through Singlebox or preprod")
    network_register_case.add_argument("--profile", type=Path, required=True)
    network_register_case.add_argument("--state-dir", type=Path, required=True)
    network_register_case.add_argument("--run-id", required=True)
    network_register_case.add_argument("--confirm-run-id", required=True)
    network_register_case.add_argument("--allow-write", action="store_true")
    network_case = commands.add_parser(
        "dual-network-status-run",
        help="Run the reviewed public-v1 actor-status story through Singlebox or preprod")
    network_case.add_argument("--profile", type=Path, required=True)
    network_case.add_argument("--state-dir", type=Path, required=True)
    network_case.add_argument("--run-id", required=True)
    network_case.add_argument("--confirm-run-id", required=True)
    network_case.add_argument("--allow-write", action="store_true")
    friend_case = commands.add_parser(
        "dual-friend-run",
        help="Run the reviewed dual friendship product-CLI story through Singlebox or preprod")
    friend_case.add_argument("--profile", type=Path, required=True)
    friend_case.add_argument("--cli-binary", type=Path, required=True)
    friend_case.add_argument("--cli-binary-sha256", required=True)
    friend_case.add_argument("--state-dir", type=Path, required=True)
    friend_case.add_argument("--run-id", required=True)
    friend_case.add_argument("--confirm-run-id", required=True)
    friend_case.add_argument("--allow-write", action="store_true")
    edge_friend_mutation_case = commands.add_parser(
        "dual-edge-friend-mutation-run",
        help="Run the reviewed dual Edge friend-mutation case family through preprod Gateway and station bcs-direct")
    edge_friend_mutation_case.add_argument("--profile", type=Path, required=True)
    edge_friend_mutation_case.add_argument("--station-profile", type=Path, required=True)
    edge_friend_mutation_case.add_argument("--case", choices=EDGE_FRIEND_MUTATION_CASE_KEYS, required=True)
    edge_friend_mutation_case.add_argument("--state-dir", type=Path, required=True)
    edge_friend_mutation_case.add_argument("--run-id", required=True)
    edge_friend_mutation_case.add_argument("--confirm-run-id", required=True)
    edge_friend_mutation_case.add_argument("--allow-write", action="store_true")
    edge_permission_backlog_case = commands.add_parser(
        "dual-edge-permission-backlog-run",
        help="Run the two backlog Edge-permission cases as offline dual receipts (local model + preprod placeholder)")
    edge_permission_backlog_case.add_argument("--profile", type=Path, required=True)
    edge_permission_backlog_case.add_argument("--case", choices=EDGE_PERMISSION_BACKLOG_CASE_KEYS, required=True)
    edge_permission_backlog_case.add_argument("--state-dir", type=Path, required=True)
    edge_permission_backlog_case.add_argument("--run-id", required=True)
    edge_permission_backlog_case.add_argument("--confirm-run-id", required=True)
    edge_permission_backlog_case.add_argument("--allow-write", action="store_true")
    session_service_case = commands.add_parser(
        "dual-session-service-run",
        help="Run the reviewed dual Session Service product-CLI stories through Singlebox or preprod")
    session_service_case.add_argument("--profile", type=Path, required=True)
    session_service_case.add_argument("--cli-binary", type=Path, required=True)
    session_service_case.add_argument("--cli-binary-sha256", required=True)
    session_service_case.add_argument("--case", choices=tuple(SESSION_SERVICE_CASE_KEYS), required=True)
    session_service_case.add_argument("--state-dir", type=Path, required=True)
    session_service_case.add_argument("--run-id", required=True)
    session_service_case.add_argument("--confirm-run-id", required=True)
    session_service_case.add_argument("--allow-write", action="store_true")
    operator_case = commands.add_parser(
        "dual-operator-cli-run",
        help="Run the reviewed dual Operator product-CLI story through Singlebox or preprod")
    operator_case.add_argument("--profile", type=Path, required=True)
    operator_case.add_argument("--cli-binary", type=Path, required=True)
    operator_case.add_argument("--cli-binary-sha256", required=True)
    operator_case.add_argument("--state-dir", type=Path, required=True)
    operator_case.add_argument("--run-id", required=True)
    operator_case.add_argument("--confirm-run-id", required=True)
    operator_case.add_argument("--allow-write", action="store_true")
    direct_chat_case = commands.add_parser(
        "dual-direct-chat-run",
        help="Run the reviewed dual Direct-chat product-CLI story through Singlebox or preprod")
    direct_chat_case.add_argument("--profile", type=Path, required=True)
    direct_chat_case.add_argument("--cli-binary", type=Path, required=True)
    direct_chat_case.add_argument("--cli-binary-sha256", required=True)
    direct_chat_case.add_argument("--case", choices=tuple(DIRECT_CHAT_CASE_KEYS), required=True)
    direct_chat_case.add_argument("--state-dir", type=Path, required=True)
    direct_chat_case.add_argument("--run-id", required=True)
    direct_chat_case.add_argument("--confirm-run-id", required=True)
    direct_chat_case.add_argument("--allow-write", action="store_true")
    delivery_policy_case = commands.add_parser(
        "dual-delivery-policy-run",
        help="Run the reviewed dual Delivery Policy product-HTTP story through Singlebox or preprod")
    delivery_policy_case.add_argument("--profile", type=Path, required=True)
    delivery_policy_case.add_argument("--cli-binary", type=Path, required=True)
    delivery_policy_case.add_argument("--cli-binary-sha256", required=True)
    delivery_policy_case.add_argument("--case", choices=tuple(DELIVERY_POLICY_CASE_KEYS), required=True)
    delivery_policy_case.add_argument("--state-dir", type=Path, required=True)
    delivery_policy_case.add_argument("--run-id", required=True)
    delivery_policy_case.add_argument("--confirm-run-id", required=True)
    delivery_policy_case.add_argument("--allow-write", action="store_true")
    provider_subbots_case = commands.add_parser(
        "dual-provider-subbots-run",
        help="Run the reviewed Provider subbots story through owned-local or the bounded preprod route probe")
    provider_subbots_case.add_argument("--profile", type=Path, required=True)
    provider_subbots_case.add_argument(
        "--case", choices=tuple(PROVIDER_SUBBOTS_CASE_KEYS), required=True)
    provider_subbots_case.add_argument("--state-dir", type=Path, required=True)
    provider_subbots_case.add_argument("--run-id", required=True)
    provider_subbots_case.add_argument("--confirm-run-id", required=True)
    provider_subbots_case.add_argument("--allow-write", action="store_true")
    provider_subbots_case.add_argument("--binary", type=Path)
    provider_subbots_case.add_argument("--binary-sha256")
    draft_delivery_case = commands.add_parser(
        "dual-draft-delivery-run",
        help="Run the frozen 32-obligation Draft delivery story through owned-local or the preprod bcs-direct legacy face")
    draft_delivery_case.add_argument("--profile", type=Path, required=True)
    draft_delivery_case.add_argument("--state-dir", type=Path, required=True)
    draft_delivery_case.add_argument("--run-id", required=True)
    draft_delivery_case.add_argument("--confirm-run-id", required=True)
    draft_delivery_case.add_argument("--allow-write", action="store_true")
    draft_delivery_case.add_argument("--binary", type=Path, required=True)
    draft_delivery_case.add_argument("--binary-sha256", required=True)
    draft_delivery_case.add_argument("--cli-binary", type=Path, required=True)
    draft_delivery_case.add_argument("--cli-binary-sha256", required=True)
    service_policy_case = commands.add_parser(
        "dual-service-policy-run",
        help="Run the retired service policy story through owned-local or the preprod bcs-direct bearer face")
    service_policy_case.add_argument("--profile", type=Path, required=True)
    service_policy_case.add_argument("--state-dir", type=Path, required=True)
    service_policy_case.add_argument("--run-id", required=True)
    service_policy_case.add_argument("--confirm-run-id", required=True)
    service_policy_case.add_argument("--allow-write", action="store_true")
    service_policy_case.add_argument("--binary", type=Path, required=True)
    service_policy_case.add_argument("--binary-sha256", required=True)
    register_provider_publishes_dual_case_commands(commands)
    register_slow_judge_dual_case_commands(commands)
    register_provider_owner_dual_case_commands(commands)
    register_session_owner_dual_case_commands(commands)
    register_authority_dual_case_commands(commands)
    custom_collaboration_case = commands.add_parser(
        "dual-custom-collaboration-run",
        help="Run the reviewed dual Custom-collaboration product-CLI story through Singlebox or preprod")
    custom_collaboration_case.add_argument("--profile", type=Path, required=True)
    custom_collaboration_case.add_argument("--cli-binary", type=Path, required=True)
    custom_collaboration_case.add_argument("--cli-binary-sha256", required=True)
    custom_collaboration_case.add_argument("--state-dir", type=Path, required=True)
    custom_collaboration_case.add_argument("--run-id", required=True)
    custom_collaboration_case.add_argument("--confirm-run-id", required=True)
    custom_collaboration_case.add_argument("--allow-write", action="store_true")
