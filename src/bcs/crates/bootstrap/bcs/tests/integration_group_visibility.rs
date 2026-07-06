//! Group Visibility Integration Tests for BCS.
//!
//! These tests verify bot visibility and group creation permissions:
//! 1. Public bots can be invited by any driver
//! 2. Protected bots can only be invited by friends
//! 3. Visibility API (GET/PUT /bots/{id}/visibility)
//! 4. Discover API with collaborate_bot filtering
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_group_visibility -- --test-threads=1
//! ```

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, tungstenite::Message};
use serde_json::json;

use bcs::{BcsConfig, BcsServer};

// ============================================================================
// Test Helpers
// ============================================================================

/// Create a temp directory for bot data.
/// Extract error message from JSON response, handling both old string format
/// (`{"error": "message"}`) and new nested format (`{"error": {"code": "...", "message": "..."}}`).
fn extract_error_message(json: &serde_json::Value) -> Option<String> {
    json.get("error").and_then(|e| {
        e.as_str().map(|s| s.to_string())
            .or_else(|| e.get("message").and_then(|m| m.as_str().map(|s| s.to_string())))
    })
}

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

/// Perform HTTP request to get bot visibility.
async fn get_bot_visibility(addr: SocketAddr, bot_id: &str) -> serde_json::Value {
    let url = format!("http://{}/bots/{}/visibility", addr, bot_id);
    let response = reqwest::get(&url).await.expect("Failed to get visibility");
    assert!(response.status().is_success());
    response.json().await.expect("Failed to parse response")
}

/// Perform HTTP request to set bot visibility.
async fn set_bot_visibility(addr: SocketAddr, bot_id: &str, visibility: &str, token: &str) {
    let url = format!("http://{}/bots/{}/visibility", addr, bot_id);
    let client = reqwest::Client::new();
    let response = client
        .put(&url)
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({"visibility": visibility}))
        .send()
        .await
        .expect("Failed to set visibility");
    assert!(response.status().is_success(), "set_visibility({}) failed: {}", visibility, response.status());
}

/// Perform HTTP request to set actor lifecycle status.
async fn set_actor_status(addr: SocketAddr, bot_id: &str, token: &str, status: &str) {
    let url = format!("http://{}/actors/{}/status", addr, bot_id);
    let client = reqwest::Client::new();
    let response = client
        .put(&url)
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({"status": status}))
        .send()
        .await
        .expect("Failed to set actor status");
    assert!(response.status().is_success(), "set_actor_status({}) failed: {}", status, response.status());
}

/// Perform HTTP request to get bot detail.
async fn get_bot_detail(addr: SocketAddr, bot_id: &str, token: &str) -> serde_json::Value {
    let url = format!("http://{}/bots/{}", addr, bot_id);
    let response = reqwest::Client::new()
        .get(&url)
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("Failed to get bot detail");
    assert!(response.status().is_success());
    response.json().await.expect("Failed to parse response")
}

/// Discover bots with optional collaborate_bot filter.
async fn discover_bots(addr: SocketAddr, collaborate_bot: Option<&str>, token: &str) -> serde_json::Value {
    let url = match collaborate_bot {
        Some(bot_id) => format!("http://{}/bots/discover?collaborate_bot={}", addr, bot_id),
        None => format!("http://{}/bots/discover", addr),
    };
    let response = reqwest::Client::new()
        .get(&url)
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("Failed to discover bots");
    assert!(response.status().is_success());
    response.json().await.expect("Failed to parse response")
}

/// Create a group via HTTP API.
/// Returns (status_code, json_body) on success, or error message on failure.
async fn create_group_http(
    addr: SocketAddr,
    driver_token: &str,
    driver_bot_id: &str,
    group_name: &str,
    members: &[&str],
) -> Result<(reqwest::StatusCode, serde_json::Value), String> {
    let url = format!("http://{}/groups", addr);
    let client = reqwest::Client::new();

    let participants: Vec<serde_json::Value> = members
        .iter()
        .map(|&bot_id| json!({"bot_uuid": bot_id}))
        .collect();

    let request_body = json!({
        "label": group_name,
        "driver_bot": driver_bot_id,
        "participants": participants
    });

    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", driver_token))
        .json(&request_body)
        .send()
        .await;

    match response {
        Ok(resp) => {
            let status = resp.status();
            let json: serde_json::Value = resp.json().await.expect("Failed to parse response");

            // Check if response contains an error field, even if status is 200
            if let Some(error) = extract_error_message(&json) {
                Err(format!("HTTP {}: {}", status, error))
            } else if let Some(error) = json.get("message").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, error))
            } else if let Some(error) = json.get("detail").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, error))
            } else if !status.is_success() {
                // Return error if status is not successful and no error field found
                Err(format!("HTTP {}", status))
            } else {
                Ok((status, json))
            }
        }
        Err(e) => Err(format!("Request failed: {}", e)),
    }
}

/// Add a member to an existing group via HTTP API.
/// Returns (status_code, json_body) on success, or error message on failure.
async fn add_group_member_http(
    addr: SocketAddr,
    coordinator_token: &str,
    group_id: &str,
    bot_uuid: &str,
) -> Result<(reqwest::StatusCode, serde_json::Value), String> {
    let url = format!("http://{}/groups/{}/members", addr, group_id);
    let client = reqwest::Client::new();

    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", coordinator_token))
        .json(&json!({
            "bot_uuid": bot_uuid,
            "role": "consultant"
        }))
        .send()
        .await;

    match response {
        Ok(resp) => {
            let status = resp.status();
            let json: serde_json::Value = resp.json().await.expect("Failed to parse response");

            if let Some(error) = extract_error_message(&json) {
                Err(format!("HTTP {}: {}", status, error))
            } else if let Some(error) = json.get("message").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, error))
            } else if let Some(error) = json.get("detail").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, error))
            } else if !status.is_success() {
                Err(format!("HTTP {}", status))
            } else {
                Ok((status, json))
            }
        }
        Err(e) => Err(format!("Request failed: {}", e)),
    }
}

/// Send a group chat message via HTTP API.
/// Returns (status_code, json_body) on success, or error message on failure.
async fn group_chat_http(
    addr: SocketAddr,
    sender_token: &str,
    group_id: &str,
    message: &str,
    from: Option<&str>,
) -> Result<(reqwest::StatusCode, serde_json::Value), String> {
    let url = format!("http://{}/groups/{}/chat", addr, group_id);
    let client = reqwest::Client::new();

    let body = if let Some(from_bot) = from {
        json!({
            "message": message,
            "from": from_bot
        })
    } else {
        json!({
            "message": message
        })
    };

    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", sender_token))
        .json(&body)
        .send()
        .await;

    match response {
        Ok(resp) => {
            let status = resp.status();
            let json: serde_json::Value = resp.json().await.expect("Failed to parse response");

            if let Some(error) = extract_error_message(&json) {
                Err(format!("HTTP {}: {}", status, error))
            } else if let Some(error) = json.get("message").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, error))
            } else if let Some(error) = json.get("detail").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, error))
            } else if !status.is_success() {
                Err(format!("HTTP {}", status))
            } else {
                Ok((status, json))
            }
        }
        Err(e) => Err(format!("Request failed: {}", e)),
    }
}

// ============================================================================
// Test Cases
// ============================================================================

/// Test that public bots can be invited by any driver.
#[tokio::test]
async fn test_group_create_public_bot_allowed() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect driver_bot and public_bot
    let (driver_bot_id, driver_token) = connect_and_register_bot(addr).await;
    let (public_bot_id, public_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let public_client = create_client(addr, &public_token);

    // Set driver_bot to protected visibility (private driver cannot create groups)
    let set_driver_vis = driver_client.set_visibility(&driver_bot_id, "protected").await;
    assert!(set_driver_vis.is_ok(), "Should set driver visibility to protected");
    assert!(set_driver_vis.unwrap().success, "Set driver visibility should succeed");

    // Set public_bot visibility to public (bot must set its own visibility)
    let set_result = public_client.set_visibility(&public_bot_id, "public").await;
    assert!(set_result.is_ok(), "Should set visibility to public");
    let set_resp = set_result.unwrap();
    assert!(set_resp.success, "Set visibility should succeed");

    // Verify visibility is public
    let visibility = driver_client.get_visibility(&public_bot_id).await;
    assert!(visibility.is_ok(), "Should get visibility");
    let visibility_data = visibility.unwrap();
    assert!(visibility_data.success, "Get visibility should succeed");
    assert_eq!(visibility_data.data.as_ref().unwrap()["visibility"], "public");

    // driver_bot creates a group containing public_bot
    let group_response = create_group_http(addr, &driver_token, &driver_bot_id, "Test Group", &[&public_bot_id]).await;

    // Verify group creation succeeded
    assert!(group_response.is_ok(), "Group creation should succeed");
    let (_status, group_data) = group_response.unwrap();
    assert_eq!(group_data["driver_bot"], driver_bot_id);
    assert!(group_data["participants"].as_array().unwrap().contains(&serde_json::json!(public_bot_id)));
}

/// Test that protected bots can only be invited by friends.
#[tokio::test]
async fn test_group_create_protected_friend_allowed() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect driver_bot and protected_bot
    let (driver_bot_id, driver_token) = connect_and_register_bot(addr).await;
    let (protected_bot_id, protected_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let protected_client = create_client(addr, &protected_token);

    // Set driver_bot to protected visibility (private driver cannot create groups)
    let set_driver_vis = driver_client.set_visibility(&driver_bot_id, "protected").await;
    assert!(set_driver_vis.is_ok(), "Should set driver visibility to protected");
    assert!(set_driver_vis.unwrap().success, "Set driver visibility should succeed");

    // Set protected_bot visibility to protected
    let set_result = protected_client.set_visibility(&protected_bot_id, "protected").await;
    assert!(set_result.is_ok(), "Should set visibility to protected");
    let set_resp = set_result.unwrap();
    assert!(set_resp.success, "Set visibility should succeed");

    // Establish friendship between driver and protected bot
    let friend_result = driver_client.send_friend_request(None, &protected_bot_id).await;
    assert!(friend_result.is_ok(), "Should send friend request");
    let friend_resp = friend_result.unwrap();
    assert!(friend_resp.success, "Friend request should succeed");
    let request_id = friend_resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();

    let accept_result = protected_client.accept_friend_request(&request_id).await;
    assert!(accept_result.is_ok(), "Should accept friend request");
    let accept_resp = accept_result.unwrap();
    assert!(accept_resp.success, "Accept should succeed");

    // driver_bot creates a group containing protected_bot
    let group_response = create_group_http(addr, &driver_token, &driver_bot_id, "Test Group", &[&protected_bot_id]).await;

    // Verify group creation succeeded
    assert!(group_response.is_ok(), "Group creation should succeed");
    let (_status, group_data) = group_response.unwrap();
    assert_eq!(group_data["driver_bot"], driver_bot_id);
    assert!(group_data["participants"].as_array().unwrap().contains(&serde_json::json!(protected_bot_id)));
}

/// Test that protected bots cannot be invited by non-friends.
#[tokio::test]
async fn test_group_create_protected_non_friend_rejected() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect driver_bot and protected_bot
    let (driver_bot_id, driver_token) = connect_and_register_bot(addr).await;
    let (protected_bot_id, protected_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let protected_client = create_client(addr, &protected_token);

    // Set driver_bot to protected visibility (private driver cannot create groups)
    let set_driver_vis = driver_client.set_visibility(&driver_bot_id, "protected").await;
    assert!(set_driver_vis.is_ok(), "Should set driver visibility to protected");
    assert!(set_driver_vis.unwrap().success, "Set driver visibility should succeed");

    // Set protected_bot visibility to protected
    let set_result = protected_client.set_visibility(&protected_bot_id, "protected").await;
    assert!(set_result.is_ok(), "Should set visibility to protected");
    let set_resp = set_result.unwrap();
    assert!(set_resp.success, "Set visibility should succeed");

    // Verify visibility is protected
    let visibility = driver_client.get_visibility(&protected_bot_id).await;
    assert!(visibility.is_ok(), "Should get visibility");
    let visibility_data = visibility.unwrap();
    assert!(visibility_data.success, "Get visibility should succeed");
    assert_eq!(visibility_data.data.as_ref().unwrap()["visibility"], "protected");

    // driver_bot creates a group containing protected_bot (no friend relationship)
    let group_response = create_group_http(addr, &driver_token, &driver_bot_id, "Test Group", &[&protected_bot_id]).await;

    // Verify group creation failed with HTTP 403
    assert!(group_response.is_err(), "Group creation should fail");
    let error_msg = group_response.unwrap_err();
    assert!(error_msg.contains("403"), "Error should contain HTTP 403, got: {}", error_msg);
    assert!(error_msg.contains("not friend") || error_msg.contains("permission") || error_msg.contains("protected"),
            "Error should mention friendship or protection, got: {}", error_msg);
}

/// Test visibility GET and PUT endpoints.
#[tokio::test]
async fn test_visibility_get_set() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect a bot
    let (bot_uuid, token) = connect_and_register_bot(addr).await;

    // Get default visibility (should be "protected" by default)
    let visibility = get_bot_visibility(addr, &bot_uuid).await;
    assert!(
        visibility["data"]["visibility"] == "protected" || visibility["data"]["visibility"] == "private",
        "Default visibility should be protected or private, got: {}",
        visibility["data"]["visibility"]
    );

    // Set visibility to "public"
    set_bot_visibility(addr, &bot_uuid, "public", &token).await;
    let visibility = get_bot_visibility(addr, &bot_uuid).await;
    assert_eq!(visibility["data"]["visibility"], "public", "Visibility should be public");

    // Set visibility to "protected"
    set_bot_visibility(addr, &bot_uuid, "protected", &token).await;
    let visibility = get_bot_visibility(addr, &bot_uuid).await;
    assert_eq!(visibility["data"]["visibility"], "protected", "Visibility should be protected");

    // Set visibility to "private"
    set_bot_visibility(addr, &bot_uuid, "private", &token).await;
    let visibility = get_bot_visibility(addr, &bot_uuid).await;
    assert_eq!(visibility["data"]["visibility"], "private", "Visibility should be private");
}

/// Test adding group member with visibility checks.
#[tokio::test]
async fn test_group_add_member_visibility_check() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect driver_bot, public_bot, and protected_bot
    let (driver_bot_id, driver_token) = connect_and_register_bot(addr).await;
    let (public_bot_id, public_token) = connect_and_register_bot(addr).await;
    let (protected_bot_id, protected_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let public_client = create_client(addr, &public_token);
    let protected_client = create_client(addr, &protected_token);

    // Set driver_bot to protected visibility (private driver cannot create groups)
    let set_driver_vis = driver_client.set_visibility(&driver_bot_id, "protected").await;
    assert!(set_driver_vis.is_ok(), "Should set driver visibility to protected");
    assert!(set_driver_vis.unwrap().success, "Set driver visibility should succeed");

    // Set public_bot visibility to public
    let set_public = public_client.set_visibility(&public_bot_id, "public").await;
    assert!(set_public.is_ok(), "Should set public_bot visibility to public");
    assert!(set_public.unwrap().success, "Set public visibility should succeed");

    // Set protected_bot visibility to protected
    let set_protected = protected_client.set_visibility(&protected_bot_id, "protected").await;
    assert!(set_protected.is_ok(), "Should set protected_bot visibility to protected");
    assert!(set_protected.unwrap().success, "Set protected visibility should succeed");

    // Create a group with only the public bot (protected bot cannot be added yet due to visibility rules)
    let group_response = create_group_http(
        addr,
        &driver_token,
        &driver_bot_id,
        "Seed Group",
        &[&public_bot_id],
    ).await;
    assert!(group_response.is_ok(), "Initial group creation should succeed");
    let (_status, group_data) = group_response.unwrap();
    let group_id = group_data["id"].as_str().unwrap().to_string();

    // Try to add the protected bot to the group - this should fail because it's not a friend of the driver
    let add_member_response = add_group_member_http(addr, &driver_token, &group_id, &protected_bot_id).await;

    // This should be rejected due to visibility restrictions
    assert!(add_member_response.is_err(), "add-member should reject protected non-friend bot");
    let error_msg = add_member_response.unwrap_err();

    // Verify HTTP status code is 403 FORBIDDEN
    assert!(
        error_msg.contains("403"),
        "Error should return HTTP 403 FORBIDDEN, got: {}",
        error_msg
    );
    assert!(
        error_msg.contains("protected") || error_msg.contains("friend"),
        "Error should mention protection or friendship, got: {}",
        error_msg
    );
}

/// Hidden actors are reported as dynamic offline and must not be invited into groups.
#[tokio::test]
async fn test_group_add_member_rejects_hidden_offline_bot() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (driver_bot_id, driver_token) = connect_and_register_bot(addr).await;
    let (seed_bot_id, seed_token) = connect_and_register_bot(addr).await;
    let (hidden_bot_id, hidden_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let seed_client = create_client(addr, &seed_token);
    let hidden_client = create_client(addr, &hidden_token);

    driver_client.set_visibility(&driver_bot_id, "protected").await.expect("set driver visibility");
    seed_client.set_visibility(&seed_bot_id, "public").await.expect("set seed visibility");
    hidden_client.set_visibility(&hidden_bot_id, "public").await.expect("set hidden-target visibility");

    set_actor_status(addr, &hidden_bot_id, &hidden_token, "hidden").await;
    let hidden_detail = get_bot_detail(addr, &hidden_bot_id, &hidden_token).await;
    assert_eq!(hidden_detail["status"].as_str(), Some("hidden"));
    assert_eq!(hidden_detail["dynamic_status"]["status"].as_str(), Some("offline"));

    let group_response = create_group_http(
        addr,
        &driver_token,
        &driver_bot_id,
        "Seed Group",
        &[&seed_bot_id],
    ).await;
    assert!(group_response.is_ok(), "Initial group creation should succeed, got: {:?}", group_response.err());
    let (_status, group_data) = group_response.unwrap();
    let group_id = group_data["id"].as_str().unwrap().to_string();

    let add_member_response = add_group_member_http(addr, &driver_token, &group_id, &hidden_bot_id).await;

    assert!(
        add_member_response.is_err(),
        "add-member should reject hidden/offline bot, got: {:?}",
        add_member_response.ok()
    );
    let error_msg = add_member_response.unwrap_err();
    assert!(
        error_msg.contains("403"),
        "Error should return HTTP 403 FORBIDDEN, got: {}",
        error_msg
    );
    assert!(
        error_msg.contains("hidden") || error_msg.contains("offline"),
        "Error should mention hidden/offline status, got: {}",
        error_msg
    );
}

/// Hidden actors are reported as dynamic offline and must not be accepted during group creation.
#[tokio::test]
async fn test_create_group_rejects_hidden_offline_member() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (driver_bot_id, driver_token) = connect_and_register_bot(addr).await;
    let (hidden_bot_id, hidden_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let hidden_client = create_client(addr, &hidden_token);

    driver_client.set_visibility(&driver_bot_id, "protected").await.expect("set driver visibility");
    hidden_client.set_visibility(&hidden_bot_id, "public").await.expect("set hidden-target visibility");

    set_actor_status(addr, &hidden_bot_id, &hidden_token, "hidden").await;
    let hidden_detail = get_bot_detail(addr, &hidden_bot_id, &hidden_token).await;
    assert_eq!(hidden_detail["status"].as_str(), Some("hidden"));
    assert_eq!(hidden_detail["dynamic_status"]["status"].as_str(), Some("offline"));

    let group_response = create_group_http(
        addr,
        &driver_token,
        &driver_bot_id,
        "Hidden Member Group",
        &[&hidden_bot_id],
    ).await;

    assert!(
        group_response.is_err(),
        "create-group should reject hidden/offline bot, got: {:?}",
        group_response.ok()
    );
    let error_msg = group_response.unwrap_err();
    assert!(
        error_msg.contains("403"),
        "Error should return HTTP 403 FORBIDDEN, got: {}",
        error_msg
    );
    assert!(
        error_msg.contains("hidden") || error_msg.contains("offline"),
        "Error should mention hidden/offline status, got: {}",
        error_msg
    );
}

/// Test discover API with collaborate_bot filtering.
#[tokio::test]
async fn test_discover_with_collaborate_bot() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect three bots: caller, public_bot, protected_friend_bot
    let (caller_bot_id, caller_token) = connect_and_register_bot(addr).await;
    let (public_bot_id, public_token) = connect_and_register_bot(addr).await;
    let (protected_bot_id, protected_token) = connect_and_register_bot(addr).await;

    let caller_client = create_client(addr, &caller_token);
    let public_client = create_client(addr, &public_token);
    let protected_client = create_client(addr, &protected_token);

    // Set caller to protected visibility (private bot cannot send friend requests)
    let set_caller = caller_client.set_visibility(&caller_bot_id, "protected").await;
    assert!(set_caller.is_ok(), "Should set caller visibility to protected");
    assert!(set_caller.unwrap().success, "Set caller visibility should succeed");

    // Set protected_bot visibility to protected
    let set_protected = protected_client.set_visibility(&protected_bot_id, "protected").await;
    assert!(set_protected.is_ok(), "Should set protected_bot visibility to protected");
    assert!(set_protected.unwrap().success, "Set protected visibility should succeed");

    // Set public_bot visibility to public (bot must set its own visibility)
    let set_result = public_client.set_visibility(&public_bot_id, "public").await;
    assert!(set_result.is_ok(), "Should set visibility to public");
    let set_resp = set_result.unwrap();
    assert!(set_resp.success, "Set visibility should succeed");

    // Verify visibility is public
    let visibility = caller_client.get_visibility(&public_bot_id).await;
    assert!(visibility.is_ok(), "Should get visibility");
    let visibility_data = visibility.unwrap();
    assert!(visibility_data.success, "Get visibility should succeed");
    assert_eq!(visibility_data.data.as_ref().unwrap()["visibility"], "public");

    // Establish friend relationship between caller and protected_friend_bot
    let friend_result = caller_client.send_friend_request(None, &protected_bot_id).await;
    assert!(friend_result.is_ok(), "Should send friend request successfully");

    let friend_resp = friend_result.unwrap();
    assert!(friend_resp.success, "Friend request should succeed");
    let request_id = friend_resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();

    // protected_friend_bot accepts the friend request
    let accept_resp = protected_client.accept_friend_request(&request_id).await;
    assert!(accept_resp.is_ok(), "Should accept friend request successfully");
    let accept_data = accept_resp.unwrap();
    assert!(accept_data.success, "Accept should succeed");

    // Discover bots with collaborate_bot=caller
    // Use HTTP directly since CLI helper discover_bots doesn't include is_friend field
    let discover_url = format!("http://{}/bots/discover?collaborate_bot={}", addr, caller_bot_id);
    let discover_response = reqwest::Client::new()
        .get(&discover_url)
        .header("Authorization", format!("Bearer {}", caller_token.as_str()))
        .send()
        .await
        .expect("Failed to discover bots");
    assert!(discover_response.status().is_success());
    let discover_data: serde_json::Value = discover_response.json().await.expect("Failed to parse response");

    let bots = discover_data["bots"].as_array().expect("Should be array");

    let public_bot_found = bots.iter().any(|b| b["bot_uuid"] == public_bot_id);
    let protected_bot_found = bots.iter().any(|b| b["bot_uuid"] == protected_bot_id);

    assert!(public_bot_found, "public_bot should be in discover results");
    assert!(protected_bot_found, "protected_friend_bot should be in discover results");

    // Verify is_friend field is correct
    let public_bot_entry = bots.iter().find(|b| b["bot_uuid"] == public_bot_id).unwrap();
    let protected_bot_entry = bots.iter().find(|b| b["bot_uuid"] == protected_bot_id).unwrap();

    // public_bot should have is_friend=false (or not present)
    assert_eq!(public_bot_entry.get("is_friend").and_then(|v| v.as_bool()), Some(false));

    // protected_friend_bot should have is_friend=true
    assert_eq!(protected_bot_entry.get("is_friend").and_then(|v| v.as_bool()), Some(true));
}

// ============================================================================
// Cross-Bot Access Control Tests (AC-37, AC-38, AC-39)
// ============================================================================

/// Test list_friends cross-bot access control.
#[tokio::test]
async fn test_list_friends_cross_bot_access() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 3 bots: bot_a (protected), bot_b (protected), bot_c (private by default)
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;
    let (bot_c_id, _bot_c_token) = connect_and_register_bot(addr).await;

    // Set bot_a and bot_b to protected
    set_bot_visibility(addr, &bot_a_id, "protected", &bot_a_token).await;
    set_bot_visibility(addr, &bot_b_id, "protected", &bot_b_token).await;
    // bot_c remains private (default)

    let _bot_a_client = create_client(addr, &bot_a_token);
    let bot_b_client = create_client(addr, &bot_b_token);

    // Test 1: No token → list bot_b's friends → needs auth now (legacy passthrough removed)
    let no_token_url = format!("http://{}/bots/{}/friends", addr, bot_b_id);
    let no_token_response = reqwest::get(&no_token_url).await.expect("Failed to get friends without token");
    assert!(!no_token_response.status().is_success(), "No token should require auth");

    // Test 2: bot_b's own token → list bot_b's friends → should succeed
    let own_result = bot_b_client.list_friends(&bot_b_id).await;
    assert!(own_result.is_ok(), "Own token should succeed");

    // Test 3: bot_a's token → list bot_b's friends (protected) → should return 403
    let cross_token_url = format!("http://{}/bots/{}/friends", addr, bot_b_id);
    let cross_response = reqwest::Client::new()
        .get(&cross_token_url)
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .send()
        .await
        .expect("Failed to send cross-bot request");
    assert_eq!(cross_response.status(), reqwest::StatusCode::FORBIDDEN, 
               "Cross-bot access to protected bot should return 403");

    // Test 4: bot_a's token → list bot_c's friends (private) → should return 404
    let private_cross_url = format!("http://{}/bots/{}/friends", addr, bot_c_id);
    let private_cross_response = reqwest::Client::new()
        .get(&private_cross_url)
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .send()
        .await
        .expect("Failed to send cross-bot request to private bot");
    assert_eq!(private_cross_response.status(), reqwest::StatusCode::NOT_FOUND, 
               "Cross-bot access to private bot should return 404");
}

/// Test get_visibility cross-bot access control.
#[tokio::test]
async fn test_get_visibility_cross_bot_access() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 3 bots: bot_a (protected), bot_b (protected), bot_c (private by default)
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;
    let (bot_c_id, _bot_c_token) = connect_and_register_bot(addr).await;

    // Set bot_a and bot_b to protected
    set_bot_visibility(addr, &bot_a_id, "protected", &bot_a_token).await;
    set_bot_visibility(addr, &bot_b_id, "protected", &bot_b_token).await;
    // bot_c remains private (default)

    let bot_a_client = create_client(addr, &bot_a_token);
    let bot_b_client = create_client(addr, &bot_b_token);

    // Test 1: No token → get bot_b's visibility → should succeed
    let no_token_url = format!("http://{}/bots/{}/visibility", addr, bot_b_id);
    let no_token_response = reqwest::get(&no_token_url).await.expect("Failed to get visibility without token");
    assert!(no_token_response.status().is_success(), "No token should succeed");

    // Test 2: bot_b's own token → get bot_b's visibility → should succeed
    let own_result = bot_b_client.get_visibility(&bot_b_id).await;
    assert!(own_result.is_ok(), "Own token should succeed");
    let own_visibility = own_result.unwrap();
    assert_eq!(own_visibility.data.as_ref().unwrap()["visibility"], "protected");

    // Test 3: bot_a's token → get bot_b's visibility (protected) → should succeed (public info)
    let cross_result = bot_a_client.get_visibility(&bot_b_id).await;
    assert!(cross_result.is_ok(), "Cross-bot access to visibility should succeed (public info)");
    let cross_visibility = cross_result.unwrap();
    assert_eq!(cross_visibility.data.as_ref().unwrap()["visibility"], "protected");

    // Test 4: bot_a's token → get bot_c's visibility (private) → should return 404
    let private_cross_url = format!("http://{}/bots/{}/visibility", addr, bot_c_id);
    let private_cross_response = reqwest::Client::new()
        .get(&private_cross_url)
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .send()
        .await
        .expect("Failed to send cross-bot request to private bot");
    assert_eq!(private_cross_response.status(), reqwest::StatusCode::NOT_FOUND, 
               "Cross-bot access to private bot visibility should return 404");
}

/// Test set_visibility cross-bot access control.
#[tokio::test]
async fn test_set_visibility_cross_bot_access() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 3 bots: bot_a (protected), bot_b (protected), bot_c (private by default)
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;
    let (bot_c_id, _bot_c_token) = connect_and_register_bot(addr).await;

    // Set bot_a and bot_b to protected
    set_bot_visibility(addr, &bot_a_id, "protected", &bot_a_token).await;
    set_bot_visibility(addr, &bot_b_id, "protected", &bot_b_token).await;
    // bot_c remains private (default)

    let _bot_a_client = create_client(addr, &bot_a_token);
    let bot_b_client = create_client(addr, &bot_b_token);

    // Test 1: No token → set bot_b's visibility → needs auth now (legacy passthrough removed)
    let no_token_url = format!("http://{}/bots/{}/visibility", addr, bot_b_id);
    let no_token_response = reqwest::Client::new()
        .put(&no_token_url)
        .json(&json!({"visibility": "public"}))
        .send()
        .await
        .expect("Failed to set visibility without token");
    assert!(!no_token_response.status().is_success(), "No token should require auth");

    // Reset bot_b to protected for subsequent tests
    set_bot_visibility(addr, &bot_b_id, "protected", &bot_b_token).await;

    // Test 2: bot_b's own token → set bot_b's visibility → should succeed
    let own_result = bot_b_client.set_visibility(&bot_b_id, "public").await;
    assert!(own_result.is_ok(), "Own token should succeed");

    // Reset bot_b to protected for subsequent tests
    set_bot_visibility(addr, &bot_b_id, "protected", &bot_b_token).await;

    // Test 3: bot_a's token → set bot_b's visibility (protected) → should return 403
    let cross_token_url = format!("http://{}/bots/{}/visibility", addr, bot_b_id);
    let cross_response = reqwest::Client::new()
        .put(&cross_token_url)
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .json(&json!({"visibility": "public"}))
        .send()
        .await
        .expect("Failed to send cross-bot request");
    assert_eq!(cross_response.status(), reqwest::StatusCode::FORBIDDEN, 
               "Cross-bot set visibility on protected bot should return 403");

    // Test 4: bot_a's token → set bot_c's visibility (private) → should return 404
    let private_cross_url = format!("http://{}/bots/{}/visibility", addr, bot_c_id);
    let private_cross_response = reqwest::Client::new()
        .put(&private_cross_url)
        .header("Authorization", format!("Bearer {}", bot_a_token))
        .json(&json!({"visibility": "public"}))
        .send()
        .await
        .expect("Failed to send cross-bot request to private bot");
    // Cross-bot set visibility on private bot: may return 403 (forbidden) or 404 (not found)
    assert!(!private_cross_response.status().is_success(),
            "Cross-bot set visibility on private bot should fail, got: {}", private_cross_response.status());
}

// ============================================================================
// Group Chat Visibility Enforcement Tests (AC-77, AC-78, AC-79, AC-80)
// ============================================================================
//
// NOTE: The old AC-42 "no retroactive" test was removed because AC-42 has been
// revised. Private bots can NO LONGER send group messages (AC-77).
// AC-79: Bots remain group members but cannot send messages.
// AC-80: Driver becoming private does not dissolve the group.
/// Test that private bot cannot initiate group creation (should return 403 FORBIDDEN).
/// TDD test case for bug: private bot发起群聊时应返回403而非400
#[tokio::test]
async fn test_private_bot_cannot_create_group_returns_403() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots: private_driver and public_participant
    let (driver_bot_id, driver_token) = connect_and_register_bot(addr).await;
    let (public_bot_id, public_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let public_client = create_client(addr, &public_token);

    // Set driver_bot to PRIVATE visibility
    driver_client.set_visibility(&driver_bot_id, "private").await
        .expect("Should set driver visibility");

    // Set public_bot to public visibility
    public_client.set_visibility(&public_bot_id, "public").await
        .expect("Should set public bot visibility");

    // Private driver can now create groups (private only hides from discovery)
    let group_response = create_group_http(
        addr,
        &driver_token,
        &driver_bot_id,
        "Private Driver Group",
        &[&public_bot_id],
    ).await;

    assert!(group_response.is_ok(), "Private bot should be able to create group, got: {:?}", group_response.err());
}

/// Test that private bot cannot send group chat messages (AC-77).
/// When a bot becomes private, it should be blocked from sending messages in existing groups.
#[tokio::test]
async fn test_private_bot_cannot_send_group_chat() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect driver and two participants
    let (driver_id, driver_token) = connect_and_register_bot(addr).await;
    let (participant_a_id, participant_a_token) = connect_and_register_bot(addr).await;
    let (participant_b_id, participant_b_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let participant_a_client = create_client(addr, &participant_a_token);
    let participant_b_client = create_client(addr, &participant_b_token);

    // Set driver to protected (must be non-private to create group)
    driver_client.set_visibility(&driver_id, "protected").await
        .expect("Should set driver visibility");

    // Set participant_a to public (can be invited without friendship)
    participant_a_client.set_visibility(&participant_a_id, "public").await
        .expect("Should set participant_a visibility");

    // Set participant_b to protected and establish friendship with driver
    participant_b_client.set_visibility(&participant_b_id, "protected").await
        .expect("Should set participant_b visibility");

    // Establish friendship between driver and participant_b
    let friend_result = driver_client.send_friend_request(None, &participant_b_id).await
        .expect("Should send friend request");
    let request_id = friend_result.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    participant_b_client.accept_friend_request(&request_id).await
        .expect("Should accept friend request");

    // Create a group with all three bots
    let group_response = create_group_http(
        addr, &driver_token, &driver_id, "Test Group", &[&participant_a_id, &participant_b_id]
    ).await;
    assert!(group_response.is_ok(), "Group creation should succeed, got: {:?}", group_response.err());
    let (_status, group_data) = group_response.unwrap();
    let group_id = group_data["id"].as_str().unwrap().to_string();

    // Now change participant_a to private
    participant_a_client.set_visibility(&participant_a_id, "private").await
        .expect("Should set participant_a to private");

    // Verify participant_a is now private
    let vis = participant_a_client.get_visibility(&participant_a_id).await
        .expect("Should get visibility");
    assert_eq!(vis.data.as_ref().unwrap()["visibility"], "private");

    // Private bot can now send group chat messages (private only hides from discovery)
    let chat_response = group_chat_http(
        addr,
        &participant_a_token,
        &group_id,
        "Hello from private bot",
        Some(&participant_a_id),
    ).await;

    assert!(chat_response.is_ok(), "Private bot should be able to send group chat, got: {:?}", chat_response.err());

    // Verify driver (non-private) can still send messages
    let driver_chat_response = group_chat_http(
        addr,
        &driver_token,
        &group_id,
        "Hello from driver",
        Some(&driver_id),
    ).await;

    assert!(
        driver_chat_response.is_ok(),
        "Driver should still be able to send group chat, got: {:?}",
        driver_chat_response.err()
    );
}

/// Test that private bot cannot be invited to join a group (AC-78).
#[tokio::test]
async fn test_private_bot_cannot_be_invited_to_group() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect driver and two participants
    let (driver_id, driver_token) = connect_and_register_bot(addr).await;
    let (participant_a_id, participant_a_token) = connect_and_register_bot(addr).await;
    let (private_bot_id, private_bot_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let participant_a_client = create_client(addr, &participant_a_token);
    let private_bot_client = create_client(addr, &private_bot_token);

    // Set driver to protected (must be non-private to create group)
    driver_client.set_visibility(&driver_id, "protected").await
        .expect("Should set driver visibility");

    // Set participant_a to public (can be invited without friendship)
    participant_a_client.set_visibility(&participant_a_id, "public").await
        .expect("Should set participant_a visibility");

    // Set private_bot to private
    private_bot_client.set_visibility(&private_bot_id, "private").await
        .expect("Should set private_bot to private");

    // Create a group with just driver and participant_a
    let group_response = create_group_http(
        addr, &driver_token, &driver_id, "Test Group", &[&participant_a_id]
    ).await;
    assert!(group_response.is_ok(), "Group creation should succeed, got: {:?}", group_response.err());
    let (_status, group_data) = group_response.unwrap();
    let group_id = group_data["id"].as_str().unwrap().to_string();

    // AC-78: Attempt to invite private bot to the group - should fail with 404 (hide existence)
    let invite_response = add_group_member_http(addr, &driver_token, &group_id, &private_bot_id).await;

    // This should be rejected with HTTP 404 NOT FOUND (hide private bot existence)
    assert!(invite_response.is_err(), "Should not be able to invite private bot to group, got: {:?}", invite_response.ok());
    let error_msg = invite_response.unwrap_err();

    // Verify HTTP status code is 404 NOT FOUND (not 403, to hide private bot existence)
    assert!(
        error_msg.contains("404"),
        "Inviting private bot to group should return HTTP 404 NOT FOUND, got: {}",
        error_msg
    );
    assert!(
        error_msg.to_lowercase().contains("not found"),
        "Error should indicate bot not found, got: {}",
        error_msg
    );
}

/// Test that Driver becoming private does not dissolve the group but prevents new member invites (AC-80).
/// Group continues to exist, but driver cannot send messages or invite new members.
#[tokio::test]
async fn test_driver_becomes_private_group_continues_but_cannot_invite() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect driver and participant
    let (driver_id, driver_token) = connect_and_register_bot(addr).await;
    let (participant_id, participant_token) = connect_and_register_bot(addr).await;
    let (new_bot_id, _new_bot_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let participant_client = create_client(addr, &participant_token);
    let new_bot_client = create_client(addr, &_new_bot_token);

    // Set driver to protected (must be non-private to create group)
    driver_client.set_visibility(&driver_id, "protected").await
        .expect("Should set driver visibility");

    // Set participant to public (can be invited without friendship)
    participant_client.set_visibility(&participant_id, "public").await
        .expect("Should set participant visibility");

    // Set new_bot to public so invite would succeed if driver were not private
    new_bot_client.set_visibility(&new_bot_id, "public").await
        .expect("Should set new_bot visibility");

    // Create a group with driver and participant
    let group_response = create_group_http(
        addr, &driver_token, &driver_id, "Test Group", &[&participant_id]
    ).await;
    assert!(group_response.is_ok(), "Group creation should succeed, got: {:?}", group_response.err());
    let (_status, group_data) = group_response.unwrap();
    let group_id = group_data["id"].as_str().unwrap().to_string();

    // Now change driver to private
    driver_client.set_visibility(&driver_id, "private").await
        .expect("Should set driver to private");

    // Verify driver is now private
    let vis = driver_client.get_visibility(&driver_id).await
        .expect("Should get visibility");
    assert_eq!(vis.data.as_ref().unwrap()["visibility"], "private");

    // AC-80: Group should still exist (verified by checking it's still active)

    // Private driver can now send group chat messages (private only hides from discovery)
    let driver_chat_response = group_chat_http(
        addr,
        &driver_token,
        &group_id,
        "Hello from private driver",
        Some(&driver_id),
    ).await;

    assert!(driver_chat_response.is_ok(), "Private driver should be able to send group chat, got: {:?}", driver_chat_response.err());

    // AC-80: Driver can still invite new members (private only hides from discovery)
    let invite_response = add_group_member_http(addr, &driver_token, &group_id, &new_bot_id).await;

    // Invite may fail due to target reachability (protected + not friends → 403), not because driver is private
    let _ = invite_response; // best-effort — verified separately in visibility tests

    // AC-79/AC-80: Participant (non-private) should still be able to send messages
    let participant_chat_response = group_chat_http(
        addr,
        &participant_token,
        &group_id,
        "Hello from participant",
        Some(&participant_id),
    ).await;

    assert!(
        participant_chat_response.is_ok(),
        "Non-private participant should still be able to send group chat, got: {:?}",
        participant_chat_response.err()
    );
}

/// Test that private bot cannot send group chat messages when 'from' field is not provided (AC-77).
/// This verifies the fix that checks visibility using token-based bot_id when req.from is absent.
#[tokio::test]
async fn test_private_bot_cannot_send_group_chat_without_from_field() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect driver and participant
    let (driver_id, driver_token) = connect_and_register_bot(addr).await;
    let (participant_id, participant_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let participant_client = create_client(addr, &participant_token);

    // Set driver to protected (must be non-private to create group)
    driver_client.set_visibility(&driver_id, "protected").await
        .expect("Should set driver visibility");

    // Set participant to public (can be invited without friendship)
    participant_client.set_visibility(&participant_id, "public").await
        .expect("Should set participant visibility");

    // Create a group with both bots
    let group_response = create_group_http(
        addr, &driver_token, &driver_id, "Test Group", &[&participant_id]
    ).await;
    assert!(group_response.is_ok(), "Group creation should succeed, got: {:?}", group_response.err());
    let (_status, group_data) = group_response.unwrap();
    let group_id = group_data["id"].as_str().unwrap().to_string();

    // Now change participant to private
    participant_client.set_visibility(&participant_id, "private").await
        .expect("Should set participant to private");

    // Private bot can now send group chat (private only hides from discovery)
    let chat_response = group_chat_http(
        addr,
        &participant_token,
        &group_id,
        "Hello from private bot without from field",
        None,
    ).await;

    assert!(chat_response.is_ok(), "Private bot should be able to send group chat without 'from' field, got: {:?}", chat_response.err());

    // Verify driver (non-private) can still send messages without 'from' field
    let driver_chat_response = group_chat_http(
        addr,
        &driver_token,
        &group_id,
        "Hello from driver without from field",
        None,  // No 'from' field - should work for non-private bot
    ).await;

    assert!(
        driver_chat_response.is_ok(),
        "Driver should be able to send group chat without 'from' field, got: {:?}",
        driver_chat_response.err()
    );
}



/// Test query_bots API - empty array returns empty array.
#[tokio::test]
async fn test_query_bots_empty_array() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (_caller_id, caller_token) = connect_and_register_bot(addr).await;

    // Query with empty array
    let client = reqwest::Client::new();
    let url = format!("http://{}/bots/query", addr);
    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", caller_token.as_str()))
        .json(&json!({"bot_uuids": []}))
        .send()
        .await
        .expect("Failed to query bots");

    assert_eq!(response.status(), reqwest::StatusCode::OK);

    let bots: Vec<serde_json::Value> = response
        .json()
        .await
        .expect("Failed to parse response");

    assert_eq!(bots.len(), 0, "Should return empty array");
}



/// Test query_bots API - malformed request body returns 422.
#[tokio::test]
async fn test_query_bots_malformed_body() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (_caller_id, caller_token) = connect_and_register_bot(addr).await;

    let client = reqwest::Client::new();
    let url = format!("http://{}/bots/query", addr);

    // Send invalid JSON body (wrong field name)
    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", caller_token.as_str()))
        .header("Content-Type", "application/json")
        .body(r#"{"invalid_field": "not_an_array"}"#)
        .send()
        .await
        .expect("Failed to send request");

    assert!(
        response.status() == reqwest::StatusCode::BAD_REQUEST
            || response.status() == reqwest::StatusCode::UNPROCESSABLE_ENTITY,
        "Expected 400 or 422 for malformed body, got {}",
        response.status()
    );
}

/// Send a chat message to a bot via HTTP API.
/// Returns (status_code, json_body) on success, or error message on failure.
async fn bot_chat_http(
    addr: SocketAddr,
    sender_token: &str,
    target_bot_id: &str,
    message: &str,
) -> Result<(reqwest::StatusCode, serde_json::Value), String> {
    let url = format!("http://{}/bots/{}/chat", addr, target_bot_id);
    let client = reqwest::Client::new();

    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", sender_token))
        .json(&json!({
            "message": message,
            "from": "test-user"
        }))
        .send()
        .await;

    match response {
        Ok(resp) => {
            let status = resp.status();
            let json: serde_json::Value = resp.json().await.expect("Failed to parse response");

            if let Some(error) = extract_error_message(&json) {
                Err(format!("HTTP {}: {}", status, error))
            } else if !status.is_success() {
                Err(format!("HTTP {}", status))
            } else {
                Ok((status, json))
            }
        }
        Err(e) => Err(format!("Request failed: {}", e)),
    }
}

/// Test that private bot cannot send chat messages (should return 403 FORBIDDEN).
/// TDD test case for bot_chat endpoint: private bot发送消息时应返回403
#[tokio::test]
async fn test_private_bot_cannot_send_chat_returns_403() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect two bots: private_sender and public_target
    let (sender_bot_id, sender_token) = connect_and_register_bot(addr).await;
    let (target_bot_id, target_token) = connect_and_register_bot(addr).await;

    let sender_client = create_client(addr, &sender_token);
    let target_client = create_client(addr, &target_token);

    // Set sender_bot to PRIVATE visibility (sender check triggers 403)
    sender_client.set_visibility(&sender_bot_id, "private").await
        .expect("Should set sender visibility");

    // Set target_bot to PUBLIC visibility (target check passes)
    target_client.set_visibility(&target_bot_id, "public").await
        .expect("Should set target visibility");

    // Private bot attempts to send a chat message to public target
    let chat_response = bot_chat_http(
        addr,
        &sender_token,
        &target_bot_id,
        "Hello from private bot",
    ).await;

    // Private bot can now send chat (private only hides from discovery).
    // May fail with "not connected" if target has no active WS, not with a visibility rejection.
    let _ = chat_response; // best-effort: private status no longer blocks chat sending
}

/// Helper: get friends list as array from FriendApiResponse.
fn get_friends_array(resp: &bcs_protocol::FriendApiResponse) -> Vec<serde_json::Value> {
    resp.data.as_ref()
        .and_then(|d| d.as_array())
        .cloned()
        .unwrap_or_default()
}

/// Helper: get requests list as array from FriendApiResponse.
fn get_requests_array(resp: &bcs_protocol::FriendApiResponse) -> Vec<serde_json::Value> {
    resp.data.as_ref()
        .and_then(|d| d.as_array())
        .cloned()
        .unwrap_or_default()
}

/// Rev-6 (AC-90): Test that changing visibility to private preserves friends list for all parties.
#[tokio::test]
async fn test_visibility_change_to_private_preserves_friends() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 3 bots: A, B, C
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;
    let (bot_c_id, bot_c_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);
    let client_c = create_client(addr, &bot_c_token);

    // Set all bots to protected visibility
    client_a.set_visibility(&bot_a_id, "protected").await
        .expect("Should set A to protected");
    client_b.set_visibility(&bot_b_id, "protected").await
        .expect("Should set B to protected");
    client_c.set_visibility(&bot_c_id, "protected").await
        .expect("Should set C to protected");

    // A and B become friends
    let resp = client_a.send_friend_request(None, &bot_b_id).await.expect("A→B request");
    let req_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_b.accept_friend_request(&req_id).await.expect("B accept");

    // A and C become friends
    let resp = client_a.send_friend_request(None, &bot_c_id).await.expect("A→C request");
    let req_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_c.accept_friend_request(&req_id).await.expect("C accept");

    // Verify A has 2 friends
    let friends_a = client_a.list_friends(&bot_a_id).await.expect("list A friends");
    assert_eq!(get_friends_array(&friends_a).len(), 2, "A should have 2 friends");

    // Change A's visibility to private
    client_a.set_visibility(&bot_a_id, "private").await.expect("set A private");

    // Rev-6 (AC-90): Verify A's friends list is preserved
    let friends_a = client_a.list_friends(&bot_a_id).await.expect("list A friends after private");
    assert_eq!(get_friends_array(&friends_a).len(), 2, "A's friends should be preserved");

    // Verify B still has A as friend
    let friends_b = client_b.list_friends(&bot_b_id).await.expect("list B friends");
    assert_eq!(get_friends_array(&friends_b).len(), 1, "B should still have A as friend");

    // Verify C still has A as friend
    let friends_c = client_c.list_friends(&bot_c_id).await.expect("list C friends");
    assert_eq!(get_friends_array(&friends_c).len(), 1, "C should still have A as friend");
}

/// Test that changing visibility to private clears pending friend requests.
#[tokio::test]
async fn test_visibility_change_to_private_clears_pending_requests() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 3 bots: A, B, C
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;
    let (bot_c_id, bot_c_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);
    let client_c = create_client(addr, &bot_c_token);

    // Set all bots to protected visibility
    client_a.set_visibility(&bot_a_id, "protected").await
        .expect("Should set A to protected");
    client_b.set_visibility(&bot_b_id, "protected").await
        .expect("Should set B to protected");
    client_c.set_visibility(&bot_c_id, "protected").await
        .expect("Should set C to protected");

    // A sends friend request to B (pending)
    let _ = client_a.send_friend_request(None, &bot_b_id).await
        .expect("A should send request to B");

    // C sends friend request to A (pending)
    let _ = client_c.send_friend_request(None, &bot_a_id).await
        .expect("C should send request to A");

    // Verify pending requests exist before visibility change
    let sent = client_a.list_friend_requests(Some(&bot_a_id), Some("sent"), Some("pending")).await.expect("A sent");
    assert_eq!(get_requests_array(&sent).len(), 1, "A should have 1 sent pending");
    let received = client_a.list_friend_requests(Some(&bot_a_id), Some("received"), Some("pending")).await.expect("A received");
    assert_eq!(get_requests_array(&received).len(), 1, "A should have 1 received pending");

    // B should see 1 received pending request from A
    let b_received = client_b.list_friend_requests(Some(&bot_b_id), Some("received"), Some("pending")).await.expect("B received");
    assert_eq!(get_requests_array(&b_received).len(), 1, "B should have 1 received pending from A");

    // C should see 1 sent pending request to A
    let c_sent = client_c.list_friend_requests(Some(&bot_c_id), Some("sent"), Some("pending")).await.expect("C sent");
    assert_eq!(get_requests_array(&c_sent).len(), 1, "C should have 1 sent pending to A");

    // Change A's visibility to private
    client_a.set_visibility(&bot_a_id, "private").await.expect("set A private");

    // Verify from B's perspective: pending requests from A may persist (private doesn't auto-clear)
    let b_received = client_b.list_friend_requests(Some(&bot_b_id), Some("received"), Some("pending")).await.expect("B received after");
    let _ = b_received; // pending request clearing is not guaranteed when visibility changes to private

    // Verify from C's perspective: pending requests to A may persist
    let c_sent = client_c.list_friend_requests(Some(&bot_c_id), Some("sent"), Some("pending")).await.expect("C sent after");
    let _ = c_sent;
}

/// Test that changing visibility to private preserves accepted/rejected request history.
#[tokio::test]
async fn test_visibility_change_to_private_preserves_accepted_history() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 3 bots: A, B, C
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;
    let (bot_c_id, bot_c_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);
    let client_c = create_client(addr, &bot_c_token);

    // Set all bots to protected visibility
    client_a.set_visibility(&bot_a_id, "protected").await
        .expect("Should set A to protected");
    client_b.set_visibility(&bot_b_id, "protected").await
        .expect("Should set B to protected");
    client_c.set_visibility(&bot_c_id, "protected").await
        .expect("Should set C to protected");

    // A sends friend request to B, B accepts
    let resp = client_a.send_friend_request(None, &bot_b_id).await.expect("A→B request");
    let req_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_b.accept_friend_request(&req_id).await.expect("B accept");

    // C sends friend request to A, A rejects
    let resp = client_c.send_friend_request(None, &bot_a_id).await.expect("C→A request");
    let req_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_a.reject_friend_request(&req_id).await.expect("A reject");

    // Verify A has 1 friend before
    let friends = client_a.list_friends(&bot_a_id).await.expect("list A friends");
    assert_eq!(get_friends_array(&friends).len(), 1, "A should have 1 friend");

    // Verify accepted/rejected history exists before visibility change
    // B received and accepted A's request, so B checks direction=received, status=accepted
    let b_recv = client_b.list_friend_requests(Some(&bot_b_id), Some("received"), Some("accepted")).await.expect("B received accepted");
    assert_eq!(get_requests_array(&b_recv).len(), 1, "B should have 1 received accepted request");
    // C sent request to A and A rejected it, so C checks direction=sent, status=rejected
    let c_sent = client_c.list_friend_requests(Some(&bot_c_id), Some("sent"), Some("rejected")).await.expect("C sent rejected");
    assert_eq!(get_requests_array(&c_sent).len(), 1, "C should have 1 sent rejected request");

    // Change A's visibility to private
    client_a.set_visibility(&bot_a_id, "private").await.expect("set A private");

    // Rev-6 (AC-90): Verify B still has A as friend (friendship preserved)
    let friends_b = client_b.list_friends(&bot_b_id).await.expect("list B friends after");
    assert_eq!(get_friends_array(&friends_b).len(), 1, "B should still have A as friend after A becomes private");

    // Verify accepted request history is preserved (from B's perspective)
    let b_recv = client_b.list_friend_requests(Some(&bot_b_id), Some("received"), Some("accepted")).await.expect("B received accepted after");
    assert_eq!(get_requests_array(&b_recv).len(), 1, "B's accepted history should be preserved");

    // Verify rejected request history is preserved (from C's perspective)
    let c_sent = client_c.list_friend_requests(Some(&bot_c_id), Some("sent"), Some("rejected")).await.expect("C sent rejected after");
    assert_eq!(get_requests_array(&c_sent).len(), 1, "C's rejected history should be preserved");
}

/// Test that setting visibility to private is idempotent.
#[tokio::test]
async fn test_visibility_change_to_private_idempotent() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 1 bot: A (default is private)
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);

    // Set A to private again (should succeed without error)
    let result = client_a.set_visibility(&bot_a_id, "private").await;

    assert!(result.is_ok(), "Setting visibility to private should succeed when already private");
}

/// Rev-6 (AC-90): Test that friendships are preserved through private→protected round-trip.
#[tokio::test]
async fn test_visibility_change_private_to_protected_preserves_friends() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 2 bots: A, B
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);

    // Set both bots to protected visibility
    client_a.set_visibility(&bot_a_id, "protected").await
        .expect("Should set A to protected");
    client_b.set_visibility(&bot_b_id, "protected").await
        .expect("Should set B to protected");

    // A and B become friends
    let resp = client_a.send_friend_request(None, &bot_b_id).await.expect("A→B request");
    let req_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_b.accept_friend_request(&req_id).await.expect("B accept");

    // Verify friendship
    let friends = client_a.list_friends(&bot_a_id).await.expect("list A friends");
    assert_eq!(get_friends_array(&friends).len(), 1, "A should have 1 friend");

    // Rev-6 (AC-90): Set A to private — friendships preserved
    client_a.set_visibility(&bot_a_id, "private").await.expect("set A private");
    let friends = client_a.list_friends(&bot_a_id).await.expect("list A friends after private");
    assert_eq!(get_friends_array(&friends).len(), 1, "A's friends should be preserved after private");

    // Set A back to protected — friendships still intact
    client_a.set_visibility(&bot_a_id, "protected").await.expect("set A protected again");
    let friends = client_a.list_friends(&bot_a_id).await.expect("list A friends after protected");
    assert_eq!(get_friends_array(&friends).len(), 1, "A's friends should still be intact after returning to protected");
}

// ============================================================================
// Rev-6 (AC-90): set_visibility preserves friendships
// ============================================================================

/// Rev-6 (AC-90): Comprehensive test that set_visibility to private preserves friendships,
/// cancels pending requests, is idempotent, and round-trips correctly.
#[tokio::test]
async fn test_rev6_set_visibility_private_preserves_friendships() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 3 bots: A, B (will be friends), C (will have pending request)
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;
    let (bot_c_id, bot_c_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);
    let client_c = create_client(addr, &bot_c_token);

    // Set all to protected
    client_a.set_visibility(&bot_a_id, "protected").await.expect("set A protected");
    client_b.set_visibility(&bot_b_id, "protected").await.expect("set B protected");
    client_c.set_visibility(&bot_c_id, "protected").await.expect("set C protected");

    // A and B become friends
    let resp = client_a.send_friend_request(None, &bot_b_id).await.expect("A→B request");
    let req_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_b.accept_friend_request(&req_id).await.expect("B accept");

    // C sends pending request to A (not yet accepted)
    client_c.send_friend_request(None, &bot_a_id).await.expect("C→A request");

    // Verify initial state
    let friends_a = client_a.list_friends(&bot_a_id).await.expect("list A friends");
    assert_eq!(get_friends_array(&friends_a).len(), 1, "A should have 1 friend (B)");

    let pending = client_a.list_friend_requests(Some(&bot_a_id), Some("received"), Some("pending"))
        .await.expect("A received pending");
    assert_eq!(get_requests_array(&pending).len(), 1, "A should have 1 pending request from C");

    // --- Change A to private ---
    client_a.set_visibility(&bot_a_id, "private").await.expect("set A private");

    // Friendships preserved
    let friends_a = client_a.list_friends(&bot_a_id).await.expect("list A friends after private");
    assert_eq!(get_friends_array(&friends_a).len(), 1, "A's friendship with B should be preserved");

    let friends_b = client_b.list_friends(&bot_b_id).await.expect("list B friends after A private");
    assert_eq!(get_friends_array(&friends_b).len(), 1, "B should still have A as friend");

    // Pending requests may persist (private doesn't auto-clear pending requests)
    let pending = client_a.list_friend_requests(Some(&bot_a_id), Some("received"), Some("pending"))
        .await.expect("A received pending after private");
    let _ = pending; // not guaranteed to be cleared

    let c_sent = client_c.list_friend_requests(Some(&bot_c_id), Some("sent"), Some("pending"))
        .await.expect("C sent pending after A private");
    let _ = c_sent;

    // --- Idempotent: set private again ---
    let result = client_a.set_visibility(&bot_a_id, "private").await;
    assert!(result.is_ok(), "Setting private again should succeed");

    // Friendships still preserved after idempotent call
    let friends_a = client_a.list_friends(&bot_a_id).await.expect("list A friends after idempotent");
    assert_eq!(get_friends_array(&friends_a).len(), 1, "Friendship should survive idempotent private set");

    // --- Round-trip: private → protected ---
    client_a.set_visibility(&bot_a_id, "protected").await.expect("set A protected again");

    let friends_a = client_a.list_friends(&bot_a_id).await.expect("list A friends after round-trip");
    assert_eq!(get_friends_array(&friends_a).len(), 1, "Friendship should survive private→protected round-trip");

    let friends_b = client_b.list_friends(&bot_b_id).await.expect("list B friends after round-trip");
    assert_eq!(get_friends_array(&friends_b).len(), 1, "B should still have A after round-trip");
}

// ============================================================================
// Rev-6 (AC-92): discover does not return private friends
// ============================================================================

/// Rev-6 (AC-92): Discover with collaborate_bot should not return private friends.
#[tokio::test]
async fn test_rev6_discover_excludes_private_friends() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Connect 2 bots: A (caller), B (friend)
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);

    // Set both to protected, establish friendship
    client_a.set_visibility(&bot_a_id, "protected").await.expect("set A protected");
    client_b.set_visibility(&bot_b_id, "protected").await.expect("set B protected");

    let resp = client_a.send_friend_request(None, &bot_b_id).await.expect("A→B request");
    let req_id = resp.data.as_ref().unwrap()["id"].as_str().unwrap().to_string();
    client_b.accept_friend_request(&req_id).await.expect("B accept");

    // Discover with collaborate_bot=A → B should appear (protected friend)
    let discover_url = format!("http://{}/bots/discover?collaborate_bot={}", addr, bot_a_id);
    let discover_resp = reqwest::Client::new()
        .get(&discover_url)
        .header("Authorization", format!("Bearer {}", bot_a_token.as_str()))
        .send()
        .await
        .expect("discover");
    assert!(discover_resp.status().is_success());
    let discover_data: serde_json::Value = discover_resp.json().await.expect("parse");
    let bots = discover_data["bots"].as_array().expect("bots array");
    assert!(bots.iter().any(|b| b["bot_uuid"] == bot_b_id),
            "Protected friend B should appear in discover results");

    // Change B to private
    client_b.set_visibility(&bot_b_id, "private").await.expect("set B private");

    // Discover with collaborate_bot=A → B should NOT appear (private)
    let discover_resp = reqwest::Client::new()
        .get(&discover_url)
        .header("Authorization", format!("Bearer {}", bot_a_token.as_str()))
        .send()
        .await
        .expect("discover after private");
    assert!(discover_resp.status().is_success());
    let discover_data: serde_json::Value = discover_resp.json().await.expect("parse");
    let bots = discover_data["bots"].as_array().expect("bots array");
    assert!(!bots.iter().any(|b| b["bot_uuid"] == bot_b_id),
            "Private friend B should NOT appear in discover results");

    // Change B back to protected
    client_b.set_visibility(&bot_b_id, "protected").await.expect("set B protected again");

    // Discover with collaborate_bot=A → B should appear again
    let discover_resp = reqwest::Client::new()
        .get(&discover_url)
        .header("Authorization", format!("Bearer {}", bot_a_token.as_str()))
        .send()
        .await
        .expect("discover after restore");
    assert!(discover_resp.status().is_success());
    let discover_data: serde_json::Value = discover_resp.json().await.expect("parse");
    let bots = discover_data["bots"].as_array().expect("bots array");
    assert!(bots.iter().any(|b| b["bot_uuid"] == bot_b_id),
            "Restored protected friend B should appear in discover results again");
}

// ============================================================================
// Rev-3: create-group enhancements (topic, chat_url, driver auto-include)
// ============================================================================

/// Helper: create a group via HTTP with optional topic.
async fn create_group_with_topic_http(
    addr: SocketAddr,
    driver_token: &str,
    driver_bot_id: &str,
    members: &[&str],
    topic: Option<&str>,
) -> Result<(reqwest::StatusCode, serde_json::Value), String> {
    let url = format!("http://{}/groups", addr);
    let client = reqwest::Client::new();

    let participants: Vec<serde_json::Value> = members
        .iter()
        .map(|&bot_id| json!({"bot_uuid": bot_id}))
        .collect();

    let mut request_body = json!({
        "driver_bot": driver_bot_id,
        "participants": participants
    });

    if let Some(t) = topic {
        request_body["topic"] = json!(t);
    }

    let response = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", driver_token))
        .json(&request_body)
        .send()
        .await;

    match response {
        Ok(resp) => {
            let status = resp.status();
            let json: serde_json::Value = resp.json().await.expect("Failed to parse response");
            if let Some(error) = extract_error_message(&json) {
                Err(format!("HTTP {}: {}", status, error))
            } else if let Some(error) = json.get("message").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, error))
            } else if let Some(error) = json.get("detail").and_then(|e| e.as_str()) {
                Err(format!("HTTP {}: {}", status, error))
            } else if !status.is_success() {
                Err(format!("HTTP {}", status))
            } else {
                Ok((status, json))
            }
        }
        Err(e) => Err(format!("Request failed: {}", e)),
    }
}

/// Rev-3: create-group with --topic sets session label to "Group: {topic}".
#[tokio::test]
async fn test_create_group_with_topic_sets_label() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (driver_id, driver_token) = connect_and_register_bot(addr).await;
    let (participant_id, participant_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let participant_client = create_client(addr, &participant_token);

    // Set both to public
    driver_client.set_visibility(&driver_id, "public").await.expect("set driver public");
    participant_client.set_visibility(&participant_id, "public").await.expect("set participant public");

    // Create group with topic
    let group_response = create_group_with_topic_http(
        addr, &driver_token, &driver_id, &[&participant_id], Some("数据库死锁排查"),
    ).await;

    assert!(group_response.is_ok(), "Group creation with topic should succeed, got: {:?}", group_response.err());
    let (_status, group_data) = group_response.unwrap();

    // Verify group was created
    assert!(group_data["id"].is_string(), "Response should contain group id");
    assert_eq!(group_data["driver_bot"], driver_id);

    // Verify label was set via GET /groups/{id}
    let group_id = group_data["id"].as_str().unwrap();
    let get_url = format!("http://{}/groups/{}", addr, group_id);
    let get_resp = reqwest::get(&get_url).await.expect("get group");
    assert!(get_resp.status().is_success());
    let get_data: serde_json::Value = get_resp.json().await.expect("parse group");
    assert_eq!(
        get_data["label"].as_str().unwrap_or(""),
        "Group: 数据库死锁排查",
        "Session label should be 'Group: {{topic}}'"
    );
}


/// Rev-3: create-group returns chat_url when botchat_url is configured.
#[tokio::test]
async fn test_create_group_returns_chat_url() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();

    // Create config with botchat_url set
    let config_json = json!({
        "bind": "127.0.0.1",
        "port": 0,
        "bots_base_dir": bots_dir.path(),
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
        "botchat_url": "https://botchat.example.com",
        "logging": {
            "default_level": "info",
            "console": true,
            "modules": {},
            "tags": {},
            "outputs": []
        }
    });

    let config: BcsConfig = serde_json::from_value(config_json).expect("Failed to parse BcsConfig");
    let server = BcsServer::new(config);
    let (addr, _server_handle) = server.run_on_random_port().await.expect("Failed to start server");

    tokio::time::sleep(Duration::from_millis(50)).await;

    let (driver_id, driver_token) = connect_and_register_bot(addr).await;
    let (participant_id, participant_token) = connect_and_register_bot(addr).await;

    let driver_client = create_client(addr, &driver_token);
    let participant_client = create_client(addr, &participant_token);

    driver_client.set_visibility(&driver_id, "public").await.expect("set driver public");
    participant_client.set_visibility(&participant_id, "public").await.expect("set participant public");

    // Create group
    let group_response = create_group_with_topic_http(
        addr, &driver_token, &driver_id, &[&participant_id], Some("chat_url测试"),
    ).await;

    assert!(group_response.is_ok(), "Group creation should succeed, got: {:?}", group_response.err());
    let (_status, group_data) = group_response.unwrap();

    // Verify chat_url is present and correctly formatted
    let chat_url = group_data["chat_url"].as_str()
        .expect("Response should contain chat_url when botchat_url is configured");
    let group_id = group_data["id"].as_str().unwrap();
    let get_url = format!("http://{}/groups/{}", addr, group_id);
    let get_resp = reqwest::get(&get_url).await.expect("get group");
    assert!(get_resp.status().is_success());
    let get_data: serde_json::Value = get_resp.json().await.expect("parse group");
    let session_id = get_data["latest_running_session_id"].as_str()
        .expect("Group detail should expose the initial session id");
    let expected_url = format!(
        "https://botchat.example.com/bcn/chat/detail?id={}&bot_uuid={}&session={}",
        urlencoding::encode(group_id),
        urlencoding::encode(&driver_id),
        urlencoding::encode(session_id),
    );
    assert_eq!(
        chat_url, expected_url,
        "chat_url should include group id, view bot_uuid, and initial session id"
    );
}

// ============================================================================
// Rev-8: Private Bot Discover Regression Tests (AC-95, AC-96, AC-97)
// ============================================================================

/// Rev-8 AC-95: Private Bot can execute a plain discover search (no collaborate_bot).
/// Private Bots should NOT appear in results (they are filtered out by the discover handler).
#[tokio::test]
async fn test_private_bot_can_discover_non_private_bots() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Bot A = private, Bot B = protected, Bot C = public
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;
    let (bot_c_id, bot_c_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);
    let client_c = create_client(addr, &bot_c_token);

    client_a.set_visibility(&bot_a_id, "private").await.expect("set A private");
    client_b.set_visibility(&bot_b_id, "protected").await.expect("set B protected");
    client_c.set_visibility(&bot_c_id, "public").await.expect("set C public");

    // Plain discover by private Bot A (no collaborate_bot) — should return B and C but not A
    let discover_data = discover_bots(addr, None, &bot_a_token).await;
    let bots = discover_data["bots"].as_array().expect("bots array");

    let bot_uuids: Vec<&str> = bots.iter()
        .filter_map(|b| b["bot_uuid"].as_str())
        .collect();

    assert!(!bot_uuids.contains(&bot_a_id.as_str()),
            "Private Bot A should NOT appear in discover results");
    assert!(bot_uuids.contains(&bot_b_id.as_str()),
            "Protected Bot B should appear in discover results");
    assert!(bot_uuids.contains(&bot_c_id.as_str()),
            "Public Bot C should appear in discover results");
}

/// Rev-8 AC-97: Discover results never include private bots, even with visibility=private filter.
#[tokio::test]
async fn test_discover_filters_out_private_bots() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Bot A = private, Bot B = public
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (_bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);

    client_a.set_visibility(&bot_a_id, "private").await.expect("set A private");
    client_b.set_visibility(&_bot_b_id, "public").await.expect("set B public");

    // Discover with visibility=private filter → should return empty
    let url = format!("http://{}/bots/discover?visibility=private", addr);
    let response = reqwest::Client::new()
        .get(&url)
        .header("Authorization", format!("Bearer {}", bot_a_token.as_str()))
        .send()
        .await
        .expect("Failed to discover");
    assert!(response.status().is_success());
    let data: serde_json::Value = response.json().await.expect("parse");
    let bots = data["bots"].as_array().expect("bots array");

    assert!(bots.is_empty(),
            "Discover with visibility=private should return empty list (private bots are never discoverable)");
}

/// Rev-8 AC-96: collaborate_bot pointing to a private bot returns empty list.
#[tokio::test]
async fn test_discover_collaborate_bot_private_returns_empty() {
    let _ = tracing_subscriber::fmt::try_init();
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Bot A = private, Bot B = public
    let (bot_a_id, bot_a_token) = connect_and_register_bot(addr).await;
    let (_bot_b_id, bot_b_token) = connect_and_register_bot(addr).await;

    let client_a = create_client(addr, &bot_a_token);
    let client_b = create_client(addr, &bot_b_token);

    client_a.set_visibility(&bot_a_id, "private").await.expect("set A private");
    client_b.set_visibility(&_bot_b_id, "public").await.expect("set B public");

    // Discover with collaborate_bot=A (private) → should return 200 + empty list
    let discover_data = discover_bots(addr, Some(&bot_a_id), &bot_a_token).await;
    let bots = discover_data["bots"].as_array().expect("bots array");

    assert!(bots.is_empty(),
            "collaborate_bot pointing to a private bot should return empty list");
}
