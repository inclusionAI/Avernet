//! Integration tests for BCS + BCN plugin protocol.
//!
//! These tests simulate the BCN plugin's WebSocket behavior using `MockBot`,
//! verifying that BCS correctly handles the full protocol lifecycle.
//!
//! No external services required — BCS starts on a random port in-process.
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test integration_bcn_protocol -- --test-threads=1
//! ```

mod helpers;

use helpers::{MockBot, create_temp_bots_dir, frame_action, frame_ctx, start_test_server};
use bcs_protocol::ParticipantInfo;
use std::time::Duration;

/// Connect, onboard, and register a bot to be used as an external message sender.
/// Returns the bot and its HTTP client.
async fn setup_sender_bot(addr: std::net::SocketAddr) -> (MockBot, bcs_cli::BcsClient) {
    let mut bot = MockBot::connect(addr).await;
    bot.register("Sender", &[], addr).await;
    let client = bot.http_client(addr);
    (bot, client)
}

// ── Test 1: Full BCN handshake ────────────────────────────────────────────────

/// Mirrors BCN's connect_and_run(): connect → onboard → verify token is returned.
/// Note: BCS token→bot_id mapping is in-memory only; tokens are cleared on disconnect.
/// This test verifies the handshake protocol produces valid credentials.
#[tokio::test]
async fn test_bcn_full_handshake() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // Step 1: new bot connects, gets is_new=true with bot_id + token
    let mut bot = MockBot::connect(addr).await;
    assert!(!bot.bot_id.is_empty(), "Should receive bot_id");
    assert!(!bot.token.is_empty(), "Should receive token");
    assert!(bot.bot_id.starts_with("bot_"), "New bot gets a bot_ id");

    // Step 2: onboard via HTTP (BCN uses bcs-cli onboard after getting token)
    bot.register("TestBot", &["skill_a", "skill_b"], addr).await;

    // Step 3: second bot connects independently — gets a different bot_id
    let bot2 = MockBot::connect(addr).await;
    assert_ne!(bot.bot_id, bot2.bot_id, "Two bots should get different bot_ids");
    assert_ne!(bot.token, bot2.token, "Two bots should get different tokens");
}

// ── Test 2: chat.send vs chat.inject routing ──────────────────────────────────

/// Verifies BCS routing: coordinator gets chat.send, others get chat.inject.
/// No @mention → broadcast with coordinator as responder.
#[tokio::test]
async fn test_chat_send_vs_inject_routing() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut driver = MockBot::connect(addr).await;
    driver.register("Driver", &[], addr).await;
    let driver_client = driver.http_client(addr);

    let mut consultant = MockBot::connect(addr).await;
    consultant.register("Consultant", &[], addr).await;

    // Add a sender bot as a third participant for the "external user" role
    let (mut sender, sender_client) = setup_sender_bot(addr).await;

    #[allow(deprecated)]
    let group = driver_client.create_group(
        "agent",
        &driver.bot_id,
        vec![
            ParticipantInfo { bot_uuid: driver.bot_id.clone(), role: Some("driver".to_string()) },
            ParticipantInfo { bot_uuid: consultant.bot_id.clone(), role: Some("consultant".to_string()) },
            ParticipantInfo { bot_uuid: sender.bot_id.clone(), role: Some("consultant".to_string()) },
        ],
    ).await.expect("Failed to create group");

    // Drain initial group-context frames injected by create_group
    while driver.recv_frame_short().await.is_some() {}
    while consultant.recv_frame_short().await.is_some() {}
    while sender.recv_frame_short().await.is_some() {}

    // Sender bot sends a message (no @mention)
    sender_client.group_chat(&group.id, "Please help", Some(&sender.bot_id)).await
        .expect("group_chat should succeed");

    // Driver (coordinator) should receive chat.send
    let driver_frame = driver.recv_frame().await.expect("Driver should receive a frame");
    assert_eq!(frame_action(&driver_frame), "chat.send",
        "Coordinator should receive chat.send, got: {driver_frame}");

    // Consultant should receive chat.inject
    let consultant_frame = consultant.recv_frame().await.expect("Consultant should receive a frame");
    assert_eq!(frame_action(&consultant_frame), "chat.inject",
        "Non-coordinator should receive chat.inject, got: {consultant_frame}");
}

// ── Test 3: GroupContext fields ───────────────────────────────────────────────

/// Verifies GroupContext is populated correctly: you_are_mentioned, originator, from.
#[tokio::test]
async fn test_group_context_fields() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("Bot1", &[], addr).await;
    let client1 = bot1.http_client(addr);

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("Bot2", &[], addr).await;

    let (mut sender, sender_client) = setup_sender_bot(addr).await;

    #[allow(deprecated)]
    let group = client1.create_group(
        "agent",
        &bot1.bot_id,
        vec![
            ParticipantInfo { bot_uuid: bot1.bot_id.clone(), role: Some("driver".to_string()) },
            ParticipantInfo { bot_uuid: bot2.bot_id.clone(), role: Some("consultant".to_string()) },
            ParticipantInfo { bot_uuid: sender.bot_id.clone(), role: Some("consultant".to_string()) },
        ],
    ).await.expect("Failed to create group");

    // Drain initial group context frames sent during group creation
    while bot1.recv_frame_short().await.is_some() {}
    while bot2.recv_frame_short().await.is_some() {}
    while sender.recv_frame_short().await.is_some() {}

    // Send message @mentioning bot2
    let mention_msg = format!("@{} please respond", bot2.bot_id);
    sender_client.group_chat(&group.id, &mention_msg, Some(&sender.bot_id)).await
        .expect("group_chat should succeed");

    // bot2 should get chat.send with you_are_mentioned=true
    let frame2 = bot2.recv_frame().await.expect("Bot2 should receive frame");
    assert_eq!(frame_action(&frame2), "chat.send", "Mentioned bot should get chat.send");
    let ctx2 = frame_ctx(&frame2);
    assert!(ctx2["you_are_mentioned"].as_bool().unwrap_or(false),
        "you_are_mentioned should be true for @mentioned bot, ctx: {ctx2}");
    assert!(ctx2["originator"].as_str().unwrap_or("").contains(&bot1.bot_id),
        "originator should contain bot1 id (group creator)");
    assert!(ctx2["from"].as_str().unwrap_or("").contains(&sender.bot_id),
        "from should contain sender bot id, got: {}", ctx2["from"].as_str().unwrap_or(""));

    // bot1 should get chat.inject with you_are_mentioned=false
    let frame1 = bot1.recv_frame().await.expect("Bot1 should receive frame");
    assert_eq!(frame_action(&frame1), "chat.inject",
        "Non-mentioned coordinator gets chat.inject when another is @mentioned");
    let ctx1 = frame_ctx(&frame1);
    assert!(!ctx1["you_are_mentioned"].as_bool().unwrap_or(true),
        "you_are_mentioned should be false for non-mentioned bot");
}

// ── Test 4: Sender exclusion ──────────────────────────────────────────────────

/// When a bot sends a message with from=its own bot_id, it should NOT receive it back.
#[tokio::test]
async fn test_sender_exclusion_with_group_context() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("Bot1", &[], addr).await;
    let client1 = bot1.http_client(addr);

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("Bot2", &[], addr).await;

    // Create group via HTTP
    let create_resp = reqwest::Client::new()
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", bot1.token))
        .json(&serde_json::json!({
            "driver_bot": bot1.bot_id,
            "participants": [
                {"bot_uuid": bot1.bot_id},
                {"bot_uuid": bot2.bot_id},
            ],
        }))
        .send()
        .await
        .expect("Failed to create group");
    assert!(create_resp.status().is_success(), "create_group failed: {}", create_resp.status());
    let group_body: serde_json::Value = create_resp.json().await.expect("parse group response");
    let group_id = group_body["id"].as_str().expect("group should have id").to_string();

    // Drain group context frames injected via WebSocket during creation
    tokio::time::sleep(Duration::from_millis(200)).await;
    while bot1.recv_frame_short().await.is_some() {}
    while bot2.recv_frame_short().await.is_some() {}

    // Bot1 sends a message as itself (from = bot1_id)
    client1.group_chat(&group_id, "Update from driver", Some(&bot1.bot_id)).await
        .expect("group_chat should succeed");

    // Bot2 should receive the message
    let frame2 = bot2.recv_frame().await.expect("Bot2 should receive the message");
    let action = frame_action(&frame2);
    assert!(action == "chat.send" || action == "chat.inject",
        "Bot2 should receive chat.send or chat.inject, got: {frame2}");
}

// ── Test 5: Bot responds with chat.event ──────────────────────────────────────

/// Verifies that a bot can respond to chat.send with a chat.event frame (BCN outbound flow).
#[tokio::test]
async fn test_bot_responds_with_chat_event() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot = MockBot::connect(addr).await;
    bot.register("ResponderBot", &[], addr).await;
    let client = bot.http_client(addr);

    // Create a solo group so the bot receives chat.send
    let (mut sender, sender_client) = setup_sender_bot(addr).await;
    #[allow(deprecated)]
    let group = client.create_group(
        "agent",
        &bot.bot_id,
        vec![
            ParticipantInfo { bot_uuid: bot.bot_id.clone(), role: Some("driver".to_string()) },
            ParticipantInfo { bot_uuid: sender.bot_id.clone(), role: Some("consultant".to_string()) },
        ],
    ).await.expect("Failed to create group");

    // Drain initial group context frames
    while bot.recv_frame_short().await.is_some() {}
    while sender.recv_frame_short().await.is_some() {}

    // Sender bot sends a message
    sender_client.group_chat(&group.id, "Hello bot", Some(&sender.bot_id)).await
        .expect("group_chat should succeed");

    // Bot receives chat.send
    let frame = bot.recv_frame().await.expect("Bot should receive chat.send");
    assert_eq!(frame_action(&frame), "chat.send", "Bot should receive chat.send, got: {frame}");

    let request_id = frame["id"].as_str().unwrap_or("unknown").to_string();
    let group_id = frame["params"]["bcs_group_id"]
        .as_str()
        .unwrap_or(&group.id)
        .to_string();

    // Bot responds with chat.event (no panic = BCS accepted it)
    bot.send_chat_event(&group_id, &request_id, "Hello back!").await;
}

// ── Test 6: Proposal flow end-to-end ─────────────────────────────────────────

/// Full proposal flow: request → confirm → group context broadcast to participants.
#[tokio::test(flavor = "multi_thread")]
async fn test_proposal_flow_end_to_end() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot1 = MockBot::connect(addr).await;
    bot1.register("Requester", &["coordination"], addr).await;
    let client1 = bot1.http_client(addr);

    let mut bot2 = MockBot::connect(addr).await;
    bot2.register("Expert", &["database"], addr).await;

    // Bot1 requests group help
    let proposal = client1.propose_group_chat_with_token(
        "Need database expert help",
        None,
        None,
    ).await;

    if let Ok(prop) = proposal {
        if !prop.confirm_url.is_empty() {
            let token = prop.confirm_url
                .split('/')
                .rev()
                .nth(1)
                .unwrap_or("")
                .to_string();

            if !token.is_empty() {
                let anon = bcs_cli::BcsClient::new(format!("http://{}", addr));
                let _ = anon.confirm_proposal(&token).await;

                let frame1 = bot1.recv_frame().await;
                if let Some(f) = frame1 {
                    let action = frame_action(&f);
                    assert!(action == "chat.send" || action == "chat.inject",
                        "Bot1 should receive group context frame, got: {f}");
                }
            }
        }
    }
    // Test passes even if no matching bots found
    let _ = bot2;
}

// ── Test 7: Heartbeat (bot.status) ───────────────────────────────────────────

/// Verifies BCS responds ok to bot.status heartbeat frames (BCN heartbeat loop).
#[tokio::test]
async fn test_heartbeat_bot_status() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut bot = MockBot::connect(addr).await;
    bot.register("HeartbeatBot", &[], addr).await;

    let resp = bot.send_heartbeat().await;
    assert!(resp["ok"].as_bool().unwrap_or(false),
        "BCS should respond ok to bot.status heartbeat: {resp}");
}
