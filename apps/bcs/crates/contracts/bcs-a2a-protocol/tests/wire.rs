//! Golden wire tests: v1 enum spelling, oneof members and spec examples.
use bcs_a2a_protocol::*;
use serde_json::{Value, json};

fn roundtrip<T: serde::Serialize + serde::de::DeserializeOwned>(value: Value) -> Value {
    let decoded: T = serde_json::from_value(value).expect("decode");
    serde_json::to_value(decoded).expect("encode")
}

#[test]
fn spec_sample_agent_card_roundtrips_losslessly() {
    let text = include_str!("fixtures/spec_agent_card.json");
    let value: Value = serde_json::from_str(text).unwrap();
    assert_eq!(roundtrip::<AgentCard>(value.clone()), value);
    let card: AgentCard = serde_json::from_value(value).unwrap();
    assert_eq!(card.jsonrpc_interface().unwrap().url, "https://georoute-agent.example.com/a2a/v1");
    assert_eq!(card.capabilities.streaming, Some(true));
}

#[test]
fn message_uses_v1_role_names_and_member_based_parts() {
    let wire = json!({
        "messageId": "m-1", "contextId": "c-1", "role": "ROLE_USER",
        "parts": [
            {"text": "hello"},
            {"url": "https://x/log.txt", "filename": "log.txt", "mediaType": "text/plain"},
            {"data": {"decision": "allow-once"}},
            {"raw": "aGk=", "mediaType": "application/octet-stream"}
        ],
        "metadata": {"k": 1}
    });
    assert_eq!(roundtrip::<Message>(wire.clone()), wire);
    let message: Message = serde_json::from_value(wire).unwrap();
    assert_eq!(message.role, Role::User);
    assert_eq!(message.text(), "hello");
    assert_eq!(message.data(), Some(&json!({"decision": "allow-once"})));
}

#[test]
fn legacy_v03_shapes_are_rejected() {
    // 0.3 used lowercase roles and a `kind` discriminator.
    assert!(serde_json::from_value::<Message>(json!({"messageId":"m","role":"user","parts":[{"text":"x"}]})).is_err());
    assert!(serde_json::from_value::<Part>(json!({"kind":"text"})).is_err());
    assert!(serde_json::from_value::<TaskStatus>(json!({"state":"completed"})).is_err());
}

#[test]
fn stream_response_is_a_single_member_oneof() {
    let wire = json!({"statusUpdate": {"taskId": "task-uuid", "contextId": "context-uuid", "status": {"state": "TASK_STATE_COMPLETED"}}});
    assert_eq!(roundtrip::<StreamResponse>(wire.clone()), wire);
    let artifact = json!({"artifactUpdate": {"taskId": "t", "contextId": "c",
        "artifact": {"artifactId": "response", "parts": [{"text": "a"}]}, "append": true, "lastChunk": true,
        "metadata": {"seq": 3}}});
    assert_eq!(roundtrip::<StreamResponse>(artifact.clone()), artifact);
    let task = json!({"task": {"id": "t", "contextId": "c", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
        "message": {"messageId": "s", "role": "ROLE_AGENT", "parts": [{"text": "approve?"}]}}}});
    let decoded: StreamResponse = serde_json::from_value(task.clone()).unwrap();
    assert!(matches!(&decoded, StreamResponse::Task(t) if t.status.state == TaskState::InputRequired));
    assert_eq!(serde_json::to_value(decoded).unwrap(), task);
}

#[test]
fn send_message_request_and_response_shapes() {
    let request = json!({
        "message": {"messageId": "run-1", "role": "ROLE_USER", "parts": [{"text": "hi"}]},
        "configuration": {"returnImmediately": true, "historyLength": 0,
            "taskPushNotificationConfig": {"url": "https://bcs/a2a/push/run-1",
                "authentication": {"scheme": "Bearer", "credentials": "tok"}}}
    });
    assert_eq!(roundtrip::<SendMessageRequest>(request.clone()), request);
    let decoded: SendMessageRequest = serde_json::from_value(request).unwrap();
    let push = decoded.configuration.unwrap().task_push_notification_config.unwrap();
    assert_eq!(push.authorization().as_deref(), Some("Bearer tok"));

    let response = json!({"message": {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "done"}]}});
    assert!(matches!(serde_json::from_value::<SendMessageResponse>(response).unwrap(), SendMessageResponse::Message(_)));
}

#[test]
fn task_states_classify_terminal_and_interrupted() {
    for state in [TaskState::Completed, TaskState::Failed, TaskState::Canceled, TaskState::Rejected] {
        assert!(state.is_terminal());
    }
    for state in [TaskState::Submitted, TaskState::Working, TaskState::InputRequired, TaskState::AuthRequired] {
        assert!(!state.is_terminal());
    }
    assert!(TaskState::InputRequired.is_interrupted());
    assert_eq!(serde_json::to_value(TaskState::InputRequired).unwrap(), json!("TASK_STATE_INPUT_REQUIRED"));
}

#[test]
fn jsonrpc_errors_carry_error_info_reason() {
    let error = JsonRpcError::new(A2aErrorCode::TaskNotFound, "Task not found");
    let response = JsonRpcResponse::failure(json!(2), error);
    let wire = serde_json::to_value(&response).unwrap();
    assert_eq!(wire["error"]["code"], -32001);
    assert_eq!(wire["error"]["data"][0]["reason"], "TASK_NOT_FOUND");
    assert_eq!(wire["error"]["data"][0]["@type"], "type.googleapis.com/google.rpc.ErrorInfo");
    assert!(wire.get("result").is_none());
    let decoded: JsonRpcResponse = serde_json::from_value(wire).unwrap();
    let error = decoded.into_result::<Task>().unwrap_err();
    assert_eq!(error.kind(), Some(A2aErrorCode::TaskNotFound));
    // Generic JSON-RPC errors carry no A2A reason.
    assert!(JsonRpcError::new(A2aErrorCode::InvalidParams, "bad").data.is_none());
}

#[test]
fn jsonrpc_request_envelope() {
    let request = JsonRpcRequest::new("1", methods::GET_TASK, GetTaskRequest { tenant: None, id: "t".into(), history_length: None }).unwrap();
    assert_eq!(serde_json::to_value(&request).unwrap(),
        json!({"jsonrpc": "2.0", "id": "1", "method": "GetTask", "params": {"id": "t"}}));
}
