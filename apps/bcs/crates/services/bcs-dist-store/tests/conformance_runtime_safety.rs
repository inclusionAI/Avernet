#![allow(clippy::unwrap_used, reason = "real provider conformance assertions")]
mod common;
mod control_support;
use bcs_dist_model::*;
use bcs_dist_store::{
    budget::*,
    coordination::CoordinationStore,
    inbox::{StoreAdmissionControl, StoreInboxProducer},
    task_feed::StoreTaskFeed,
};
use bcs_service_api::port::repo::distributed::control::*;
use bcs_test_support::contract::distributed::runtime_safety::*;
use control_support::Fixture;
#[test]
fn conformance_shared_scan_budget() {
    scan_budget_port_contract_tests(&SharedScanBudget::new(Default::default()).unwrap());
}
#[tokio::test]
async fn conformance_admission_and_control() {
    let f = Fixture::new().await;
    let port: &CoordinationStore = &f.store;
    let guard = f.guard().await;
    session_control_port_contract_tests(
        port,
        guard.clone(),
        AttemptIdentity {
            id: RequestId("contract-attempt".into()),
            head: guard.expected_head,
            state_digest: Digest("state".into()),
            input_digest: Digest("input".into()),
            context_digest: Digest("context".into()),
            frozen_now: TimestampMillis(100),
        },
    )
    .await
    .unwrap();
    let f = Fixture::new().await;
    f.admit("contract-input").await;
    let port: &StoreInboxProducer = &f.inbox;
    admission_maintenance_port_contract_tests(port, ShardId(42))
        .await
        .unwrap();
    let feed: &StoreTaskFeed = f.feed.as_ref();
    terminal_task_evidence_port_contract_tests(
        feed,
        &common::scope(),
        &Tid(common::TID.into()),
        None,
    )
    .await
    .unwrap();
}
#[tokio::test]
async fn conformance_empty_closure_certificate() {
    let f = Fixture::new().await;
    let closure = f.isolate("contract-isolation").await;
    let raw: &StoreInboxProducer = &f.inbox;
    admission_control_port_contract_tests(raw, &f.owner, &closure)
        .await
        .unwrap();
    let port: &StoreAdmissionControl = &f.control;
    admission_control_port_contract_tests(port, &f.owner, &closure)
        .await
        .unwrap();
    admission_closure_port_contract_tests(port, &f.owner, &closure)
        .await
        .unwrap();
}
