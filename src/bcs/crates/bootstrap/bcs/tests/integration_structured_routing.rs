//! Integration tests for structured message routing (tasks 13.1–13.4).
//!
//! These tests use MockBot (WebSocket client) to verify the full routing pipeline:
//! bot sends chat.event(final) with routing metadata → BCS routes to targets
//! → targets receive chat.send, non-targets receive chat.inject.
//!
//! Note: When a group is created via POST /groups, BCS automatically injects
//! a [GROUP CONTEXT] message to all participants (chat.send to driver,
//! chat.inject to others). Tests must drain these frames before proceeding.

mod helpers;

use helpers::{
    create_temp_bots_dir, start_test_server, MockBot, frame_action, frame_ctx,
};
use serde_json::json;

/// Create a group with routing_policy via HTTP POST /groups.
async fn create_group_with_routing(
    addr: std::net::SocketAddr,
    token: &str,
    driver_bot: &str,
    participants: Vec<serde_json::Value>,
    routing_policy: Option<serde_json::Value>,
) -> String {
    let client = reqwest::Client::new();
    let mut body = json!({
        "driver_bot": driver_bot,
        "participants": participants,
    });
    if let Some(rp) = routing_policy {
        body["routing_policy"] = rp;
    }
    let resp = client
        .post(format!("http://{}/groups", addr))
        .header("Authorization", format!("Bearer {}", token))
        .json(&body)
        .send()
        .await
        .expect("Failed to create group");
    let json: serde_json::Value = resp.json().await.expect("Failed to parse group response");
    json["id"].as_str().expect("No group id in response").to_string()
}

/// Send a user message to the group via HTTP POST /groups/{id}/chat.
/// This triggers routing to all participants.
async fn send_group_message(
    addr: std::net::SocketAddr,
    token: &str,
    group_id: &str,
    message: &str,
    sender: &str,
) {
    let client = reqwest::Client::new();
    let body = json!({
        "message": message,
        "from": sender,
    });
    let resp = client
        .post(format!("http://{}/groups/{}/chat", addr, group_id))
        .header("Authorization", format!("Bearer {}", token))
        .json(&body)
        .send()
        .await
        .expect("Failed to send group message");
    assert!(resp.status().is_success(), "group_chat failed: {}", resp.status());
}

/// Drain all frames from group creation context injection + user message delivery.
/// Returns the driver's chat.send frame ID for the user message.
async fn setup_group_and_send_message(
    addr: std::net::SocketAddr,
    bot_a: &mut MockBot,
    bot_b: &mut MockBot,
    bot_c: &mut MockBot,
    group_id: &str,
    message: &str,
) -> String {
    // Group creation injects [GROUP CONTEXT] to all participants.
    let _ = bot_a.recv_frame().await; // context chat.send for driver
    let _ = bot_b.recv_frame().await; // context chat.inject
    let _ = bot_c.recv_frame().await; // context chat.inject

    // Send user message
    send_group_message(addr, &bot_a.token, group_id, message, &bot_a.bot_id).await;

    // Driver receives chat.send for the user message
    let driver_frame = bot_a.recv_frame().await.expect("Driver should receive chat.send");
    assert_eq!(frame_action(&driver_frame), "chat.send");
    let driver_req_id = driver_frame["id"].as_str().unwrap_or("").to_string();

    // Drain user message delivery to bot_b and bot_c
    let _ = bot_b.recv_frame().await; // user msg inject
    let _ = bot_c.recv_frame().await; // user msg inject

    driver_req_id
}

/// Sanity check: verify chat.event frame with routing can be deserialized
#[test]
fn test_chat_event_frame_deserialization() {
    let frame_json = json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "run_id": "test-run-1",
            "bcs_group_id": "test-group-1",
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}],
                "timestamp": 0
            },
            "routing": {
                "responders": [{"type": "name", "value": "DBA"}],
                "mode": "required",
                "reason": "test reason"
            }
        },
        "seq": 1
    });
    let frame_str = serde_json::to_string(&frame_json).unwrap();
    let frame: bcs_protocol::BcsFrame = serde_json::from_str(&frame_str)
        .expect("Should deserialize chat.event frame with routing");
    match frame {
        bcs_protocol::BcsFrame::Event(e) => {
            assert_eq!(e.event, "chat.event");
            let payload: bcs_protocol::ChatEventPayload =
                serde_json::from_value(e.payload.unwrap()).expect("Should parse ChatEventPayload");
            assert_eq!(payload.run_id, "test-run-1");
            assert_eq!(payload.state, bcs_protocol::ChatEventState::Final);
            assert!(payload.routing.is_some(), "routing should be present");
            let routing = payload.routing.unwrap();
            assert_eq!(routing.responders.len(), 1);
            assert_eq!(routing.responders[0].selector_type, "name");
        }
        _ => panic!("Expected Event frame"),
    }
}

/// 13.1: Bot final event with routing metadata → target gets chat.send, non-target gets chat.inject
#[tokio::test]
async fn test_structured_routing_send_vs_inject() {
    let tmp = create_temp_bots_dir();
    let (addr, _handle) = start_test_server(&tmp.path().to_path_buf()).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_b = MockBot::connect(addr).await;
    let mut bot_c = MockBot::connect(addr).await;

    bot_a.register("Coordinator", &["coordination"], addr).await;
    bot_b.register("DBA", &["database"], addr).await;
    bot_c.register("DevOps", &["deployment"], addr).await;

    let group_id = create_group_with_routing(
        addr,
        &bot_a.token,
        &bot_a.bot_id,
        vec![
            json!({"bot_uuid": bot_a.bot_id, "role": "driver"}),
            json!({"bot_uuid": bot_b.bot_id, "role": "consultant"}),
            json!({"bot_uuid": bot_c.bot_id, "role": "consultant"}),
        ],
        Some(json!({
            "mode": "hybrid",
            "default_bot_final_delivery": "send_to_driver"
        })),
    ).await;

    let driver_req_id = setup_group_and_send_message(
        addr, &mut bot_a, &mut bot_b, &mut bot_c, &group_id, "请 DBA 排查死锁问题",
    ).await;

    // Bot A responds with structured routing: route to bot_b (DBA) by name
    bot_a.send_chat_event_with_routing(
        &group_id,
        &driver_req_id,
        "这个死锁问题需要 DBA 来排查",
        json!({
            "responders": [{"type": "name", "value": "DBA"}],
            "mode": "required",
            "reason": "需要数据库专家排查死锁"
        }),
    ).await;

    // Bot B (DBA, targeted) should receive chat.send
    let bot_b_frame = bot_b.recv_frame().await.expect("DBA should receive a frame");
    assert_eq!(
        frame_action(&bot_b_frame), "chat.send",
        "Targeted bot should receive chat.send, got: {}",
        serde_json::to_string_pretty(&bot_b_frame).unwrap()
    );

    // Bot C (DevOps, not targeted) should receive chat.inject
    let bot_c_frame = bot_c.recv_frame().await.expect("DevOps should receive a frame");
    assert_eq!(
        frame_action(&bot_c_frame), "chat.inject",
        "Non-targeted bot should receive chat.inject, got: {}",
        serde_json::to_string_pretty(&bot_c_frame).unwrap()
    );
}

/// 13.2: Hybrid mode — legacy @mention fallback still works when no structured metadata
#[tokio::test]
async fn test_hybrid_mode_legacy_mention_fallback() {
    let tmp = create_temp_bots_dir();
    let (addr, _handle) = start_test_server(&tmp.path().to_path_buf()).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_b = MockBot::connect(addr).await;
    let mut bot_c = MockBot::connect(addr).await;

    bot_a.register("Coordinator", &["coordination"], addr).await;
    bot_b.register("DBA", &["database"], addr).await;
    bot_c.register("DevOps", &["deployment"], addr).await;

    let group_id = create_group_with_routing(
        addr,
        &bot_a.token,
        &bot_a.bot_id,
        vec![
            json!({"bot_uuid": bot_a.bot_id, "role": "driver"}),
            json!({"bot_uuid": bot_b.bot_id, "role": "consultant"}),
            json!({"bot_uuid": bot_c.bot_id, "role": "consultant"}),
        ],
        Some(json!({
            "mode": "hybrid",
            "default_bot_final_delivery": "send_to_driver"
        })),
    ).await;

    let driver_req_id = setup_group_and_send_message(
        addr, &mut bot_a, &mut bot_b, &mut bot_c, &group_id, "开始排查",
    ).await;

    // Bot A responds WITHOUT routing metadata, but with @DBA mention in text
    bot_a.send_chat_event(
        &group_id,
        &driver_req_id,
        "@DBA 请检查数据库锁表情况",
    ).await;

    // In hybrid mode without routing metadata, legacy @mention should kick in.
    // DBA (@mentioned) should receive chat.send
    let bot_b_frame = bot_b.recv_frame().await.expect("DBA should receive a frame");
    assert_eq!(
        frame_action(&bot_b_frame), "chat.send",
        "@mentioned bot should receive chat.send in hybrid mode, got: {}",
        serde_json::to_string_pretty(&bot_b_frame).unwrap()
    );

    // DevOps (not @mentioned) should receive chat.inject
    let bot_c_frame = bot_c.recv_frame().await.expect("DevOps should receive a frame");
    assert_eq!(
        frame_action(&bot_c_frame), "chat.inject",
        "Non-mentioned bot should receive chat.inject in hybrid mode, got: {}",
        serde_json::to_string_pretty(&bot_c_frame).unwrap()
    );
}

/// 13.3: response_directive correctly injected into GroupContext
#[tokio::test]
async fn test_response_directive_in_group_context() {
    let tmp = create_temp_bots_dir();
    let (addr, _handle) = start_test_server(&tmp.path().to_path_buf()).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_b = MockBot::connect(addr).await;
    let mut bot_c = MockBot::connect(addr).await;

    bot_a.register("Coordinator", &["coordination"], addr).await;
    bot_b.register("DBA", &["database"], addr).await;
    bot_c.register("DevOps", &["deployment"], addr).await;

    let group_id = create_group_with_routing(
        addr,
        &bot_a.token,
        &bot_a.bot_id,
        vec![
            json!({"bot_uuid": bot_a.bot_id, "role": "driver"}),
            json!({"bot_uuid": bot_b.bot_id, "role": "consultant"}),
            json!({"bot_uuid": bot_c.bot_id, "role": "consultant"}),
        ],
        Some(json!({
            "mode": "hybrid",
            "default_bot_final_delivery": "send_to_driver"
        })),
    ).await;

    let driver_req_id = setup_group_and_send_message(
        addr, &mut bot_a, &mut bot_b, &mut bot_c, &group_id, "处理问题",
    ).await;

    // Bot A responds with structured routing targeting DBA
    bot_a.send_chat_event_with_routing(
        &group_id,
        &driver_req_id,
        "需要 DBA 排查",
        json!({
            "responders": [{"type": "name", "value": "DBA"}],
            "mode": "required",
            "reason": "需要数据库专家"
        }),
    ).await;

    // Bot B (targeted) should receive chat.send with response_directive
    let bot_b_frame = bot_b.recv_frame().await.expect("DBA should receive a frame");
    assert_eq!(frame_action(&bot_b_frame), "chat.send");

    let ctx = frame_ctx(&bot_b_frame);
    assert!(!ctx.is_null(), "GroupContext should be present in the frame");

    let directive = &ctx["response_directive"];
    assert!(!directive.is_null(), "response_directive should be present in GroupContext");
    assert_eq!(
        directive["action"].as_str().unwrap_or(""),
        "respond",
        "Targeted bot should have action=respond"
    );
    assert_eq!(
        directive["request_source"].as_str().unwrap_or(""),
        "structured_metadata",
        "Should indicate structured_metadata as the routing source"
    );

    // Bot C (observer) should receive chat.inject with response_directive
    let bot_c_frame = bot_c.recv_frame().await.expect("DevOps should receive a frame");
    assert_eq!(frame_action(&bot_c_frame), "chat.inject");

    let ctx_c = frame_ctx(&bot_c_frame);
    let directive_c = &ctx_c["response_directive"];
    assert!(!directive_c.is_null(), "response_directive should be present for observer too");
    assert_eq!(
        directive_c["action"].as_str().unwrap_or(""),
        "observe",
        "Non-targeted bot should have action=observe"
    );
}

/// 13.4: Backward compatibility — you_are_mentioned and mentions legacy fields still work
#[tokio::test]
async fn test_backward_compat_you_are_mentioned() {
    let tmp = create_temp_bots_dir();
    let (addr, _handle) = start_test_server(&tmp.path().to_path_buf()).await;

    let mut bot_a = MockBot::connect(addr).await;
    let mut bot_b = MockBot::connect(addr).await;
    let mut bot_c = MockBot::connect(addr).await;

    bot_a.register("Coordinator", &["coordination"], addr).await;
    bot_b.register("DBA", &["database"], addr).await;
    bot_c.register("DevOps", &["deployment"], addr).await;

    let group_id = create_group_with_routing(
        addr,
        &bot_a.token,
        &bot_a.bot_id,
        vec![
            json!({"bot_uuid": bot_a.bot_id, "role": "driver"}),
            json!({"bot_uuid": bot_b.bot_id, "role": "consultant"}),
            json!({"bot_uuid": bot_c.bot_id, "role": "consultant"}),
        ],
        Some(json!({
            "mode": "hybrid",
            "default_bot_final_delivery": "send_to_driver"
        })),
    ).await;

    let driver_req_id = setup_group_and_send_message(
        addr, &mut bot_a, &mut bot_b, &mut bot_c, &group_id, "处理问题",
    ).await;

    // Bot A responds with structured routing targeting DBA
    bot_a.send_chat_event_with_routing(
        &group_id,
        &driver_req_id,
        "需要 DBA 排查死锁",
        json!({
            "responders": [{"type": "name", "value": "DBA"}],
            "mode": "required",
            "reason": "需要数据库专家"
        }),
    ).await;

    // Bot B (targeted) — legacy fields should be populated
    let bot_b_frame = bot_b.recv_frame().await.expect("DBA should receive a frame");
    assert_eq!(frame_action(&bot_b_frame), "chat.send");

    let ctx = frame_ctx(&bot_b_frame);
    // you_are_mentioned should be true for targeted bot (backward compat)
    assert_eq!(
        ctx["you_are_mentioned"].as_bool().unwrap_or(false),
        true,
        "Legacy you_are_mentioned should be true for targeted bot"
    );

    // Bot C (observer) — legacy fields should indicate not mentioned
    let bot_c_frame = bot_c.recv_frame().await.expect("DevOps should receive a frame");
    assert_eq!(frame_action(&bot_c_frame), "chat.inject");

    let ctx_c = frame_ctx(&bot_c_frame);
    // you_are_mentioned should be false for non-targeted bot
    assert_eq!(
        ctx_c["you_are_mentioned"].as_bool().unwrap_or(true),
        false,
        "Legacy you_are_mentioned should be false for non-targeted bot"
    );

    // response_directive should ALSO be present (new field alongside legacy)
    assert!(
        !ctx["response_directive"].is_null(),
        "New response_directive should coexist with legacy you_are_mentioned"
    );
    assert!(
        !ctx_c["response_directive"].is_null(),
        "New response_directive should coexist with legacy you_are_mentioned for observer"
    );
}
