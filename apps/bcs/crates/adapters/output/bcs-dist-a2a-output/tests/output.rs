#![allow(clippy::unwrap_used, reason = "test assertions")]

use bcs_dist_a2a_output::A2aOutputAdapter;
use bcs_dist_model::*;
use bcs_service_api::port::dist_output::DistOutputAdapterPort;
use serde_json::{Value, json};

fn change(class: OutputClass, update: ArtifactUpdate) -> ArtifactChange {
    ArtifactChange {
        node: NodeId("n1".into()),
        artifact_id: ArtifactId("answer".into()),
        class,
        update,
    }
}

fn snapshot(content: Value) -> Value {
    json!({ "format_version": 1, "units": [
        { "node": "n1", "artifact_id": "answer", "class": "output", "content": content }
    ] })
}

#[test]
fn full_view_and_upsert_use_the_same_stable_artifact_identity() {
    let adapter = A2aOutputAdapter;
    let full = adapter.snapshot(&snapshot(json!("hello"))).unwrap();
    let upsert = adapter.artifact(&change(OutputClass::Output, ArtifactUpdate::Upsert {
        content: Payload::Inline(json!("hello")),
    })).unwrap();
    assert_eq!(full["artifacts"][0], upsert["artifact"]);
    assert_eq!(upsert["artifact"]["parts"], json!([{ "text": "hello" }]));
    assert_eq!(upsert["append"], false);
    assert!(full.get("history").is_none());
    assert!(full.get("status_message").is_none());
}

#[test]
fn append_text_is_a_delta_for_the_same_artifact() {
    let adapter = A2aOutputAdapter;
    let full = adapter.snapshot(&snapshot(json!("hello world"))).unwrap();
    let append = adapter.artifact(&change(OutputClass::Output, ArtifactUpdate::AppendText {
        delta: Payload::Inline(json!(" world")),
    })).unwrap();
    assert_eq!(append["artifact"]["artifactId"], full["artifacts"][0]["artifactId"]);
    assert_eq!(append["artifact"]["parts"], json!([{ "text": " world" }]));
    assert_eq!(append["append"], true);
    assert!(append.get("last_chunk").is_none());
}

#[test]
fn json_content_and_explicit_message_sources_are_preserved() {
    let adapter = A2aOutputAdapter;
    for value in [json!({ "answer": [1, true, null] }), json!(["x", 1]), json!(42), Value::Null] {
        let full = adapter.snapshot(&snapshot(value.clone())).unwrap();
        assert_eq!(full["artifacts"][0]["parts"], json!([{ "data": value }]));
    }
    let mut input = snapshot(json!("ok"));
    input["history"] = json!([{ "messageId": "m1", "role": "ROLE_AGENT", "parts": [{ "text": "prior" }] }]);
    input["status_message"] = json!({ "messageId": "m2", "role": "ROLE_AGENT", "parts": [{ "text": "status" }] });
    let full = adapter.snapshot(&input).unwrap();
    assert_eq!(full["history"], input["history"]);
    assert_eq!(full["status_message"], input["status_message"]);
}

#[test]
fn node_and_class_are_part_of_collision_free_artifact_identity() {
    let adapter = A2aOutputAdapter;
    let first = change(OutputClass::Output, ArtifactUpdate::Upsert { content: Payload::Inline(json!("x")) });
    let base = adapter.artifact(&first).unwrap();
    let mut other = first.clone();
    other.node = NodeId("n2".into());
    assert_ne!(base["artifact"]["artifactId"], adapter.artifact(&other).unwrap()["artifact"]["artifactId"]);
    other.node = first.node;
    other.class = OutputClass::Tool;
    let tool = adapter.artifact(&other).unwrap();
    assert_ne!(base["artifact"]["artifactId"], tool["artifact"]["artifactId"]);
    assert_eq!(tool["artifact"]["metadata"], json!({ "node": "n1", "class": "tool" }));
}

#[test]
fn strict_answer_keeps_one_artifact_with_ordered_typed_parts() {
    let adapter = A2aOutputAdapter;
    let content = json!([
        { "kind": "text", "text": "line 1\n\"quoted\"" },
        { "kind": "data", "data": "a data string" },
        { "kind": "data", "data": {"answer": [1, true, null]} },
    ]);
    let mut input = snapshot(content.clone());
    input["units"][0]["class"] = json!("final_answer");
    let full = adapter.snapshot(&input).unwrap();
    let upsert = adapter.artifact(&change(OutputClass::FinalAnswer, ArtifactUpdate::Upsert {
        content: Payload::Inline(content),
    })).unwrap();
    assert_eq!(full["artifacts"], json!([upsert["artifact"]]));
    assert_eq!(upsert["artifact"]["parts"], json!([
        { "text": "line 1\n\"quoted\"" },
        { "data": "a data string" },
        { "data": {"answer": [1, true, null]} },
    ]));
    assert_eq!(upsert["append"], false);
}

#[test]
fn invalid_neutral_content_is_rejected_instead_of_dropped() {
    let adapter = A2aOutputAdapter;
    let mut unknown = snapshot(json!("x"));
    unknown["format_version"] = json!(2);
    assert!(adapter.snapshot(&unknown).is_err());
    assert!(adapter.artifact(&change(OutputClass::Output, ArtifactUpdate::AppendText {
        delta: Payload::Inline(json!({ "invalid": "delta" })),
    })).is_err());
    assert!(adapter.artifact(&change(OutputClass::Output, ArtifactUpdate::Upsert {
        content: Payload::Ref(BlobRef { key: "x".into(), digest: Digest("sha256:x".into()) }),
    })).is_err());
}
