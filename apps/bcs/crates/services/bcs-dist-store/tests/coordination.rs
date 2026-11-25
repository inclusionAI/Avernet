#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "contract assertions"
)]
use bcs_dist_model::*;
use bcs_dist_store::{coordination::CoordinationStore, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::*, ownership::*, session::*};
use bcs_store_api::{Expiry, WriteCondition, document::DocumentStorePlugin};
use bcs_test_support::store_model::Model;
use serde_json::json;
use std::{sync::Arc, time::Duration};
fn instance(name: &str) -> Instance {
    Instance {
        node: name.into(),
        incarnation: format!("{name}-1"),
    }
}
fn setup() -> (Arc<Model>, CoordinationStore) {
    let model = Arc::new(Model::default());
    let store = CoordinationStore::new(
        model.clone(),
        model.clone(),
        model.clone(),
        KeyLayout::new("test", "worker").unwrap(),
    )
    .unwrap();
    (model, store)
}
fn initial() -> Checkpoint {
    Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(0),
        consumed: None,
        tasks: vec![TaskProgress {
            steps: 0,
            tid: Tid("s0042.session.task".into()),
            phase: TaskPhase::Running,
            input: Payload::Inline(json!({})),
            result_ref: None,
            output_limits: PHASE_ONE_OUTPUT_LIMITS,
            output_usage: OutputUsage::default(),
            publication: SnapshotProgress::default(),
            finalization: None,
        }]
        .into(),
        runner_state: Some(TaskRunnerState {
            tid: Tid("s0042.session.task".into()),
            state: RunnerState {
                format_version: 1,
                value: json!({"a":1,"b":2}),
            },
        }),
        dispatch_seq: Default::default(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: Default::default(),
        last_event_id: None,
        feed_publications: Vec::new(),
        outbox: None,
    }
}
async fn register(s: &CoordinationStore, name: &str) -> MembershipLease {
    s.register(
        instance(name),
        RuntimeRole::Worker,
        format!("http://{name}.example.test:8080"),
        Duration::from_secs(3),
    )
    .await
    .unwrap()
}
async fn claim(s: &CoordinationStore, name: &str, epoch: Option<u64>) -> ShardClaim {
    match s
        .claim(ClaimShard {
            shard: ShardId(42),
            me: instance(name),
            expected_epoch: epoch.map(WorkerEpoch),
            request_id: RequestId(format!("claim-{name}")),
        })
        .await
        .unwrap()
    {
        LeaseClaim::Claimed(c) | LeaseClaim::AlreadyClaimed(c) => c,
        other => panic!("{other:?}"),
    }
}
fn sid() -> Sid {
    Sid("s0042.session".into())
}
async fn acquire(s: &CoordinationStore, c: &ShardClaim) -> AcquiredSession {
    match s.acquire(&sid(), c, initial()).await.unwrap() {
        AcquireOutcome::Acquired(x) => x,
        x => panic!("{x:?}"),
    }
}
fn commit(session: &AcquiredSession, seq: u64) -> CommitRequest {
    let mut candidate = session.envelope.checkpoint.clone();
    candidate.commit_seq = CommitSeq(seq);
    let id = CommitId(format!("commit-{seq}"));
    let digest = Digest(format!("digest-{seq}"));
    candidate.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: id.clone(),
        source_commit: CommitSeq(seq),
        content_digest: digest.clone(),
        effects: vec![],
    });
    CommitRequest {
        purpose: CommitPurpose::Ordinary {
            control_revision: 0,
        },
        owner: session.ownership.clone(),
        expected_commit_seq: CommitSeq(seq - 1),
        commit_id: id,
        content_digest: digest,
        candidate,
    }
}
#[tokio::test]
async fn competing_claims_have_one_winner_and_unknown_membership_is_not_dead() {
    let (_, s) = setup();
    register(&s, "a").await;
    register(&s, "b").await;
    let a = ClaimShard {
        shard: ShardId(42),
        me: instance("a"),
        expected_epoch: None,
        request_id: RequestId("a".into()),
    };
    let mut b = a.clone();
    b.me = instance("b");
    b.request_id = RequestId("b".into());
    let (x, y) = tokio::join!(s.claim(a), s.claim(b));
    assert_eq!(
        [x.unwrap(), y.unwrap()]
            .iter()
            .filter(|x| matches!(x, LeaseClaim::Claimed(_)))
            .count(),
        1
    );
    assert_eq!(
        s.liveness(&instance("not-registered")).await.unwrap(),
        Liveness::Unknown
    );
}
#[tokio::test]
async fn sealing_preserves_state_and_rejects_old_full_checkpoint() {
    let (_, s) = setup();
    let lease = register(&s, "a").await;
    register(&s, "b").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    assert!(matches!(
        s.commit(commit(&session, 1)).await.unwrap(),
        CommitOutcome::Committed(_)
    ));
    s.retire(&lease).await.unwrap();
    let b = claim(&s, "b", Some(1)).await;
    let recovered = acquire(&s, &b).await;
    assert_eq!(recovered.envelope.checkpoint.commit_seq, CommitSeq(1));
    assert_eq!(
        recovered.envelope.checkpoint.runner_state,
        session.envelope.checkpoint.runner_state
    );
    assert_eq!(
        s.commit(commit(&session, 1)).await.unwrap(),
        CommitOutcome::Fenced
    );
}
#[tokio::test]
async fn control_versions_and_consumed_only_commits_do_not_reset_recovery_evidence() {
    let (_, s) = setup();
    register(&s, "a").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    s.record_recovery_attempt(&session.ownership, CommitSeq(0), RequestId("replay".into()))
        .await
        .unwrap();
    assert!(matches!(
        s.commit(commit(&session, 1)).await.unwrap(),
        CommitOutcome::Committed(_)
    ));
    assert_eq!(
        s.load(&sid())
            .await
            .unwrap()
            .unwrap()
            .recovery
            .takeover_streak,
        1
    );
}
#[tokio::test]
async fn handoff_defers_source_then_transfers_without_resetting_checkpoint() {
    let (_, s) = setup();
    register(&s, "a").await;
    register(&s, "b").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    let request = HandoffRequest {
        request_id: RequestId("h1".into()),
        from: a.clone(),
        target: instance("b"),
        grace: Duration::from_secs(30),
    };
    let HandoffOutcome::Accepted(record) = s.handoff(request.clone()).await.unwrap() else {
        panic!()
    };
    assert_eq!(
        s.handoff(request.clone()).await.unwrap(),
        HandoffOutcome::Accepted(record.clone())
    );
    assert!(matches!(
        s.acquire(&sid(), &record.claim, initial()).await.unwrap(),
        AcquireOutcome::Deferred { .. }
    ));
    assert!(matches!(
        s.commit(commit(&session, 1)).await.unwrap(),
        CommitOutcome::Committed(_)
    ));
    assert_eq!(
        s.handoff_session(&session.ownership, &record.claim, &request.request_id)
            .await
            .unwrap(),
        SessionTransferOutcome::Transferred
    );
    assert_eq!(
        acquire(&s, &record.claim)
            .await
            .envelope
            .checkpoint
            .commit_seq,
        CommitSeq(1)
    );
    assert!(
        s.source_done(&record.claim, &request.request_id)
            .await
            .unwrap()
    );
    assert!(
        s.clear_handoff(&record.claim, &request.request_id)
            .await
            .unwrap()
    );
    assert_eq!(
        s.current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .claim
            .owner
            .epoch,
        WorkerEpoch(2)
    );
}
#[tokio::test]
async fn failed_receiver_is_replaced_and_old_handoff_removed_atomically() {
    let (_, s) = setup();
    register(&s, "a").await;
    let blease = register(&s, "b").await;
    register(&s, "c").await;
    let a = claim(&s, "a", None).await;
    acquire(&s, &a).await;
    let request = HandoffRequest {
        request_id: RequestId("h".into()),
        from: a,
        target: instance("b"),
        grace: Duration::from_secs(30),
    };
    s.handoff(request).await.unwrap();
    s.retire(&blease).await.unwrap();
    let c = claim(&s, "c", Some(2)).await;
    assert!(
        s.current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .handoff
            .is_none()
    );
    assert_eq!(acquire(&s, &c).await.ownership.owner, c.owner);
}
#[tokio::test]
async fn unknown_commit_is_confirmed_by_identity_not_version() {
    let (m, s) = setup();
    register(&s, "a").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    let request = commit(&session, 1);
    m.lose_next_response();
    assert!(matches!(
        s.commit(request.clone()).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    assert!(matches!(
        s.resolve_commit(&request.owner, &request.commit_id, &request.content_digest)
            .await
            .unwrap(),
        CommitResolution::Committed(_)
    ));
    assert_eq!(
        s.resolve_commit(
            &request.owner,
            &request.commit_id,
            &Digest("different".into())
        )
        .await
        .unwrap(),
        CommitResolution::ContentConflict
    );
}
#[tokio::test]
async fn cached_commit_uses_one_write_and_missing_known_checkpoint_never_recreates() {
    let (m, s) = setup();
    register(&s, "a").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    let key = KeyLayout::new("test", "worker")
        .unwrap()
        .ckpt(&sid())
        .unwrap();
    use bcs_store_api::json::{JsonMutation, JsonStorePlugin};
    m.mutate_json(
        &key,
        WriteCondition::Any,
        JsonMutation::Delete,
        Expiry::Persistent,
    )
    .await
    .unwrap();
    assert!(s.commit(commit(&session, 1)).await.is_err());
    assert!(s.acquire(&sid(), &a, initial()).await.is_err());
    assert!(s.acquire(&sid(), &a, initial()).await.is_err());
    assert!(m.read_document(&key).await.unwrap().is_none());
}

#[tokio::test]
async fn separate_workers_cannot_confirm_from_a_stale_cached_receipt() {
    let (m, a) = setup();
    let b = CoordinationStore::new(
        m.clone(),
        m.clone(),
        m.clone(),
        KeyLayout::new("test", "worker").unwrap(),
    )
    .unwrap();
    let lease = register(&a, "a").await;
    register(&b, "b").await;
    let old = claim(&a, "a", None).await;
    let session = acquire(&a, &old).await;
    let request = commit(&session, 1);
    assert!(matches!(
        a.commit(request.clone()).await.unwrap(),
        CommitOutcome::Committed(_)
    ));
    a.retire(&lease).await.unwrap();
    let new = claim(&b, "b", Some(1)).await;
    acquire(&b, &new).await;
    assert_eq!(a.commit(request).await.unwrap(), CommitOutcome::Fenced);
}

#[tokio::test]
async fn same_request_different_target_is_rejected_and_late_handoff_cannot_downgrade() {
    let (_, s) = setup();
    register(&s, "a").await;
    let blease = register(&s, "b").await;
    register(&s, "c").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    let h = HandoffRequest {
        request_id: RequestId("h".into()),
        from: a,
        target: instance("b"),
        grace: Duration::from_secs(30),
    };
    let HandoffOutcome::Accepted(b) = s.handoff(h.clone()).await.unwrap() else {
        panic!()
    };
    let mut changed = h.clone();
    changed.target = instance("c");
    assert!(s.handoff(changed).await.is_err());
    s.retire(&blease).await.unwrap();
    let c = claim(&s, "c", Some(2)).await;
    acquire(&s, &c).await;
    assert_eq!(
        s.handoff_session(&session.ownership, &b.claim, &h.request_id)
            .await
            .unwrap(),
        SessionTransferOutcome::Fenced
    );
    assert_eq!(s.load(&sid()).await.unwrap().unwrap().owner, c.owner);
}

#[tokio::test]
async fn source_done_cleanup_does_not_collide_with_next_epoch() {
    let (_, s) = setup();
    register(&s, "a").await;
    let blease = register(&s, "b").await;
    register(&s, "c").await;
    let a = claim(&s, "a", None).await;
    let h = HandoffRequest {
        request_id: RequestId("h".into()),
        from: a,
        target: instance("b"),
        grace: Duration::from_secs(30),
    };
    let HandoffOutcome::Accepted(b) = s.handoff(h.clone()).await.unwrap() else {
        panic!()
    };
    assert!(s.source_done(&b.claim, &h.request_id).await.unwrap());
    assert!(s.clear_handoff(&b.claim, &h.request_id).await.unwrap());
    s.retire(&blease).await.unwrap();
    assert_eq!(claim(&s, "c", Some(2)).await.owner.epoch, WorkerEpoch(3));
}

#[tokio::test]
async fn unknown_control_write_is_not_proof_of_a_business_commit() {
    let (m, s) = setup();
    register(&s, "a").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    m.lose_next_response();
    assert!(
        s.record_recovery_attempt(&session.ownership, CommitSeq(0), RequestId("r".into()))
            .await
            .is_err()
    );
    assert_eq!(
        s.resolve_commit(
            &session.ownership,
            &CommitId("future".into()),
            &Digest("digest".into())
        )
        .await
        .unwrap(),
        CommitResolution::OutcomeUnknown
    );
    let RecoveryUpdate::Recorded { metadata } = s
        .record_recovery_attempt(&session.ownership, CommitSeq(0), RequestId("r".into()))
        .await
        .unwrap()
    else {
        panic!()
    };
    assert_eq!(metadata.takeover_streak, 1);
}

#[tokio::test]
async fn lost_timer_does_not_restart_handoff_wait() {
    let (m, s) = setup();
    register(&s, "a").await;
    register(&s, "b").await;
    let a = claim(&s, "a", None).await;
    acquire(&s, &a).await;
    let h = HandoffRequest {
        request_id: RequestId("h".into()),
        from: a,
        target: instance("b"),
        grace: Duration::from_secs(30),
    };
    let HandoffOutcome::Accepted(b) = s.handoff(h.clone()).await.unwrap() else {
        panic!()
    };
    let recovered = CoordinationStore::new(
        m.clone(),
        m.clone(),
        m,
        KeyLayout::new("test", "worker").unwrap(),
    )
    .unwrap();
    assert!(matches!(
        recovered.handoff(h).await.unwrap(),
        HandoffOutcome::Accepted(_)
    ));
    assert_eq!(
        acquire(&recovered, &b.claim).await.ownership.owner,
        b.claim.owner
    );
    assert!(
        recovered
            .current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .handoff
            .is_none()
    );
}

#[tokio::test]
async fn recovery_attempt_ids_remain_idempotent_across_other_attempts_at_same_head() {
    let (_, s) = setup();
    register(&s, "a").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    for id in ["r1", "r2", "r1"] {
        s.record_recovery_attempt(&session.ownership, CommitSeq(0), RequestId(id.into()))
            .await
            .unwrap();
    }
    assert_eq!(
        s.load(&sid())
            .await
            .unwrap()
            .unwrap()
            .recovery
            .takeover_streak,
        2
    );
}

#[tokio::test]
async fn rejected_registration_does_not_poison_other_role_discovery() {
    let (_, s) = setup();
    register(&s, "a").await;
    assert!(
        s.register(
            instance("a"),
            RuntimeRole::Connector,
            "http://a.example.test:8080".into(),
            Duration::from_secs(3)
        )
        .await
        .is_err()
    );
    let page = s
        .members(
            RuntimeRole::Connector,
            PageRequest {
                after: None,
                limit: std::num::NonZeroU32::new(10).unwrap(),
            },
        )
        .await
        .unwrap();
    assert!(page.items.is_empty());
}

#[tokio::test]
async fn unknown_checkpoint_format_is_rejected_before_sealing() {
    let (m, s) = setup();
    register(&s, "a").await;
    let a = claim(&s, "a", None).await;
    let mut envelope = acquire(&s, &a).await.envelope;
    envelope.checkpoint.format_version = 99;
    let key = KeyLayout::new("test", "worker")
        .unwrap()
        .ckpt(&sid())
        .unwrap();
    let old = m.read_document(&key).await.unwrap().unwrap();
    m.replace_document(&key, old.version, &serde_json::to_value(envelope).unwrap())
        .await
        .unwrap();
    assert!(matches!(
        s.load(&sid()).await,
        Err(StoreError::UnsupportedFormat { format: 99 })
    ));
    assert!(matches!(
        s.acquire(&sid(), &a, initial()).await,
        Err(StoreError::UnsupportedFormat { format: 99 })
    ));
}

#[tokio::test]
async fn unsupported_checkpoint_layout_is_classified_before_typed_decode() {
    for (format, runner) in [
        (99, json!({"future_state": true})),
        (1, json!({"format_version": 1, "value": {"legacy": true}})),
    ] {
        let (model, store) = setup();
        register(&store, "a").await;
        let owner = claim(&store, "a", None).await;
        let envelope = acquire(&store, &owner).await.envelope;
        let key = KeyLayout::new("test", "worker").unwrap().ckpt(&sid()).unwrap();
        let old = model.read_document(&key).await.unwrap().unwrap();
        let mut raw = serde_json::to_value(envelope).unwrap();
        raw["checkpoint"]["format_version"] = json!(format);
        raw["checkpoint"]["runner_state"] = runner;
        assert!(matches!(
            model.replace_document(&key, old.version, &raw).await.unwrap(),
            bcs_store_api::WriteOutcome::Applied { .. }
        ));
        let before = model.read_document(&key).await.unwrap().unwrap();
        assert!(matches!(
            store.load(&sid()).await,
            Err(StoreError::UnsupportedFormat { format: found }) if found == format
        ));
        assert!(matches!(
            store.acquire(&sid(), &owner, initial()).await,
            Err(StoreError::UnsupportedFormat { format: found }) if found == format
        ));
        let after = model.read_document(&key).await.unwrap().unwrap();
        assert_eq!(after.version, before.version);
        assert_eq!(after.value, before.value);
    }
}

struct DelayedDocuments {
    model: Arc<Model>,
    armed: std::sync::atomic::AtomicBool,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
    reads: std::sync::atomic::AtomicUsize,
    writes: std::sync::atomic::AtomicUsize,
}
impl DelayedDocuments {
    fn new(model: Arc<Model>) -> Self {
        Self {
            model,
            armed: false.into(),
            entered: Default::default(),
            release: Default::default(),
            reads: 0.into(),
            writes: 0.into(),
        }
    }
}
impl bcs_store_api::StorePlugin for DelayedDocuments {
    fn pool_observation(&self) -> Option<bcs_store_api::StorePoolObservation> {
        self.model.pool_observation()
    }
    fn supports(&self, c: bcs_store_api::StoreCapability) -> bool {
        c == bcs_store_api::StoreCapability::DocumentCas
    }
}
#[async_trait::async_trait]
impl DocumentStorePlugin for DelayedDocuments {
    async fn read_document(
        &self,
        key: &str,
    ) -> bcs_store_api::StoreResult<Option<bcs_store_api::Versioned<serde_json::Value>>> {
        self.reads.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        self.model.read_document(key).await
    }
    async fn create_document(
        &self,
        key: &str,
        value: &serde_json::Value,
    ) -> bcs_store_api::StoreResult<bcs_store_api::WriteOutcome> {
        self.writes
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        self.model.create_document(key, value).await
    }
    async fn replace_document(
        &self,
        key: &str,
        v: bcs_store_api::Version,
        value: &serde_json::Value,
    ) -> bcs_store_api::StoreResult<bcs_store_api::WriteOutcome> {
        self.writes
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        if self.armed.swap(false, std::sync::atomic::Ordering::SeqCst) {
            self.entered.notify_one();
            self.release.notified().await;
        }
        self.model.replace_document(key, v, value).await
    }
}
#[tokio::test]
async fn delayed_old_commit_cannot_overwrite_a_new_seal() {
    let (m, b) = setup();
    let docs = Arc::new(DelayedDocuments::new(m.clone()));
    let a = Arc::new(
        CoordinationStore::new(
            docs.clone(),
            m.clone(),
            m,
            KeyLayout::new("test", "worker").unwrap(),
        )
        .unwrap(),
    );
    let lease = register(&a, "a").await;
    register(&b, "b").await;
    let claim_a = claim(&a, "a", None).await;
    let session = acquire(&a, &claim_a).await;
    docs.armed.store(true, std::sync::atomic::Ordering::SeqCst);
    let writer = a.clone();
    let pending = tokio::spawn(async move { writer.commit(commit(&session, 1)).await });
    docs.entered.notified().await;
    a.retire(&lease).await.unwrap();
    let claim_b = claim(&b, "b", Some(1)).await;
    acquire(&b, &claim_b).await;
    docs.release.notify_one();
    assert_eq!(pending.await.unwrap().unwrap(), CommitOutcome::Fenced);
    assert_eq!(
        b.load(&sid()).await.unwrap().unwrap().checkpoint.commit_seq,
        CommitSeq(0)
    );
}
#[tokio::test]
async fn delayed_seal_reloads_and_keeps_a_commit_that_won_first() {
    let (m, a) = setup();
    let docs = Arc::new(DelayedDocuments::new(m.clone()));
    let b = Arc::new(
        CoordinationStore::new(
            docs.clone(),
            m.clone(),
            m,
            KeyLayout::new("test", "worker").unwrap(),
        )
        .unwrap(),
    );
    let lease = register(&a, "a").await;
    register(&b, "b").await;
    let claim_a = claim(&a, "a", None).await;
    let session = acquire(&a, &claim_a).await;
    a.retire(&lease).await.unwrap();
    let claim_b = claim(&b, "b", Some(1)).await;
    docs.armed.store(true, std::sync::atomic::Ordering::SeqCst);
    let target = b.clone();
    let sealing = tokio::spawn(async move { target.acquire(&sid(), &claim_b, initial()).await });
    docs.entered.notified().await;
    assert!(matches!(
        a.commit(commit(&session, 1)).await.unwrap(),
        CommitOutcome::Committed(_)
    ));
    docs.release.notify_one();
    let AcquireOutcome::Acquired(result) = sealing.await.unwrap().unwrap() else {
        panic!()
    };
    assert_eq!(result.envelope.checkpoint.commit_seq, CommitSeq(1));
}
#[tokio::test]
async fn confirmed_version_makes_normal_commit_one_write_without_reads() {
    use std::sync::atomic::Ordering::SeqCst;
    let m = Arc::new(Model::default());
    let docs = Arc::new(DelayedDocuments::new(m.clone()));
    let s = CoordinationStore::new(
        docs.clone(),
        m.clone(),
        m,
        KeyLayout::new("test", "worker").unwrap(),
    )
    .unwrap();
    register(&s, "a").await;
    let a = claim(&s, "a", None).await;
    let session = acquire(&s, &a).await;
    docs.reads.store(0, SeqCst);
    docs.writes.store(0, SeqCst);
    assert!(matches!(
        s.commit(commit(&session, 1)).await.unwrap(),
        CommitOutcome::Committed(_)
    ));
    assert_eq!(docs.reads.load(SeqCst), 0);
    assert_eq!(docs.writes.load(SeqCst), 1);
}

#[tokio::test]
async fn fenced_acquisitions_do_not_exhaust_the_local_session_cache() {
    let (_, s) = setup();
    register(&s, "a").await;
    let mut stale = claim(&s, "a", None).await;
    stale.owner.epoch = WorkerEpoch(0);
    for n in 0..4100 {
        assert!(matches!(
            s.acquire(&Sid(format!("s0042.stale{n}")), &stale, initial())
                .await
                .unwrap(),
            AcquireOutcome::Fenced
        ));
    }
    let current = s.current(ShardId(42)).await.unwrap().unwrap().claim;
    assert!(matches!(
        s.acquire(&sid(), &current, initial()).await.unwrap(),
        AcquireOutcome::Acquired(_)
    ));
}

#[tokio::test]
async fn transfer_releases_source_cache_without_dropping_new_local_owner() {
    let m = Arc::new(Model::default());
    let docs = Arc::new(DelayedDocuments::new(m.clone()));
    let s = CoordinationStore::new(
        docs.clone(),
        m.clone(),
        m,
        KeyLayout::new("test", "worker").unwrap(),
    )
    .unwrap();
    register(&s, "a").await;
    register(&s, "b").await;
    let a = claim(&s, "a", None).await;
    let source = acquire(&s, &a).await;
    let id = RequestId("release-transfer".into());
    let HandoffOutcome::Accepted(record) = s
        .handoff(HandoffRequest {
            request_id: id.clone(),
            from: a,
            target: instance("b"),
            grace: Duration::from_secs(5),
        })
        .await
        .unwrap()
    else {
        panic!("handoff rejected")
    };
    s.handoff_session(&source.ownership, &record.claim, &id)
        .await
        .unwrap();
    s.release_local(&source.ownership).await.unwrap();
    // Target reacquires the same durable value, and a late source release must
    // leave its checkpoint version usable for a single-write business commit.
    let target = acquire(&s, &record.claim).await;
    s.release_local(&source.ownership).await.unwrap();
    docs.reads.store(0, std::sync::atomic::Ordering::SeqCst);
    assert!(matches!(
        s.commit(commit(&target, 1)).await.unwrap(),
        CommitOutcome::Committed(_)
    ));
    assert_eq!(docs.reads.load(std::sync::atomic::Ordering::SeqCst), 0);
}

#[tokio::test]
async fn deferred_observations_do_not_fill_receiver_cache() {
    let (m, s) = setup();
    register(&s, "a").await;
    register(&s, "b").await;
    let a = claim(&s, "a", None).await;
    let source = acquire(&s, &a).await;
    let layout = KeyLayout::new("test", "worker").unwrap();
    for n in 0..4100 {
        m.create_document(
            &layout.ckpt(&Sid(format!("s0042.protected{n}"))).unwrap(),
            &serde_json::to_value(&source.envelope).unwrap(),
        )
        .await
        .unwrap();
    }
    let HandoffOutcome::Accepted(record) = s
        .handoff(HandoffRequest {
            request_id: RequestId("large-handoff".into()),
            from: a,
            target: instance("b"),
            grace: Duration::from_secs(60),
        })
        .await
        .unwrap()
    else {
        panic!("handoff rejected")
    };
    for n in 0..4100 {
        assert!(matches!(
            s.acquire(
                &Sid(format!("s0042.protected{n}")),
                &record.claim,
                initial()
            )
            .await
            .unwrap(),
            AcquireOutcome::Deferred { .. }
        ));
    }
    assert!(matches!(
        s.acquire(&Sid("s0042.new".into()), &record.claim, initial())
            .await
            .unwrap(),
        AcquireOutcome::Acquired(_)
    ));
}

#[tokio::test]
async fn raw_deferred_and_fenced_observations_do_not_consume_receiver_capacity() {
    use bcs_service_api::port::repo::distributed::control::{
        ControlAcquireOutcome, SessionControlPort,
    };
    let (m, s) = setup();
    register(&s, "a").await;
    register(&s, "b").await;
    let a = claim(&s, "a", None).await;
    let source = acquire(&s, &a).await;
    let layout = KeyLayout::new("test", "worker").unwrap();
    for n in 0..2 {
        m.create_document(
            &layout.ckpt(&Sid(format!("s0042.protected{n}"))).unwrap(),
            &serde_json::to_value(&source.envelope).unwrap(),
        )
        .await
        .unwrap();
    }
    let receiver = CoordinationStore::with_config(
        m.clone(),
        m.clone(),
        m.clone(),
        layout.clone(),
        bcs_dist_store::coordination::CoordinationStoreConfig {
            max_cached_sessions: 1,
            max_handoff_timers: 1,
            ..Default::default()
        },
    )
    .unwrap();
    let HandoffOutcome::Accepted(record) = receiver
        .handoff(HandoffRequest {
            request_id: RequestId("handoff".into()),
            from: a,
            target: instance("b"),
            grace: Duration::from_secs(60),
        })
        .await
        .unwrap()
    else {
        panic!()
    };
    for n in 0..2 {
        assert!(matches!(
            receiver
                .acquire_control(&Sid(format!("s0042.protected{n}")), &record.claim)
                .await
                .unwrap(),
            ControlAcquireOutcome::Deferred { .. }
        ));
    }
    let mut future = source.envelope.clone();
    future.owner.epoch = WorkerEpoch(999);
    for n in 0..2 {
        let sid = Sid(format!("s0042.future{n}"));
        m.create_document(
            &layout.ckpt(&sid).unwrap(),
            &serde_json::to_value(&future).unwrap(),
        )
        .await
        .unwrap();
        assert!(matches!(
            receiver.acquire_control(&sid, &record.claim).await.unwrap(),
            ControlAcquireOutcome::Fenced
        ));
    }
    assert!(matches!(
        receiver
            .acquire(&Sid("s0042.new".into()), &record.claim, initial())
            .await
            .unwrap(),
        AcquireOutcome::Acquired(_)
    ));
}
