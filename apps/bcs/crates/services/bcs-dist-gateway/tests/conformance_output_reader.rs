#![allow(clippy::unwrap_used, reason = "test assertions")]

#[path = "../../bcs-dist-gateway-store/tests/common/mod.rs"]
mod common;

use bcs_dist_model::*;
use bcs_dist_worker::WorkerOutputReader;
use bcs_service_api::port::dist_output::*;
use bcs_test_support::contract::distributed::output::dist_task_output_reader_port_contract_tests;
use common::*;
use serde_json::{Value, json};
use std::sync::Arc;

/// A second format proves the facade invokes its injected formatter for both
/// snapshots and deltas instead of depending on A2A or bypassing the port.
struct JsonOutput;
impl DistOutputAdapterPort for JsonOutput {
    fn snapshot(&self, value: &Value) -> Result<Value, OutputAdapterError> {
        Ok(json!({ "full": value }))
    }
    fn artifact(&self, change: &ArtifactChange) -> Result<Value, OutputAdapterError> {
        Ok(json!({ "change": change }))
    }
}

#[tokio::test]
async fn conformance_worker_output_reader_uses_the_injected_format() {
    let p = ports();
    let task = snapshot(TID, TaskStatus::Working, None, NOW);
    p.worker.create(&task).await;
    p.worker.append(&text_event(TID, eid(1, 1), "hello")).await;
    let reader = WorkerOutputReader::new(p.tasks.clone(), p.blobs.clone(), Arc::new(JsonOutput));
    let mut content = task.content;
    content.snapshot = Payload::Inline(json!({ "full": { "format_version": 1, "units": [] } }));
    dist_task_output_reader_port_contract_tests(&reader, &TaskView {
        scope: scope(), sid: Sid(SID.into()), tid: Tid(TID.into()), content, covered_through: None,
    }, &FormattedOutputEvent {
        id: eid(1, 1), tid: Tid(TID.into()), body: FormattedOutputBody::Artifact {
            content: json!({ "change": {
                "node": "n1", "artifact_id": "a-1", "class": "output",
                "update": { "op": "upsert", "content": { "kind": "inline", "value": "hello" } }
            } }),
        },
    }).await.unwrap();
}
