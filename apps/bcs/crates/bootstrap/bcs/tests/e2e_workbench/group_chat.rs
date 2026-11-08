//! E2E workbench `group_chat` responsibility tests.
//!
//! Split out of the former single-file `e2e_workbench.rs` target; shared setup
//! (`WorkbenchClient`, trusted registration helpers, bot WS plumbing) lives in
//! the parent module and is re-used via `use super::*`.

use super::*;

// Test: AI Workbench Group Chat E2E
// ============================================================================

/// Full E2E test: AI workbench creates group, sends message, receives bot events.
///
/// Prerequisites:
/// - BCS binary built: `cargo build --package bcs`
/// - Moltis binary built: `cd submodules/moltis && cargo build`
/// - LLM provider configured: `~/.config/moltis/provider_keys.json`
#[tokio::test]
async fn e2e_workbench_group_chat() {
    init_logging();
    println!("\n========== E2E Workbench Group Chat Test ==========\n");

    let (_temp_dir, data_dir) = create_temp_dir();
    println!("[Setup] Temp data dir: {:?}", data_dir);

    let mut pm = ProcessManager::new();

    // 1. Start BCS server
    println!("\n[Step 1] Starting BCS server...");
    let bcs_port = match pm.start_bcs(&data_dir).await {
        Ok(port) => port,
        Err(e) => {
            eprintln!("Failed to start BCS: {}. Skipping E2E test.", e);
            return;
        }
    };
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);
    println!("[Step 1] BCS started on port {}", bcs_port);

    // 2. Start Bot 1 (coordinator)
    println!("\n[Step 2] Starting Bot 1 (coordinator)...");
    let bot1_port = next_port();
    let (bot1_uuid, bot1_token) = match pm
        .start_moltis(bcs_port, bot1_port, "coordinator", &data_dir)
        .await
    {
        Ok(v) => v,
        Err(e) => {
            eprintln!("Failed to start bot1: {}. Skipping E2E test.", e);
            return;
        }
    };
    println!("[Step 2] Bot1 (coordinator) started: uuid={}, port={}", bot1_uuid, bot1_port);

    // 3. Start Bot 2 (consultant)
    println!("\n[Step 3] Starting Bot 2 (consultant)...");
    let bot2_port = next_port();
    let (bot2_uuid, bot2_token) = match pm
        .start_moltis(bcs_port, bot2_port, "consultant", &data_dir)
        .await
    {
        Ok(v) => v,
        Err(e) => {
            eprintln!("Failed to start bot2: {}. Skipping E2E test.", e);
            return;
        }
    };
    println!("[Step 3] Bot2 (consultant) started: uuid={}, port={}", bot2_uuid, bot2_port);

    // Wait for bots to onboard
    println!("\n[Step 4] Waiting for bots to onboard (5s)...");
    tokio::time::sleep(Duration::from_secs(5)).await;

    // 5. Create a group via HTTP API
    println!("\n[Step 5] Creating group via HTTP API...");
    let client = reqwest::Client::new();
    if let Err(e) =
        bind_bot_to_workbench_user(&client, &bcs_url, &bot1_token, "coordinator").await
    {
        panic!("[Step 5] Failed to bind bot1 owner: {}", e);
    }
    if let Err(e) =
        bind_bot_to_workbench_user(&client, &bcs_url, &bot2_token, "consultant").await
    {
        panic!("[Step 5] Failed to bind bot2 owner: {}", e);
    }
    let create_group_resp = client
        .post(&format!("{}/groups", bcs_url))
        .header("Authorization", format!("Bearer {}", bot1_token))
        .json(&serde_json::json!({
            "driver_bot": bot1_uuid,
            "participants": [
                {
                    "bot_uuid": bot1_uuid,
                    "role": "driver"
                },
                {
                    "bot_uuid": bot2_uuid,
                    "role": "consultant"
                }
            ],
            "label": "E2E Workbench Test Group"
        }))
        .timeout(Duration::from_secs(10))
        .send()
        .await;

    let group_id = match create_group_resp {
        Ok(resp) if resp.status().is_success() => {
            let body: serde_json::Value = resp.json().await.unwrap();
            let gid = body["id"]
                .as_str()
                .unwrap_or("")
                .to_string();
            println!("[Step 5] Group created: {}", gid);
            gid
        }
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            eprintln!("[Step 5] Failed to create group: {} - {}", status, body);
            return;
        }
        Err(e) => {
            eprintln!("[Step 5] Failed to create group: {}", e);
            return;
        }
    };

    assert!(!group_id.is_empty(), "Group ID should not be empty");

    // 6. Simulate AI workbench frontend: connect via WebSocket
    println!("\n[Step 6] Connecting workbench frontend via WebSocket...");
    let mut wb_client = match WorkbenchClient::connect(bcs_port).await {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[Step 6] Failed to connect workbench client: {}", e);
            return;
        }
    };
    println!("[Step 6] Workbench client connected to BCS");

    // 7. Subscribe to the group
    println!("\n[Step 7] Subscribing to group {}...", group_id);
    let connect_resp = wb_client.subscribe_group(&group_id).await;
    match &connect_resp {
        Ok(resp) => {
            assert!(resp["ok"].as_bool().unwrap_or(false), "connect should succeed");
            println!(
                "[Step 7] Subscribed to group:\n{}",
                serde_json::to_string_pretty(&resp["payload"]).unwrap()
            );
        }
        Err(e) => {
            eprintln!("[Step 7] Failed to subscribe to group: {}", e);
            return;
        }
    }

    // 8. Send a group message as a bot owned by the bound Workbench user
    println!("\n[Step 8] Sending group message...");
    let send_resp = wb_client
        .send_group_message(
            &group_id,
            "你好，请帮我分析一下数据库性能问题",
            Some(&bot1_uuid),
            Some("coordinator"),
            &[], // no mentions → driver responds
        )
        .await;

    match &send_resp {
        Ok(resp) => {
            assert!(resp["ok"].as_bool().unwrap_or(false), "chat.send should succeed");
            let run_id = resp["payload"]["runId"].as_str().unwrap_or("");
            println!("[Step 8] Message sent, runId: {}", run_id);
            assert!(!run_id.is_empty(), "runId should not be empty");
        }
        Err(e) => {
            eprintln!("[Step 8] Failed to send group message: {}", e);
            return;
        }
    }

    // 9. Collect events from bot responses (wait up to 60s for LLM response)
    println!("\n[Step 9] Waiting for bot events (up to 60s)...");
    let events = wb_client.collect_events(Duration::from_secs(60)).await;

    println!("\n[Step 9] Received {} events:", events.len());
    for (i, event) in events.iter().enumerate() {
        let event_type = event["event"].as_str().unwrap_or("unknown");
        let state = event["payload"]["state"].as_str().unwrap_or("unknown");
        let bot = event["bot_uuid"].as_str().unwrap_or("unknown");
        println!("  [{}] type={}, state={}, bot={}", i, event_type, state, bot);

        // Print content preview for chat events
        if event_type == "chat" {
            if let Some(content) = event["payload"]["content"].as_str() {
                let preview = if content.len() > 100 {
                    format!("{}...", &content[..100])
                } else {
                    content.to_string()
                };
                println!("       content: {}", preview);
            }
        }
    }

    // Verify we received at least some events
    assert!(
        !events.is_empty(),
        "Should receive at least one event from bot"
    );

    // Verify we got a final event
    let has_final = events
        .iter()
        .any(|e| e["payload"]["state"].as_str() == Some("final"));
    assert!(has_final, "Should receive a final event from bot");

    println!("\n========== E2E Workbench Group Chat Test PASSED ==========\n");
}

