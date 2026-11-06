use std::{
    collections::HashMap,
    future::Future,
    io::{self, Write},
    sync::Arc,
    time::Duration,
};

use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use base64::Engine;
use bcs_bot::{BotCore, ProviderBotEvents, ProviderCore, ProviderManagement};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_http::{
    gateway_trace::{BcnMakeSpan, BcnOnResponse},
    router::build_router,
    state::{BotRuntimeTokenResolverPort, HttpAppState},
};
use bcs_test_support::NoopRelationCoreService;
use bcs_service_api::{
    BotEventCommand, BotEventOutcome, BotRegistryCoreService, BotRunContext, BotRunContextPort,
    CancelStateMachineRunCommand, ChatAbortCommand, ChatAbortOutcome, ChatEventState,
    CollaborationDefinition, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    GroupCallbackCommand, GroupCallbackOutcome, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, MessageFlowService, ProviderAuthMode,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderCoordinationConfig,
    ProviderCoreService, ProviderCredentialRepoPort, ProviderEventIngestCommand,
    ProviderEventSource, ProviderRepoPort, ProviderRunTransport, RegisterProviderBotParams,
    ServiceResult, SessionHistoryResult, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation, StateMachineRunView,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskMessageCommand, TaskMessageOutcome, TaskRunAliasRegistration, WebSendCommand,
    WebSendOutcome,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::sync::{Mutex, RwLock, Semaphore};
use tower::ServiceExt;
use tower_http::trace::TraceLayer;
use tracing::instrument::WithSubscriber;
use tracing_subscriber::prelude::*;


struct TestApp {
    app: Router,
    registry: Arc<BotCore>,
    provider_core: Arc<dyn ProviderCoreService>,
    provider_bot_core: Arc<dyn ProviderBotCoreService>,
    run_context: Arc<RecordingRunContext>,
    message_flow: Arc<RecordingMessageFlow>,
    _temp_dir: TempDir,
}

fn test_app(resolver: Arc<dyn BotRuntimeTokenResolverPort>) -> TestApp {
    test_app_with_collaboration_runtime(resolver, None)
}

fn test_app_with_collaboration_runtime(
    resolver: Arc<dyn BotRuntimeTokenResolverPort>,
    collaboration_runtime: Option<Arc<dyn CollaborationRuntimeService>>,
) -> TestApp {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let registry_service: Arc<dyn BotRegistryCoreService> = registry.clone();
    let provider_core_impl = Arc::new(ProviderCore::new(
        provider_repo,
        provider_credentials,
        provider_bindings,
        registry_service.clone(),
    ));
    let provider_core: Arc<dyn ProviderCoreService> = provider_core_impl.clone();
    let provider_bot_core: Arc<dyn ProviderBotCoreService> = provider_core_impl.clone();
    let provider_management = Arc::new(ProviderManagement::new(
        provider_core.clone(),
        provider_bot_core.clone(),
        registry_service.clone(),
        Arc::new(NoopRelationCoreService),
    ));
    let run_context = Arc::new(RecordingRunContext::default());
    let message_flow = Arc::new(RecordingMessageFlow::default());

    let provider_bot_events = ProviderBotEvents::new(
        provider_bot_core.clone(),
        run_context.clone(),
        message_flow.clone(),
    );
    let provider_bot_events = match collaboration_runtime.as_ref() {
        Some(runtime) => provider_bot_events.with_collaboration_runtime(runtime.clone()),
        None => provider_bot_events,
    };

    let services = Services::builder()
        .registry(registry_service)
        .provider_core(provider_core.clone())
        .provider_bot_core(provider_bot_core.clone())
        .provider_management(provider_management)
        .provider_bot_events(Arc::new(provider_bot_events))
        .bot_run_context(run_context.clone())
        .message_flow(message_flow.clone());
    let services = match collaboration_runtime {
        Some(runtime) => services.collaboration_runtime(runtime),
        None => services,
    }
    .build_for_test();
    let app = build_router(HttpAppState::new(services).with_bot_runtime_token_resolver(resolver));

    TestApp {
        app,
        registry,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir: temp_dir,
    }
}

struct BlockingCollaborationRuntime {
    correlations: RwLock<HashMap<String, StateMachineDeliveryCorrelation>>,
    terminal_calls: Mutex<Vec<HandleBotTerminalEventCommand>>,
    terminal_request_ids: Mutex<Vec<String>>,
    terminal_started: Semaphore,
    terminal_release: Semaphore,
    terminal_completed: Semaphore,
}

impl Default for BlockingCollaborationRuntime {
    fn default() -> Self {
        Self {
            correlations: RwLock::new(HashMap::new()),
            terminal_calls: Mutex::new(Vec::new()),
            terminal_request_ids: Mutex::new(Vec::new()),
            terminal_started: Semaphore::new(0),
            terminal_release: Semaphore::new(0),
            terminal_completed: Semaphore::new(0),
        }
    }
}

impl BlockingCollaborationRuntime {
    async fn insert_correlation(
        &self,
        provider_run_id: &str,
        assignee_bot_id: &str,
    ) {
        self.correlations.write().await.insert(
            provider_run_id.to_string(),
            StateMachineDeliveryCorrelation {
                state_machine_run_id: "sm-run-async".to_string(),
                node_id: "judge-node".to_string(),
                attempt: 0,
                assignee_bot_id: assignee_bot_id.to_string(),
                delivery_request_id: "sm-delivery-async".to_string(),
                bot_delivery_run_id: Some(provider_run_id.to_string()),
            },
        );
    }

    async fn wait_for_terminal_start(&self) {
        tokio::time::timeout(Duration::from_secs(1), self.terminal_started.acquire())
            .await
            .expect("state-machine terminal processing should start")
            .expect("terminal start semaphore should remain open")
            .forget();
    }

    fn release_terminal(&self) {
        self.terminal_release.add_permits(1);
    }

    async fn wait_for_terminal_completion(&self) {
        tokio::time::timeout(Duration::from_secs(1), self.terminal_completed.acquire())
            .await
            .expect("state-machine terminal processing should complete")
            .expect("terminal completion semaphore should remain open")
            .forget();
    }

    async fn terminal_call_count(&self) -> usize {
        self.terminal_calls.lock().await.len()
    }
}

#[async_trait::async_trait]
impl CollaborationRuntimeService for BlockingCollaborationRuntime {
    async fn start_state_machine_run(
        &self,
        _cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        unreachable!("not used by bot event contract")
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_session_history(
        &self,
        _session_id: &str,
        _limit: u64,
        _before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn cancel_state_machine_run(
        &self,
        _cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        unreachable!("not used by bot event contract")
    }

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(self.correlations.read().await.get(run_id).cloned())
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
        cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        self.terminal_calls.lock().await.push(cmd);
        self.terminal_started.add_permits(1);
        self.terminal_release
            .acquire()
            .await
            .expect("terminal release semaphore should remain open")
            .forget();
        self.terminal_request_ids.lock().await.push(bcs_observability::current_request_id());
        self.terminal_completed.add_permits(1);
        Ok(HandleBotTerminalEventOutcome {
            consumed: true,
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
        unreachable!("not used by bot event contract")
    }
}

fn state_machine_final_request(
    provider_id: &str,
    token: &str,
    provider_run_id: &str,
) -> Request<Body> {
    state_machine_final_request_with_text(provider_id, token, provider_run_id, "candidate artifact")
}

fn state_machine_final_request_with_text(
    provider_id: &str,
    token: &str,
    provider_run_id: &str,
    text: &str,
) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri("/bot/events")
        .header("content-type", "application/json")
        .header("X-BCN-Provider-Id", provider_id)
        .header("authorization", format!("Bearer {token}"))
        .body(Body::from(
            json!({
                "run_id": provider_run_id,
                "seq": 1,
                "state": "final",
                "message": { "text": text }
            })
            .to_string(),
        ))
        .unwrap()
}










fn diagnostic_jwt(header: Value, payload: Value, signature: &[u8]) -> String {
    let encode = |bytes: &[u8]| {
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
    };
    format!(
        "{}.{}.{}",
        encode(&serde_json::to_vec(&header).unwrap()),
        encode(&serde_json::to_vec(&payload).unwrap()),
        encode(signature),
    )
}







struct RegisteredTestBot {
    provider_id: String,
    bot_uuid: String,
    bot_runtime_token: Option<String>,
}

async fn register_provider_bot(
    provider_core: &dyn ProviderCoreService,
    provider_bot_core: &dyn ProviderBotCoreService,
    auth_mode: ProviderAuthMode,
    provider_bot_ref: &str,
) -> RegisteredTestBot {
    register_provider_bot_with_coordination(
        provider_core,
        provider_bot_core,
        auth_mode,
        provider_bot_ref,
        None,
    )
    .await
}

async fn register_provider_bot_with_coordination(
    provider_core: &dyn ProviderCoreService,
    provider_bot_core: &dyn ProviderBotCoreService,
    auth_mode: ProviderAuthMode,
    provider_bot_ref: &str,
    coordination: Option<ProviderCoordinationConfig>,
) -> RegisteredTestBot {
    let provider = provider_core
        .register_provider(
            "Provider".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            auth_mode,
            "11111111".to_string(),
            None,
            coordination,
        )
        .await
        .unwrap();
    let provider_id = provider.provider.provider_id;
    let (binding, bot_runtime_token) = provider_bot_core
        .register_provider_bot_with_bot_uuid(
            &provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: Some("Reviews code".to_string()),
                owners: vec!["11111111".to_string()],
                provider_bot_ref: provider_bot_ref.to_string(),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    RegisteredTestBot {
        provider_id,
        bot_uuid: binding.bot_uuid,
        bot_runtime_token,
    }
}

struct RegisteredAdminTestBot {
    provider_id: String,
    bot_uuid: String,
    admin_token: String,
}

async fn register_provider_bot_with_admin_token(
    provider_core: &dyn ProviderCoreService,
    provider_bot_core: &dyn ProviderBotCoreService,
    auth_mode: ProviderAuthMode,
    provider_bot_ref: &str,
) -> RegisteredAdminTestBot {
    let provider = provider_core
        .register_provider(
            "Provider".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            auth_mode,
            "11111111".to_string(),
            None,
            None,
        )
        .await
        .unwrap();
    let provider_id = provider.provider.provider_id;
    let admin_token = provider.provider_admin_token;
    let (binding, _) = provider_bot_core
        .register_provider_bot_with_bot_uuid(
            &provider_id,
            &admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: Some("Reviews code".to_string()),
                owners: vec!["11111111".to_string()],
                provider_bot_ref: provider_bot_ref.to_string(),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    RegisteredAdminTestBot {
        provider_id,
        bot_uuid: binding.bot_uuid,
        admin_token,
    }
}

#[derive(Default)]
struct StaticAgentpassResolver {
    tokens: RwLock<HashMap<String, String>>,
    provider_admin_tokens: RwLock<HashMap<String, String>>,
}

impl StaticAgentpassResolver {
    fn new(tokens: HashMap<String, String>) -> Self {
        Self {
            tokens: RwLock::new(tokens),
            provider_admin_tokens: RwLock::new(HashMap::new()),
        }
    }

    async fn insert_provider_admin(&self, token: &str, provider_id: &str) {
        self.provider_admin_tokens
            .write()
            .await
            .insert(token.to_string(), provider_id.to_string());
    }
}

#[async_trait::async_trait]
impl BotRuntimeTokenResolverPort for StaticAgentpassResolver {
    async fn resolve_agentpass_agent_code(&self, token: &str) -> Option<String> {
        self.tokens.read().await.get(token).cloned()
    }

    async fn try_provider_admin(&self, token: &str) -> Option<String> {
        self.provider_admin_tokens
            .read()
            .await
            .get(token)
            .cloned()
    }
}

#[derive(Default)]
struct RecordingRunContext {
    contexts: RwLock<HashMap<String, BotRunContext>>,
    processing: RwLock<std::collections::HashSet<String>>,
    provider_transports: RwLock<HashMap<String, ProviderRunTransport>>,
}

#[async_trait::async_trait]
impl BotRunContextPort for RecordingRunContext {
    async fn put_context(&self, context: BotRunContext) {
        self.contexts
            .write()
            .await
            .insert(context.run_id.clone(), context);
    }

    async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
        self.contexts.read().await.get(run_id).cloned()
    }

    async fn try_begin_terminal(&self, run_id: &str) -> bool {
        let contexts = self.contexts.read().await;
        let Some(context) = contexts.get(run_id) else {
            return false;
        };
        if context.terminal {
            return false;
        }
        drop(contexts);

        let mut processing = self.processing.write().await;
        if processing.contains(run_id) {
            return false;
        }
        processing.insert(run_id.to_string());
        true
    }

    async fn mark_terminal(&self, run_id: &str) -> bool {
        let mut contexts = self.contexts.write().await;
        let Some(context) = contexts.get_mut(run_id) else {
            return false;
        };
        if context.terminal {
            return false;
        }
        context.terminal = true;
        self.processing.write().await.remove(run_id);
        true
    }

    async fn release_terminal(&self, run_id: &str) {
        self.processing.write().await.remove(run_id);
    }

    async fn begin_provider_transport(&self, run_id: &str, _deadline_ms: u64) -> bool {
        let mut transports = self.provider_transports.write().await;
        if transports.contains_key(run_id) {
            return false;
        }
        transports.insert(run_id.to_string(), ProviderRunTransport::Negotiating);
        true
    }

    async fn bind_provider_transport(
        &self,
        run_id: &str,
        transport: ProviderRunTransport,
    ) -> bool {
        self.provider_transports
            .write()
            .await
            .insert(run_id.to_string(), transport);
        true
    }

    async fn get_provider_transport(&self, run_id: &str) -> Option<ProviderRunTransport> {
        self.provider_transports.read().await.get(run_id).copied()
    }

    async fn mark_provider_transport_terminal(&self, run_id: &str) {
        self.provider_transports
            .write()
            .await
            .insert(run_id.to_string(), ProviderRunTransport::Terminal);
    }

    async fn clear_provider_transport(&self, run_id: &str) {
        self.provider_transports.write().await.remove(run_id);
    }
}

#[derive(Default)]
struct RecordingMessageFlow {
    managed_context: Mutex<Option<(String, BotRunContext)>>,
    events: Mutex<Vec<BotEventCommand>>,
    sources: Mutex<Vec<ProviderEventSource>>,
    task_dispatches: Mutex<Vec<TaskDispatchCommand>>,
    task_messages: Mutex<Vec<TaskMessageCommand>>,
    task_completes: Mutex<Vec<TaskCompleteCommand>>,
    fail_next: Mutex<bool>,
}

#[async_trait::async_trait]
impl MessageFlowService for RecordingMessageFlow {
    async fn resolve_managed_provider_run(&self, run_id: &str, provider_id: &str, bot_id: &str) -> ServiceResult<Option<BotRunContext>> {
        Ok(self.managed_context.lock().await.as_ref().filter(|(provider, context)| provider == provider_id && context.bot_id == bot_id && context.run_id == run_id).map(|(_, context)| context.clone()))
    }
    async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        unreachable!("not used by this contract")
    }

    async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        let mut fail_next = self.fail_next.lock().await;
        if *fail_next {
            *fail_next = false;
            return Err(bcs_service_api::ServiceError::InternalError(
                "message flow failed".to_string(),
            ));
        }
        drop(fail_next);

        let run_id = cmd.run_id.clone();
        self.events.lock().await.push(cmd);
        Ok(BotEventOutcome {
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            unregistered_run_ids: vec![run_id],
            mentions: Vec::new(),
            delivered_count: 1,
            failed_count: 0,
            delivery_results: Vec::new(),
        })
    }

    async fn ingest_provider_event(
        &self,
        cmd: ProviderEventIngestCommand,
    ) -> ServiceResult<BotEventOutcome> {
        self.sources.lock().await.push(cmd.source);
        self.handle_bot_event(cmd.event).await
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        unreachable!("not used by this contract")
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
        cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        self.task_dispatches.lock().await.push(cmd);
        Ok(TaskDispatchOutcome {
            task_id: "task-dispatched".to_string(),
            status: "dispatched".to_string(),
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            real_run_id: None,
})
    }

    async fn handle_task_message(
        &self,
        cmd: TaskMessageCommand,
    ) -> ServiceResult<TaskMessageOutcome> {
        self.task_messages.lock().await.push(cmd);
        Ok(TaskMessageOutcome {
            status: "sent".to_string(),
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
        })
    }

    async fn handle_task_complete(
        &self,
        cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        self.task_completes.lock().await.push(cmd);
        Ok(TaskCompleteOutcome {
            status: "completed".to_string(),
            blocked: false,
            pending: Vec::new(),
            callback_requested: false,
            completed_session: None,
            frontend_deliveries: Vec::new(),
        })
    }
}

async fn response_json(response: axum::response::Response) -> Value {
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    serde_json::from_slice(&body).unwrap()
}

#[path = "bot_events_contract/authentication.rs"]
mod authentication;
#[path = "bot_events_contract/task_events.rs"]
mod task_events;
#[path = "bot_events_contract/agent_events.rs"]
mod agent_events;
#[path = "bot_events_contract/coordination.rs"]
mod coordination;
#[path = "bot_events_contract/redaction.rs"]
mod redaction;
