//! Conformance entries: the reusable port harnesses against the Gateway's
//! store providers (`StoreTaskReader`, `GatewaySessionStore`).
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

mod common;

use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::inbox::AdmitRequest;
use bcs_test_support::contract::distributed::state::session_spec_admission_contract_tests;
use bcs_test_support::contract::distributed::task_feed::task_reader_port_contract_tests;
use common::*;

/// `StoreTaskReader` passes `task_reader_port_contract_tests`: a live cut is
/// restored as stored, and a cursor before the snapshot's coverage is a gap
/// both when validated and when followed.
#[tokio::test]
async fn conformance_store_task_reader() {
    let f = fixture();
    let task = snapshot(TID, TaskStatus::Working, Some(eid(12, 1)), 1);
    f.worker.append(&status_event(TID, eid(11, 1), TaskStatus::Working)).await;
    f.worker.append(&status_event(TID, eid(12, 1), TaskStatus::Working)).await;
    f.worker.create(&task).await;
    let expected = TaskView {
        scope: task.scope.clone(),
        sid: task.sid.clone(),
        tid: task.tid.clone(),
        content: task.content.clone(),
        covered_through: task.snapshot_event_id,
    };
    let expired = TaskCursor {
        sid: task.sid.clone(),
        tid: task.tid.clone(),
        token: ReplayToken {
            backend: BackendId("tbase".into()),
            generation: LogGeneration("1".into()),
            value: "11-1".into(),
        },
    };
    task_reader_port_contract_tests(&f.reader, &scope(), &expected, expired)
        .await
        .unwrap();
}

/// `GatewaySessionStore` serves the spec read of `SessionStorePort`. Its Worker
/// operations are refused by design, so `session_store_port_contract_tests`
/// (which commits) does not apply; the spec-read harness does.
#[tokio::test]
async fn conformance_gateway_session_store_spec_read() {
    let p = ports();
    let request = AdmitRequest {
        input: envelope(TID, "in-1", user_body("hello")),
        create_session: Some(session_spec(SID, scope())),
        create_task: Some(snapshot(TID, TaskStatus::Submitted, None, NOW)),
    };
    session_spec_admission_contract_tests(p.inbox.as_ref(), p.sessions.as_ref(), request)
        .await
        .unwrap();
}
