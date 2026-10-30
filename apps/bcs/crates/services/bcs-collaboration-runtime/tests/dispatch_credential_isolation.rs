//! Credential-isolation canary for StateMachineDispatchPayload.request (spec
//! §8.4/L244): the persisted request carries the already-rendered chat.send
//! frame the runtime actually re-sends on recovery — credentials must never
//! enter it; they are resolved per delivery via the bot registry. This test
//! builds the real frame through the same builder the dispatch path uses and
//! asserts the serialized form contains no credential-suspect keys.

use bcs_protocol::{BcsFrame, GroupContextInput, build_chat_send_frame};
use serde_json::Value;

fn frame() -> BcsFrame {
    let group = GroupContextInput {
        session_id: "session-1".to_string(),
        driver_bot: "bot-mgr".to_string(),
        originator: "state_machine".to_string(),
        participants: Vec::new(),
        bcs_session_id: Some("session-1".to_string()),
    };
    build_chat_send_frame(
        "smnode-run-1-node-a-0",
        "session-1",
        &group,
        "node prompt",
        "state_machine",
        "StateMachine",
        &[],
        "bot-w",
        &["state_machine".to_string()],
        &None,
        &None,
        false,
        3,
        None,
        Some("state_machine".to_string()),
        Some("session-1"),
    )
}

/// Keys that must never appear anywhere in a persisted dispatch request.
/// Recursively descended: a nested credential channel is still a leak.
const FORBIDDEN_KEYS: [&str; 8] = [
    "token", "credential", "caller_context", "caller_identity", "authorization",
    "opaque", "jwt", "secret",
];

fn assert_no_forbidden_keys(value: &Value, path: &str, violations: &mut Vec<String>) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                let key_lower = key.to_lowercase();
                if FORBIDDEN_KEYS.iter().any(|suspect| key_lower.contains(suspect)) {
                    violations.push(format!("{path}.{key}"));
                }
                assert_no_forbidden_keys(child, &format!("{path}.{key}"), violations);
            }
        }
        Value::Array(items) => {
            for (index, child) in items.iter().enumerate() {
                assert_no_forbidden_keys(child, &format!("{path}[{index}]"), violations);
            }
        }
        _ => {}
    }
}

#[test]
fn the_persisted_dispatch_request_shape_carries_no_credential_material() {
    let mut frame = frame();
    // The runtime adds the node timeout to the built frame before persisting.
    if let BcsFrame::Request(request) = &mut frame {
        if let Some(params) = request.params.as_mut().and_then(Value::as_object_mut) {
            params.insert("timeout_ms".to_string(), Value::from(60_000u64));
        }
    }
    let serialized = serde_json::to_value(&frame).expect("serializable request");
    let mut violations = Vec::new();
    assert_no_forbidden_keys(&serialized, "$", &mut violations);
    assert!(
        violations.is_empty(),
        "StateMachineDispatchPayload.request was serialized with credential-suspect keys: {violations:?}"
    );
    // Identity forwarding is at most a boolean channel opt-in flag — never
    // principal material.
    if let Value::Object(map) = &serialized {
        if let Some(Value::Object(params)) = map.get("params").cloned().map(|v| v) {
            if let Some(channel) = params.get("channel") {
                if let Some(forwarding) = channel.get("identity_forwarding") {
                    assert!(
                        forwarding.is_boolean() || forwarding.is_null(),
                        "identity_forwarding must stay a boolean opt-in flag, got: {forwarding}"
                    );
                }
            }
        }
    }
}