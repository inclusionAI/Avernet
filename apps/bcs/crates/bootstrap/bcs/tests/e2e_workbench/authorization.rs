//! E2E workbench `authorization` responsibility tests.
//!
//! Split out of the former single-file `e2e_workbench.rs` target; shared setup
//! (`WorkbenchClient`, trusted registration helpers, bot WS plumbing) lives in
//! the parent module and is re-used via `use super::*`.

use super::*;

// Test: RAW onboarding (no trusted seed) is DENIED
// ============================================================================

/// v0 polarity (bot-authority ruling T6d-1.2): a
/// raw WS connect + mock-user onboard flow never
/// mints authority ownership (A01: runtime self-
/// declared identity cannot mint authority), so
/// the workbench group gate DENIES exactly the
/// flow that the seeded harness above allows.
#[tokio::test]
async fn e2e_workbench_raw_onboarding_denies_group_access() {
    init_logging();
    println!("\n========== E2E Raw Onboarding Polarity Test ==========\n");

    let (_temp_dir, data_dir) = create_temp_dir();
    let mut pm = ProcessManager::new();

    // v0 deny polarity is the POST-migration posture: pin it with
    // the migration-window legacy fallback explicitly OFF. With the
    // shim ON (default) a self-declared runtime header legitimately
    // echoes legacy created_by ownership during the window; that
    // posture has its own store-level coverage
    // (`legacy_created_by_fallback_resolves_only_the_legacy_creator`
    // on both drivers).
    let bcs_port = match pm
        .start_bcs_with_extra_config(
            &data_dir,
            Some(serde_json::json!({
                "bot_authority": { "legacy_created_by_fallback": false }
            })),
        )
        .await
    {
        Ok(port) => port,
        Err(e) => {
            eprintln!("Failed to start BCS: {}. Skipping test.", e);
            return;
        }
    };
    let bcs_url = format!("http://127.0.0.1:{}", bcs_port);

    // RAW connect: anonymous WS connect mints an
    // UNOWNED bot; the mock-user onboard never
    // touches authority (a runtime self-declared
    // user header must not mint authority
    // ownership).
    println!("\n[Step 1] Raw WS connect (no trusted registration)...");
    let ws = connect_bot_ws_with_retry(bcs_port).await;
    let (mut bot_sink, mut bot_stream) = ws.split();
    let connect_frame = serde_json::json!({
        "type": "req", "id": "bot_connect_1", "method": "bot.connect", "params": {}
    });
    bot_sink
        .send(Message::Text(
            serde_json::to_string(&connect_frame).unwrap().into(),
        ))
        .await
        .unwrap();
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
    let bot_token = connect_resp["payload"]["token"].as_str().unwrap_or("").to_string();
    println!("[Step 1] Raw bot connected: uuid={}", bot_uuid);

    // RAW onboard with the mock user header — succeeds as
    // a runtime onboarding, but stays authority-inert.
    let client = reqwest::Client::new();
    bind_bot_to_workbench_user(&client, &bcs_url, &bot_token, "Raw Onboard Bot")
        .await
        .expect("raw onboard succeeds over HTTP");
    println!("[Step 1] Raw onboard done (authority untouched)");

    // Drain the onboarding instruction.
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

    // The Bot (its own identity) can still create its
    // group.
    println!("\n[Step 2] Creating group as the raw bot...");
    let create_resp = client
        .post(&format!("{}/groups", bcs_url))
        .header("Authorization", format!("Bearer {}", bot_token))
        .json(&serde_json::json!({
            "driver_bot": bot_uuid,
            "participants": [
                { "bot_uuid": bot_uuid, "role": "driver" }
            ],
            "label": "Raw Onboard Group"
        }))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("group create request");
    assert!(
        create_resp.status().is_success(),
        "the bot-self group creation stays allowed: {}",
        create_resp.status()
    );
    let group_id = create_resp.json::<serde_json::Value>().await
        .expect("group create body")["id"]
        .as_str()
        .expect("group id")
        .to_string();
    println!("[Step 2] Group created: {}", group_id);

    // The workbench gate DENIES: no authority edge was
    // ever minted by the raw onboard.
    println!("\n[Step 3] Workbench subscribe (must be DENIED)...");
    let mut wb_client = WorkbenchClient::connect(bcs_port).await
        .expect("workbench client");
    let resp = wb_client.subscribe_group(&group_id).await.expect("subscribe response");
    assert!(
        !resp["ok"].as_bool().unwrap_or(true),
        "the raw-onboard bot must NOT be workbench-accessible: {resp}"
    );
    println!("[Step 3] DENIED as expected — v0 deny polarity pinned");

    println!("\n========== E2E Raw Onboarding Polarity Test PASSED ==========\n");
}

