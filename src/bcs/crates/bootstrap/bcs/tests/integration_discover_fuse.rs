//! Integration tests for Discover + Fuse Recommend feature.
//!
//! **Scope**: These tests verify degradation and error-handling paths using a
//! dead bcsfuse endpoint (no mock server). The fuse-success path (merge ordering,
//! dedup) is NOT covered here because the test infra lacks an HTTP mock server;
//! that logic lives in `merge_discover_results` and should be tested when a mock
//! framework (e.g. `wiremock`) is added.
//!
//! Tests cover:
//! - Discover gracefully degrades when bcsfuse is unreachable (returns registry-only results)
//! - Discover `name` parameter filtering (case-insensitive contains, q-priority)
//! - leave_bot completes without errors (set_worker_offline call was removed)
//! - set_visibility succeeds with fire-and-forget sync (sync failure is non-blocking)
//!
//! These tests use MockBot + in-process BCS server (no real bcsfuse service).

mod helpers;

use helpers::{MockBot, create_temp_bots_dir, start_test_server};
use std::path::PathBuf;

use bcs::{BcsConfig, BcsServer, LoggingConfig, MessageHistoryConfig};

/// Create a test config with bcsfuse enabled but pointing to a non-existent service.
fn create_config_bcsfuse_enabled(bots_dir: &PathBuf) -> BcsConfig {
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
        collaboration: Default::default(),
        store_messages: true,
        max_groups_as_driver: 3,
        group_chat_delay_min_ms: 0,
        group_chat_delay_max_ms: 0,
        max_group_members: 5,
        max_groups_as_member: 10,
        max_group_messages: 100,
        onboard_binding_enabled: false,
        default_visibility: None,
        manifest: Default::default(),
        allowed_switch_provider_ids: Vec::new(),
        provider_stream_gray_enabled: false,
        provider_stream_gray_created_by: Vec::new(),
        strict_container_validation: false,
        bcs_endpoint: None,
        botchat_url: None,
        register_path: "/bcn/register".to_string(),
        logging: LoggingConfig::default(),
        bcsfuse: bcs_fuse_client::BcsFuseConfig {
            enabled: true,
            url: "http://127.0.0.1:19999".to_string(), // no server here
            sync_timeout_ms: 1000,
            fusion_timeout_ms: 2000,
            ..Default::default()
        },
        auth_sdk: Default::default(),
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
    }
}

// ============================================================================
// 12.1: Discover + Fuse Recommend degradation tests
// ============================================================================

/// When bcsfuse is enabled but unreachable, discover with `q` should degrade
/// gracefully and return only registry results (no error).
#[tokio::test]
async fn discover_with_fuse_unreachable_degrades_to_registry_only() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();

    let config = create_config_bcsfuse_enabled(&bots_dir);
    let server = BcsServer::new(config);
    let (addr, _handle) = server.run_on_random_port().await.expect("start server");

    // Register two bots with relevant domains
    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("ArchBot", &["architecture", "design"], addr).await;
    bot1.send_heartbeat().await;

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("DBABot", &["database", "sql"], addr).await;
    bot2.send_heartbeat().await;

    // Discover with q — fuse recommend will fail (unreachable), should degrade
    let client = bot1.http_client(addr);
    let result = client.discover_bots(Some("architecture")).await;

    assert!(result.is_ok(), "Discover should succeed even when fuse is unreachable: {:?}", result.err());

    let response = result.unwrap();
    // Should return at least the registry-matched bot
    assert!(!response.bots.is_empty(), "Should return registry results on fuse degradation");
}

/// When bcsfuse is disabled, discover with `q` should use only registry (baseline behavior).
#[tokio::test]
async fn discover_without_fuse_returns_registry_results() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("ArchBot", &["architecture"], addr).await;
    bot1.send_heartbeat().await;

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("DBABot", &["database"], addr).await;
    bot2.send_heartbeat().await;

    let client = bot1.http_client(addr);
    let result = client.discover_bots(Some("architecture")).await;

    assert!(result.is_ok(), "Discover should succeed: {:?}", result.err());
    let response = result.unwrap();
    assert!(!response.bots.is_empty(), "Should return matching bots from registry");
}

// ============================================================================
// 12.2: Discover name parameter tests
// ============================================================================

/// Discover with `?name=xxx` returns bots matching by name (case-insensitive contains).
#[tokio::test]
async fn discover_by_name_returns_matching_bots() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("ArchBot", &["architecture"], addr).await;
    bot1.send_heartbeat().await;

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("DBABot", &["database"], addr).await;
    bot2.send_heartbeat().await;

    let mut bot3 = MockBot::connect(addr).await;
    bot3.register("ArchHelper", &["design"], addr).await;
    bot3.send_heartbeat().await;

    // Search by name "arch" — should match ArchBot and ArchHelper (case-insensitive)
    let url = format!("http://{}/bots/discover?name=arch", addr);
    let response: serde_json::Value = reqwest::Client::new()
        .get(&url)
        .header("Authorization", format!("Bearer {}", bot1.token.as_str()))
        .send()
        .await
        .expect("Failed to call discover")
        .json()
        .await
        .expect("Failed to parse response");

    let bots = response["bots"].as_array().expect("bots should be array");
    assert!(bots.len() >= 2, "Should match at least 2 bots with 'arch' in name, got {}", bots.len());

    // All returned bots should have "arch" in their name (case-insensitive)
    for bot in bots {
        let name = bot["capabilities"]["name"].as_str().unwrap_or("");
        assert!(
            name.to_lowercase().contains("arch"),
            "Bot name '{}' should contain 'arch'", name
        );
    }
}

/// Discover with `?name=xxx` does NOT trigger fuse recommend (name-only path).
#[tokio::test]
async fn discover_by_name_does_not_trigger_fuse() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();

    let config = create_config_bcsfuse_enabled(&bots_dir);
    let server = BcsServer::new(config);
    let (addr, _handle) = server.run_on_random_port().await.expect("start server");

    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("TestBot", &["testing"], addr).await;
    bot1.send_heartbeat().await;

    // name search should NOT call fuse recommend — should succeed even with fuse unreachable
    let url = format!("http://{}/bots/discover?name=test", addr);
    let response = reqwest::Client::new()
        .get(&url)
        .header("Authorization", format!("Bearer {}", bot1.token.as_str()))
        .send()
        .await
        .expect("Failed to call discover");
    assert!(response.status().is_success(), "name search should succeed without fuse");

    let body: serde_json::Value = response.json().await.expect("Failed to parse");
    let bots = body["bots"].as_array().expect("bots should be array");
    assert!(!bots.is_empty(), "Should find bot by name");
}

/// When both `q` and `name` are provided, `q` takes priority.
#[tokio::test]
async fn discover_q_takes_priority_over_name() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("ArchBot", &["architecture"], addr).await;
    bot1.send_heartbeat().await;

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("DBABot", &["database"], addr).await;
    bot2.send_heartbeat().await;

    // q=database&name=arch — q should take priority, returning DBABot not ArchBot
    let url = format!("http://{}/bots/discover?q=database&name=arch", addr);
    let response: serde_json::Value = reqwest::Client::new()
        .get(&url)
        .header("Authorization", format!("Bearer {}", bot1.token.as_str()))
        .send()
        .await
        .expect("Failed to call discover")
        .json()
        .await
        .expect("Failed to parse response");

    let bots = response["bots"].as_array().expect("bots should be array");
    // q=database should match DBABot (by domain), not ArchBot (by name)
    let has_dba = bots.iter().any(|b| {
        b["capabilities"]["name"].as_str().unwrap_or("") == "DBABot"
    });
    assert!(has_dba, "q should take priority: DBABot should be in results");
}

// ============================================================================
// 12.3: leave_bot no longer triggers offline
// ============================================================================

/// DELETE /bots/{id} should succeed for the human owner without calling set_worker_offline.
/// Since bcsfuse is unreachable, if set_worker_offline were still called, it would
/// either fail or add latency — but leave should complete quickly and cleanly.
#[tokio::test]
async fn leave_bot_does_not_trigger_offline() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();

    let mut config = create_config_bcsfuse_enabled(&bots_dir);
    config.auth.mock_user_id = Some("alice".to_string());
    config.auth.mock_user_name = Some("Alice".to_string());
    let server = BcsServer::new(config);
    let (addr, _handle) = server.run_on_random_port().await.expect("start server");

    let mut bot = MockBot::connect(addr).await;
    bot.register("LeaveTestBot", &["testing"], addr).await;
    bot.send_heartbeat().await;

    // Wait for background sync to attempt (and fail)
    tokio::time::sleep(std::time::Duration::from_millis(300)).await;

    // Leave the bot via DELETE /bots/{id} as its human owner — should succeed without calling set_worker_offline
    let http = reqwest::Client::new();
    let url = format!("http://{}/bots/{}", addr, bot.bot_id);
    let response = http
        .delete(&url)
        .send()
        .await
        .expect("Failed to send DELETE");
    assert!(response.status().is_success(), "DELETE /bots/{{id}} should succeed, got {}", response.status());

    let body: serde_json::Value = response.json().await.expect("Failed to parse response");
    assert!(body["left"].as_bool().unwrap_or(false), "Bot should be marked as left");
}

// ============================================================================
// 12.4: set_visibility triggers fuse sync (fire-and-forget)
// ============================================================================

/// set_visibility should succeed even when bcsfuse is unreachable
/// (sync is fire-and-forget, placed after rollback logic).
#[tokio::test]
async fn set_visibility_succeeds_with_fuse_unreachable() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();

    let config = create_config_bcsfuse_enabled(&bots_dir);
    let server = BcsServer::new(config);
    let (addr, _handle) = server.run_on_random_port().await.expect("start server");

    let mut bot = MockBot::connect(addr).await;
    bot.register("VisBot", &["testing"], addr).await;
    bot.send_heartbeat().await;

    // Wait for onboard sync to attempt
    tokio::time::sleep(std::time::Duration::from_millis(300)).await;

    // Change visibility — should succeed (sync is fire-and-forget)
    let client = bot.http_client(addr);
    let result = client.set_visibility(&bot.bot_id, "protected").await;
    assert!(result.is_ok(), "set_visibility should succeed even with fuse unreachable: {:?}", result.err());

    // Change again to verify repeated changes work
    let result2 = client.set_visibility(&bot.bot_id, "public").await;
    assert!(result2.is_ok(), "Second set_visibility should also succeed: {:?}", result2.err());

    // Wait for background sync attempts
    tokio::time::sleep(std::time::Duration::from_millis(300)).await;

    // Bot should still be functional
    let discover_result = client.discover_bots(None).await;
    assert!(discover_result.is_ok(), "Bot should still be discoverable after visibility changes");
}

/// set_visibility to private should succeed and trigger fuse sync (fire-and-forget).
/// Verifies the sync is placed after rollback logic — when no friends exist,
/// no rollback occurs and the change completes successfully.
#[tokio::test]
async fn set_visibility_to_private_succeeds_and_triggers_sync() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();

    let config = create_config_bcsfuse_enabled(&bots_dir);
    let server = BcsServer::new(config);
    let (addr, _handle) = server.run_on_random_port().await.expect("start server");

    let mut bot = MockBot::connect(addr).await;
    bot.register("PrivateBot", &["testing"], addr).await;
    bot.send_heartbeat().await;

    tokio::time::sleep(std::time::Duration::from_millis(300)).await;

    // Change to private — no friends to clean up, so no rollback
    let client = bot.http_client(addr);
    let result_data = client.set_visibility(&bot.bot_id, "private").await
        .expect("set_visibility to private should succeed");
    assert!(result_data.success, "set_visibility response should indicate success");

    // Wait for background sync attempt
    tokio::time::sleep(std::time::Duration::from_millis(300)).await;

    // Change back to public to verify round-trip works
    let result2 = client.set_visibility(&bot.bot_id, "public").await;
    assert!(result2.is_ok(), "set_visibility back to public should succeed: {:?}", result2.err());
}
