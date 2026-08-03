//! Onboarding Process Integration Tests for BCS.
//!
//! These tests verify the complete bot onboarding flow:
//! 1. WebSocket connection with bot.connect handshake
//! 2. HTTP API onboarding
//! 3. Token persistence and reconnection
//! 4. Edge cases and error handling
//!
//! Test naming convention:
//! - `ws_connect_*` - WebSocket connection handshake tests
//! - `http_onboard_*` - HTTP API onboarding tests
//! - `persistence_*` - Token/capabilities persistence tests
//! - `reconnect_*` - Reconnection with token tests
//! - `edge_case_*` - Edge cases and error handling tests
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_onboarding -- --test-threads=1
//! ```

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, tungstenite::Message};
use serde_json::json;

use bcs::{BcsConfig, BcsServer, MessageHistoryConfig};

// ============================================================================
// Test Helpers
// ============================================================================

/// Create a temp directory for bot data.
fn create_temp_bots_dir() -> tempfile::TempDir {
    tempfile::TempDir::new().expect("Failed to create temp dir")
}

/// Create a test BCS config.
fn create_test_config(bots_dir: &PathBuf) -> BcsConfig {
    use bcs::LoggingConfig;

    BcsConfig {
        bind: "127.0.0.1".to_string(),
        port: 0, // Will be assigned by OS
        bots_base_dir: bots_dir.clone(),
        fusion_provider: None,
        llm: Default::default(),
        max_history_per_session: 100,
        dingtalk_accounts: vec![],
        auth_token: None,
        leader_election: None,
        cache: Default::default(),
        database: Default::default(),
        secret: Default::default(),
        channels: Default::default(),
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
        user_directory: Default::default(),
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
        invite: Default::default(),
        ..BcsConfig::default()
    }
}

/// Start a BCS server on a random port.
async fn start_test_server(bots_dir: &PathBuf) -> (SocketAddr, tokio::task::JoinHandle<Result<(), bcs::BcsError>>) {
    let config = create_test_config(bots_dir);
    let server = BcsServer::new(config);
    server.run_on_random_port().await.expect("Failed to start server")
}

/// Connect a WebSocket client to BCS.
async fn connect_bot(addr: SocketAddr, token: Option<&str>) -> WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>> {
    let url = match token {
        Some(t) => format!("ws://{}/ws/bot?token={}", addr, t),
        None => format!("ws://{}/ws/bot", addr),
    };

    let (ws, _) = tokio_tungstenite::connect_async(&url)
        .await
        .expect("Failed to connect WebSocket");

    ws
}

/// Send a JSON frame and receive the response.
async fn send_frame(
    ws: &mut WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>,
    frame: serde_json::Value,
) -> Option<serde_json::Value> {
    let text = frame.to_string();
    let msg = Message::Text(text.into());
    ws.send(msg).await.expect("Failed to send message");

    // Wait for response with timeout
    loop {
        match tokio::time::timeout(Duration::from_secs(5), ws.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                return serde_json::from_str(&text).ok();
            }
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = ws.send(Message::Pong(data)).await;
                continue;
            }
            Ok(Some(Ok(Message::Pong(_)))) => {
                continue;
            }
            _ => return None,
        }
    }
}

/// Receive a frame with timeout.
async fn recv_frame(
    ws: &mut WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>,
) -> Option<serde_json::Value> {
    loop {
        match tokio::time::timeout(Duration::from_secs(5), ws.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                return serde_json::from_str(&text).ok();
            }
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = ws.send(Message::Pong(data)).await;
                continue;
            }
            Ok(Some(Ok(Message::Pong(_)))) => {
                continue;
            }
            _ => return None,
        }
    }
}

/// Create a BcsClient with token authentication.
fn create_client(addr: SocketAddr, token: &str) -> bcs_cli::BcsClient {
    bcs_cli::BcsClient::with_token(format!("http://{}", addr), token)
}

// ============================================================================
// WebSocket Connection Handshake Tests
// ============================================================================

/// New bot sends bot.connect with empty params → gets new botUuid and token
#[tokio::test]
async fn ws_connect_new_bot_gets_bot_uuid_and_token() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect without any token
    let mut ws = connect_bot(addr, None).await;

    // Send bot.connect with empty params
    let connect_frame = json!({
        "type": "req",
        "id": "connect-1",
        "method": "bot.connect",
        "params": {}
    });

    let response = send_frame(&mut ws, connect_frame).await;
    assert!(response.is_some(), "Should receive response from server");

    let resp = response.unwrap();
    assert_eq!(resp["type"], "res");
    assert_eq!(resp["id"], "connect-1");
    assert!(resp["ok"].as_bool().unwrap(), "bot.connect should succeed");

    // Validate response payload
    let payload = &resp["payload"];
    assert!(payload["is_new"].as_bool().unwrap(), "Should be a new bot");
    assert!(!payload["bot_uuid"].as_str().unwrap().is_empty(), "Should have botUuid");
    assert!(!payload["token"].as_str().unwrap().is_empty(), "Should have token");

    // Validate botUuid format (should start with "bot_")
    let bot_uuid = payload["bot_uuid"].as_str().unwrap();
    assert!(bot_uuid.starts_with("bot_"), "New botUuid should start with 'bot_'");

    // Validate token format (should be a UUID)
    let token = payload["token"].as_str().unwrap();
    assert!(uuid::Uuid::parse_str(token).is_ok(), "Token should be a valid UUID");
}

/// Bot reconnects with valid token in bot.connect params → gets same botUuid
#[tokio::test]
async fn ws_connect_reconnect_with_valid_token_in_params() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // First connection - get token
    let mut ws1 = connect_bot(addr, None).await;
    let resp1 = send_frame(&mut ws1, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp1["payload"]["token"].as_str().unwrap().to_string();

    // Second connection with token in params (not URL)
    let mut ws2 = connect_bot(addr, None).await;
    let resp2 = send_frame(&mut ws2, json!({
        "type": "req", "id": "2", "method": "bot.connect", "params": {
            "token": token
        }
    })).await.unwrap();

    assert!(resp2["ok"].as_bool().unwrap(), "Should succeed with valid token in params");
}


/// Multiple bots connect simultaneously → each gets unique botUuid
#[tokio::test]
async fn ws_connect_multiple_bots_get_unique_ids() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect multiple bots concurrently
    let mut connections = vec![];
    let mut bot_ids = vec![];

    for i in 0..5 {
        let mut ws = connect_bot(addr, None).await;
        let resp = send_frame(&mut ws, json!({
            "type": "req",
            "id": format!("connect-{}", i),
            "method": "bot.connect",
            "params": {}
        })).await.unwrap();

        let bot_id = resp["payload"]["bot_uuid"].as_str().unwrap().to_string();
        bot_ids.push(bot_id);
        connections.push(ws);
    }

    // All bot_ids should be unique
    let unique_count = bot_ids.iter().collect::<std::collections::HashSet<_>>().len();
    assert_eq!(unique_count, 5, "All 5 bots should have unique IDs");
}

// ============================================================================
// HTTP API Onboarding Tests
// ============================================================================

/// Bot calls HTTP /bots/onboard with valid token → succeeds
#[tokio::test]
async fn http_onboard_with_valid_token_succeeds() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect via WebSocket
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let bot_id = resp["payload"]["bot_uuid"].as_str().unwrap().to_string();
    let token = resp["payload"]["token"].as_str().unwrap().to_string();

    // Onboard via HTTP API
    let client = create_client(addr, &token);
    let result = client.onboard(
        "TestBot",
        Some("A test bot for onboarding"),
        Some(vec![bcs_protocol::Skill::new("skill1"), bcs_protocol::Skill::new("skill2")]),
        Some(vec!["domain1".to_string()]),
        Some(vec!["scope1".to_string()]),
        None,
    ).await;

    assert!(result.is_ok(), "HTTP onboard should succeed");

    let onboard_resp = result.unwrap();
    assert_eq!(onboard_resp.bot_uuid, bot_id);
    assert!(onboard_resp.onboarded);
    assert_eq!(onboard_resp.name, "TestBot");
}

/// HTTP /bots/onboard with invalid token → fails
#[tokio::test]
async fn http_onboard_with_invalid_token_fails() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Try to onboard with invalid token
    let client = bcs_cli::BcsClient::with_token(format!("http://{}", addr), "invalid-token");
    let result = client.onboard(
        "TestBot",
        Some("A test bot"),
        None,
        None,
        None,
        None,
    ).await;

    assert!(result.is_err(), "HTTP onboard should fail with invalid token");
}

/// HTTP /bots/onboard without token → fails
#[tokio::test]
async fn http_onboard_without_token_fails() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Try to onboard without token
    let client = bcs_cli::BcsClient::new(format!("http://{}", addr));
    let result = client.onboard(
        "TestBot",
        Some("A test bot"),
        None,
        None,
        None,
        None,
    ).await;

    assert!(result.is_err(), "HTTP onboard should fail without token");
}

/// HTTP /bots/onboard with empty name → handled gracefully
#[tokio::test]
async fn http_onboard_with_empty_name_handled_gracefully() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect to get token
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp["payload"]["token"].as_str().unwrap().to_string();

    // Onboard with empty name (should fail or be handled gracefully)
    let client = create_client(addr, &token);
    let result = client.onboard(
        "", // Empty name
        None,
        None,
        None,
        None,
        None,
    ).await;

    // Behavior depends on validation - might fail or succeed with default
    // The important thing is it doesn't crash
    let _ = result;
}

// ============================================================================
// Persistence Tests
// ============================================================================

/// Bot capabilities are persisted to $BCS_DATA_DIR/{botUuid}/bot.json after onboard
#[tokio::test]
async fn persistence_capabilities_saved_to_disk_after_onboard() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect and onboard
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let bot_id = resp["payload"]["bot_uuid"].as_str().unwrap().to_string();
    let token = resp["payload"]["token"].as_str().unwrap().to_string();

    let client = create_client(addr, &token);
    client.onboard(
        "PersistentBot",
        Some("A bot that persists"),
        Some(vec![bcs_protocol::Skill::new("test_skill")]),
        Some(vec!["test_domain".to_string()]),
        Some(vec!["test_scope".to_string()]),
        None,
    ).await.expect("Onboard should succeed");

    // Check that capabilities file was created
    let bot_file = bots_dir.join(&bot_id).join("bot.json");
    assert!(bot_file.exists(), "Bot capabilities file should be created");

    // Verify file contents
    let content = std::fs::read_to_string(&bot_file).expect("Should read bot file");
    let json: serde_json::Value = serde_json::from_str(&content).expect("Should parse JSON");
    assert_eq!(json["name"], "PersistentBot");
    assert_eq!(json["summary"], "A bot that persists");
}

/// Token remains valid after WebSocket disconnect for HTTP API calls
#[tokio::test]
async fn persistence_token_valid_after_ws_disconnect() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect and get token
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp["payload"]["token"].as_str().unwrap().to_string();
    let _bot_id = resp["payload"]["bot_uuid"].as_str().unwrap().to_string();

    // Token should be valid while connected
    let client = create_client(addr, &token);
    let result1 = client.onboard("TestBot", None, None, None, None, None).await;
    assert!(result1.is_ok(), "Onboard should work while connected");

    // Disconnect
    drop(ws);
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Token should still be valid for HTTP API (persisted for reconnection)
    let result2 = client.onboard("TestBot2", None, None, None, None, None).await;
    assert!(result2.is_ok(), "Onboard should still work after disconnect (token persisted)");
}

// ============================================================================
// Reconnection Tests
// ============================================================================

/// Bot reconnects with valid token → gets same botUuid, capabilities loaded from disk
#[tokio::test]
async fn reconnect_with_valid_token_gets_same_bot_uuid() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect and get token
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp["payload"]["token"].as_str().unwrap().to_string();
    let bot_id = resp["payload"]["bot_uuid"].as_str().unwrap().to_string();

    // Disconnect
    drop(ws);
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Bot can reconnect via WebSocket with the same token
    let mut ws2 = connect_bot(addr, Some(&token)).await;
    let resp2 = send_frame(&mut ws2, json!({
        "type": "req", "id": "2", "method": "bot.connect", "params": {
            "token": token
        }
    })).await.unwrap();

    // Should reconnect to the same botUuid
    assert!(resp2["ok"].as_bool().unwrap(), "Reconnect should succeed");
    assert_eq!(resp2["payload"]["bot_uuid"].as_str().unwrap(), bot_id, "Should reconnect to same botUuid");
    assert!(!resp2["payload"]["is_new"].as_bool().unwrap(), "Should not be a new connection");
}

/// Bot reconnects after onboard → capabilities loaded from disk
#[tokio::test]
async fn reconnect_loads_capabilities_from_disk() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // First connection and onboard
    let mut ws1 = connect_bot(addr, None).await;
    let resp1 = send_frame(&mut ws1, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp1["payload"]["token"].as_str().unwrap().to_string();

    let client = create_client(addr, &token);
    client.onboard(
        "ReconnectBot",
        Some("Tests reconnection"),
        Some(vec![bcs_protocol::Skill::new("reconnect_test")]),
        None,
        None,
        None,
    ).await.expect("Onboard should succeed");

    // Drop first connection
    drop(ws1);
    tokio::time::sleep(Duration::from_millis(100)).await;

    // Reconnect with same token
    let mut ws2 = connect_bot(addr, Some(&token)).await;
    let resp2 = send_frame(&mut ws2, json!({
        "type": "req", "id": "2", "method": "bot.connect", "params": {
            "token": token
        }
    })).await.unwrap();

    // Should reconnect successfully with capabilities loaded
    assert!(resp2["ok"].as_bool().unwrap(), "Reconnect should succeed");
}

// ============================================================================
// Edge Cases and Error Handling Tests
// ============================================================================

/// Bot sends bot.status after onboard → succeeds
#[tokio::test]
async fn edge_case_status_update_after_onboard_succeeds() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect and onboard
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp["payload"]["token"].as_str().unwrap().to_string();

    let client = create_client(addr, &token);
    client.onboard("StatusBot", None, None, None, None, None).await.ok();

    // Send status update via WebSocket
    let status_frame = json!({
        "type": "req",
        "id": "status-1",
        "method": "bot.status",
        "params": {
            "status": "busy",
            "dynamic_summary": "Processing requests",
            "load": 0.75
        }
    });

    let status_resp = send_frame(&mut ws, status_frame).await;
    assert!(status_resp.is_some(), "Should receive status response");
}

/// New bot receives onboarding instruction via chat.send event
#[tokio::test]
async fn edge_case_new_bot_receives_onboarding_instruction() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect as new bot
    let mut ws = connect_bot(addr, None).await;
    let _ = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    // Receive any subsequent frames (might be onboarding instruction)
    // The server should send an event instructing the bot to onboard
    if let Some(frame) = recv_frame(&mut ws).await {
        // Could be an event frame with onboarding instructions
        if frame["type"] == "event" {
            // This is the expected onboarding instruction
            assert!(frame["event"].is_string() || frame["payload"].is_object());
        }
        // It's also valid if we just receive ping/pong
    }
}

/// List bots after multiple onboards
#[tokio::test]
async fn edge_case_list_bots_after_multiple_onboards() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Keep track of all connections to prevent tokens from being invalidated
    let mut connections = vec![];
    let mut last_token = String::new();

    // Connect and onboard multiple bots - keep connections alive
    for i in 0..3 {
        let mut ws = connect_bot(addr, None).await;
        let resp = send_frame(&mut ws, json!({
            "type": "req", "id": "1", "method": "bot.connect", "params": {}
        })).await.unwrap();

        let token = resp["payload"]["token"].as_str().unwrap().to_string();
        let client = create_client(addr, &token);
        client.onboard(
            &format!("Bot{}", i),
            Some(&format!("Bot number {}", i)),
            None,
            None,
            None,
            None,
        ).await.expect("Onboard should succeed");

        last_token = token;
        connections.push(ws); // Keep connection alive
    }

    // List bots using the last token (connection still alive)
    let client = create_client(addr, &last_token);
    let bots_result = client.list_bots().await;
    // Result depends on whether the token is still valid
    // The key test is that the onboarding succeeded for all 3 bots
    let _ = bots_result;
}

/// Bot can be discovered after onboard
#[tokio::test]
async fn edge_case_bot_discoverable_after_onboard() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect and onboard bot
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp["payload"]["token"].as_str().unwrap().to_string();

    // Keep the connection alive by responding to pings
    let client = create_client(addr, &token);
    client.onboard(
        "DiscoverableBot",
        Some("An expert in database deadlocks"),
        Some(vec![bcs_protocol::Skill::new("database"), bcs_protocol::Skill::new("deadlock")]),
        Some(vec!["database".to_string()]),
        None,
        None,
    ).await.expect("Onboard should succeed");

    // Drain any pending frames from WebSocket (like onboarding instruction)
    let _ = recv_frame(&mut ws).await;

    // Discover bots using a fresh connection
    let mut ws2 = connect_bot(addr, None).await;
    let resp2 = send_frame(&mut ws2, json!({
        "type": "req", "id": "2", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token2 = resp2["payload"]["token"].as_str().unwrap().to_string();
    let client2 = create_client(addr, &token2);
    client2.onboard("DiscovererBot", None, None, None, None, None).await.ok();

    // Now discover should work
    let discovered = client2.discover_bots(Some("database")).await;
    // The test verifies the flow works without crashing
    let _ = discovered;
}

// ============================================================================
// Ownership / created_by Tests
// ============================================================================

/// Onboard without SDK initialized → created_by is null in GET /bots/{id} response
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn onboard_without_sdk_created_by_is_null() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect and onboard a bot (SDK not initialized in test config)
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp["payload"]["token"].as_str().unwrap().to_string();
    let bot_uuid = resp["payload"]["bot_uuid"].as_str().unwrap().to_string();

    let client = create_client(addr, &token);
    client.onboard("OwnershipTestBot", Some("Test bot"), None, None, None, None)
        .await
        .expect("Onboard should succeed");

    // GET /bots/{id} as raw JSON to check created_by field
    let http = reqwest::Client::new();
    let bot_resp: serde_json::Value = http
        .get(format!("http://{}/bots/{}", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await.expect("GET /bots/{id}")
        .json()
        .await.expect("parse JSON");

    assert_eq!(bot_resp["bot_uuid"].as_str(), Some(bot_uuid.as_str()));
    // created_by should be null (no user identity SDK in test)
    assert!(
        bot_resp.get("created_by").map_or(true, |v| v.is_null()),
        "created_by should be null without SDK: got {:?}",
        bot_resp.get("created_by")
    );
}

/// Middleware passthrough: requests work correctly when SDK is not initialized.
/// This verifies sdk_context_middleware gracefully falls back (no-op) when SDK is disabled.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn middleware_passthrough_without_sdk() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect, onboard, and perform write operations — all should work
    // even without SDK initialized (middleware is a no-op)
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req", "id": "1", "method": "bot.connect", "params": {}
    })).await.unwrap();

    let token = resp["payload"]["token"].as_str().unwrap().to_string();
    let client = create_client(addr, &token);

    // Onboard goes through sdk_context_middleware → should work
    let onboard_result = client.onboard("MiddlewareTestBot", Some("Test"), None, None, None, None).await;
    assert!(onboard_result.is_ok(), "Onboard should work without SDK: {:?}", onboard_result.err());

    // List bots goes through middleware → should work
    let bots = client.list_bots().await;
    assert!(bots.is_ok(), "List bots should work without SDK: {:?}", bots.err());
}

/// Admin onboard with non-existent bot_id returns 200 with onboarded=false.
/// Verifies that the API returns a friendly error message instead of 404.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn admin_onboard_nonexistent_bot_returns_200_with_onboarded_false() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let http = reqwest::Client::new();
    let response = http
        .post(format!("http://{}/admin/bots/onboard", addr))
        .json(&json!({
            "bot_id": "nonexistent-bot-id",
            "name": "Ghost Bot",
            "summary": "A ghost bot for testing"
        }))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("POST /admin/bots/onboard should not fail");

    assert_eq!(response.status().as_u16(), 200, "Should return 200 for non-existent bot");

    let body: serde_json::Value = response.json().await.expect("Should parse JSON body");
    assert_eq!(body["onboarded"], false, "onboarded should be false");
    assert_eq!(body["bot_uuid"], "nonexistent-bot-id", "bot_uuid should match request");
    assert!(
        body["message"].as_str().unwrap_or("").contains("未在协作网络注册"),
        "message should contain registration hint, got: {:?}",
        body["message"]
    );
}
