use bcn_runtime_model::*;
use serde_json::json;
#[test]
fn agent_effects_preserve_frozen_correlation_without_current_session_state() {
    let scope = json!({"environment":"test","tenant":"a","namespace":"new"});
    let task = json!({"scope":scope,"value":"task"});
    let operation = json!({"scope":scope,"value":"cancel-operation"});
    let dispatch = json!({"scope":scope,"value":"cancel-dispatch"});
    let binding = json!({"target":{"scope":scope,"value":"agent"},"binding_version":"v1","binding_id":"catalog","credential_ref":null,"capabilities":{"streaming":false,"query":true,"subscribe":true,"input_continuation":true,"cancellation":"single_task","idempotency":{"kind":"unsupported"}}});
    let handle = json!({"target":binding["target"],"binding_version":"v1","remote_task_id":"remote","remote_context_id":"context"});
    let cancel = json!({"kind":"cancel_agent","task_id":task,"node_id":"node","dispatch_id":dispatch,"binding":binding,"request":{"operation_id":operation,"handle":handle,"exclusive_context":false}});
    let send = json!({"kind":"send_agent","task_id":task,"node_id":"node","request":{"dispatch_id":{"scope":scope,"value":"send"},"operation_id":{"scope":scope,"value":"send-op"},"binding":binding,"message":{"message_id":{"scope":scope,"value":"message"},"task_id":null,"context_id":null,"parts":[]},"handle":null,"expires_at_ms":null}});
    for value in [cancel, send] {
        let effect: EffectIntent = serde_json::from_value(value.clone()).unwrap();
        assert_eq!(serde_json::to_value(effect).unwrap(), value);
    }
}

#[test]
fn obsolete_request_only_agent_outbox_is_rejected_instead_of_guessing_correlation() {
    // The pre-release request-only shape cannot identify its task after a switch.
    for kind in ["send_agent", "cancel_agent"] {
        assert!(serde_json::from_value::<EffectIntent>(json!({"kind":kind,"request":{}})).is_err());
    }
}

#[test]
fn queued_task_without_terminal_intent_remains_checkpoint_compatible() {
    let scope = json!({"environment":"test","tenant":"a","namespace":"new"});
    let value = json!({"task_id":{"scope":scope,"value":"task"},"message":{"message_id":{"scope":scope,"value":"message"},"task_id":null,"context_id":null,"parts":[]},"admitted_at_ms":0});
    let task: QueuedTask = serde_json::from_value(value).unwrap();
    let encoded = serde_json::to_value(task).unwrap();
    assert!(encoded.as_object().unwrap().contains_key("terminal_intent"));
    assert_eq!(encoded["terminal_intent"], serde_json::Value::Null);
}
