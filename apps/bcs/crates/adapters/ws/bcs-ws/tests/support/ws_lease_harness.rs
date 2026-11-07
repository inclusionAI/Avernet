//! Harness for the WS lease-command contract: minimal recording services
//! (the dispatcher's lease arm touches none of them) plus the real
//! dispatcher and a real memory lease store.
use std::sync::Arc;

use async_trait::async_trait;
use bcs_protocol::{BcsFrame, RequestFrame, ResponseFrame};
use bcs_service_api::{
    BotEventCommand, BotEventOutcome, ChatAbortCommand, ChatAbortOutcome, ChatAbortScope,
    GroupCallbackCommand, GroupCallbackOutcome,
    MessageFlowService, ServiceResult, TaskCompleteCommand, TaskCompleteOutcome,
    TaskDispatchCommand, TaskDispatchOutcome, TaskRunAliasRegistration, WebSendCommand,
    WebSendOutcome, WorkbenchConnectCommand, WorkbenchConnectOutcome,
    WorkbenchSessionService, WorkbenchUseCaseError,
};
use bcs_service_api::InteractionService;
use bcs_session_store::MemorySpeakingLeaseRepo;
use bcs_ws::shared::RunChannelManager;
use bcs_service_api::application::v1::DeliveryAdmissionService;
use bcs_ws::web::{
    WebDispatchState, WorkbenchConnectionAuth,
    WebDispatchOutcome, WorkbenchConnectionRegistry, dispatch_client_frame,
};
use bcs_service_api::application::interaction::{
    ResolveInteractionCommand, ResolveInteractionResult, InteractionRequestedOutcome,
    ProviderInteractionRequestedCommand, ProviderInteractionResolvedCommand,
};
use bcs_service_api::InteractionFrontendEvent;
use tokio::sync::mpsc;

/// Minimal allow-all delivery admission: the lease contract never consults
/// output admission; the field only exists to satisfy the dispatcher state.
/// Minimal allow-all delivery admission: the lease contract never consults
/// output admission; the field only exists to satisfy the dispatcher state.
struct AllowAllAdmission;

#[async_trait]
impl bcs_service_api::application::v1::DeliveryAdmissionService for AllowAllAdmission {
    async fn resolve_recipient_views(
        &self,
        _scope: &bcs_service_api::application::v1::DeliveryScope,
        candidates: &[bcs_service_api::application::v1::ProtectedDeliveryBinding],
    ) -> Result<
        Vec<bcs_service_api::application::v1::AdmittedRecipient>,
        bcs_service_api::application::v1::ApplicationError,
    > {
        Ok(candidates
            .iter()
            .map(|candidate| bcs_service_api::application::v1::AdmittedRecipient {
                connection_instance_id: candidate.connection_instance_id,
                view_actor_id: candidate.view_actor_id.clone(),
                scope: bcs_domain::MessageViewScope::Full,
                allow_legacy_unclassified_chat: true,
            })
            .collect())
    }
}

pub struct Response {
    pub ok: bool,
    pub error_code: String,
    pub payload: serde_json::Value,
}

impl std::fmt::Debug for Response {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Response")
            .field("ok", &self.ok)
            .field("error_code", &self.error_code)
            .field("payload", &self.payload)
            .finish()
    }
}

struct NopMessageFlow;

#[async_trait]
impl MessageFlowService for NopMessageFlow {
    async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        Err(bcs_service_api::ServiceError::InternalError("not used".into()))
    }
    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        Err(bcs_service_api::ServiceError::InternalError("not used".into()))
    }
    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        Err(bcs_service_api::ServiceError::InternalError("not used".into()))
    }
    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        Err(bcs_service_api::ServiceError::InternalError("not used".into()))
    }
    async fn resolve_chat_abort_scope(
        &self,
        _group_id: &str,
        _run_id: &str,
    ) -> ServiceResult<Option<ChatAbortScope>> {
        Ok(None)
    }
    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        Ok(TaskRunAliasRegistration::NotTask)
    }
    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        Err(bcs_service_api::ServiceError::InternalError("not used".into()))
    }
    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        Err(bcs_service_api::ServiceError::InternalError("not used".into()))
    }
}

struct NopWorkbenchSessions { access: Arc<std::sync::atomic::AtomicBool> }

#[async_trait]
impl WorkbenchSessionService for NopWorkbenchSessions {
    async fn connect(
        &self,
        _command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        Ok(WorkbenchConnectOutcome {
            group_id: _command.group_id,
            participants: Vec::new(),
            speaking_mode: bcs_domain::speaking_lease::SessionSpeakingMode::Plain,
        })
    }

    async fn authorize_chat_send(
        &self,
        _command: bcs_service_api::WorkbenchChatAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        if self.access.load(std::sync::atomic::Ordering::SeqCst)
            && _command.group_id == "group-1"
            && matches!(_command.from_actor_id.as_str(), "human_alice" | "human_bob")
            && _command.bound_actor_id.as_deref() == Some(_command.from_actor_id.as_str()) {
            Ok(())
        } else {
            Err(WorkbenchUseCaseError::SenderNotInGroup)
        }
    }
}

#[allow(unused_variables)]
struct NopInteraction;

#[async_trait]
impl InteractionService for NopInteraction {
    async fn on_provider_requested(
        &self,
        _command: ProviderInteractionRequestedCommand,
    ) -> ServiceResult<InteractionRequestedOutcome> {
        Ok(InteractionRequestedOutcome::Duplicate)
    }
    async fn on_provider_resolved(
        &self,
        _command: ProviderInteractionResolvedCommand,
    ) -> ServiceResult<()> {
        Ok(())
    }
    async fn resolve(
        &self,
        _command: ResolveInteractionCommand,
    ) -> Result<ResolveInteractionResult, bcs_service_api::InteractionServiceError> {
        use bcs_service_api::InteractionStatus;
        Ok(ResolveInteractionResult {
            accepted: true,
            interaction_id: String::new(),
            status: InteractionStatus::Accepted,
            idempotency_key: String::new(),
        })
    }
    async fn list_pending(
        &self,
        _bcs_session_id: &str,
    ) -> ServiceResult<Vec<InteractionFrontendEvent>> {
        Ok(Vec::new())
    }
    async fn invalidate_run(
        &self,
        _bcs_run_id: &str,
        _reason: &str,
        _invalidated_at_ms: u64,
    ) -> ServiceResult<usize> {
        Ok(0)
    }
    async fn cleanup_terminal(&self, _terminal_before_ms: u64) -> ServiceResult<usize> {
        Ok(0)
    }
}

fn nop_state(
    leases: Option<Arc<MemorySpeakingLeaseRepo>>,
    access: Arc<std::sync::atomic::AtomicBool>,
) -> Arc<WebDispatchState> {
    nop_state_with_idle(leases, access, 60_000)
}

fn nop_state_with_idle(
    leases: Option<Arc<MemorySpeakingLeaseRepo>>,
    access: Arc<std::sync::atomic::AtomicBool>,
    idle_timeout_ms: i64,
) -> Arc<WebDispatchState> {
    Arc::new(WebDispatchState {
        message_flow: Arc::new(NopMessageFlow),
        collaboration_runtime: Arc::new(bcs_test_support::NoopCollaborationRuntimeService),
        workbench_sessions: Arc::new(NopWorkbenchSessions { access }),
        interactions: Arc::new(NopInteraction),
        group_session_connections: None,
        frontend_connections: Arc::new(WorkbenchConnectionRegistry::new()),
        run_channels: Arc::new(RunChannelManager::new()),
        delivery_admission: Arc::new(AllowAllAdmission),
        speaking_leases: leases.map(|store| store as Arc<dyn bcs_service_api::port::repo::session_speaking_lease::SpeakingLeaseRepoPort>),
        lease_idle_timeout_ms: idle_timeout_ms,
    })
}

pub async fn lease_state() -> Arc<WebDispatchState> {
    nop_state(Some(Arc::new(MemorySpeakingLeaseRepo::new())), Arc::new(std::sync::atomic::AtomicBool::new(true)))
}

/// §15.99: a state whose configured lease idle window is overridden, so
/// contract tests can pin that acquire/preempt grant the CONFIGURED span
/// instead of the frozen 60s legacy value.
pub async fn lease_state_with_idle_timeout_ms(idle_timeout_ms: i64) -> Arc<WebDispatchState> {
    nop_state_with_idle(
        Some(Arc::new(MemorySpeakingLeaseRepo::new())),
        Arc::new(std::sync::atomic::AtomicBool::new(true)),
        idle_timeout_ms,
    )
}

pub async fn lease_state_without_store() -> Arc<WebDispatchState> {
    nop_state(None, Arc::new(std::sync::atomic::AtomicBool::new(true)))
}

pub async fn send_frame(
    state: &Arc<WebDispatchState>,
    tx: &mpsc::Sender<String>,
    connection: &mut bcs_ws::web::WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
    id: &str,
    method: &str,
    params: serde_json::Value,
) -> Response {
    let (feedback, mut feedback_rx) = mpsc::channel::<String>(8);
    let frame = BcsFrame::Request(RequestFrame::new(id, method, Some(params)));
    let outcome = dispatch_client_frame(
        state,
        &serde_json::to_string(&frame).unwrap(),
        &feedback,
        connection,
        auth,
    )
    .await
    .expect("dispatch");
    assert!(matches!(outcome, WebDispatchOutcome::Dispatched));
    let raw = feedback_rx.recv().await.expect("response frame");
    let _ = tx;
    let response: ResponseFrame = match serde_json::from_str(&raw).unwrap() {
        BcsFrame::Response(response) => response,
        other => panic!("expected response frame, got {other:?}"),
    };
    let payload = response.payload.clone().unwrap_or(serde_json::Value::Null);
    Response {
        ok: response.ok,
        error_code: response.error.map(|error| error.code).unwrap_or_default(),
        payload,
    }
}


pub fn lease_state_with_access(access: Arc<std::sync::atomic::AtomicBool>) -> Arc<WebDispatchState> {
    nop_state(Some(Arc::new(MemorySpeakingLeaseRepo::new())), access)
}
