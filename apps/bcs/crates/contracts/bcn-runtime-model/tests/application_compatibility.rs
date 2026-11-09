use bcn_runtime_model::*;
use serde_json::json;
#[test]
fn legacy_message_and_submit_decode_without_new_optional_context_fields() {
    let response: AgentSendResponse =
        serde_json::from_value(json!({"kind":"message","parts":[]})).unwrap();
    assert_eq!(
        response,
        AgentSendResponse::Message {
            parts: vec![],
            handle: None
        }
    );
    let scope = Scope::new("test", "tenant", "new");
    let request:SubmitRequest=serde_json::from_value(json!({"target":{"agent":{"agent_id":{"scope":scope,"value":"a"}}},"message":{"message_id":{"scope":scope,"value":"m"},"context_id":null,"task_id":null,"parts":[]}})).unwrap();
    assert!(request.interaction_id.is_none());
    assert!(
        serde_json::to_value(request)
            .unwrap()
            .get("interaction_id")
            .is_none()
    );
}
#[test]
fn legacy_failed_event_remains_distinct_from_partial_output_failure() {
    let original = AgentEvent::Failed {
        error: RuntimeError::ExecutionUnknown,
    };
    let wire = serde_json::to_value(&original).unwrap();
    assert_eq!(wire, json!({"kind":"failed","error":"execution_unknown"}));
    assert_eq!(
        serde_json::from_value::<AgentEvent>(wire).unwrap(),
        original
    );
    let event = AgentEvent::FailedWithOutput {
        error: RuntimeError::ExecutionUnknown,
        output: vec![MessagePart::Text {
            text: "partial".into(),
        }],
    };
    assert_eq!(
        serde_json::from_value::<AgentEvent>(serde_json::to_value(&event).unwrap()).unwrap(),
        event
    );
}
#[test]
fn old_timer_input_defaults_partial_output_to_empty() {
    let input:SessionInputKind=serde_json::from_value(json!({"kind":"timer_fired","timer_id":{"scope":{"environment":"test","tenant":"tenant","namespace":"new"},"value":"timer"}})).unwrap();
    assert!(matches!(input,SessionInputKind::TimerFired{output,..} if output.is_empty()));
}
