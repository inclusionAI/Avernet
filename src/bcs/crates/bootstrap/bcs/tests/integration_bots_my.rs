//! Integration tests for GET /bots/my — Rev-1 D-F handler-shape verification.
//!
//! ## Requirements Coverage
//!
//! - AC-42: /bots/my response includes `actor_kind` field
//! - AC-43: /bots/my response includes `env` field
//! - AC-44: /bots/my response includes `status` (lifecycle) and `dynamic_status` fields
//! - AC-52: /bots/my response includes `created_by` field
//! - AC-55: Test matrix coverage
//!
//! ## AC-41 Verification Scope
//!
//! Current `start_test_server` uses InMemory registry. These tests validate
//! handler-layer field correctness (AC-42/43/44), NOT the unified DB query
//! data source switch (AC-41 core). Sidecar-backed verification is future work.

mod helpers;

use helpers::{MockBot, create_temp_bots_dir, start_test_server, onboard_bot_as_user, query_my_bots};
use std::time::Duration;

/// M-1: /bots/my returns registered actors with correct fields.
///
/// Onboarding with mock user identity triggers `ensure_human_actor`, creating
/// both the bot row AND a `human_{staff_no}` row with the same `created_by`.
/// AC-43 requires `/bots/my` to return **both** the bot and the human actor.
#[tokio::test]
async fn m1_my_bots_handler_returns_registered_actors() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let staff_no = "test_staff_001";
    let human_id = format!("human_{}", staff_no);

    // Connect a bot via WS and onboard with mock user identity.
    // This creates both the bot row and the human_test_staff_001 row.
    let bot = MockBot::connect(addr).await;
    let token = bot.token.clone();
    let bot_uuid = bot.bot_id.clone();
    onboard_bot_as_user(addr, &token, "MyBot1", staff_no).await;

    // Query /bots/my
    let resp = query_my_bots(addr, staff_no).await;
    let items = resp["items"].as_array().expect("items should be array");
    let total = resp["total"].as_u64().expect("total should be number");

    assert!(total >= 2, "Should have at least 2 entries (bot + human)");
    assert!(items.len() >= 2, "Items should contain at least 2 entries");

    // Find the bot entry
    let my_bot = items.iter()
        .find(|item| item["capabilities"]["name"].as_str() == Some("MyBot1"))
        .expect("MyBot1 should be in results");

    // Verify all required fields on the bot entry
    assert_eq!(my_bot["bot_uuid"].as_str(), Some(bot_uuid.as_str()), "bot_uuid should match");
    assert!(my_bot["capabilities"].is_object(), "capabilities should exist");
    assert_eq!(my_bot["created_by"].as_str(), Some(staff_no), "created_by should match staff_no");
    assert_eq!(my_bot["actor_kind"].as_str(), Some("bot"), "actor_kind should be 'bot'");
    assert!(my_bot["env"].is_string(), "env should exist");
    assert_eq!(my_bot["status"].as_str(), Some("online"), "default lifecycle status should be online");
    assert!(my_bot["dynamic_status"].is_object(), "dynamic_status should exist");
    assert_eq!(my_bot["dynamic_status"]["status"].as_str(), Some("active"),
               "connected bot dynamic_status should be active");

    // Find the human actor entry — AC-43 requires it to be present
    let my_human = items.iter()
        .find(|item| item["bot_uuid"].as_str() == Some(human_id.as_str()))
        .expect("human actor should also be in /bots/my results (AC-43)");

    assert_eq!(my_human["actor_kind"].as_str(), Some("human"), "human entry actor_kind should be 'human'");
    assert_eq!(my_human["created_by"].as_str(), Some(staff_no), "human created_by should match staff_no");

    drop(bot);
}

/// M-2: /bots/my dynamic_status reflects WS connection state.
#[tokio::test]
async fn m2_my_bots_dynamic_status_active_when_ws_connected() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let staff_no = "test_staff_002";

    let mut bot = MockBot::connect(addr).await;
    let token = bot.token.clone();
    onboard_bot_as_user(addr, &token, "MyBot2", staff_no).await;

    // WS connected → dynamic_status.status == "active"
    let resp = query_my_bots(addr, staff_no).await;
    let items = resp["items"].as_array().unwrap();
    let my_bot = items.iter()
        .find(|item| item["capabilities"]["name"].as_str() == Some("MyBot2"))
        .expect("MyBot2 should be in results");
    assert_eq!(my_bot["dynamic_status"]["status"].as_str(), Some("active"),
               "WS connected → dynamic_status should be active");

    // Disconnect WS → dynamic_status.status == "offline"
    bot.disconnect().await;
    tokio::time::sleep(Duration::from_millis(100)).await;

    let resp2 = query_my_bots(addr, staff_no).await;
    let items2 = resp2["items"].as_array().unwrap();
    let my_bot2 = items2.iter()
        .find(|item| item["capabilities"]["name"].as_str() == Some("MyBot2"))
        .expect("MyBot2 should still be in results after disconnect");
    assert_eq!(my_bot2["dynamic_status"]["status"].as_str(), Some("offline"),
               "WS disconnected → dynamic_status should be offline");
}

/// M-3: /bots/my returns empty result for user with no bots.
#[tokio::test]
async fn m3_my_bots_empty_result() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let staff_no = "nonexistent_staff_999";

    let resp = query_my_bots(addr, staff_no).await;
    let items = resp["items"].as_array().expect("items should be array");
    let total = resp["total"].as_u64().expect("total should be number");

    assert_eq!(total, 0, "Total should be 0 for user with no bots");
    assert!(items.is_empty(), "Items should be empty for user with no bots");
}
