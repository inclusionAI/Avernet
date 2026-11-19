#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, common::*, inbox::*};
use bcs_store_api::{document::DocumentStorePlugin, stream::*, zset::ZSetStorePlugin};
use common::*;
use std::{
    num::NonZeroU32,
    sync::{
        Arc,
        atomic::{AtomicI64, Ordering},
    },
};
fn layout() -> KeyLayout {
    KeyLayout::new("test", "maintenance").unwrap()
}
struct ClockAt(AtomicI64);
impl Clock for ClockAt {
    fn now(&self) -> TimestampMillis {
        TimestampMillis(self.0.load(Ordering::SeqCst))
    }
}
fn provider(
    s: Arc<Recorder>,
    clock: Arc<ClockAt>,
    config: InboxProducerConfig,
) -> StoreInboxProducer {
    StoreInboxProducer::new(
        Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        ),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        s.clone(),
        s.clone(),
        s.clone(),
        s,
        layout(),
        clock,
        config,
    )
    .unwrap()
}
fn config() -> InboxProducerConfig {
    InboxProducerConfig::new(BackendId("model".into()))
}
fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}
#[tokio::test]
async fn permanent_gap_does_not_hide_later_intents_and_watermark_never_crosses_it() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let p = provider(store.clone(), clock.clone(), config());
    // A corrupt canonical cannot be safely finalized and remains explicit Pending.
    let pause = store.pause_after("document.create", ":canonical:", 1);
    let bad = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("bad")).await }
    });
    pause.reached().await;
    let key = layout()
        .canonical_admission(&scope(), &InputId("bad".into()))
        .unwrap();
    let mut doc = store.read_document(&key).await.unwrap().unwrap();
    doc.value["schema_version"] = 2.into();
    store
        .replace_document(&key, doc.version, &doc.value)
        .await
        .unwrap();
    pause.resume();
    assert!(bad.await.unwrap().is_err());
    store.lose_write(1);
    assert!(p.admit(request("good")).await.is_err());
    let report = p.repair(ShardId(42)).await.unwrap();
    assert!(report.pending > 0);
    assert_eq!(report.completed_through, 0);
    // Each maintenance visit now persists one small phase, allowing normal RTT
    // without restarting a long all-or-nothing chain on every 100ms turn.
    for _ in 0..12 {
        assert_eq!(p.repair(ShardId(42)).await.unwrap().completed_through, 0);
    }
    assert!(matches!(
        p.admission_decision(&scope(), &InputId("good".into()))
            .await
            .unwrap(),
        Some(AdmitOutcome::Duplicate(_))
    ));
    let restarted = provider(store.clone(), clock.clone(), config());
    let report = restarted.repair(ShardId(42)).await.unwrap();
    assert_eq!(report.completed_through, 0);
    assert!(report.pending > 0);
    // Lag pauses only new Starts; existing cancellation retains admission quota.
    clock.0.store(60_200, Ordering::SeqCst);
    restarted.repair(ShardId(42)).await.unwrap();
    assert!(
        matches!(restarted.admit(request("paused")).await,Err(StoreError::NotApplied{code}) if code=="admission_capacity")
    );
    let mut control = request("cancel");
    control.create_session = None;
    control.create_task = None;
    control.input.body = InputBody::Cancel { reason: None };
    assert!(matches!(
        restarted.admit(control).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
}
#[tokio::test]
async fn discovery_survives_deleted_hints_and_completed_repair_watermarks() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let p = provider(store.clone(), clock.clone(), config());
    p.admit(request("done")).await.unwrap();
    assert!(p.repair(ShardId(42)).await.unwrap().completed_through > 0);
    store
        .model
        .remove(&layout().active(ShardId(42)), &[SID.into()])
        .await
        .unwrap();
    store
        .model
        .trim(&layout().wake(ShardId(42)), TrimPolicy::MaxLen(0))
        .await
        .unwrap();
    // Even next_check=None must be repaired from unconsumed physical Inbox.
    let key = layout().schedule(&Sid(SID.into())).unwrap();
    let mut doc = store.read_document(&key).await.unwrap().unwrap();
    doc.value["next_check"] = serde_json::Value::Null;
    store
        .replace_document(&key, doc.version, &doc.value)
        .await
        .unwrap();
    let restarted = provider(store.clone(), clock, config());
    assert!(restarted.discover(ShardId(42)).await.unwrap().activated > 0);
    assert_eq!(
        restarted
            .due(
                ShardId(42),
                TimestampMillis(100),
                PageRequest {
                    after: None,
                    limit: NonZeroU32::new(128).unwrap()
                }
            )
            .await
            .unwrap()
            .items,
        vec![Sid(SID.into())]
    );
}
#[tokio::test]
async fn durable_fixed_cut_advances_across_provider_restarts_and_new_history() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let p = provider(store.clone(), clock.clone(), config());
    for i in 0..140 {
        p.admit(request(&format!("input-{i}"))).await.unwrap();
    }
    let first = p.discover(ShardId(42)).await.unwrap();
    assert!(first.examined <= 64);
    assert!(!first.round_complete);
    for i in 140..150 {
        p.admit(request(&format!("input-{i}"))).await.unwrap();
    }
    let restarted = provider(store.clone(), clock.clone(), config());
    let second = restarted.discover(ShardId(42)).await.unwrap();
    assert!(second.examined <= 64);
    let key = layout().admission_scan(ShardId(42), "discovery").unwrap();
    let mut previous = store.read_document(&key).await.unwrap().unwrap().value["cursor"]
        .as_u64()
        .unwrap();
    let mut completed = false;
    // The per-turn time budget can stop before the item limit under contention.
    // Restart repeatedly while retaining the original cut and continuous cursor.
    for _ in 0..12 {
        let restarted = provider(store.clone(), clock.clone(), config());
        let turn = restarted.discover(ShardId(42)).await.unwrap();
        assert!(turn.examined <= 64);
        let scan = store.read_document(&key).await.unwrap().unwrap().value;
        assert_eq!(scan["cut"], 140);
        let cursor = scan["cursor"].as_u64().unwrap();
        assert!(cursor >= previous && cursor <= 140);
        previous = cursor;
        if turn.round_complete {
            assert_eq!(cursor, 140);
            completed = true;
            break;
        }
    }
    assert!(
        completed,
        "the fixed cut did not complete within bounded restarts"
    );
}
#[tokio::test]
async fn concurrent_reservation_cannot_spend_control_capacity() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let mut cfg = config();
    cfg.max_history_entries = 128;
    cfg.reserved_entries = 32;
    cfg.max_sessions = 64;
    cfg.max_history_bytes = 4 * 1024 * 1024;
    cfg.reserved_bytes = 512 * 1024;
    let p = provider(store.clone(), clock, cfg);
    let mut running = Vec::new();
    for i in 0..40 {
        let p = p.clone();
        running.push(tokio::spawn(async move {
            p.admit(request(&format!("normal-{i}"))).await
        }));
    }
    for task in running {
        let _ = task.await.unwrap();
    }
    for _ in 0..4 {
        let _ = p.repair(ShardId(42)).await;
    }
    let root = store
        .read_document(&layout().admission_root(ShardId(42)))
        .await
        .unwrap()
        .unwrap()
        .value;
    assert!(root["normal_entries"].as_u64().unwrap() <= 96);
    assert!(root["entries"].as_u64().unwrap() <= 96);
    let mut control = request("reserved-control");
    control.create_session = None;
    control.create_task = None;
    control.input.body = InputBody::Cancel { reason: None };
    assert!(matches!(
        p.admit(control).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    let root = store
        .read_document(&layout().admission_root(ShardId(42)))
        .await
        .unwrap()
        .unwrap()
        .value;
    assert!(root["entries"].as_u64().unwrap() <= 128);
}

#[tokio::test]
async fn stopped_scanners_backpressure_without_a_wall_clock_or_paused_flag_update() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let mut cfg = config();
    cfg.maintenance_stale_after = std::time::Duration::from_millis(40);
    let p = provider(store.clone(), clock.clone(), cfg);
    p.admit(request("initial")).await.unwrap();
    tokio::time::sleep(std::time::Duration::from_millis(45)).await;
    assert!(
        matches!(p.admit(request("stopped")).await,Err(StoreError::NotApplied{code}) if code=="admission_capacity")
    );
    p.repair(ShardId(42)).await.unwrap();
    p.discover(ShardId(42)).await.unwrap();
    assert!(matches!(
        p.admit(request("healthy-again")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    let cold = provider(store, clock, config());
    assert!(
        matches!(cold.admit(request("cold-start")).await,Err(StoreError::NotApplied{code}) if code=="admission_capacity")
    );
}

#[tokio::test]
async fn scanning_large_intents_bounds_actual_log_read_bytes() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let p = provider(store.clone(), clock, config());
    for index in 0..24 {
        let mut r = request(&format!("large-{index}"));
        r.input.body = InputBody::User {
            payload: Payload::Inline(serde_json::json!("x".repeat(55_000))),
        };
        p.admit(r).await.unwrap();
        // A tiny broken canonical makes each scan item fail quickly, exposing
        // accidental reads beyond a page's byte cap rather than hiding them in IO.
        let key = layout()
            .canonical_admission(&scope(), &InputId(format!("large-{index}")))
            .unwrap();
        let old = store.model.read_document(&key).await.unwrap().unwrap();
        store
            .model
            .replace_document(&key, old.version, &serde_json::json!({"schema_version":2}))
            .await
            .unwrap();
    }
    store.clear();
    p.discover(ShardId(42)).await.unwrap();
    let calls = store.calls();
    let mut bytes = 0;
    for call in calls
        .iter()
        .filter(|c| c.starts_with("document.read ") && c.contains(":admission-intent:"))
    {
        let key = call.strip_prefix("document.read ").unwrap();
        let doc = store.model.read_document(key).await.unwrap().unwrap();
        bytes += serde_json::to_vec(&doc.value).unwrap().len();
    }
    assert!(bytes <= 1024 * 1024, "read {bytes} intent bytes");
}

#[tokio::test]
async fn takeover_enumeration_is_not_blocked_by_another_corrupt_canonical() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let p = provider(store.clone(), clock, config());
    p.admit(request("broken-first")).await.unwrap();
    let mut later = request("healthy-later");
    let sid = Sid("s0042.later".into());
    let tid = Tid("s0042.later.task".into());
    later.input.sid = sid.clone();
    later.input.tid = tid.clone();
    later.create_session.as_mut().unwrap().sid = sid.clone();
    let task = later.create_task.as_mut().unwrap();
    task.sid = sid.clone();
    task.tid = tid;
    p.admit(later).await.unwrap();
    let key = layout()
        .canonical_admission(&scope(), &InputId("broken-first".into()))
        .unwrap();
    let old = store.model.read_document(&key).await.unwrap().unwrap();
    store
        .model
        .replace_document(&key, old.version, &serde_json::json!({"schema_version":2}))
        .await
        .unwrap();
    let first = p
        .enumerate_pending(
            ShardId(42),
            PageRequest {
                after: None,
                limit: NonZeroU32::MIN,
            },
        )
        .await
        .unwrap();
    assert_eq!(first.items, vec![Sid(SID.into())]);
    let second = p
        .enumerate_pending(
            ShardId(42),
            PageRequest {
                after: first.next,
                limit: NonZeroU32::MIN,
            },
        )
        .await
        .unwrap();
    assert_eq!(second.items, vec![sid]);
}

#[tokio::test]
async fn completed_fixed_round_publishes_health_while_new_intents_keep_arriving() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let p = provider(store.clone(), clock, config());
    p.admit(request("initial")).await.unwrap();
    for (kind, discovery) in [("repair", false), ("discovery", true)] {
        let pause = store.pause_after("document.create", &format!(":admission-scan:{kind}"), 1);
        let scan = tokio::spawn({
            let p = p.clone();
            async move {
                if discovery {
                    p.discover(ShardId(42)).await
                } else {
                    p.repair(ShardId(42)).await
                }
            }
        });
        pause.reached().await;
        p.admit(request(&format!("after-{kind}-cut")))
            .await
            .unwrap();
        pause.resume();
        assert!(scan.await.unwrap().unwrap().round_complete);
        let scan_doc = store
            .model
            .read_document(&layout().admission_scan(ShardId(42), kind).unwrap())
            .await
            .unwrap()
            .unwrap();
        let root = store
            .model
            .read_document(&layout().admission_root(ShardId(42)))
            .await
            .unwrap()
            .unwrap();
        assert!(scan_doc.value["cut"].as_u64().unwrap() < root.value["next"].as_u64().unwrap());
        assert!(root.value[format!("{kind}_health")].as_u64().unwrap() > 0);
    }
}

#[tokio::test]
async fn bounded_health_cache_can_follow_sequential_shard_assignments() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let mut cfg = config();
    cfg.max_readers = 1;
    let p = provider(store.clone(), clock, cfg);
    p.admit(request("first-shard")).await.unwrap();
    p.repair(ShardId(42)).await.unwrap();
    let mut second = request("second-shard");
    let sid = Sid("s0043.second".into());
    let tid = Tid("s0043.second.task".into());
    second.input.sid = sid.clone();
    second.input.tid = tid.clone();
    let spec = second.create_session.as_mut().unwrap();
    spec.sid = sid.clone();
    spec.shard = ShardId(43);
    let task = second.create_task.as_mut().unwrap();
    task.sid = sid;
    task.tid = tid;
    assert!(matches!(
        p.admit(second).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    p.repair(ShardId(43)).await.unwrap();
    p.discover(ShardId(43)).await.unwrap();
    // Revisiting an evicted shard must re-establish health from retained history.
    assert!(matches!(
        p.admit(request("cold-again")).await,
        Err(StoreError::NotApplied { .. })
    ));
    p.repair(ShardId(42)).await.unwrap();
    p.discover(ShardId(42)).await.unwrap();
    assert!(matches!(
        p.admit(request("recovered-first")).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
}

#[tokio::test]
async fn corrupt_intent_validation_still_spends_the_scan_byte_budget() {
    let store = Arc::new(Recorder::default());
    let clock = Arc::new(ClockAt(AtomicI64::new(100)));
    let p = provider(store.clone(), clock, config());
    for index in 0..24 {
        let mut r = request(&format!("corrupt-large-{index}"));
        r.input.body = InputBody::User {
            payload: Payload::Inline(serde_json::json!("x".repeat(55_000))),
        };
        p.admit(r).await.unwrap();
        let key = layout().admission_intent(ShardId(42), index + 1);
        let mut old = store.model.read_document(&key).await.unwrap().unwrap();
        old.value["schema_version"] = 2.into();
        store
            .model
            .replace_document(&key, old.version, &old.value)
            .await
            .unwrap();
    }
    store.clear();
    assert!(p.discover(ShardId(42)).await.unwrap().pending > 0);
    let mut bytes = 0;
    for call in store
        .calls()
        .iter()
        .filter(|c| c.starts_with("document.read ") && c.contains(":admission-intent:"))
    {
        let key = call.strip_prefix("document.read ").unwrap();
        let doc = store.model.read_document(key).await.unwrap().unwrap();
        bytes += serde_json::to_vec(&doc.value).unwrap().len();
    }
    assert!(bytes <= 1024 * 1024, "read {bytes} corrupt intent bytes");
}

#[tokio::test]
async fn lost_disposable_hint_does_not_poison_a_confirmed_discovery_round() {
    let store = Arc::new(Recorder::default());
    let p = provider(
        store.clone(),
        Arc::new(ClockAt(AtomicI64::new(100))),
        config(),
    );
    p.admit(request("hint-timeout")).await.unwrap();
    let pause = store.pause_after("stream.append", &layout().wake(ShardId(42)), 1);
    let mut finished = None;
    for _ in 0..20 {
        let report = p.discover(ShardId(42)).await.unwrap();
        if report.round_complete {
            finished = Some(report);
            break;
        }
    }
    pause.resume();
    let report = finished.expect("bounded discovery completes");
    assert_eq!(
        report.pending, 0,
        "authority was checked and activation persisted; a disposable hint is not a repair gap"
    );
    let root = store
        .read_document(&layout().admission_root(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert!(root.value["discovery_health"].as_u64().unwrap() > 0);
}

#[tokio::test]
async fn valid_single_io_profile_can_advance_maintenance() {
    let s = Arc::new(Recorder::default());
    let budget = Arc::new(
        bcs_dist_store::budget::SharedScanBudget::new(bcs_dist_store::budget::ScanBudgetConfig {
            pool_size: 4,
            io_slots: 1,
            ..Default::default()
        })
        .unwrap(),
    );
    let mut config = config();
    config.connection_pool_size = 4;
    let p = StoreInboxProducer::new(
        budget,
        Arc::new(bcs_dist_projection::OutputProjectionService),
        s.clone(),
        s.clone(),
        s.clone(),
        s,
        layout(),
        Arc::new(FixedClock(100)),
        config,
    )
    .unwrap();
    let mut complete = false;
    for _ in 0..3 {
        let result = p.repair(ShardId(42)).await.unwrap();
        complete |= result.round_complete;
    }
    assert!(complete);
}
