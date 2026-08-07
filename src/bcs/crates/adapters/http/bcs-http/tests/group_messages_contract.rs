use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthError, AuthPluginChain, AuthPrincipal, UserIdentityInfo};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::BotCore;
use bcs_group::{GroupManagement, GroupStore};
use bcs_http::{
    router::build_router,
    state::{BotRequestPort, ChainUserIdentityPort, HttpAppState, UserIdentityPort},
};
use bcs_protocol::{BcsFrame, BotDeliveryKind, FrontendDeliveryKind, FrontendDeliveryTarget};
use bcs_service_api::{
    BotCapabilities, BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget,
    BotEventCommand, BotEventOutcome, BotRegistryCoreService, BotSendResult, CallerContext, ChatAbortCommand,
    ChatAbortOutcome, CancelStateMachineRunCommand, CollaborationDefinition,
    CollaborationRuntimeError, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, DeliveryType, FrontendDeliveryCommand, FrontendDeliveryPort,
    FrontendDeliveryResult, Group, GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand,
    GroupChatOutcome, GroupCoreService, GroupHistoryCommand, GroupHistoryResult, GroupMessage,
    GroupMessageHistoryService, GroupMessageType, GroupStatus, GroupUseCaseError,
    GroupStrategy, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    MessageDeliveryResult, MessageFlowService, MessageRole, Participant, ParticipantRole,
    ParticipantMode,
    SessionHistoryCommand, SessionHistoryResult,
    PersistentGroupSendCommand, PersistentGroupSendOutcome, RouteAndSendResult, RoutingCoreService,
    RoutingDecision, RoutingTarget, ServiceError, ServiceResult, Session, SessionKind,
    SessionManagementService, SessionStatus, SessionUseCaseError, SystemMessageEvent,
    SystemMessageService, TaskCompleteCommand,
    TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome, TaskRunAliasRegistration,
    WebSendCommand, WebSendOutcome,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineDeliveryCorrelation,
    StateMachineRunView,
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

    async fn update_token(
        &self,
        _user_id: &str,
        _token: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        Ok(())
    }
}

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(principal))]))
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

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
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
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

fn test_session(session_id: &str, group_id: &str, participants: Vec<Participant>) -> Session {
    Session {
        id: session_id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants,
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
        activation_count: 1,
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
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        self.web_sends.lock().await.push(cmd);
        Ok(WebSendOutcome {
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
        let session_member = match &cmd.caller {
            CallerContext::Human(human) => cmd
                .session_participants
                .iter()
                .any(|participant| participant.bot_uuid == human.actor_id),
            CallerContext::Bot(bot) => cmd
                .session_participants
                .iter()
                .any(|participant| participant.bot_uuid == bot.bot_uuid),
            _ => false,
        };
        if !session_member {
            return Err(GroupUseCaseError::Forbidden(
                "caller is not a session participant".to_string(),
            ));
        }
        self.session_calls.lock().await.push(cmd.clone());
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages: vec![GroupMessage {
                id: "hist-1".to_string(),
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
}

struct RecordingStateMachineHistoryRuntime {
    calls: Mutex<Vec<(String, u64, Option<u64>)>>,
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
        .store_token_mapping(
            "intruder-token".to_string(),
            "intruder-bot".to_string(),
        )
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

    let routing = Arc::new(RecordingRouting::default());
    let bot_delivery = Arc::new(RecordingBotDelivery::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let bot_request = Arc::new(RecordingBotRequest::default());
    let message_flow = Arc::new(RecordingMessageFlow::default());
    let group_message_history = Arc::new(RecordingGroupMessageHistory::default());
    let group_use_cases = Arc::new(GroupManagement::with_defaults(
        group_store.clone(),
        registry.clone(),
        Arc::new(NoopFriendCoreService),
    ));
    let services = Services::builder()
        .registry(registry)
        .group(group_store.clone())
        .routing(routing.clone())
        .bot_delivery(bot_delivery.clone())
        .frontend_delivery(frontend_delivery.clone())
        .message_flow(message_flow.clone())
        .system_message(message_flow.clone())
        .session_management(Arc::new(StaticSessionManagement::new(test_session(
                "group-1:abcdef12",
                "group-1",
                vec![
                    Participant::bot("owner-bot", ParticipantRole::Driver),
                    Participant::bot("target-bot", ParticipantRole::Consultant),
                    Participant::human("human_123", ParticipantRole::Observer),
                ],
            ))))
        .group_query(group_use_cases)
        .group_message_history(group_message_history.clone())
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

#[tokio::test]
async fn group_chat_missing_group_preserves_legacy_404_before_auth() {
    let (app, ..) = build_group_app_with_identity(Arc::new(ChainUserIdentityPort::new(
        Arc::new(AuthPluginChain::new(vec![])),
    )))
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/missing-group/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "hello",
                        "from": "owner-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "Group not found: missing-group");
    assert_eq!(json["status"], 404);
}

#[tokio::test]
async fn session_messages_without_identity_returns_unauthorized() {
    let (app, _group_store, _routing, _bot_delivery, _frontend_delivery, _bot_request, _message_flow, group_message_history) =
        build_group_app_with_identity(Arc::new(NoUserIdentity)).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12/messages")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(group_message_history.session_calls.lock().await.is_empty());
}

#[tokio::test]
async fn session_messages_authenticated_human_uses_history_service_without_view_bot() {
    let (app, _group_store, _routing, _bot_delivery, _frontend_delivery, _bot_request, _message_flow, group_message_history) =
        build_group_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12/messages")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let session_calls = group_message_history.session_calls.lock().await;
    assert_eq!(session_calls.len(), 1);
    assert_eq!(session_calls[0].view_bot_id, None);
    assert!(matches!(
        &session_calls[0].caller,
        CallerContext::Human(human)
            if human.actor_id == "human_123" && human.staff_no == "123"
    ));
}

#[tokio::test]
async fn session_messages_non_session_human_returns_forbidden() {
    let (app, _group_store, _routing, _bot_delivery, _frontend_delivery, _bot_request, _message_flow, group_message_history) =
        build_group_app_with_identity(Arc::new(ChainUserIdentityPort::new(static_auth_chain(
            "456", "Intruder",
        ))))
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12/messages")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(group_message_history.session_calls.lock().await.is_empty());
}

#[tokio::test]
async fn session_messages_non_participant_bot_token_returns_forbidden() {
    let (app, _group_store, _routing, _bot_delivery, _frontend_delivery, _bot_request, _message_flow, group_message_history) =
        build_group_app_with_identity(Arc::new(NoUserIdentity)).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12/messages")
                .header("authorization", "Bearer intruder-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(group_message_history.session_calls.lock().await.is_empty());
}

#[tokio::test]
async fn session_messages_uses_history_service() {
    let (app, _group_store, _routing, _bot_delivery, _frontend_delivery, _bot_request, _message_flow, group_message_history) =
        build_group_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12/messages?view_bot_id=owner-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json[0]["id"], "hist-1");
    assert_eq!(json[0]["content"], "history answer");

    // Verify the history service was called with the correct session ID
    let session_calls = group_message_history.session_calls.lock().await;
    assert_eq!(session_calls.len(), 1);
    assert_eq!(session_calls[0].session_id, "group-1:abcdef12");
    assert_eq!(session_calls[0].group_id, "group-1");
    assert_eq!(session_calls[0].view_bot_id, Some("owner-bot".to_string()));
    assert!(matches!(
        &session_calls[0].caller,
        CallerContext::Human(human)
            if human.actor_id == "human_123" && human.staff_no == "123"
    ));
}

#[tokio::test]
async fn session_chat_auto_joins_authenticated_human_and_binds_sender_identity() {
    let chain = static_auth_chain("456", "Alice");
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app_with_identity(Arc::new(ChainUserIdentityPort::new(chain))).await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:abcdef12/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "hello as Alice",
                        "from": "owner-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let group_chats = message_flow.group_chats.lock().await;
    assert_eq!(group_chats.len(), 1);
    assert_eq!(
        group_chats[0].requested_sender_id.as_deref(),
        Some("human_456")
    );
    assert!(matches!(
        &group_chats[0].caller,
        CallerContext::Human(human)
            if human.actor_id == "human_456" && human.staff_no == "456"
    ));
    drop(group_chats);

    let system_notifications = message_flow.system_notifications.lock().await;
    assert_eq!(system_notifications.len(), 1);
    assert!(matches!(
        &system_notifications[0],
        SystemMessageEvent::HumanJoined { group_id, actor }
            if group_id == "group-1"
                && actor.bot_uuid == "human_456"
                && actor.actor_kind == bcs_service_api::ActorKind::Human
    ));
    drop(system_notifications);

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let participant = json["participants"]
        .as_array()
        .unwrap()
        .iter()
        .find(|participant| participant["bot_uuid"] == "human_456")
        .expect("authenticated Human should be added to the session");
    assert_eq!(participant["bot_name"], "Alice");
    assert_eq!(participant["role"], "observer");
    assert_eq!(participant["actor_kind"], "human");
    assert_eq!(participant["mode"], "present");
}

#[tokio::test]
async fn session_chat_does_not_auto_join_authenticated_bot() {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:abcdef12/chat")
                .header("content-type", "application/json")
                .header("authorization", "Bearer intruder-token")
                .body(Body::from(
                    serde_json::json!({
                        "message": "should be rejected",
                        "from": "intruder-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "caller is not a session participant");
    assert!(message_flow.group_chats.lock().await.is_empty());

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/group-1:abcdef12")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert!(json["participants"]
        .as_array()
        .unwrap()
        .iter()
        .all(|participant| participant["bot_uuid"] != "intruder-bot"));
}

async fn post_session_chat_with_flow_error(error: ServiceError) -> (StatusCode, Value) {
    let (
        app,
        _group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;
    *message_flow.next_group_chat_error.lock().await = Some(error);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:abcdef12/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "hello from session"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    (status, json)
}

#[tokio::test]
async fn session_chat_maps_invalid_operation_to_bad_request() {
    let (status, json) = post_session_chat_with_flow_error(ServiceError::InvalidOperation {
        message: "group 'group-1' is not active".to_string(),
        request_id: None,
    })
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        json["error"],
        "Invalid operation on request unknown: group 'group-1' is not active"
    );
}

#[tokio::test]
async fn session_chat_maps_session_not_found_to_not_found() {
    let (status, json) = post_session_chat_with_flow_error(ServiceError::SessionNotFound(
        "group-1:missing".to_string(),
    ))
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(json["error"], "Session 'group-1:missing' not found");
}

#[tokio::test]
async fn session_chat_keeps_internal_error_as_server_error() {
    let (status, json) = post_session_chat_with_flow_error(ServiceError::InternalError(
        "database unavailable".to_string(),
    ))
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(json["error"], "Internal error: database unavailable");
}

#[tokio::test]
async fn state_machine_session_messages_use_runtime_history() {
    let mut group = Group::new(
        "sm-group",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let session = test_session(
        "sm-group:abcdef12",
        "sm-group",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::human("human_123", ParticipantRole::Observer),
        ],
    );
    let group_message_history = Arc::new(RecordingGroupMessageHistory::default());
    let collaboration_runtime = Arc::new(RecordingStateMachineHistoryRuntime {
        calls: Mutex::new(Vec::new()),
        result: SessionHistoryResult {
            session_id: session.id.clone(),
            messages: vec![GroupMessage {
                id: "sm-msg-1".to_string(),
                timestamp: 42,
                sender: "bcs_state_machine".to_string(),
                content: "<AixPanel />".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: Some("BCS State Machine".to_string()),
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
                attachments: None,
            }],
            limit: 20,
            before: None,
            next_before: None,
        },
    });
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.group_message_history = group_message_history.clone();
    services.collaboration_runtime = collaboration_runtime.clone();
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(
            static_auth_chain("123", "Owner"),
        ))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group:abcdef12/messages?limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json[0]["id"], "sm-msg-1");
    assert_eq!(json[0]["sender"], "bcs_state_machine");
    assert_eq!(json[0]["content"], "<AixPanel />");
    assert_eq!(json[0]["role"], "assistant");
    assert_eq!(json[0]["message_type"], "bot");
    assert!(
        group_message_history.session_calls.lock().await.is_empty(),
        "state-machine sessions must not fetch history from a single driver bot"
    );
    let calls = collaboration_runtime.calls.lock().await;
    assert_eq!(calls.as_slice(), &[("sm-group:abcdef12".to_string(), 20, None)]);
}

#[tokio::test]
async fn state_machine_session_messages_non_session_human_returns_forbidden() {
    let mut group = Group::new(
        "sm-group-human-forbidden",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let session = test_session(
        "sm-group-human-forbidden:abcdef12",
        "sm-group-human-forbidden",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    let collaboration_runtime = Arc::new(RecordingStateMachineHistoryRuntime {
        calls: Mutex::new(Vec::new()),
        result: SessionHistoryResult {
            session_id: session.id.clone(),
            messages: vec![],
            limit: 20,
            before: None,
            next_before: None,
        },
    });
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.collaboration_runtime = collaboration_runtime.clone();
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(
            static_auth_chain("456", "Intruder"),
        ))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group-human-forbidden:abcdef12/messages?limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(collaboration_runtime.calls.lock().await.is_empty());
}

#[tokio::test]
async fn state_machine_session_messages_group_only_bot_returns_forbidden() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register(
            "intruder-bot".to_string(),
            BotCapabilities {
                name: Some("Intruder".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping(
            "intruder-token".to_string(),
            "intruder-bot".to_string(),
        )
        .await;

    let mut group = Group::new(
        "sm-group-bot-forbidden",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("intruder-bot", ParticipantRole::Observer),
        ],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let session = test_session(
        "sm-group-bot-forbidden:abcdef12",
        "sm-group-bot-forbidden",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    let collaboration_runtime = Arc::new(RecordingStateMachineHistoryRuntime {
        calls: Mutex::new(Vec::new()),
        result: SessionHistoryResult {
            session_id: session.id.clone(),
            messages: vec![],
            limit: 20,
            before: None,
            next_before: None,
        },
    });
    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.collaboration_runtime = collaboration_runtime.clone();
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(NoUserIdentity)),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group-bot-forbidden:abcdef12/messages?limit=20")
                .header("authorization", "Bearer intruder-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(collaboration_runtime.calls.lock().await.is_empty());
}

#[tokio::test]
async fn state_machine_session_messages_without_identity_returns_unauthorized() {
    let mut group = Group::new(
        "sm-group-no-auth",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.status = GroupStatus::Active;
    let group_store = Arc::new(GroupStore::new());
    group_store.upsert(group).await.unwrap();

    let session = test_session(
        "sm-group-no-auth:abcdef12",
        "sm-group-no-auth",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    let collaboration_runtime = Arc::new(RecordingStateMachineHistoryRuntime {
        calls: Mutex::new(Vec::new()),
        result: SessionHistoryResult {
            session_id: session.id.clone(),
            messages: vec![],
            limit: 20,
            before: None,
            next_before: None,
        },
    });
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = Arc::new(StaticSessionManagement::new(session));
    services.collaboration_runtime = collaboration_runtime.clone();
    let app = build_router(
        HttpAppState::new(services).with_user_identity(Arc::new(NoUserIdentity)),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/sessions/sm-group-no-auth:abcdef12/messages?limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(collaboration_runtime.calls.lock().await.is_empty());
}

#[tokio::test]
async fn persistent_send_missing_group_preserves_legacy_404_before_auth() {
    let (app, ..) = build_group_app_with_identity(Arc::new(NoUserIdentity)).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/missing-group/messages")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "sender": "owner-bot",
                        "content": "hello"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "Group not found: missing-group");
    assert_eq!(json["status"], 404);
}

#[tokio::test]
async fn group_message_routes_preserve_delivery_and_history_shapes() {
    let (
        app,
        group_store,
        routing,
        bot_delivery,
        frontend_delivery,
        bot_request,
        message_flow,
        group_message_history,
    ) = build_group_app().await;

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/messages")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "sender": "owner-bot",
                        "content": "@Target hello",
                        "role": "user"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["message_id"], "flow-message-1");
    assert_eq!(json["routed_to"], serde_json::json!(["target-bot"]));
    assert_eq!(json["mentions"], serde_json::json!(["target-bot"]));

    let persistent_sends = message_flow.persistent_sends.lock().await;
    assert_eq!(persistent_sends.len(), 1);
    assert_eq!(persistent_sends[0].group_id, "group-1");
    assert_eq!(persistent_sends[0].sender, "owner-bot");
    assert_eq!(persistent_sends[0].content, "@Target hello");
    assert!(persistent_sends[0].store_messages);
    assert_eq!(persistent_sends[0].max_group_messages, 10);
    drop(persistent_sends);
    assert!(routing.sent_to_bot.lock().await.is_empty());
    let group = group_store.get("group-1").await.unwrap();
    assert_eq!(group.messages.len(), 1);

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/groups/group-1/messages?view_bot_id=owner-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json[0]["id"], "hist-use-case");
    assert_eq!(json[0]["sender"], "owner-bot");
    assert_eq!(json[0]["content"], "history answer");
    assert_eq!(json[0]["role"], "assistant");
    let history_calls = group_message_history.calls.lock().await;
    assert_eq!(history_calls.len(), 1);
    assert_eq!(history_calls[0].group_id, "group-1");
    assert_eq!(history_calls[0].view_bot_id.as_deref(), Some("owner-bot"));
    assert_eq!(history_calls[0].limit, u64::MAX);
    assert_eq!(history_calls[0].before, None);
    assert!(matches!(
        &history_calls[0].caller,
        CallerContext::Human(human)
            if human.actor_id == "human_123" && human.staff_no == "123"
    ));
    drop(history_calls);
    let history_requests = bot_request.requests.lock().await;
    assert!(history_requests.is_empty());

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "@Target direct hello",
                        "from": "owner-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["delivered"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["driver_bot"], "owner-bot");
    assert_eq!(json["delivered_count"], 2);
    assert_eq!(json["failed_count"], 0);
    assert_eq!(json["mentions"], serde_json::json!(["target-bot"]));
    assert_eq!(json["delivery_results"][1]["bot_uuid"], "target-bot");

    let group_chats = message_flow.group_chats.lock().await;
    assert_eq!(group_chats.len(), 1);
    assert_eq!(group_chats[0].group_id, "group-1");
    assert_eq!(
        group_chats[0].requested_sender_id.as_deref(),
        Some("owner-bot")
    );
    assert_eq!(group_chats[0].message, "@Target direct hello");
    drop(group_chats);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/callback")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "system callback",
                        "mentions": ["target-bot"],
                        "metadata": {"source": "test"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["delivered"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["driver_bot"], "owner-bot");
    assert_eq!(json["delivered_count"], 1);
    assert_eq!(json["failed_count"], 0);
    assert_eq!(json["mentions"], serde_json::json!(["target-bot"]));

    let frames = bot_delivery.frames.lock().await;
    assert!(frames.is_empty());
    let frontend_events = frontend_delivery.events.lock().await;
    assert!(frontend_events.is_empty());
    let callbacks = message_flow.callbacks.lock().await;
    assert_eq!(callbacks.len(), 1);
    assert_eq!(callbacks[0].group_id, "group-1");
    assert_eq!(callbacks[0].message, "system callback");
    assert_eq!(callbacks[0].mentions, vec!["target-bot".to_string()]);
    assert_eq!(
        callbacks[0].metadata,
        Some(serde_json::json!({"source": "test"}))
    );
}

#[tokio::test]
async fn inactive_group_chat_is_delegated_to_message_flow() {
    let (
        app,
        group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;
    let mut group = group_store.get("group-1").await.unwrap();
    group.status = GroupStatus::Inactive;
    group_store.upsert(group).await.unwrap();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/chat")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "wake up",
                        "from": "owner-bot"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["delivered"], true);
    assert_eq!(json["group_id"], "group-1");
    assert_eq!(json["driver_bot"], "owner-bot");

    let group_chats = message_flow.group_chats.lock().await;
    assert_eq!(group_chats.len(), 1);
    assert_eq!(group_chats[0].group_id, "group-1");
    assert_eq!(
        group_chats[0].requested_sender_id.as_deref(),
        Some("owner-bot")
    );
    assert_eq!(group_chats[0].message, "wake up");
}

#[tokio::test]
async fn persistent_send_delegates_inactive_and_cap_paths_to_message_flow_with_legacy_errors() {
    let (
        app,
        group_store,
        _routing,
        _bot_delivery,
        _frontend_delivery,
        _bot_request,
        message_flow,
        _group_message_history,
    ) = build_group_app().await;

    let mut group = group_store.get("group-1").await.unwrap();
    group.status = GroupStatus::Inactive;
    group_store.upsert(group).await.unwrap();
    *message_flow.next_persistent_error.lock().await = Some(ServiceError::InvalidOperation {
        message: "Group 'group-1' is not active (status: Inactive)".to_string(),
        request_id: None,
    });

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/messages")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "sender": "owner-bot",
                        "content": "inactive send"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json["error"],
        "Group 'group-1' is not active (status: Inactive)"
    );
    assert_eq!(json["status"], 400);

    let mut group = group_store.get("group-1").await.unwrap();
    group.status = GroupStatus::Active;
    group_store.upsert(group).await.unwrap();
    for _ in 0..10 {
        group_store
            .increment_message_count("group-1")
            .await
            .unwrap();
    }
    *message_flow.next_persistent_error.lock().await = Some(ServiceError::MessageLimitReached(
        "Group 'group-1' already has 10 messages (max 10)".to_string(),
    ));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/messages")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "sender": "owner-bot",
                        "content": "cap send"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json["error"],
        "Group 'group-1' already has 10 messages (max 10)"
    );
    assert_eq!(json["status"], 400);

    let persistent_sends = message_flow.persistent_sends.lock().await;
    assert_eq!(persistent_sends.len(), 2);
    assert_eq!(persistent_sends[0].content, "inactive send");
    assert_eq!(persistent_sends[1].content, "cap send");
}
