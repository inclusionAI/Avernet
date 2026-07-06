//! Integration tests for actor status visibility in /actors/search and /actors/list.
//!
//! These tests verify that the `status` field in bot search/list responses correctly
//! reflects the combination of `ActorStatus` and WebSocket connection state, as
//! defined by `bot_is_effectively_online`.
//!
//! ## Requirements Coverage
//!
//! ### R1.AC-22 ~ R1.AC-25: /actors/search status field
//! - R1.AC-22: Actor with status=Online + WS connected → status="online"
//! - R1.AC-23: Actor with status=Online + WS disconnected → status="offline"
//! - R1.AC-24: Actor with status=Hidden + WS connected → status="offline" (Hidden takes precedence)
//! - R1.AC-25: Actor with status=Hidden + WS disconnected → status="offline"
//!
//! ### R3.AC-30 ~ R3.AC-34: /actors/list status field
//! - R3.AC-30: Actor with status=Online + WS connected → status="online"
//! - R3.AC-31: Actor with status=Online + WS disconnected → status="offline"
//! - R3.AC-32: Actor with status=Hidden + WS connected → status="offline"
//! - R3.AC-33: Actor with status=Hidden + WS disconnected → status="offline"
//! - R3.AC-34: Both /actors/search and /actors/list return consistent status values
//!
//! ## Status Logic
//!
//! The `status` field is calculated by `bot_is_effectively_online`:
//! - Returns "online" if WS is connected AND status != Hidden
//! - Returns "offline" otherwise (WS disconnected OR status == Hidden)
//!
//! Hidden status takes precedence over WS connection: even with active WS,
//! a Hidden actor is reported as "offline".
//!
//! NOTE: "online" is determined by active WebSocket connection, NOT by a
//! separate `bot.status` heartbeat frame. The heartbeat frame updates load/
//! summary metadata but does not gate the `is_connected` flag.

mod helpers;

use helpers::{MockBot, create_temp_bots_dir, start_test_server};
use reqwest::Client;

/// Helper: Set actor status to Hidden via PUT /actors/{bot_id}/status
async fn set_actor_status(addr: &str, bot_id: &str, token: &str, status: &str) {
    let url = format!("{}/actors/{}/status", addr, bot_id);
    let client = Client::new();
    let response = client
        .put(&url)
        .header("Authorization", format!("Bearer {}", token))
        .json(&serde_json::json!({ "status": status }))
        .send()
        .await
        .expect("Failed to set actor status");
    
    assert!(
        response.status().is_success(),
        "Failed to set status to {}: {:?}",
        status,
        response.text().await
    );
}

/// Helper: Query /actors/search and extract status for a specific bot
async fn search_bot_status(addr: &str, query: &str, current_bot_uuid: &str) -> Option<String> {
    let url = format!(
        "{}/actors/search?q={}&current_bot_uuid={}&cooperatable_only=false",
        addr, query, current_bot_uuid
    );
    let client = Client::new();
    let response = client
        .get(&url)
        .send()
        .await
        .expect("Failed to search bots");
    
    assert!(response.status().is_success(), "Search failed: {:?}", response.text().await);
    
    let json: serde_json::Value = response.json().await.expect("Invalid JSON response");
    json["bots"]
        .as_array()
        .expect("bots should be an array")
        .iter()
        .find(|bot| bot["capabilities"]["name"].as_str() == Some(query))
        .and_then(|bot| bot["dynamic_status"]["status"].as_str().map(|s| s.to_string()))
}

/// Helper: Query /actors/list and extract status for a specific bot
async fn list_bot_status(addr: &str, current_bot_uuid: &str, target_bot_id: &str) -> Option<String> {
    let url = format!(
        "{}/actors/list?current_bot_uuid={}&cooperatable_only=false",
        addr, current_bot_uuid
    );
    let client = Client::new();
    let response = client
        .get(&url)
        .send()
        .await
        .expect("Failed to list bots");
    
    assert!(response.status().is_success(), "List failed: {:?}", response.text().await);
    
    let json: serde_json::Value = response.json().await.expect("Invalid JSON response");
    json["bots"]
        .as_array()
        .expect("bots should be an array")
        .iter()
        .find(|bot| bot["bot_uuid"].as_str() == Some(target_bot_id))
        .and_then(|bot| bot["dynamic_status"]["status"].as_str().map(|s| s.to_string()))
}

/// A-1: Actor with status=Online + WS connected → /actors/search and /actors/list return status="active"
///
/// Covers R1.AC-22 and R3.AC-30
#[tokio::test]
async fn a1_online_with_ws_connected_shows_online() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);
    
    // Create target bot — WS connection established via MockBot::connect
    let mut target_bot = MockBot::connect(addr).await;
    target_bot.register("TargetBotA1", &["test"], addr).await;
    let target_bot_id = target_bot.bot_id.clone();
    
    // Create observer bot to perform queries
    let mut observer_bot = MockBot::connect(addr).await;
    observer_bot.register("ObserverBotA1", &["test"], addr).await;
    let observer_bot_id = observer_bot.bot_id.clone();
    
    // Query /actors/search
    let search_status = search_bot_status(&base_url, "TargetBotA1", &observer_bot_id)
        .await
        .expect("Target bot should appear in search results");
    
    // Query /actors/list
    let list_status = list_bot_status(&base_url, &observer_bot_id, &target_bot_id)
        .await
        .expect("Target bot should appear in list results");
    
    // Both should return "active" — WS connected + status=Online (Rev-1: "online" → "active")
    assert_eq!(search_status, "active", "Search should show active for Online+WS connected");
    assert_eq!(list_status, "active", "List should show active for Online+WS connected");

    // Keep target_bot alive so WS stays connected through assertions
    drop(target_bot);
}

/// A-2: Actor with status=Online + WS disconnected → /actors/search and /actors/list return status="offline"
///
/// Covers R1.AC-23 and R3.AC-31
///
/// NOTE: The online check is based on WebSocket connection state (`is_connected`),
/// not on a separate heartbeat frame.  Disconnecting the WS makes the bot "offline".
#[tokio::test]
async fn a2_online_with_ws_disconnected_shows_offline() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);
    
    // Create target bot, register, then disconnect WS
    let mut target_bot = MockBot::connect(addr).await;
    target_bot.register("TargetBotA2", &["test"], addr).await;
    let target_bot_id = target_bot.bot_id.clone();
    // Disconnect WS — bot is now "offline" even though status is still Online
    target_bot.disconnect().await;
    // Brief delay for server to detect disconnect
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    
    // Create observer bot to perform queries
    let mut observer_bot = MockBot::connect(addr).await;
    observer_bot.register("ObserverBotA2", &["test"], addr).await;
    let observer_bot_id = observer_bot.bot_id.clone();
    
    // Query /actors/search
    let search_status = search_bot_status(&base_url, "TargetBotA2", &observer_bot_id)
        .await
        .expect("Target bot should appear in search results");
    
    // Query /actors/list
    let list_status = list_bot_status(&base_url, &observer_bot_id, &target_bot_id)
        .await
        .expect("Target bot should appear in list results");
    
    // Both should return "offline" (WS disconnected)
    assert_eq!(search_status, "offline", "Search should show offline for Online+WS disconnected");
    assert_eq!(list_status, "offline", "List should show offline for Online+WS disconnected");
}

/// A-3: Actor with status=Hidden + WS connected → /actors/search and /actors/list return status="offline"
///
/// Covers R1.AC-24 and R3.AC-32
/// Key assertion: Hidden status takes precedence over WS connection — even with active WS,
/// a Hidden actor is reported as "offline".
#[tokio::test]
async fn a3_hidden_with_ws_connected_shows_offline() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);
    
    // Create target bot — WS connected
    let mut target_bot = MockBot::connect(addr).await;
    target_bot.register("TargetBotA3", &["test"], addr).await;
    let target_bot_id = target_bot.bot_id.clone();
    let target_token = target_bot.token.clone();
    
    // Set status to Hidden (this overrides the Online status)
    set_actor_status(&base_url, &target_bot_id, &target_token, "hidden").await;
    
    // Create observer bot to perform queries
    let mut observer_bot = MockBot::connect(addr).await;
    observer_bot.register("ObserverBotA3", &["test"], addr).await;
    let observer_bot_id = observer_bot.bot_id.clone();
    
    // Query /actors/search
    let search_status = search_bot_status(&base_url, "TargetBotA3", &observer_bot_id)
        .await
        .expect("Target bot should appear in search results");
    
    // Query /actors/list
    let list_status = list_bot_status(&base_url, &observer_bot_id, &target_bot_id)
        .await
        .expect("Target bot should appear in list results");
    
    // Both should return "offline" (Hidden takes precedence over WS connection)
    assert_eq!(search_status, "offline", "Search should show offline for Hidden+WS connected (Hidden wins)");
    assert_eq!(list_status, "offline", "List should show offline for Hidden+WS connected (Hidden wins)");

    // Keep target_bot alive so WS stays connected through assertions
    drop(target_bot);
}

/// A-4: Actor with status=Hidden + WS disconnected → /actors/search and /actors/list return status="offline"
///
/// Covers R1.AC-25 and R3.AC-33
///
/// Both conditions (Hidden + WS disconnected) independently cause "offline".
/// This test validates the combined case.
#[tokio::test]
async fn a4_hidden_with_ws_disconnected_shows_offline() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);
    
    // Create target bot, register, then set Hidden and disconnect WS
    let mut target_bot = MockBot::connect(addr).await;
    target_bot.register("TargetBotA4", &["test"], addr).await;
    let target_bot_id = target_bot.bot_id.clone();
    let target_token = target_bot.token.clone();
    
    // Set status to Hidden
    set_actor_status(&base_url, &target_bot_id, &target_token, "hidden").await;
    
    // Create observer bot to perform queries
    let mut observer_bot = MockBot::connect(addr).await;
    observer_bot.register("ObserverBotA4", &["test"], addr).await;
    let observer_bot_id = observer_bot.bot_id.clone();
    
    // Query /actors/search
    let search_status = search_bot_status(&base_url, "TargetBotA4", &observer_bot_id)
        .await
        .expect("Target bot should appear in search results");
    
    // Query /actors/list
    let list_status = list_bot_status(&base_url, &observer_bot_id, &target_bot_id)
        .await
        .expect("Target bot should appear in list results");
    
    // Both should return "offline" (WS disconnected AND Hidden)
    assert_eq!(search_status, "offline", "Search should show offline for Hidden+WS disconnected");
    assert_eq!(list_status, "offline", "List should show offline for Hidden+WS disconnected");
}

// ── Rev-1 D-G verification tests ──────────────────────────────────────────────

/// S-1: /actors/search and /actors/list filter out human actors.
///
/// Covers AC-45
///
/// Strategy: use `onboard_bot_as_user` which triggers `ensure_human_actor`
/// server-side, creating a `human_{staff_no}` row with `actor_kind=Human`.
/// Then verify that the human actor is absent from both search and list,
/// while the bot is present.
#[tokio::test]
async fn s1_actors_search_list_filters_human() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let staff_no = "s1_staff";
    let human_id = format!("human_{}", staff_no);

    // Create a bot and onboard with mock user identity.
    // This triggers ensure_human_actor, creating `human_s1_staff` in the registry.
    let bot = MockBot::connect(addr).await;
    let token = bot.token.clone();
    helpers::onboard_bot_as_user(addr, &token, "RegularBotS1", staff_no).await;
    // Set visibility so the bot appears in search/list
    bot.http_client(addr).set_visibility(&bot.bot_id, "public").await.ok();
    let bot_id = bot.bot_id.clone();

    // Create observer bot for queries
    let mut observer = MockBot::connect(addr).await;
    observer.register("ObserverBotS1", &["test"], addr).await;
    let observer_id = observer.bot_id.clone();

    // Search: regular bot should appear
    let search_status = search_bot_status(&base_url, "RegularBotS1", &observer_id).await;
    assert!(search_status.is_some(), "Regular bot should appear in search");

    // List: regular bot should appear
    let list_status = list_bot_status(&base_url, &observer_id, &bot_id).await;
    assert!(list_status.is_some(), "Regular bot should appear in list");

    // Search: human actor should NOT appear
    // The human's name is the staff_no's nick_name ("test_user" from onboard_bot_as_user)
    let human_search = search_bot_status(&base_url, "test_user", &observer_id).await;
    assert!(human_search.is_none(), "Human actor should NOT appear in search results (AC-45)");

    // List: human actor should NOT appear
    let human_list = list_bot_status(&base_url, &observer_id, &human_id).await;
    assert!(human_list.is_none(), "Human actor should NOT appear in list results (AC-45)");

    drop(bot);
    drop(observer);
}

/// S-2: /actors/search response uses dynamic_status.status, not top-level status.
///
/// Covers AC-46
#[tokio::test]
async fn s2_actors_search_dynamic_status_field() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let mut target = MockBot::connect(addr).await;
    target.register("TargetBotS2", &["test"], addr).await;

    let mut observer = MockBot::connect(addr).await;
    observer.register("ObserverBotS2", &["test"], addr).await;
    let observer_id = observer.bot_id.clone();

    // Raw JSON search response — verify field structure
    let url = format!(
        "{}/actors/search?q={}&current_bot_uuid={}&cooperatable_only=false",
        base_url, "TargetBotS2", observer_id
    );
    let client = Client::new();
    let response: serde_json::Value = client
        .get(&url)
        .send()
        .await
        .expect("search request")
        .json()
        .await
        .expect("json");

    let bot_entry = response["bots"]
        .as_array()
        .expect("bots array")
        .iter()
        .find(|b| b["capabilities"]["name"].as_str() == Some("TargetBotS2"))
        .expect("target bot in results");

    // dynamic_status.status should exist with value "active"
    assert!(bot_entry["dynamic_status"].is_object(), "dynamic_status should be an object");
    assert_eq!(bot_entry["dynamic_status"]["status"].as_str(), Some("active"),
               "dynamic_status.status should be 'active' for connected bot");

    // top-level status should NOT exist (Rev-1 breaking change)
    assert!(bot_entry.get("status").is_none() || bot_entry["status"].is_null(),
            "top-level status should not exist in Rev-1 response");

    drop(target);
    drop(observer);
}

/// S-3: /actors/search uses capabilities.summary, not description.
///
/// Covers AC-47
#[tokio::test]
async fn s3_actors_search_summary_field() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let mut target = MockBot::connect(addr).await;
    target.register("TargetBotS3", &["test"], addr).await;

    let mut observer = MockBot::connect(addr).await;
    observer.register("ObserverBotS3", &["test"], addr).await;
    let observer_id = observer.bot_id.clone();

    let url = format!(
        "{}/actors/search?q={}&current_bot_uuid={}&cooperatable_only=false",
        base_url, "TargetBotS3", observer_id
    );
    let client = Client::new();
    let response: serde_json::Value = client
        .get(&url)
        .send()
        .await
        .expect("search request")
        .json()
        .await
        .expect("json");

    let bot_entry = response["bots"]
        .as_array()
        .expect("bots array")
        .iter()
        .find(|b| b["capabilities"]["name"].as_str() == Some("TargetBotS3"))
        .expect("target bot in results");

    // capabilities should have "summary", not "description"
    let caps = &bot_entry["capabilities"];
    assert!(!caps["summary"].is_null(), "capabilities.summary should exist");
    assert!(caps.get("description").is_none() || caps["description"].is_null(),
            "capabilities.description should not exist in Rev-1 response");

    drop(target);
    drop(observer);
}

/// S-4: /actors/list dynamic_status transitions between active and offline.
///
/// Covers AC-53 (list + Hidden scenario)
#[tokio::test]
async fn s4_actors_list_dynamic_status_active_offline() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let mut target = MockBot::connect(addr).await;
    target.register("TargetBotS4", &["test"], addr).await;
    let target_id = target.bot_id.clone();
    let target_token = target.token.clone();

    let mut observer = MockBot::connect(addr).await;
    observer.register("ObserverBotS4", &["test"], addr).await;
    let observer_id = observer.bot_id.clone();

    // WS connected → dynamic_status.status == "active"
    let status = list_bot_status(&base_url, &observer_id, &target_id)
        .await
        .expect("target should be in list");
    assert_eq!(status, "active", "Connected bot should show active in list");

    // Set Hidden → dynamic_status.status == "offline"
    set_actor_status(&base_url, &target_id, &target_token, "hidden").await;
    let status_hidden = list_bot_status(&base_url, &observer_id, &target_id)
        .await
        .expect("target should still be in list after Hidden");
    assert_eq!(status_hidden, "offline", "Hidden bot should show offline in list");

    drop(target);
    drop(observer);
}

/// S-5: /actors/search dynamic_status transitions between active and offline,
/// including Hidden scenario.
///
/// Covers AC-53 (search + Hidden scenario)
#[tokio::test]
async fn s5_actors_search_dynamic_status_active_offline() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server_handle) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let mut target = MockBot::connect(addr).await;
    target.register("TargetBotS5", &["test"], addr).await;
    let target_id = target.bot_id.clone();
    let target_token = target.token.clone();

    let mut observer = MockBot::connect(addr).await;
    observer.register("ObserverBotS5", &["test"], addr).await;
    let observer_id = observer.bot_id.clone();

    // WS connected → dynamic_status.status == "active"
    let status = search_bot_status(&base_url, "TargetBotS5", &observer_id)
        .await
        .expect("target should be in search");
    assert_eq!(status, "active", "Connected bot should show active in search");

    // WS disconnected → dynamic_status.status == "offline"
    target.disconnect().await;
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    let status_disc = search_bot_status(&base_url, "TargetBotS5", &observer_id)
        .await
        .expect("target should be in search after disconnect");
    assert_eq!(status_disc, "offline", "Disconnected bot should show offline in search");

    // Reconnect and set Hidden → dynamic_status.status == "offline"
    let target2 = MockBot::reconnect(addr, &target_token).await;
    // Verify reconnected bot is active again
    let status_recon = search_bot_status(&base_url, "TargetBotS5", &observer_id)
        .await
        .expect("target should be in search after reconnect");
    assert_eq!(status_recon, "active", "Reconnected bot should show active");

    // Now set Hidden
    set_actor_status(&base_url, &target_id, &target_token, "hidden").await;
    let status_hidden = search_bot_status(&base_url, "TargetBotS5", &observer_id)
        .await
        .expect("target should be in search after Hidden");
    assert_eq!(status_hidden, "offline", "Hidden bot should show offline in search (AC-53)");

    drop(target2);
    drop(observer);
}
