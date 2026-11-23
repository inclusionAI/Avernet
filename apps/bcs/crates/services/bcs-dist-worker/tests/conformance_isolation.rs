#![allow(
    clippy::unwrap_used,
    reason = "real processor authorization conformance"
)]
mod processor_support;
mod support;
use bcs_dist_model::PrincipalId;
use bcs_dist_worker::WorkerProcessor;
use bcs_test_support::contract::distributed::runtime_safety::dist_worker_isolation_control_port_contract_tests;
use processor_support::*;
use std::sync::Arc;
#[tokio::test]
async fn conformance_processor_scope_isolation() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    f.admit("scope").await;
    f.warm().await;
    let mut wrong = spec().scope;
    wrong.principal = PrincipalId("other".into());
    let port: &WorkerProcessor = &f.processor;
    dist_worker_isolation_control_port_contract_tests(port, &wrong, &spec().sid)
        .await
        .unwrap();
}

#[tokio::test]
async fn conformance_idle_runtime_drain() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    let runtime: &bcs_dist_worker::WorkerRuntime = f.runtime.as_ref();
    bcs_test_support::contract::distributed::services::dist_runtime_control_port_contract_tests(
        runtime,
    )
    .await
    .unwrap();
}
