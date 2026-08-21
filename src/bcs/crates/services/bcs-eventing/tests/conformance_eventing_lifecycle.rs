#![allow(clippy::expect_used, clippy::unwrap_used)]

mod support;

use std::sync::Arc;
use std::time::Duration;

use bcs_eventing::{
    EventCatalog, EventDispatcher, EventFanoutWorker, EventRetentionWorker, EventRetryPolicy,
    EventingLifecycle,
};
use bcs_test_support::contract::lifecycle::service_lifecycle_contract_tests;

use support::{CaptureMetrics, harness};

#[tokio::test]
async fn eventing_workers_satisfy_shared_lifecycle_contract() {
    let harness = harness(true);
    let metrics = Arc::new(CaptureMetrics::default());
    let fanout = Arc::new(EventFanoutWorker::new(
        harness.repo.clone(),
        metrics.clone(),
        Arc::new(EventCatalog::load_embedded().expect("catalog")),
        "test",
        1_000,
        10,
        262_144,
    ));
    let dispatcher = Arc::new(EventDispatcher::new(
        harness.repo.clone(),
        harness.delivery.clone(),
        metrics,
        EventRetryPolicy::default(),
        "test",
        1_000,
        10,
        2,
        1,
    ));
    let retention = Arc::new(EventRetentionWorker::new(
        harness.repo.clone(),
        "test",
        100,
        100,
    ));
    let lifecycle = EventingLifecycle::new(
        fanout,
        Some(dispatcher),
        retention,
        Duration::from_millis(10),
        Duration::from_millis(10),
        Duration::from_secs(60),
        Duration::from_secs(1),
    )
    .expect("valid lifecycle");

    service_lifecycle_contract_tests(&lifecycle).await;
}
