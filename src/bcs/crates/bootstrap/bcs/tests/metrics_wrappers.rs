mod helpers;

use std::sync::Arc;

use async_trait::async_trait;
use bcs::metrics::{
    InstrumentedA2aChatRunService, InstrumentedMessageFlowService, MetricsBotDeliveryPort,
    MetricsDeliveryPolicyBlockHook, MetricsFrontendDeliveryPort, MetricsGroupManagementService,
    MetricsRuntime,
};
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::*;
use serial_test::serial;

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
#[serial]
async fn metrics_wrappers_record_expected_labels_and_preserve_results() {
    let runtime = install_runtime();
    let env: Arc<str> = Arc::from("dev");

    let group = Arc::new(GroupServiceFake);
    let group = MetricsGroupManagementService::new(group, env.clone());
    let created = group.create_group(group_create_cmd()).await.expect("create group");
    assert_eq!(created.group_id, "group-wrapper");
    let _ = group
        .update_status(group_status_cmd("completed"))
        .await
        .expect("complete group");
    let _ = group
        .update_status(group_status_cmd("closed"))
        .await
        .expect("close group");
    let _ = group
        .update_status(group_status_cmd("active"))
        .await
        .expect("status update group");
    let _ = group.add_member(group_add_member_cmd()).await.expect("add member");
    let deleted = group.delete_group(group_delete_cmd()).await.expect("delete group");
    assert!(deleted.deleted);

    let flow = Arc::new(MessageFlowFake);
    let flow = InstrumentedMessageFlowService::new(flow, env.clone());
    assert_eq!(flow.handle_web_send(web_send_cmd()).await.unwrap().status, "ok");
    assert_eq!(
        flow.handle_group_chat(group_chat_cmd()).await.unwrap().group_id,
        "group-wrapper"
    );
    let _ = flow
        .handle_persistent_group_send(persistent_group_send_cmd())
        .await
        .unwrap();
    let _ = flow.handle_bot_event(bot_event_cmd()).await.unwrap();
    let _ = flow
        .handle_group_callback(group_callback_cmd())
        .await
        .unwrap();
    let _ = flow.handle_chat_abort(chat_abort_cmd()).await.unwrap();
    let _ = flow
        .handle_task_dispatch(task_dispatch_cmd())
        .await
        .unwrap();
    let _ = flow.handle_task_complete(task_complete_cmd()).await.unwrap();

    let direct = Arc::new(A2aRunFake);
    let direct = InstrumentedA2aChatRunService::new(direct, env.clone());
    assert!(
        direct
            .run_blocking_chat(blocking_chat_cmd())
            .await
            .unwrap()
            .delivered
    );

    let bot_delivery = MetricsBotDeliveryPort::new(Arc::new(BotDeliveryFake {
        delivered: true,
    }), env.clone());
    assert!(
        bot_delivery
            .deliver(bot_delivery_cmd(BotDeliveryKind::Send))
            .await
            .unwrap()
            .delivered
    );
    let bot_delivery = MetricsBotDeliveryPort::new(Arc::new(BotDeliveryFake {
        delivered: false,
    }), env.clone());
    assert!(
        !bot_delivery
            .deliver(bot_delivery_cmd(BotDeliveryKind::TaskDispatch))
            .await
            .unwrap()
            .delivered
    );

    let frontend = MetricsFrontendDeliveryPort::new(Arc::new(FrontendDeliveryFake {
        delivered: 0,
    }), env.clone());
    assert_eq!(
        frontend
            .publish(frontend_delivery_cmd())
            .await
            .unwrap()
            .delivered,
        0
    );

    MetricsDeliveryPolicyBlockHook::new(env)
        .blocked(DeliveryBlockContext {
            target: DeliveryMetricTarget::Bot,
            delivery_kind: DeliveryMetricKind::Send,
            surface: DeliveryBlockSurface::GroupMessage,
            reason: DeliveryBlockReason::PolicyBlocked,
        })
        .await;

    let body = runtime.render();
    for expected in [
        "bcs_group_session_events_total{env=\"dev\",event=\"created\",kind=\"normal\",result=\"success\"}",
        "event=\"completed\",kind=\"normal\",result=\"success\"",
        "event=\"closed\",kind=\"normal\",result=\"success\"",
        "event=\"status_updated\",kind=\"normal\",result=\"success\"",
        "event=\"member_added\",kind=\"unknown\",result=\"success\"",
        "event=\"deleted\",kind=\"unknown\",result=\"success\"",
        "source=\"web_ws\",operation=\"web_send\",result=\"success\"",
        "source=\"http\",operation=\"group_chat\",result=\"success\"",
        "source=\"http\",operation=\"persistent_group_send\",result=\"success\"",
        "source=\"bot_ws\",operation=\"bot_event\",result=\"success\"",
        "source=\"http\",operation=\"group_callback\",result=\"success\"",
        "source=\"web_ws\",operation=\"chat_abort\",result=\"success\"",
        "source=\"bot_ws\",operation=\"task_dispatch\",result=\"success\"",
        "source=\"bot_ws\",operation=\"task_complete\",result=\"success\"",
        "source=\"http\",operation=\"direct_chat\",result=\"success\"",
        "target=\"bot\",delivery_kind=\"send\",result=\"delivered\",error_code=\"none\"",
        "target=\"bot\",delivery_kind=\"task_dispatch\",result=\"failed\",error_code=\"not_connected\"",
        "target=\"frontend\",delivery_kind=\"workbench_event\",result=\"no_receivers\",error_code=\"none\"",
        "target=\"bot\",delivery_kind=\"send\",result=\"blocked\",error_code=\"policy_blocked\"",
        "bcs_message_delivery_duration_seconds_bucket",
    ] {
        assert!(body.contains(expected), "missing metrics fragment: {expected}");
    }
    assert!(!body.contains("raw policy message"));
}

#[cfg(feature = "prometheus-metrics")]
fn install_runtime() -> Arc<MetricsRuntime> {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    MetricsRuntime::install(&config)
        .expect("install metrics")
        .expect("metrics enabled")
}

struct GroupServiceFake;

#[async_trait]
impl GroupManagementService for GroupServiceFake {
    async fn create_group(
        &self,
        _cmd: GroupCreateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Ok(group_detail(GroupStatus::Active, GroupKind::Normal))
    }

    async fn create_dm(&self, _cmd: DmCreateCommand) -> Result<DmCreateResult, GroupUseCaseError> {
        Ok(DmCreateResult {
            group: group_detail(GroupStatus::Active, GroupKind::Dm),
            created: true,
        })
    }

    async fn update_status(
        &self,
        cmd: GroupStatusCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        let status = match cmd.status.as_str() {
            "completed" => GroupStatus::Completed,
            "closed" => GroupStatus::Closed,
            _ => GroupStatus::Active,
        };
        Ok(group_detail(status, GroupKind::Normal))
    }

    async fn add_member(
        &self,
        _cmd: GroupAddMemberCommand,
    ) -> Result<GroupAddMemberResult, GroupUseCaseError> {
        Ok(GroupAddMemberResult {
            group_id: "group-wrapper".to_string(),
            member: participant_view("bot-member"),
        })
    }

    async fn remove_member(
        &self,
        cmd: GroupRemoveMemberCommand,
    ) -> Result<GroupRemoveMemberResult, GroupUseCaseError> {
        Ok(GroupRemoveMemberResult {
            group_id: cmd.group_id,
            removed_bot_uuid: cmd.bot_id,
        })
    }

    async fn delete_group(
        &self,
        _cmd: GroupDeleteCommand,
    ) -> Result<GroupDeleteResult, GroupUseCaseError> {
        Ok(GroupDeleteResult {
            group_id: "group-wrapper".to_string(),
            deleted: true,
        })
    }

    async fn terminate_group(
        &self,
        _cmd: GroupTerminateCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Ok(group_detail(GroupStatus::Completed, GroupKind::Normal))
    }

    async fn update_label(
        &self,
        _cmd: GroupUpdateLabelCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Ok(group_detail(GroupStatus::Active, GroupKind::Normal))
    }

    async fn update_visibility(
        &self,
        _cmd: GroupUpdateVisibilityCommand,
    ) -> Result<GroupDetailResult, GroupUseCaseError> {
        Ok(group_detail(GroupStatus::Active, GroupKind::Normal))
    }

    async fn update_workspace(
        &self,
        _cmd: GroupUpdateWorkspaceCommand,
    ) -> Result<GroupWorkspaceResult, GroupUseCaseError> {
        Ok(GroupWorkspaceResult {
            group_id: "group-wrapper".to_string(),
            workspace: Workspace::default(),
        })
    }

    async fn update_routing_policy(
        &self,
        _cmd: GroupRoutingPolicyCommand,
    ) -> Result<GroupRoutingPolicyResult, GroupUseCaseError> {
        Ok(GroupRoutingPolicyResult {
            group_id: "group-wrapper".to_string(),
            routing_policy: RoutingPolicy::default(),
        })
    }

    async fn update_participant_mode(
        &self,
        cmd: GroupParticipantModeCommand,
    ) -> Result<GroupParticipantModeResult, GroupUseCaseError> {
        Ok(GroupParticipantModeResult {
            group_id: cmd.group_id,
            actor_id: cmd.actor_id,
            mode: cmd.mode,
        })
    }

    async fn patch_group_settings(
        &self,
        cmd: GroupPatchSettingsCommand,
    ) -> Result<GroupPatchSettingsResult, GroupUseCaseError> {
        Ok(GroupPatchSettingsResult {
            group_id: cmd.group_id,
            service_spec: cmd.service_spec.unwrap_or(None),
        })
    }
}

struct MessageFlowFake;

#[async_trait]
impl MessageFlowService for MessageFlowFake {
    async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        Ok(WebSendOutcome {
            primary_run_id: "run-wrapper".to_string(),
            status: "ok".to_string(),
            active_run_ids: vec![],
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
            mentions: vec![],
            hidden_mentions: vec![],
            delivered_count: 0,
            failed_count: 0,
            delivery_results: vec![],
        })
    }

    async fn handle_group_chat(&self, _cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
        Ok(GroupChatOutcome {
            group_id: "group-wrapper".to_string(),
            driver_bot_id: "bot-driver".to_string(),
            delivered_count: 0,
            failed_count: 0,
            delivery_results: vec![],
            mentions: vec![],
            hidden_mentions: vec![],
        })
    }

    async fn handle_persistent_group_send(
        &self,
        _cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        Ok(PersistentGroupSendOutcome {
            message_id: "msg-wrapper".to_string(),
            routed_to: vec![],
            mentions: vec![],
        })
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        Ok(BotEventOutcome {
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
            unregistered_run_ids: vec![],
            mentions: vec![],
            delivered_count: 0,
            failed_count: 0,
            delivery_results: vec![],
        })
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        Ok(GroupCallbackOutcome {
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
            mentions: vec![],
            delivered_count: 0,
            failed_count: 0,
            delivery_results: vec![],
        })
    }

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        Ok(ChatAbortOutcome {
            aborted: true,
            aborted_run_ids: vec!["run-wrapper".to_string()],
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
        })
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        Ok(TaskRunAliasRegistration::Registered)
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        Ok(TaskDispatchOutcome {
            task_id: "task-wrapper".to_string(),
            status: "ok".to_string(),
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
        })
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        Ok(TaskCompleteOutcome {
            status: "ok".to_string(),
            blocked: false,
            pending: Vec::new(),
            callback_requested: false,
            completed_session: None,
            frontend_deliveries: vec![],
        })
    }
}

struct A2aRunFake;

#[async_trait]
impl A2aChatRunService for A2aRunFake {
    async fn run_blocking_chat(
        &self,
        _cmd: BlockingA2aChatCommand,
    ) -> ServiceResult<BlockingA2aChatOutcome> {
        Ok(BlockingA2aChatOutcome {
            delivered: true,
            bot_uuid: "bot-target".to_string(),
            session_id: "session-wrapper".to_string(),
            content: "ok".to_string(),
        })
    }

    async fn start_async_chat(
        &self,
        _cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        Ok(AsyncA2aChatAccepted {
            run_id: "run-wrapper".to_string(),
            bot_uuid: "bot-target".to_string(),
            session_id: "session-wrapper".to_string(),
            status: "pending".to_string(),
            expires_at_ms: 1,
        })
    }

    async fn get_run(&self, _cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> {
        Ok(A2aRunStatus {
            run_id: "run-wrapper".to_string(),
            status: "completed".to_string(),
            response: None,
        })
    }

    async fn cancel_run(&self, _cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> {
        Ok(A2aRunStatus {
            run_id: "run-wrapper".to_string(),
            status: "cancelled".to_string(),
            response: None,
        })
    }
}

struct BotDeliveryFake {
    delivered: bool,
}

#[async_trait]
impl BotDeliveryPort for BotDeliveryFake {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        self.delivered
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: self.delivered,
            error: (!self.delivered)
                .then(|| ServiceError::BotNotConnected("bot-target".to_string())),
        })
    }
}

struct FrontendDeliveryFake {
    delivered: usize,
}

#[async_trait]
impl FrontendDeliveryPort for FrontendDeliveryFake {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        Ok(FrontendDeliveryResult {
            target: cmd.target,
            delivered: self.delivered,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

fn group_detail(status: GroupStatus, group_kind: GroupKind) -> GroupDetailResult {
    GroupDetailResult {
        group_id: "group-wrapper".to_string(),
        label: None,
        status,
        driver_bot_id: "bot-driver".to_string(),
        context: None,
        participants: vec![participant_view("bot-driver")],
        message_count: 0,
        workspace: Workspace::default(),
        service_group_uuid: None,
        service_mode: None,
        group_kind,
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        created_at: 1,
        updated_at: 1,
        chat_url: None,
        context_injected: 0,
        service_spec: None,
        latest_running_session_id: None,
        originator: None,
        visibility: "private".to_string(),
    }
}

fn participant_view(bot_uuid: &str) -> GroupParticipantView {
    GroupParticipantView {
        bot_uuid: bot_uuid.to_string(),
        bot_name: None,
        kind: None,
        role: "driver".to_string(),
        actor_kind: ActorKind::Bot,
        mode: None,
    }
}

fn group_create_cmd() -> GroupCreateCommand {
    GroupCreateCommand {
        group_id: Some("group-wrapper".to_string()),
        caller_actor_id: None,
        driver_bot_id: "bot-driver".to_string(),
        originator: None,
        label: None,
        topic: None,
        context: None,
        routing_policy: None,
        participants: vec![],
        member_bot_ids: vec![],
        group_kind: Some(GroupKind::Normal),
        service_spec: None,
        group_strategy: None,
        visibility: None,
    }
}

fn group_status_cmd(status: &str) -> GroupStatusCommand {
    GroupStatusCommand {
        caller_actor_id: None,
        group_id: "group-wrapper".to_string(),
        status: status.to_string(),
    }
}

fn group_add_member_cmd() -> GroupAddMemberCommand {
    GroupAddMemberCommand {
        caller_actor_id: None,
        human_actor_id: None,
        group_id: "group-wrapper".to_string(),
        bot_id: "bot-member".to_string(),
        role: None,
    }
}

fn group_delete_cmd() -> GroupDeleteCommand {
    GroupDeleteCommand {
        caller_actor_id: "bot-driver".to_string(),
        group_id: "group-wrapper".to_string(),
    }
}

fn web_send_cmd() -> WebSendCommand {
    WebSendCommand {
        caller: CallerContext::Public,
        group_id: "group-wrapper".to_string(),
        session_id: None,
        from_actor_id: "human-user".to_string(),
        from_name: None,
        message: "hello".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        sender_conn_id: None,
    }
}

fn group_chat_cmd() -> GroupChatCommand {
    GroupChatCommand {
        caller: CallerContext::Public,
        group_id: "group-wrapper".to_string(),
        requested_sender_id: None,
        message: "hello".to_string(),
        session_id: None,
    }
}

fn persistent_group_send_cmd() -> PersistentGroupSendCommand {
    PersistentGroupSendCommand {
        caller: CallerContext::Public,
        group_id: "group-wrapper".to_string(),
        sender: "bot-driver".to_string(),
        content: "hello".to_string(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::User,
        max_group_messages: 10,
        store_messages: true,
    }
}

fn bot_event_cmd() -> BotEventCommand {
    BotEventCommand {
        bot_id: "bot-driver".to_string(),
        run_id: "run-wrapper".to_string(),
        group_id: "group-wrapper".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: serde_json::json!({}),
        state: ChatEventState::Final,
        bcs_session_id: None,
    }
}

fn group_callback_cmd() -> GroupCallbackCommand {
    GroupCallbackCommand {
        group_id: "group-wrapper".to_string(),
        message: "hello".to_string(),
        mentions: vec![],
        metadata: None,
        store_message: false,
    }
}

fn chat_abort_cmd() -> ChatAbortCommand {
    ChatAbortCommand {
        caller: CallerContext::Public,
        group_id: "group-wrapper".to_string(),
        run_id: Some("run-wrapper".to_string()),
    }
}

fn task_dispatch_cmd() -> TaskDispatchCommand {
    TaskDispatchCommand {
        driver_bot_id: "bot-driver".to_string(),
        group_id: "group-wrapper".to_string(),
        target_bot_id: "bot-target".to_string(),
        target_bot_name: None,
        payload: serde_json::json!({}),
    }
}

fn task_complete_cmd() -> TaskCompleteCommand {
    TaskCompleteCommand {
        task_id: "task-wrapper".to_string(),
        bot_id: "bot-target".to_string(),
        via_echo: false,
        payload: serde_json::json!({}),
    }
}

fn blocking_chat_cmd() -> BlockingA2aChatCommand {
    BlockingA2aChatCommand {
        caller: CallerContext::Public,
        target_bot_id: "bot-target".to_string(),
        message: "hello".to_string(),
        from_actor_id: None,
        run_channel_from: None,
        authenticated_staff_id: None,
        tags: Vec::new(),
        run_id: "run-wrapper".to_string(),
        session_key: "session-wrapper".to_string(),
        timeout_ms: 1,
        client: None,
        response_mode: ChatResponseMode::Full,
        organization_code: None,
    }
}

fn bot_delivery_cmd(delivery_kind: BotDeliveryKind) -> BotDeliveryCommand {
    BotDeliveryCommand {
        target: BotDeliveryTarget::WebSocket {
            bot_id: "bot-target".to_string(),
        },
        run_id: "run-wrapper".to_string(),
        frame: BcsFrame::Request(RequestFrame::new("run-wrapper", "chat.send", None)),
        delivery_kind,
        provider_transport: Default::default(),
    }
}

fn frontend_delivery_cmd() -> FrontendDeliveryCommand {
    FrontendDeliveryCommand {
        target: FrontendDeliveryTarget::Group {
            group_id: "group-wrapper".to_string(),
        },
        event_json: "{}".to_string(),
        delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
        run_fallback: None,
        exclude_conn_id: None,
    }
}
