//! T1 checkpoint prototype against the real CoordinationStore and a test-only
//! plugin Model. Reconstructing a store is NOT a process/provider restart.
//! These characterize baseline primitives, not the eventual D0 proof protocol.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "prototype assertions")]
use bcs_dist_model::*;
use bcs_dist_store::{coordination::CoordinationStore, layout::KeyLayout};
use bcs_service_api::port::repo::distributed::{common::*, ownership::*, session::*};
use bcs_test_support::store_model::Model;
use serde_json::json;
use bcs_store_api::zset::*;
use std::{num::NonZeroU32, ops::Bound, sync::Arc, time::Duration};

fn store(model: &Arc<Model>) -> CoordinationStore {
    CoordinationStore::new(model.clone(), model.clone(), model.clone(),
        KeyLayout::new("t1", "checkpoint").unwrap()).unwrap()
}

fn initial() -> Checkpoint {
    Checkpoint {
        format_version: 1, commit_seq: CommitSeq(0), consumed: None,
        tasks: Default::default(), deadline_history: Default::default(),
        runner_state: Some(TaskRunnerState { tid: Tid("s0042.prototype.task".into()),
            state: RunnerState { format_version: 1, value: json!({}) } }),
        dispatch_seq: Default::default(), deadlines: vec![],
        view_drafts: Default::default(), last_event_id: None, outbox: None,
    }
}

async fn owner(s: &CoordinationStore, name: &str, previous: Option<u64>)
    -> (MembershipLease, ShardClaim)
{
    let me = Instance { node: name.into(), incarnation: format!("{name}-1") };
    let lease = s.register(me.clone(), RuntimeRole::Worker,
        format!("http://{name}.example.test"), Duration::from_secs(60)).await.unwrap();
    let outcome = s.claim(ClaimShard {
        shard: ShardId(42), me, expected_epoch: previous.map(WorkerEpoch),
        request_id: RequestId(format!("claim-{name}")),
    }).await.unwrap();
    let claim = match outcome {
        LeaseClaim::Claimed(c) | LeaseClaim::AlreadyClaimed(c) => c,
        other => panic!("claim: {other:?}"),
    };
    (lease, claim)
}

async fn acquire(s: &CoordinationStore, c: &ShardClaim) -> AcquiredSession {
    match s.acquire(&Sid("s0042.prototype".into()), c, initial()).await.unwrap() {
        AcquireOutcome::Acquired(a) => a,
        other => panic!("acquire: {other:?}"),
    }
}

fn candidate(a: &AcquiredSession, id: &str, tid: &str, generation: u64) -> CommitRequest {
    let expected = a.envelope.checkpoint.commit_seq;
    let mut next = a.envelope.checkpoint.clone();
    next.commit_seq = CommitSeq(expected.0 + 1);
    next.runner_state = Some(TaskRunnerState { tid: Tid(tid.into()),
        state: RunnerState { format_version: 1, value: json!({"tid": tid, "generation": generation,
            "accepted": true, "dispatch_pending": true}) } });
    let commit_id = CommitId(id.into());
    let content_digest = Digest(format!("digest-{id}"));
    next.outbox = Some(CommittedBatch { created_at: None,
        commit_id: commit_id.clone(), source_commit: next.commit_seq,
        content_digest: content_digest.clone(), effects: vec![],
    });
    CommitRequest { purpose: CommitPurpose::Ordinary { control_revision: 0 },
        owner: a.ownership.clone(), expected_commit_seq: expected,
        commit_id, content_digest, candidate: next }
}

#[tokio::test]
async fn competing_round_candidates_have_one_checkpoint_winner() {
    let model = Arc::new(Model::default());
    let s1 = store(&model);
    let (_, c) = owner(&s1, "worker-a", None).await;
    let a = acquire(&s1, &c).await;
    let s2 = store(&model);
    let b = acquire(&s2, &c).await;
    let (left, right) = tokio::join!(
        s1.commit(candidate(&a, "accept-a", "task-a", 1)),
        s2.commit(candidate(&b, "accept-b", "task-b", 1)));
    let results = [left.unwrap(), right.unwrap()];
    assert_eq!(results.iter().filter(|r| matches!(r, CommitOutcome::Committed(_))).count(), 1);
    assert_eq!(results.iter().filter(|r| matches!(r, CommitOutcome::CommitSeqConflict)).count(), 1);
    // A CAS loser is not yet a product BUSY response: that mapping belongs to T2.
    assert_eq!(s1.load(&a.ownership.sid).await.unwrap().unwrap().checkpoint.commit_seq, CommitSeq(1));
}

#[tokio::test]
async fn accepted_without_hint_is_readable_if_sid_is_already_known() {
    let model = Arc::new(Model::default());
    let s = store(&model);
    let (_, c) = owner(&s, "worker-a", None).await;
    let a = acquire(&s, &c).await;
    let request = candidate(&a, "accept-a", "task-a", 1);
    model.lose_next_response();
    assert!(matches!(s.commit(request.clone()).await, Err(StoreError::OutcomeUnknown { .. })));
    drop(s);
    let recovered = store(&model);
    assert!(matches!(recovered.resolve_commit(&a.ownership, &request.commit_id,
        &request.content_digest).await.unwrap(), CommitResolution::Committed(_)));
    let loaded = recovered.load(&a.ownership.sid).await.unwrap().unwrap();
    assert_eq!(loaded.checkpoint.runner_state.as_ref().unwrap().state.value["tid"], "task-a");
    assert_eq!(loaded.checkpoint.runner_state.as_ref().unwrap().state.value["dispatch_pending"], true);
    // This does not prove discovery of an unknown Sid or actual Runner dispatch.
}

#[tokio::test]
async fn unregistered_owner_cannot_create_checkpoint() {
    let model = Arc::new(Model::default());
    let s = store(&model);
    let fabricated = ShardClaim { shard: ShardId(42), owner: ShardOwner {
        instance: Instance { node: "missing".into(), incarnation: "missing-1".into() },
        epoch: WorkerEpoch(1),
    }};
    let sid = Sid("s0042.prototype".into());
    let result = s.acquire(&sid, &fabricated, initial()).await;
    assert!(!matches!(result, Ok(AcquireOutcome::Acquired(_))));
    assert!(s.load(&sid).await.unwrap().is_none());
}

#[tokio::test]
async fn fresh_checkpoint_requires_owner_and_takeover_fences_old_writer() {
    let model = Arc::new(Model::default());
    let s = store(&model);
    let sid = Sid("s0042.prototype".into());
    assert!(s.load(&sid).await.unwrap().is_none());
    let (lease, c) = owner(&s, "worker-a", None).await;
    let a = acquire(&s, &c).await;
    assert_eq!(a.envelope.checkpoint.commit_seq, CommitSeq(0));
    let request = candidate(&a, "accept-a", "task-a", 1);
    assert!(matches!(s.commit(request).await.unwrap(), CommitOutcome::Committed(_)));
    s.retire(&lease).await.unwrap();
    let (_, next) = owner(&s, "worker-b", Some(1)).await;
    let b = acquire(&s, &next).await;
    assert_eq!(b.envelope.checkpoint.runner_state.as_ref().unwrap().state.value["tid"], "task-a");
    assert_eq!(s.commit(candidate(&a, "late", "task-late", 1)).await.unwrap(), CommitOutcome::Fenced);
}

#[tokio::test]
async fn advancing_checkpoint_does_not_preserve_old_commit_receipt() {
    let model = Arc::new(Model::default());
    let s = store(&model);
    let (_, c) = owner(&s, "worker-a", None).await;
    let a = acquire(&s, &c).await;
    let first = candidate(&a, "accept-a", "task-a", 1);
    assert!(matches!(s.commit(first.clone()).await.unwrap(), CommitOutcome::Committed(_)));
    let current = acquire(&s, &c).await;
    assert!(matches!(s.commit(candidate(&current, "advance", "task-a", 1)).await.unwrap(), CommitOutcome::Committed(_)));
    assert_eq!(s.resolve_commit(&a.ownership, &first.commit_id, &first.content_digest).await.unwrap(),
        CommitResolution::OutcomeUnknown);
}

fn all_rows() -> ZSetRange {
    ZSetRange { min: Bound::Unbounded, max: Bound::Unbounded, after: None,
        limit: NonZeroU32::new(10).unwrap() }
}

#[tokio::test]
async fn independent_index_version_cannot_prove_two_sid_commit_coverage() {
    let model = Arc::new(Model::default());
    let s = store(&model);
    let (_, c) = owner(&s, "worker-a", None).await;
    let a = acquire(&s, &c).await;
    let b = match s.acquire(&Sid("s0042.second".into()), &c, initial()).await.unwrap() {
        AcquireOutcome::Acquired(b) => b,
        other => panic!("{other:?}"),
    };
    let (ra, rb) = tokio::join!(s.commit(candidate(&a, "accept-a", "task-a", 1)),
        s.commit(candidate(&b, "accept-b", "task-b", 1)));
    assert!(matches!(ra.unwrap(), CommitOutcome::Committed(_)));
    assert!(matches!(rb.unwrap(), CommitOutcome::Committed(_)));
    // Delay A's indexing, but advance the independently versioned collection via B.
    model.add("public-index", &[ZSetEntry { member: "task-b".into(), score: 2.0 }],
        ZAddMode::Upsert).await.unwrap();
    let page = model.snapshot("public-index", &all_rows()).await.unwrap();
    assert!(page.version.is_some());
    assert_eq!(page.entries.iter().map(|e| e.member.as_str()).collect::<Vec<_>>(), vec!["task-b"]);
    assert_eq!(s.load(&a.ownership.sid).await.unwrap().unwrap().checkpoint.commit_seq, CommitSeq(1));
    // An index version is not a coverage certificate. No List implementation or
    // coverage barrier exists here, so this page must NOT be presented as total=1.
}

#[tokio::test]
async fn checkpoint_commit_does_not_create_a_discovery_schedule() {
    let model = Arc::new(Model::default());
    let s = store(&model);
    let (_, c) = owner(&s, "worker-a", None).await;
    let a = acquire(&s, &c).await;
    assert!(matches!(s.commit(candidate(&a, "accept-a", "task-a", 1)).await.unwrap(),
        CommitOutcome::Committed(_)));
    let layout = KeyLayout::new("t1", "checkpoint").unwrap();
    let key = layout.sched(a.ownership.shard);
    assert!(model.range(&key, &all_rows()).await.unwrap().is_empty());
    assert!(model.range(&layout.active(a.ownership.shard), &all_rows()).await.unwrap().is_empty());
    // A pre-registered discovery intent or atomic directory protocol is still
    // needed; knowing the Sid in another test does not prove automatic discovery.
}

/// A fresh consumer still cannot recover historical evidence using this API
/// alone. D0 must test the chosen retained-proof protocol, not change this API
/// merely to make a hypothetical historical lookup succeed.
#[tokio::test]
async fn baseline_receipt_lookup_after_restart_needs_retained_proof_protocol() {
    let model = Arc::new(Model::default());
    let s = store(&model);
    let (_, c) = owner(&s, "worker-a", None).await;
    let a = acquire(&s, &c).await;
    let first = candidate(&a, "accept-a", "task-a", 1);
    assert!(matches!(s.commit(first.clone()).await.unwrap(), CommitOutcome::Committed(_)));
    let current = acquire(&s, &c).await;
    assert!(matches!(s.commit(candidate(&current, "advance", "task-a", 1)).await.unwrap(), CommitOutcome::Committed(_)));
    let recovered = store(&model);
    assert_eq!(recovered.resolve_commit(&a.ownership, &first.commit_id,
        &first.content_digest).await.unwrap(), CommitResolution::OutcomeUnknown);
}
