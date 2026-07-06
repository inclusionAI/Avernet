//! Integration tests for WebSocket token pre-validation on `/ws/bot`.
//!
//! Tests the behavior matrix from the design:
//! - No token: allow connection (new bot)
//! - Empty token: allow connection (treated as no token)
//! - Valid token: allow connection (reconnecting bot)
//! - Invalid token: reject with 401

mod helpers;

use helpers::{create_temp_bots_dir, start_test_server, MockBot};

/// Test: No token query parameter — connection should succeed (new bot scenario).
#[tokio::test]
async fn test_ws_no_token_allows_connection() {
    let temp_dir = create_temp_bots_dir();
    let (addr, _handle) = start_test_server(&temp_dir.path().to_path_buf()).await;

    // Connect without any token query parameter — should work
    let bot = MockBot::connect(addr).await;
    assert!(!bot.bot_id.is_empty(), "Should get a bot_id for new connection");
    assert!(!bot.token.is_empty(), "Should get a token for new connection");
}

/// Test: Empty token query parameter — connection should succeed (treated as no token).
#[tokio::test]
async fn test_ws_empty_token_allows_connection() {
    let temp_dir = create_temp_bots_dir();
    let (addr, _handle) = start_test_server(&temp_dir.path().to_path_buf()).await;

    // Connect with empty token query parameter
    let url = format!("ws://{}/ws/bot?token=", addr);
    let result = tokio_tungstenite::connect_async(&url).await;
    assert!(result.is_ok(), "Empty token should allow WebSocket upgrade");
}

/// Test: Valid token (from a previously connected bot) — connection should succeed.
#[tokio::test]
async fn test_ws_valid_token_allows_connection() {
    let temp_dir = create_temp_bots_dir();
    let (addr, _handle) = start_test_server(&temp_dir.path().to_path_buf()).await;

    // First, connect a bot to get a valid token
    let bot = MockBot::connect(addr).await;
    let valid_token = bot.token.clone();
    drop(bot);

    // Now connect with the valid token as query parameter
    let url = format!("ws://{}/ws/bot?token={}", addr, valid_token);
    let result = tokio_tungstenite::connect_async(&url).await;
    assert!(result.is_ok(), "Valid token should allow WebSocket upgrade");
}
