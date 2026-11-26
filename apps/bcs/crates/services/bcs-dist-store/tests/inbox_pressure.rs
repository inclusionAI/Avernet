#![allow(clippy::unwrap_used, reason = "pool pressure contract assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{budget::*, inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::*};
use bcs_store_api::{StorePoolObservation, document::DocumentStorePlugin};
use common::*;
use std::{sync::Arc, time::Duration};

fn layout() -> KeyLayout {
    KeyLayout::new("test", "pressure").unwrap()
}
fn config() -> InboxProducerConfig {
    InboxProducerConfig::new(BackendId("model".into()))
}
fn provider(
    store: Arc<Recorder>,
    config: InboxProducerConfig,
) -> Result<StoreInboxProducer, StoreError> {
    StoreInboxProducer::new(
        Arc::new(SharedScanBudget::new(ScanBudgetConfig::default()).unwrap()),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        layout(),
        Arc::new(FixedClock(100)),
        config,
    )
}
fn request(id: &str) -> AdmitRequest {
    let mut initial = task();
    initial.tid = Tid(format!("{SID}.{id}"));
    let mut input = user_input(id, "hello");
    input.tid = initial.tid.clone();
    AdmitRequest {
        scope: scope(),
        input,
        create_session: Some(spec("g")),
        create_task: Some(initial),
    }
}
fn observation(
    capacity: usize,
    in_use: usize,
    waiters: usize,
    millis: u64,
) -> StorePoolObservation {
    StorePoolObservation::Bounded {
        capacity,
        in_use,
        waiters,
        congested_for: Duration::from_millis(millis),
    }
}

#[test]
fn startup_requires_consistent_pool_facts_and_bounded_policy() {
    for snapshot in [
        None,
        Some(observation(16, 0, 0, 0)),
        Some(observation(32, 33, 0, 0)),
        Some(observation(32, 1, 0, 10)),
    ] {
        let store = Arc::new(Recorder::default());
        store.observe_pool(snapshot);
        assert!(provider(store.clone(), config()).is_err(), "accepted {snapshot:?}");
        assert!(store.calls().is_empty(), "startup must not perform IO");
    }
    for millis in [0, 4, 5001] {
        let mut config = config();
        config.pool_pressure_after = Duration::from_millis(millis);
        assert!(provider(Arc::new(Recorder::default()), config).is_err());
    }
    for millis in [5, 5000] {
        let mut config = config();
        config.pool_pressure_after = Duration::from_millis(millis);
        assert!(provider(Arc::new(Recorder::default()), config).is_ok());
    }
    let store = Arc::new(Recorder::default());
    store.observe_pool(Some(observation(32, 32, 1, 200)));
    assert!(provider(store, config()).is_ok());
}

#[tokio::test]
async fn pool_observation_does_not_change_single_document_admission_semantics() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config()).unwrap();
    store.observe_pool(Some(observation(32, 32, 1, 200)));
    assert!(matches!(p.admit(request("under-pressure")).await.unwrap(), AdmitOutcome::Accepted(_)));
    assert!(store
        .model
        .read_document(&layout().admission(&scope(), &InputId("under-pressure".into())).unwrap())
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn same_identity_retry_remains_idempotent_when_pool_observation_changes() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config()).unwrap();
    p.admit(request("existing")).await.unwrap();
    store.observe_pool(Some(observation(32, 32, 1, 200)));
    assert!(matches!(
        p.admit(request("existing")).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
}
