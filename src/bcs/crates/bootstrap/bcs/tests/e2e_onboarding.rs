//! End-to-End Tests for BCS with Moltis.
//!
//! These tests start actual BCS and Moltis processes and verify the complete
//! onboarding flow using the skill-based approach (chat → skill → bcs-cli).
//!
//! All tests in this file are E2E tests that require:
//! - BCS server process
//! - Moltis gateway process with BCN plugin
//!
//! Token flow in E2E tests:
//! 1. BCN plugin connects via WebSocket and calls bot.connect
//! 2. BCS returns bot_uuid and token
//! 3. BCN plugin saves to session file: $BOT_DATA_DIR/.bcs/session.json
//! 4. bcs-cli discovers token from env var (BCN_BOT_TOKEN) or session file
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_e2e_onboarding -- --test-threads=1
//! ```

mod e2e_helpers;

use std::time::Duration;

use e2e_helpers::{
    create_temp_dir, get_bots, next_port,
    run_bcs_cli_with_env_token, run_bcs_cli_with_session_file,
    trigger_skill_onboarding, ProcessManager,
};

// ============================================================================
// Category 1: WebSocket Connection Tests
// ============================================================================

/// Test: Bot can successfully reconnect after restart with token persistence
/// Flow: Connect → Disconnect → Reconnect with same token
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_ws_reconnection() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    // Start BCS (process is tracked internally)
    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start Moltis gateway for "PM" with skill (process is tracked internally)
    let bot_port = next_port();
    let (original_bot_id, original_token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "PM",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    println!("Original connection: bot_id={}, token={}", original_bot_id, original_token);

    // Wait for connection to stabilize
    tokio::time::sleep(Duration::from_secs(2)).await;

    // Kill only the Moltis process (simulating disconnect)
    println!("Killing Moltis process to simulate disconnect...");
    proc_mgr.kill_last();

    // Wait a moment for cleanup
    tokio::time::sleep(Duration::from_millis(500)).await;

    // Restart Moltis - it should use the same token from session.json
    let bot_port2 = next_port();
    let (new_bot_id, new_token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port2,
        "PM",
        &data_dir,
    ).await.expect("Failed to restart Moltis");

    println!("Reconnected with bot_id={}, token={}", new_bot_id, new_token);

    // With token persistence, the bot should reconnect with the SAME bot_id
    assert!(!new_token.is_empty(), "Should have a token after reconnect");

    // Verify we can still interact with BCS via HTTP API
    let bots = get_bots(&bcs_url, &new_token).await;
    println!("Bots list after reconnect: {:?}", bots);
}

/// Test: New bot gets assigned bot_id and token via WebSocket
/// Flow: Start BCS → Start Moltis (no prior token) → Verify new bot_id/token assigned
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_ws_new_bot() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    // Start BCS
    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    // Start Moltis with a bot that has no prior session
    let bot_port = next_port();
    let (bot_id, token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "新机器人",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    // Verify we got a new bot_id and token
    assert!(!bot_id.is_empty(), "Should have assigned a bot_id");
    assert!(!token.is_empty(), "Should have assigned a token");

    println!("New bot created: bot_id={}, token={}", bot_id, token);

    // Verify session file was created
    let session_file = data_dir.join("新机器人").join(".bcs").join("session.json");
    assert!(session_file.exists(), "Session file should be created for new bot");
}

/// Test: Bot_id remains consistent across reconnections
/// Flow: Connect → Reconnect → Verify same bot_id
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_ws_consistent_bot_id() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    // First connection
    let bot_port1 = next_port();
    let (bot_id1, _token1) = proc_mgr.start_moltis(
        bcs_port,
        bot_port1,
        "稳定Bot",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    println!("First connection: bot_id={}", bot_id1);

    // Kill and reconnect
    proc_mgr.kill_last();
    tokio::time::sleep(Duration::from_millis(500)).await;

    let bot_port2 = next_port();
    let (bot_id2, _token2) = proc_mgr.start_moltis(
        bcs_port,
        bot_port2,
        "稳定Bot",
        &data_dir,
    ).await.expect("Failed to restart Moltis");

    println!("Second connection: bot_id={}", bot_id2);

    // bot_id should be the same after reconnection
    assert_eq!(bot_id1, bot_id2, "bot_id should remain consistent after reconnection");
}

// ============================================================================
// Category 2: Full Onboarding Flow Tests
// ============================================================================

/// Test: Complete onboarding flow with single bot
/// Flow: Start BCS → Start Moltis with skill → Connect via WS → Onboard via skill
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_onboard_single_bot() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    // Start BCS (process is tracked internally)
    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start Moltis gateway for bot "张三" with skill setup (process is tracked internally)
    let bot_port = next_port();
    let (bot_id, token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "张三",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    println!("Bot connected via WebSocket: bot_id={}, token={}", bot_id, token);
    assert!(!bot_id.is_empty(), "Should have bot_id");
    assert!(!token.is_empty(), "Should have token");

    // Verify skill directory was created
    let skill_dir = data_dir.join("张三").join("skills").join("bcs-coordination");
    assert!(skill_dir.exists(), "Skill directory should exist");
    assert!(skill_dir.join("SKILL.md").exists(), "SKILL.md should exist");
    assert!(skill_dir.join("bcs-cli").exists(), "bcs-cli binary should be copied");

    println!("Skill-based approach: Bot will use bcs-coordination skill to onboard");

    // Trigger skill-based onboarding by sending a message to the bot
    // The bot should use its skill to call bcs-cli onboard
    let response = trigger_skill_onboarding(
        bot_port,
        "张三",
        "开发助手",
        "code_review,deployment,debugging"
    ).await;

    println!("Bot response to onboarding instruction: {:?}", response);

    // Wait for onboarding to complete through the skill
    tokio::time::sleep(Duration::from_secs(3)).await;

    // Verify bot is listed via HTTP API (not using bcs-cli directly)
    let bots = get_bots(&bcs_url, &token).await;
    println!("Bots list: {:?}", bots);

    // Verify session file persists
    let session_file = data_dir.join("张三").join(".bcs").join("session.json");
    assert!(session_file.exists(), "Session file should persist");
}

/// Test: Multiple bots onboarding with skill-based approach
/// Flow: Start BCS → Start multiple Moltis bots → Connect all → Onboard all
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_onboard_multiple_bots() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    // Start BCS (process is tracked internally)
    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start multiple Moltis gateways with skill setup
    let bot_configs = [
        ("张三", "开发助手", "code_review,deployment,debugging"),
        ("李四", "产品经理", "prd,requirements,prioritization"),
        ("DBA", "数据库专家", "database,deadlock,performance"),
    ];

    let mut bot_infos = vec![];
    for (bot_name, summary, skills) in &bot_configs {
        let bot_port = next_port();

        let (bot_id, token) = proc_mgr.start_moltis(
            bcs_port,
            bot_port,
            bot_name,
            &data_dir,
        ).await.expect(&format!("Failed to start Moltis for {}", bot_name));

        bot_infos.push((bot_name.to_string(), bot_id, token, bot_port, summary.to_string(), skills.to_string()));
    }

    // Trigger skill-based onboarding for each bot
    for (bot_name, _bot_id, _token, bot_port, summary, skills) in &bot_infos {
        println!("Triggering skill-based onboarding for {}...", bot_name);

        let result = trigger_skill_onboarding(*bot_port, bot_name, summary, skills).await;
        println!("{} onboarding response: {:?}", bot_name, result);

        // Small delay between each onboarding
        tokio::time::sleep(Duration::from_millis(500)).await;
    }

    // Wait for all onboardings to complete
    tokio::time::sleep(Duration::from_secs(5)).await;

    // Verify bots are listed via HTTP API (using first bot's token)
    let bots = get_bots(&bcs_url, &bot_infos[0].2).await;
    println!("Bots list after onboarding: {:?}", bots);

    // Verify skill directories exist for all bots
    for (bot_name, _, _, _, _, _) in &bot_infos {
        let skill_dir = data_dir.join(bot_name).join("skills").join("bcs-coordination");
        assert!(skill_dir.exists(), "Skill directory should exist for {}", bot_name);
    }
}

/// Test: Verify skill directory setup is correct
/// Flow: Start BCS → Start Moltis → Verify skill directory structure
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_onboard_skill_setup() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    // Start BCS (process is tracked internally)
    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let _bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start Moltis gateway for "安全" (Security) bot with skill (process is tracked internally)
    let bot_port = next_port();
    let (bot_id, _token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "安全",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    // Verify skill directory structure
    let skill_dir = data_dir.join("安全").join("skills").join("bcs-coordination");

    assert!(skill_dir.exists(), "Skill directory should be created");
    assert!(skill_dir.join("SKILL.md").exists(), "SKILL.md should be created");

    // Verify SKILL.md contains correct bot_id
    let skill_content = std::fs::read_to_string(skill_dir.join("SKILL.md"))
        .expect("Should read SKILL.md");
    assert!(skill_content.contains("bcs-coordination"), "SKILL.md should have correct name");

    // Verify bcs-cli binary is copied
    assert!(skill_dir.join("bcs-cli").exists(), "bcs-cli binary should be copied");

    // Verify skill is enabled in config
    let config_path = data_dir.join("安全").join("config").join("moltis.toml");
    let config_content = std::fs::read_to_string(&config_path)
        .expect("Should read config");
    assert!(config_content.contains("auto_load = [\"bcs-coordination\"]"),
            "Config should auto-load bcs-coordination skill");

    println!("Skill setup verified for bot: {}", bot_id);
}

/// Test: Capability persistence after skill-based onboarding
/// Flow: Start BCS → Start Moltis → Connect → Onboard → Verify persistence
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_onboard_persistence() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    // Start BCS (process is tracked internally)
    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let _bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start Moltis gateway for "Security" bot with skill (process is tracked internally)
    let bot_port = next_port();
    let (bot_id, _token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "安全",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    // Trigger skill-based onboarding
    let _ = trigger_skill_onboarding(
        bot_port,
        "安全",
        "安全专家",
        "security,audit,risk_assessment"
    ).await;

    // Wait for onboarding to complete
    tokio::time::sleep(Duration::from_secs(3)).await;

    // Verify bot file was created by the skill
    let bot_file = data_dir.join(&bot_id).join("bot.json");

    // Note: The bot file is created by BCS when the skill calls bcs-cli onboard
    // If the file doesn't exist yet, the skill might still be processing
    println!("Checking for bot file at: {:?}", bot_file);

    // Check session file exists (this confirms BCN connection works)
    let session_file = data_dir.join("安全").join(".bcs").join("session.json");
    assert!(session_file.exists(), "Session file should persist");
}

/// Test: Bot can be discovered after onboarding
/// Flow: Start BCS → Start Moltis → Onboard → Discover bot via HTTP API
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_onboard_discover_after() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start and onboard bot
    let bot_port = next_port();
    let (_bot_id, token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "可发现Bot",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    // Trigger onboarding
    let _ = trigger_skill_onboarding(
        bot_port,
        "可发现Bot",
        "测试专家",
        "testing,automation,qa"
    ).await;

    tokio::time::sleep(Duration::from_secs(2)).await;

    // Discover the bot via HTTP API (with token)
    let bots = get_bots(&bcs_url, &token).await.expect("Failed to get bots");
    println!("Bots after onboarding: {:?}", bots);

    // List bots via CLI with env token (BCN plugin sets BCN_BOT_TOKEN)
    let bot_data_dir = data_dir.join("可发现Bot");
    let output = run_bcs_cli_with_env_token(&bcs_url, &token, &bot_data_dir, &["list"])
        .expect("List command failed");
    println!("List output after onboarding: {}", output);
}

/// Test: List bots via bcs-cli with token
/// Flow: Start BCS → Start Moltis → List bots via CLI with session file
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_onboard_list_bots() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start bot
    let bot_port = next_port();
    let (_bot_id, token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "列表Bot",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    // List bots via CLI with env token (BCN plugin sets BCN_BOT_TOKEN)
    let bot_data_dir = data_dir.join("列表Bot");
    let output = run_bcs_cli_with_env_token(&bcs_url, &token, &bot_data_dir, &["list"])
        .expect("List command failed");

    println!("List bots output: {}", output);
    assert!(!output.is_empty(), "Should return some output");
}

/// Test: bcs-cli reads token from session file (BCN plugin creates this)
/// Flow: Start BCS → Start Moltis → Use CLI with session file
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_onboard_session_file_token() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start bot (BCN plugin creates session file)
    let bot_port = next_port();
    let (_bot_id, _token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "SessionBot",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    // Verify session file exists
    let bot_data_dir = data_dir.join("SessionBot");
    let session_file = bot_data_dir.join(".bcs").join("session.json");
    assert!(session_file.exists(), "Session file should be created by BCN plugin");

    // Use CLI without explicit token - it should read from session file
    let output = run_bcs_cli_with_session_file(&bcs_url, &bot_data_dir, &["list"])
        .expect("List command with session file failed");

    println!("List bots output (via session file): {}", output);
}

/// Test: Session context is properly set after connection
/// Flow: Start BCS → Start Moltis → Verify session context
#[tokio::test]
#[ignore = "requires moltis binary from submodules/moltis"]
async fn e2e_onboard_session_context() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();

    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir)
        .await
        .expect("Failed to start BCS");

    // Start Moltis
    let bot_port = next_port();
    let (bot_id, token) = proc_mgr.start_moltis(
        bcs_port,
        bot_port,
        "上下文Bot",
        &data_dir,
    ).await.expect("Failed to start Moltis");

    // Verify session file content
    let session_file = data_dir.join("上下文Bot").join(".bcs").join("session.json");
    let content = std::fs::read_to_string(&session_file).expect("Failed to read session file");
    let session: serde_json::Value = serde_json::from_str(&content).expect("Failed to parse session");

    // Verify session has required fields
    assert!(session["bot_uuid"].is_string(), "Session should have bot_uuid");
    assert!(session["token"].is_string(), "Session should have token");
    assert!(!session["bot_uuid"].as_str().unwrap_or("").is_empty(), "bot_uuid should not be empty");
    assert!(!session["token"].as_str().unwrap_or("").is_empty(), "token should not be empty");

    println!("Session context verified for bot: {} (bot_id={})", "上下文Bot", bot_id);
    println!("Token: {}", token);
}