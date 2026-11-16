use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthError, AuthPluginChain, AuthPrincipal, UserIdentityInfo};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::BotCore;
use bcs_domain::{AttachmentType, MessageAttachment};
use bcs_group::{GroupManagement, GroupStore};
use bcs_http::{
    router::build_router,
    state::{BotRequestPort, ChainUserIdentityPort, HttpAppState, UserIdentityPort},
};
use bcs_protocol::{BcsFrame, BotDeliveryKind, FrontendDeliveryKind, FrontendDeliveryTarget};
use bcs_service_api::{
    BotCapabilities, BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget,
    BotEventCommand, BotEventOutcome, BotRegistryCoreService, BotSendResult, CallerContext,
    CancelStateMachineRunCommand, ChatAbortCommand, ChatAbortOutcome, CollaborationDefinition,
    CollaborationRuntimeError, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, DeliveryType, FrontendDeliveryCommand, FrontendDeliveryPort,
    FrontendDeliveryResult, Group, GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand,
    GroupChatOutcome, GroupCoreService, GroupHistoryCommand, GroupHistoryResult, GroupMessage,
    GroupMessageHistoryService, GroupMessageType, GroupStatus, GroupStrategy, GroupUseCaseError,
    HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, MessageDeliveryResult,
    MessageFlowService, MessageRole, Participant, ParticipantMode, ParticipantRole,
    PersistentGroupSendCommand, PersistentGroupSendOutcome, RouteAndSendResult, RoutingCoreService,
    RoutingDecision, RoutingTarget, ServiceError, ServiceResult, Session, SessionHistoryCommand,
    SessionHistoryResult, SessionKind, SessionManagementService, SessionStatus,
    SessionUseCaseError, StartStateMachineRunCommand, StartStateMachineRunOutcome,
    StateMachineDeliveryCorrelation, StateMachineRunView, SystemMessageEvent, SystemMessageService,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome,
};
use bcs_services_container::Services;
use bcs_test_support::NoopFriendCoreService;
use serde_json::Value;
use std::sync::Arc;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

struct NoUserIdentity;

#[async_trait::async_trait]
impl UserIdentityPort for NoUserIdentity {
    async fn extract(
        &self,
        _headers: &axum::http::HeaderMap,
        _uri: &axum::http::Uri,
    ) -> Option<bcs_http::state::HttpUserIdentity> {
        None
    }

    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Ok("noop-identity".to_string())
    }

    async fn get_identity_by_token(
        &self,
        _token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }
}

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

#[derive(Default)]
struct RecordingRouting {
    sent_to_bot: Mutex<Vec<(String, String, Option<String>, Option<String>)>>,
}

#[async_trait::async_trait]
impl RoutingCoreService for RecordingRouting {
    async fn route(
        &self,
        group: &Group,
        message: &str,
        sender_bot_id: Option<&str>,
    ) -> RoutingDecision {
        let targets = group
            .participants
            .iter()
            .filter(|participant| participant.is_bot())
            .filter(|participant| Some(participant.bot_uuid.as_str()) != sender_bot_id)
            .map(|participant| RoutingTarget {
                bot_uuid: participant.bot_uuid.clone(),
                url: String::new(),
                is_driver: participant.role == ParticipantRole::Driver,
                delivery_type: DeliveryType::Send,
            })
            .collect();
        RoutingDecision {
            targets,
            mentions: vec!["target-bot".to_string()],
            cleaned_message: message.replace("@Target", "Target"),
            hidden_mentions: vec![],
        }
    }

    async fn send_to_bot(
        &self,
        target: &RoutingTarget,
        message: &str,
        from: Option<&str>,
        group_id: Option<&str>,
    ) -> BotSendResult {
        self.sent_to_bot.lock().await.push((
            target.bot_uuid.clone(),
            message.to_string(),
            from.map(str::to_string),
            group_id.map(str::to_string),
        ));
        BotSendResult {
            bot_uuid: target.bot_uuid.clone(),
            content: String::new(),
            success: true,
            error: None,
        }
    }

    async fn route_and_send(
        &self,
        _group: &Group,
        _message: &str,
        _from: Option<&str>,
    ) -> RouteAndSendResult {
        unreachable!("not used by this contract")
    }
}

#[derive(Default)]
struct RecordingBotDelivery {
    frames: Mutex<Vec<(String, BotDeliveryKind, BcsFrame)>>,
}

#[async_trait::async_trait]
impl BotDeliveryPort for RecordingBotDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        self.frames
            .lock()
            .await
            .push((target_bot_id.clone(), cmd.delivery_kind, cmd.frame));
        Ok(BotDeliveryResult {
            target_bot_id,
            delivered: true,
            error: None,
        })
    }
}

#[derive(Default)]
struct RecordingFrontendDelivery {
    events: Mutex<Vec<(FrontendDeliveryTarget, FrontendDeliveryKind, String)>>,
}

#[async_trait::async_trait]
impl FrontendDeliveryPort for RecordingFrontendDelivery {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        self.events
            .lock()
            .await
            .push((cmd.target.clone(), cmd.delivery_kind, cmd.event_json));
        Ok(FrontendDeliveryResult {
            target: cmd.target,
            delivered: 1,
        })
    }

    async fn unregister_run(&self, _run_id: &str) -> ServiceResult<()> {
        Ok(())
    }
}

#[derive(Default)]
struct RecordingBotRequest {
    requests: Mutex<Vec<(String, String, Value, u64)>>,
}

#[async_trait::async_trait]
impl BotRequestPort for RecordingBotRequest {
    async fn send_request(
        &self,
        bot_uuid: &str,
        method: &str,
        params: Value,
        timeout_ms: u64,
    ) -> Result<Value, String> {
        self.requests.lock().await.push((
            bot_uuid.to_string(),
            method.to_string(),
            params,
            timeout_ms,
        ));
        Ok(serde_json::json!({
            "messages": [
                {
                    "id": "hist-1",
                    "role": "assistant",
                    "content": "history answer",
                    "timestamp": 42
                }
            ]
        }))
    }
}

struct StaticSessionManagement {
    session: Mutex<Session>,
}

impl StaticSessionManagement {
    fn new(session: Session) -> Self {
        Self {
            session: Mutex::new(session),
        }
    }
}

#[async_trait::async_trait]
impl SessionManagementService for StaticSessionManagement {
    async fn create_or_reactivate(
        &self,
        _cmd: bcs_service_api::CreateOrReactivateCommand,
    ) -> Result<bcs_service_api::CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        let session = self.session.lock().await;
        Ok((session.id == session_id).then(|| session.clone()))
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        let session = self.session.lock().await;
        Ok(session.id == session_id && session.group_id == group_id)
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        let mut session = self.session.lock().await;
        if session.id != session_id {
            return Err(SessionUseCaseError::NotFound(session_id.to_string()));
        }
        if !session
            .participants
            .iter()
            .any(|existing| existing.bot_uuid == participant.bot_uuid)
        {
            session.participants.push(participant);
        }
        Ok(session.clone())
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }
}

fn test_session(session_id: &str, group_id: &str, participants: Vec<Participant>) -> Session {
    test_session_with_status(session_id, group_id, participants, SessionStatus::Running)
}

fn test_session_with_status(
    session_id: &str,
    group_id: &str,
    participants: Vec<Participant>,
    status: SessionStatus,
) -> Session {
    Session {
        id: session_id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status,
        session_kind: SessionKind::Chat,
        participants,
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        activation_count: 1,
        message_visibility_version: 1,
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        collected_at: None,
    }
}

#[derive(Default)]
struct RecordingMessageFlow {
    web_sends: Mutex<Vec<WebSendCommand>>,
    group_chats: Mutex<Vec<GroupChatCommand>>,
    persistent_sends: Mutex<Vec<PersistentGroupSendCommand>>,
    callbacks: Mutex<Vec<GroupCallbackCommand>>,
    system_notifications: Mutex<Vec<SystemMessageEvent>>,
    next_group_chat_error: Mutex<Option<ServiceError>>,
    next_persistent_error: Mutex<Option<ServiceError>>,
}

#[async_trait::async_trait]
impl MessageFlowService for RecordingMessageFlow {
    async fn query_message_deliveries(
        &self,
        _query: bcs_service_api::application::message_delivery::DeliveryStatusQuery,
    ) -> ServiceResult<Vec<bcs_service_api::application::message_delivery::DeliveryStatusView>> {
        Ok(Vec::new())
    }
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        self.web_sends.lock().await.push(cmd);
        Ok(WebSendOutcome {
            operation_id: "op-test".to_string(),
            queue_admission: None,
            primary_run_id: "run-web".to_string(),
            status: "started".to_string(),
            active_run_ids: vec!["run-web".to_string()],
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            mentions: vec!["target-bot".to_string()],
            hidden_mentions: vec![],
            delivered_count: 2,
            failed_count: 0,
            delivery_results: vec![
                MessageDeliveryResult {
                    bot_uuid: "owner-bot".to_string(),
                    delivery_type: DeliveryType::Inject,
                    success: true,
                    error: None,
                },
                MessageDeliveryResult {
                    bot_uuid: "target-bot".to_string(),
                    delivery_type: DeliveryType::Send,
                    success: true,
                    error: None,
                },
            ],
        })
    }

    async fn handle_group_chat(&self, cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
        self.group_chats.lock().await.push(cmd);
        if let Some(error) = self.next_group_chat_error.lock().await.take() {
            return Err(error);
        }
        Ok(GroupChatOutcome {
            queue_admission: None,
            group_id: "group-1".to_string(),
            driver_bot_id: "owner-bot".to_string(),
            mentions: vec!["target-bot".to_string()],
            hidden_mentions: vec![],
            delivered_count: 2,
            failed_count: 0,
            delivery_results: vec![
                MessageDeliveryResult {
                    bot_uuid: "owner-bot".to_string(),
                    delivery_type: DeliveryType::Inject,
                    success: true,
                    error: None,
                },
                MessageDeliveryResult {
                    bot_uuid: "target-bot".to_string(),
                    delivery_type: DeliveryType::Send,
                    success: true,
                    error: None,
                },
            ],
        })
    }

    async fn handle_persistent_group_send(
        &self,
        cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        self.persistent_sends.lock().await.push(cmd);
        if let Some(error) = self.next_persistent_error.lock().await.take() {
            return Err(error);
        }
        Ok(PersistentGroupSendOutcome {
            queue_admission: None,
            message_id: "flow-message-1".to_string(),
            routed_to: vec!["target-bot".to_string()],
            mentions: vec!["target-bot".to_string()],
        })
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        unreachable!("group callback route should use the pure group callback command")
    }

    async fn handle_group_callback(
        &self,
        cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        self.callbacks.lock().await.push(cmd);
        Ok(GroupCallbackOutcome {
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            mentions: vec!["target-bot".to_string()],
            delivered_count: 1,
            failed_count: 0,
            delivery_results: vec![MessageDeliveryResult {
                bot_uuid: "target-bot".to_string(),
                delivery_type: DeliveryType::Send,
                success: true,
                error: None,
            }],
        })
    }

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        unreachable!("not used by this contract")
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        unreachable!("not used by this contract")
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        unreachable!("not used by this contract")
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        unreachable!("not used by this contract")
    }
}

#[async_trait::async_trait]
impl SystemMessageService for RecordingMessageFlow {
    async fn notify(
        &self,
        _group_id: &str,
        event: SystemMessageEvent,
        _session_id: &str,
        _session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.system_notifications.lock().await.push(event);
        Ok(1)
    }
}

#[derive(Default)]
struct RecordingGroupMessageHistory {
    calls: Mutex<Vec<GroupHistoryCommand>>,
    session_calls: Mutex<Vec<SessionHistoryCommand>>,
}

fn rich_session_messages() -> Vec<GroupMessage> {
    vec![GroupMessage {
        id: "hist-1".to_string(),
        timestamp: 42,
        sender: "owner-bot".to_string(),
        content: "history answer".to_string(),
        message_type: GroupMessageType::Bot,
        bot_name: Some("Owner Bot".into()),
        role: MessageRole::Assistant,
        history_meta: Some(serde_json::json!({"assistantAggregation": true})),
        metadata: Some(serde_json::json!({"tool": "search"})),
        run_id: "run-1".into(),
        attachments: Some(vec![MessageAttachment {
            attachment_id: "att-1".into(),
            attachment_type: AttachmentType::Image,
            file_name: "result.png".into(),
            mime_type: Some("image/png".into()),
            size: Some(42),
            sha256: Some("abcd".into()),
            url: Some("https://download.example/result.png".into()),
            expires_at: Some(123),
        }]),
    }]
}

#[async_trait::async_trait]
impl GroupMessageHistoryService for RecordingGroupMessageHistory {
    async fn get_history(
        &self,
        cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        self.calls.lock().await.push(cmd.clone());
        Ok(GroupHistoryResult {
            group_id: cmd.group_id,
            messages: vec![GroupMessage {
                id: "hist-use-case".to_string(),
                timestamp: 42,
                sender: "owner-bot".to_string(),
                content: "history answer".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
                attachments: None,
            }],
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        self.session_calls.lock().await.push(cmd.clone());
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages: rich_session_messages(),
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }
}

struct RecordingStateMachineHistoryRuntime {
    calls: Mutex<Vec<(String, u64, Option<u64>)>>,
    human_views: Mutex<Vec<bcs_domain::HumanMessageView>>,
    result: SessionHistoryResult,
}

#[async_trait::async_trait]
impl CollaborationRuntimeService for RecordingStateMachineHistoryRuntime {
    async fn start_state_machine_run(
        &self,
        _cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        unimplemented!("not used by this contract")
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_session_history(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        self.calls
            .lock()
            .await
            .push((session_id.to_string(), limit, before));
        Ok(Some(self.result.clone()))
    }

    async fn get_state_machine_session_history_for_view(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
        human_view: bcs_domain::HumanMessageView,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        self.human_views.lock().await.push(human_view);
        self.get_state_machine_session_history(session_id, limit, before)
            .await
    }

    async fn cancel_state_machine_run(
        &self,
        _cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        unimplemented!("not used by this contract")
    }

    async fn lookup_delivery_correlation(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn register_delivery_alias(
        &self,
        _delivery_request_id: &str,
        _bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        _cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        Ok(HandleBotTerminalEventOutcome {
            consumed: false,
            view: None,
        })
    }

    async fn upsert_definition(
        &self,
        _definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        _cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        unimplemented!("not used by this contract")
    }
}

async fn build_group_app() -> (
    axum::Router,
    Arc<GroupStore>,
    Arc<RecordingRouting>,
    Arc<RecordingBotDelivery>,
    Arc<RecordingFrontendDelivery>,
    Arc<RecordingBotRequest>,
    Arc<RecordingMessageFlow>,
    Arc<RecordingGroupMessageHistory>,
) {
    let chain = static_auth_chain("123", "Owner");
    build_group_app_with_identity(Arc::new(ChainUserIdentityPort::new(chain))).await
}



async fn build_group_app_with_identity(
    user_identity: Arc<dyn UserIdentityPort>,
) -> (
    axum::Router,
    Arc<GroupStore>,
    Arc<RecordingRouting>,
    Arc<RecordingBotDelivery>,
    Arc<RecordingFrontendDelivery>,
    Arc<RecordingBotRequest>,
    Arc<RecordingMessageFlow>,
    Arc<RecordingGroupMessageHistory>,
) {
    build_group_app_with_identity_and_session_status(
        user_identity,
        SessionStatus::Running,
        None,
    )
    .await
}

async fn build_group_app_with_identity_and_session_status(
    user_identity: Arc<dyn UserIdentityPort>,
    session_status: SessionStatus,
    session_participants: Option<Vec<Participant>>,
) -> (
    axum::Router,
    Arc<GroupStore>,
    Arc<RecordingRouting>,
    Arc<RecordingBotDelivery>,
    Arc<RecordingFrontendDelivery>,
    Arc<RecordingBotRequest>,
    Arc<RecordingMessageFlow>,
    Arc<RecordingGroupMessageHistory>,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name) in [
        ("owner-bot", "Owner"),
        ("target-bot", "Target"),
        ("intruder-bot", "Intruder"),
    ] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("intruder-token".to_string(), "intruder-bot".to_string())
        .await;
    registry
        .save_created_by("owner-bot", "123", true)
        .await
        .unwrap();

    let mut group = Group::new(
        "group-1",
        "owner-bot",
        vec![
            Participant::bot("owner-bot", ParticipantRole::Driver),
            Participant::bot("target-bot", ParticipantRole::Consultant),
            Participant::human("human_123", ParticipantRole::Observer),
        ],
    );
    group.status = GroupStatus::Active;
    group.messages.push(GroupMessage {
        id: "stored-1".to_string(),
        timestamp: 1,
        sender: "owner-bot".to_string(),
        content: "[from:Owner]stored hello".to_string(),
        message_type: GroupMessageType::Bot,
        bot_name: None,
        role: MessageRole::User,
        history_meta: None,
        metadata: None,
        run_id: String::new(),
        attachments: None,
    });

    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    // LIVE authority chain mirroring the fixture
    // `created_by` rows (bot-authority plan Task 6.5).
    let authority = bcs_test_support::lazy_authority_chain().hook;
    authority.seed_owner("owner-bot", "123");
    let routing = Arc::new(RecordingRouting::default());
    let bot_delivery = Arc::new(RecordingBotDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let bot_request = Arc::new(RecordingBotRequest::default());
    let message_flow = Arc::new(RecordingMessageFlow::default());
    let group_message_history = Arc::new(RecordingGroupMessageHistory::default());
    let session_participants = session_participants.unwrap_or_else(|| {
        vec![
            Participant::bot("owner-bot", ParticipantRole::Driver),
            Participant::bot("target-bot", ParticipantRole::Consultant),
            Participant::human("human_123", ParticipantRole::Observer),
        ]
    });
    let group_use_cases = Arc::new(GroupManagement::with_defaults(
        group_store.clone(),
        registry.clone(),
        Arc::new(NoopFriendCoreService),
    ));
    let services = Services::builder()
        .bot_query(Arc::new(bcs_bot::Bot::new(registry.clone())))
        .registry(registry)
        .group(group_store.clone())
        .routing(routing.clone())
        .bot_delivery(bot_delivery.clone())
        .frontend_delivery(frontend_delivery.clone())
        .message_flow(message_flow.clone())
        .system_message(message_flow.clone())
        .session_management(Arc::new(StaticSessionManagement::new(
            test_session_with_status(
                "group-1:abcdef12",
                "group-1",
                session_participants,
                session_status,
            ),
        )))
        .group_query(group_use_cases)
        .group_message_history(group_message_history.clone())
        .bot_authority_hook(authority as _)
        .build_for_test();
    let app = build_router(
        HttpAppState::new(services)
            .with_user_identity(user_identity)
            .with_bot_request(bot_request.clone())
            .with_message_config(true, 10, 0, 0, 60_000),
    );
    (
        app,
        group_store,
        routing,
        bot_delivery,
        frontend_delivery,
        bot_request,
        message_flow,
        group_message_history,
    )
}











#[path = "group_messages_contract/history.rs"]
mod history;
#[path = "group_messages_contract/access.rs"]
mod access;
#[path = "group_messages_contract/sending.rs"]
mod sending;
