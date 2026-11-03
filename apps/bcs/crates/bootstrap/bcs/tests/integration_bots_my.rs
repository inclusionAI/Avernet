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
use std::net::SocketAddr;
use std::time::Duration;

/// bot-authority ruling T6d-1: `/bots/my` lists the
/// OWNER/MANAGER authority union (Task-4d semantics), so
/// the fixture Bot's ownership is seeded through the
/// TRUSTED register surface (`register_with_ownership`
/// mints the authority owner edge); the raw onboard
/// never mints. Returns `(bot_uuid, bot_token)`.
async fn register_trusted_fixture_bot(
    addr: SocketAddr,
    staff_no: &str,
    name: &str,
) -> (String, String) {
    let http = reqwest::Client::new();
    let resp = http
        .get(format!("http://{addr}/register/token"))
        .header("X-Mock-User-Id", staff_no)
        .send()
        .await
        .expect("register token request");
    assert!(resp.status().is_success(), "register token mint: {}", resp.status());
    let body: serde_json::Value = resp.json().await.expect("register token body");
    let register_token = body["token"].as_str().expect("register token").to_string();
    let encoded_name = name.replace(' ', "%20");
    let resp = http
        .post(format!(
            "http://{addr}/register?token={register_token}&bot-name={encoded_name}"
        ))
        .send()
        .await
        .expect("trusted registration");
    assert!(resp.status().is_success(), "trusted registration: {}", resp.status());
    let registered: serde_json::Value =
        resp.json().await.expect("registration body");
    (
        registered["bot_uuid"].as_str().expect("bot_uuid").to_string(),
        registered["bot_token"].as_str().expect("bot_token").to_string(),
    )
}

/// M-1: /bots/my returns the trusted registered actor
/// AND the Human SELF entry with correct fields.
///
/// bot-authority ruling T6d-1: the actor union is the
/// authority owner/manager set; the fixture Bot's
/// ownership is seeded through the TRUSTED register
/// surface, the WS RESUME creates the bot row, and the
/// re-onboard (mock user) keeps actor rows WITHOUT
/// touching authority. Ruling T6d-3: the Human SELF
/// entry keeps its self semantics — it appears for the
/// user themselves (original AC-43 expectation).
#[tokio::test]
async fn m1_my_bots_handler_returns_registered_actors() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let staff_no = "test_staff_001";
    let human_id = format!("human_{}", staff_no);

    // Trusted registration seeds the authority owner
    // edge; the WS RESUME creates the bot row and
    // connect state; the re-onboard (mock user) keeps
    // actor rows without touching authority.
    let (trusted_bot_uuid, trusted_token) =
        register_trusted_fixture_bot(addr, staff_no, "MyBot1").await;
    let bot = MockBot::reconnect(addr, &trusted_token).await;
    let token = bot.token.clone();
    let bot_uuid = bot.bot_id.clone();
    assert_eq!(bot_uuid, trusted_bot_uuid, "the WS resume must return the trusted Bot");
    onboard_bot_as_user(addr, &token, "MyBot1", staff_no).await;

    // Query /bots/my
    let resp = query_my_bots(addr, staff_no).await;
    let items = resp["items"].as_array().expect("items should be array");
    let total = resp["total"].as_u64().expect("total should be number");

    // Spec §8 first row: the Human SELF entry keeps its
    // self semantics alongside the owner/manager Bot
    // union — the trusted Bot entry is REQUIRED.
    assert!(total >= 2, "Should have at least 2 entries (bot + human), got {} ({:?})", total, items);
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

    // Spec §8 mine row: the union entries carry their
    // sampled relation on the legacy page too
    // (`access_relation=owner|manager`; the Human
    // self entry keeps `self`).
    assert_eq!(my_bot["access_relation"].as_str(), Some("owner"),
               "the owner edge samples as 'owner'");

    // Find the human self entry — spec §8 first row
    // (self semantics preserved): the OWN user's
    // Human actor row appears alongside the union; no
    // cross-user visibility (a different staff listing
    // never sees this row).
    let my_human = items.iter()
        .find(|item| item["bot_uuid"].as_str() == Some(human_id.as_str()))
        .expect("human self entry should be in /bots/my results");

    assert_eq!(my_human["actor_kind"].as_str(), Some("human"), "human entry actor_kind should be 'human'");
    assert_eq!(my_human["created_by"].as_str(), Some(staff_no), "human created_by should match staff_no");
    assert_eq!(my_human["access_relation"].as_str(), Some("self"),
               "the Human self entry keeps 'self'");

    drop(bot);
}

/// M-2: /bots/my dynamic_status reflects WS connection state.
///
/// The fixture Bot is trusted-registered (its authority
/// owner edge makes it union-visible) and connected over
/// WS; disconnecting must flip `dynamic_status` offline.
#[tokio::test]
async fn m2_my_bots_dynamic_status_active_when_ws_connected() {
    let _ = tracing_subscriber::fmt::try_init();
    let temp_dir = create_temp_bots_dir();
    let bots_dir = temp_dir.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let staff_no = "test_staff_002";

    let (trusted_bot_uuid, trusted_token) =
        register_trusted_fixture_bot(addr, staff_no, "MyBot2").await;
    let mut bot = MockBot::reconnect(addr, &trusted_token).await;
    let token = bot.token.clone();
    assert_eq!(bot.bot_id, trusted_bot_uuid);
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
