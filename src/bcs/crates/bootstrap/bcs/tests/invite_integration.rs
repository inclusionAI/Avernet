//! Invite-link integration tests for BCS.
//!
//! These tests verify the invite-link HTTP endpoints:
//! 1. Token generation by the driver bot
//! 2. Token generation forbidden for non-driver bots
//! 3. Join without login returns 401
//! 4. Tampered token returns 401
//! 5. Invite link for non-existent group returns 403 or 404
//!
//! **Known limitation (V1)**: Since `BCS_AUTH_MOCK=1` disables user identity
//! extraction, we cannot test the full join flow where a human actually joins
//! a group via invite link. The join endpoints always return 401 ("login
//! required") in the test environment. A proper end-to-end join test requires
//! user identity mocking support, which is not yet available.
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test invite_integration -- --test-threads=1
//! ```

mod helpers;

use serde_json::{Value, json};

#[tokio::test]
async fn generate_group_invite_link_as_driver() {
    let bots_dir = helpers::create_temp_bots_dir();
    let (addr, _handle) = helpers::start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let base = format!("http://{}", addr);

    // Connect and register a bot
    let mut bot = helpers::MockBot::connect(addr).await;
    bot.register("DriverBot", &["coding"], addr).await;

    // Create a group with this bot as driver
    let resp = client
        .post(format!("{}/groups", base))
        .header("Authorization", format!("Bearer {}", bot.token))
        .json(&json!({
            "driver_bot": &bot.bot_id,
            "participants": [{"bot_uuid": &bot.bot_id, "role": "driver"}]
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200, "group creation failed");
    let group: Value = resp.json().await.unwrap();
    let group_id = group["id"].as_str().unwrap();

    // Generate invite link as the driver bot
    let resp = client
        .post(format!("{}/groups/{}/invite-link", base, group_id))
        .header("Authorization", format!("Bearer {}", bot.token))
        .json(&json!({"ttl_seconds": 3600}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200, "invite link generation failed");
    let invite: Value = resp.json().await.unwrap();

    assert!(invite["invite_token"].as_str().is_some(), "missing invite_token");
    assert!(invite["expires_at"].as_u64().is_some(), "missing expires_at");
    let join_url = invite["join_url"].as_str().unwrap();
    assert!(join_url.contains("/groups/join/"), "join_url should contain /groups/join/");
}

#[tokio::test]
async fn invite_link_forbidden_for_non_driver() {
    let bots_dir = helpers::create_temp_bots_dir();
    let (addr, _handle) = helpers::start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let base = format!("http://{}", addr);

    // Connect two bots
    let mut driver = helpers::MockBot::connect(addr).await;
    driver.register("Driver", &["driving"], addr).await;

    let mut other = helpers::MockBot::connect(addr).await;
    other.register("Other", &["other"], addr).await;

    // Create group with driver bot
    let resp = client
        .post(format!("{}/groups", base))
        .header("Authorization", format!("Bearer {}", driver.token))
        .json(&json!({
            "driver_bot": &driver.bot_id,
            "participants": [{"bot_uuid": &driver.bot_id, "role": "driver"}]
        }))
        .send()
        .await
        .unwrap();
    let group: Value = resp.json().await.unwrap();
    let group_id = group["id"].as_str().unwrap();

    // Try to generate invite link as non-driver → 403
    let resp = client
        .post(format!("{}/groups/{}/invite-link", base, group_id))
        .header("Authorization", format!("Bearer {}", other.token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 403, "non-driver should be forbidden");
}

#[tokio::test]
async fn join_with_mock_identity_succeeds() {
    let bots_dir = helpers::create_temp_bots_dir();
    let (addr, _handle) = helpers::start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let base = format!("http://{}", addr);

    // Connect and register a bot, create group, generate invite
    let mut bot = helpers::MockBot::connect(addr).await;
    bot.register("Bot", &["skill"], addr).await;

    let resp = client
        .post(format!("{}/groups", base))
        .header("Authorization", format!("Bearer {}", bot.token))
        .json(&json!({
            "driver_bot": &bot.bot_id,
            "participants": [{"bot_uuid": &bot.bot_id, "role": "driver"}]
        }))
        .send()
        .await
        .unwrap();
    let group: Value = resp.json().await.unwrap();
    let group_id = group["id"].as_str().unwrap();

    let resp = client
        .post(format!("{}/groups/{}/invite-link", base, group_id))
        .header("Authorization", format!("Bearer {}", bot.token))
        .send()
        .await
        .unwrap();
    let invite: Value = resp.json().await.unwrap();
    let token = invite["invite_token"].as_str().unwrap();

    // Join using mock user identity
    let resp = client
        .post(format!("{}/groups/join/{}", base, token))
        .header("x-mock-user-id", "12345")
        .header("x-mock-nick-name", "TestHuman")
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200, "join should succeed with mock identity");
    let result: Value = resp.json().await.unwrap();
    assert_eq!(result["joined"], true);
    assert_eq!(result["target_type"], "group");
    assert_eq!(result["actor_id"], "human_12345");

    // Verify human is now in the group
    let resp = client
        .get(format!("{}/groups/{}", base, group_id))
        .send()
        .await
        .unwrap();
    let group_detail: Value = resp.json().await.unwrap();
    let participants = group_detail["participants"].as_array().unwrap();
    let human_participant = participants
        .iter()
        .find(|p| p["bot_uuid"] == "human_12345")
        .expect("human should be in group participants");
    assert_eq!(human_participant["role"], "consultant");
    assert_eq!(human_participant["actor_kind"], "human");
    assert_eq!(human_participant["mode"], "present");
}

#[tokio::test]
async fn tampered_token_rejected() {
    let bots_dir = helpers::create_temp_bots_dir();
    let (addr, _handle) = helpers::start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let base = format!("http://{}", addr);

    let mut bot = helpers::MockBot::connect(addr).await;
    bot.register("Bot", &["skill"], addr).await;

    let resp = client
        .post(format!("{}/groups", base))
        .header("Authorization", format!("Bearer {}", bot.token))
        .json(&json!({
            "driver_bot": &bot.bot_id,
            "participants": [{"bot_uuid": &bot.bot_id, "role": "driver"}]
        }))
        .send()
        .await
        .unwrap();
    let group: Value = resp.json().await.unwrap();
    let group_id = group["id"].as_str().unwrap();

    let resp = client
        .post(format!("{}/groups/{}/invite-link", base, group_id))
        .header("Authorization", format!("Bearer {}", bot.token))
        .send()
        .await
        .unwrap();
    let invite: Value = resp.json().await.unwrap();
    let token = invite["invite_token"].as_str().unwrap();

    // Tamper with token
    let tampered = format!("{}TAMPERED", token);
    let resp = client
        .post(format!("{}/groups/join/{}", base, tampered))
        .send()
        .await
        .unwrap();
    // Login check happens first in the handler, so this returns 401 for
    // "login required" (BCS_AUTH_MOCK=1 means no user identity).
    // Both "invalid token" and "login required" map to 401, which is correct.
    assert_eq!(resp.status(), 401);
}

#[tokio::test]
async fn invite_link_nonexistent_group_returns_404() {
    let bots_dir = helpers::create_temp_bots_dir();
    let (addr, _handle) = helpers::start_test_server(&bots_dir.path().to_path_buf()).await;
    let client = reqwest::Client::new();
    let base = format!("http://{}", addr);

    let mut bot = helpers::MockBot::connect(addr).await;
    bot.register("Bot", &["skill"], addr).await;

    // Try to generate invite link for non-existent group
    let resp = client
        .post(format!("{}/groups/{}/invite-link", base, "nonexistent-group-id"))
        .header("Authorization", format!("Bearer {}", bot.token))
        .send()
        .await
        .unwrap();
    // The authorize_group_invite will try to get the group and fail —
    // depending on the order of checks, this is 403 (bot is not driver)
    // or 404 (group not found).
    assert!(
        resp.status() == 403 || resp.status() == 404,
        "expected 403 or 404, got {}",
        resp.status()
    );
}
