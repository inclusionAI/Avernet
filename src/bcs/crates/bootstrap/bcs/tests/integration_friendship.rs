//! Friendship Management Integration Tests for BCS.
//!
//! These tests verify the complete friendship management flow:
//! 1. Friend request creation and listing
//! 2. Friend request acceptance
//! 3. Friend request rejection
//! 4. Edge cases (self-request, duplicate, already friends)
//!
//! Test naming convention:
//! - `test_friend_request_*` - Friend request lifecycle tests
//! - `test_friend_request_*_error` - Error handling tests
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_friendship -- --test-threads=1
//! ```

mod helpers;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, tungstenite::Message};
use serde_json::json;

use bcs::{BcsConfig, BcsServer};
use helpers::MockBot;

// ============================================================================
// Test Helpers
// ============================================================================

/// Create a temp directory for bot data.
fn create_temp_bots_dir() -> tempfile::TempDir {
    tempfile::TempDir::new().expect("Failed to create temp dir")
}

/// Create a test BCS config.
fn create_test_config(bots_dir: &PathBuf) -> BcsConfig {
    // Create config via JSON to avoid accessing private LoggingConfig type
    let config_json = json!({
        "bind": "127.0.0.1",
        "port": 0,
        "bots_base_dir": bots_dir,
        "max_history_per_session": 100,
        "store_messages": true,
        "max_groups_as_driver": 3,
        "group_chat_delay_min_ms": 0,
        "group_chat_delay_max_ms": 0,
        "max_group_members": 5,
        "max_groups_as_member": 10,
        "max_group_messages": 100,
        "onboard_binding_enabled": false,
        "strict_container_validation": false,
        "bcs_endpoint": null,
        "default_visibility": null,
        "logging": {
            "default_level": "info",
            "console": true,
            "modules": {},
            "tags": {},
            "outputs": []
        }
    });

    serde_json::from_value(config_json).expect("Failed to parse BcsConfig")
}

/// Start a BCS server on a random port.
async fn start_test_server(bots_dir: &PathBuf) -> (SocketAddr, tokio::task::JoinHandle<Result<(), bcs::BcsError>>) {
    let config = create_test_config(bots_dir);
    let server = BcsServer::new_allowing_private_outbound_for_tests(config);
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

/// Create a BcsClient with token authentication.
fn create_client(addr: SocketAddr, token: &str) -> bcs_cli::BcsClient {
    bcs_cli::BcsClient::with_token(format!("http://{}", addr), token)
}

/// Connect a bot via WebSocket and return (bot_uuid, token).
async fn connect_and_register_bot(addr: SocketAddr) -> (String, String) {
    let mut ws = connect_bot(addr, None).await;
    let resp = send_frame(&mut ws, json!({
        "type": "req",
        "id": "1",
        "method": "bot.connect",
        "params": {}
    })).await.expect("Should receive response");

    let bot_uuid = resp["payload"]["bot_uuid"].as_str().unwrap().to_string();
    let token = resp["payload"]["token"].as_str().unwrap().to_string();
    (bot_uuid, token)
}

// ============================================================================
// Friend Request Tests
// ============================================================================

/// Test friend request creation and listing.
#[tokio::test]
async fn test_friend_request_create_and_list() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);
    let client_b = create_client(addr, &token_b);

    // Set both bots to protected visibility (private bots cannot send/receive friend requests)
    let set_vis_a = client_a.set_visibility(&bot_a_uuid, "protected").await;
    assert!(set_vis_a.is_ok(), "Should set bot_a visibility to protected");
    let set_vis_b = client_b.set_visibility(&bot_b_uuid, "protected").await;
    assert!(set_vis_b.is_ok(), "Should set bot_b visibility to protected");

    // bot_a sends friend request to bot_b
    let result = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result.is_ok(), "Should send friend request successfully");

    let resp = result.unwrap();
    assert!(resp.success, "Friend request should succeed");
    assert!(resp.data.is_some(), "Should have request data");

    // Check bot_a's sent requests
    let sent_resp = client_a.list_friend_requests(Some(&bot_a_uuid), Some("sent"), None).await;
    assert!(sent_resp.is_ok(), "Should list sent requests");
    let sent_data = sent_resp.unwrap();
    assert!(sent_data.success, "Should succeed");
    let sent_requests = sent_data.data.as_ref().and_then(|d| d.as_array()).expect("Should be array");
    assert_eq!(sent_requests.len(), 1, "Should have 1 sent request");
    assert_eq!(sent_requests[0]["to_bot"], bot_b_uuid, "Request should be to bot_b");
    assert_eq!(sent_requests[0]["status"], "pending", "Status should be pending");

    // Check bot_b's received requests
    let received_resp = client_b.list_friend_requests(Some(&bot_b_uuid), Some("received"), None).await;
    assert!(received_resp.is_ok(), "Should list received requests");
    let received_data = received_resp.unwrap();
    assert!(received_data.success, "Should succeed");
    let received_requests = received_data.data.as_ref().and_then(|d| d.as_array()).expect("Should be array");
    assert_eq!(received_requests.len(), 1, "Should have 1 received request");
    assert_eq!(received_requests[0]["from_bot"], bot_a_uuid, "Request should be from bot_a");
    assert_eq!(received_requests[0]["status"], "pending", "Status should be pending");
}

/// Test friend request acceptance.
#[tokio::test]
async fn test_friend_request_accept() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);
    let client_b = create_client(addr, &token_b);

    // Set both bots to protected visibility (private bots cannot send friend requests)
    let set_vis_a = client_a.set_visibility(&bot_a_uuid, "protected").await;
    assert!(set_vis_a.is_ok(), "Should set visibility to protected");
    let set_vis_b = client_b.set_visibility(&bot_b_uuid, "protected").await;
    assert!(set_vis_b.is_ok(), "Should set visibility to protected");

    // bot_a sends friend request to bot_b
    let result = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result.is_ok(), "Should send friend request successfully");

    let resp = result.unwrap();
    let request_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();

    // bot_b accepts the friend request
    let accept_resp = client_b.accept_friend_request(&request_id).await;
    assert!(accept_resp.is_ok(), "Should accept friend request successfully");
    let accept_data = accept_resp.unwrap();
    assert!(accept_data.success, "Accept should succeed");

    // Check bot_a's friends list
    let friends_a = client_a.list_friends(&bot_a_uuid).await;
    assert!(friends_a.is_ok(), "Should list bot_a's friends");
    let friends_a_data = friends_a.unwrap();
    assert!(friends_a_data.success, "Should succeed");
    let friends_a_list = friends_a_data.data.as_ref().and_then(|d| d.as_array()).expect("Should be array");
    assert_eq!(friends_a_list.len(), 1, "bot_a should have 1 friend");
    assert_eq!(friends_a_list[0]["bot_uuid"], bot_b_uuid, "Friend should be bot_b");

    // Check bot_b's friends list
    let friends_b = client_b.list_friends(&bot_b_uuid).await;
    assert!(friends_b.is_ok(), "Should list bot_b's friends");
    let friends_b_data = friends_b.unwrap();
    assert!(friends_b_data.success, "Should succeed");
    let friends_b_list = friends_b_data.data.as_ref().and_then(|d| d.as_array()).expect("Should be array");
    assert_eq!(friends_b_list.len(), 1, "bot_b should have 1 friend");
    assert_eq!(friends_b_list[0]["bot_uuid"], bot_a_uuid, "Friend should be bot_a");
}

/// Test friend request rejection.
#[tokio::test]
async fn test_friend_request_reject() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);
    let client_b = create_client(addr, &token_b);

    // Set both bots to protected visibility (private bots cannot send friend requests)
    let set_vis_a = client_a.set_visibility(&bot_a_uuid, "protected").await;
    assert!(set_vis_a.is_ok(), "Should set visibility to protected");
    let set_vis_b = client_b.set_visibility(&bot_b_uuid, "protected").await;
    assert!(set_vis_b.is_ok(), "Should set visibility to protected");

    // bot_a sends friend request to bot_b
    let result = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result.is_ok(), "Should send friend request successfully");

    let resp = result.unwrap();
    let request_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();

    // bot_b rejects the friend request
    let reject_resp = client_b.reject_friend_request(&request_id).await;
    assert!(reject_resp.is_ok(), "Should reject friend request successfully");
    let reject_data = reject_resp.unwrap();
    assert!(reject_data.success, "Reject should succeed");

    // Check that neither bot has friends
    let friends_a = client_a.list_friends(&bot_a_uuid).await;
    assert!(friends_a.is_ok(), "Should list bot_a's friends");
    let friends_a_data = friends_a.unwrap();
    assert!(friends_a_data.success, "Should succeed");
    let friends_a_list = friends_a_data.data.as_ref().and_then(|d| d.as_array()).expect("Should be array");
    assert_eq!(friends_a_list.len(), 0, "bot_a should have no friends");

    let friends_b = client_b.list_friends(&bot_b_uuid).await;
    assert!(friends_b.is_ok(), "Should list bot_b's friends");
    let friends_b_data = friends_b.unwrap();
    assert!(friends_b_data.success, "Should succeed");
    let friends_b_list = friends_b_data.data.as_ref().and_then(|d| d.as_array()).expect("Should be array");
    assert_eq!(friends_b_list.len(), 0, "bot_b should have no friends");
}

/// Test sending friend request to self returns 400 error.
#[tokio::test]
async fn test_friend_request_self_error() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect bot
    let (bot_uuid, token) = connect_and_register_bot(addr).await;
    let client = create_client(addr, &token);

    // Set bot to protected visibility (private bot would get 403 before reaching self-check)
    let set_vis = client.set_visibility(&bot_uuid, "protected").await;
    assert!(set_vis.is_ok(), "Should set visibility to protected");

    // bot tries to send friend request to itself
    let result = client.send_friend_request(None, &bot_uuid).await;
    assert!(result.is_err(), "Should fail when sending friend request to self");

    let err = result.unwrap_err();
    // Check that it's a 400 error (use Debug format to see full error chain)
    let err_debug = format!("{:?}", err);
    assert!(err_debug.contains("400") || err_debug.contains("Bad Request"), 
            "Should return 400 error, got: {}", err_debug);
}

/// Test duplicate friend request is idempotent: returns 200 OK with existing request_id.
/// Intent: re-sending a pending friend request should not error (409), but return the
/// existing pending request so the caller can retrieve the request_id without tracking state.
#[tokio::test]
async fn test_friend_request_duplicate_is_idempotent() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);
    let client_b = create_client(addr, &token_b);

    // Set both bots to protected visibility
    let set_vis = client_a.set_visibility(&bot_a_uuid, "protected").await;
    assert!(set_vis.is_ok(), "Should set visibility to protected");
    let set_vis_b = client_b.set_visibility(&bot_b_uuid, "protected").await;
    assert!(set_vis_b.is_ok(), "Should set bot_b visibility to protected");

    // First request: should succeed with 201 CREATED
    let result1 = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result1.is_ok(), "First request should succeed");
    let resp1 = result1.unwrap();
    let data1 = resp1.data.expect("First request should return data");
    let request_id_1 = data1["id"].as_str().unwrap_or("").to_string();
    assert!(!request_id_1.is_empty(), "First request should return a request id");

    // Second request (duplicate): should succeed with 200 OK — idempotent
    let result2 = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result2.is_ok(), "Duplicate request should NOT return error (idempotent 200)");

    let resp2 = result2.unwrap();
    let data2 = resp2.data.expect("Duplicate request should return data");
    let request_id_2 = data2["id"].as_str().unwrap_or("").to_string();
    assert_eq!(request_id_2, request_id_1, "Duplicate request should return same request id");
    assert_eq!(data2["status"].as_str(), Some("pending"), "Status should be pending");
}

/// Test sending friend request when already friends returns 200 with "Already friends".
#[tokio::test]
async fn test_friend_request_already_friends() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);
    let client_b = create_client(addr, &token_b);

    // Set both bots to protected visibility (private bots cannot send friend requests)
    let set_vis_a = client_a.set_visibility(&bot_a_uuid, "protected").await;
    assert!(set_vis_a.is_ok(), "Should set visibility to protected");
    let set_vis_b = client_b.set_visibility(&bot_b_uuid, "protected").await;
    assert!(set_vis_b.is_ok(), "Should set visibility to protected");

    // Establish friendship
    let result1 = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result1.is_ok(), "First request should succeed");

    let resp1 = result1.unwrap();
    let request_id = resp1.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();

    // Accept the friend request
    let accept_resp = client_b.accept_friend_request(&request_id).await;
    assert!(accept_resp.is_ok(), "Should accept friend request");

    // Try to send another friend request (should be idempotent)
    let result2 = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result2.is_ok(), "Request when already friends should succeed with idempotent response");

    let resp2 = result2.unwrap();
    assert!(resp2.success, "Should return success");
    // Check for "Already friends" message in response
    if let Some(msg) = &resp2.message {
        assert!(msg.contains("Already friends") || msg.to_lowercase().contains("friend"),
                "Should indicate already friends, got: {}", msg);
    }
}

// ============================================================================
// Private Bot Tests (AC-32, AC-33, AC-34)
// ============================================================================

/// Test that a private bot cannot send friend requests (AC-32).
#[tokio::test]
async fn test_private_bot_cannot_send_friend_request() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots, keep bot_a as private (default), set bot_b to protected
    let (_bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);
    let client_b = create_client(addr, &token_b);

    // Set bot_b to protected so it can receive friend requests
    let set_result = client_b.set_visibility(&bot_b_uuid, "protected").await;
    assert!(set_result.is_ok(), "Should set visibility to protected");

    // bot_a (private) can now send friend requests (private only hides from discovery)
    let result = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result.is_ok(), "Private bot should be able to send friend request, got: {:?}", result.err());
}

/// Test that sending friend request to a private bot returns 404 (AC-34).
#[tokio::test]
async fn test_friend_request_to_private_bot_returns_404() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots, set bot_a to protected, keep bot_b as private (default)
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, _token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);

    // Set bot_a to protected so it can send friend requests
    let set_result = client_a.set_visibility(&bot_a_uuid, "protected").await;
    assert!(set_result.is_ok(), "Should set visibility to protected");

    // bot_a sends friend request to bot_b (private) → should fail with 404
    let result1 = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result1.is_err(), "Should fail when sending to private bot");

    let err1 = result1.unwrap_err();
    let err1_debug = format!("{:?}", err1);
    assert!(err1_debug.contains("404") || err1_debug.contains("Not Found"), 
            "Should return 404 error, got: {}", err1_debug);

    // Also send friend request to a non-existent bot UUID
    let fake_bot_uuid = "00000000-0000-0000-0000-000000000000";
    let result2 = client_a.send_friend_request(None, fake_bot_uuid).await;
    assert!(result2.is_err(), "Should fail when sending to non-existent bot");

    let err2 = result2.unwrap_err();
    let err2_debug = format!("{:?}", err2);
    assert!(err2_debug.contains("404") || err2_debug.contains("Not Found"), 
            "Should return 404 error, got: {}", err2_debug);
}

/// Rev-9 AC-100: Private bot CAN list friend requests (read-only relationship management).
/// Previously blocked by AC-33, now allowed.
#[tokio::test]
async fn test_private_bot_can_list_friend_requests() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect bot, keep as private (default)
    let (bot_uuid, token) = connect_and_register_bot(addr).await;
    let client = create_client(addr, &token);

    // Private bot lists friend requests → should succeed
    let result = client.list_friend_requests(Some(&bot_uuid), Some("sent"), None).await;
    assert!(result.is_ok(), "Private bot should be able to list friend requests (Rev-9 AC-100)");
    let data = result.unwrap();
    assert!(data.success, "Should return success");
}

/// Test visibility change to private preserves existing friendships (Rev-6 AC-90).
/// Intent: When a bot becomes private, friendships are preserved but:
/// - Cannot send new friend requests (403)
/// - Cannot receive new friend requests (404)
#[tokio::test]
async fn test_visibility_change_to_private_preserves_friendships() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots, set both to protected, establish friendship
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);
    let client_b = create_client(addr, &token_b);

    // Set both to protected
    client_a.set_visibility(&bot_a_uuid, "protected").await.expect("Should set visibility");
    client_b.set_visibility(&bot_b_uuid, "protected").await.expect("Should set visibility");

    // Establish friendship
    let result = client_a.send_friend_request(None, &bot_b_uuid).await;
    assert!(result.is_ok(), "Should send friend request");
    let request_id = result.unwrap().data.unwrap()["id"].as_str().unwrap().to_string();
    client_b.accept_friend_request(&request_id).await.expect("Should accept request");

    // Verify friendship exists (both directions)
    let friends_a = client_a.list_friends(&bot_a_uuid).await.expect("Should list A's friends");
    assert_eq!(friends_a.data.as_ref().unwrap().as_array().unwrap().len(), 1,
            "A should have 1 friend before visibility change");
    let friends_b = client_b.list_friends(&bot_b_uuid).await.expect("Should list B's friends");
    assert_eq!(friends_b.data.as_ref().unwrap().as_array().unwrap().len(), 1,
            "B should have 1 friend before visibility change");

    // Change bot_a to private
    client_a.set_visibility(&bot_a_uuid, "private").await.expect("Should set visibility to private");

    // Rev-6 (AC-90): Verify both A and B's friend lists are preserved
    let friends_a_after = client_a.list_friends(&bot_a_uuid).await.expect("Should list A's friends");
    assert_eq!(friends_a_after.data.as_ref().unwrap().as_array().unwrap().len(), 1,
            "A's friend list should be preserved after becoming private");

    let friends_b_after = client_b.list_friends(&bot_b_uuid).await.expect("Should list B's friends");
    assert_eq!(friends_b_after.data.as_ref().unwrap().as_array().unwrap().len(), 1,
            "B's friend list should still contain A");

    // Private bot can still send new friend requests (private only hides from discovery)
    let (bot_c_uuid, token_c) = connect_and_register_bot(addr).await;
    let client_c = create_client(addr, &token_c);
    client_c.set_visibility(&bot_c_uuid, "protected").await.expect("Should set bot_c visibility");
    let new_request_result = client_a.send_friend_request(None, &bot_c_uuid).await;
    assert!(new_request_result.is_ok(), "Private bot should be able to send new friend request, got: {:?}", new_request_result.err());

    // Verify new friend requests to bot_a return 404
    let (bot_d_uuid, token_d) = connect_and_register_bot(addr).await;
    let client_d = create_client(addr, &token_d);
    client_d.set_visibility(&bot_d_uuid, "protected").await.expect("Should set visibility");
    let request_to_private = client_d.send_friend_request(None, &bot_a_uuid).await;
    assert!(request_to_private.is_err(), "Should fail to send request to private bot");
    let err_debug = format!("{:?}", request_to_private.unwrap_err());
    assert!(err_debug.contains("404"), "Should return 404 error for request to private bot");
}

// ============================================================================
// Bot Chat Private Tests (AC-41)
// ============================================================================

/// Test that a private bot cannot send chat messages (AC-41).
#[tokio::test]
async fn test_private_bot_cannot_send_chat() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots: sender (private by default), target (protected)
    let (_sender_uuid, sender_token) = connect_and_register_bot(addr).await;
    let (target_uuid, target_token) = connect_and_register_bot(addr).await;

    let target_client = create_client(addr, &target_token);
    target_client.set_visibility(&target_uuid, "protected").await
        .expect("Should set target visibility to protected");

    // Private sender tries to chat with target → should fail with 403
    let client = reqwest::Client::new();
    let response = client
        .post(format!("http://{}/bots/{}/chat", addr, target_uuid))
        .header("Authorization", format!("Bearer {}", sender_token))
        .json(&json!({
            "message": "Hello from private bot",
            "from": "test-user"
        }))
        .send()
        .await
        .expect("Failed to send chat request");

    assert_eq!(response.status(), reqwest::StatusCode::FORBIDDEN,
               "Private bot sending chat to non-friend protected target should be rejected with 403");
    let body: serde_json::Value = response.json().await.expect("Failed to parse response");
    let error_msg = body.get("error").and_then(|e| e.as_str()).unwrap_or("");
    assert!(!error_msg.is_empty(), "Error message should not be empty");
}

/// Test that sending chat to a private bot returns 404 (AC-41).
#[tokio::test]
async fn test_chat_to_private_bot_returns_404() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots: sender (protected), target (private by default)
    let (sender_uuid, sender_token) = connect_and_register_bot(addr).await;
    let (target_uuid, _target_token) = connect_and_register_bot(addr).await;

    let sender_client = create_client(addr, &sender_token);
    sender_client.set_visibility(&sender_uuid, "protected").await
        .expect("Should set sender visibility to protected");

    // Sender tries to chat with private target → should fail with 404
    let client = reqwest::Client::new();
    let response = client
        .post(format!("http://{}/bots/{}/chat", addr, target_uuid))
        .header("Authorization", format!("Bearer {}", sender_token))
        .json(&json!({
            "message": "Hello to private bot",
            "from": "test-user"
        }))
        .send()
        .await
        .expect("Failed to send chat request");

    assert_eq!(response.status(), reqwest::StatusCode::NOT_FOUND,
               "Chat to private bot should return 404");

    // Also try with a non-existent bot UUID to verify 404 consistency
    let fake_uuid = "00000000-0000-0000-0000-000000000000";
    let response_fake = client
        .post(format!("http://{}/bots/{}/chat", addr, fake_uuid))
        .header("Authorization", format!("Bearer {}", sender_token))
        .json(&json!({
            "message": "Hello to non-existent bot",
            "from": "test-user"
        }))
        .send()
        .await
        .expect("Failed to send chat request to fake bot");

    assert_eq!(response_fake.status(), reqwest::StatusCode::NOT_FOUND,
               "Chat to non-existent bot should also return 404");
}

// ============================================================================
// Rev-6 (AC-91): list_friends returns is_online=false for private friends
// ============================================================================

/// Rev-6 (R1.AC-27): Private visibility no longer forces is_online=false.
/// list_friends reflects real WebSocket connection state.
///
/// Uses MockBot to keep WS connections alive through the entire test.
/// (The old `connect_and_register_bot` helper drops the WS on return,
/// which caused this test to fail — Finding 3.)
#[tokio::test]
async fn test_rev6_private_friend_is_online_reflects_heartbeat() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots via MockBot — WS stays alive as long as bot_a/bot_b are in scope
    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_b = MockBot::connect(addr).await;
    bot_a.register("friend_a", &["chat"], addr).await;
    bot_b.register("friend_b", &["chat"], addr).await;

    let bot_a_uuid = bot_a.bot_id.clone();
    let bot_b_uuid = bot_b.bot_id.clone();
    let client_a = bot_a.http_client(addr);
    let client_b = bot_b.http_client(addr);

    // Set both to protected
    client_a.set_visibility(&bot_a_uuid, "protected").await.expect("set A protected");
    client_b.set_visibility(&bot_b_uuid, "protected").await.expect("set B protected");

    // Establish friendship
    let resp = client_a.send_friend_request(None, &bot_b_uuid).await.expect("A→B request");
    let req_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_b.accept_friend_request(&req_id).await.expect("B accept");

    // B's WS is alive → is_online should be true
    let friends_a = client_a.list_friends(&bot_a_uuid).await.expect("list A friends");
    let friends_arr = friends_a.data.as_ref().unwrap().as_array().unwrap();
    assert_eq!(friends_arr.len(), 1, "A should have 1 friend");
    let friend_b = &friends_arr[0];
    assert_eq!(friend_b["bot_uuid"], bot_b_uuid);
    assert_eq!(friend_b["is_online"], true,
               "B should be online (WS connected)");

    // Change B to private (B still has active WS connection)
    // R1.AC-27: Private visibility no longer forces is_online=false
    client_b.set_visibility(&bot_b_uuid, "private").await.expect("set B private");

    // A lists friends → B should still appear with is_online=true (reflects real WS state)
    let friends_a = client_a.list_friends(&bot_a_uuid).await.expect("list A friends after B private");
    let friends_arr = friends_a.data.as_ref().unwrap().as_array().unwrap();
    assert_eq!(friends_arr.len(), 1, "A should still have 1 friend (friendship preserved)");
    let friend_b = &friends_arr[0];
    assert_eq!(friend_b["bot_uuid"], bot_b_uuid);
    assert_eq!(friend_b["is_online"], true,
               "Private friend B should show is_online=true when WS connected (R1.AC-27)");

    // Change B back to protected → is_online should still be true
    client_b.set_visibility(&bot_b_uuid, "protected").await.expect("set B protected again");

    let friends_a = client_a.list_friends(&bot_a_uuid).await.expect("list A friends after B restored");
    let friends_arr = friends_a.data.as_ref().unwrap().as_array().unwrap();
    assert_eq!(friends_arr.len(), 1, "A should still have 1 friend");
    let friend_b = &friends_arr[0];
    assert_eq!(friend_b["bot_uuid"], bot_b_uuid);
    assert_eq!(friend_b["is_online"], true,
               "Restored protected friend B should show real is_online=true");

    // Keep bots alive through all assertions
    drop(bot_a);
    drop(bot_b);
}

// ============================================================================
// Rev-7: Private Bot Self-Access Regression Tests (AC-93, AC-94)
// ============================================================================

/// Rev-7 AC-93: Private Bot can query its own friend list using self token.
#[tokio::test]
async fn test_private_bot_can_list_own_friends() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &token_a);
    let client_b = create_client(addr, &token_b);

    // Set both to protected so they can exchange friend requests
    client_a.set_visibility(&bot_a_uuid, "protected").await.expect("set A protected");
    client_b.set_visibility(&bot_b_uuid, "protected").await.expect("set B protected");

    // Establish friendship: A → B, B accepts
    let req_resp = client_a.send_friend_request(None, &bot_b_uuid).await.expect("send request");
    let request_id = req_resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_b.accept_friend_request(&request_id).await.expect("accept request");

    // Verify friendship established
    let friends_a = client_a.list_friends(&bot_a_uuid).await.expect("list A friends");
    let friends_arr = friends_a.data.as_ref().unwrap().as_array().unwrap();
    assert_eq!(friends_arr.len(), 1, "A should have 1 friend");

    // Switch A to private
    client_a.set_visibility(&bot_a_uuid, "private").await.expect("set A private");

    // AC-93: Private Bot A queries its own friend list using self token → should succeed
    let friends_a_private = client_a.list_friends(&bot_a_uuid).await.expect("private A should list own friends");
    assert!(friends_a_private.success, "Should return success");
    let friends_arr = friends_a_private.data.as_ref().unwrap().as_array().unwrap();
    assert_eq!(friends_arr.len(), 1, "Private A should still have 1 friend (friendship preserved)");
    assert_eq!(friends_arr[0]["bot_uuid"], bot_b_uuid, "Friend should be B");
}

/// Rev-9 AC-100: Private Bot CAN view friend request list after switching from protected.
/// This locks the Rev-9 behavior: both friends list and friend requests list are
/// read-only relationship management interfaces, accessible to Private Bots.
#[tokio::test]
async fn test_private_bot_can_list_friend_requests_after_visibility_change() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect bot and set to protected first
    let (bot_a_uuid, token_a) = connect_and_register_bot(addr).await;
    let client_a = create_client(addr, &token_a);
    client_a.set_visibility(&bot_a_uuid, "protected").await.expect("set A protected");

    // Connect another bot and send a friend request to A
    let (bot_b_uuid, token_b) = connect_and_register_bot(addr).await;
    let client_b = create_client(addr, &token_b);
    client_b.set_visibility(&bot_b_uuid, "protected").await.expect("set B protected");
    client_b.send_friend_request(None, &bot_a_uuid).await.expect("B sends request to A");

    // Verify A can see the request while protected
    let requests_before = client_a.list_friend_requests(Some(&bot_a_uuid), Some("received"), None).await;
    assert!(requests_before.is_ok(), "Protected A should list friend requests");

    // Switch A to private
    client_a.set_visibility(&bot_a_uuid, "private").await.expect("set A private");

    // Rev-9 AC-100: Private Bot A can still list friend requests
    let requests_after = client_a.list_friend_requests(Some(&bot_a_uuid), Some("received"), None).await;
    assert!(requests_after.is_ok(), "Private A should be able to list friend requests (Rev-9 AC-100)");
    let data = requests_after.unwrap();
    assert!(data.success, "Should return success");
}
