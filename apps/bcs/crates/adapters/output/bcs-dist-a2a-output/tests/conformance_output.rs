#![allow(clippy::unwrap_used, reason = "test assertions")]

use bcs_dist_a2a_output::A2aOutputAdapter;
use bcs_dist_model::*;
use bcs_test_support::contract::distributed::output::dist_output_adapter_port_contract_tests;
use serde_json::json;

#[test]
fn conformance_a2a_output_adapter() {
    let snapshot = json!({ "format_version": 1, "units": [
        { "node": "n1", "artifact_id": "answer", "class": "output", "content": "hello" }
    ] });
    let artifact = json!({
        "artifactId": "dist:output:2:n1:answer",
        "parts": [{ "text": "hello" }],
        "metadata": { "node": "n1", "class": "output" },
    });
    dist_output_adapter_port_contract_tests(
        &A2aOutputAdapter,
        &snapshot,
        &ArtifactChange {
            node: NodeId("n1".into()), artifact_id: ArtifactId("answer".into()), class: OutputClass::Output,
            update: ArtifactUpdate::Upsert { content: Payload::Inline(json!("hello")) },
        },
        &json!({ "artifacts": [artifact.clone()] }),
        &json!({ "artifact": artifact, "append": false, "metadata": { "node": "n1", "class": "output" } }),
    ).unwrap();
}
