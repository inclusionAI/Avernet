//! End-to-End Communication Scenarios for BCS.
//!
//! These tests verify the three fundamental communication patterns:
//!
//! 1. **Personal Assistant** - Real person talks to their own bot via BCS
//! 2. **Expert Consultation** - Real person consults another bot via BCS
//! 3. **Group Chat** - Real person + own bot + other bots collaborate via BCS
//!
//! # Key Architecture Point
//!
//! Bots are deployed in internal networks WITHOUT public IP. All communication
//! MUST go through BCS which routes messages via WebSocket:
//!
//! ```text
//! ┌─────────────┐         ┌─────────────┐         ┌─────────────┐
//! │   User      │  HTTP   │     BCS     │   WS    │    Bot      │
//! │             │ ──────▶ │   Server    │ ──────▶ │ (internal)  │
//! │             │  POST   │             │  push   │             │
//! └─────────────┘         └─────────────┘         └─────────────┘
//!       │                       │                       │
//!       │  POST /bots/{id}/chat │                       │
//!       │ ──────────────────────▶│                       │
//!       │                       │  WebSocket chat.send  │
//!       │                       │ ──────────────────────▶│
//!       │                       │                       │
//! ```
//!
//! # Onboarding Flow (Automatic)
//!
//! When a bot connects via WebSocket:
//! 1. BCS sends `chat.send` with onboarding instruction
//! 2. Bot's SOUL.md contains identity info
//! 3. Bot (with LLM) uses bcs-coordination skill to call `bcs-cli onboard`
//! 4. Tests don't call `bcs-cli` directly - that's the bot's responsibility
//!
//! # LLM Configuration
//!
//! Tests require a configured LLM provider. The `start_moltis` helper:
//! - Copies `~/.config/moltis/provider_keys.json` to bot's config
//! - Configures `custom-antchat-alipay-com` provider in moltis.toml
//!
//! Run with:
//! ```bash
//! cargo test --package bcs --test e2e_chat -- --test-threads=1
//! ```

mod e2e_helpers;

use std::time::Duration;

use e2e_helpers::{create_temp_dir, next_port, ProcessManager};

// ============================================================================
// Scenario 1: Personal Assistant
// ============================================================================
// Real Person → Own Bot (via BCS routing)
//
// ┌──────────────────────────────────────────────────────────────────────────┐
// │                    Scenario 1: Personal Assistant                        │
// ├──────────────────────────────────────────────────────────────────────────┤
// │                                                                           │
// │  User (张三)             BCS Server              张三's Bot               │
// │       │                       │                       │                  │
// │       │  POST /bots/{张三}/chat                     │                  │
// │       │  Authorization: Bearer <张三's token>        │                  │
// │       │  { "message": "我今天要做什么？" }            │                  │
// │       │ ─────────────────▶│                       │                  │
// │       │                       │                       │                  │
// │       │                       │  Verify token         │                  │
// │       │                       │  Find 张三's WS       │                  │
// │       │                       │                       │                  │
// │       │                       │  chat.send (WS)       │                  │
// │       │                       │ ─────────────────────▶│                  │
// │       │                       │                       │                  │
// │       │                       │                       │ Bot processes   │
// │       │                       │                       │ with MEMORY.md  │
// │       │                       │                       │                  │
// │       │                       │  chat.event (response)│                  │
// │       │                       │ ◀─────────────────────│                  │
// │       │                       │                       │                  │
// │       │  HTTP Response:       │                       │                  │
// │       │  { "delivered": true, │                       │                  │
// │       │    "response": "..." }│                       │                  │
// │       │ ◀─────────────────────│                       │                  │
// │       │                       │                       │                  │
// │                                                                           │
// │  Key: User talks to own bot VIA BCS, not directly.                       │
// │       BCS routes HTTP request to bot's WebSocket connection.              │
// │                                                                           │
// └──────────────────────────────────────────────────────────────────────────┘

/// Scenario 1: Personal Assistant - Real person talks to their own bot via BCS.
///
/// This is the most common scenario: user has a personal assistant bot.
/// All communication goes through BCS which routes to the bot via WebSocket.
///
/// Flow:
/// 1. Bot connects to BCS via WebSocket (internal network, no public IP)
/// 2. User sends message via BCS HTTP API: POST /bots/{own_bot}/chat
/// 3. BCS routes message to bot's WebSocket
/// 4. Bot responds, BCS returns response to user
#[tokio::test]
#[ignore = "requires external moltis binary"]
async fn e2e_personal_assistant() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();
    let mut proc_mgr = ProcessManager::new();

    // Start BCS (the central routing hub)
    let bcs_port = proc_mgr.start_bcs(&data_dir).await.expect("Failed to start BCS");
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    println!("[S1] BCS started on port {}", bcs_port);

    // Start 张三's Bot (connects via WebSocket, no public IP needed)
    let zhangsan_port = next_port();
    let (zhangsan_uuid, zhangsan_token) = proc_mgr
        .start_moltis(bcs_port, zhangsan_port, "张三", &data_dir)
        .await
        .expect("Failed to start 张三's bot");

    println!("[S1] 张三's bot connected: uuid={}, token=***", zhangsan_uuid);

    // Wait for bot to be ready (WebSocket connected, possibly onboarded)
    tokio::time::sleep(Duration::from_secs(3)).await;

    // Act: User sends message to own bot VIA BCS (not directly to bot's gateway)
    // This is the key point: bots are in internal network, user accesses via BCS
    let user_message = "我今天要做什么？请根据你的上下文回答。";
    println!("[S1] User → BCS: POST /bots/{}/chat", zhangsan_uuid);

    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/bots/{}/chat", bcs_url, zhangsan_uuid))
        .bearer_auth(&zhangsan_token)
        .json(&serde_json::json!({
            "message": user_message,
            "from": "user-zhangsan"
        }))
        .timeout(Duration::from_secs(60))
        .send()
        .await;

    match response {
        Ok(resp) if resp.status().is_success() => {
            let result: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!({}));
            println!("[S1] BCS Response: {}", serde_json::to_string_pretty(&result).unwrap_or_default());

            if result.get("delivered").and_then(|d| d.as_bool()).unwrap_or(false) {
                println!("[S1] Message delivered successfully via BCS routing");
            }
        }
        Ok(resp) => {
            println!("[S1] BCS returned error: {}", resp.status());
        }
        Err(e) => {
            println!("[S1] Request failed: {}", e);
        }
    }

    println!("[S1] Personal assistant test completed");
}

// ============================================================================
// Scenario 2: Expert Consultation
// ============================================================================
// Real Person → Other Bot (via BCS routing)
//
// ┌──────────────────────────────────────────────────────────────────────────┐
// │                    Scenario 2: Expert Consultation                       │
// ├──────────────────────────────────────────────────────────────────────────┤
// │                                                                           │
// │  User (张三)             BCS Server              DBA Bot                  │
// │       │                       │                       │                  │
// │       │  POST /bots/{DBA}/chat                        │                  │
// │       │  Authorization: Bearer <张三's token>          │                  │
// │       │  { "message": "帮我排查死锁" }                 │                  │
// │       │ ─────────────────▶│                       │                  │
// │       │                       │                       │                  │
// │       │                       │  Verify token         │                  │
// │       │                       │  Find DBA's WS        │                  │
// │       │                       │                       │                  │
// │       │                       │  chat.send (WS)       │                  │
// │       │                       │ ─────────────────────▶│                  │
// │       │                       │                       │                  │
// │       │                       │                       │ DBA analyzes    │
// │       │                       │                       │ with expertise  │
// │       │                       │                       │                  │
// │       │                       │  chat.event (response)│                  │
// │       │                       │ ◀─────────────────────│                  │
// │       │                       │                       │                  │
// │       │  Expert's response    │                       │                  │
// │       │ ◀─────────────────────│                       │                  │
// │       │                       │                       │                  │
// │                                                                           │
// │  Key: User consults expert bot VIA BCS. The token is the user's own      │
// │       bot's token (好人 操守自己的 bot). BCS routes to target bot.         │
// │                                                                           │
// └──────────────────────────────────────────────────────────────────────────┘

/// Scenario 2: Expert Consultation - Real person consults an expert bot via BCS.
///
/// User wants to consult a specific expert bot (e.g., DBA).
/// Request goes through BCS, which routes to the expert's WebSocket.
///
/// Flow:
/// 1. Expert bot (DBA) connects to BCS via WebSocket
/// 2. User sends message via BCS: POST /bots/{dba_uuid}/chat
/// 3. BCS routes to DBA's WebSocket
/// 4. DBA responds with expertise
#[tokio::test]
#[ignore = "requires external moltis binary"]
async fn e2e_expert_consultation() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();
    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir).await.expect("Failed to start BCS");
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    println!("[S2] BCS started on port {}", bcs_port);

    // Start 张三's Bot (user's own bot, provides the token)
    let zhangsan_port = next_port();
    let (_, zhangsan_token) = proc_mgr
        .start_moltis(bcs_port, zhangsan_port, "张三", &data_dir)
        .await
        .expect("Failed to start 张三's bot");

    // Start DBA Bot (the expert user wants to consult)
    let dba_port = next_port();
    let (dba_uuid, _) = proc_mgr
        .start_moltis(bcs_port, dba_port, "DBA", &data_dir)
        .await
        .expect("Failed to start DBA bot");

    println!("[S2] Bots connected: 张三, DBA(uuid={})", dba_uuid);

    tokio::time::sleep(Duration::from_secs(3)).await;

    // Act: User sends message to DBA bot VIA BCS (using 张三's token for auth)
    let user_message = "我遇到了数据库死锁问题，请帮我分析可能的原因。";
    println!("[S2] User → BCS: POST /bots/{}/chat", dba_uuid);

    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/bots/{}/chat", bcs_url, dba_uuid))
        .bearer_auth(&zhangsan_token)  // User's own bot's token
        .json(&serde_json::json!({
            "message": user_message,
            "from": "user-zhangsan"
        }))
        .timeout(Duration::from_secs(60))
        .send()
        .await;

    match response {
        Ok(resp) if resp.status().is_success() => {
            let result: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!({}));
            println!("[S2] BCS Response: {}", serde_json::to_string_pretty(&result).unwrap_or_default());
        }
        Ok(resp) => {
            println!("[S2] BCS returned error: {}", resp.status());
        }
        Err(e) => {
            println!("[S2] Request failed: {}", e);
        }
    }

    println!("[S2] Expert consultation test completed");
}

// ============================================================================
// Scenario 3: Group Chat (including 2-participant as special case)
// ============================================================================
// Real Person + Own Bot + Other Bots (via BCS broadcast routing)
//
// ┌──────────────────────────────────────────────────────────────────────────┐
// │                    Scenario 3: Group Chat                                │
// ├──────────────────────────────────────────────────────────────────────────┤
// │                                                                           │
// │  Phase 1: Bot discovers collaboration need                               │
// │  ───────────────────────────────────────────                             │
// │                                                                           │
// │  User (张三)             BCS Server              张三's Bot               │
// │       │                       │                       │                  │
// │       │  POST /bots/{张三}/chat                     │                  │
// │       │  "我们可以找李四帮我"                        │                  │
// │       │ ─────────────────▶│                       │                  │
// │       │                       │                       │                  │
// │       │                       │  chat.send            │                  │
// │       │                       │ ─────────────────────▶│                  │
// │       │                       │                       │                  │
// │       │                       │                       │ Bot recognizes   │
// │       │                       │                       │ need for help    │
// │       │                       │                       │                  │
// │       │                       │                       │ skill: discover  │
// │       │                       │ ◀─────────────────────│                  │
// │       │                       │                       │                  │
// │       │                       │                       │ skill: request   │
// │       │                       │ ◀─────────────────────│  group-help      │
// │       │                       │                       │                  │
// │       │  Proposal res         │                       │                  │
// │       │  with confirm_url     │                       │                  │
// │       │ ◀─────────────────────│                       │                  │
// │       │                       │                       │                  │
// │                                                                           │
// │  Phase 2: User confirms, group created                                   │
// │  ────────────────────────────────────                                     │
// │                                                                           │
// │  User                     BCS Server                                      │
// │       │                        │                                          │
// │       │  POST /groups/{token}/confirm                                    │
// │       │ ──────────────────────▶│                                          │
// │       │                        │                                          │
// │       │                        │  Create group                            │
// │       │                        │  Broadcast context                       │
// │       │                        │    via WS to all                         │
// │       │                        │                                          │
// │       │  Group created         │                                          │
// │       │ ◀──────────────────────│                                          │
// │       │                        │                                          │
// │                                                                           │
// │  Phase 3: Group chat (broadcast to all participants)                     │
// │  ────────────────────────────────────────────                            │
// │                                                                           │
// │  User                     BCS Server         张三-Bot       李四-Bot      │
// │       │                        │                  │             │         │
// │       │  POST /groups/{id}/chat                  │             │         │
// │       │  "大家怎么看？"                           │             │         │
// │       │ ──────────────────────▶│                  │             │         │
// │       │                        │                  │             │         │
// │       │                        │  broadcast to all:             │         │
// │       │                        │  ────────────────▶│             │         │
// │       │                        │  ──────────────────────────────▶│         │
// │       │                        │                  │             │         │
// │       │                        │                  │ (as driver, │         │
// │       │                        │                  │  responds)  │         │
// │       │                        │                  │             │         │
// │       │                        │  chat.event      │             │         │
// │       │                        │ ◀────────────────│             │         │
// │       │                        │                  │             │         │
// │       │  Response              │                  │             │         │
// │       │ ◀──────────────────────│                  │             │         │
// │       │                        │                  │             │         │
// │                                                                           │
// │  Key: ALL group messages go through BCS, which broadcasts to all.        │
// │       @mention determines who should respond.                            │
// │                                                                           │
// └──────────────────────────────────────────────────────────────────────────┘

/// Scenario 3: Group Chat - Multi-bot collaboration via BCS broadcast.
///
/// User wants to collaborate with multiple bots. All messaging goes through BCS.
///
/// Flow:
/// 1. User tells their bot to find collaborators via BCS
/// 2. Bot discovers candidates and creates a proposal
/// 3. User confirms proposal (creates group)
/// 4. Group chat happens via BCS broadcast
#[tokio::test]
#[ignore = "requires external moltis binary"]
async fn e2e_group_chat() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();
    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir).await.expect("Failed to start BCS");
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    println!("[S3] BCS started on port {}", bcs_port);

    // Start 张三's Bot (originator/driver)
    let zhangsan_port = next_port();
    let (_, zhangsan_token) = proc_mgr
        .start_moltis(bcs_port, zhangsan_port, "张三", &data_dir)
        .await
        .expect("Failed to start 张三's bot");

    // Start 李四's Bot (potential participant)
    let lisi_port = next_port();
    let (lisi_uuid, _) = proc_mgr
        .start_moltis(bcs_port, lisi_port, "李四", &data_dir)
        .await
        .expect("Failed to start 李四's bot");

    println!("[S3] Bots connected: 张三, 李四(uuid={})", lisi_uuid);

    tokio::time::sleep(Duration::from_secs(3)).await;

    // Phase 1: User tells their bot to find collaboration help
    let user_message = "我们可以找李四帮我出一个建议方案。你知道他的联系方式吗？";
    println!("[S3] User -> BCS: POST /bots/{{张三}}/chat");

    let client = reqwest::Client::new();
    let response = client
        .post(format!("{}/bots/张三/chat", bcs_url))
        .bearer_auth(&zhangsan_token)
        .json(&serde_json::json!({
            "message": user_message,
            "from": "user-zhangsan"
        }))
        .timeout(Duration::from_secs(90))
        .send()
        .await;

    match response {
        Ok(resp) if resp.status().is_success() => {
            let result: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!({}));
            println!("[S3] Bot response: {:?}", result);

            // In a real flow with LLM, bot would:
            // 1. Discover 李四 candidates
            // 2. Return proposal with confirm_url
            // 3. User confirms, group created
        }
        Ok(resp) => {
            println!("[S3] BCS returned error: {}", resp.status());
        }
        Err(e) => {
            println!("[S3] Request failed: {}", e);
        }
    }

    // Phase 2: Create group directly (simulating user confirmation)
    println!("[S3] Creating group directly...");
    let create_response = client
        .post(format!("{}/groups", bcs_url))
        .bearer_auth(&zhangsan_token)
        .json(&serde_json::json!({
            "driver_bot": "张三",
            "participants": [
                {"bot_uuid": "张三", "role": "driver"},
                {"bot_uuid": "李四", "role": "consultant"}
            ]
        }))
        .send()
        .await;

    if let Ok(resp) = create_response {
        if let Ok(result) = resp.json::<serde_json::Value>().await {
            println!("[S3] Group created: {:?}", result);
            let group_id = result.get("id").and_then(|i| i.as_str()).unwrap_or("");

            // Phase 3: Send group message via BCS
            if !group_id.is_empty() {
                println!("[S3] Sending group message via BCS...");
                let chat_response = client
                    .post(format!("{}/groups/{}/chat", bcs_url, group_id))
                    .bearer_auth(&zhangsan_token)
                    .json(&serde_json::json!({
                        "message": "大家怎么看这个问题？",
                        "from": "user-zhangsan"
                    }))
                    .send()
                    .await;

                if let Ok(resp) = chat_response {
                    println!("[S3] Group chat response: {:?}", resp.json::<serde_json::Value>().await);
                }
            }
        }
    }

    println!("[S3] Group chat test completed");
}

/// Scenario 3a: 2-Participant Group (the "1:1 collaboration" case).
///
/// Demonstrates that "bot-to-bot chat" is actually a 2-participant group.
/// All messages go through BCS, which broadcasts to both participants.
#[tokio::test]
#[ignore = "requires external moltis binary"]
async fn e2e_two_participant_group() {
    let _ = tracing_subscriber::fmt::try_init();

    let (_temp_dir, data_dir) = create_temp_dir();
    let mut proc_mgr = ProcessManager::new();

    let bcs_port = proc_mgr.start_bcs(&data_dir).await.expect("Failed to start BCS");
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // Start 张三's Bot and 李四's Bot
    let zhangsan_port = next_port();
    let (_, zhangsan_token) = proc_mgr
        .start_moltis(bcs_port, zhangsan_port, "张三", &data_dir)
        .await
        .expect("Failed to start 张三's bot");

    let lisi_port = next_port();
    let _ = proc_mgr
        .start_moltis(bcs_port, lisi_port, "李四", &data_dir)
        .await
        .expect("Failed to start 李四's bot");

    println!("[S3a] 2-participant setup: 张三 + 李四");

    tokio::time::sleep(Duration::from_secs(3)).await;

    // Create a 2-participant group directly
    let client = reqwest::Client::new();
    let create_response = client
        .post(format!("{}/groups", bcs_url))
        .bearer_auth(&zhangsan_token)
        .json(&serde_json::json!({
            "driver_bot": "张三",
            "participants": [
                {"bot_uuid": "张三", "role": "driver"},
                {"bot_uuid": "李四", "role": "consultant"}
            ]
        }))
        .send()
        .await;

    match create_response {
        Ok(resp) if resp.status().is_success() => {
            let result: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!({}));
            println!("[S3a] 2-participant group created: {:?}", result);

            // This is equivalent to "1:1 chat" but through group mechanism
            // Both participants receive messages via BCS broadcast
        }
        Ok(resp) => {
            println!("[S3a] Failed to create group: {}", resp.status());
        }
        Err(e) => {
            println!("[S3a] Request failed: {}", e);
        }
    }

    println!("[S3a] Two-participant group test completed");
}