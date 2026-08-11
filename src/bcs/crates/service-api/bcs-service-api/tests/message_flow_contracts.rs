use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::{
    BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget,
    BotEventCommand, BotEventOutcome, CallerContext, ChatAbortCommand, ChatAbortOutcome, FrontendDeliveryCommand,
    FrontendDeliveryKind, FrontendDeliveryPort, FrontendDeliveryResult, FrontendDeliveryTarget,
    FusionRequest, GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand,
    GroupFusionCommand, GroupFusionService, GroupMessageType, MessageFlowService, MessageRole,
    PersistentGroupSendCommand, ServiceError, ServiceResult, TaskCompleteCommand,
    TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome, TaskRunAliasRegistration,
    WebSendCommand, WebSendOutcome,
};
use bcs_test_support::{NoopGroupFusionService, NoopMessageFlowService};

#[derive(Debug, Default)]
struct FakeBotDelivery {
    delivered: Mutex<Vec<String>>,
}

#[async_trait]
impl BotDeliveryPort for FakeBotDelivery {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        target.bot_id() == "bot-1"
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        self.delivered
            .lock()
            .unwrap()
            .push(target_bot_id.clone());
        Ok(BotDeliveryResult {
            target_bot_id,
            delivered: true,
            error: None,
        })
    }
}

#[derive(Debug, Default)]
struct FakeFrontendDelivery;

#[async_trait]
impl FrontendDeliveryPort for FakeFrontendDelivery {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        Ok(FrontendDeliveryResult {
            target: cmd.target,
            delivered: 1,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

#[tokio::test]
async fn delivery_ports_are_transport_free_contracts() {
    let bot_port: Arc<dyn BotDeliveryPort> = Arc::new(FakeBotDelivery::default());
    let frontend_port: Arc<dyn FrontendDeliveryPort> = Arc::new(FakeFrontendDelivery);

    let frame = BcsFrame::Request(RequestFrame::new(
        "run-1",
        "chat.send",
        Some(serde_json::json!({"message": "hello"})),
    ));

    let result = bot_port
        .deliver(BotDeliveryCommand {
            target: BotDeliveryTarget::WebSocket {
                bot_id: "bot-1".to_string(),
            },
            run_id: "run-1".to_string(),
            frame,
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert!(result.delivered);

    let frontend = frontend_port
        .publish(FrontendDeliveryCommand {
            target: FrontendDeliveryTarget::Run {
                run_id: "run-1".to_string(),
            },
            event_json: r#"{"type":"event"}"#.to_string(),
            delivery_kind: FrontendDeliveryKind::RunEvent,
            run_fallback: None,
            exclude_conn_id: None,
        })
        .await
        .unwrap();

    assert_eq!(frontend.delivered, 1);
}

#[tokio::test]
async fn noop_group_fusion_service_fails_closed() {
    let service = NoopGroupFusionService;

    let result = service
        .fuse_for_group(GroupFusionCommand {
            group_id: "group-1".to_string(),
            request: FusionRequest::default(),
        })
        .await;

    assert_service_not_configured(result, "group fusion service is not configured");
}

#[tokio::test]
async fn added_message_flow_methods_fail_closed_for_noop_and_legacy_implementations() {
    let noop = NoopMessageFlowService;
    let legacy = LegacyMessageFlowService;

    assert_service_not_configured(
        noop.handle_group_chat(group_chat_command()).await,
        "message flow service is not configured",
    );
    assert_service_not_configured(
        noop.handle_persistent_group_send(persistent_group_send_command())
            .await,
        "message flow service is not configured",
    );
    assert_service_not_configured(
        legacy.handle_group_chat(group_chat_command()).await,
        "message flow service is not configured",
    );
    assert_service_not_configured(
        legacy
            .handle_persistent_group_send(persistent_group_send_command())
            .await,
        "message flow service is not configured",
    );
}

fn group_chat_command() -> GroupChatCommand {
    GroupChatCommand {
        caller: CallerContext::Public,
        group_id: "group-1".to_string(),
        requested_sender_id: None,
        message: "hello".to_string(),
        session_id: None,
        provider_bypass_headers: Vec::new(),
    }
}

fn persistent_group_send_command() -> PersistentGroupSendCommand {
    PersistentGroupSendCommand {
        caller: CallerContext::Public,
        group_id: "group-1".to_string(),
        sender: "human_alice".to_string(),
        content: "hello".to_string(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::User,
        max_group_messages: 10,
        store_messages: false,
    }
}

fn assert_service_not_configured<T>(result: ServiceResult<T>, expected_message: &str) {
    assert!(matches!(
        result,
        Err(ServiceError::InvalidOperation {
            message,
            request_id: None,
        }) if message == expected_message
    ));
}

#[derive(Debug, Default)]
struct LegacyMessageFlowService;

#[async_trait]
impl MessageFlowService for LegacyMessageFlowService {
    async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        Err(ServiceError::InternalError("not used".to_string()))
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        Err(ServiceError::InternalError("not used".to_string()))
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        Err(ServiceError::InternalError("not used".to_string()))
    }

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        Err(ServiceError::InternalError("not used".to_string()))
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        Err(ServiceError::InternalError("not used".to_string()))
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        Err(ServiceError::InternalError("not used".to_string()))
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        Err(ServiceError::InternalError("not used".to_string()))
    }
}
