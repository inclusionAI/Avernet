//! End-to-End Integration Test: AI Workbench Group Chat
//!
//! This test simulates the full AI workbench group chat flow:
//!
//! 1. Start BCS server
//! 2. Start 2 bots via OpenClaw (Moltis) + BCN plugin
//! 3. Create a group via HTTP API
//! 4. Simulate AI workbench frontend via WebSocket:
//!    - connect (subscribe to group)
//!    - chat.send (send group message)
//!    - receive bot events (delta/final)
//!
//! ```text
//!  AI Workbench (simulated)     BCS Server          Bot1 (OpenClaw)    Bot2 (OpenClaw)
//!       │                          │                      │                  │
//!       │  WS connect              │                      │                  │
//!       │─────────────────────────▶│                      │                  │
//!       │                          │                      │                  │
//!       │  connect {group_id}      │                      │                  │
//!       │─────────────────────────▶│                      │                  │
//!       │  ok {participants}       │                      │                  │
//!       │◀─────────────────────────│                      │                  │
//!       │                          │                      │                  │
//!       │  chat.send {message}     │                      │                  │
//!       │─────────────────────────▶│                      │                  │
//!       │                          │  chat.send (WS)      │                  │
//!       │                          │─────────────────────▶│                  │
//!       │                          │  chat.inject (WS)    │                  │
//!       │                          │────────────────────────────────────────▶│
//!       │                          │                      │                  │
//!       │  event: delta            │  event (bot reply)   │                  │
//!       │◀─────────────────────────│◀─────────────────────│                  │
//!       │  event: final            │                      │                  │
//!       │◀─────────────────────────│                      │                  │
//!       │                          │                      │                  │
//! ```
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test e2e_workbench -- --test-threads=1 --nocapture
//! ```

#[path = "support/e2e_helpers.rs"]
mod e2e_helpers;

#[path = "e2e_workbench/group_chat.rs"]
mod group_chat;
#[path = "e2e_workbench/protocol.rs"]
mod protocol;
#[path = "e2e_workbench/authorization.rs"]
mod authorization;
#[path = "e2e_workbench/broadcast.rs"]
mod broadcast;

use std::sync::Once;

use std::time::Duration;

use e2e_helpers::{create_temp_dir, next_port, ProcessManager};
use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::{
    connect_async,
    tungstenite::{client::IntoClientRequest, http::HeaderValue, Message},
};

// Initialize logging once
static LOG_INIT: Once = Once::new();
const WORKBENCH_STAFF_NO: &str = "workbenchuser";
const WORKBENCH_NICK_NAME: &str = "Workbench Test User";

fn init_logging() {
    LOG_INIT.call_once(|| {
        tracing_subscriber::fmt()
            .with_env_filter(
                tracing_subscriber::EnvFilter::try_from_default_env()
                    .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("debug"))
            )
            .with_test_writer()
            .init();
    });
}

// ============================================================================
// Helper: Simulated AI Workbench Frontend
// ============================================================================

/// A simulated AI workbench frontend that connects to BCS via WebSocket.
struct WorkbenchClient {
    write: futures_util::stream::SplitSink<
        tokio_tungstenite::WebSocketStream<
            tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
        >,
        Message,
    >,
    read: futures_util::stream::SplitStream<
        tokio_tungstenite::WebSocketStream<
            tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
        >,
    >,
    req_counter: u32,
}

impl WorkbenchClient {
    /// Connect to BCS WebSocket endpoint as a frontend client.
    async fn connect(bcs_port: u16) -> Result<Self, Box<dyn std::error::Error>> {
        let url = format!("ws://127.0.0.1:{}/ws", bcs_port);
        let mut request = url.into_client_request()?;
        request.headers_mut().insert(
            "X-Mock-User-Id",
            HeaderValue::from_static(WORKBENCH_STAFF_NO),
        );
        request.headers_mut().insert(
            "X-Mock-Nick-Name",
            HeaderValue::from_static(WORKBENCH_NICK_NAME),
        );
        let (ws_stream, _) = connect_async(request).await?;
        let (write, read) = ws_stream.split();
        Ok(Self {
            write,
            read,
            req_counter: 0,
        })
    }

    /// Send a `connect` request to subscribe to a group.
    async fn subscribe_group(
        &mut self,
        group_id: &str,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        self.req_counter += 1;
        let req_id = format!("req-{}", self.req_counter);

        let frame = serde_json::json!({
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {
                "group_id": group_id
            }
        });

        self.write
            .send(Message::Text(serde_json::to_string(&frame)?.into()))
            .await?;

        // Wait for response
        self.wait_for_response(&req_id, Duration::from_secs(5))
            .await
    }

    /// Send a `chat.send` request to send a group message.
    async fn send_group_message(
        &mut self,
        group_id: &str,
        message: &str,
        sender_bot_id: Option<&str>,
        sender_bot_name: Option<&str>,
        mentions: &[&str],
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        self.req_counter += 1;
        let req_id = format!("req-{}", self.req_counter);

        let mut params = serde_json::json!({
            "group_id": group_id,
            "message": message,
            "mentions": mentions,
        });

        if let Some(bot_id) = sender_bot_id {
            params["bot_id"] = serde_json::Value::String(bot_id.to_string());
        }
        if let Some(name) = sender_bot_name {
            params["bot_name"] = serde_json::Value::String(name.to_string());
        }

        let frame = serde_json::json!({
            "type": "req",
            "id": req_id,
            "method": "chat.send",
            "params": params
        });

        self.write
            .send(Message::Text(serde_json::to_string(&frame)?.into()))
            .await?;

        // Wait for response
        self.wait_for_response(&req_id, Duration::from_secs(5))
            .await
    }

    /// Wait for a response frame with the given request ID.
    async fn wait_for_response(
        &mut self,
        req_id: &str,
        timeout: Duration,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let deadline = tokio::time::Instant::now() + timeout;

        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                return Err(format!("Timeout waiting for response to {}", req_id).into());
            }

            match tokio::time::timeout(remaining, self.read.next()).await {
                Ok(Some(Ok(Message::Text(text)))) => {
                    let frame: serde_json::Value = serde_json::from_str(text.as_str())?;
                    if frame["type"] == "res" && frame["id"] == req_id {
                        return Ok(frame);
                    }
                    // Not our response, continue
                }
                Ok(Some(Ok(_))) => continue,
                Ok(Some(Err(e))) => return Err(format!("WebSocket error: {}", e).into()),
                Ok(None) => return Err("WebSocket closed".into()),
                Err(_) => {
                    return Err(format!("Timeout waiting for response to {}", req_id).into())
                }
            }
        }
    }

    /// Collect events for a given duration.
    /// Returns all received event frames.
    async fn collect_events(
        &mut self,
        duration: Duration,
    ) -> Vec<serde_json::Value> {
        let mut events = Vec::new();
        let deadline = tokio::time::Instant::now() + duration;

        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                break;
            }

            match tokio::time::timeout(remaining, self.read.next()).await {
                Ok(Some(Ok(Message::Text(text)))) => {
                    if let Ok(frame) = serde_json::from_str::<serde_json::Value>(text.as_str()) {
                        if frame["type"] == "event" {
                            events.push(frame);
                        }
                    }
                }
                Ok(Some(Ok(_))) => continue,
                _ => break,
            }
        }

        events
    }
}

async fn connect_bot_ws_with_retry(
    bcs_port: u16,
) -> tokio_tungstenite::WebSocketStream<
    tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
> {
    let ws_url = format!("ws://127.0.0.1:{}/ws/bot", bcs_port);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(5);

    loop {
        match connect_async(&ws_url).await {
            Ok((ws, _)) => return ws,
            Err(err) if tokio::time::Instant::now() < deadline => {
                eprintln!("[BCS] waiting for bot WebSocket {}: {}", ws_url, err);
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
            Err(err) => panic!("Failed to connect WebSocket {}: {}", ws_url, err),
        }
    }
}

/// TRUSTED registration (bot-authority plan Task 6,
/// ruling T6d-1): the harness seeds each fixture
/// Bot's authority ownership through the local
/// trusted register surface —
/// `GET /register/token` (mock HUMAN principal) →
/// `POST /register?token&bot-name` (the
/// `register_with_ownership` composite commits the
/// authority owner edge). The mock-user header
/// NEVER reaches authority writes through
/// onboard-side flows.
async fn register_trusted_bot(
    client: &reqwest::Client,
    bcs_url: &str,
    name: &str,
) -> Result<(String, String), Box<dyn std::error::Error>> {
    let resp = client
        .get(&format!("{}/register/token", bcs_url))
        .header("X-Mock-User-Id", WORKBENCH_STAFF_NO)
        .header("X-Mock-Nick-Name", WORKBENCH_NICK_NAME)
        .timeout(Duration::from_secs(5))
        .send()
        .await?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!(
            "register token mint failed: {} - {}",
            status, body
        )
        .into());
    }
    let body: serde_json::Value = resp.json().await?;
    let register_token = body["token"]
        .as_str()
        .ok_or("register token response missing 'token'")?
        .to_string();

    let resp = client
        .post(&format!(
            "{}/register?token={}&bot-name={}",
            bcs_url,
            urlencode(&register_token),
            urlencode(name)
        ))
        .timeout(Duration::from_secs(5))
        .send()
        .await?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(format!(
            "trusted registration failed: {} - {}",
            status, text
        )
        .into());
    }
    let body: serde_json::Value = serde_json::from_str(&text)?;
    let bot_uuid = body["bot_uuid"]
        .as_str()
        .ok_or("registration response missing bot_uuid")?
        .to_string();
    let bot_token = body["bot_token"]
        .as_str()
        .ok_or("registration response missing bot_token")?
        .to_string();
    Ok((bot_uuid, bot_token))
}

fn urlencode(value: &str) -> String {
    let mut encoded = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_'
            | b'.' | b'~' => encoded.push(byte as char),
            other => encoded.push_str(&format!("%{other:02X}")),
        }
    }
    encoded
}

async fn bind_bot_to_workbench_user(
    client: &reqwest::Client,
    bcs_url: &str,
    bot_token: &str,
    name: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let resp = client
        .post(&format!("{}/bots/onboard", bcs_url))
        .header("Authorization", format!("Bearer {}", bot_token))
        .header("X-Mock-User-Id", WORKBENCH_STAFF_NO)
        .header("X-Mock-Nick-Name", WORKBENCH_NICK_NAME)
        .json(&serde_json::json!({
            "name": name,
            "summary": "Test bot for workbench protocol",
            "skills": ["test"],
            "domains": ["test"],
            "scopes": []
        }))
        .timeout(Duration::from_secs(5))
        .send()
        .await?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("bot onboard failed: {} - {}", status, body).into());
    }

    Ok(())
}
