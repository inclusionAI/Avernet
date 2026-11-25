use a2a::{StreamResponse, TaskState};
use bcs_a2a_http::projection::{Output, Projection, ProjectionError};
use serde_json::{json, Value};

fn event(value: Value) -> StreamResponse { serde_json::from_value(value).unwrap() }
fn initial() -> StreamResponse {
    event(json!({"task":{"id":"remote-task","contextId":"remote-context",
        "status":{"state":"TASK_STATE_SUBMITTED"}}}))
}
fn chunk(text: &str, append: bool, last: bool) -> StreamResponse {
    event(json!({"artifactUpdate":{"taskId":"remote-task","contextId":"remote-context",
        "artifact":{"artifactId":"text","parts":[{"text":text}]},"append":append,"lastChunk":last}}))
}
fn state(state: &str) -> StreamResponse {
    event(json!({"statusUpdate":{"taskId":"remote-task","contextId":"remote-context",
        "status":{"state":state}}}))
}

#[test]
fn initial_identity_is_required_and_never_replaced_by_remote_updates() {
    let projection = Projection::default();
    assert_eq!(projection.stage(chunk("early", false, false)).unwrap_err(), ProjectionError::InitialTaskRequired);
    let (projection, output) = projection.stage(initial()).unwrap();
    assert!(matches!(&output[0], Output::Identity { task_id, .. } if task_id == "remote-task"));
    let wrong = event(json!({"statusUpdate":{"taskId":"other","contextId":"remote-context",
        "status":{"state":"TASK_STATE_COMPLETED"}}}));
    assert_eq!(projection.stage(wrong).unwrap_err(), ProjectionError::IdentityMismatch);
}

#[test]
fn failed_ingest_does_not_advance_cursor_and_last_chunk_does_not_complete_task() {
    let (projection, _) = Projection::default().stage(initial()).unwrap();
    let (_, first) = projection.stage(chunk("你好", false, false)).unwrap();
    // Simulate ingestion failure: do not commit the returned projection.
    let (projection, retried) = projection.stage(chunk("你好", false, false)).unwrap();
    assert_eq!(first, retried);
    let (projection, final_chunk) = projection.stage(chunk("你好世界", false, true)).unwrap();
    assert_eq!(final_chunk, vec![Output::Delta("世界".into())]);
    let (projection, completed) = projection.stage(state("TASK_STATE_COMPLETED")).unwrap();
    assert_eq!(completed, vec![Output::Status(TaskState::Completed)]);
    assert!(projection.stage(state("TASK_STATE_COMPLETED")).unwrap().1.is_empty());
    assert_eq!(projection.stage(chunk("late", true, false)).unwrap_err(), ProjectionError::EventAfterTerminal);
}

#[test]
fn unsupported_non_text_is_rejected_without_losing_the_text_baseline() {
    let (projection, _) = Projection::default().stage(initial()).unwrap();
    let invalid = event(json!({"artifactUpdate":{"taskId":"remote-task","contextId":"remote-context",
        "artifact":{"artifactId":"text","parts":[{"data":{"secret":false}}]}}}));
    assert_eq!(projection.stage(invalid).unwrap_err(), ProjectionError::UnsupportedContent);
    assert_eq!(projection.stage(chunk("safe", false, true)).unwrap().1, vec![Output::Delta("safe".into())]);
}
