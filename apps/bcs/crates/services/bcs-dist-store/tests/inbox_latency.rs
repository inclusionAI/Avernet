#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
mod latency_support;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{admission::*, common::*, inbox::*};
use bcs_store_api::document::DocumentStorePlugin;
use common::*;
use latency_support::DelayedDocuments;
use std::{
    num::NonZeroU32,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};
fn layout() -> KeyLayout {
    KeyLayout::new("test", "latency").unwrap()
}
fn request(id: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(id, "hello"),
        create_session: Some(spec("g")),
        create_task: Some(task()),
    }
}
fn provider(
    store: Arc<Recorder>,
    delay: Arc<DelayedDocuments>,
    cfg: InboxProducerConfig,
) -> StoreInboxProducer {
    StoreInboxProducer::new(
        store.clone(),
        delay,
        store.clone(),
        store,
        layout(),
        Arc::new(FixedClock(100)),
        cfg,
    )
    .unwrap()
}
fn setup(cfg: InboxProducerConfig) -> (Arc<Recorder>, Arc<DelayedDocuments>, StoreInboxProducer) {
    let store = Arc::new(Recorder::default());
    let delay = Arc::new(DelayedDocuments {
        store: store.clone(),
        millis: AtomicU64::new(0),
        slow_key: std::sync::Mutex::new(None),
    });
    let p = provider(store.clone(), delay.clone(), cfg);
    (store, delay, p)
}
#[tokio::test]
async fn repair_makes_durable_progress_at_five_and_ten_millisecond_document_rtt() {
    for millis in [5, 10] {
        let (store, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
        store.lose_write(1);
        assert!(p.admit(request("slow-repair")).await.is_err());
        delay.millis.store(millis, Ordering::Relaxed);
        for _ in 0..24 {
            let _ = p.repair(ShardId(42)).await;
        }
        assert!(
            matches!(
                p.admission_decision(&scope(), &InputId("slow-repair".into()))
                    .await
                    .unwrap(),
                Some(AdmitOutcome::Duplicate(_))
            ),
            "RTT {millis}"
        );
    }
}
#[tokio::test]
async fn inbox_capacity_rejects_before_new_intent_and_reserves_control_and_repair() {
    let mut cfg = InboxProducerConfig::new(BackendId("model".into()));
    cfg.max_inbox_entries = 129;
    let (store, _, p) = setup(cfg);
    p.admit(request("first")).await.unwrap();
    let mut second = request("second");
    let tid = Tid(format!("{SID}.second"));
    second.input.tid = tid.clone();
    second.create_task.as_mut().unwrap().tid = tid.clone();
    assert!(matches!(
        p.admit(second).await,
        Err(StoreError::NotApplied { .. })
    ));
    assert!(
        store
            .model
            .read_document(&layout().task(&tid).unwrap())
            .await
            .unwrap()
            .is_none()
    );
    let root = store
        .model
        .read_document(&layout().admission_root(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(root.value["next"], 1);
    let mut cancel = request("cancel");
    cancel.create_session = None;
    cancel.create_task = None;
    cancel.input.body = InputBody::Cancel { reason: None };
    assert!(matches!(
        p.admit(cancel).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
}
#[tokio::test]
async fn reader_returns_classified_prefix_when_later_row_exhausts_time_budget() {
    let (_, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    for i in 0..16 {
        p.admit(request(&format!("row-{i}"))).await.unwrap();
    }
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    delay.millis.store(3, Ordering::Relaxed);
    let batch = feed
        .next(
            ReadLimits {
                max_sessions: NonZeroU32::MIN,
                max_entries: NonZeroU32::new(128).unwrap(),
                max_bytes: NonZeroU32::new(1024 * 1024).unwrap(),
            },
            Duration::ZERO,
        )
        .await
        .unwrap();
    assert!(batch.failures.is_empty());
    assert_eq!(batch.sessions.len(), 1);
    assert!(!batch.sessions[0].entries.is_empty());
}

#[tokio::test]
async fn lost_first_append_credit_still_repairs_at_normal_capacity_boundary() {
    let mut cfg = InboxProducerConfig::new(BackendId("model".into()));
    cfg.max_inbox_entries = 129;
    let (store, _, p) = setup(cfg);
    let pause = store.pause_after("document.replace", ":canonical:", 7);
    let creator = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("lost-credit")).await }
    });
    pause.reached().await;
    creator.abort();
    let _ = creator.await;
    let key = layout()
        .canonical_admission(&scope(), &InputId("lost-credit".into()))
        .unwrap();
    let canonical = store.model.read_document(&key).await.unwrap().unwrap();
    assert_eq!(canonical.value["append_credits"], 0);
    assert_eq!(canonical.value["position"], serde_json::Value::Null);
    store.lose_write(1); // lose the subsequent repair-budget reservation reply too.
    for _ in 0..8 {
        p.repair(ShardId(42)).await.unwrap();
    }
    assert!(matches!(
        p.admission_decision(&scope(), &InputId("lost-credit".into()))
            .await
            .unwrap(),
        Some(AdmitOutcome::Duplicate(_))
    ));
}

#[tokio::test]
async fn closure_returns_completed_pages_under_ordinary_document_latency() {
    use bcs_service_api::port::repo::distributed::session::{
        Checkpoint, CheckpointEnvelope, RecoveryMetadata,
    };
    let (store, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    for i in 0..16 {
        p.admit(request(&format!("closure-{i}"))).await.unwrap();
    }
    let owner = SessionOwnership {
        sid: Sid(SID.into()),
        shard: ShardId(42),
        owner: ShardOwner {
            instance: Instance {
                node: "worker".into(),
                incarnation: "one".into(),
            },
            epoch: WorkerEpoch(1),
        },
    };
    let envelope = CheckpointEnvelope {
        owner: owner.owner.clone(),
        checkpoint: Checkpoint {
            format_version: 1,
            commit_seq: CommitSeq(0),
            consumed: None,
            tasks: Default::default(),
            runner_state: None,
            dispatch_seq: Default::default(),
            deadlines: vec![],
            view_drafts: Default::default(),
            last_event_id: None,
            outbox: None,
        },
        recovery: RecoveryMetadata {
            takeover_streak: 0,
            last_attempt: None,
            quarantined: false,
            last_active_at: TimestampMillis(100),
        },
    };
    store
        .model
        .create_document(
            &layout().ckpt(&owner.sid).unwrap(),
            &serde_json::to_value(envelope).unwrap(),
        )
        .await
        .unwrap();
    p.observe(&owner).await.unwrap();
    let GateCloseOutcome::Closed(closure) = p
        .close_gate(&owner, 1, RequestId("close-slow".into()))
        .await
        .unwrap()
    else {
        panic!("close")
    };
    delay.millis.store(5, Ordering::Relaxed);
    let mut after = None;
    let mut total = 0;
    for _ in 0..8 {
        let page = p
            .closure_obligations(
                &closure,
                PageRequest {
                    after,
                    limit: NonZeroU32::new(128).unwrap(),
                },
            )
            .await
            .unwrap();
        assert!(!page.items.is_empty());
        total += page.items.len();
        let Some(next) = page.next else { break };
        after = Some(next);
    }
    assert_eq!(total, 16);
}

#[tokio::test]
async fn capacity_refusal_rechecks_a_canonical_winner_that_raced_the_initial_read() {
    let mut cfg = InboxProducerConfig::new(BackendId("model".into()));
    cfg.max_inbox_entries = 129;
    let (store, _, p) = setup(cfg);
    let pause = store.pause_after("document.read", ":canonical:", 1);
    let caller = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("capacity-race")).await }
    });
    pause.reached().await;
    p.admit(request("capacity-race")).await.unwrap();
    pause.resume();
    assert!(matches!(
        caller.await.unwrap().unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
}

#[tokio::test]
async fn discovery_persists_full_round_progress_with_many_ten_millisecond_rows() {
    use bcs_store_api::zset::ZSetStorePlugin;
    let (store, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    for index in 0..16 {
        p.admit(request(&format!("discover-{index}")))
            .await
            .unwrap();
    }
    store
        .model
        .remove(&layout().active(ShardId(42)), &[SID.into()])
        .await
        .unwrap();
    delay.millis.store(10, Ordering::Relaxed);
    for _ in 0..48 {
        let _ = p.discover(ShardId(42)).await;
        let root = store
            .model
            .read_document(&layout().admission_root(ShardId(42)))
            .await
            .unwrap()
            .unwrap();
        if root.value["discovery_health"].as_u64().unwrap() > 0 {
            break;
        }
    }
    let root = store
        .model
        .read_document(&layout().admission_root(ShardId(42)))
        .await
        .unwrap()
        .unwrap();
    assert!(
        root.value["discovery_health"].as_u64().unwrap() > 0,
        "complete discovery health must persist; scan={:?}",
        store
            .model
            .read_document(&layout().admission_scan(ShardId(42), "discovery").unwrap())
            .await
            .unwrap()
    );
    assert_eq!(
        p.due(
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
async fn slow_pending_retry_cannot_consume_every_forward_scan_turn() {
    use bcs_store_api::zset::ZSetStorePlugin;
    let (store, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    p.admit(request("slow-first")).await.unwrap();
    let mut healthy = request("healthy-second");
    let sid = Sid("s0042.other".into());
    let tid = Tid("s0042.other.task".into());
    healthy.input.sid = sid.clone();
    healthy.input.tid = tid.clone();
    healthy.create_session.as_mut().unwrap().sid = sid.clone();
    healthy.create_task.as_mut().unwrap().sid = sid.clone();
    healthy.create_task.as_mut().unwrap().tid = tid;
    p.admit(healthy).await.unwrap();
    store
        .model
        .remove(&layout().active(ShardId(42)), &[SID.into(), sid.0.clone()])
        .await
        .unwrap();
    delay.millis.store(10, Ordering::Relaxed);
    *delay.slow_key.lock().unwrap() = Some((
        layout()
            .canonical_admission(&scope(), &InputId("slow-first".into()))
            .unwrap(),
        100,
    ));
    for _ in 0..8 {
        let _ = p.discover(ShardId(42)).await;
    }
    assert!(
        p.due(
            ShardId(42),
            TimestampMillis(100),
            PageRequest {
                after: None,
                limit: NonZeroU32::new(128).unwrap()
            }
        )
        .await
        .unwrap()
        .items
        .contains(&sid)
    );
}
