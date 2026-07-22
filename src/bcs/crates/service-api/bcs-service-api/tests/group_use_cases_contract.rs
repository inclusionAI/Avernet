use async_trait::async_trait;
use bcs_service_api::{
    CallerContext, DefaultDelivery, DmCreateCommand, DmCreateResult, GroupAddMemberCommand, GroupAddMemberResult,
    GroupCreateCommand, GroupCreateParticipantCommand,
    GroupDeleteCommand, GroupDetailResult, GroupHistoryCommand, GroupHistoryResult,
    GroupManagementService, GroupMessageHistoryService, GroupParticipantModeCommand,
    GroupParticipantView, GroupProposalConfirmCommand, GroupProposalConfirmResult,
    GroupProposalCreateCommand, GroupProposalCreateResult, GroupProposalPreviewCommand,
    GroupProposalService, GroupRoutingPolicyCommand, GroupStatus, GroupStatusCommand,
    GroupTerminateCommand, GroupUpdateLabelCommand, GroupUpdateWorkspaceCommand, GroupUseCaseError,
    ParticipantMode, ProposalContext, RoutingMode, RoutingPolicy, ServiceError, Workspace,
};
use bcs_test_support::{
    NoopGroupManagementService, NoopGroupMessageHistoryService,
    NoopGroupProposalService,
};

#[test]
fn group_create_command_carries_caller_and_members() {
    let cmd = GroupCreateCommand {
        group_id: Some("group-explicit".to_string()),
        caller_actor_id: Some("human_123".to_string()),
        driver_bot_id: "driver".to_string(),
        originator: None,
        label: Some("Incident Room".to_string()),
        topic: Some("debug incident".to_string()),
        context: Some("prod checkout outage".to_string()),
        routing_policy: Some(RoutingPolicy {
            mode: RoutingMode::Hybrid,
            default_bot_final_delivery: DefaultDelivery::SendToDriver,
            ..Default::default()
        }),
        participants: vec![
            GroupCreateParticipantCommand {
                bot_id: "driver".to_string(),
                role: Some("driver".to_string()),
            },
            GroupCreateParticipantCommand {
                bot_id: "bot-a".to_string(),
                role: Some("consultant".to_string()),
            },
        ],
        member_bot_ids: vec!["bot-a".to_string(), "bot-b".to_string()],
        group_kind: None,
        service_spec: None,
        group_strategy: None,
        visibility: None,
    };

    assert_eq!(cmd.group_id.as_deref(), Some("group-explicit"));
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("human_123"));
    assert_eq!(cmd.driver_bot_id, "driver");
    assert_eq!(cmd.label.as_deref(), Some("Incident Room"));
    assert_eq!(cmd.topic.as_deref(), Some("debug incident"));
    assert_eq!(cmd.context.as_deref(), Some("prod checkout outage"));
    let routing_policy = cmd.routing_policy.as_ref().expect("typed routing policy");
    assert_eq!(routing_policy.mode, RoutingMode::Hybrid);
    assert_eq!(
        routing_policy.default_bot_final_delivery,
        DefaultDelivery::SendToDriver
    );
    assert_eq!(cmd.participants.len(), 2);
    assert_eq!(cmd.participants[1].bot_id, "bot-a");
    assert_eq!(cmd.participants[1].role.as_deref(), Some("consultant"));
    assert_eq!(cmd.member_bot_ids.len(), 2);
}

#[test]
fn dm_create_command_and_result_carry_create_or_reuse_semantics() {
    let cmd = DmCreateCommand {
        group_id: Some("dm-explicit".to_string()),
        caller_actor_id: Some("human_123".to_string()),
        driver_bot: Some("assistant".to_string()),
        target_actor_id: "assistant".to_string(),
        label: Some("Alice / Assistant".to_string()),
        topic: Some("debug incident".to_string()),
        context: Some("prod checkout outage".to_string()),
    };
    assert_eq!(cmd.group_id.as_deref(), Some("dm-explicit"));
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("human_123"));
    assert_eq!(cmd.driver_bot.as_deref(), Some("assistant"));
    assert_eq!(cmd.target_actor_id, "assistant");
    assert_eq!(cmd.topic.as_deref(), Some("debug incident"));

    let result = DmCreateResult {
        group: GroupDetailResult {
            group_id: "dm-explicit".to_string(),
            label: cmd.label.clone(),
            status: GroupStatus::Active,
            driver_bot_id: "assistant".to_string(),
            context: cmd.context.clone(),
            participants: Vec::new(),
            message_count: 0,
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            group_kind: bcs_service_api::GroupKind::Dm,
            dm_pair_key: Some("assistant|human_123".to_string()),
            group_strategy: Default::default(),
            created_at: 10,
            updated_at: 10,
            chat_url: None,
            context_injected: 0,
            service_spec: None,
            latest_running_session_id: None,
            originator: None,
            visibility: "private".to_string(),
        },
        created: false,
    };

    assert!(!result.created);
    assert_eq!(result.group.group_kind, bcs_service_api::GroupKind::Dm);
    assert_eq!(result.group.dm_pair_key.as_deref(), Some("assistant|human_123"));
}

#[test]
fn group_status_and_history_commands_carry_route_inputs() {
    let status = GroupStatusCommand {
        caller_actor_id: Some("driver".to_string()),
        group_id: "group-1".to_string(),
        status: "completed".to_string(),
    };
    let add_member = GroupAddMemberCommand {
        caller_actor_id: Some("driver".to_string()),
        human_actor_id: Some("human_alice".to_string()),
        group_id: "group-1".to_string(),
        bot_id: "bot-a".to_string(),
        role: Some("consultant".to_string()),
    };
    let history = GroupHistoryCommand {
        caller: CallerContext::Public,
        group_id: "group-1".to_string(),
        view_bot_id: Some("bot-a".to_string()),
        limit: 50,
        before: Some(1234),
    };

    assert_eq!(status.group_id, "group-1");
    assert_eq!(status.status, "completed");
    assert_eq!(add_member.caller_actor_id.as_deref(), Some("driver"));
    assert_eq!(add_member.group_id, "group-1");
    assert_eq!(add_member.bot_id, "bot-a");
    assert_eq!(add_member.role.as_deref(), Some("consultant"));
    assert_eq!(history.group_id, "group-1");
    assert_eq!(history.view_bot_id.as_deref(), Some("bot-a"));
    assert_eq!(history.limit, 50);
    assert_eq!(history.before, Some(1234));
}

#[test]
fn group_proposal_commands_preserve_context_and_confirmation_token() {
    let context = ProposalContext {
        user_query: Some("need database expertise".to_string()),
        detected_gap: Some("storage diagnosis".to_string()),
        relevant_history: vec!["db latency rose".to_string()],
    };
    let create = GroupProposalCreateCommand {
        caller_actor_id: Some("bot-driver".to_string()),
        driver_bot_id: "bot-driver".to_string(),
        suggested_driver_bot_id: Some("bot-suggested-driver".to_string()),
        suggested_participants: vec!["bot-a".to_string(), "bot-b".to_string()],
        topic: "debug incident".to_string(),
        context: Some(context.clone()),
    };
    let confirm = GroupProposalConfirmCommand {
        caller_actor_id: Some("human_123".to_string()),
        token: "proposal-token".to_string(),
    };

    assert_eq!(create.driver_bot_id, "bot-driver");
    assert_eq!(
        create.suggested_driver_bot_id.as_deref(),
        Some("bot-suggested-driver")
    );
    assert_eq!(create.suggested_participants, ["bot-a", "bot-b"]);
    assert_eq!(
        create
            .context
            .as_ref()
            .and_then(|ctx| ctx.user_query.as_deref()),
        Some("need database expertise")
    );
    assert_eq!(confirm.token, "proposal-token");
}

#[test]
fn group_result_dtos_are_route_friendly_views() {
    let participant = GroupParticipantView {
        bot_uuid: "driver".to_string(),
        bot_name: Some("Driver Bot".to_string()),
        kind: Some(bcs_service_api::ParticipantKind::Bot),
        role: "driver".to_string(),
        actor_kind: bcs_service_api::ActorKind::Bot,
        mode: None,
    };
    let detail = GroupDetailResult {
        group_id: "group-1".to_string(),
        label: Some("Group: debug incident".to_string()),
        status: GroupStatus::Active,
        driver_bot_id: "driver".to_string(),
        context: Some("debug incident".to_string()),
        participants: vec![participant],
        message_count: 1,
        workspace: Workspace::default(),
        service_group_uuid: Some("service-group-1".to_string()),
        service_mode: Some("master_slave".to_string()),
        group_kind: bcs_service_api::GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: Default::default(),
        created_at: 100,
        updated_at: 200,
        chat_url: Some("https://chat.example/group-1".to_string()),
        context_injected: 1,
        service_spec: None,
        latest_running_session_id: None,
        originator: None,
        visibility: "private".to_string(),
    };
    let proposal = GroupProposalCreateResult {
        proposal_created: true,
        driver_bot_id: "driver".to_string(),
        participant_bot_ids: vec!["driver".to_string(), "bot-a".to_string()],
        member_intros: "Driver Bot\nBot A".to_string(),
        confirm_url: "https://bcs.example/groups/token/confirm".to_string(),
        expires_in_seconds: 600,
        message: "confirm this group".to_string(),
    };
    let confirmed = GroupProposalConfirmResult {
        created: true,
        group_id: "group-1".to_string(),
        driver_bot_id: "driver".to_string(),
        participant_bot_ids: vec!["driver".to_string(), "bot-a".to_string()],
        chat_url: None,
        session_id: "group-1:initial".to_string(),
        context_injected: 0,
    };
    let add_member = GroupAddMemberResult {
        group_id: "group-1".to_string(),
        member: GroupParticipantView {
            bot_uuid: "bot-a".to_string(),
            bot_name: Some("Bot A".to_string()),
            kind: Some(bcs_service_api::ParticipantKind::Bot),
            role: "consultant".to_string(),
            actor_kind: bcs_service_api::ActorKind::Bot,
            mode: None,
        },
    };
    let history = GroupHistoryResult {
        group_id: "group-1".to_string(),
        messages: Vec::new(),
        limit: 50,
        before: None,
        next_before: None,
    };
    let legacy_participant = serde_json::to_value(GroupParticipantView {
        bot_uuid: "legacy-bot".to_string(),
        bot_name: None,
        kind: None,
        role: "observer".to_string(),
        actor_kind: bcs_service_api::ActorKind::Bot,
        mode: None,
    })
    .unwrap();

    assert_eq!(
        detail.participants[0].bot_name.as_deref(),
        Some("Driver Bot")
    );
    assert_eq!(detail.context_injected, 1);
    assert!(proposal.proposal_created);
    assert_eq!(proposal.expires_in_seconds, 600);
    assert!(confirmed.created);
    assert_eq!(add_member.member.role, "consultant");
    assert_eq!(history.messages.len(), 0);
    assert!(legacy_participant.get("bot_name").is_none());
    assert!(legacy_participant.get("type").is_none());
    assert!(legacy_participant.get("mode").is_none());
}

#[test]
fn group_use_case_errors_are_typed() {
    let invalid_status = GroupUseCaseError::InvalidGroupStatus("paused".to_string());
    let invalid_limit = GroupUseCaseError::InvalidHistoryLimit(0);

    assert!(matches!(
        invalid_status,
        GroupUseCaseError::InvalidGroupStatus(status) if status == "paused"
    ));
    assert!(matches!(
        invalid_limit,
        GroupUseCaseError::InvalidHistoryLimit(limit) if limit == 0
    ));
}

#[tokio::test]
async fn noop_group_management_service_fails_closed() {
    let service = NoopGroupManagementService;

    let created = service
        .create_group(GroupCreateCommand {
            group_id: None,
            caller_actor_id: None,
            driver_bot_id: "driver".to_string(),
            originator: None,
            label: None,
            topic: Some("debug incident".to_string()),
            context: None,
            routing_policy: None,
            participants: vec![GroupCreateParticipantCommand {
                bot_id: "bot-a".to_string(),
                role: Some("consultant".to_string()),
            }],
            member_bot_ids: vec!["bot-a".to_string()],
            group_kind: None,
            service_spec: None,
            group_strategy: None,
            visibility: None,
        })
        .await;
    assert_not_configured(created, "group management service is not configured");

    let dm = service
        .create_dm(DmCreateCommand {
            group_id: None,
            caller_actor_id: Some("human_123".to_string()),
            driver_bot: Some("assistant".to_string()),
            target_actor_id: "assistant".to_string(),
            label: None,
            topic: None,
            context: None,
        })
        .await;
    assert_not_configured(dm, "group management service is not configured");

    let status = service
        .update_status(GroupStatusCommand {
            caller_actor_id: None,
            group_id: "group-1".to_string(),
            status: "completed".to_string(),
        })
        .await;
    assert_not_configured(status, "group management service is not configured");

    let added = service
        .add_member(GroupAddMemberCommand {
            caller_actor_id: None,
            human_actor_id: None,
            group_id: "group-1".to_string(),
            bot_id: "bot-a".to_string(),
            role: Some("consultant".to_string()),
        })
        .await;
    assert_not_configured(added, "group management service is not configured");

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-1".to_string(),
        })
        .await;
    assert_not_configured(deleted, "group management service is not configured");

    let terminated = service
        .terminate_group(GroupTerminateCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-1".to_string(),
        })
        .await;
    assert_not_configured(terminated, "group management service is not configured");

    let label = service
        .update_label(GroupUpdateLabelCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-1".to_string(),
            label: Some("Renamed".to_string()),
        })
        .await;
    assert_not_configured(label, "group management service is not configured");

    let workspace = service
        .update_workspace(GroupUpdateWorkspaceCommand {
            caller_actor_id: None,
            group_id: "group-1".to_string(),
            workspace: Workspace::default(),
        })
        .await;
    assert_not_configured(workspace, "group management service is not configured");

    let routing = service
        .update_routing_policy(GroupRoutingPolicyCommand {
            caller_actor_id: Some("driver".to_string()),
            group_id: "group-1".to_string(),
            mode: Some(RoutingMode::Structured),
            default_bot_final_delivery: None,
            sender_routes: None,
        })
        .await;
    assert_not_configured(routing, "group management service is not configured");

    let mode = service
        .update_participant_mode(GroupParticipantModeCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-1".to_string(),
            actor_id: "driver".to_string(),
            mode: ParticipantMode::Muted,
        })
        .await;
    assert_not_configured(mode, "group management service is not configured");
}

#[tokio::test]
async fn noop_group_proposal_service_fails_closed() {
    let service = NoopGroupProposalService;

    let created = service
        .create_proposal(GroupProposalCreateCommand {
            caller_actor_id: None,
            driver_bot_id: "driver".to_string(),
            suggested_driver_bot_id: None,
            suggested_participants: Vec::new(),
            topic: "debug incident".to_string(),
            context: None,
        })
        .await;
    assert_not_configured(created, "group proposal service is not configured");

    let confirmed = service
        .confirm_proposal(GroupProposalConfirmCommand {
            caller_actor_id: None,
            token: "proposal-token".to_string(),
        })
        .await;
    assert_not_configured(confirmed, "group proposal service is not configured");

    let preview = service
        .preview_proposal(GroupProposalPreviewCommand {
            token: "proposal-token".to_string(),
        })
        .await;
    assert_not_configured(preview, "group proposal service is not configured");
}

#[tokio::test]
async fn added_group_proposal_methods_fail_closed_for_legacy_implementations() {
    let service = LegacyGroupProposalService;

    let preview = service
        .preview_proposal(GroupProposalPreviewCommand {
            token: "proposal-token".to_string(),
        })
        .await;
    assert_not_configured(preview, "group proposal service is not configured");
}

#[tokio::test]
async fn noop_group_message_history_service_fails_closed() {
    let service = NoopGroupMessageHistoryService;

    let result = service
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Public,
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: 10,
            before: None,
        })
        .await;
    assert_not_configured(result, "group message history service is not configured");
}

fn assert_not_configured<T>(result: Result<T, GroupUseCaseError>, expected_message: &str) {
    assert!(matches!(
        result,
        Err(GroupUseCaseError::Service(ServiceError::InvalidOperation {
            message,
            request_id: None,
        })) if message == expected_message
    ));
}

struct LegacyGroupProposalService;

#[async_trait]
impl GroupProposalService for LegacyGroupProposalService {
    async fn create_proposal(
        &self,
        _cmd: GroupProposalCreateCommand,
    ) -> Result<GroupProposalCreateResult, GroupUseCaseError> {
        Err(ServiceError::InternalError("not used".to_string()).into())
    }

    async fn confirm_proposal(
        &self,
        _cmd: GroupProposalConfirmCommand,
    ) -> Result<GroupProposalConfirmResult, GroupUseCaseError> {
        Err(ServiceError::InternalError("not used".to_string()).into())
    }
}
