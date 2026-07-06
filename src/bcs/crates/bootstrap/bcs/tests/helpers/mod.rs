//! Shared test helpers for BCS integration tests.
//!
//! Provides `MockBot` — a WebSocket client that mimics the BCN plugin's
//! connection protocol (bot.connect → HTTP onboard → recv/send frames).

#![allow(dead_code)]

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, tungstenite::Message};

use bcs::BcsServer;
use bcs::server::BcsServerState;

// ── Server helpers ────────────────────────────────────────────────────────────

pub fn create_temp_bots_dir() -> tempfile::TempDir {
    tempfile::TempDir::new().expect("Failed to create temp dir")
}

use bcs::{BcsConfig, LoggingConfig, MessageHistoryConfig, MistConfig};

pub fn create_test_config(bots_dir: &PathBuf) -> BcsConfig {
    BcsConfig {
        bind: "127.0.0.1".to_string(),
        port: 0,
        bots_base_dir: bots_dir.clone(),
        fusion_provider: None,
        llm: Default::default(),
        max_history_per_session: 100,
        dingtalk_accounts: vec![],
        auth_token: None,
        master_election: None,
        cache: Default::default(),
        database: Default::default(),
        mist: MistConfig::default(),
        collaboration: Default::default(),
        store_messages: true,
        max_groups_as_driver: 3,
        group_chat_delay_min_ms: 0,
        group_chat_delay_max_ms: 0,
        max_group_members: 5,
        max_groups_as_member: 10,
        max_group_messages: 100,
        onboard_binding_enabled: false,
        default_visibility: None,
        manifest: Default::default(),
        allowed_switch_provider_ids: Vec::new(),
        provider_stream_gray_enabled: false,
        provider_stream_gray_created_by: Vec::new(),
        strict_container_validation: false,
        bcs_endpoint: None,
        botchat_url: None,
        register_path: "/bcn/register".to_string(),
        logging: LoggingConfig::default(),
        bcsfuse: bcs_fuse_client::BcsFuseConfig::default(),
        auth_sdk: Default::default(),
        auth: Default::default(),
        cors: Default::default(),
        group_logger: None,
        async_chat_run_timeout_ms: 30 * 60 * 1_000,
        async_chat_run_retention_ms: 120 * 1_000,
        async_chat_poll_wait_max_ms: 30_000,
        async_chat_run_max_entries: 100_000,
        security_gateway: Default::default(),
        security: Default::default(),
        message_history: MessageHistoryConfig::default(),
        api_keys: vec![],
        metrics: Default::default(),
        invite: bcs::InviteConfig {
            token_secret: Some("test-invite-secret-32-bytes!!!!".to_string()),
            default_ttl_seconds: 3600,
            base_url: None,
            group_link_url: None,
            session_link_url: None,
        },
    }
}

/// One-time process-level init for auth mock env.
/// Using `std::sync::Once` avoids racing with concurrent test threads.
static INIT_AUTH_MOCK: std::sync::Once = std::sync::Once::new();

pub async fn start_test_server(
    bots_dir: &PathBuf,
) -> (SocketAddr, tokio::task::JoinHandle<Result<(), bcs::BcsError>>) {
    INIT_AUTH_MOCK.call_once(|| {
        // SAFETY: runs exactly once before any server starts handling requests.
        unsafe { std::env::set_var("BCS_AUTH_MOCK", "1") };
    });
    let config = create_test_config(bots_dir);
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    server
        .run_on_random_port()
        .await
        .expect("Failed to start server")
}

pub async fn start_test_server_with_state(
    bots_dir: &PathBuf,
) -> (
    SocketAddr,
    tokio::task::JoinHandle<Result<(), bcs::BcsError>>,
    Arc<BcsServerState>,
) {
    INIT_AUTH_MOCK.call_once(|| {
        // SAFETY: runs exactly once before any server starts handling requests.
        unsafe { std::env::set_var("BCS_AUTH_MOCK", "1") };
    });
    let config = create_test_config(bots_dir);
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
    server
        .run_on_random_port_with_state()
        .await
        .expect("Failed to start server")
}

// ── MockBot ───────────────────────────────────────────────────────────────────

type WsStream = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;

/// A mock bot that speaks the BCN WebSocket protocol.
///
/// Mirrors what `submodules/moltis/crates/bcn/src/bot.rs` does:
/// 1. Connect to `/ws/bot`
/// 2. Send `bot.connect` frame
/// 3. Receive `bot_id` + `token`
/// 4. Onboard via HTTP API (using `bcs-cli onboard` or direct HTTP call)
pub struct MockBot {
    ws: WsStream,
    pub bot_id: String,
    pub token: String,
}

impl MockBot {
    /// Connect as a brand-new bot (no token). Returns `is_new: true`.
    pub async fn connect(addr: SocketAddr) -> Self {
        let url = format!("ws://{}/ws/bot", addr);
        let (ws, _) = tokio_tungstenite::connect_async(&url)
            .await
            .expect("Failed to connect WebSocket");
        let mut bot = Self { ws, bot_id: String::new(), token: String::new() };
        bot.do_connect(None).await;
        // Drain any onboarding messages BCS sends to new bots
        bot.drain_onboarding().await;
        bot
    }

    /// Reconnect with an existing token. Returns `is_new: false` for valid tokens.
    #[allow(dead_code)]
    pub async fn reconnect(addr: SocketAddr, token: &str) -> Self {
        // BCN plugin connects to the same URL, passes token in bot.connect params
        let url = format!("ws://{}/ws/bot", addr);
        let (ws, _) = tokio_tungstenite::connect_async(&url)
            .await
            .expect("Failed to connect WebSocket");
        let mut bot = Self { ws, bot_id: String::new(), token: String::new() };
        bot.do_connect(Some(token.to_string())).await;
        bot
    }

    /// Drain any onboarding event frames BCS sends immediately after bot.connect for new bots.
    async fn drain_onboarding(&mut self) {
        // BCS sends an onboarding chat.send event right after bot.connect for new bots.
        // Drain frames for up to 200ms to clear them before tests start.
        loop {
            match tokio::time::timeout(Duration::from_millis(200), self.ws.next()).await {
                Ok(Some(Ok(Message::Text(_)))) => continue, // discard onboarding frame
                Ok(Some(Ok(Message::Ping(data)))) => {
                    let _ = self.ws.send(Message::Pong(data)).await;
                    continue;
                }
                _ => break, // timeout or close — done draining
            }
        }
    }

    async fn do_connect(&mut self, token: Option<String>) {
        // BCN plugin passes token in bot.connect params (not URL query param)
        let params = match &token {
            Some(t) => json!({ "token": t }),
            None => json!({}),
        };
        let frame = json!({
            "type": "req",
            "id": "connect_001",
            "method": "bot.connect",
            "params": params
        });
        let resp = self.send_and_recv(frame).await.expect("No response to bot.connect");
        assert!(resp["ok"].as_bool().unwrap_or(false), "bot.connect failed: {resp}");
        self.bot_id = resp["payload"]["bot_uuid"].as_str().unwrap_or("").to_string();
        self.token = resp["payload"]["token"].as_str().unwrap_or("").to_string();
    }

    /// Onboard via HTTP API and set visibility to public (mirrors `bcs-cli onboard`).
    /// This is the correct registration path — `bot.register` WS frame is not used in practice.
    /// Visibility is set to "public" so bots can participate in group collaboration
    /// without requiring friend relationships in non-visibility-focused tests.
    pub async fn register(&mut self, name: &str, skills: &[&str], addr: SocketAddr) {
        let skills_vec: Vec<bcs_protocol::Skill> = skills.iter().map(|s| bcs_protocol::Skill {
            name: s.to_string(),
            description: None,
        }).collect();
        let client = self.http_client(addr);
        client
            .onboard(name, Some(name), Some(skills_vec), None, None, None)
            .await
            .expect("Failed to onboard bot");
        client
            .set_visibility(&self.bot_id, "public")
            .await
            .ok(); // Ignore error if visibility endpoint unavailable
    }

    /// Gracefully close the WebSocket connection, making this bot appear "disconnected".
    /// The bot_id and token remain valid for HTTP API calls.
    pub async fn disconnect(&mut self) {
        let _ = self.ws.close(None).await;
    }

    /// Send a `bot.status` heartbeat frame (mirrors BCN's heartbeat loop).
    pub async fn send_heartbeat(&mut self) -> Value {
        let frame = json!({
            "type": "req",
            "id": "heartbeat_001",
            "method": "bot.status",
            "params": {
                "status": "idle",
                "dynamic_summary": "Running",
                "load": 0.0
            }
        });
        self.send_and_recv(frame).await.expect("No response to bot.status")
    }

    /// Respond to a `chat.send` request with a `chat.event` frame.
    /// `request_id` is the `id` field from the incoming `chat.send` frame.
    pub async fn send_chat_event(&mut self, group_id: &str, request_id: &str, text: &str) {
        // First send the response to the chat.send request
        let response = json!({
            "type": "res",
            "id": request_id,
            "ok": true,
            "payload": { "run_id": format!("run_{}", uuid_v4()) }
        });
        self.send_raw(response).await;

        // Then send the chat.event with the actual content
        let event = json!({
            "type": "event",
            "event": "chat.event",
            "payload": {
                "run_id": format!("run_{}", uuid_v4()),
                "bcs_group_id": group_id,
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{ "type": "text", "text": text }],
                    "timestamp": 0
                }
            },
            "seq": 1
        });
        self.send_raw(event).await;
    }

    /// Receive the next non-ping frame (5 s timeout). Returns `None` on timeout.
    pub async fn recv_frame(&mut self) -> Option<Value> {
        loop {
            match tokio::time::timeout(Duration::from_secs(5), self.ws.next()).await {
                Ok(Some(Ok(Message::Text(text)))) => {
                    return serde_json::from_str(&text).ok();
                }
                Ok(Some(Ok(Message::Ping(data)))) => {
                    let _ = self.ws.send(Message::Pong(data)).await;
                    continue;
                }
                Ok(Some(Ok(Message::Pong(_)))) => continue,
                _ => return None,
            }
        }
    }

    /// Receive the next frame with a short timeout (for "should NOT receive" assertions).
    pub async fn recv_frame_short(&mut self) -> Option<Value> {
        loop {
            match tokio::time::timeout(Duration::from_millis(300), self.ws.next()).await {
                Ok(Some(Ok(Message::Text(text)))) => {
                    return serde_json::from_str(&text).ok();
                }
                Ok(Some(Ok(Message::Ping(data)))) => {
                    let _ = self.ws.send(Message::Pong(data)).await;
                    continue;
                }
                Ok(Some(Ok(Message::Pong(_)))) => continue,
                _ => return None,
            }
        }
    }

    /// Send a frame and wait for the matching `"res"` response.
    pub async fn send_and_recv(&mut self, frame: Value) -> Option<Value> {
        let id = frame["id"].as_str().unwrap_or("").to_string();
        self.send_raw(frame).await;
        // Wait for the response with matching id
        loop {
            match tokio::time::timeout(Duration::from_secs(5), self.ws.next()).await {
                Ok(Some(Ok(Message::Text(text)))) => {
                    if let Ok(v) = serde_json::from_str::<Value>(&text) {
                        if v["type"] == "res" && v["id"] == id {
                            return Some(v);
                        }
                        // Other frames (events, etc.) — keep waiting
                        continue;
                    }
                }
                Ok(Some(Ok(Message::Ping(data)))) => {
                    let _ = self.ws.send(Message::Pong(data)).await;
                    continue;
                }
                Ok(Some(Ok(Message::Pong(_)))) => continue,
                _ => return None,
            }
        }
    }

    async fn send_raw(&mut self, frame: Value) {
        self.ws
            .send(Message::Text(frame.to_string().into()))
            .await
            .expect("Failed to send frame");
    }

    /// Onboard via HTTP API with skills and domains.
    pub async fn register_with_capabilities(
        &mut self,
        name: &str,
        skills: &[&str],
        domains: &[&str],
        addr: SocketAddr,
    ) {
        let skills_vec: Vec<bcs_protocol::Skill> = skills.iter().map(|s| bcs_protocol::Skill {
            name: s.to_string(),
            description: None,
        }).collect();
        let domains_vec: Vec<String> = domains.iter().map(|s| s.to_string()).collect();
        self.http_client(addr)
            .onboard(
                name,
                Some(name),
                Some(skills_vec),
                Some(domains_vec),
                None,
                None,
            )
            .await
            .expect("Failed to onboard bot with capabilities");
    }

    /// Send a `chat.event(state=final)` with structured routing metadata.
    /// This mimics a bot responding to a `chat.send` with routing intent attached.
    pub async fn send_chat_event_with_routing(
        &mut self,
        group_id: &str,
        request_id: &str,
        text: &str,
        routing: Value,
    ) {
        // First send the response to the chat.send request
        let run_id = format!("run_{}", uuid_v4());
        let response = json!({
            "type": "res",
            "id": request_id,
            "ok": true,
            "payload": { "run_id": &run_id }
        });
        self.send_raw(response).await;

        // Then send the chat.event with routing metadata
        let event = json!({
            "type": "event",
            "event": "chat.event",
            "payload": {
                "run_id": &run_id,
                "bcs_group_id": group_id,
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{ "type": "text", "text": text }],
                    "timestamp": 0
                },
                "routing": routing
            },
            "seq": 1
        });
        self.send_raw(event).await;
    }

    /// Create an HTTP client authenticated with this bot's token.
    pub fn http_client(&self, addr: SocketAddr) -> bcs_cli::BcsClient {
        bcs_cli::BcsClient::with_token(format!("http://{}", addr), &self.token)
    }
}

/// Extract the message action from a frame regardless of whether it's a "req" or "event" frame.
/// - "req" frames: use the "method" field (e.g. "chat.send")
/// - "event" frames: use the "event" field (e.g. "chat.inject")
pub fn frame_action(frame: &Value) -> &str {
    if let Some(m) = frame["method"].as_str() {
        return m;
    }
    if let Some(e) = frame["event"].as_str() {
        return e;
    }
    ""
}

/// Extract the sessionContext from a frame.
/// - "req" frames (chat.send): params.sessionContext
/// - "event" frames (chat.inject): payload.sessionContext
pub fn frame_ctx(frame: &Value) -> Value {
    // req frame: try snake_case first, then camelCase
    if frame["params"]["session_context"].is_object() {
        return frame["params"]["session_context"].clone();
    }
    if frame["params"]["sessionContext"].is_object() {
        return frame["params"]["sessionContext"].clone();
    }
    // event frame: try snake_case first, then camelCase
    if frame["payload"]["session_context"].is_object() {
        return frame["payload"]["session_context"].clone();
    }
    if frame["payload"]["sessionContext"].is_object() {
        return frame["payload"]["sessionContext"].clone();
    }
    Value::Null
}

/// Onboard a bot with mock user identity, binding `created_by` to `staff_no`.
/// Requires `BCS_AUTH_MOCK=1` to be set in the test environment.
pub async fn onboard_bot_as_user(
    addr: std::net::SocketAddr,
    token: &str,
    name: &str,
    staff_no: &str,
) -> serde_json::Value {
    let url = format!("http://{}/bots/onboard", addr);
    let client = reqwest::Client::new();
    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", token))
        .header("X-Mock-User-Id", staff_no)
        .header("X-Mock-Nick-Name", "test_user")
        .json(&serde_json::json!({
            "name": name,
            "summary": name,
            "skills": [{"name": "chat"}],
            "domains": [],
            "scopes": [],
        }))
        .send()
        .await
        .expect("Failed to onboard bot as user");
    assert!(
        response.status().is_success(),
        "Onboard as user failed: {:?}",
        response.status()
    );
    response.json().await.expect("Invalid onboard response")
}

/// Query `/bots/my` with mock user identity.
/// Returns the full JSON response.
pub async fn query_my_bots(
    addr: std::net::SocketAddr,
    staff_no: &str,
) -> serde_json::Value {
    let url = format!("http://{}/bots/my", addr);
    let client = reqwest::Client::new();
    let response = client
        .get(&url)
        .header("X-Mock-User-Id", staff_no)
        .header("X-Mock-Nick-Name", "test_user")
        .send()
        .await
        .expect("Failed to query my bots");
    assert!(
        response.status().is_success(),
        "Query my bots failed: {:?}",
        response.status()
    );
    response.json().await.expect("Invalid my bots response")
}

fn uuid_v4() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let t = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .subsec_nanos();
    format!("{:08x}", t)
}
