//! Integration tests for bcsfuse integration.
//!
//! Tests that BCS behaves correctly with bcsfuse enabled/disabled:
//! - bcsfuse.enabled=false → fusion uses local FusionEngine
//! - onboard succeeds regardless of bcsfuse state
//!
//! These tests use MockBot + in-process BCS server (no real bcsfuse service).

mod helpers;

use helpers::{MockBot, create_temp_bots_dir, start_test_server};
use std::path::PathBuf;

use bcs::{BcsConfig, BcsServer, LoggingConfig, MessageHistoryConfig, ParticipantInfo};

/// Create a test config with bcsfuse enabled but pointing to a non-existent service.
fn create_test_config_bcsfuse_enabled(bots_dir: &PathBuf) -> BcsConfig {
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

// ── Tests: bcsfuse disabled (default) ────────────────────────────────────

#[tokio::test]
async fn fuse_without_bcsfuse_uses_local_engine() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    // Connect and onboard two bots
    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("ArchBot", &["architecture"], addr).await;
    bot1.send_heartbeat().await;

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("DBABot", &["database"], addr).await;
    bot2.send_heartbeat().await;

    // Create a group
    let client = bot1.http_client(addr);
    let participants = vec![
        ParticipantInfo { bot_uuid: bot1.bot_id.clone(), role: Some("driver".into()) },
        ParticipantInfo { bot_uuid: bot2.bot_id.clone(), role: Some("consultant".into()) },
    ];
    let group = client
        .create_group_no_mode(&bot1.bot_id, participants)
        .await
        .expect("create group");

    // Fuse — should use local FusionEngine (bcsfuse disabled)
    let fuse_result = client
        .fuse_context(
            &group.id,
            "How should we design the database schema?",
            vec![bot1.bot_id.clone(), bot2.bot_id.clone()],
        )
        .await;

    // Local FusionEngine succeeds — key assertion is no bcsfuse HTTP errors
    assert!(fuse_result.is_ok(), "Fuse should succeed with local engine: {:?}", fuse_result.err());
}

#[tokio::test]
async fn onboard_with_bcsfuse_disabled_succeeds() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();
    let (addr, _handle) = start_test_server(&bots_dir).await;

    let mut bot = MockBot::connect(addr).await;
    bot.register("TestBot", &["testing", "qa"], addr).await;

    // Verify bot is connected by sending heartbeat (returns ok)
    let resp = bot.send_heartbeat().await;
    assert!(resp["ok"].as_bool().unwrap_or(false), "heartbeat should succeed after onboard");

    // Verify capabilities persisted to disk
    let bot_json = bots_dir.join(&bot.bot_id).join("bot.json");
    assert!(bot_json.exists(), "bot.json should be saved to disk");
}

// ── Tests: bcsfuse enabled but unreachable ───────────────────────────────

#[tokio::test]
async fn onboard_with_bcsfuse_enabled_but_unreachable_succeeds() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();

    let config = create_test_config_bcsfuse_enabled(&bots_dir);
    let server = BcsServer::new(config);
    let (addr, _handle) = server.run_on_random_port().await.expect("start server");

    // Onboard should succeed — bcsfuse sync failure is non-blocking
    let mut bot = MockBot::connect(addr).await;
    bot.register("TestBot", &["testing"], addr).await;

    // Wait for background sync to fail
    tokio::time::sleep(std::time::Duration::from_millis(500)).await;

    // Bot should still be connected — heartbeat succeeds
    let resp = bot.send_heartbeat().await;
    assert!(resp["ok"].as_bool().unwrap_or(false), "Bot should remain connected even if bcsfuse sync fails");

    // Capabilities should be persisted to disk
    let bot_json = bots_dir.join(&bot.bot_id).join("bot.json");
    assert!(bot_json.exists(), "bot.json should be saved to disk");
}

#[tokio::test]
async fn fuse_with_bcsfuse_enabled_but_unreachable_returns_error() {
    let tmp = create_temp_bots_dir();
    let bots_dir = tmp.path().to_path_buf();

    let config = create_test_config_bcsfuse_enabled(&bots_dir);
    let server = BcsServer::new(config);
    let (addr, _handle) = server.run_on_random_port().await.expect("start server");

    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("Bot1", &["dev"], addr).await;
    bot1.send_heartbeat().await;

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("Bot2", &["qa"], addr).await;
    bot2.send_heartbeat().await;

    let client = bot1.http_client(addr);
    let participants = vec![
        ParticipantInfo { bot_uuid: bot1.bot_id.clone(), role: Some("driver".into()) },
        ParticipantInfo { bot_uuid: bot2.bot_id.clone(), role: Some("consultant".into()) },
    ];
    let group = client
        .create_group_no_mode(&bot1.bot_id, participants)
        .await
        .expect("create group");

    // Fuse should fail because bcsfuse is unreachable
    let fuse_result = client
        .fuse_context(
            &group.id,
            "test question",
            vec![bot1.bot_id.clone(), bot2.bot_id.clone()],
        )
        .await;

    // bcsfuse HTTP call fails → BCS returns error
    assert!(fuse_result.is_err(), "Fuse should fail when bcsfuse is unreachable");
}
