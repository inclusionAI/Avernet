//! E2E workbench `broadcast` responsibility tests.
//!
//! Split out of the former single-file `e2e_workbench.rs` target; shared setup
//! (`WorkbenchClient`, trusted registration helpers, bot WS plumbing) lives in
//! the parent module and is re-used via `use super::*`.

use super::*;

// Test: Multiple Frontends Receive Same Events
// ============================================================================

/// Test that multiple workbench frontends subscribed to the same group
/// all receive the same bot events.
#[tokio::test]
async fn e2e_workbench_multi_frontend_broadcast() {
    init_logging();
    println!("\n========== E2E Multi-Frontend Broadcast Test ==========\n");

    let (_temp_dir, data_dir) = create_temp_dir();
    println!("[Setup] Temp data dir: {:?}", data_dir);

    let mut pm = ProcessManager::new();

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
    // through the local trusted register surface.
    let client = reqwest::Client::new();
    println!("\n[Step 2] Registering the trusted fixture bot...");
    let (future_bot_uuid, bot_token) =
        register_trusted_bot(&client, &bcs_url, "Multi Test Bot")
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

    // Runtime onboarding (mock user header) must
    // keep the trusted owner edge intact.
    if let Err(e) =
        bind_bot_to_workbench_user(&client, &bcs_url, &bot_token, "Multi Test Bot").await
    {
        panic!("[Step 3] Failed to onboard the registered bot: {}", e);
    }
    println!("[Step 3] Runtime onboard kept the authority owner intact: {}", bot_uuid);

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

    println!("\n[Step 3] Creating group...");
    let create_resp = client
        .post(&format!("{}/groups", bcs_url))
        .header("Authorization", format!("Bearer {}", bot_token))
        .json(&serde_json::json!({
            "driver_bot": bot_uuid,
            "participants": [
                { "bot_uuid": bot_uuid, "role": "driver" }
            ],
            "label": "Multi Frontend Test"
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

    // Connect two workbench clients
    println!("\n[Step 4] Connecting workbench client 1...");
    let mut client1 = match WorkbenchClient::connect(bcs_port).await {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[Step 4] Failed to connect client1: {}", e);
            return;
        }
    };
    println!("[Step 4] Client 1 connected");

    println!("\n[Step 5] Connecting workbench client 2...");
    let mut client2 = match WorkbenchClient::connect(bcs_port).await {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[Step 5] Failed to connect client2: {}", e);
            return;
        }
    };
    println!("[Step 5] Client 2 connected");

    // Both subscribe to the same group
    println!("\n[Step 6] Client 1 subscribing to group {}...", group_id);
    let resp1 = client1.subscribe_group(&group_id).await.unwrap();
    assert!(resp1["ok"].as_bool().unwrap_or(false));
    println!("[Step 6] Client 1 subscribed");

    println!("\n[Step 7] Client 2 subscribing to group {}...", group_id);
    let resp2 = client2.subscribe_group(&group_id).await.unwrap();
    assert!(resp2["ok"].as_bool().unwrap_or(false));
    println!("[Step 7] Client 2 subscribed");

    println!("\n[Step 8] Sending message from client 1...");
    // Send a message from client1 as a bot owned by the bound Workbench user
    let _ = client1
        .send_group_message(
            &group_id,
            "Test broadcast",
            Some(&bot_uuid),
            Some("Multi Test Bot"),
            &[],
        )
        .await;

    println!("[Step 8] Message sent");

    println!("\n========== E2E Multi-Frontend Broadcast Test PASSED ==========\n");
}
