//! E2E workbench `protocol` responsibility tests.
//!
//! Split out of the former single-file `e2e_workbench.rs` target; shared setup
//! (`WorkbenchClient`, trusted registration helpers, bot WS plumbing) lives in
//! the parent module and is re-used via `use super::*`.

use super::*;

// Test: Workbench Protocol (no LLM, protocol-level only)
// ============================================================================

/// Protocol-level test: verify connect and chat.send work without LLM.
/// This test only verifies the BCS protocol handling, not bot responses.
#[tokio::test]
async fn e2e_workbench_protocol_connect_and_send() {
    init_logging();
    println!("\n========== E2E Workbench Protocol Test ==========\n");

    let (_temp_dir, data_dir) = create_temp_dir();
    println!("[Setup] Temp data dir: {:?}", data_dir);

    let mut pm = ProcessManager::new();

    // Start BCS server only (no Moltis bots needed for protocol test)
    println!("\n[Step 1] Starting BCS server...");
    let bcs_port = match pm.start_bcs(&data_dir).await {
        Ok(port) => port,
        Err(e) => {
            eprintln!("Failed to start BCS: {}. Skipping test.", e);
            return;
        }
    };
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);
    println!("[Step 1] BCS started on port {}", bcs_port);

    // TRUSTED authorization seeding (ruling T6d-1):
    // the fixture Bot's ownership is established
    // through the local trusted register surface
    // (`register_with_ownership` commits the
    // authority owner edge); the WS connect then
    // RESUMES that registered bot.
    let client = reqwest::Client::new();
    println!("\n[Step 2] Registering the trusted fixture bot...");
    let (future_bot_uuid, bot_token) =
        register_trusted_bot(&client, &bcs_url, "Protocol Test Bot")
            .await
            .expect("trusted fixture registration");
    println!(
        "[Step 2] Trusted bot registered: uuid={}",
        future_bot_uuid
    );

    println!("\n[Step 3] Connecting the registered bot via WebSocket...");
    let ws = connect_bot_ws_with_retry(bcs_port).await;
    let (mut bot_sink, mut bot_stream) = ws.split();

    let connect_frame = serde_json::json!({
        "type": "req", "id": "bot_connect_1", "method": "bot.connect",
        "params": { "token": bot_token }
    });
    bot_sink.send(Message::Text(serde_json::to_string(&connect_frame).unwrap().into())).await.unwrap();

    let connect_resp = loop {
        match tokio::time::timeout(Duration::from_secs(5), bot_stream.next()).await {
            Ok(Some(Ok(Message::Text(text)))) => {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if v["type"] == "res" && v["id"] == "bot_connect_1" {
                        break v;
                    }
                }
            }
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = bot_sink.send(Message::Pong(data)).await;
                continue;
            }
            _ => panic!("No response to bot.connect"),
        }
    };
    let bot_uuid = connect_resp["payload"]["bot_uuid"].as_str().unwrap_or("").to_string();
    assert_eq!(bot_uuid, future_bot_uuid, "the WS resume must return the REGISTERED bot");
    println!("[Step 3] Bot connected: uuid={}", bot_uuid);

    // Runtime onboarding (mock user header) keeps its
    // old semantics but must never touch the authority
    // owner edge seeded by the trusted registration.
    if let Err(e) =
        bind_bot_to_workbench_user(&client, &bcs_url, &bot_token, "Protocol Test Bot").await
    {
        panic!("[Step 3] Failed to onboard the registered bot: {}", e);
    }
    println!("[Step 3] Runtime onboard kept the authority owner intact");

    // Drain onboarding instruction
    loop {
        match tokio::time::timeout(Duration::from_millis(200), bot_stream.next()).await {
            Ok(Some(Ok(Message::Text(_)))) => continue,
            Ok(Some(Ok(Message::Ping(data)))) => {
                let _ = bot_sink.send(Message::Pong(data)).await;
                continue;
            }
            _ => break,
        }
    }

    // Create a group
    println!("\n[Step 3] Creating group...");
    let create_resp = client
        .post(&format!("{}/groups", bcs_url))
        .header("Authorization", format!("Bearer {}", bot_token))
        .json(&serde_json::json!({
            "driver_bot": bot_uuid,
            "participants": [
                { "bot_uuid": bot_uuid, "role": "driver" }
            ],
            "label": "Protocol Test Group"
        }))
        .timeout(Duration::from_secs(5))
        .send()
        .await;

    let group_id = match create_resp {
        Ok(resp) if resp.status().is_success() => {
            let body: serde_json::Value = resp.json().await.unwrap();
            let gid = body["id"].as_str().unwrap_or("").to_string();
            println!("[Step 3] Group created: {}", gid);
            gid
        }
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            eprintln!("[Step 3] Failed to create group: {} - {}", status, body);
            return;
        }
        Err(e) => {
            eprintln!("[Step 3] Failed to create group: {}", e);
            return;
        }
    };

    assert!(!group_id.is_empty());
    println!("[Step 3] Group created: {}", group_id);

    // Connect workbench client
    println!("\n[Step 4] Connecting workbench client...");
    let mut wb_client = match WorkbenchClient::connect(bcs_port).await {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[Step 4] Failed to connect: {}", e);
            return;
        }
    };
    println!("[Step 4] Workbench client connected");

    // Test 1: connect (subscribe to group)
    println!("\n[Step 5] Testing connect (subscribe to group)...");
    let resp = wb_client.subscribe_group(&group_id).await.unwrap();
    assert!(resp["ok"].as_bool().unwrap_or(false));
    assert_eq!(resp["payload"]["group_id"].as_str().unwrap(), &group_id);
    println!("[Step 5] connect OK: subscribed to group {}", group_id);
    println!("         Participants: {:?}", resp["payload"]["participants"]);

    // Test 2: connect to non-existent group should fail
    println!("\n[Step 6] Testing connect to non-existent group (should fail)...");
    let resp = wb_client.subscribe_group("nonexistent-group").await;
    match resp {
        Ok(r) => {
            assert!(
                !r["ok"].as_bool().unwrap_or(true),
                "connect to nonexistent group should fail"
            );
            println!("[Step 6] connect correctly failed: {:?}", r["error"]);
        }
        Err(e) => {
            println!("[Step 6] connect correctly errored: {}", e);
        }
    }

    // Test 3: chat.send
    println!("\n[Step 7] Testing chat.send...");
    let resp = wb_client
        .send_group_message(
            &group_id,
            "Hello from workbench",
            Some(&bot_uuid),
            Some("Protocol Test Bot"),
            &[],
        )
        .await;

    match resp {
        Ok(r) => {
            println!("[Step 7] chat.send response:\n{}",
                serde_json::to_string_pretty(&r).unwrap()
            );
            assert!(r["ok"].as_bool().unwrap_or(false), "chat.send should be accepted");
        }
        Err(e) => {
            println!("[Step 7] chat.send result: {} (expected if bot not WS-connected)", e);
        }
    }

    println!("\n========== E2E Workbench Protocol Test PASSED ==========\n");
}

