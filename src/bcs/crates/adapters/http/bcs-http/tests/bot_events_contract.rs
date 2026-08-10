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
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome, CoordinationMode,
    GroupCallbackCommand, GroupCallbackOutcome, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, MessageFlowService, ProviderAuthMode,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderCoordinationConfig,
    ProviderCoreService, ProviderCredentialRepoPort, ProviderRepoPort,
    RegisterProviderBotParams, ServiceResult, SessionHistoryResult, StartStateMachineRunCommand,
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

#[derive(Clone, Default)]
struct SharedLogBuffer(Arc<std::sync::Mutex<Vec<u8>>>);

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

async fn capture_tracing_logs<Fut>(future: Fut) -> String
where
    Fut: Future<Output = ()>,
{
    let buffer = SharedLogBuffer::default();
    let subscriber = tracing_subscriber::fmt()
        .with_ansi(false)
        .with_level(false)
        .with_target(true)
        .with_writer(buffer.clone())
        .finish();
    let dispatch = tracing::Dispatch::new(subscriber);
    let guard = tracing::dispatcher::set_default(&dispatch);
    future.await;
    drop(guard);
    String::from_utf8(buffer.0.lock().unwrap().clone()).unwrap()
}

async fn capture_otel_spans<F, T>(future: F) -> (T, Vec<opentelemetry_sdk::trace::SpanData>)
where
    F: Future<Output = T>,
{
    opentelemetry::global::set_text_map_propagator(
        opentelemetry_sdk::propagation::TraceContextPropagator::new(),
    );
    let exporter = opentelemetry_sdk::trace::InMemorySpanExporterBuilder::new().build();
    let provider = opentelemetry_sdk::trace::SdkTracerProvider::builder()
        .with_simple_exporter(exporter.clone())
        .build();
    let tracer = opentelemetry::trace::TracerProvider::tracer(&provider, "bot-events-contract");
    let subscriber = tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new("bcn_otel=info"))
        .with(tracing_opentelemetry::layer().with_tracer(tracer));

    let output = future.with_subscriber(subscriber).await;
    provider.force_flush().unwrap();
    (output, exporter.get_finished_spans().unwrap())
}

fn assert_otel_span_string_attribute(
    span: &opentelemetry_sdk::trace::SpanData,
    key: &str,
    expected: &str,
) {
    assert!(span.attributes.iter().any(|attr| {
        attr.key.as_str() == key
            && matches!(&attr.value, opentelemetry::Value::String(value) if value.as_str() == expected)
    }));
}

fn assert_otel_span_bool_attribute(
    span: &opentelemetry_sdk::trace::SpanData,
    key: &str,
    expected: bool,
) {
    assert!(span.attributes.iter().any(|attr| {
        attr.key.as_str() == key && attr.value == opentelemetry::Value::Bool(expected)
    }));
}

fn assert_gen_ai_output_message(
    span: &opentelemetry_sdk::trace::SpanData,
    expected_content: &str,
    expected_finish_reason: &str,
) {
    let value = span
        .attributes
        .iter()
        .find_map(|attr| match &attr.value {
            opentelemetry::Value::String(value)
                if attr.key.as_str() == "gen_ai.output.messages" =>
            {
                Some(value.as_str())
            }
            _ => None,
        })
        .expect("gen_ai.output.messages string attribute");
    let messages: Value = serde_json::from_str(value).expect("schema-compliant output messages JSON");
    assert_eq!(messages.as_array().map(Vec::len), Some(1));
    assert_eq!(messages[0]["role"], "assistant");
    assert_eq!(messages[0]["parts"].as_array().map(Vec::len), Some(1));
    assert_eq!(messages[0]["parts"][0]["type"], "text");
    assert_eq!(messages[0]["parts"][0]["content"], expected_content);
    assert_eq!(messages[0]["finish_reason"], expected_finish_reason);
}

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
    terminal_started: Semaphore,
    terminal_release: Semaphore,
    terminal_completed: Semaphore,
}

impl Default for BlockingCollaborationRuntime {
    fn default() -> Self {
        Self {
            correlations: RwLock::new(HashMap::new()),
            terminal_calls: Mutex::new(Vec::new()),
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

#[tokio::test]
async fn bot_events_returns_200_before_state_machine_final_processing_completes() {
    let collaboration_runtime = Arc::new(BlockingCollaborationRuntime::default());
    let runtime_port: Arc<dyn CollaborationRuntimeService> = collaboration_runtime.clone();
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        ..
    } = test_app_with_collaboration_runtime(
        Arc::new(StaticAgentpassResolver::default()),
        Some(runtime_port),
    );
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "async-state-machine-bot",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    collaboration_runtime
        .insert_correlation("provider-run-async", &registered.bot_uuid)
        .await;

    let response = tokio::time::timeout(
        Duration::from_secs(1),
        app.oneshot(state_machine_final_request(
            &registered.provider_id,
            &token,
            "provider-run-async",
        )),
    )
    .await
    .expect("state-machine final callback should not wait for background processing")
    .expect("state-machine final callback response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);
    assert_eq!(body["failed_count"], 0);

    collaboration_runtime.wait_for_terminal_start().await;
    collaboration_runtime.release_terminal();
    collaboration_runtime.wait_for_terminal_completion().await;
}

#[tokio::test]
async fn bot_events_coalesces_duplicate_state_machine_final_while_processing() {
    let collaboration_runtime = Arc::new(BlockingCollaborationRuntime::default());
    let runtime_port: Arc<dyn CollaborationRuntimeService> = collaboration_runtime.clone();
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        ..
    } = test_app_with_collaboration_runtime(
        Arc::new(StaticAgentpassResolver::default()),
        Some(runtime_port),
    );
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "deduplicated-state-machine-bot",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    collaboration_runtime
        .insert_correlation("provider-run-first", &registered.bot_uuid)
        .await;
    collaboration_runtime
        .insert_correlation("provider-run-retry", &registered.bot_uuid)
        .await;

    let first = app
        .clone()
        .oneshot(state_machine_final_request(
            &registered.provider_id,
            &token,
            "provider-run-first",
        ))
        .await
        .expect("first callback response");
    assert_eq!(first.status(), StatusCode::OK);
    collaboration_runtime.wait_for_terminal_start().await;

    let duplicate = app
        .oneshot(state_machine_final_request(
            &registered.provider_id,
            &token,
            "provider-run-retry",
        ))
        .await
        .expect("duplicate callback response");
    assert_eq!(duplicate.status(), StatusCode::OK);
    let body = response_json(duplicate).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);
    assert_eq!(collaboration_runtime.terminal_call_count().await, 1);

    collaboration_runtime.release_terminal();
    collaboration_runtime.wait_for_terminal_completion().await;
}

#[tokio::test]
async fn bot_events_rejects_state_machine_identity_mismatch_before_spawning() {
    let collaboration_runtime = Arc::new(BlockingCollaborationRuntime::default());
    let runtime_port: Arc<dyn CollaborationRuntimeService> = collaboration_runtime.clone();
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        ..
    } = test_app_with_collaboration_runtime(
        Arc::new(StaticAgentpassResolver::default()),
        Some(runtime_port),
    );
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "mismatched-state-machine-bot",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    collaboration_runtime
        .insert_correlation("provider-run-mismatch", "different-bot")
        .await;

    let response = app
        .oneshot(state_machine_final_request(
            &registered.provider_id,
            &token,
            "provider-run-mismatch",
        ))
        .await
        .expect("identity mismatch response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(collaboration_runtime.terminal_call_count().await, 0);
}

#[tokio::test]
async fn bot_events_rejects_empty_state_machine_final_before_spawning() {
    let collaboration_runtime = Arc::new(BlockingCollaborationRuntime::default());
    let runtime_port: Arc<dyn CollaborationRuntimeService> = collaboration_runtime.clone();
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        ..
    } = test_app_with_collaboration_runtime(
        Arc::new(StaticAgentpassResolver::default()),
        Some(runtime_port),
    );
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "empty-state-machine-bot",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    collaboration_runtime
        .insert_correlation("provider-run-empty", &registered.bot_uuid)
        .await;

    let response = app
        .oneshot(state_machine_final_request_with_text(
            &registered.provider_id,
            &token,
            "provider-run-empty",
            "   ",
        ))
        .await
        .expect("empty final response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["error"], "invalid_request");
    assert_eq!(collaboration_runtime.terminal_call_count().await, 0);
}

#[tokio::test]
async fn bot_events_logs_json_rejections_before_handler() {
    let TestApp { app, .. } = test_app(Arc::new(StaticAgentpassResolver::default()));

    let logs = capture_tracing_logs(async {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bot/events")
                    .header("content-type", "application/json")
                    .header("X-BCN-Provider-Id", "prv-log")
                    .header("authorization", "Bearer runtime-token")
                    .body(Body::from(
                        json!({
                            "run_id": "run-json-rejection",
                            "state": "failed",
                            "message": { "text": "boom" }
                        })
                        .to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
        let body = String::from_utf8(
            to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap()
                .to_vec(),
        )
        .unwrap();
        assert!(body.contains("Failed to deserialize"), "body: {body}");
        assert!(body.contains("unknown variant `failed`"), "body: {body}");
    })
    .await;

    assert!(
        logs.contains("provider callback: invalid bot event request"),
        "expected invalid request log, got:\n{logs}"
    );
    assert!(
        logs.contains("provider_id=prv-log"),
        "expected provider id in log, got:\n{logs}"
    );
    assert!(
        logs.contains("unknown variant"),
        "expected serde rejection in log, got:\n{logs}"
    );
    assert!(
        logs.contains("request_body_len="),
        "expected request body length in log, got:\n{logs}"
    );
    assert!(
        !logs.contains("boom"),
        "request body leaked into log:\n{logs}"
    );
}

#[tokio::test]
async fn bot_events_accepts_static_bearer_final_for_matching_run() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-1".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-1",
                        "seq": 1,
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.bot_id, registered.bot_uuid);
    assert_eq!(cmd.run_id, "run-1");
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert_eq!(cmd.state, ChatEventState::Final);
    assert_eq!(cmd.event_payload["state"], "final");
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["type"],
        "text"
    );
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["text"],
        "done"
    );
    drop(events);
    assert!(run_context.get_context("run-1").await.unwrap().terminal);
}

#[tokio::test]
async fn bot_events_defaults_missing_message_to_empty_text() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-missing-message".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:missing-message".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-missing-message",
                        "state": "final"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.bot_id, registered.bot_uuid);
    assert_eq!(cmd.run_id, "run-missing-message");
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.bcs_session_id.as_deref(), Some("group-1:missing-message"));
    assert_eq!(cmd.state, ChatEventState::Final);
    assert_eq!(cmd.event_payload["state"], "final");
    assert_eq!(cmd.event_payload["message"]["content"][0]["text"], "");
    drop(events);
    assert!(run_context
        .get_context("run-missing-message")
        .await
        .unwrap()
        .terminal);
}

#[tokio::test]
async fn bot_events_accepts_agentpass_final_for_matching_run() {
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        "agentpass.header.sig".to_string(),
        "agent-code-1".to_string(),
    )]));
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(resolver));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-1",
    )
    .await;
    assert!(registered.bot_runtime_token.is_none());
    run_context
        .put_context(BotRunContext {
            run_id: "run-agentpass".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-agentpass".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", "Bearer agentpass.header.sig")
                .body(Body::from(
                    json!({
                        "run_id": "run-agentpass",
                        "state": "final",
                        "message": { "text": "agentpass done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, registered.bot_uuid);
    assert_eq!(events[0].event_payload["state"], "final");
    assert_eq!(
        events[0].event_payload["message"]["content"][0]["type"],
        "text"
    );
    assert_eq!(
        events[0].event_payload["message"]["content"][0]["text"],
        "agentpass done"
    );
}

#[tokio::test]
async fn bot_events_accepts_static_bearer_error_for_matching_run() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-error".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:error-session".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-error",
                        "seq": 1,
                        "state": "error",
                        "message": { "text": "provider failed" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.bot_id, registered.bot_uuid);
    assert_eq!(cmd.run_id, "run-error");
    assert_eq!(cmd.group_id, "group-1");
    assert_eq!(cmd.bcs_session_id.as_deref(), Some("group-1:error-session"));
    assert_eq!(cmd.state, ChatEventState::Error);
    assert_eq!(cmd.event_payload["state"], "error");
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["type"],
        "text"
    );
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["text"],
        "provider failed"
    );
    drop(events);
    assert!(run_context.get_context("run-error").await.unwrap().terminal);
}

#[tokio::test]
async fn bot_events_rejects_delta_state() {
    let TestApp {
        app, _temp_dir, ..
    } = test_app(Arc::new(StaticAgentpassResolver::default()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "run_id": "run-1",
                        "state": "delta",
                        "message": { "text": "partial" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["error"], "invalid_request");
}

/// Callback streaming (spec §11): a 2.0 provider POSTs a full agent tool-result
/// event (non-terminal). With `event`+`payload` present the terminal-only guard
/// is relaxed: the event flows through the pipeline and the run stays OPEN
/// (not marked terminal), so subsequent events for the same run still arrive.
#[tokio::test]
async fn bot_events_accepts_callback_streaming_agent_tool_result_without_closing_run() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-cb".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-cb",
                        "seq": 1,
                        "event": "agent",
                        "payload": {
                            "stream": "tool",
                            "data": {
                                "phase": "result",
                                "name": "search",
                                "toolCallId": "tc-1",
                                "result": { "content": [{ "type": "text", "text": "hits=3" }] },
                                "isError": false
                            }
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.run_id, "run-cb");
    assert_eq!(cmd.event_type, "agent");
    assert_eq!(cmd.event_payload["stream"], "tool");
    assert_eq!(cmd.event_payload["data"]["phase"], "result");
    drop(events);
    // Non-terminal: the run must remain open.
    assert!(!run_context.get_context("run-cb").await.unwrap().terminal);
}

/// Callback streaming: a chat `state=final` event (carried in `payload`) IS a
/// terminal event — it closes the run, exactly like the legacy terminal path.
#[tokio::test]
async fn bot_events_accepts_callback_streaming_chat_final_and_closes_run() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-cb2".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-cb2",
                        "seq": 9,
                        "event": "chat",
                        "payload": {
                            "state": "final",
                            "message": { "content": [{ "type": "text", "text": "all done" }] }
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.run_id, "run-cb2");
    assert_eq!(cmd.event_type, "chat.event");
    assert_eq!(cmd.state, ChatEventState::Final);
    assert_eq!(cmd.event_payload["state"], "final");
    drop(events);
    // Terminal: the run must be closed.
    assert!(run_context.get_context("run-cb2").await.unwrap().terminal);
}

#[tokio::test]
async fn bot_events_accepts_callback_streaming_chat_error_message() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-cb2-error".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("group-1:abcdef12".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-cb2-error",
                        "seq": 10,
                        "event": "chat",
                        "payload": {
                            "state": "error",
                            "errorMessage": "engine crashed"
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["delivered_count"], 1);

    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    let cmd = &events[0];
    assert_eq!(cmd.run_id, "run-cb2-error");
    assert_eq!(cmd.event_type, "chat.event");
    assert_eq!(cmd.state, ChatEventState::Error);
    assert_eq!(cmd.event_payload["state"], "error");
    assert_eq!(
        cmd.event_payload["message"]["content"][0]["text"],
        "engine crashed"
    );
    drop(events);
    assert!(run_context
        .get_context("run-cb2-error")
        .await
        .unwrap()
        .terminal);
}

#[tokio::test]
async fn bot_events_rejects_identity_run_mismatch() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-mismatch".to_string(),
            bot_id: "other-bot".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-mismatch",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(message_flow.events.lock().await.is_empty());
    assert!(!run_context
        .get_context("run-mismatch")
        .await
        .unwrap()
        .terminal);
}

#[tokio::test]
async fn bot_events_does_not_fallback_to_agentpass_on_static_provider_mismatch() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let static_bot = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let static_token = static_bot.bot_runtime_token.expect("runtime token");
    resolver
        .tokens
        .write()
        .await
        .insert(static_token.clone(), "agent-code-2".to_string());
    let agentpass_bot = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-2",
    )
    .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-no-fallback".to_string(),
            bot_id: agentpass_bot.bot_uuid,
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", agentpass_bot.provider_id.as_str())
                .header("authorization", format!("Bearer {static_token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-no-fallback",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_routes_jwt_shaped_token_to_agentpass_before_static_lookup() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let static_bot = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let jwt_token = "jwt.header.sig";
    registry
        .save_token(&static_bot.bot_uuid, jwt_token)
        .await
        .expect("save jwt-shaped static token");
    resolver
        .tokens
        .write()
        .await
        .insert(jwt_token.to_string(), "agent-code-2".to_string());
    let agentpass_bot = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-2",
    )
    .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-jwt-agentpass".to_string(),
            bot_id: agentpass_bot.bot_uuid.clone(),
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", agentpass_bot.provider_id.as_str())
                .header("authorization", format!("Bearer {jwt_token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-jwt-agentpass",
                        "state": "final",
                        "message": { "text": "agentpass done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, agentpass_bot.bot_uuid);
}

#[tokio::test]
async fn agentpass_resolve_returns_agent_code_binding_and_bot() {
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        "agentpass.header.sig".to_string(),
        "agent-code-1".to_string(),
    )]));
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        ..
    } = test_app(Arc::new(resolver));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-1",
    )
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", "Bearer agentpass.header.sig")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["agent_code"], "agent-code-1");
    assert_eq!(body["provider_bot_binding"]["provider_id"], registered.provider_id);
    assert_eq!(body["provider_bot_binding"]["provider_bot_ref"], "agent-code-1");
    assert_eq!(body["provider_bot_binding"]["bot_uuid"], registered.bot_uuid);
    assert_eq!(body["bot"]["bot_uuid"], registered.bot_uuid);
    assert_eq!(body["bot"]["capabilities"]["name"], "Code Reviewer");
}

#[tokio::test]
async fn agentpass_resolve_returns_nulls_when_token_cannot_be_resolved() {
    let TestApp { app, .. } = test_app(Arc::new(StaticAgentpassResolver::default()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", "prv_missing")
                .header("authorization", "Bearer unknown.header.sig")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert!(body["agent_code"].is_null());
    assert!(body["provider_bot_binding"].is_null());
    assert!(body["bot"].is_null());
}

#[tokio::test]
async fn agentpass_resolve_returns_agent_code_with_null_binding_when_mapping_is_absent() {
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        "agentpass.header.sig".to_string(),
        "missing-agent-code".to_string(),
    )]));
    let TestApp { app, .. } = test_app(Arc::new(resolver));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", "prv_missing")
                .header("authorization", "Bearer agentpass.header.sig")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["agent_code"], "missing-agent-code");
    assert!(body["provider_bot_binding"].is_null());
    assert!(body["bot"].is_null());
}

#[tokio::test]
async fn agentpass_resolve_returns_binding_with_null_bot_when_bot_row_is_missing() {
    let resolver = StaticAgentpassResolver::new(HashMap::from([(
        "agentpass.header.sig".to_string(),
        "agent-code-1".to_string(),
    )]));
    let TestApp {
        app,
        registry,
        provider_core,
        provider_bot_core,
        ..
    } = test_app(Arc::new(resolver));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::AgentPass,
        "agent-code-1",
    )
    .await;
    assert!(registry.unregister(&registered.bot_uuid).await);

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers/agentpass/resolve")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", "Bearer agentpass.header.sig")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["agent_code"], "agent-code-1");
    assert_eq!(body["provider_bot_binding"]["bot_uuid"], registered.bot_uuid);
    assert!(body["bot"].is_null());
}

#[tokio::test]
async fn bot_events_releases_terminal_processing_when_message_flow_fails() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-retry".to_string(),
            bot_id: registered.bot_uuid,
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;
    *message_flow.fail_next.lock().await = true;

    let failed_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-retry",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(failed_response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert!(!run_context.get_context("run-retry").await.unwrap().terminal);

    let retry_response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-retry",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(retry_response.status(), StatusCode::OK);
    assert!(run_context.get_context("run-retry").await.unwrap().terminal);
}

#[tokio::test]
async fn bot_events_accepts_provider_admin_with_bot_ref() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-admin".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-admin",
                        "state": "final",
                        "message": { "text": "admin done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let events = message_flow.events.lock().await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].bot_id, registered.bot_uuid);
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_without_bot_ref() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-admin-noref".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-admin-noref",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["error"], "invalid_request");
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_when_run_bot_mismatches_ref() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-mismatch".to_string(),
            bot_id: "other-bot".to_string(),
            group_id: "group-admin".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-mismatch",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_provider_id_mismatch() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    let other = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "other-reviewer",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-provider-mismatch".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", other.provider_id.as_str())
                .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-provider-mismatch",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["error"], "provider_id_mismatch");
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_unknown_bot_ref() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::ProviderAdmin,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-unknown-ref".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("X-BCN-Provider-Bot-Ref", "missing-ref")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-unknown-ref",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = response_json(response).await;
    assert_eq!(body["error"], "bot_not_found");
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_rejects_provider_admin_token_for_static_bearer_provider() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-mode-mismatch".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-admin".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                .header(
                    "authorization",
                    format!("Bearer {}", registered.admin_token),
                )
                .body(Body::from(
                    json!({
                        "run_id": "run-mode-mismatch",
                        "state": "final",
                        "message": { "text": "done" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body = response_json(response).await;
    assert_eq!(body["error"], "auth_mode_mismatch");
    assert!(message_flow.events.lock().await.is_empty());
}

#[tokio::test]
async fn bot_events_marks_auth_failure_content_untrusted_without_business_attributes() {
    let resolver = Arc::new(StaticAgentpassResolver::default());
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        run_context,
        ..
    } = test_app(resolver.clone());
    let registered = register_provider_bot_with_admin_token(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    resolver
        .insert_provider_admin(&registered.admin_token, &registered.provider_id)
        .await;
    run_context
        .put_context(BotRunContext {
            run_id: "run-untrusted".to_string(),
            bot_id: registered.bot_uuid,
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;
    let app = app.layer(
        TraceLayer::new_for_http()
            .make_span_with(BcnMakeSpan)
            .on_response(BcnOnResponse),
    );
    let provider_id = registered.provider_id;
    let admin_token = registered.admin_token;

    let (status, spans) = capture_otel_spans(async move {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bot/events")
                    .header("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
                    .header("content-type", "application/json")
                    .header("X-BCN-Provider-Id", provider_id)
                    .header("X-BCN-Provider-Bot-Ref", "reviewer-v2")
                    .header("authorization", format!("Bearer {admin_token}"))
                    .body(Body::from(json!({
                        "run_id": "run-untrusted",
                        "state": "final",
                        "message": { "text": "untrusted-callback-content" }
                    }).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        drop(response);
        status
    })
    .await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let span = spans.iter().find(|span| span.name == "bcn.bot.response").unwrap();
    assert_otel_span_string_attribute(span, "bcn.auth.result", "failed");
    assert!(!span.attributes.iter().any(|attr| attr.key.as_str() == "bcn.provider.id"));
    assert!(!span.attributes.iter().any(|attr| attr.key.as_str() == "bcn.run.id"));
    assert!(span.events.events.is_empty());
    assert_otel_span_bool_attribute(span, "bcn.content.untrusted", true);
    assert_gen_ai_output_message(span, "untrusted-callback-content", "stop");
}

#[tokio::test]
async fn bot_events_marks_success_content_trusted_with_business_attributes() {
    let TestApp {
        app,
        provider_core,
        provider_bot_core,
        run_context,
        ..
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "reviewer-v2",
    )
    .await;
    let token = registered.bot_runtime_token.unwrap();
    run_context
        .put_context(BotRunContext {
            run_id: "run-trusted".to_string(),
            bot_id: registered.bot_uuid,
            group_id: "group-1".to_string(),
            bcs_session_id: None,
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;
    let app = app.layer(
        TraceLayer::new_for_http()
            .make_span_with(BcnMakeSpan)
            .on_response(BcnOnResponse),
    );
    let provider_id = registered.provider_id;
    let request_provider_id = provider_id.clone();

    let (status, spans) = capture_otel_spans(async move {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bot/events")
                    .header("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
                    .header("content-type", "application/json")
                    .header("X-BCN-Provider-Id", request_provider_id.as_str())
                    .header("authorization", format!("Bearer {token}"))
                    .body(Body::from(json!({
                        "run_id": "run-trusted",
                        "state": "final",
                        "message": { "text": "trusted-callback-content" }
                    }).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        drop(response);
        status
    })
    .await;

    assert_eq!(status, StatusCode::OK);
    let span = spans.iter().find(|span| span.name == "bcn.bot.response").unwrap();
    assert_otel_span_string_attribute(span, "bcn.auth.result", "success");
    assert_otel_span_string_attribute(span, "bcn.provider.id", &provider_id);
    assert_otel_span_string_attribute(span, "bcn.run.id", "run-trusted");
    assert!(span.events.events.is_empty());
    assert_otel_span_bool_attribute(span, "bcn.content.untrusted", false);
    assert_gen_ai_output_message(span, "trusted-callback-content", "stop");
}

#[tokio::test]
async fn bot_events_records_untrusted_content_when_auth_cannot_follow_invalid_shape() {
    let TestApp { app, .. } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let app = app.layer(
        TraceLayer::new_for_http()
            .make_span_with(BcnMakeSpan)
            .on_response(BcnOnResponse),
    );

    let (status, spans) = capture_otel_spans(async move {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bot/events")
                    .header("traceparent", "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
                    .header("content-type", "application/json")
                    .header("X-BCN-Provider-Id", "unverified-provider")
                    .header("authorization", "Bearer unverified-token")
                    .body(Body::from(json!({
                        "run_id": "unverified-run",
                        "message": { "text": "invalid-shape-content" }
                    }).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        drop(response);
        status
    })
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    let span = spans.iter().find(|span| span.name == "bcn.bot.response").unwrap();
    assert_otel_span_string_attribute(span, "bcn.auth.result", "unverified");
    assert!(!span.attributes.iter().any(|attr| attr.key.as_str() == "bcn.provider.id"));
    assert!(span.events.events.is_empty());
    assert_otel_span_bool_attribute(span, "bcn.content.untrusted", true);
    assert_gen_ai_output_message(span, "invalid-shape-content", "unknown");
}

#[tokio::test]
async fn bot_coordination_accepts_mcporter_tool_result_for_matching_run() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot_with_coordination(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "mcporter-manager",
        Some(ProviderCoordinationConfig {
            mode: CoordinationMode::McporterMcp,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: Some("mcporter".to_string()),
        }),
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-mcporter".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-mcporter".to_string(),
            bcs_session_id: Some("group-mcporter:session".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events/coordination")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-mcporter",
                        "tool_call_id": "tool-1",
                        "kind": "tool_result",
                        "tool_name": "mcporter",
                        "result_text": "mcporter log\n{\"__bcs_coordination__\":true,\"v\":1,\"tool\":\"bcs_assign_task\",\"arguments\":{\"target_bot\":\"worker-a\",\"message\":\"review this file\"},\"status\":\"received\"}"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["processed"], true);
    assert_eq!(body["duplicate"], false);

    let dispatches = message_flow.task_dispatches.lock().await;
    assert_eq!(dispatches.len(), 1);
    let cmd = &dispatches[0];
    assert_eq!(cmd.driver_bot_id, registered.bot_uuid);
    assert_eq!(cmd.group_id, "group-mcporter");
    assert_eq!(cmd.target_bot_id, "worker-a");
    assert_eq!(cmd.payload["message"], "review this file");
    assert_eq!(cmd.payload["bcs_session_id"], "group-mcporter:session");
}

#[tokio::test]
async fn bot_coordination_accepts_native_mcp_intent_for_matching_run() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot_with_coordination(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "manager-v2",
        Some(ProviderCoordinationConfig {
            mode: CoordinationMode::NativeMcp,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: None,
        }),
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-native-mcp".to_string(),
            bot_id: registered.bot_uuid.clone(),
            group_id: "group-native-mcp".to_string(),
            bcs_session_id: Some("group-native-mcp:session".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events/coordination")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-native-mcp",
                        "tool_call_id": "tool-1",
                        "kind": "coordination_intent",
                        "mcp_server": "bcs",
                        "intent": {
                            "v": 1,
                            "tool": "bcs_assign_task",
                            "arguments": {
                                "target_bot": "worker-a",
                                "message": "review this file"
                            }
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["ok"], true);
    assert_eq!(body["processed"], true);
    assert_eq!(body["duplicate"], false);

    let dispatches = message_flow.task_dispatches.lock().await;
    assert_eq!(dispatches.len(), 1);
    let cmd = &dispatches[0];
    assert_eq!(cmd.driver_bot_id, registered.bot_uuid);
    assert_eq!(cmd.group_id, "group-native-mcp");
    assert_eq!(cmd.target_bot_id, "worker-a");
    assert_eq!(cmd.payload["message"], "review this file");
    assert_eq!(cmd.payload["bcs_session_id"], "group-native-mcp:session");
}

#[tokio::test]
async fn bot_coordination_rejects_native_tool_with_mcp_server() {
    let TestApp {
        app,
        registry: _,
        provider_core,
        provider_bot_core,
        run_context,
        message_flow,
        _temp_dir,
    } = test_app(Arc::new(StaticAgentpassResolver::default()));
    let registered = register_provider_bot_with_coordination(
        provider_core.as_ref(),
        provider_bot_core.as_ref(),
        ProviderAuthMode::StaticBearer,
        "native-tool-bot",
        Some(ProviderCoordinationConfig {
            mode: CoordinationMode::NativeTool,
            mcp_server: None,
            mcporter_command: None,
        }),
    )
    .await;
    let token = registered.bot_runtime_token.expect("runtime token");
    run_context
        .put_context(BotRunContext {
            run_id: "run-native-tool-mismatch".to_string(),
            bot_id: registered.bot_uuid,
            group_id: "group-native-tool".to_string(),
            bcs_session_id: Some("group-native-tool:session".to_string()),
            deadline_ms: bcs_protocol::now_ms() + 60_000,
            terminal: false,
        })
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bot/events/coordination")
                .header("content-type", "application/json")
                .header("X-BCN-Provider-Id", registered.provider_id.as_str())
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(
                    json!({
                        "run_id": "run-native-tool-mismatch",
                        "tool_call_id": "tool-1",
                        "kind": "coordination_intent",
                        "mcp_server": "bcs",
                        "intent": {
                            "v": 1,
                            "tool": "bcs_send_task_message",
                            "arguments": {
                                "message": "done"
                            }
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["error"], "invalid_request");
    assert!(message_flow.task_messages.lock().await.is_empty());
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
            "https://provider.example.com/bcs/webhook".to_string(),
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
            "https://provider.example.com/bcs/webhook".to_string(),
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
}

#[derive(Default)]
struct RecordingMessageFlow {
    events: Mutex<Vec<BotEventCommand>>,
    task_dispatches: Mutex<Vec<TaskDispatchCommand>>,
    task_messages: Mutex<Vec<TaskMessageCommand>>,
    task_completes: Mutex<Vec<TaskCompleteCommand>>,
    fail_next: Mutex<bool>,
}

#[async_trait::async_trait]
impl MessageFlowService for RecordingMessageFlow {
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
