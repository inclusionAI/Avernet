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
        Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        ),
        Arc::new(bcs_dist_projection::OutputProjectionService),
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
async fn eighty_millisecond_reads_survive_sliced_discovery_and_cancelled_polls() {
    let (_, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    p.admit(request("slow-history")).await.unwrap();
    delay.millis.store(80, Ordering::Relaxed);
    let page = PageRequest {
        after: None,
        limit: NonZeroU32::new(128).unwrap(),
    };
    // Cancellation must retain the original in-flight native read in its slot.
    assert!(
        tokio::time::timeout(
            Duration::from_millis(10),
            p.enumerate_pending(ShardId(42), page.clone())
        )
        .await
        .is_err()
    );
    let mut next = page;
    let mut found = false;
    for _ in 0..20 {
        let start = std::time::Instant::now();
        let result = p
            .enumerate_pending(ShardId(42), next.clone())
            .await
            .unwrap();
        assert!(
            start.elapsed() < Duration::from_millis(160),
            "poll slice was not bounded"
        );
        found |= result.items.contains(&Sid(SID.into()));
        if found {
            break;
        }
        assert!(
            result.next.is_some(),
            "pending read must not claim complete history"
        );
        next.after = result.next;
    }
    assert!(
        found,
        "healthy 80ms intent read must eventually be discovered"
    );
}

#[tokio::test]
async fn eighty_millisecond_repair_resumes_instead_of_restarting_the_prefix() {
    let (store, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    store.lose_write(1);
    assert!(p.admit(request("slow-durable-repair")).await.is_err());
    delay.millis.store(80, Ordering::Relaxed);
    let mut completed = false;
    for _ in 0..100 {
        let start = std::time::Instant::now();
        let _ = p.repair(ShardId(42)).await;
        assert!(
            start.elapsed() < Duration::from_millis(160),
            "repair poll must yield"
        );
        if matches!(
            p.admission_decision(&scope(), &InputId("slow-durable-repair".into()))
                .await
                .unwrap(),
            Some(AdmitOutcome::Duplicate(_))
        ) {
            completed = true;
            break;
        }
    }
    assert!(
        completed,
        "repair must retain multi-IO progress across 100ms polls"
    );
}

#[tokio::test]
async fn pending_history_token_reconstructs_the_same_fixed_cut_after_provider_restart() {
    let cfg = InboxProducerConfig::new(BackendId("model".into()));
    let (store, delay, p) = setup(cfg.clone());
    p.admit(request("original-cut")).await.unwrap();
    delay.millis.store(80, Ordering::Relaxed);
    let pending = p
        .enumerate_pending(
            ShardId(42),
            PageRequest {
                after: None,
                limit: NonZeroU32::new(128).unwrap(),
            },
        )
        .await
        .unwrap();
    assert!(pending.items.is_empty());
    let token = pending.next.unwrap();
    let decoded: serde_json::Value =
        serde_json::from_str(token.0.strip_prefix("scan-poll-v1:").unwrap()).unwrap();
    assert_eq!(decoded["cut"], 1);
    drop(p);
    delay.millis.store(0, Ordering::Relaxed);
    let restarted = provider(store, delay.clone(), cfg);
    // Append history after the old round fixed its cut; it must not extend it.
    let _ = restarted.repair(ShardId(42)).await;
    let _ = restarted.discover(ShardId(42)).await;
    restarted.admit(request("new-tail")).await.unwrap();
    delay.millis.store(80, Ordering::Relaxed);
    let mut page = PageRequest {
        after: Some(token),
        limit: NonZeroU32::new(128).unwrap(),
    };
    for turn in 0..10 {
        let result = restarted
            .enumerate_pending(ShardId(42), page.clone())
            .await
            .unwrap();
        if !result.items.is_empty() {
            assert_eq!(result.items, vec![Sid(SID.into())]);
            assert!(result.next.is_none());
            return;
        }
        page.after = result.next;
        assert!(page.after.is_some(), "unfinished restart turn {turn}");
    }
    panic!("restarted discovery did not finish its fixed cut");
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
            deadline_history: Default::default(),
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
    let mut raw = serde_json::to_value(envelope).unwrap();
    raw["control"] = serde_json::json!({"revision":1,"mode":{"kind":"quarantined","isolation":{"operation_id":"close-slow","reason":"test","head":0,"failed_attempt":"attempt","closure":null,"completion":null}},"active_attempt":null,"last_business_progress":0,"last_recovery":null});
    store
        .model
        .create_document(&layout().ckpt(&owner.sid).unwrap(), &raw)
        .await
        .unwrap();
    p.observe(&owner).await.unwrap();
    let GateCloseOutcome::Closed(closure) = p
        .close_gate(
            bcs_service_api::port::repo::distributed::control::ControlGuard {
                owner: owner.clone(),
                expected_head: CommitSeq(0),
                expected_revision: 1,
            },
            1,
            RequestId("close-slow".into()),
        )
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

#[tokio::test]
async fn reader_accepts_normal_eighty_millisecond_authoritative_reads() {
    let (_, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    p.admit(request("reader-realistic-rtt")).await.unwrap();
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    delay.millis.store(80, Ordering::Relaxed);
    let batch = feed
        .next(
            ReadLimits {
                max_sessions: NonZeroU32::MIN,
                max_entries: NonZeroU32::MIN,
                max_bytes: NonZeroU32::new(128 * 1024).unwrap(),
            },
            Duration::ZERO,
        )
        .await
        .unwrap();
    assert!(batch.failures.is_empty(), "{:?}", batch.failures);
    assert_eq!(batch.sessions.len(), 1);
    assert_eq!(batch.sessions[0].entries.len(), 1);
}

#[tokio::test]
async fn cancelling_a_read_keeps_its_watch_and_undelivered_input() {
    let (_, delay, p) = setup(InboxProducerConfig::new(BackendId("model".into())));
    p.admit(request("cancel-reader")).await.unwrap();
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    delay.millis.store(80, Ordering::Relaxed);
    let limits = ReadLimits {
        max_sessions: NonZeroU32::MIN,
        max_entries: NonZeroU32::MIN,
        max_bytes: NonZeroU32::new(128 * 1024).unwrap(),
    };
    assert!(
        tokio::time::timeout(
            Duration::from_millis(20),
            feed.next(limits.clone(), Duration::ZERO)
        )
        .await
        .is_err()
    );
    delay.millis.store(0, Ordering::Relaxed);
    let batch = feed.next(limits, Duration::ZERO).await.unwrap();
    assert!(batch.failures.is_empty());
    assert_eq!(batch.sessions.len(), 1);
    assert_eq!(
        batch.sessions[0].entries[0].input_id,
        InputId("cancel-reader".into())
    );
}
