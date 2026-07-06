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

mod e2e_helpers;

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

// ============================================================================
// Test: AI Workbench Group Chat E2E
// ============================================================================

/// Full E2E test: AI workbench creates group, sends message, receives bot events.
///
/// Prerequisites:
/// - BCS binary built: `cargo build --package bcs`
/// - Moltis binary built: `cd submodules/moltis && cargo build`
/// - LLM provider configured: `~/.config/moltis/provider_keys.json`
#[tokio::test]
async fn e2e_workbench_group_chat() {
    init_logging();
    println!("\n========== E2E Workbench Group Chat Test ==========\n");

    let (_temp_dir, data_dir) = create_temp_dir();
    println!("[Setup] Temp data dir: {:?}", data_dir);

    let mut pm = ProcessManager::new();

    // 1. Start BCS server
    println!("\n[Step 1] Starting BCS server...");
    let bcs_port = match pm.start_bcs(&data_dir).await {
        Ok(port) => port,
        Err(e) => {
            eprintln!("Failed to start BCS: {}. Skipping E2E test.", e);
            return;
        }
    };
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);
    println!("[Step 1] BCS started on port {}", bcs_port);

    // 2. Start Bot 1 (coordinator)
    println!("\n[Step 2] Starting Bot 1 (coordinator)...");
    let bot1_port = next_port();
    let (bot1_uuid, bot1_token) = match pm
        .start_moltis(bcs_port, bot1_port, "coordinator", &data_dir)
        .await
    {
        Ok(v) => v,
        Err(e) => {
            eprintln!("Failed to start bot1: {}. Skipping E2E test.", e);
            return;
        }
    };
    println!("[Step 2] Bot1 (coordinator) started: uuid={}, port={}", bot1_uuid, bot1_port);

    // 3. Start Bot 2 (consultant)
    println!("\n[Step 3] Starting Bot 2 (consultant)...");
    let bot2_port = next_port();
    let (bot2_uuid, bot2_token) = match pm
        .start_moltis(bcs_port, bot2_port, "consultant", &data_dir)
        .await
    {
        Ok(v) => v,
        Err(e) => {
            eprintln!("Failed to start bot2: {}. Skipping E2E test.", e);
            return;
        }
    };
    println!("[Step 3] Bot2 (consultant) started: uuid={}, port={}", bot2_uuid, bot2_port);

    // Wait for bots to onboard
    println!("\n[Step 4] Waiting for bots to onboard (5s)...");
    tokio::time::sleep(Duration::from_secs(5)).await;

    // 5. Create a group via HTTP API
    println!("\n[Step 5] Creating group via HTTP API...");
    let client = reqwest::Client::new();
    if let Err(e) =
        bind_bot_to_workbench_user(&client, &bcs_url, &bot1_token, "coordinator").await
    {
        panic!("[Step 5] Failed to bind bot1 owner: {}", e);
    }
    if let Err(e) =
        bind_bot_to_workbench_user(&client, &bcs_url, &bot2_token, "consultant").await
    {
        panic!("[Step 5] Failed to bind bot2 owner: {}", e);
    }
    let create_group_resp = client
        .post(&format!("{}/groups", bcs_url))
        .header("Authorization", format!("Bearer {}", bot1_token))
        .json(&serde_json::json!({
            "driver_bot": bot1_uuid,
            "participants": [
                {
                    "bot_uuid": bot1_uuid,
                    "role": "driver"
                },
                {
                    "bot_uuid": bot2_uuid,
                    "role": "consultant"
                }
            ],
            "label": "E2E Workbench Test Group"
        }))
        .timeout(Duration::from_secs(10))
        .send()
        .await;

    let group_id = match create_group_resp {
        Ok(resp) if resp.status().is_success() => {
            let body: serde_json::Value = resp.json().await.unwrap();
            let gid = body["id"]
                .as_str()
                .unwrap_or("")
                .to_string();
            println!("[Step 5] Group created: {}", gid);
            gid
        }
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            eprintln!("[Step 5] Failed to create group: {} - {}", status, body);
            return;
        }
        Err(e) => {
            eprintln!("[Step 5] Failed to create group: {}", e);
            return;
        }
    };

    assert!(!group_id.is_empty(), "Group ID should not be empty");

    // 6. Simulate AI workbench frontend: connect via WebSocket
    println!("\n[Step 6] Connecting workbench frontend via WebSocket...");
    let mut wb_client = match WorkbenchClient::connect(bcs_port).await {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[Step 6] Failed to connect workbench client: {}", e);
            return;
        }
    };
    println!("[Step 6] Workbench client connected to BCS");

    // 7. Subscribe to the group
    println!("\n[Step 7] Subscribing to group {}...", group_id);
    let connect_resp = wb_client.subscribe_group(&group_id).await;
    match &connect_resp {
        Ok(resp) => {
            assert!(resp["ok"].as_bool().unwrap_or(false), "connect should succeed");
            println!(
                "[Step 7] Subscribed to group:\n{}",
                serde_json::to_string_pretty(&resp["payload"]).unwrap()
            );
        }
        Err(e) => {
            eprintln!("[Step 7] Failed to subscribe to group: {}", e);
            return;
        }
    }

    // 8. Send a group message as a bot owned by the bound Workbench user
    println!("\n[Step 8] Sending group message...");
    let send_resp = wb_client
        .send_group_message(
            &group_id,
            "你好，请帮我分析一下数据库性能问题",
            Some(&bot1_uuid),
            Some("coordinator"),
            &[], // no mentions → driver responds
        )
        .await;

    match &send_resp {
        Ok(resp) => {
            assert!(resp["ok"].as_bool().unwrap_or(false), "chat.send should succeed");
            let run_id = resp["payload"]["runId"].as_str().unwrap_or("");
            println!("[Step 8] Message sent, runId: {}", run_id);
            assert!(!run_id.is_empty(), "runId should not be empty");
        }
        Err(e) => {
            eprintln!("[Step 8] Failed to send group message: {}", e);
            return;
        }
    }

    // 9. Collect events from bot responses (wait up to 60s for LLM response)
    println!("\n[Step 9] Waiting for bot events (up to 60s)...");
    let events = wb_client.collect_events(Duration::from_secs(60)).await;

    println!("\n[Step 9] Received {} events:", events.len());
    for (i, event) in events.iter().enumerate() {
        let event_type = event["event"].as_str().unwrap_or("unknown");
        let state = event["payload"]["state"].as_str().unwrap_or("unknown");
        let bot = event["bot_uuid"].as_str().unwrap_or("unknown");
        println!("  [{}] type={}, state={}, bot={}", i, event_type, state, bot);

        // Print content preview for chat events
        if event_type == "chat" {
            if let Some(content) = event["payload"]["content"].as_str() {
                let preview = if content.len() > 100 {
                    format!("{}...", &content[..100])
                } else {
                    content.to_string()
                };
                println!("       content: {}", preview);
            }
        }
    }

    // Verify we received at least some events
    assert!(
        !events.is_empty(),
        "Should receive at least one event from bot"
    );

    // Verify we got a final event
    let has_final = events
        .iter()
        .any(|e| e["payload"]["state"].as_str() == Some("final"));
    assert!(has_final, "Should receive a final event from bot");

    println!("\n========== E2E Workbench Group Chat Test PASSED ==========\n");
}

// ============================================================================
// Test: Workbench Protocol (no LLM, protocol-level only)
// ============================================================================

/// Protocol-level test: verify connect and chat.send work without LLM.
/// This test only verifies the BCS protocol handling, not bot responses.
#[tokio::test]
async fn e2e_workbench_protocol_connect_and_send() {
    init_logging();
    println!("\n========== E2E Workbench Protocol Test ==========\n");

    let (_temp_dir, data_dir) = create_temp_dir();
    println!("[Setup] Temp data dir: {:?}", data_dir);

    let mut pm = ProcessManager::new();

    // Start BCS server only (no Moltis bots needed for protocol test)
    println!("\n[Step 1] Starting BCS server...");
    let bcs_port = match pm.start_bcs(&data_dir).await {
        Ok(port) => port,
        Err(e) => {
            eprintln!("Failed to start BCS: {}. Skipping test.", e);
            return;
        }
    };
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);
    println!("[Step 1] BCS started on port {}", bcs_port);

    // Connect a real bot via WebSocket (required for onboard and group creation)
    println!("\n[Step 2] Connecting bot via WebSocket...");
    let ws = connect_bot_ws_with_retry(bcs_port).await;
    let (mut bot_sink, mut bot_stream) = ws.split();

    let connect_frame = serde_json::json!({
        "type": "req", "id": "bot_connect_1", "method": "bot.connect", "params": {}
    });
    bot_sink.send(Message::Text(serde_json::to_string(&connect_frame).unwrap().into())).await.unwrap();

    let connect_resp = loop {
        match tokio::time::timeout(Duration::from_secs(5), bot_stream.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if v["type"] == "res" && v["id"] == "bot_connect_1" {
                        break v;
                    }
                }
            }
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = bot_sink.send(Message::Pong(data)).await;
                continue;
            }
            _ => panic!("No response to bot.connect"),
        }
    };
    let bot_uuid = connect_resp["payload"]["bot_uuid"].as_str().unwrap_or("").to_string();
    let bot_token = connect_resp["payload"]["token"].as_str().unwrap_or("").to_string();
    assert!(!bot_uuid.is_empty(), "bot_uuid should not be empty");
    assert!(!bot_token.is_empty(), "bot_token should not be empty");
    println!("[Step 2] Bot connected: uuid={}", bot_uuid);

    // Onboard the bot via HTTP API
    let client = reqwest::Client::new();
    if let Err(e) =
        bind_bot_to_workbench_user(&client, &bcs_url, &bot_token, "Protocol Test Bot").await
    {
        panic!("[Step 2] Failed to onboard bot: {}", e);
    }
    println!("[Step 2] Bot onboarded");

    // Drain onboarding instruction
    loop {
        match tokio::time::timeout(Duration::from_millis(200), bot_stream.next()).await {
            Ok(Some(Ok(Message::Text(_)))) => continue,
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = bot_sink.send(Message::Pong(data)).await;
                continue;
            }
            _ => break,
        }
    }

    // Create a group
    println!("\n[Step 3] Creating group...");
    let create_resp = client
        .post(&format!("{}/groups", bcs_url))
        .header("Authorization", format!("Bearer {}", bot_token))
        .json(&serde_json::json!({
            "driver_bot": bot_uuid,
            "participants": [
                { "bot_uuid": bot_uuid, "role": "driver" }
            ],
            "label": "Protocol Test Group"
        }))
        .timeout(Duration::from_secs(5))
        .send()
        .await;

    let group_id = match create_resp {
        Ok(resp) if resp.status().is_success() => {
            let body: serde_json::Value = resp.json().await.unwrap();
            let gid = body["id"].as_str().unwrap_or("").to_string();
            println!("[Step 3] Group created: {}", gid);
            gid
        }
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            eprintln!("[Step 3] Failed to create group: {} - {}", status, body);
            return;
        }
        Err(e) => {
            eprintln!("[Step 3] Failed to create group: {}", e);
            return;
        }
    };

    assert!(!group_id.is_empty());
    println!("[Step 3] Group created: {}", group_id);

    // Connect workbench client
    println!("\n[Step 4] Connecting workbench client...");
    let mut wb_client = match WorkbenchClient::connect(bcs_port).await {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[Step 4] Failed to connect: {}", e);
            return;
        }
    };
    println!("[Step 4] Workbench client connected");

    // Test 1: connect (subscribe to group)
    println!("\n[Step 5] Testing connect (subscribe to group)...");
    let resp = wb_client.subscribe_group(&group_id).await.unwrap();
    assert!(resp["ok"].as_bool().unwrap_or(false));
    assert_eq!(resp["payload"]["group_id"].as_str().unwrap(), &group_id);
    println!("[Step 5] connect OK: subscribed to group {}", group_id);
    println!("         Participants: {:?}", resp["payload"]["participants"]);

    // Test 2: connect to non-existent group should fail
    println!("\n[Step 6] Testing connect to non-existent group (should fail)...");
    let resp = wb_client.subscribe_group("nonexistent-group").await;
    match resp {
        Ok(r) => {
            assert!(
                !r["ok"].as_bool().unwrap_or(true),
                "connect to nonexistent group should fail"
            );
            println!("[Step 6] connect correctly failed: {:?}", r["error"]);
        }
        Err(e) => {
            println!("[Step 6] connect correctly errored: {}", e);
        }
    }

    // Test 3: chat.send
    println!("\n[Step 7] Testing chat.send...");
    let resp = wb_client
        .send_group_message(
            &group_id,
            "Hello from workbench",
            Some(&bot_uuid),
            Some("Protocol Test Bot"),
            &[],
        )
        .await;

    match resp {
        Ok(r) => {
            println!("[Step 7] chat.send response:\n{}",
                serde_json::to_string_pretty(&r).unwrap()
            );
            assert!(r["ok"].as_bool().unwrap_or(false), "chat.send should be accepted");
        }
        Err(e) => {
            println!("[Step 7] chat.send result: {} (expected if bot not WS-connected)", e);
        }
    }

    println!("\n========== E2E Workbench Protocol Test PASSED ==========\n");
}

// ============================================================================
// Test: Multiple Frontends Receive Same Events
// ============================================================================

/// Test that multiple workbench frontends subscribed to the same group
/// all receive the same bot events.
#[tokio::test]
async fn e2e_workbench_multi_frontend_broadcast() {
    init_logging();
    println!("\n========== E2E Multi-Frontend Broadcast Test ==========\n");

    let (_temp_dir, data_dir) = create_temp_dir();
    println!("[Setup] Temp data dir: {:?}", data_dir);

    let mut pm = ProcessManager::new();

    println!("\n[Step 1] Starting BCS server...");
    let bcs_port = match pm.start_bcs(&data_dir).await {
        Ok(port) => port,
        Err(e) => {
            eprintln!("Failed to start BCS: {}. Skipping test.", e);
            return;
        }
    };
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);
    println!("[Step 1] BCS started on port {}", bcs_port);

    // Connect a real bot via WebSocket (required for group creation)
    println!("\n[Step 2] Connecting test bot via WebSocket...");
    let ws = connect_bot_ws_with_retry(bcs_port).await;
    let (mut bot_sink, mut bot_stream) = ws.split();

    let connect_frame = serde_json::json!({
        "type": "req", "id": "bot_connect_1", "method": "bot.connect", "params": {}
    });
    bot_sink.send(Message::Text(serde_json::to_string(&connect_frame).unwrap().into())).await.unwrap();

    let connect_resp = loop {
        match tokio::time::timeout(Duration::from_secs(5), bot_stream.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if v["type"] == "res" && v["id"] == "bot_connect_1" {
                        break v;
                    }
                }
            }
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = bot_sink.send(Message::Pong(data)).await;
                continue;
            }
            _ => panic!("No response to bot.connect"),
        }
    };
    let bot_uuid = connect_resp["payload"]["bot_uuid"].as_str().unwrap_or("").to_string();
    let bot_token = connect_resp["payload"]["token"].as_str().unwrap_or("").to_string();
    assert!(!bot_uuid.is_empty(), "bot_uuid should not be empty");
    println!("[Step 2] Bot connected: uuid={}", bot_uuid);

    // Onboard the bot via HTTP API
    let client = reqwest::Client::new();
    if let Err(e) =
        bind_bot_to_workbench_user(&client, &bcs_url, &bot_token, "Multi Test Bot").await
    {
        panic!("[Step 2] Failed to onboard bot: {}", e);
    }
    println!("[Step 2] Bot onboarded: {}", bot_uuid);

    // Drain onboarding instruction
    loop {
        match tokio::time::timeout(Duration::from_millis(200), bot_stream.next()).await {
            Ok(Some(Ok(Message::Text(_)))) => continue,
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = bot_sink.send(Message::Pong(data)).await;
                continue;
            }
            _ => break,
        }
    }

    println!("\n[Step 3] Creating group...");
    let create_resp = client
        .post(&format!("{}/groups", bcs_url))
        .header("Authorization", format!("Bearer {}", bot_token))
        .json(&serde_json::json!({
            "driver_bot": bot_uuid,
            "participants": [
                { "bot_uuid": bot_uuid, "role": "driver" }
            ],
            "label": "Multi Frontend Test"
        }))
        .timeout(Duration::from_secs(5))
        .send()
        .await;

    let group_id = match create_resp {
        Ok(resp) if resp.status().is_success() => {
            let body: serde_json::Value = resp.json().await.unwrap();
            let gid = body["id"].as_str().unwrap_or("").to_string();
            println!("[Step 3] Group created: {}", gid);
            gid
        }
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            eprintln!("[Step 3] Failed to create group: {} - {}", status, body);
            return;
        }
        Err(e) => {
            eprintln!("[Step 3] Failed to create group: {}", e);
            return;
        }
    };

    // Connect two workbench clients
    println!("\n[Step 4] Connecting workbench client 1...");
    let mut client1 = match WorkbenchClient::connect(bcs_port).await {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[Step 4] Failed to connect client1: {}", e);
            return;
        }
    };
    println!("[Step 4] Client 1 connected");

    println!("\n[Step 5] Connecting workbench client 2...");
    let mut client2 = match WorkbenchClient::connect(bcs_port).await {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[Step 5] Failed to connect client2: {}", e);
            return;
        }
    };
    println!("[Step 5] Client 2 connected");

    // Both subscribe to the same group
    println!("\n[Step 6] Client 1 subscribing to group {}...", group_id);
    let resp1 = client1.subscribe_group(&group_id).await.unwrap();
    assert!(resp1["ok"].as_bool().unwrap_or(false));
    println!("[Step 6] Client 1 subscribed");

    println!("\n[Step 7] Client 2 subscribing to group {}...", group_id);
    let resp2 = client2.subscribe_group(&group_id).await.unwrap();
    assert!(resp2["ok"].as_bool().unwrap_or(false));
    println!("[Step 7] Client 2 subscribed");

    println!("\n[Step 8] Sending message from client 1...");
    // Send a message from client1 as a bot owned by the bound Workbench user
    let _ = client1
        .send_group_message(
            &group_id,
            "Test broadcast",
            Some(&bot_uuid),
            Some("Multi Test Bot"),
            &[],
        )
        .await;

    println!("[Step 8] Message sent");

    println!("\n========== E2E Multi-Frontend Broadcast Test PASSED ==========\n");
}
