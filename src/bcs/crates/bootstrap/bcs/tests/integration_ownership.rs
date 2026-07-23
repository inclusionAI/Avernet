//! Bot Ownership Verification Integration Tests for BCS.
//!
//! These tests verify that bot ownership verification works correctly
//! across all protected HTTP endpoints when the auth SDK is NOT initialized.
//! (SDK initialization requires real company credentials, so we test the
//! passthrough and NoopBotRegistry scenarios here.)
//!
//! When SDK is not initialized:
//! - Requests that still allow bot tokens pass through without user identity
//! - created_by is always null
//! - Owner-only routes that require a human identity return 401
//!
//! For tests requiring real SDK credentials, see the human test cases document
//! at docs/testing/bot-ownership-cli-oauth-functional-test.md
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_ownership -- --test-threads=1
//! ```

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use tokio_tungstenite::tungstenite::Message;

use bcs::{BcsConfig, BcsServer, MessageHistoryConfig};

// ============================================================================
// Test Helpers
// ============================================================================

fn create_temp_bots_dir() -> tempfile::TempDir {
    tempfile::TempDir::new().expect("Failed to create temp dir")
}

use bcs::LoggingConfig;

fn create_test_config(bots_dir: &PathBuf) -> BcsConfig {
    BcsConfig {
        bind: "127.0.0.1".to_string(),
        port: 0,
        bots_base_dir: bots_dir.clone(),
        fusion_provider: None,
        llm: Default::default(),
        max_history_per_session: 100,
        dingtalk_accounts: vec![],
        auth_token: None,
        leader_election: None,
        cache: Default::default(),
        database: Default::default(),
        mist: bcs::MistConfig::default(),
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
        strict_container_validation: false,
        bcs_endpoint: None,
        botchat_url: None,
        register_path: "/bcn/register".to_string(),
        default_visibility: Some("public".to_string()), // Use public for ownership tests to avoid friendship constraints
        manifest: Default::default(),
        allowed_switch_provider_ids: Vec::new(),
        provider_stream_gray_enabled: false,
        provider_stream_gray_created_by: Vec::new(),
        logging: LoggingConfig::default(),
        bcsfuse: bcs_fuse_client::BcsFuseConfig::default(),
        auth_sdk: Default::default(), // SDK NOT initialized
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

async fn start_test_server(
    bots_dir: &PathBuf,
) -> (SocketAddr, tokio::task::JoinHandle<Result<(), bcs::BcsError>>) {
    let config = create_test_config(bots_dir);
    let server = BcsServer::new(config);
    server
        .run_on_random_port()
        .await
        .expect("Failed to start server")
}

type WsSink = futures_util::stream::SplitSink<
    tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
    Message,
>;
type WsStream = futures_util::stream::SplitStream<
    tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
>;

/// Helper: connect a bot via WebSocket, do bot.connect, onboard, return (bot_uuid, token, client, stream, sink).
async fn setup_bot(addr: SocketAddr, name: &str) -> (String, String, bcs_cli::BcsClient, WsStream, WsSink) {
    let url = format!("ws://{}/ws/bot", addr);
    let (ws, _) = tokio_tungstenite::connect_async(&url)
        .await
        .expect("Failed to connect WebSocket");
    let (mut sink, mut stream) = ws.split();

    // Send bot.connect
    let connect_frame = json!({
        "type": "req",
        "id": "connect_001",
        "method": "bot.connect",
        "params": {}
    });
    sink.send(Message::Text(connect_frame.to_string().into()))
        .await
        .expect("Failed to send bot.connect");

    // Read response
    let resp = loop {
        match tokio::time::timeout(Duration::from_secs(5), stream.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if v["type"] == "res" && v["id"] == "connect_001" {
                        break v;
                    }
                }
            }
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = sink.send(Message::Pong(data)).await;
                continue;
            }
            _ => panic!("No response to bot.connect"),
        }
    };

    let bot_uuid = resp["payload"]["bot_uuid"].as_str().unwrap().to_string();
    let token = resp["payload"]["token"].as_str().unwrap().to_string();

    // Drain onboarding instruction
    loop {
        match tokio::time::timeout(Duration::from_millis(200), stream.next()).await {
            Ok(Some(Ok(Message::Text(_)))) => continue,
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = sink.send(Message::Pong(data)).await;
                continue;
            }
            _ => break,
        }
    }

    let client = bcs_cli::BcsClient::with_token(format!("http://{}", addr), &token);
    client
        .onboard(name, Some(name), None, None, None, None)
        .await
        .expect("Failed to onboard");

    (bot_uuid, token, client, stream, sink)
}

// ============================================================================
// Test Category A: SDK Not Initialized — All Operations Allowed
// ============================================================================

/// A3.3 — Without SDK, POST /bots/status succeeds (production passthrough)
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ownership_no_sdk_update_status_allowed() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (bot_uuid, _token, _client, _stream, mut sink) =
        setup_bot(addr, "StatusTestBot").await;

    // POST /bots/status with bot token — should work without SDK
    let http = reqwest::Client::new();
    let resp = http
        .post(format!("http://{}/bots/status", addr))
        .header("Authorization", format!("Bearer {}", _token))
        .json(&json!({
            "bot_uuid": bot_uuid,
            "status": {
                "status": "idle",
                "dynamic_summary": "Running",
                "load": 0.0
            }
        }))
        .send()
        .await
        .expect("POST /bots/status");

    assert!(
        resp.status().is_success(),
        "Expected success without SDK, got: {}",
        resp.status()
    );

    let _ = sink.close().await;
}

/// A3.4 — Without SDK, DELETE /bots/{id} requires human owner identity
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ownership_no_sdk_delete_bot_requires_human_identity() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (bot_uuid, token, _client, _stream, mut sink) =
        setup_bot(addr, "DeleteTestBot").await;

    let http = reqwest::Client::new();
    let resp = http
        .delete(format!("http://{}/bots/{}", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("DELETE /bots/{id}");

    assert_eq!(
        resp.status(),
        reqwest::StatusCode::UNAUTHORIZED,
        "Expected DELETE /bots/{{id}} to require human identity, got: {}",
        resp.status()
    );

    let get_resp = http
        .get(format!("http://{}/bots/{}", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token.as_str()))
        .send()
        .await
        .expect("GET /bots/{id}");
    assert!(
        get_resp.status().is_success(),
        "Bot should remain after rejected DELETE, got: {}",
        get_resp.status()
    );

    let _ = sink.close().await;
}

/// A5.1 — Without SDK, GET /bots succeeds and created_by is null
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ownership_no_sdk_list_bots_created_by_null() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (_bot_uuid, token, _client, _stream, mut sink) =
        setup_bot(addr, "ListTestBot").await;

    let http = reqwest::Client::new();
    let resp: serde_json::Value = http
        .get(format!("http://{}/bots", addr))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("GET /bots")
        .json()
        .await
        .expect("parse JSON");

    // All bots should have created_by: null without SDK
    if let Some(bots) = resp.as_array() {
        for bot in bots {
            assert!(
                bot.get("created_by").map_or(true, |v| v.is_null()),
                "created_by should be null without SDK: {:?}",
                bot.get("created_by")
            );
        }
    }

    let _ = sink.close().await;
}

/// A5.2 — GET /bots/{id} returns created_by as null without SDK
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ownership_no_sdk_get_bot_created_by_null() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (bot_uuid, token, _client, _stream, mut sink) =
        setup_bot(addr, "GetBotTestBot").await;

    let http = reqwest::Client::new();
    let resp: serde_json::Value = http
        .get(format!("http://{}/bots/{}", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("GET /bots/{id}")
        .json()
        .await
        .expect("parse JSON");

    assert!(
        resp.get("created_by").map_or(true, |v| v.is_null()),
        "created_by should be null without SDK: got {:?}",
        resp.get("created_by")
    );

    let _ = sink.close().await;
}

// ============================================================================
// Test Category A6: created_by Persistence Across Reconnection
// ============================================================================

/// A6.1 — created_by persists across bot reconnection (in-memory/disk).
/// In this test without SDK, created_by stays null even after reconnect,
/// verifying that the persistence path does not incorrectly set created_by.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ownership_no_sdk_created_by_stays_null_after_reconnect() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (bot_uuid, token, _client, _stream, mut sink) =
        setup_bot(addr, "ReconnectTestBot").await;

    // Verify created_by is null before reconnect
    let http = reqwest::Client::new();
    let resp_before: serde_json::Value = http
        .get(format!("http://{}/bots/{}", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("GET /bots/{id}")
        .json()
        .await
        .expect("parse JSON");
    assert!(
        resp_before.get("created_by").map_or(true, |v| v.is_null()),
        "created_by should be null before reconnect"
    );

    // Close WebSocket and reconnect with same token
    let _ = sink.close().await;

    // Reconnect with the saved token
    let url = format!("ws://{}/ws/bot", addr);
    let (ws2, _) = tokio_tungstenite::connect_async(&url)
        .await
        .expect("Reconnect WebSocket");
    let (mut sink2, mut stream2) = ws2.split();

    let connect_frame = json!({
        "type": "req",
        "id": "reconnect_001",
        "method": "bot.connect",
        "params": { "token": token }
    });
    sink2.send(Message::Text(connect_frame.to_string().into()))
        .await
        .expect("Send bot.connect");

    let resp = loop {
        match tokio::time::timeout(Duration::from_secs(5), stream2.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if v["type"] == "res" && v["id"] == "reconnect_001" {
                        break v;
                    }
                }
            }
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = sink2.send(Message::Pong(data)).await;
                continue;
            }
            _ => panic!("No response to reconnect bot.connect"),
        }
    };
    assert!(resp["ok"].as_bool().unwrap_or(false), "Reconnect failed: {resp}");

    // After reconnect, created_by should still be null
    let resp_after: serde_json::Value = http
        .get(format!("http://{}/bots/{}", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("GET /bots/{id} after reconnect")
        .json()
        .await
        .expect("parse JSON");
    assert!(
        resp_after.get("created_by").map_or(true, |v| v.is_null()),
        "created_by should remain null after reconnect: got {:?}",
        resp_after.get("created_by")
    );

    let _ = sink2.close().await;
}

// ============================================================================
// Test Category A7: Multiple Protected Endpoints Without SDK
// ============================================================================


// ============================================================================
// Test Category: SDK Not Initialized — Middleware Is Transparent
// ============================================================================

/// Verify that sdk-less servers still allow public health checks while
/// bot read endpoints require a caller identity.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ownership_no_sdk_anonymous_read_endpoints_require_caller() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let http = reqwest::Client::new();

    // GET /health — no auth required
    let health = http
        .get(format!("http://{}/health", addr))
        .send()
        .await
        .expect("GET /health");
    assert!(health.status().is_success(), "Health check failed");

    // GET /bots — bot reads require auth, even without SDK context.
    let bots = http
        .get(format!("http://{}/bots", addr))
        .send()
        .await
        .expect("GET /bots");
    assert_eq!(bots.status(), reqwest::StatusCode::UNAUTHORIZED);

    // GET /bots/discover — discover also requires a caller.
    let discover = http
        .get(format!("http://{}/bots/discover", addr))
        .query(&[("query", "test")])
        .send()
        .await
        .expect("GET /bots/discover");
    assert_eq!(discover.status(), reqwest::StatusCode::UNAUTHORIZED);
}

// ============================================================================
// Test Category: Regression Tests for Auth Plugin Migration (PR2)
// ============================================================================

/// Regression: Legacy bots with created_by = NULL remain accessible.
/// After PR2 auth plugin migration, bots created before the migration
/// (or via SDK-less flows) have created_by = NULL in the database.
/// These bots must NOT trigger 403 Forbidden when any user tries to
/// access them, even with cookie/JWT auth enabled.
///
/// This test simulates a legacy bot (created_by = NULL) and verifies
/// that any authenticated caller can still read its details.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn regression_legacy_null_created_by_allows_access() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Create a bot via WebSocket (SDK not initialized, so created_by = NULL)
    let (bot_uuid, token, _client, _stream, mut sink) =
        setup_bot(addr, "LegacyBot").await;

    // Verify created_by is NULL in GET /bots/{id}
    let http = reqwest::Client::new();
    let resp: serde_json::Value = http
        .get(format!("http://{}/bots/{}", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token.as_str()))
        .send()
        .await
        .expect("GET /bots/{id}")
        .json()
        .await
        .expect("parse JSON");

    assert!(
        resp.get("created_by").map_or(true, |v| v.is_null()),
        "Legacy bot should have created_by = NULL, got: {:?}",
        resp.get("created_by")
    );

    // Now simulate another authenticated caller trying to read the bot.
    let (_other_bot_uuid, other_token, _other_client, _other_stream, mut other_sink) =
        setup_bot(addr, "OtherCallerBot").await;

    // With SDK not initialized, this should succeed (no 403).
    // In production with SDK initialized, a NULL created_by bot should
    // also NOT trigger 403 (backward compatibility requirement).
    let resp2 = http
        .get(format!("http://{}/bots/{}", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", other_token.as_str()))
        .send()
        .await
        .expect("GET /bots/{id} by another user");

    assert!(
        resp2.status().is_success(),
        "Legacy bot with created_by = NULL should be accessible by any user, got: {}",
        resp2.status()
    );

    let _ = other_sink.close().await;
    let _ = sink.close().await;
}

/// Regression: Cookie-based ownership is enforced after PR2 migration.
/// When a bot is created via cookie auth (human user), the `created_by`
/// field should be set to the user's staff_no, and only that user (or
/// admin) should be able to modify the bot (e.g., update status, leave group).
///
/// This test is PLACEHOLDER-only in the SDK-less scenario (all operations
/// succeed without SDK). For real cookie ownership enforcement, see the
/// manual functional test doc at docs/testing/bot-ownership-cli-oauth-functional-test.md,
/// or run this test in a staging environment with SDK initialized.
///
/// Here we verify the NEGATIVE case: without SDK, there is NO enforcement,
/// which is expected behavior (passthrough mode).
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn regression_cookie_ownership_not_enforced_without_sdk() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (bot_uuid, token, _client, _stream, mut sink) =
        setup_bot(addr, "OwnershipTestBot").await;

    // Without SDK, created_by is NULL and no ownership checks happen.
    // Simulate a "different user" (any token or no token) updating status.
    let http = reqwest::Client::new();
    let resp = http
        .post(format!("http://{}/bots/status", addr))
        .header("Authorization", format!("Bearer {}", token)) // same token, but conceptually "any user"
        .json(&json!({
            "bot_uuid": bot_uuid,
            "status": {
                "status": "idle",
                "dynamic_summary": "Running",
                "load": 0.0
            }
        }))
        .send()
        .await
        .expect("POST /bots/status");

    // Should succeed without SDK (no 403)
    assert!(
        resp.status().is_success(),
        "Expected success without SDK (no ownership enforcement), got: {}",
        resp.status()
    );

    // NOTE: To verify real cookie ownership enforcement (403 for non-owner),
    // run this test in a staging environment with:
    // - SDK initialized (auth.cookie.enabled = true)
    // - Two real staff_no users (alice, bob)
    // - alice creates a bot, bob tries to update it → expect 403
    // See docs/testing/bot-ownership-cli-oauth-functional-test.md for manual steps.

    let _ = sink.close().await;
}
