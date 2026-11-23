#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, inbox::*};
use bcs_store_api::document::DocumentStorePlugin;
use common::*;
use std::sync::Arc;

fn layout() -> KeyLayout {
    KeyLayout::new("test", "retention").unwrap()
}
fn config() -> InboxProducerConfig {
    InboxProducerConfig::new(BackendId("model".into()))
}
fn provider(
    store: Arc<Recorder>,
    cfg: InboxProducerConfig,
) -> Result<StoreInboxProducer, bcs_service_api::port::repo::distributed::common::StoreError> {
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
fn larger_retained_history_profile_keeps_the_root_session_bound() {
    let mut cfg = config();
    cfg.max_history_entries = 8192;
    cfg.max_sessions = 4096;
    assert!(provider(Arc::new(Recorder::default()), cfg.clone()).is_ok());
    cfg.max_sessions = 4097;
    assert!(provider(Arc::new(Recorder::default()), cfg).is_err());
}

#[test]
fn impossible_retained_scan_capacity_is_rejected_before_storage_io() {
    let store = Arc::new(Recorder::default());
    let mut cfg = config();
    cfg.max_history_entries = 1_000_000;
    assert!(provider(store.clone(), cfg).is_err());
    assert!(store.calls().is_empty());
}

#[test]
fn pending_scan_budget_accepts_exact_bytes_and_rejects_the_next_entry() {
    let store = Arc::new(Recorder::default());
    let mut cfg = config();
    cfg.max_history_entries = 20_000;
    // 222 bytes of maximum-width metadata and 21 per possible pending u64.
    cfg.max_document_bytes = 420_222;
    assert!(provider(store.clone(), cfg.clone()).is_ok());
    cfg.max_history_entries += 1;
    assert!(matches!(
        provider(store.clone(), cfg.clone()),
        Err(bcs_service_api::port::repo::distributed::common::StoreError::Invalid { code })
            if code == "admission_scan_capacity"
    ));
    cfg.max_history_entries -= 1;
    cfg.max_document_bytes -= 1;
    assert!(provider(store.clone(), cfg).is_err());
    let mut cfg = config();
    cfg.max_history_entries = 46_800;
    assert!(provider(store.clone(), cfg.clone()).is_ok());
    cfg.max_history_entries += 1;
    assert!(provider(store.clone(), cfg).is_err());
    assert!(store.calls().is_empty());
}

#[tokio::test]
async fn overflowing_inbox_bytes_refuse_without_wrapping_or_changing_authority() {
    let store = Arc::new(Recorder::default());
    let mut cfg = config();
    cfg.max_inbox_bytes = u64::MAX;
    let p = provider(store.clone(), cfg).unwrap();
    p.admit(request("existing")).await.unwrap();
    let root_key = layout().admission_root(ShardId(42));
    let mut root = store.model.read_document(&root_key).await.unwrap().unwrap();
    root.value["inbox"][SID]["bytes"] = (u64::MAX - 1).into();
    store
        .model
        .replace_document(&root_key, root.version, &root.value)
        .await
        .unwrap();
    let mut cancel = request("overflow-cancel");
    cancel.create_session = None;
    cancel.create_task = None;
    cancel.input.body = InputBody::Cancel { reason: None };
    assert!(matches!(
        p.admit(cancel).await,
        Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied { code })
            if code == "admission_capacity"
    ));
    assert_eq!(
        store
            .model
            .read_document(&root_key)
            .await
            .unwrap()
            .unwrap()
            .value,
        root.value
    );
}

#[tokio::test]
async fn retained_charges_stop_new_starts_but_keep_control_reserve_visible() {
    let store = Arc::new(Recorder::default());
    let mut cfg = config();
    cfg.max_history_entries = 130;
    cfg.reserved_entries = 64;
    cfg.max_sessions = 16;
    let p = provider(store.clone(), cfg).unwrap();
    let empty = p.maintenance_observation(ShardId(42)).await.unwrap();
    assert!(empty.capacity.is_none());
    p.admit(request("accepted")).await.unwrap();
    p.repair(ShardId(42)).await.unwrap();
    let root_key = layout().admission_root(ShardId(42));
    let root = store.model.read_document(&root_key).await.unwrap().unwrap();
    assert_eq!(root.value["entries"], 2);
    assert_eq!(root.value["normal_entries"], 2);
    assert_eq!(root.value["pending"], serde_json::json!({}));
    store.clear();
    let observed = p.maintenance_observation(ShardId(42)).await.unwrap();
    let capacity = observed.capacity.unwrap();
    assert_eq!(observed.retained_entries, Some(1));
    assert_eq!(capacity.charged_history_entries, 2);
    assert_eq!(capacity.normal_entries_remaining, 0);
    assert_eq!(capacity.history_entries_remaining, 128);
    assert_eq!(capacity.session_reservations_remaining, 15);
    assert_eq!(capacity.distinct_sessions_remaining, 15);
    assert_eq!(capacity.minimum_inbox_entries_remaining, 4095);
    assert_eq!(capacity.minimum_normal_inbox_entries_remaining, 3967);
    assert!(capacity.near_limit);
    // Diagnostics use the same two bounded reads; no per-Session/N+1 queries.
    assert_eq!(store.calls().len(), 2);
    assert!(
        store
            .calls()
            .iter()
            .all(|call| call.starts_with("document.read"))
    );
    let mut next = request("new-start");
    let tid = Tid(format!("{SID}.second"));
    next.input.tid = tid.clone();
    next.create_task.as_mut().unwrap().tid = tid;
    assert!(matches!(
        p.admit(next).await,
        Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied { code })
            if code == "admission_capacity"
    ));
    assert!(matches!(
        p.admit(request("accepted")).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
    let mut cancel = request("cancel");
    cancel.create_session = None;
    cancel.create_task = None;
    cancel.input.body = InputBody::Cancel { reason: None };
    assert!(matches!(
        p.admit(cancel).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    let capacity = p
        .maintenance_observation(ShardId(42))
        .await
        .unwrap()
        .capacity
        .unwrap();
    assert_eq!(capacity.normal_entries_remaining, 0);
    assert_eq!(capacity.history_entries_remaining, 126);
    assert_eq!(capacity.charged_history_entries, 4);
}

#[tokio::test]
async fn exhausted_repair_preserves_its_intent_and_resumes_after_coordinated_expansion() {
    let store = Arc::new(Recorder::default());
    let cfg = config();
    let p = provider(store.clone(), cfg.clone()).unwrap();
    // Only one real intent is created. The persisted counters below model the
    // exact lifetime boundary; this is not a history or load test.
    let pause = store.pause_after("document.replace", ":canonical:", 7);
    let creator = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("pending-at-capacity")).await }
    });
    pause.reached().await;
    creator.abort();
    let _ = creator.await;
    let canonical_key = layout()
        .canonical_admission(&scope(), &InputId("pending-at-capacity".into()))
        .unwrap();
    let original = store
        .model
        .read_document(&canonical_key)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(original.value["append_credits"], 0);
    assert!(original.value["position"].is_null());
    let root_key = layout().admission_root(ShardId(42));
    let mut root = store.model.read_document(&root_key).await.unwrap().unwrap();
    root.value["entries"] = cfg.max_history_entries.into();
    root.value["bytes"] = cfg.max_history_bytes.into();
    root.value["normal_entries"] = (cfg.max_history_entries - cfg.reserved_entries).into();
    root.value["normal_bytes"] = (cfg.max_history_bytes - cfg.reserved_bytes).into();
    root.value["inbox"][SID]["entries"] = cfg.max_inbox_entries.into();
    root.value["inbox"][SID]["bytes"] = cfg.max_inbox_bytes.into();
    root.value["inbox"][SID]["normal_entries"] = (cfg.max_inbox_entries - 64).into();
    root.value["inbox"][SID]["normal_bytes"] = (cfg.max_inbox_bytes - 128 * 1024).into();
    store
        .model
        .replace_document(&root_key, root.version, &root.value)
        .await
        .unwrap();
    for _ in 0..2 {
        let report = p.repair(ShardId(42)).await.unwrap();
        assert_eq!(report.completed_through, 0);
        assert!(report.pending > 0);
    }
    assert_eq!(
        store
            .model
            .read_document(&canonical_key)
            .await
            .unwrap()
            .unwrap()
            .value,
        original.value
    );
    let capacity = p
        .maintenance_observation(ShardId(42))
        .await
        .unwrap()
        .capacity
        .unwrap();
    assert_eq!(capacity.history_entries_remaining, 0);
    assert_eq!(capacity.history_bytes_remaining, 0);
    assert_eq!(capacity.normal_entries_remaining, 0);
    assert_eq!(capacity.normal_bytes_remaining, 0);
    assert_eq!(capacity.minimum_inbox_entries_remaining, 0);
    assert_eq!(capacity.minimum_inbox_bytes_remaining, 0);
    assert_eq!(capacity.minimum_normal_inbox_entries_remaining, 0);
    assert_eq!(capacity.minimum_normal_inbox_bytes_remaining, 0);
    assert!(capacity.near_limit);
    // Stop old writers, increase every exhausted budget, retain namespace and
    // immutable evidence, then resume maintenance without caller re-admission.
    drop(p);
    let mut expanded = cfg;
    expanded.max_history_entries *= 2;
    expanded.max_history_bytes *= 2;
    expanded.max_inbox_entries *= 2;
    expanded.max_inbox_bytes *= 2;
    let successor = provider(store.clone(), expanded).unwrap();
    for _ in 0..8 {
        successor.repair(ShardId(42)).await.unwrap();
    }
    assert!(matches!(
        successor
            .admission_decision(&scope(), &InputId("pending-at-capacity".into()))
            .await
            .unwrap(),
        Some(AdmitOutcome::Duplicate(_))
    ));
    let repaired = store
        .model
        .read_document(&canonical_key)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(repaired.value["intent"], original.value["intent"]);
    assert!(!repaired.value["position"].is_null());
    assert_eq!(
        store
            .model
            .read_document(&root_key)
            .await
            .unwrap()
            .unwrap()
            .value["next"],
        1
    );
}
