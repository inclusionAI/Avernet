#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "processor lifecycle assertions"
)]
mod support;
use bcs_dist_model::*;
use bcs_dist_worker::WorkerRuntime;
use bcs_service_api::{
    port::{
        dist_runtime::DistRuntimeControlPort,
        repo::distributed::{common::*, inbox::*, session::*},
    },
    types::dist_runtime::DrainOutcome,
};
use std::{num::NonZeroU32, sync::Arc, time::Duration};
use support::*;

async fn ready_session() -> (World, Arc<WorkerRuntime>, SessionOwnership) {
    let world = World::new();
    let worker = world.worker("a").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    warm(std::slice::from_ref(&worker)).await;
    let owner = worker.acquired_sessions().unwrap()[0].ownership.clone();
    worker.recovery_completed(&owner).await.unwrap();
    (world, worker, owner)
}

fn limits() -> ReadLimits {
    ReadLimits {
        max_sessions: NonZeroU32::new(2).unwrap(),
        max_entries: NonZeroU32::new(8).unwrap(),
        max_bytes: NonZeroU32::new(1024).unwrap(),
    }
}

fn next_commit(session: &AcquiredSession) -> CommitRequest {
    let mut candidate = session.envelope.checkpoint.clone();
    let seq = CommitSeq(candidate.commit_seq.0 + 1);
    let commit_id = CommitId(format!("commit-{}", seq.0));
    let content_digest = Digest(format!("digest-{}", seq.0));
    candidate.commit_seq = seq;
    candidate.outbox = Some(CommittedBatch {
        commit_id: commit_id.clone(),
        content_digest: content_digest.clone(),
        source_commit: seq,
        effects: vec![Effect {
            id: EffectId {
                sid: session.ownership.sid.clone(),
                commit: seq,
                index: NonZeroU32::new(1).unwrap(),
            },
            content_digest: Digest("event-digest".into()),
            kind: EffectKind::AppendEvents {
                previous: None,
                events: vec![],
            },
        }],
    });
    CommitRequest {
        owner: session.ownership.clone(),
        expected_commit_seq: session.envelope.checkpoint.commit_seq,
        commit_id,
        content_digest,
        candidate,
    }
}

#[tokio::test(start_paused = true)]
async fn feed_preserves_batch_limits_and_caps_wait_without_consuming_unowned_shards() {
    let (world, worker, _) = ready_session().await;
    world.data.batches.lock().unwrap().push_back(FeedBatch {
                failures: vec![],
        sessions: vec![],
        hints: vec![sid()],
    });
    assert_eq!(
        worker
            .read_inputs(ShardId(43), limits(), Duration::ZERO)
            .await
            .unwrap_err()
            .code,
        "worker_not_ready"
    );
    assert!(world.data.reads.lock().unwrap().is_empty());
    let batch = worker
        .read_inputs(ShardId(42), limits(), Duration::from_secs(10))
        .await
        .unwrap();
    assert_eq!(batch.hints, vec![sid()]);
    assert_eq!(
        world.data.reads.lock().unwrap().as_slice(),
        &[(limits(), Duration::from_millis(500))]
    );
    let batch = worker
        .read_inputs(ShardId(42), limits(), Duration::ZERO)
        .await
        .unwrap();
    assert!(batch.hints.is_empty());
    assert_eq!(
        world.data.reads.lock().unwrap().last().unwrap().1,
        Duration::ZERO
    );
}

#[tokio::test(start_paused = true)]
async fn confirmed_commit_waits_for_effects_before_admitting_the_next_round() {
    let (world, worker, owner) = ready_session().await;
    let request = next_commit(&worker.begin_round(&sid()).unwrap());
    let CommitOutcome::Committed(receipt) = world.store.commit(request).await.unwrap() else {
        panic!()
    };
    let envelope = world.store.load(&sid()).await.unwrap().unwrap();
    worker
        .checkpoint_confirmed(&owner, envelope, receipt.clone(), 1)
        .unwrap();
    assert_eq!(worker.snapshot().await.unwrap().pending_effects, 1);
    assert_eq!(
        worker.begin_round(&sid()).unwrap_err().code,
        "session_not_quiescent"
    );
    assert!(worker.release_session(&owner).await.is_err());
    let mut stale = owner.clone();
    stale.owner.epoch = WorkerEpoch(0);
    assert_eq!(
        worker.effects_completed(&stale).unwrap_err().code,
        "session_effect_state"
    );
    assert_eq!(worker.snapshot().await.unwrap().pending_effects, 1);
    worker.effects_completed(&owner).unwrap();
    assert_eq!(worker.snapshot().await.unwrap().pending_effects, 0);
    let next = worker.begin_round(&sid()).unwrap();
    assert_eq!(next.last_commit, Some(receipt));
    assert_eq!(next.envelope.checkpoint.commit_seq, CommitSeq(1));
    assert!(worker.effects_completed(&owner).is_err());
}

#[tokio::test(start_paused = true)]
async fn scheduling_requires_matching_completion_before_round_or_release() {
    let (world, worker, owner) = ready_session().await;
    worker.scheduling_started(&owner).unwrap();
    assert!(worker.scheduling_started(&owner).is_err());
    assert_eq!(
        worker.begin_round(&sid()).unwrap_err().code,
        "session_not_quiescent"
    );
    assert_eq!(
        worker.release_session(&owner).await.unwrap_err().code,
        "session_not_quiescent"
    );
    let mut stale = owner.clone();
    stale.owner.instance.incarnation = "previous-process".into();
    assert_eq!(
        worker.scheduling_completed(&stale).unwrap_err().code,
        "session_owner_changed"
    );
    assert!(worker.begin_round(&sid()).is_err());
    worker.scheduling_completed(&owner).unwrap();
    worker.release_session(&owner).await.unwrap();
    assert!(world.data.watched.lock().unwrap().is_empty());
    assert_eq!(
        world.sessions.released.lock().unwrap().as_slice(),
        std::slice::from_ref(&owner)
    );
    assert_eq!(
        worker.scheduling_completed(&owner).unwrap_err().code,
        "session_not_active"
    );
}

#[tokio::test(start_paused = true)]
async fn uncertain_commit_resumes_only_from_matching_durable_receipt() {
    let (world, worker, owner) = ready_session().await;
    let request = next_commit(&worker.begin_round(&sid()).unwrap());
    world.model.lose_next_response();
    assert!(matches!(
        world.store.commit(request.clone()).await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    worker.round_unknown(&owner).unwrap();
    assert_eq!(worker.snapshot().await.unwrap().active_work, 1);
    assert!(worker.begin_round(&sid()).is_err());
    let CommitResolution::Committed(receipt) = world
        .store
        .resolve_commit(&owner, &request.commit_id, &request.content_digest)
        .await
        .unwrap()
    else {
        panic!()
    };
    let envelope = world.store.load(&sid()).await.unwrap().unwrap();
    let mut forged = receipt.clone();
    forged.content_digest = Digest("different-decision".into());
    assert_eq!(
        worker
            .checkpoint_confirmed(&owner, envelope.clone(), forged, 1)
            .unwrap_err()
            .code,
        "checkpoint_receipt_mismatch"
    );
    assert!(worker.begin_round(&sid()).is_err());
    worker
        .checkpoint_confirmed(&owner, envelope, receipt, 1)
        .unwrap();
    assert!(worker.begin_round(&sid()).is_err());
    worker.effects_completed(&owner).unwrap();
    assert_eq!(
        worker
            .begin_round(&sid())
            .unwrap()
            .envelope
            .checkpoint
            .commit_seq,
        CommitSeq(1)
    );
}

#[tokio::test(start_paused = true)]
async fn round_permission_expires_and_shutdown_cannot_restore_it() {
    let (_world, worker, owner) = ready_session().await;
    worker.begin_round(&sid()).unwrap();
    assert!(worker.may_continue_round(&owner).unwrap());
    let mut stale = owner.clone();
    stale.owner.epoch = WorkerEpoch(0);
    assert!(!worker.may_continue_round(&stale).unwrap());
    tokio::time::advance(Duration::from_secs(3)).await;
    assert!(!worker.may_continue_round(&owner).unwrap());
    assert_eq!(
        worker.scheduling_started(&owner).unwrap_err().code,
        "worker_not_ready"
    );
    assert!(matches!(
        worker.drain(Duration::ZERO).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    assert!(!worker.may_continue_round(&owner).unwrap());
    assert_eq!(worker.heartbeat().await.unwrap_err().code, "worker_stopped");
}
