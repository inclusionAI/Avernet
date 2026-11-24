//! Candidate round wire format only; these tests do not certify D0 or a provider.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "contract assertions")]
use bcs_dist_model::{Admission, RoundInput, TerminalProof};
use serde_json::{Value, json};

fn input() -> Value {
    json!({
        "schema_version": 1,
        "source": "group_a2a",
        "key": {
            "scope": {"tenant": "tenant-a", "principal": "caller-a"},
            "group": "group-a", "sid": "s0042.a", "tid": "task-a", "generation": 1
        },
        "ordered_parts": [
            {"kind": "text", "text": "first"},
            {"kind": "data", "data": {"answer": 42}}
        ],
        "workflow_ref": {"definition_id": "flow", "revision": "v1", "digest": "flow-digest"},
        "history_cut": {"key": "history-cut-a", "digest": "history-digest"},
        "execution_deadline": 123456
    })
}

#[test]
fn frozen_identity_survives_reload() {
    let original: RoundInput = serde_json::from_value(input()).unwrap();
    let reloaded: RoundInput = serde_json::from_slice(&serde_json::to_vec(&original).unwrap()).unwrap();
    assert_eq!(reloaded, original);
    assert_eq!(serde_json::to_value(reloaded).unwrap(), input());
}

#[test]
fn old_shape_cannot_guess_owner() {
    for field in ["scope", "generation"] {
        let mut old = input();
        old["key"].as_object_mut().unwrap().remove(field);
        assert!(serde_json::from_value::<RoundInput>(old).is_err(), "missing {field}");
    }
    let mut missing_source = input();
    missing_source.as_object_mut().unwrap().remove("source");
    assert!(serde_json::from_value::<RoundInput>(missing_source).is_err());
}

#[test]
fn unsupported_version_source_and_zero_generation_are_rejected() {
    for (field, value) in [("schema_version", json!(2)), ("source", json!("json_rpc"))] {
        let mut invalid = input();
        invalid[field] = value;
        assert!(serde_json::from_value::<RoundInput>(invalid).is_err());
    }
    let mut zero = input();
    zero["key"]["generation"] = json!(0);
    assert!(serde_json::from_value::<RoundInput>(zero).is_err());
}

#[test]
fn replay_keeps_original_identity_and_return_configuration() {
    let wire = json!({"kind": "replay", "accepted": {
        "input": input(),
        "request_id": "message-a",
        "request_digest": "original-normalized-digest",
        "normalization_ref": {"key": "normalization-a", "digest": "normalization-digest"},
        "return_configuration": {"output_mode": "text", "include_progress": false}
    }});
    let admission: Admission = serde_json::from_value(wire.clone()).unwrap();
    assert_eq!(serde_json::to_value(admission).unwrap(), wire);
    assert!(serde_json::from_value::<Admission>(json!({"kind": "new"})).is_err());
}

#[test]
fn terminal_record_requires_round_commit_and_stop_evidence() {
    let wire = json!({
        "schema_version": 1, "key": input()["key"],
        "commit_id": "terminal-a", "commit_seq": 2,
        "status": "completed",
        "result_ref": {"key": "result-a", "digest": "result-digest"},
        "closure_ref": {"key": "all-attempts-a", "digest": "closure-digest"}
    });
    let proof: TerminalProof = serde_json::from_value(wire.clone()).unwrap();
    assert_eq!(serde_json::to_value(proof).unwrap(), wire);
    for field in ["key", "commit_id", "commit_seq", "closure_ref", "result_ref"] {
        let mut invalid = wire.clone();
        invalid.as_object_mut().unwrap().remove(field);
        assert!(serde_json::from_value::<TerminalProof>(invalid).is_err(), "missing {field}");
    }
}
