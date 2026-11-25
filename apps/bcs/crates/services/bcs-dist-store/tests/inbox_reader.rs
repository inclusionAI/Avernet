#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]
mod common;
use bcs_dist_model::*;
use bcs_dist_store::{inbox::*, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::*, inbox::*, session::*};
use bcs_store_api::{document::DocumentStorePlugin, stream::*};
use common::*;
use std::{num::NonZeroU32, ops::Bound, sync::Arc, time::Duration};
fn layout() -> KeyLayout {
    KeyLayout::new("test", "reader").unwrap()
}
fn provider(s: Arc<Recorder>) -> StoreInboxProducer {
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
        Arc::new(FixedClock(100)),
        InboxProducerConfig::new(BackendId("model".into())),
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
fn limits() -> ReadLimits {
    ReadLimits {
        max_sessions: NonZeroU32::new(8).unwrap(),
        max_entries: NonZeroU32::new(128).unwrap(),
        max_bytes: NonZeroU32::new(1024 * 1024).unwrap(),
    }
}
fn another(mut r: AdmitRequest) -> AdmitRequest {
    let sid = Sid("s0042.other".into());
    let tid = Tid("s0042.other.t".into());
    r.input.sid = sid.clone();
    r.input.tid = tid.clone();
    let s = r.create_session.as_mut().unwrap();
    s.sid = sid.clone();
    let t = r.create_task.as_mut().unwrap();
    t.sid = sid;
    t.tid = tid;
    r
}
#[tokio::test]
async fn unresolved_position_blocks_only_its_session_and_restarts_replay_uncommitted_work() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    p.admit(request("A")).await.unwrap();
    let pause = store.pause_after("stream.append", ":inbox:", 1);
    let pending = tokio::spawn({
        let p = p.clone();
        async move { p.admit(request("B")).await }
    });
    pause.reached().await;
    p.admit(request("C")).await.unwrap();
    p.admit(another(request("other"))).await.unwrap();
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    feed.watch(Sid("s0042.other".into()), None).await.unwrap();
    let first = feed.next(limits(), Duration::ZERO).await.unwrap();
    assert_eq!(first.sessions.len(), 2);
    let a = first.sessions.iter().find(|s| s.sid.0 == SID).unwrap();
    assert_eq!(
        a.entries
            .iter()
            .map(|i| i.input_id.0.as_str())
            .collect::<Vec<_>>(),
        vec!["A"]
    );
    assert!(first.hints.iter().any(|s| s.0 == SID));
    assert!(
        feed.next(limits(), Duration::ZERO)
            .await
            .unwrap()
            .sessions
            .is_empty()
    );
    pause.resume();
    pending.await.unwrap().unwrap();
    let next = feed.next(limits(), Duration::ZERO).await.unwrap();
    assert_eq!(
        next.sessions[0]
            .entries
            .iter()
            .map(|i| i.input_id.0.as_str())
            .collect::<Vec<_>>(),
        vec!["B", "C"]
    );
    drop(feed);
    let mut restarted = p.open(ShardId(42)).await.unwrap();
    restarted.watch(Sid(SID.into()), None).await.unwrap();
    assert_eq!(
        restarted
            .next(limits(), Duration::ZERO)
            .await
            .unwrap()
            .sessions[0]
            .entries
            .len(),
        3
    );
}
#[tokio::test]
async fn duplicate_physical_records_advance_coverage_without_business_inputs() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    p.admit(request("once")).await.unwrap();
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    let first = feed.next(limits(), Duration::ZERO).await.unwrap();
    assert_eq!(first.sessions[0].entries.len(), 1);
    let key = layout().inbox(&Sid(SID.into())).unwrap();
    let row = store
        .model
        .range(
            &key,
            Bound::Unbounded,
            Bound::Unbounded,
            NonZeroU32::MIN,
            false,
        )
        .await
        .unwrap()
        .entries
        .remove(0);
    store
        .model
        .append(&key, AppendId::Auto, &row.fields)
        .await
        .unwrap();
    let duplicate = feed.next(limits(), Duration::ZERO).await.unwrap();
    assert_eq!(duplicate.sessions.len(), 1);
    assert!(duplicate.sessions[0].entries.is_empty());
    assert_ne!(
        duplicate.sessions[0].read_through,
        first.sessions[0].read_through
    );
}
async fn persist_commit(store: &Recorder, consumed: Option<InboxPos>) -> CommitReceipt {
    let owner = ShardOwner {
        instance: Instance {
            node: "worker".into(),
            incarnation: "one".into(),
        },
        epoch: WorkerEpoch(1),
    };
    let commit_id = CommitId("commit".into());
    let digest = Digest("digest".into());
    let c = CheckpointEnvelope {
        owner: owner.clone(),
        checkpoint: Checkpoint {
            format_version: 1,
            commit_seq: CommitSeq(1),
            consumed: consumed.clone(),
            tasks: Default::default(),
            runner_state: None,
            dispatch_seq: Default::default(),
            deadlines: vec![],
            deadline_history: Default::default(),
            view_drafts: Default::default(),
            last_event_id: None,
            feed_publications: Vec::new(),
            outbox: Some(CommittedBatch {
                created_at: None,
                commit_id: commit_id.clone(),
                source_commit: CommitSeq(1),
                content_digest: digest.clone(),
                effects: vec![],
            }),
        },
        recovery: RecoveryMetadata {
            takeover_streak: 0,
            last_attempt: None,
            quarantined: false,
            last_active_at: TimestampMillis(100),
        },
    };
    let key = layout().ckpt(&Sid(SID.into())).unwrap();
    let doc = serde_json::to_value(c).unwrap();
    if let Some(old) = store.read_document(&key).await.unwrap() {
        store
            .replace_document(&key, old.version, &doc)
            .await
            .unwrap();
    } else {
        store.create_document(&key, &doc).await.unwrap();
    }
    CommitReceipt {
        sid: Sid(SID.into()),
        owner,
        commit_id,
        content_digest: digest,
        sequence: CommitSeq(1),
        consumed,
        proof: "caller-string-is-not-authority".into(),
    }
}
#[tokio::test]
async fn receipt_checks_persisted_coverage_and_reader_generation() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    p.admit(request("once")).await.unwrap();
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    let mut batch = feed.next(limits(), Duration::ZERO).await.unwrap();
    let batch = batch.sessions.remove(0);
    let insufficient = persist_commit(&store, None).await;
    assert!(
        p.confirm_committed(&batch.delivery, &insufficient)
            .await
            .is_err()
    );
    let proof = persist_commit(&store, Some(batch.read_through.clone())).await;
    let forged = DeliveryReceipt {
        backend: batch.delivery.backend.clone(),
        consumer_generation: batch.delivery.consumer_generation.clone(),
        opaque: "forged".into(),
    };
    assert!(p.confirm_committed(&forged, &proof).await.is_err());
    let mut bad = proof.clone();
    bad.content_digest = Digest("forged".into());
    assert!(p.confirm_committed(&batch.delivery, &bad).await.is_err());
    p.confirm_committed(&batch.delivery, &proof).await.unwrap();
    p.confirm_committed(&batch.delivery, &proof).await.unwrap();
    drop(feed);
    assert!(p.confirm_committed(&batch.delivery, &proof).await.is_err());
    let mut restarted = p.open(ShardId(42)).await.unwrap();
    restarted.watch(Sid(SID.into()), None).await.unwrap();
    assert!(
        restarted
            .next(limits(), Duration::ZERO)
            .await
            .unwrap()
            .sessions
            .is_empty()
    );
    let mut future = batch.read_through;
    future.token.value = "9999999999999-0".into();
    assert!(
        restarted
            .watch(Sid(SID.into()), Some(future))
            .await
            .is_err()
    );
}

#[tokio::test]
async fn one_corrupt_session_is_reported_without_poisoning_other_sessions() {
    let store = Arc::new(Recorder::default());
    let p = provider(store.clone());
    p.admit(request("corrupt")).await.unwrap();
    p.admit(another(request("healthy"))).await.unwrap();
    let key = layout()
        .admission(&scope(), &InputId("corrupt".into()))
        .unwrap();
    let original = store.read_document(&key).await.unwrap().unwrap();
    let mut bad = original.value.clone();
    bad["schema_version"] = 2.into();
    store
        .replace_document(&key, original.version, &bad)
        .await
        .unwrap();
    let mut feed = p.open(ShardId(42)).await.unwrap();
    feed.watch(Sid(SID.into()), None).await.unwrap();
    feed.watch(Sid("s0042.other".into()), None).await.unwrap();
    let result = feed.next(limits(), Duration::ZERO).await.unwrap();
    assert_eq!(result.failures.len(), 1);
    assert_eq!(result.failures[0].sid.0, SID);
    assert_eq!(result.sessions.len(), 1);
    assert_eq!(result.sessions[0].sid.0, "s0042.other");
    let bad = store.read_document(&key).await.unwrap().unwrap();
    store
        .replace_document(&key, bad.version, &original.value)
        .await
        .unwrap();
    // A caller must explicitly rebuild after classification/repair. No silent
    // cursor skip resumes a failed session from its uncommitted physical offset.
    assert_eq!(
        feed.next(limits(), Duration::ZERO)
            .await
            .unwrap()
            .failures
            .len(),
        1
    );
    feed.watch(Sid(SID.into()), None).await.unwrap();
    let result = feed.next(limits(), Duration::ZERO).await.unwrap();
    assert!(result.failures.is_empty());
    assert_eq!(result.sessions[0].entries[0].input_id.0, "corrupt");
}

#[tokio::test]
async fn reader_and_watch_capacity_is_shared_across_open_feeds() {
    let store = Arc::new(Recorder::default());
    let mut config = InboxProducerConfig::new(BackendId("model".into()));
    config.max_readers = 2;
    config.max_watches = 1;
    let p = StoreInboxProducer::new(
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
        config,
    )
    .unwrap();
    let mut a = p.open(ShardId(42)).await.unwrap();
    let mut b = p.open(ShardId(42)).await.unwrap();
    assert!(p.open(ShardId(42)).await.is_err());
    a.watch(Sid(SID.into()), None).await.unwrap();
    assert!(b.watch(Sid("s0042.other".into()), None).await.is_err());
    a.unwatch(&Sid(SID.into())).await.unwrap();
    b.watch(Sid("s0042.other".into()), None).await.unwrap();
    drop(a);
    assert!(p.open(ShardId(42)).await.is_ok());
}
