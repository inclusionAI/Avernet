#![allow(clippy::unwrap_used, reason = "pool pressure contract assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{budget::*, inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, common::StoreError, inbox::*};
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
fn congested() -> StorePoolObservation {
    observation(32, 32, 1, 200)
}
fn assert_pressure(result: Result<AdmitOutcome, StoreError>) {
    assert!(
        matches!(result, Err(StoreError::NotApplied { ref code }) if code == "admission_pool_pressure"),
        "{result:?}"
    );
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
        assert!(
            provider(store.clone(), config()).is_err(),
            "accepted {snapshot:?}"
        );
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
    // Being busy is not an invalid composition; only admitting new work is gated.
    let store = Arc::new(Recorder::default());
    store.observe_pool(Some(congested()));
    assert!(provider(store, config()).is_ok());
}

#[tokio::test]
async fn sustained_pressure_refuses_before_intent_but_keeps_control_and_existing_identity() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config()).unwrap();
    p.admit(request("existing")).await.unwrap();
    let root = layout().admission_root(ShardId(42));
    let before = store.model.read_document(&root).await.unwrap();
    store.observe_pool(Some(congested()));
    store.clear();
    assert_pressure(p.admit(request("refused")).await);
    assert_eq!(store.model.read_document(&root).await.unwrap(), before);
    assert!(
        !store
            .calls()
            .iter()
            .any(|call| call.starts_with("document.create")
                || call.starts_with("document.replace")
                || call.starts_with("stream.append"))
    );
    assert!(
        store
            .model
            .read_document(&layout().task(&request("refused").input.tid).unwrap())
            .await
            .unwrap()
            .is_none()
    );
    assert!(matches!(
        p.admit(request("existing")).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
    let mut cancel = request("existing");
    cancel.input.input_id = InputId("critical-cancel".into());
    cancel.input.body = InputBody::Cancel { reason: None };
    cancel.create_task = None;
    cancel.create_session = None;
    assert!(matches!(
        p.admit(cancel).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    for _ in 0..8 {
        p.repair(ShardId(42)).await.unwrap();
    }
    store.observe_pool(Some(observation(32, 0, 0, 0)));
    assert!(matches!(
        p.admit(request("after-pressure")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
}

#[tokio::test]
async fn unavailable_or_invalid_observation_fails_closed_only_for_new_starts() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config()).unwrap();
    p.admit(request("existing")).await.unwrap();
    for snapshot in [
        None,
        Some(observation(31, 1, 0, 0)),
        Some(observation(32, 33, 0, 0)),
        Some(observation(32, 1, 0, 1)),
    ] {
        store.observe_pool(snapshot);
        assert_pressure(p.admit(request("unavailable")).await);
        assert!(matches!(
            p.admit(request("existing")).await.unwrap(),
            AdmitOutcome::Duplicate(_)
        ));
    }
}

#[tokio::test]
async fn short_pressure_is_allowed_but_queued_slot_handoff_is_still_pressure() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config()).unwrap();
    store.observe_pool(Some(observation(32, 32, 0, 199)));
    assert!(matches!(
        p.admit(request("brief")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    // A slot was handed off to a waiter but that future has not resumed yet.
    store.observe_pool(Some(observation(32, 31, 1, 200)));
    assert_pressure(p.admit(request("handoff-pressure")).await);
}

#[tokio::test]
async fn an_unknown_durable_reservation_is_repaired_while_new_starts_are_blocked() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config()).unwrap();
    store.lose_write(1);
    assert!(matches!(
        p.admit(request("lost-reservation")).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    store.observe_pool(Some(congested()));
    assert_pressure(p.admit(request("new")).await);
    // Repair, without a caller retry, finishes the original durable intent.
    for _ in 0..12 {
        p.repair(ShardId(42)).await.unwrap();
        if p.admission_decision(&scope(), &InputId("lost-reservation".into()))
            .await
            .unwrap()
            .is_some()
        {
            break;
        }
    }
    assert!(matches!(
        p.admission_decision(&scope(), &InputId("lost-reservation".into()))
            .await
            .unwrap(),
        Some(AdmitOutcome::Duplicate(_))
    ));
    assert!(matches!(
        p.admit(request("lost-reservation")).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
}

#[tokio::test]
async fn pressure_does_not_refuse_a_same_identity_pending_reservation_retry() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config()).unwrap();
    store.lose_write(1);
    assert!(matches!(
        p.admit(request("pending")).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    store.observe_pool(Some(congested()));
    assert!(matches!(
        p.admit(request("pending")).await.unwrap(),
        AdmitOutcome::Accepted(_) | AdmitOutcome::Duplicate(_)
    ));
}

#[tokio::test]
async fn pressure_refusal_rechecks_a_racing_canonical_winner() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config()).unwrap();
    let pause = store.pause_after("document.read", ":canonical:", 1);
    let caller = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("race")).await }
    });
    pause.reached().await;
    p.admit(request("race")).await.unwrap();
    store.observe_pool(Some(congested()));
    pause.resume();
    assert!(matches!(
        caller.await.unwrap().unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
}
