use bcn_runtime_model::*;
use serde_json::json;
#[test]
fn legacy_message_and_submit_decode_without_new_optional_context_fields() {
    let response: AgentSendResponse =
        serde_json::from_value(json!({"kind":"message","parts":[]})).unwrap();
    assert_eq!(
        response,
        AgentSendResponse::Message { observation_error: None,
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

#[test]
fn local_observation_failure_has_distinct_wire_identity() {
    let event = AgentEvent::ObservationFailed { error: RuntimeError::CapacityExceeded, output: vec![] };
    let value = serde_json::to_value(&event).unwrap();
    assert_eq!(value["kind"], "observation_failed");
    assert_eq!(serde_json::from_value::<AgentEvent>(value).unwrap(), event);
}

#[test]
fn snapshot_observation_error_roundtrips_separately_from_remote_error() {
    let scope=Scope::new("test","tenant","runtime");
    let old=json!({"handle":{"target":{"scope":scope,"value":"agent"},"binding_version":"v1","remote_task_id":"task","remote_context_id":"context"},"status":"working","output":[],"error":null});
    let decoded:ExecutionSnapshot=serde_json::from_value(old.clone()).unwrap();
    assert_eq!(serde_json::to_value(decoded).unwrap(),old);
    let mut with_error=old;with_error["observation_error"]=json!("execution_unknown");
    let decoded:ExecutionSnapshot=serde_json::from_value(with_error.clone()).unwrap();
    assert_eq!(serde_json::to_value(decoded).unwrap(),with_error);
}

#[test]
fn direct_message_observation_error_survives_wire_without_inventing_task() {
    let value=json!({"kind":"message","parts":[],"observation_error":"execution_unknown"});
    let message:AgentSendResponse=serde_json::from_value(value.clone()).unwrap();
    assert_eq!(serde_json::to_value(message).unwrap(),value);
}

#[test]
fn snapshot_input_prompt_is_optional_and_does_not_replace_artifacts() {
    let scope=Scope::new("test","tenant","runtime");
    let old=json!({"handle":{"target":{"scope":scope,"value":"agent"},"binding_version":"v1","remote_task_id":"task","remote_context_id":"context"},"status":"input_required","output":[{"kind":"text","text":"partial"}],"error":null});
    let decoded:ExecutionSnapshot=serde_json::from_value(old.clone()).unwrap();
    assert_eq!(serde_json::to_value(decoded).unwrap(),old);
    for prompt in [json!([]),json!([{"kind":"text","text":"question?"}])] {
        let mut value=old.clone();value["input_prompt"]=prompt;
        let decoded:ExecutionSnapshot=serde_json::from_value(value.clone()).unwrap();
        assert_eq!(serde_json::to_value(decoded).unwrap(),value);
    }
}
