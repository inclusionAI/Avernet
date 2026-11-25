#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    deprecated,
    reason = "legacy-field compatibility assertions"
)]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::inbox::*;
use bcs_store_api::document::DocumentStorePlugin;
use common::*;
use std::sync::Arc;

fn layout() -> KeyLayout {
    KeyLayout::new("test", "retention").unwrap()
}
fn config() -> InboxProducerConfig {
    InboxProducerConfig::new(BackendId("model".into()))
}
fn provider(store: Arc<Recorder>, cfg: InboxProducerConfig) -> StoreInboxProducer {
    StoreInboxProducer::new(
        Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        ),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        layout(),
        Arc::new(FixedClock(100)),
        cfg,
    )
    .unwrap()
}
fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}

#[test]
fn legacy_history_budget_fields_do_not_control_startup_or_admission() {
    let mut cfg = config();
    cfg.max_history_entries = 1_000_000;
    cfg.max_history_bytes = 1_000_000_000;
    cfg.reserved_entries = 0;
    cfg.reserved_bytes = 0;
    cfg.max_sessions = 0;
    let store = Arc::new(Recorder::default());
    let _p = provider(store.clone(), cfg);
    assert!(store.calls().is_empty());
}

#[tokio::test]
async fn admission_history_has_no_shard_wide_capacity_observation() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config());
    for id in ["first", "second", "third"] {
        p.admit(request(id)).await.unwrap();
    }
    let observation = p.maintenance_observation(ShardId(42)).await.unwrap();
    assert!(observation.capacity.is_none());
    assert!(observation.retained_entries.is_none());
    assert!(observation.retained_bytes.is_none());
    assert!(observation.retained_sessions.is_none());
    assert!(store
        .model
        .read_document(&layout().admission(&scope(), &InputId("first".into())).unwrap())
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn uncertain_create_is_retried_by_the_same_input_id() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone(), config());
    store.lose_write(1);
    assert!(p.admit(request("retry")).await.is_err());

    let key = layout()
        .admission(&scope(), &InputId("retry".into()))
        .unwrap();
    assert!(store.model.read_document(&key).await.unwrap().is_some());
    let retry = p.admit(request("retry")).await.unwrap();
    assert!(matches!(retry, AdmitOutcome::Duplicate(_)));
    let admission = store.model.read_document(&key).await.unwrap().unwrap();
    assert!(admission.value.get("request").is_some());
    assert!(admission.value.get("intent").is_none());
    assert!(admission.value.get("sequence").is_none());
}
