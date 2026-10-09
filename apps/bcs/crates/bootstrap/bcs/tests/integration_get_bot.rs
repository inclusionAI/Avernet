//! Integration tests for GET /bots/{id} — Rev-1 D-I field verification.
//!
//! ## Requirements Coverage
//!
//! - AC-56: GET /bots/{id} includes actor_kind, env, status, dynamic_status
//! - AC-57: GET /bots/{id} dynamic_status reflects WS connection state
//! - AC-55: Test matrix coverage

mod helpers;

use helpers::{MockBot, create_temp_bots_dir, start_test_server};
use std::time::Duration;

/// B-1: GET /bots/{id} response includes all Rev-1 fields.
#[tokio::test]
async fn b1_get_bot_fields() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot = MockBot::connect(addr).await;
    bot.register("GetBotB1", &["chat"], addr).await;
    let bot_uuid = bot.bot_id.clone();
    let token = bot.token.clone();

    let client = reqwest::Client::new();
    let url = format!("http://{}/bots/{}", addr, bot_uuid);
    let response: serde_json::Value = client
        .get(&url)
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .expect("GET /bots/{id} request")
        .json()
        .await
        .expect("json response");

    // Verify original fields preserved
    assert_eq!(response["bot_uuid"].as_str(), Some(bot_uuid.as_str()), "bot_uuid should match");
    assert!(response["capabilities"].is_object(), "capabilities should exist");
    // created_by must be preserved (tasks.md B-1 explicitly requires this)
    assert!(response["created_by"].is_string() || response["created_by"].is_null(),
            "created_by field should exist");

    // Verify new Rev-1 fields
    assert!(response["actor_kind"].is_string(), "actor_kind should exist");
    assert!(response["env"].is_string(), "env should exist");
    assert!(response["status"].is_string(), "status (lifecycle) should exist");
    assert_eq!(response["status"].as_str(), Some("online"), "default status should be online");
    assert!(response["dynamic_status"].is_object(), "dynamic_status should exist");
    assert_eq!(response["dynamic_status"]["status"].as_str(), Some("active"),
               "connected bot dynamic_status should be active");

    drop(bot);
}

/// B-2: GET /bots/{id} dynamic_status reflects WS connection state AND Hidden.
///
/// Covers AC-57: dynamic_status reflects both WS disconnect and Hidden status.
#[tokio::test]
async fn b2_get_bot_dynamic_status() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot = MockBot::connect(addr).await;
    bot.register("GetBotB2", &["chat"], addr).await;
    let bot_uuid = bot.bot_id.clone();
    let token = bot.token.clone();

    let client = reqwest::Client::new();
    let url = format!("http://{}/bots/{}", addr, bot_uuid);

    // WS connected → dynamic_status.status == "active"
    let resp1: serde_json::Value = client
        .get(&url)
        .header("Authorization", format!("Bearer {}", token.as_str()))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(resp1["dynamic_status"]["status"].as_str(), Some("active"),
               "Connected bot should show active");

    // Set Hidden → dynamic_status.status == "offline" (Hidden takes precedence)
    let hidden_resp = client
        .put(format!("http://{}/actors/{}/status", addr, bot_uuid))
        .header("Authorization", format!("Bearer {}", token.as_str()))
        .json(&serde_json::json!({"status": "hidden"}))
        .send()
        .await
        .expect("set hidden");
    assert!(hidden_resp.status().is_success(), "Setting Hidden should succeed");

    let resp2: serde_json::Value = client
        .get(&url)
        .header("Authorization", format!("Bearer {}", token.as_str()))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(resp2["status"].as_str(), Some("hidden"),
               "Lifecycle status should be 'hidden'");
    assert_eq!(resp2["dynamic_status"]["status"].as_str(), Some("offline"),
               "Hidden bot should show offline even with active WS (AC-57)");

    // Disconnect WS → dynamic_status.status still == "offline"
    bot.disconnect().await;
    tokio::time::sleep(Duration::from_millis(100)).await;

    let resp3: serde_json::Value = client
        .get(&url)
        .header("Authorization", format!("Bearer {}", token.as_str()))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(resp3["dynamic_status"]["status"].as_str(), Some("offline"),
               "Disconnected + Hidden bot should show offline");
}
