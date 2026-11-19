//! Consumer assertions for pure output formatting and the Worker read facade.
use super::ContractResult;
use bcs_dist_model::*;
use bcs_service_api::port::dist_output::*;
use bcs_service_api::port::repo::distributed::common::PageRequest;
use bcs_service_api::port::repo::distributed::task_feed::{RestoredTask, ResumeCheck, TaskFilter};
use futures::StreamExt;
use serde_json::Value;
use std::num::NonZeroU32;

/// Expectations are supplied by each protocol's conformance fixture, because
/// the port deliberately does not prescribe a particular protocol JSON shape.
pub fn dist_output_adapter_port_contract_tests(
    port: &dyn DistOutputAdapterPort,
    snapshot: &Value,
    change: &ArtifactChange,
    expected_snapshot: &Value,
    expected_artifact: &Value,
) -> ContractResult {
    let original_snapshot = snapshot.clone();
    let original_change = change.clone();
    for _ in 0..2 {
        assert_eq!(&port.snapshot(snapshot)?, expected_snapshot);
        assert_eq!(&port.artifact(change)?, expected_artifact);
    }
    assert_eq!(snapshot, &original_snapshot);
    assert_eq!(change, &original_change);
    let mut unresolved = change.clone();
    unresolved.update = ArtifactUpdate::Upsert {
        content: Payload::Ref(BlobRef { key: "unresolved".into(), digest: Digest("sha256:unresolved".into()) }),
    };
    assert!(port.artifact(&unresolved).is_err(), "the formatter must not hide unresolved storage reads");
    Ok(())
}

/// A live fixture with one event after its published cut and one indexed task.
pub async fn dist_task_output_reader_port_contract_tests(
    port: &dyn DistTaskOutputReaderPort,
    expected: &TaskView,
    expected_event: &FormattedOutputEvent,
) -> ContractResult {
    let Some(RestoredTask::Live { view, cursor }) = port.restore(&expected.scope, &expected.tid).await? else {
        panic!("expected live formatted fixture");
    };
    assert_eq!(&view, expected);
    assert_eq!(port.validate_resume(&expected.scope, &cursor).await?, ResumeCheck::Valid);
    let mut follow = port.follow(expected.scope.clone(), cursor);
    let Some(Ok(FormattedFollowItem::Events { events, next })) = follow.next().await else {
        panic!("expected formatted event");
    };
    assert_eq!(events, vec![expected_event.clone()]);
    assert_eq!(next.tid, expected.tid);
    let listed = port.list(&expected.scope, TaskFilter {
        sid: Some(expected.sid.clone()), statuses: vec![],
    }, PageRequest { after: None, limit: NonZeroU32::MIN }).await?;
    assert_eq!(listed.items, vec![expected.clone()]);
    let mut other_scope = expected.scope.clone();
    other_scope.principal.0.push_str("-other");
    assert_eq!(port.restore(&other_scope, &expected.tid).await?, None);
    assert_eq!(port.validate_resume(&other_scope, &next).await?, ResumeCheck::InvalidScope);
    Ok(())
}
