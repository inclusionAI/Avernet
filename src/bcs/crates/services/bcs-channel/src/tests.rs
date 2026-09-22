use super::{GROUP_CHAT_NEW_SESSION_CONFIG, GROUP_CONTEXT_DELIVERY_CONFIG};
use std::collections::HashMap;
use std::future::Future;
use std::io::{self, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Once, OnceLock};

use async_trait::async_trait;
use tokio::sync::{Mutex, Semaphore};
use tokio::time::{Duration, timeout};

use bcs_channel_api::{
    ChannelInboundSink, ChannelProvider, ChannelProviderError, ChannelProviderRegistry,
    ChannelProviderResult,
};
use bcs_channel_store::{
    MemoryChannelBindingRepo, MemoryConversationSessionRepo, MemoryHumanInputRequestRepo,
    MemoryImParticipantRepo,
};
use bcs_domain::{
    ActorKind, BindingStatus, BindingTarget, BotCapabilities, ChannelBinding,
    ChannelConfig, ChannelType, Group, GroupChatScope, GroupKind, HumanInputNotificationMode,
    HumanInputRequestStatus, Participant, ParticipantMode, ParticipantRole, RegisteredBot,
    Session, SessionKind, SessionScope, SessionStatus, Skill, StateMachineNodeRun,
    StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus, SystemMessageEvent,
    Visibility,
};
use bcs_service_api::application::channel::{
    ChannelInboundError, ChannelInboundFailureKind, ChannelService, ChannelUseCaseError,
    CreateBindingCommand, InboundMessage, OutboundMessage,
};
use bcs_service_api::application::collaboration_runtime::{
    CancelStateMachineRunCommand, CollaborationRuntimeError, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    HumanResponseSource, ListPendingHumanNodesCommand, PendingHumanNodeView,
    RespondHumanNodeCommand, RespondHumanNodeOutcome, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineRunView,
};
use bcs_service_api::application::group_message::SessionHistoryResult;
use bcs_service_api::application::message_flow::{
    BotEventCommand, BotEventOutcome, CancelLatestQueuedMessageCommand,
    CancelLatestQueuedMessageOutcome, ChatAbortCommand, ChatAbortOutcome, GroupCallbackCommand,
    GroupCallbackOutcome, GroupChatCommand, GroupChatOutcome, MessageDeliveryResult,
    MessageFlowService, PersistentGroupSendCommand, PersistentGroupSendOutcome,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome,
};
use bcs_service_api::core::{
    AgentCredentials, BotDeliveryTarget, BotRegistryCoreService, EnsureHumanResult,
    GroupCoreService, ServiceError, ServiceResult,
};
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::port::channel_delivery::{
    ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent,
    ChannelOutboundEventKind, ChannelRenderHint,
};
use bcs_service_api::port::repo::{
    ChannelBindingRepoPort, ConversationSessionRepoPort, HumanInputRequestRepoPort,
    ImParticipantRepoPort, NewSessionParams, SessionRepoPort,
};
use bcs_service_api::{
    ChannelBindingCleanupPort, ChannelOutboundPurpose, CollaborationRuntimeService,
    SystemMessageService,
};
use bcs_service_api::{
    HumanInputReadyEvent, SessionChannelDeliveryOutcome, SessionChannelOutboundPort,
};

use crate::{
    BcsChannelService, FORWARD_SENDER_IDENTITY_CONFIG, ResolvedInboundContext, channel_meta,
    channel_owned_group_id,
};

type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

struct PanicOnListBindingRepo {
    inner: Arc<MemoryChannelBindingRepo>,
}

impl PanicOnListBindingRepo {
    fn new(inner: Arc<MemoryChannelBindingRepo>) -> Self {
        Self { inner }
    }
}

#[async_trait]
impl ChannelBindingRepoPort for PanicOnListBindingRepo {
    async fn create(&self, binding: ChannelBinding) -> ServiceResult<()> {
        self.inner.create(binding).await
    }

    async fn get(&self, id: &str) -> ServiceResult<Option<ChannelBinding>> {
        self.inner.get(id).await
    }

    async fn find_active_by_account(
        &self,
        channel_type: ChannelType,
        account_ref: &str,
    ) -> ServiceResult<Option<ChannelBinding>> {
        self.inner
            .find_active_by_account(channel_type, account_ref)
            .await
    }

    async fn list(&self) -> ServiceResult<Vec<ChannelBinding>> {
        panic!("outbound delivery must not scan all channel bindings")
    }

    async fn list_by_target(
        &self,
        target: &BindingTarget,
        channel_type: Option<&str>,
    ) -> ServiceResult<Vec<ChannelBinding>> {
        self.inner.list_by_target(target, channel_type).await
    }

    async fn delete_by_target(&self, target: &BindingTarget) -> ServiceResult<u64> {
        self.inner.delete_by_target(target).await
    }

    async fn set_status(&self, id: &str, active: bool) -> ServiceResult<()> {
        self.inner.set_status(id, active).await
    }

    async fn set_config(&self, id: &str, config: serde_json::Value) -> ServiceResult<()> {
        self.inner.set_config(id, config).await
    }

    async fn delete(&self, id: &str) -> ServiceResult<()> {
        self.inner.delete(id).await
    }
}

struct FailingBindingLookupRepo;

#[async_trait]
impl ChannelBindingRepoPort for FailingBindingLookupRepo {
    async fn create(&self, _binding: ChannelBinding) -> ServiceResult<()> {
        unreachable!("inbound binding lookup test only calls find_active_by_account")
    }

    async fn get(&self, _id: &str) -> ServiceResult<Option<ChannelBinding>> {
        unreachable!("inbound binding lookup test only calls find_active_by_account")
    }

    async fn find_active_by_account(
        &self,
        _channel_type: ChannelType,
        _account_ref: &str,
    ) -> ServiceResult<Option<ChannelBinding>> {
        Err(ServiceError::InternalError(
            "binding lookup failed".to_string(),
        ))
    }

    async fn list(&self) -> ServiceResult<Vec<ChannelBinding>> {
        unreachable!("inbound binding lookup test only calls find_active_by_account")
    }

    async fn list_by_target(
        &self,
        _target: &BindingTarget,
        _channel_type: Option<&str>,
    ) -> ServiceResult<Vec<ChannelBinding>> {
        unreachable!("inbound binding lookup test only calls find_active_by_account")
    }

    async fn delete_by_target(&self, _target: &BindingTarget) -> ServiceResult<u64> {
        unreachable!("inbound binding lookup test only calls find_active_by_account")
    }

    async fn set_status(&self, _id: &str, _active: bool) -> ServiceResult<()> {
        unreachable!("inbound binding lookup test only calls find_active_by_account")
    }

    async fn set_config(&self, _id: &str, _config: serde_json::Value) -> ServiceResult<()> {
        unreachable!("inbound binding lookup test only calls find_active_by_account")
    }

    async fn delete(&self, _id: &str) -> ServiceResult<()> {
        unreachable!("inbound binding lookup test only calls find_active_by_account")
    }
}

struct FailingParticipantRepo;

#[async_trait]
impl ImParticipantRepoPort for FailingParticipantRepo {
    async fn get(
        &self,
        _channel_type: ChannelType,
        _account_ref: &str,
        _im_user_id: &str,
    ) -> ServiceResult<Option<bcs_domain::ImParticipantMap>> {
        unreachable!("inbound actor test only writes the participant mapping")
    }

    async fn upsert(&self, _map: bcs_domain::ImParticipantMap) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "actor write failed".to_string(),
        ))
    }
}

#[derive(Clone, Default)]
struct SharedLogBuffer(Arc<std::sync::Mutex<Vec<u8>>>);

impl SharedLogBuffer {
    fn clear(&self) {
        self.0.lock().unwrap().clear();
    }

    fn contents(&self) -> String {
        String::from_utf8(self.0.lock().unwrap().clone()).unwrap()
    }
}

struct SharedLogWriter {
    buffer: Arc<std::sync::Mutex<Vec<u8>>>,
}

impl Write for SharedLogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.buffer.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
    type Writer = SharedLogWriter;

    fn make_writer(&'a self) -> Self::Writer {
        SharedLogWriter {
            buffer: self.0.clone(),
        }
    }
}

fn tracing_capture_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

fn tracing_log_buffer() -> &'static SharedLogBuffer {
    static BUFFER: OnceLock<SharedLogBuffer> = OnceLock::new();
    BUFFER.get_or_init(SharedLogBuffer::default)
}

fn ensure_tracing_subscriber() {
    static INIT: Once = Once::new();
    INIT.call_once(|| {
        let subscriber = tracing_subscriber::fmt()
            .with_ansi(false)
            .with_level(false)
            .with_target(true)
            .with_writer(tracing_log_buffer().clone())
            .finish();
        let _ = tracing::subscriber::set_global_default(subscriber);
    });
}

async fn capture_tracing_logs<Fut, T>(future: Fut) -> (T, String)
where
    Fut: Future<Output = T>,
{
    let _capture_guard = tracing_capture_lock().lock().await;
    ensure_tracing_subscriber();
    let buffer = tracing_log_buffer();
    buffer.clear();
    let output = future.await;
    let logs = buffer.contents();
    (output, logs)
}

#[derive(Clone)]
struct RecordedSystemMessageNotification {
    group_id: String,
    event: SystemMessageEvent,
    session_id: String,
    participants: Vec<Participant>,
}

#[derive(Default)]
struct RecordingSystemMessage {
    notifications: Mutex<Vec<RecordedSystemMessageNotification>>,
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessage {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        session_participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.notifications
            .lock()
            .await
            .push(RecordedSystemMessageNotification {
                group_id: group_id.to_string(),
                event,
                session_id: session_id.to_string(),
                participants: session_participants.to_vec(),
            });
        Ok(session_participants.len())
    }
}

struct TestHarness {
    service: BcsChannelService,
    binding_repo: Arc<MemoryChannelBindingRepo>,
    conversation_repo: Arc<MemoryConversationSessionRepo>,
    participant_repo: Arc<MemoryImParticipantRepo>,
    human_input_requests: Arc<MemoryHumanInputRequestRepo>,
    session_repo: Arc<RecordingSessionRepo>,
    registry: Arc<RecordingRegistry>,
    message_flow: Arc<RecordingMessageFlow>,
    system_message: Arc<RecordingSystemMessage>,
    provider: Arc<RecordingProvider>,
    delivery: Arc<RecordingDelivery>,
    collaboration_runtime: Arc<RecordingCollaborationRuntime>,
}

impl TestHarness {
    async fn new(group: Group) -> ServiceResult<Self> {
        Self::new_with_env(group, "pre").await
    }

    async fn new_with_env(group: Group, env: &str) -> ServiceResult<Self> {
        Self::new_with_env_and_id(group, env, Arc::new(|| "generated_id".to_string())).await
    }

    async fn new_with_generated_id(group: Group, generated_id: String) -> ServiceResult<Self> {
        Self::new_with_env_and_id(group, "pre", Arc::new(move || generated_id.clone())).await
    }

    async fn new_with_env_and_id(
        group: Group,
        env: &str,
        new_id: Arc<dyn Fn() -> String + Send + Sync>,
    ) -> ServiceResult<Self> {
        Self::new_with_env_id_clock(group, env, new_id, Arc::new(AtomicU64::new(42))).await
    }

    async fn new_with_clock(group: Group, clock: Arc<AtomicU64>) -> ServiceResult<Self> {
        Self::new_with_env_id_clock(
            group,
            "pre",
            Arc::new(|| "generated_id".to_string()),
            clock,
        )
        .await
    }

    async fn new_with_env_id_clock(
        group: Group,
        env: &str,
        new_id: Arc<dyn Fn() -> String + Send + Sync>,
        clock: Arc<AtomicU64>,
    ) -> ServiceResult<Self> {
        let binding_repo = Arc::new(MemoryChannelBindingRepo::new(env));
        let conversation_repo = Arc::new(MemoryConversationSessionRepo::new());
        let participant_repo = Arc::new(MemoryImParticipantRepo::new());
        let human_input_requests = Arc::new(MemoryHumanInputRequestRepo::new());
        let session_repo = Arc::new(RecordingSessionRepo::default());
        let groups = Arc::new(bcs_group::GroupCore::memory());
        groups.upsert(group).await?;
        let registry = Arc::new(RecordingRegistry::default());
        let message_flow = Arc::new(RecordingMessageFlow::default());
        let system_message = Arc::new(RecordingSystemMessage::default());
        let delivery = Arc::new(RecordingDelivery::default());
        let provider = Arc::new(RecordingProvider::new(delivery.clone()));
        let providers = Arc::new(
            ChannelProviderRegistry::new(vec![provider.clone()])
                .expect("test provider registry"),
        );
        let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::default());
        let service = BcsChannelService::new(
            binding_repo.clone(),
            conversation_repo.clone(),
            participant_repo.clone(),
            human_input_requests.clone(),
            session_repo.clone(),
            message_flow.clone(),
            system_message.clone(),
            collaboration_runtime.clone(),
            groups,
            registry.clone(),
            providers,
            env,
            Arc::new(move || clock.load(Ordering::SeqCst)),
            new_id,
        );

        Ok(Self {
            service,
            binding_repo,
            conversation_repo,
            participant_repo,
            human_input_requests,
            session_repo,
            registry,
            message_flow,
            system_message,
            provider,
            delivery,
            collaboration_runtime,
        })
    }

    async fn new_without_binding_list(group: Group) -> ServiceResult<Self> {
        let binding_repo = Arc::new(MemoryChannelBindingRepo::new("pre"));
        let conversation_repo = Arc::new(MemoryConversationSessionRepo::new());
        let participant_repo = Arc::new(MemoryImParticipantRepo::new());
        let human_input_requests = Arc::new(MemoryHumanInputRequestRepo::new());
        let session_repo = Arc::new(RecordingSessionRepo::default());
        let groups = Arc::new(bcs_group::GroupCore::memory());
        groups.upsert(group).await?;
        let registry = Arc::new(RecordingRegistry::default());
        let message_flow = Arc::new(RecordingMessageFlow::default());
        let system_message = Arc::new(RecordingSystemMessage::default());
        let delivery = Arc::new(RecordingDelivery::default());
        let provider = Arc::new(RecordingProvider::new(delivery.clone()));
        let providers = Arc::new(
            ChannelProviderRegistry::new(vec![provider.clone()])
                .expect("test provider registry"),
        );
        let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::default());
        let service = BcsChannelService::new(
            Arc::new(PanicOnListBindingRepo::new(binding_repo.clone())),
            conversation_repo.clone(),
            participant_repo.clone(),
            human_input_requests.clone(),
            session_repo.clone(),
            message_flow.clone(),
            system_message.clone(),
            collaboration_runtime.clone(),
            groups,
            registry.clone(),
            providers,
            "pre",
            Arc::new(|| 42),
            Arc::new(|| "generated_id".to_string()),
        );

        Ok(Self {
            service,
            binding_repo,
            conversation_repo,
            participant_repo,
            human_input_requests,
            session_repo,
            registry,
            message_flow,
            system_message,
            provider,
            delivery,
            collaboration_runtime,
        })
    }
}

async fn inbound_service(
    bindings: Arc<dyn ChannelBindingRepoPort>,
    im_participants: Arc<dyn ImParticipantRepoPort>,
    sessions: Arc<dyn SessionRepoPort>,
    message_flow: Arc<dyn MessageFlowService>,
    registry: Arc<dyn BotRegistryCoreService>,
) -> BcsChannelService {
    let groups = Arc::new(bcs_group::GroupCore::memory());
    groups
        .upsert(manager_group("group_1"))
        .await
        .expect("inbound test group");
    BcsChannelService::new(
        bindings,
        Arc::new(MemoryConversationSessionRepo::new()),
        im_participants,
        Arc::new(MemoryHumanInputRequestRepo::new()),
        sessions,
        message_flow,
        Arc::new(RecordingSystemMessage::default()),
        Arc::new(RecordingCollaborationRuntime::default()),
        groups,
        registry,
        Arc::new(ChannelProviderRegistry::empty()),
        "pre",
        Arc::new(|| 42),
        Arc::new(|| "generated_id".to_string()),
    )
}

async fn active_inbound_binding_repo() -> Arc<MemoryChannelBindingRepo> {
    let bindings = Arc::new(MemoryChannelBindingRepo::new("pre"));
    bindings
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await
        .expect("active inbound binding");
    bindings
}

fn assert_inbound_error(
    error: ChannelInboundError,
    kind: ChannelInboundFailureKind,
    retryable: bool,
    diagnostic: &str,
) {
    assert_eq!(error.kind, kind);
    assert_eq!(error.retryable, retryable);
    assert!(error.diagnostic_for_logging().contains(diagnostic));
}

fn manager_group(id: &str) -> Group {
    let mut group = Group::new(
        id,
        "manager_bot",
        vec![
            Participant::bot("manager_bot", ParticipantRole::Manager),
            Participant::bot("worker_bot", ParticipantRole::Worker),
        ],
    );
    group.group_strategy = bcs_domain::GroupStrategy::ManagerWorker;
    group
}

fn chat_group(id: &str) -> Group {
    Group::new(
        id,
        "driver_bot",
        vec![
            Participant::bot("driver_bot", ParticipantRole::Driver),
            Participant::bot("consultant_bot", ParticipantRole::Consultant),
        ],
    )
}

fn state_machine_group(id: &str) -> Group {
    let mut group = manager_group(id);
    group.group_strategy = bcs_domain::GroupStrategy::StateMachine;
    group
}

async fn start_state_machine_channel(harness: &TestHarness) -> TestResult {
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_sm".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    harness
        .service
        .handle_inbound(group_inbound(
            "conv_sm",
            "u1",
            Some("张三"),
            "msg_start",
            true,
        ))
        .await?;
    Ok(())
}

fn pending_human_node(node_id: &str) -> PendingHumanNodeView {
    PendingHumanNodeView {
        node_id: node_id.to_string(),
        display_name: node_id.to_string(),
        instruction: "Review the draft".to_string(),
        response_ref: format!("state_run_1:{node_id}"),
        judge_outcomes: Vec::new(),
        timeout_deadline_ms: None,
        loop_context: None,
        upstream_artifacts: Vec::new(),
    }
}

fn human_input_ready_event(
    event_id: &str,
    notification_mode: HumanInputNotificationMode,
) -> HumanInputReadyEvent {
    HumanInputReadyEvent {
        event_id: event_id.to_string(),
        group_id: "group_sm".to_string(),
        session_id: format!("session-{event_id}"),
        run_id: format!("run-{event_id}"),
        node_id: "human_review".to_string(),
        display_name: "Human review".to_string(),
        instruction: "请审核上游结果".to_string(),
        assignee_actor_id: "human_u1".to_string(),
        channel_type: channel_type(),
        notification_mode,
        fixed_group_conversation_id: Some("conv_sm".to_string()),
        response_ref: format!("run-{event_id}:human_review"),
        upstream_artifacts: vec![bcs_service_api::JudgeArtifact {
            node_id: "draft".to_string(),
            text: "draft content".to_string(),
        }],
        judge_outcomes: vec!["approve".to_string(), "reject".to_string()],
        timeout_deadline_ms: Some(1_000),
        loop_context: None,
    }
}

fn dingtalk_config(account_ref: &str) -> ChannelConfig {
    serde_json::json!({
        "robot_code": account_ref,
        "client_id": "client_id",
        "client_secret": "secret",
        "valid": true,
        "send_mode": {
            "mode": "normal",
            "message_type": "markdown"
        }
    })
}

fn invalid_provider_config(account_ref: &str) -> ChannelConfig {
    let mut config = dingtalk_config(account_ref);
    config["valid"] = serde_json::json!(false);
    config
}

fn channel_type() -> ChannelType {
    "dingtalk".to_string()
}

fn active_binding(
    id: &str,
    account_ref: &str,
    target: BindingTarget,
    visibility: Visibility,
) -> ChannelBinding {
    ChannelBinding {
        id: id.to_string(),
        channel_type: channel_type(),
        account_ref: account_ref.to_string(),
        target,
        group_chat_scope: Some(GroupChatScope::ConversationShared),
        outbound_visibility: visibility,
        env: "pre".to_string(),
        status: BindingStatus::Active,
        created_by: Some("creator".to_string()),
        config: dingtalk_config(account_ref),
    }
}

fn inbound(
    conversation_id: &str,
    user_id: &str,
    user_nick: Option<&str>,
    msg_id: &str,
) -> InboundMessage {
    InboundMessage {
        channel_type: channel_type(),
        account_ref: "robot_1".to_string(),
        im_conversation_id: conversation_id.to_string(),
        conversation_type: "1".to_string(),
        im_user_id: user_id.to_string(),
        im_user_nick: user_nick.map(str::to_string),
        text: "hello".to_string(),
        attachments: None,
        is_at_bot: true,
        msg_id: msg_id.to_string(),
    }
}

fn group_inbound(
    conversation_id: &str,
    user_id: &str,
    user_nick: Option<&str>,
    msg_id: &str,
    is_at_bot: bool,
) -> InboundMessage {
    InboundMessage {
        channel_type: channel_type(),
        account_ref: "robot_1".to_string(),
        im_conversation_id: conversation_id.to_string(),
        conversation_type: "2".to_string(),
        im_user_id: user_id.to_string(),
        im_user_nick: user_nick.map(str::to_string),
        text: "hello".to_string(),
        attachments: None,
        is_at_bot,
        msg_id: msg_id.to_string(),
    }
}

fn outbound(
    session_id: &str,
    sender_role: ParticipantRole,
    source_is_channel: bool,
) -> OutboundMessage {
    OutboundMessage {
        group_id: "group_1".to_string(),
        bcs_session_id: session_id.to_string(),
        run_id: "run_1".to_string(),
        sender_actor_id: "worker_bot".to_string(),
        sender_role,
        sender_label: "Worker".to_string(),
        kind: ChannelOutboundEventKind::ChatFinal,
        purpose: ChannelOutboundPurpose::Conversation,
        text: Some("done".to_string()),
        raw_payload: serde_json::json!({"type": "chat.final"}),
        render_hint: ChannelRenderHint::Render,
        source_im_message_id: None,
        source_is_channel,
    }
}

fn registered_bot(bot_id: &str) -> RegisteredBot {
    RegisteredBot {
        bot_uuid: bot_id.to_string(),
        capabilities: BotCapabilities {
            name: Some(bot_id.to_string()),
            summary: None,
            domains: Vec::new(),
            skills: Vec::<Skill>::new(),
            scopes: Vec::new(),
            binding_channels: None,
            hidden: false,
            visibility: "protected".to_string(),
            agent_code: None,
            agent_token: None,
        },
        env: Some("dev".to_string()),
        created_by: None,
        actor_kind: ActorKind::Bot,
        status: bcs_domain::ActorStatus::Online,
    }
}

fn not_configured(name: &str) -> ServiceError {
    ServiceError::InvalidOperation {
        message: format!("{name} is not configured"),
        request_id: None,
    }
}

fn new_command(conversation_id: &str, user_id: &str, msg_id: &str) -> InboundMessage {
    let mut msg = inbound(conversation_id, user_id, Some("张三"), msg_id);
    msg.text = "/new".to_string();
    msg
}

fn group_new_command(conversation_id: &str, user_id: &str, msg_id: &str) -> InboundMessage {
    let mut msg = group_inbound(conversation_id, user_id, Some("张三"), msg_id, true);
    msg.text = "/new".to_string();
    msg
}

fn group_command(
    conversation_id: &str,
    user_id: &str,
    msg_id: &str,
    text: &str,
) -> InboundMessage {
    let mut msg = group_inbound(conversation_id, user_id, Some("张三"), msg_id, true);
    msg.text = text.to_string();
    msg
}

async fn create_group_binding(
    harness: &TestHarness,
    group_id: &str,
    scope: GroupChatScope,
) -> TestResult {
    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: group_id.to_string(),
            },
            group_chat_scope: Some(scope),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;
    Ok(())
}

async fn delivered_texts(harness: &TestHarness) -> Vec<String> {
    harness
        .delivery
        .events
        .lock()
        .await
        .iter()
        .filter_map(|event| event.text.clone())
        .collect()
}


mod bindings;
mod conversations;
mod inbound;
mod sessions;
mod state_machine;
mod outbound;
mod commands;
mod delivery_fixtures;
mod runtime_fixtures;
use delivery_fixtures::*;
use runtime_fixtures::*;

mod human_input_queue;
