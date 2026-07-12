use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::BotCore;
use bcs_http::{
    router::build_router,
    state::{ChatRunEventPort, ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    A2aChatCommand, A2aChatOutcome, A2aChatRunService, A2aChatService, A2aRunStatus,
    AsyncA2aChatAccepted, AsyncA2aChatCommand, BlockingA2aChatCommand, BlockingA2aChatOutcome,
    BotActor, BotCapabilities, BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult,
    BotDeliveryTarget, BotRegistryCoreService, CallerContext, ChatResponseMode, ChatRunCancelCommand,
    ChatRunQueryCommand, ServiceError, ServiceResult,
};
use bcs_services_container::Services;
use serde_json::Value;
use std::collections::HashMap;
use std::future::Future;
use std::io::{self, Write};
use std::sync::Arc;
use tempfile::TempDir;
use tokio::sync::{Mutex, RwLock, mpsc};
use tower::ServiceExt;

#[derive(Default)]
struct RecordingA2aChat {
    commands: Mutex<Vec<A2aChatCommand>>,
    run_channel_froms: Mutex<Vec<Option<String>>>,
    recorded_events: Mutex<Vec<(String, String)>>,
    failed_runs: Mutex<Vec<(String, String)>>,
    not_connected_bot_id: Mutex<Option<String>>,
    not_friends_bot_ids: Mutex<Option<Vec<String>>>,
    blocking_content: Mutex<String>,
    blocking_failure: Mutex<Option<String>>,
    blocking_returns_error: Mutex<bool>,
}

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

#[async_trait::async_trait]
impl A2aChatService for RecordingA2aChat {
    async fn chat(&self, cmd: A2aChatCommand) -> ServiceResult<A2aChatOutcome> {
        if let Some(bot_id) = self.not_connected_bot_id.lock().await.clone() {
            return Err(ServiceError::BotNotConnected(bot_id));
        }
        let run_id = cmd
            .run_id
            .clone()
            .unwrap_or_else(|| "generated-run".to_string());
        let status = if cmd.async_mode { "pending" } else { "started" }.to_string();
        self.commands.lock().await.push(cmd);
        Ok(A2aChatOutcome {
            run_id,
            status,
            response: None,
        })
    }

    async fn get_run(&self, _caller: CallerContext, run_id: &str) -> ServiceResult<A2aRunStatus> {
        let events = self.recorded_events.lock().await;
        let mut content = events
            .iter()
            .rev()
            .find(|(recorded_run_id, _)| recorded_run_id == run_id)
            .and_then(|(_, event)| content_text(event))
            .unwrap_or_default();
        let has_final = events
            .iter()
            .any(|(recorded_run_id, event)| recorded_run_id == run_id && is_final_event(event));
        drop(events);

        let failed_error = self
            .failed_runs
            .lock()
            .await
            .iter()
            .rev()
            .find(|(failed_run_id, _)| failed_run_id == run_id)
            .map(|(_, error)| error.clone());
        if content.is_empty() {
            content = self.blocking_content.lock().await.clone();
        }
        let state = if failed_error.is_some() && content.is_empty() {
            "failed"
        } else if failed_error.is_some() || has_final {
            "completed"
        } else {
            "running"
        };
        Ok(A2aRunStatus {
            run_id: run_id.to_string(),
            status: state.to_string(),
            response: Some(serde_json::json!({
                "content": content,
                "state": state,
                "error_message": failed_error,
                "is_terminal": state != "running",
            })),
        })
    }

    async fn wait_run(
        &self,
        _caller: CallerContext,
        _run_id: &str,
        _since_version: u64,
        _wait_ms: u64,
    ) -> ServiceResult<A2aRunStatus> {
        unreachable!("not used by this contract")
    }

    async fn record_run_event(&self, run_id: &str, event_json: &str) -> ServiceResult<bool> {
        self.recorded_events
            .lock()
            .await
            .push((run_id.to_string(), event_json.to_string()));
        Ok(is_final_event(event_json))
    }

    async fn fail_run_if_open(&self, run_id: &str, error: &str) -> ServiceResult<bool> {
        self.failed_runs
            .lock()
            .await
            .push((run_id.to_string(), error.to_string()));
        Ok(true)
    }

    async fn cancel_run(
        &self,
        _caller: CallerContext,
        _run_id: &str,
    ) -> ServiceResult<A2aRunStatus> {
        unreachable!("not used by this contract")
    }

    async fn cleanup_expired(
        &self,
        _now_ms: u64,
        _retention_ms: u64,
    ) -> ServiceResult<(Vec<String>, Vec<String>)> {
        Ok((Vec::new(), Vec::new()))
    }
}

#[async_trait::async_trait]
impl A2aChatRunService for RecordingA2aChat {
    async fn run_blocking_chat(
        &self,
        cmd: BlockingA2aChatCommand,
    ) -> ServiceResult<BlockingA2aChatOutcome> {
        if let Some(bot_id) = self.not_connected_bot_id.lock().await.clone() {
            return Err(ServiceError::BotNotConnected(bot_id));
        }
        if let Some(bot_ids) = self.not_friends_bot_ids.lock().await.clone() {
            return Err(ServiceError::NotFriends(bot_ids));
        }
        self.commands.lock().await.push(A2aChatCommand {
            caller: cmd.caller,
            target_bot_id: cmd.target_bot_id.clone(),
            message: cmd.message,
            from_actor_id: cmd.from_actor_id,
            authenticated_staff_id: cmd.authenticated_staff_id,
            run_id: Some(cmd.run_id.clone()),
            async_mode: false,
            session_key: Some(cmd.session_key.clone()),
            timeout_ms: Some(cmd.timeout_ms),
            client: cmd.client,
            tags: cmd.tags,
            response_mode: cmd.response_mode,
            caller_wait_mode: None,
            organization_code: cmd.organization_code,
        });
        self.run_channel_froms
            .lock()
            .await
            .push(cmd.run_channel_from);

        if let Some(error) = self.blocking_failure.lock().await.clone() {
            self.failed_runs
                .lock()
                .await
                .push((cmd.run_id.clone(), error.clone()));
            if *self.blocking_returns_error.lock().await {
                return Err(ServiceError::InternalError(error));
            }
        }

        Ok(BlockingA2aChatOutcome {
            delivered: true,
            bot_uuid: cmd.target_bot_id,
            session_id: cmd.session_key,
            content: self.blocking_content.lock().await.clone(),
        })
    }

    async fn start_async_chat(
        &self,
        cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        if let Some(bot_id) = self.not_connected_bot_id.lock().await.clone() {
            return Err(ServiceError::BotNotConnected(bot_id));
        }
        if let Some(bot_ids) = self.not_friends_bot_ids.lock().await.clone() {
            return Err(ServiceError::NotFriends(bot_ids));
        }
        self.commands.lock().await.push(A2aChatCommand {
            caller: cmd.caller,
            target_bot_id: cmd.target_bot_id.clone(),
            message: cmd.message,
            from_actor_id: cmd.from_actor_id,
            authenticated_staff_id: cmd.authenticated_staff_id,
            run_id: Some(cmd.run_id.clone()),
            async_mode: true,
            session_key: Some(cmd.session_key.clone()),
            timeout_ms: Some(cmd.timeout_ms),
            client: cmd.client,
            tags: cmd.tags,
            response_mode: cmd.response_mode,
            caller_wait_mode: cmd.caller_wait_mode,
            organization_code: cmd.organization_code,
        });
        self.run_channel_froms
            .lock()
            .await
            .push(cmd.run_channel_from);
        Ok(AsyncA2aChatAccepted {
            run_id: cmd.run_id,
            bot_uuid: cmd.target_bot_id,
            session_id: cmd.session_key,
            status: "pending".to_string(),
            expires_at_ms: 1,
        })
    }

    async fn get_run(&self, cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> {
        A2aChatService::get_run(self, cmd.caller, &cmd.run_id).await
    }

    async fn cancel_run(&self, cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> {
        A2aChatService::cancel_run(self, cmd.caller, &cmd.run_id).await
    }
}

fn content_text(event: &str) -> Option<String> {
    let value: Value = serde_json::from_str(event).ok()?;
    value
        .get("payload")
        .and_then(|payload| payload.get("message"))
        .and_then(|message| message.get("content"))
        .and_then(|content| content.as_array())
        .and_then(|content| content.first())
        .and_then(|block| block.get("text"))
        .and_then(|text| text.as_str())
        .map(str::to_string)
}

fn is_final_event(event: &str) -> bool {
    serde_json::from_str::<Value>(event)
        .ok()
        .and_then(|value| {
            value
                .get("payload")
                .and_then(|payload| payload.get("state"))
                .and_then(|state| state.as_str())
                .map(|state| state == "final")
        })
        .unwrap_or(false)
}

#[derive(Default)]
struct RecordingBotDelivery;

#[async_trait::async_trait]
impl BotDeliveryPort for RecordingBotDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered: true,
            error: None,
        })
    }
}

struct RecordingChatRunEvents {
    events: Vec<String>,
    keep_open_after_send: bool,
    registrations: Mutex<Vec<(String, String, Option<String>, Option<String>)>>,
    unregistered: Mutex<Vec<String>>,
    open_senders: RwLock<HashMap<String, mpsc::Sender<String>>>,
}

#[async_trait::async_trait]
impl ChatRunEventPort for RecordingChatRunEvents {
    async fn register(
        &self,
        run_id: String,
        session_key: String,
        sender: mpsc::Sender<String>,
        source: Option<String>,
        from: Option<String>,
    ) {
        self.registrations
            .lock()
            .await
            .push((run_id.clone(), session_key, source, from));
        for event_json in &self.events {
            sender.send(event_json.clone()).await.unwrap();
        }
        if self.keep_open_after_send {
            self.open_senders.write().await.insert(run_id, sender);
        }
    }

    async fn unregister(&self, run_id: &str) {
        self.open_senders.write().await.remove(run_id);
        self.unregistered.lock().await.push(run_id.to_string());
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

async fn build_chat_app() -> (
    axum::Router,
    Arc<RecordingA2aChat>,
    Arc<RecordingChatRunEvents>,
) {
    build_chat_app_with_events(vec![final_event("pong")], false).await
}

async fn build_chat_app_with_events(
    events: Vec<String>,
    keep_open_after_send: bool,
) -> (
    axum::Router,
    Arc<RecordingA2aChat>,
    Arc<RecordingChatRunEvents>,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["caller-bot", "target-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: if bot_id == "target-bot" {
                        "protected".to_string()
                    } else {
                        "public".to_string()
                    },
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("caller-token".to_string(), "caller-bot".to_string())
        .await;
    registry
        .save_created_by("caller-bot", "123", true)
        .await
        .unwrap();

    let a2a = Arc::new(RecordingA2aChat::default());
    let (blocking_content, blocking_failure, blocking_returns_error) =
        blocking_mode_from_events(&events, keep_open_after_send);
    *a2a.blocking_content.lock().await = blocking_content;
    *a2a.blocking_failure.lock().await = blocking_failure;
    *a2a.blocking_returns_error.lock().await = blocking_returns_error;
    let run_events = Arc::new(RecordingChatRunEvents {
        events,
        keep_open_after_send,
        registrations: Mutex::new(Vec::new()),
        unregistered: Mutex::new(Vec::new()),
        open_senders: RwLock::new(HashMap::new()),
    });
    let services = Services::builder()
        .registry(registry)
        .a2a_chat(a2a.clone())
        .a2a_chat_runs(a2a.clone())
        .bot_delivery(Arc::new(RecordingBotDelivery))
        .build_for_test();
    let chain = static_auth_chain("123", "Owner");
    let app = build_router(
        HttpAppState::new(services)
            .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain)))
            .with_chat_run_events(run_events.clone()),
    );
    (app, a2a, run_events)
}

fn blocking_mode_from_events(
    events: &[String],
    keep_open_after_send: bool,
) -> (String, Option<String>, bool) {
    if events.is_empty() && !keep_open_after_send {
        return (
            String::new(),
            Some("Bot channel closed without response".to_string()),
            true,
        );
    }
    let content = events
        .iter()
        .filter_map(|event| content_text(event))
        .collect::<String>();
    if keep_open_after_send {
        (
            content,
            Some("Timeout waiting for bot response".to_string()),
            false,
        )
    } else {
        (content, None, false)
    }
}

fn final_event(text: &str) -> String {
    chat_event("final", text)
}

fn delta_event(text: &str) -> String {
    chat_event("delta", text)
}

fn chat_event(state: &str, text: &str) -> String {
    serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": state,
            "message": {
                "content": [{"type": "text", "text": text}]
            }
        }
    })
    .to_string()
}

async fn build_not_connected_app() -> axum::Router {
    let (app, a2a, _run_events) = build_chat_app().await;
    *a2a.not_connected_bot_id.lock().await = Some("target-bot".to_string());
    app
}

async fn build_not_friends_app() -> axum::Router {
    let (app, a2a, _run_events) = build_chat_app().await;
    *a2a.not_friends_bot_ids.lock().await = Some(vec!["target-bot".to_string()]);
    app
}

#[tokio::test]
async fn bot_chat_waits_for_final_event_and_preserves_response_shape() {
    let (app, a2a, _run_events) = build_chat_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/target-bot/chat")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "ping",
                        "from": "human_123",
                        "session_id": "stable-session"
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
    assert_eq!(json["bot_uuid"], "target-bot");
    assert_eq!(json["session_id"], "stable-session");
    assert_eq!(json["response"]["content"], "pong");

    let commands = a2a.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert!(matches!(
        &commands[0].caller,
        CallerContext::Bot(bot) if bot.bot_uuid == "caller-bot"
    ));
    assert_eq!(commands[0].target_bot_id, "target-bot");
    assert_eq!(commands[0].message, "ping");
    assert!(commands[0].run_id.as_deref().is_some_and(|id| id.len() > 8));
    assert_eq!(commands[0].async_mode, false);
    assert_eq!(commands[0].session_key.as_deref(), Some("stable-session"));
    assert_eq!(commands[0].timeout_ms, Some(300_000));
    assert_eq!(commands[0].client, None);
    assert_eq!(commands[0].from_actor_id.as_deref(), Some("human_123"));
    assert_eq!(commands[0].authenticated_staff_id.as_deref(), Some("123"));
    assert_eq!(commands[0].organization_code, None);
    assert_eq!(
        a2a.run_channel_froms.lock().await.as_slice(),
        &[Some("human_123".to_string())]
    );
}

#[tokio::test]
async fn bot_chat_async_returns_accepted_and_preserves_cli_session_fallback() {
    let (app, a2a, _run_events) = build_chat_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/target-bot/chat-async")
                .header("authorization", "Bearer caller-token")
                .header("x-bcs-client", "bcs-cli/0.1")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"message":"ping","tags":["tag1","tag2"],"response_mode":"after-last-tool-call","caller_wait_mode":"detached"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "target-bot");
    assert_eq!(json["status"], "pending");
    assert!(json["run_id"].as_str().unwrap().len() > 8);
    assert!(
        json["session_id"]
            .as_str()
            .unwrap()
            .starts_with("bcs-cli:caller-bot:")
    );
    assert!(json["expires_at_ms"].as_u64().unwrap() > 0);

    let commands = a2a.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert!(matches!(
        &commands[0].caller,
        CallerContext::Bot(bot) if bot.bot_uuid == "caller-bot"
    ));
    assert_eq!(commands[0].target_bot_id, "target-bot");
    assert_eq!(commands[0].message, "ping");
    assert!(commands[0].run_id.as_deref().is_some_and(|id| id.len() > 8));
    assert_eq!(commands[0].async_mode, true);
    assert!(
        commands[0]
            .session_key
            .as_deref()
            .is_some_and(|id| id.starts_with("bcs-cli:caller-bot:"))
    );
    assert_eq!(commands[0].timeout_ms, Some(300_000));
    assert_eq!(commands[0].client.as_deref(), Some("bcs-cli/0.1"));
    assert_eq!(commands[0].from_actor_id.as_deref(), Some("user"));
    assert_eq!(commands[0].authenticated_staff_id.as_deref(), Some("123"));
    assert_eq!(commands[0].tags, vec!["tag1", "tag2"]);
    assert_eq!(commands[0].response_mode, ChatResponseMode::AfterLastToolCall);
    assert_eq!(commands[0].caller_wait_mode.as_deref(), Some("detached"));
    assert_eq!(commands[0].organization_code, None);
    assert_eq!(a2a.run_channel_froms.lock().await.as_slice(), &[None]);
}

#[tokio::test]
async fn bot_chat_forwards_organization_code_to_blocking_service() {
    let (app, a2a, _run_events) = build_chat_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/target-bot/chat")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "message": "ping",
                        "organization_code": "promo-2026"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let commands = a2a.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].organization_code.as_deref(), Some("promo-2026"));
}

#[tokio::test]
async fn bot_chat_async_forwards_organization_code_to_async_service() {
    let (app, a2a, _run_events) = build_chat_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/target-bot/chat-async")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"message":"ping","organization_code":"promo-2026"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let commands = a2a.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].organization_code.as_deref(), Some("promo-2026"));
}

#[tokio::test]
async fn bot_chat_async_logs_server_side_digest_on_success() {
    let (app, _a2a, _run_events) = build_chat_app().await;

    let logs = capture_tracing_logs(async {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bots/target-bot/chat-async")
                    .header("authorization", "Bearer caller-token")
                    .header("x-bcs-client", "bcs-cli/0.1")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"message":"ping","timeout_ms":60000}"#))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::ACCEPTED);
    })
    .await;

    assert!(logs.contains("bcs_chat_digest"));
    assert!(logs.contains("endpoint=bot_chat_async"));
    assert!(logs.contains("from_bot_id=caller-bot"));
    assert!(logs.contains("target_bot_id=target-bot"));
    assert!(logs.contains("timeout_ms=60000"));
    assert!(logs.contains("success=true"));
    assert!(logs.contains("status_code=202"));
}

#[tokio::test]
async fn bot_chat_maps_typed_not_connected_error_to_legacy_404_body() {
    let app = build_not_connected_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/target-bot/chat")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"message":"ping"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json["error"],
        "Bot 'target-bot' is not connected via WebSocket"
    );
    assert_eq!(json["status"], 404);
}

#[tokio::test]
async fn bot_chat_logs_server_side_digest_on_failure() {
    let app = build_not_connected_app().await;

    let logs = capture_tracing_logs(async {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bots/target-bot/chat")
                    .header("authorization", "Bearer caller-token")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"message":"ping"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    })
    .await;

    assert!(logs.contains("bcs_chat_digest"));
    assert!(logs.contains("endpoint=bot_chat"));
    assert!(logs.contains("from_bot_id=caller-bot"));
    assert!(logs.contains("target_bot_id=target-bot"));
    assert!(logs.contains("success=false"));
    assert!(logs.contains("status_code=404"));
    assert!(logs.contains("error_kind=BotNotConnected"));
}

#[tokio::test]
async fn bot_chat_logs_not_friends_as_business_success() {
    let app = build_not_friends_app().await;

    let logs = capture_tracing_logs(async {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bots/target-bot/chat")
                    .header("authorization", "Bearer caller-token")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"message":"ping"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    })
    .await;

    assert!(logs.contains("bcs_chat_digest"));
    assert!(logs.contains("endpoint=bot_chat"));
    assert!(logs.contains("from_bot_id=caller-bot"));
    assert!(logs.contains("target_bot_id=target-bot"));
    assert!(logs.contains("success=true"));
    assert!(logs.contains("status_code=403"));
    assert!(logs.contains("error_kind=NotFriends"));
}

#[tokio::test]
async fn bot_chat_async_logs_not_friends_as_business_success() {
    let app = build_not_friends_app().await;

    let logs = capture_tracing_logs(async {
        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/bots/target-bot/chat-async")
                    .header("authorization", "Bearer caller-token")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"message":"ping"}"#))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    })
    .await;

    assert!(logs.contains("bcs_chat_digest"));
    assert!(logs.contains("endpoint=bot_chat_async"));
    assert!(logs.contains("from_bot_id=caller-bot"));
    assert!(logs.contains("target_bot_id=target-bot"));
    assert!(logs.contains("success=true"));
    assert!(logs.contains("status_code=403"));
    assert!(logs.contains("error_kind=NotFriends"));
}

#[tokio::test]
async fn bot_chat_channel_close_without_content_fails_open_run() {
    let (app, a2a, _run_events) = build_chat_app_with_events(Vec::new(), false).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/target-bot/chat")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"message":"ping"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "Bot channel closed without response");
    assert_eq!(json["status"], 500);

    let failed_runs = a2a.failed_runs.lock().await;
    assert_eq!(failed_runs.len(), 1);
    assert_eq!(failed_runs[0].1, "Bot channel closed without response");
}

#[tokio::test]
async fn bot_chat_partial_timeout_returns_content_and_completes_open_run() {
    let (app, a2a, _run_events) =
        build_chat_app_with_events(vec![delta_event("partial")], true).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/target-bot/chat")
                .header("authorization", "Bearer caller-token")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"message":"ping","timeout_ms":5}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["response"]["content"], "partial");

    {
        let failed_runs = a2a.failed_runs.lock().await;
        assert_eq!(failed_runs.len(), 1);
        assert_eq!(failed_runs[0].1, "Timeout waiting for bot response");
    }

    let commands = a2a.commands.lock().await;
    let run_id = commands[0].run_id.as_deref().unwrap();
    let status = A2aChatService::get_run(
        a2a.as_ref(),
        CallerContext::Bot(BotActor {
            bot_uuid: "caller-bot".to_string(),
        }),
        run_id,
    )
    .await
    .unwrap();
    assert_eq!(status.status, "completed");
    assert_eq!(
        status
            .response
            .as_ref()
            .and_then(|response| response.get("is_terminal"))
            .and_then(|value| value.as_bool()),
        Some(true)
    );
}
