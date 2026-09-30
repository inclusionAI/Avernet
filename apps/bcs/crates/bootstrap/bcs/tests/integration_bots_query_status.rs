//! Integration tests for POST /bots/query status field behavior.
//!
//! These tests verify the Requirements:
//! - R1.AC-19: Query endpoint returns status field reflecting ActorStatus lifecycle switch
//! - R1.AC-20: status="online" when ActorStatus::Online (default)
//! - R1.AC-21: status="hidden" when ActorStatus::Hidden (explicit offline switch)
//!
//! The status field is the **raw lifecycle switch** (online/hidden), not the
//! computed `bot_is_effectively_online` which considers visibility.

mod helpers;
use helpers::{MockBot, create_temp_bots_dir, start_test_server};

use std::time::Duration;
use serde_json::json;

#[tokio::test]
async fn q1_hidden_actor_query_returns_hidden_status() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Onboard a bot with public visibility
    let mut bot = MockBot::connect(addr).await;
    bot.register("test_bot", &["chat"], addr).await;
    
    let client = bot.http_client(addr);
    let bot_uuid = bot.bot_id.clone();
    let token = bot.token.clone();

    // Initially, status should be "online" (default)
    let online_entries = client
        .query_bots(vec![bot_uuid.clone()])
        .await
        .expect("query bots for online status");
    
    assert_eq!(online_entries.len(), 1);
    let online_entry = &online_entries[0];
    assert_eq!(online_entry.bot_uuid, bot_uuid);
    assert_eq!(online_entry.status, Some("online".to_string()),
               "Default status should be 'online'");

    // Set the bot to Hidden via PUT /actors/{aid}/status
    let http_client = reqwest::Client::new();
    let set_hidden_resp = http_client
        .put(format!("http://{}/actors/{}/status", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .json(&json!({"status": "hidden"}))
        .send()
        .await
        .expect("set actor status to hidden");
    
    assert_eq!(set_hidden_resp.status(), 200, "Setting Hidden should succeed");

    // Query again - status should now be "hidden"
    let hidden_entries = client
        .query_bots(vec![bot_uuid.clone()])
        .await
        .expect("query bots for hidden status");
    
    assert_eq!(hidden_entries.len(), 1);
    let hidden_entry = &hidden_entries[0];
    assert_eq!(hidden_entry.bot_uuid, bot_uuid);
    assert_eq!(hidden_entry.status, Some("hidden".to_string()),
               "Hidden actor should return status='hidden' (not 'offline')");

    // Verify round-trip deserialization preserves the status value
    assert_eq!(hidden_entry.status.as_deref(), Some("hidden"),
               "Round-trip deserialization should preserve 'hidden' status");
}

#[tokio::test]
async fn q1_online_actor_query_returns_online_status() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    tokio::time::sleep(Duration::from_millis(50)).await;

    // Onboard a bot
    let mut bot = MockBot::connect(addr).await;
    bot.register("test_bot", &["chat"], addr).await;
    
    let client = bot.http_client(addr);
    let bot_uuid = bot.bot_id.clone();

    // Send heartbeat to ensure actor is online
    bot.send_heartbeat().await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // Query - status should be "online"
    let entries = client
        .query_bots(vec![bot_uuid.clone()])
        .await
        .expect("query bots for online status");
    
    assert_eq!(entries.len(), 1);
    let entry = &entries[0];
    assert_eq!(entry.bot_uuid, bot_uuid);
    assert_eq!(entry.status, Some("online".to_string()),
               "Online actor should return status='online'");
}

/// Q-1 (Rev-1): /bots/query includes actor_kind and dynamic_status fields.
#[tokio::test]
async fn q1_rev1_query_bots_actor_kind_and_dynamic_status() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot = MockBot::connect(addr).await;
    bot.register("QueryBotQ1Rev1", &["chat"], addr).await;
    let client = bot.http_client(addr);
    let bot_uuid = bot.bot_id.clone();

    let entries = client
        .query_bots(vec![bot_uuid.clone()])
        .await
        .expect("query bots");

    assert_eq!(entries.len(), 1);
    let entry = &entries[0];

    // actor_kind should exist
    assert!(entry.actor_kind.is_some(), "actor_kind should be present");
    assert_eq!(entry.actor_kind.as_deref(), Some("bot"),
               "actor_kind should be 'bot'");

    // dynamic_status should exist
    assert!(entry.dynamic_status.is_some(), "dynamic_status should be present");
    let ds = entry.dynamic_status.as_ref().unwrap();
    assert_eq!(ds.status, "active", "connected bot dynamic_status should be 'active'");

    drop(bot);
}

/// Q-2 (Rev-1): /bots/query preserves original status field.
#[tokio::test]
async fn q2_rev1_query_bots_status_preserved() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot = MockBot::connect(addr).await;
    bot.register("QueryBotQ2Rev1", &["chat"], addr).await;
    let client = bot.http_client(addr);
    let bot_uuid = bot.bot_id.clone();
    let token = bot.token.clone();

    // Default: status should be "online"
    let entries = client
        .query_bots(vec![bot_uuid.clone()])
        .await
        .expect("query bots");
    assert_eq!(entries[0].status, Some("online".to_string()),
               "Default lifecycle status should be 'online'");

    // Set Hidden: status should be "hidden"
    let http_client = reqwest::Client::new();
    http_client
        .put(format!("http://{}/actors/{}/status", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token))
        .json(&serde_json::json!({"status": "hidden"}))
        .send()
        .await
        .expect("set hidden");

    let entries2 = client
        .query_bots(vec![bot_uuid.clone()])
        .await
        .expect("query bots after hidden");
    assert_eq!(entries2[0].status, Some("hidden".to_string()),
               "After Hidden, status should be 'hidden' (not 'offline')");

    // dynamic_status should reflect the Hidden state as offline
    let ds = entries2[0].dynamic_status.as_ref().expect("dynamic_status should exist");
    assert_eq!(ds.status, "offline", "Hidden bot dynamic_status should be 'offline'");

    drop(bot);
}
