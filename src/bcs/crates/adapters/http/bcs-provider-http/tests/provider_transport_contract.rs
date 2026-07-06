use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex as StdMutex, OnceLock},
};

use axum::{
    Json, Router,
    extract::State,
    http::HeaderMap,
    routing::post,
};
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_provider_http::HttpProviderTransport;
use bcs_protocol::{BCN_TRANSPORT_HEADER, BcsFrame, BotDeliveryKind, RequestFrame};
use bcs_service_api::{
    BotDeliveryCommand, BotDeliveryPort, GroupHistoryBotRequestPort, ProviderTransportPreference,
};
use serde_json::{Value, json};
use tokio::sync::Mutex;
use tracing::field::{Field, Visit};
use tracing_subscriber::{Layer, layer::Context, prelude::*};

type CapturedState = Arc<Mutex<Option<CapturedRequest>>>;

#[derive(Debug, Clone)]
struct CapturedRequest {
    authorization: Option<String>,
    accept: Option<String>,
    transport: Option<String>,
    protocol_version: Option<String>,
    message_id: Option<String>,
    timestamp: Option<String>,
    legacy_protocol_version: Option<String>,
    body: Value,
}

#[derive(Debug, Clone)]
struct CapturedLogEvent {
    fields: BTreeMap<String, String>,
}

impl CapturedLogEvent {
    fn field(&self, name: &str) -> Option<String> {
        self.fields
            .get(name)
            .map(|value| value.trim_matches('"').to_string())
    }
}

#[derive(Clone)]
struct CaptureLayer {
    events: Arc<StdMutex<Vec<CapturedLogEvent>>>,
}

impl<S> Layer<S> for CaptureLayer
where
    S: tracing::Subscriber,
{
    fn on_event(&self, event: &tracing::Event<'_>, _ctx: Context<'_, S>) {
        let mut visitor = FieldVisitor::default();
        event.record(&mut visitor);
        if visitor
            .fields
            .get("message")
            .is_some_and(|message| {
                message.trim_matches('"') == "provider downlink: history response"
            })
        {
            self.events
                .lock()
                .unwrap()
                .push(CapturedLogEvent {
                    fields: visitor.fields,
                });
        }
    }
}

#[derive(Default)]
struct FieldVisitor {
    fields: BTreeMap<String, String>,
}

impl Visit for FieldVisitor {
    fn record_str(&mut self, field: &Field, value: &str) {
        self.fields.insert(field.name().to_string(), value.to_string());
    }

    fn record_bool(&mut self, field: &Field, value: bool) {
        self.fields.insert(field.name().to_string(), value.to_string());
    }

    fn record_i64(&mut self, field: &Field, value: i64) {
        self.fields.insert(field.name().to_string(), value.to_string());
    }

    fn record_u64(&mut self, field: &Field, value: u64) {
        self.fields.insert(field.name().to_string(), value.to_string());
    }

    fn record_debug(&mut self, field: &Field, value: &dyn std::fmt::Debug) {
        self.fields
            .insert(field.name().to_string(), format!("{value:?}"));
    }
}

fn install_log_capture() -> Arc<StdMutex<Vec<CapturedLogEvent>>> {
    static EVENTS: OnceLock<Arc<StdMutex<Vec<CapturedLogEvent>>>> = OnceLock::new();

    EVENTS
        .get_or_init(|| {
            let events = Arc::new(StdMutex::new(Vec::new()));
            let subscriber = tracing_subscriber::registry().with(CaptureLayer {
                events: events.clone(),
            });
            tracing::subscriber::set_global_default(subscriber).expect("install tracing capture");
            events
        })
        .clone()
}

#[tokio::test]
async fn provider_delivery_posts_bearer_token_and_chat_send_body() {
    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_ack))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let result = transport
        .deliver(BotDeliveryCommand {
            target: provider_target(webhook_url),
            run_id: "run-1".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "run-1",
                "chat.send",
                Some(json!({
                    "session_key": "group:abc123",
                    "bcs_session_id": "group-1:feedbeef",
                    "bcs_group_id": "group-1",
                    "channel": {
                        "user_id": "sender-bot-id",
                        "actor_id": "sender-bot-id",
                        "actor_name": "Sender Bot"
                    },
                    "message": {
                        "text": "hello"
                    },
                    "timeout_ms": 1_800_000,
                    "tags": ["tag1", "tag2"]
                })),
            )),
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
        })
        .await
        .unwrap();

    assert!(result.delivered);
    let request = captured.lock().await.clone().unwrap();
    assert_eq!(request.authorization.as_deref(), Some("Bearer secret-b2p"));
    assert_eq!(request.protocol_version.as_deref(), Some("1.0"));
    assert!(request.message_id.as_deref().is_some_and(|id| !id.is_empty()));
    assert!(request.timestamp.as_deref().is_some_and(|ts| !ts.is_empty()));
    assert_eq!(request.legacy_protocol_version.as_deref(), None);
    assert_eq!(request.body["id"], "run-1");
    assert!(request.body.get("run_id").is_none());
    assert_eq!(request.body["method"], "chat.send");
    assert_eq!(request.body["session_id"], "group-1:feedbeef");
    assert!(request.body.get("session_key").is_none());
    assert_eq!(request.body["bcn_group_id"], "group-1");
    assert!(request.body.get("bcs_group_id").is_none());
    assert_eq!(request.body["to_bot"]["provider_id"], "provider-1");
    assert_eq!(request.body["to_bot"]["provider_bot_ref"], "reviewer-v2");
    assert_eq!(request.body["to_bot"]["tags"], json!(["tag1", "tag2"]));
    assert_eq!(request.body["from"]["kind"], "bot");
    assert_eq!(request.body["from"]["name"], "Sender Bot");
    assert_eq!(request.body["from"]["actor_id"], "sender-bot-id");
    assert_eq!(request.body["message"]["text"], "hello");
    assert_eq!(request.body["timeout_ms"], 1_800_000);
    assert!(request.body.get("extensions").is_none());

    server.abort();
}

#[tokio::test]
async fn provider_delivery_forwards_extensions_when_present() {
    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_ack))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    transport
        .deliver(BotDeliveryCommand {
            target: provider_target(webhook_url),
            run_id: "run-detached".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "run-detached",
                "chat.send",
                Some(json!({
                    "session_key": "group:abc123",
                    "bcs_session_id": "group-1:feedbeef",
                    "bcs_group_id": "group-1",
                    "channel": {
                        "actor_id": "sender-bot-id",
                        "actor_name": "Sender Bot"
                    },
                    "message": {
                        "text": "hello"
                    },
                    "timeout_ms": 60_000,
                    "extensions": {
                        "caller_wait_mode": "detached"
                    }
                })),
            )),
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
        })
        .await
        .unwrap();

    let request = captured.lock().await.clone().unwrap();
    assert_eq!(
        request.body["extensions"]["caller_wait_mode"],
        json!("detached")
    );

    server.abort();
}

#[tokio::test]
async fn provider_delivery_rejects_private_webhook_url_before_request() {
    let transport = HttpProviderTransport::new();
    let err = transport
        .deliver(BotDeliveryCommand {
            target: provider_target("http://127.0.0.1:1/webhook".to_string()),
            run_id: "run-private-url".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "run-private-url",
                "chat.send",
                Some(json!({
                    "bcs_group_id": "group-1",
                    "message": {
                        "text": "hello"
                    }
                })),
            )),
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
        })
        .await
        .expect_err("private webhook URL should be rejected before request");

    assert!(err.to_string().contains("provider webhook_url is not allowed"));
}

#[tokio::test]
async fn provider_delivery_falls_back_to_actor_id_for_sender_name() {
    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_ack))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let result = transport
        .deliver(BotDeliveryCommand {
            target: provider_target(webhook_url),
            run_id: "run-actor-id".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "run-actor-id",
                "chat.send",
                Some(json!({
                    "session_key": "group:abc123",
                    "bcs_session_id": "group-1:feedbeef",
                    "bcs_group_id": "group-1",
                    "channel": {
                        "user_id": "legacy-user-name",
                        "actor_id": "sender-bot-id"
                    },
                    "session_context": {
                        "from": "Session Sender(sender-bot-id)"
                    },
                    "from": {
                        "name": "Top Level Sender"
                    },
                    "message": {
                        "text": "hello"
                    }
                })),
            )),
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
        })
        .await
        .unwrap();

    assert!(result.delivered);
    let request = captured.lock().await.clone().unwrap();
    assert_eq!(request.body["from"]["kind"], "bot");
    assert_eq!(request.body["from"]["name"], "sender-bot-id");
    assert_eq!(request.body["from"]["actor_id"], "sender-bot-id");
    assert_eq!(request.body["timeout_ms"], 3_600_000);

    server.abort();
}

#[tokio::test]
async fn provider_history_returns_messages_payload_for_converter() {
    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_history))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let payload = transport
        .send_history_request(
            provider_target(webhook_url),
            "chat.history",
            json!({
                "session_key": "group:abc123",
                "bcs_session_id": "group-1:feedbeef",
                "bcs_group_id": "group-1",
                "before": 1710960050000_u64,
                "limit": 20
            }),
            30_000,
        )
        .await
        .unwrap();

    assert_eq!(payload["messages"][0]["role"], "assistant");
    assert_eq!(payload["messages"][0]["content"], "done");
    assert_eq!(payload["messages"][0]["timestamp"], 1710960050000_u64);
    assert_eq!(payload["has_more"], false);

    let request = captured.lock().await.clone().unwrap();
    assert_eq!(request.protocol_version.as_deref(), Some("1.0"));
    assert!(request.message_id.as_deref().is_some_and(|id| !id.is_empty()));
    assert!(request.timestamp.as_deref().is_some_and(|ts| !ts.is_empty()));
    assert_eq!(request.legacy_protocol_version.as_deref(), None);
    assert!(request.body["id"].as_str().is_some_and(|id| !id.is_empty()));
    assert!(request.body.get("run_id").is_none());
    assert_eq!(request.body["method"], "chat.history");
    assert_eq!(request.body["session_id"], "group-1:feedbeef");
    assert!(request.body.get("session_key").is_none());
    assert_eq!(request.body["bcn_group_id"], "group-1");
    assert!(request.body.get("bcs_group_id").is_none());
    assert_eq!(request.body["before"], 1710960050000_u64);
    assert_eq!(request.body["limit"], 20);

    server.abort();
}

#[tokio::test]
async fn provider_history_logs_provider_bot_group_and_history_response() {
    let logs = install_log_capture();
    logs.lock().unwrap().clear();

    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_history))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    transport
        .send_history_request(
            provider_target(webhook_url),
            "chat.history",
            json!({
                "session_key": "group:log123",
                "bcs_session_id": "group-log:cafebabe",
                "bcs_group_id": "group-log",
                "before": 1710960050000_u64,
                "limit": 20
            }),
            30_000,
        )
        .await
        .unwrap();

    let events = logs.lock().unwrap();
    let event = events
        .iter()
        .find(|event| event.field("session_id").as_deref() == Some("group-log:cafebabe"))
        .expect("history response log");

    assert_eq!(event.field("target_bot_id").as_deref(), Some("bot-provider"));
    assert_eq!(event.field("provider_id").as_deref(), Some("provider-1"));
    assert_eq!(event.field("provider_bot_ref").as_deref(), Some("reviewer-v2"));
    assert_eq!(event.field("method").as_deref(), Some("chat.history"));
    assert_eq!(event.field("bcn_group_id").as_deref(), Some("group-log"));
    assert_eq!(event.field("message_count").as_deref(), Some("1"));
    assert_eq!(event.field("has_more").as_deref(), Some("false"));

    let history_body: Value = serde_json::from_str(
        event
            .field("history_body")
            .as_deref()
            .expect("history body log field"),
    )
    .expect("history body should be json");
    assert_eq!(history_body["session_id"], "group-1:feedbeef");
    assert_eq!(history_body["messages"][0]["content"], "done");
    assert_eq!(history_body["messages"][0]["timestamp"], 1710960050000_u64);

    server.abort();
}

#[tokio::test]
async fn provider_delivery_posts_chat_inject_body_with_bcn_group_id() {
    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_ack))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let result = transport
        .deliver(BotDeliveryCommand {
            target: provider_target(webhook_url),
            run_id: "run-2".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "run-2",
                "chat.inject",
                Some(json!({
                    "session_key": "group:abc123",
                    "bcs_session_id": "group-1:feedbeef",
                    "bcs_group_id": "group-1",
                    "from": "human_421548",
                    "channel": {
                        "user_id": "sender-bot-id",
                        "actor_id": "sender-bot-id",
                        "actor_name": "Sender Bot"
                    },
                    "message": {
                        "text": "observe"
                    }
                })),
            )),
            delivery_kind: BotDeliveryKind::Inject,
            provider_transport: Default::default(),
        })
        .await
        .unwrap();

    assert!(result.delivered);
    let request = captured.lock().await.clone().unwrap();
    assert_eq!(request.body["id"], "run-2");
    assert_eq!(request.body["method"], "chat.inject");
    assert_eq!(request.body["session_id"], "group-1:feedbeef");
    assert_eq!(request.body["bcn_group_id"], "group-1");
    assert!(request.body.get("bcs_group_id").is_none());
    assert_eq!(request.body["from"]["kind"], "bot");
    assert_eq!(request.body["from"]["name"], "Sender Bot");
    assert_eq!(request.body["message"]["text"], "observe");

    server.abort();
}

#[tokio::test]
async fn provider_delivery_rejects_chat_send_when_frame_id_differs_from_run_id() {
    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_ack))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let err = transport
        .deliver(BotDeliveryCommand {
            target: provider_target(webhook_url),
            run_id: "run-1".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "frame-1",
                "chat.send",
                Some(json!({
                    "session_key": "group:abc123",
                    "message": {
                        "text": "hello"
                    }
                })),
            )),
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: Default::default(),
        })
        .await
        .expect_err("chat.send frame id must match run_id");

    assert!(err.to_string().contains("chat.send frame id"));
    assert!(captured.lock().await.is_none());

    server.abort();
}

async fn capture_ack(
    State(captured): State<CapturedState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Json<Value> {
    capture(captured, headers, body).await;
    Json(json!({
        "ok": true
    }))
}

async fn capture_history(
    State(captured): State<CapturedState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Json<Value> {
    capture(captured, headers, body).await;
    Json(json!({
        "ok": true,
        "session_id": "group-1:feedbeef",
        "messages": [
            {
                "role": "assistant",
                "content": "done",
                "timestamp": 1710960050000_u64
            }
        ],
        "has_more": false
    }))
}

async fn capture(captured: CapturedState, headers: HeaderMap, body: Value) {
    let authorization = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let accept = headers
        .get(axum::http::header::ACCEPT)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let transport = headers
        .get(BCN_TRANSPORT_HEADER)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let protocol_version = headers
        .get("x-bcn-protocol-version")
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let message_id = headers
        .get("x-bcn-message-id")
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let timestamp = headers
        .get("x-bcn-timestamp")
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let legacy_protocol_version = headers
        .get("x-bcs-protocol-version")
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    *captured.lock().await = Some(CapturedRequest {
        authorization,
        accept,
        transport,
        protocol_version,
        message_id,
        timestamp,
        legacy_protocol_version,
        body,
    });
}

async fn spawn_server(app: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .unwrap();
    let addr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    (format!("http://{addr}/webhook"), handle)
}

fn provider_target(webhook_url: String) -> BotDeliveryTarget {
    BotDeliveryTarget::HttpProvider {
        bot_id: "bot-provider".to_string(),
        provider_id: "provider-1".to_string(),
        provider_bot_ref: "reviewer-v2".to_string(),
        webhook_url,
        bcs_to_provider_token: RedactedToken::new("secret-b2p"),
        protocol_version: "1.0".to_string(),
    }
}

fn provider_target_v2(webhook_url: String) -> BotDeliveryTarget {
    BotDeliveryTarget::HttpProvider {
        bot_id: "bot-provider".to_string(),
        provider_id: "provider-1".to_string(),
        provider_bot_ref: "reviewer-v2".to_string(),
        webhook_url,
        bcs_to_provider_token: RedactedToken::new("secret-b2p"),
        protocol_version: "2.0".to_string(),
    }
}

/// A 2.0 provider that returns a JSON ack (Content-Type: application/json) for
/// inject/abort/history — i.e. the callback transport, not SSE. Before D5 was
/// relaxed, deliver() rejected any 2.0 non-SSE response with InternalError,
/// which broke every chat.inject (system messages). This verifies the ack is
/// now accepted as a successful delivery.
#[tokio::test]
async fn provider_delivery_2_0_inject_accepts_json_ack() {
    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_ack))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let result = transport
        .deliver(BotDeliveryCommand {
            target: provider_target_v2(webhook_url),
            run_id: "run-inj".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "run-inj",
                "chat.inject",
                Some(json!({
                    "bcs_session_id": "group-1:feedbeef",
                    "bcs_group_id": "group-1",
                    "message": { "text": "user joined" }
                })),
            )),
            delivery_kind: BotDeliveryKind::Inject,
            provider_transport: Default::default(),
        })
        .await
        .expect("2.0 inject JSON ack must be accepted, not InternalError");

    assert!(result.delivered, "2.0 inject JSON ack should mark delivered");
    assert!(result.error.is_none());
    let request = captured.lock().await.clone().unwrap();
    assert_eq!(request.protocol_version.as_deref(), Some("2.0"));
    assert_eq!(request.accept.as_deref(), Some("application/json"));
    assert_eq!(request.transport.as_deref(), Some("callback"));
    assert_eq!(request.body["method"], "chat.inject");

    server.abort();
}

#[tokio::test]
async fn provider_delivery_2_0_chat_send_with_sse_preference_advertises_sse() {
    let captured: CapturedState = Arc::new(Mutex::new(None));
    let app = Router::new()
        .route("/webhook", post(capture_ack))
        .with_state(captured.clone());
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let result = transport
        .deliver(BotDeliveryCommand {
            target: provider_target_v2(webhook_url),
            run_id: "run-sse".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "run-sse",
                "chat.send",
                Some(json!({
                    "bcs_session_id": "group-1:feedbeef",
                    "bcs_group_id": "group-1",
                    "message": { "text": "hello" }
                })),
            )),
            delivery_kind: BotDeliveryKind::Send,
            provider_transport: ProviderTransportPreference::CallbackSse,
        })
        .await
        .expect("2.0 send JSON ack should be accepted while advertising SSE");

    assert!(result.delivered);
    let request = captured.lock().await.clone().unwrap();
    assert_eq!(request.protocol_version.as_deref(), Some("2.0"));
    assert_eq!(
        request.accept.as_deref(),
        Some("text/event-stream, application/json")
    );
    assert_eq!(request.transport.as_deref(), Some("sse"));
    assert_eq!(request.body["method"], "chat.send");

    server.abort();
}

/// A 2.0 provider that returns `{"ok": false, "error": ...}` for inject should
/// surface as a non-delivered result with the provider's error, not silently
/// succeed.
#[tokio::test]
async fn provider_delivery_2_0_inject_propagates_json_rejection() {
    let app = Router::new().route(
        "/webhook",
        post(|| async { Json(json!({ "ok": false, "error": "bot offline" })) }),
    );
    let (webhook_url, server) = spawn_server(app).await;

    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let result = transport
        .deliver(BotDeliveryCommand {
            target: provider_target_v2(webhook_url),
            run_id: "run-inj2".to_string(),
            frame: BcsFrame::Request(RequestFrame::new(
                "run-inj2",
                "chat.inject",
                Some(json!({
                    "bcs_session_id": "group-1:feedbeef",
                    "bcs_group_id": "group-1",
                    "message": { "text": "user joined" }
                })),
            )),
            delivery_kind: BotDeliveryKind::Inject,
            provider_transport: Default::default(),
        })
        .await
        .expect("rejection is a normal ack, not a transport error");

    assert!(!result.delivered);
    assert!(result.error.is_some());

    server.abort();
}
