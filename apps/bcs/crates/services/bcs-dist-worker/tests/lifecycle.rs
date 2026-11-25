#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "runtime assertions"
)]
mod support;
use bcs_dist_model::*;
use bcs_dist_worker::WorkerRuntime;
use bcs_service_api::{
    port::{
        dist_runtime::DistRuntimeControlPort,
        repo::distributed::{ownership::*, session::*},
    },
    types::dist_runtime::*,
};
use std::{sync::{Arc, atomic::Ordering}, time::Duration};
use support::*;
#[tokio::test(start_paused = true)]
async fn explicit_incarnation_replays_registration_without_replacing_a_live_owner() {
    let world = World::new();
    let old = world.worker("a").await;
    let old_instance = old.instance().clone();
    world.store
        .retire(&MembershipLease { instance: old_instance.clone() })
        .await
        .unwrap();
    let (_stop, stop_rx) = tokio::sync::watch::channel(false);
    assert_eq!(old.run(stop_rx).await.unwrap_err().code, "membership_lost");

    let b = world.worker("b").await;
    let owner = world.claim("b").await;
    let new_instance = WorkerRuntime::fresh_instance(old_instance.node.clone());
    let address = "http://a.example.test:8080";
    let config = bcs_dist_worker::WorkerConfig {
        shards: vec![ShardId(42)],
        ..Default::default()
    };
    // The first registration was committed, but the host lost the response.
    world.store
        .register(new_instance.clone(), RuntimeRole::Worker, address.into(), config.lease_ttl)
        .await
        .unwrap();

    let runtime = WorkerRuntime::start_with_instance(
        new_instance.clone(),
        address.into(),
        config,
        world.store.clone(),
        world.store.clone(),
        world.sessions.clone(),
        Arc::new(Inbox(world.data.clone())),
        world.scheduling.clone(),
        |peer_instance| {
            assert_eq!(peer_instance, &new_instance);
            Ok(world.peers.clone())
        },
        Arc::new(|_| Ok(initial())),
    )
    .await
    .unwrap();
    assert_eq!(runtime.instance(), &new_instance);
    assert_ne!(runtime.instance(), &old_instance);
    assert!(!runtime.snapshot().await.unwrap().ready);
    warm(&[b, runtime.clone()]).await;
    assert!(runtime.snapshot().await.unwrap().ready);
    assert_eq!(world.store.liveness(&old_instance).await.unwrap(), Liveness::Dead);
    assert_eq!(world.store.current(ShardId(42)).await.unwrap().unwrap().claim, owner);
}

#[tokio::test(start_paused = true)]
async fn warmup_and_recovery_must_complete_before_new_round() {
    let world = World::new();
    let a = world.worker("a").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    assert!(!a.snapshot().await.unwrap().ready);
    warm(std::slice::from_ref(&a)).await;
    let sessions = a.acquired_sessions().unwrap();
    assert_eq!(sessions.len(), 1);
    assert_eq!(world.scheduling.observed.lock().unwrap().len(), 1);
    assert!(a.begin_round(&sid()).is_err());
    a.recovery_completed(&sessions[0].ownership).await.unwrap();
    assert_eq!(world.data.watched.lock().unwrap().as_slice(), &[sid()]);
    assert!(a.begin_round(&sid()).is_ok());
    assert!(a.begin_round(&sid()).is_err());
}
#[tokio::test(start_paused = true)]
async fn drain_hands_off_quiescent_session_and_retires_source() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    warm(&[a.clone(), b.clone()]).await;
    let owner = a.acquired_sessions().unwrap()[0].ownership.clone();
    a.recovery_completed(&owner).await.unwrap();
    assert_eq!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::Drained
    );
    assert_eq!(
        world
            .store
            .current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .claim
            .owner
            .instance,
        world.instance("b")
    );
    assert_eq!(
        world
            .store
            .load(&sid())
            .await
            .unwrap()
            .unwrap()
            .owner
            .instance,
        world.instance("b")
    );
    assert_eq!(
        world.store.liveness(&world.instance("a")).await.unwrap(),
        Liveness::Dead
    );
}
#[tokio::test(start_paused = true)]
async fn busy_or_unknown_work_is_not_reported_drained() {
    let world = World::new();
    let a = world.worker("a").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    warm(std::slice::from_ref(&a)).await;
    let owner = a.acquired_sessions().unwrap()[0].ownership.clone();
    a.recovery_completed(&owner).await.unwrap();
    a.begin_round(&sid()).unwrap();
    a.round_unknown(&owner).unwrap();
    assert!(
        matches!(a.drain(Duration::from_millis(20)).await.unwrap(),DrainOutcome::DeadlineReached{remaining} if remaining>0)
    );
    assert!(a.snapshot().await.unwrap().draining);
    assert!(a.begin_round(&sid()).is_err());
}
#[tokio::test(start_paused = true)]
async fn lost_handoff_reply_is_reconciled_without_a_second_epoch() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    warm(&[a.clone(), b.clone()]).await;
    world.peers.lose_reply.store(true, Ordering::SeqCst);
    assert_eq!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::Drained
    );
    assert_eq!(
        world
            .store
            .current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .claim
            .owner
            .epoch,
        WorkerEpoch(2)
    );
}
#[tokio::test(start_paused = true)]
async fn unavailable_scheduling_fence_keeps_acquire_outstanding() {
    let world = World::new();
    let a = world.worker("a").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    world.scheduling.unknown.store(true, Ordering::SeqCst);
    for _ in 0..2 {
        tokio::time::advance(Duration::from_secs(1)).await;
        a.tick().await.unwrap();
    }
    tokio::time::advance(Duration::from_secs(1)).await;
    assert!(a.tick().await.is_err());
    assert!(a.acquired_sessions().unwrap().is_empty());
    assert!(matches!(
        a.drain(Duration::from_millis(20)).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
}
#[tokio::test(start_paused = true)]
async fn request_for_restarted_target_is_fenced() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    warm(&[a, b.clone()]).await;
    let mut target = world.instance("b");
    target.incarnation = "b-old".into();
    assert_eq!(
        b.accept_handoff(HandoffRequest {
            request_id: RequestId("h".into()),
            from: claim,
            target,
            grace: Duration::from_secs(5)
        })
        .await
        .unwrap(),
        HandoffOutcome::Fenced
    );
}
#[tokio::test(start_paused = true)]
async fn healthy_owner_is_not_rebalanced_by_better_ranked_worker() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let first = world.claim("a").await;
    warm(&[a, b]).await;
    assert_eq!(
        world
            .store
            .current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .claim,
        first
    );
}
#[tokio::test(start_paused = true)]
async fn heartbeat_expiration_suspends_new_work_instead_of_reviving_instance() {
    let world = World::new();
    let a = world.worker("a").await;
    world.claim("a").await;
    warm(std::slice::from_ref(&a)).await;
    world.model.advance(Duration::from_secs(4));
    tokio::time::advance(Duration::from_secs(4)).await;
    assert!(a.tick().await.is_err());
    assert!(!a.snapshot().await.unwrap().ready);
    assert_eq!(
        world.store.liveness(&world.instance("a")).await.unwrap(),
        Liveness::Dead
    );
}
#[tokio::test(start_paused = true)]
async fn changed_retry_content_is_rejected_after_acceptance() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    warm(&[a, b.clone()]).await;
    let request = HandoffRequest {
        request_id: RequestId("fixed-id".into()),
        from: claim,
        target: world.instance("b"),
        grace: Duration::from_secs(5),
    };
    assert!(matches!(
        b.accept_handoff(request.clone()).await.unwrap(),
        HandoffOutcome::Accepted(_)
    ));
    let mut changed = request;
    changed.grace = Duration::from_secs(6);
    assert!(b.accept_handoff(changed).await.is_err());
}
#[tokio::test(start_paused = true)]
async fn source_done_checks_exact_source_not_only_a_worker_identity() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    warm(&[a, b.clone()]).await;
    let request = HandoffRequest {
        request_id: RequestId("fixed-id".into()),
        from: claim,
        target: world.instance("b"),
        grace: Duration::from_secs(5),
    };
    let HandoffOutcome::Accepted(record) = b.accept_handoff(request.clone()).await.unwrap() else {
        panic!()
    };
    assert!(
        !b.handoff_done(
            instance("someone-else"),
            record.claim.clone(),
            request.request_id.clone()
        )
        .await
        .unwrap()
    );
    assert!(
        b.handoff_done(world.instance("a"), record.claim, request.request_id)
            .await
            .unwrap()
    );
}
#[tokio::test(start_paused = true)]
async fn releasing_a_quiescent_session_frees_local_state_and_subscription() {
    let world = World::new();
    let a = world.worker("a").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    warm(std::slice::from_ref(&a)).await;
    let owner = a.acquired_sessions().unwrap()[0].ownership.clone();
    assert!(a.release_session(&owner).await.is_err());
    a.recovery_completed(&owner).await.unwrap();
    a.release_session(&owner).await.unwrap();
    assert!(world.data.watched.lock().unwrap().is_empty());
    assert!(a.begin_round(&sid()).is_err());
}
#[tokio::test(start_paused = true)]
async fn confirmed_checkpoint_and_receipt_advance_together() {
    let world = World::new();
    let a = world.worker("a").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    warm(std::slice::from_ref(&a)).await;
    let owner = a.acquired_sessions().unwrap()[0].ownership.clone();
    a.recovery_completed(&owner).await.unwrap();
    let mut acquired = a.begin_round(&sid()).unwrap();
    let batch = CommittedBatch {
        created_at: None,
        commit_id: CommitId("commit-1".into()),
        source_commit: CommitSeq(1),
        content_digest: Digest("digest-1".into()),
        effects: vec![],
    };
    acquired.envelope.checkpoint.commit_seq = CommitSeq(1);
    acquired.envelope.checkpoint.outbox = Some(batch.clone());
    let receipt = bcs_service_api::port::repo::distributed::common::CommitReceipt {
        sid: owner.sid.clone(),
        owner: owner.owner.clone(),
        commit_id: batch.commit_id,
        content_digest: batch.content_digest,
        sequence: CommitSeq(1),
        consumed: None,
        proof: "proof".into(),
    };
    a.checkpoint_confirmed(&owner, acquired.envelope, receipt.clone(), 0)
        .unwrap();
    assert_eq!(a.begin_round(&sid()).unwrap().last_commit, Some(receipt));
}

#[tokio::test(start_paused = true)]
async fn unknown_acquire_at_capacity_recovers_during_normal_ticks() {
    let world = World::new();
    let a = world.worker_with_capacity("a", 1).await;
    world.claim("a").await;
    world.pending(&sid()).await;
    world.scheduling.unknown.store(true, Ordering::SeqCst);
    for _ in 0..2 {
        tokio::time::advance(Duration::from_secs(1)).await;
        a.tick().await.unwrap();
    }
    tokio::time::advance(Duration::from_secs(1)).await;
    assert!(a.tick().await.is_err());
    assert!(a.acquired_sessions().unwrap().is_empty());
    // The outstanding acquire fills the entire capacity. A normal tick must
    // resolve it rather than waiting forever for an unused free slot or shutdown.
    world.scheduling.unknown.store(false, Ordering::SeqCst);
    tokio::time::advance(Duration::from_millis(500)).await;
    a.tick().await.unwrap();
    let recovered = a.acquired_sessions().unwrap();
    assert_eq!(recovered.len(), 1);
    assert_eq!(recovered[0].ownership.sid, sid());
    assert_eq!(world.scheduling.observed.lock().unwrap().len(), 1);
    a.recovery_completed(&recovered[0].ownership).await.unwrap();
    assert!(a.begin_round(&sid()).is_ok());
}

#[tokio::test(start_paused = true)]
async fn fenced_scheduling_retries_local_release_before_forgetting_acquisition() {
    let world = World::new();
    let a = world.worker_with_capacity("a", 1).await;
    world.claim("a").await;
    world.pending(&sid()).await;
    world.scheduling.fenced.store(true, Ordering::SeqCst);
    world
        .sessions
        .release_unavailable
        .store(true, Ordering::SeqCst);
    for _ in 0..2 {
        tokio::time::advance(Duration::from_secs(1)).await;
        a.tick().await.unwrap();
    }
    tokio::time::advance(Duration::from_secs(1)).await;
    assert!(a.tick().await.is_err());
    assert!(a.acquired_sessions().unwrap().is_empty());
    assert!(world.sessions.released.lock().unwrap().is_empty());
    // Only the in-flight acquisition can trigger another release after the
    // durable pending page disappears; it must survive the failed release.
    world.data.pending.lock().unwrap().clear();
    world
        .sessions
        .release_unavailable
        .store(false, Ordering::SeqCst);
    tokio::time::advance(Duration::from_millis(500)).await;
    a.tick().await.unwrap();
    let released = world.sessions.released.lock().unwrap();
    assert_eq!(released.len(), 1);
    assert_eq!(released[0].sid, sid());
    assert!(a.acquired_sessions().unwrap().is_empty());
    assert!(world.data.watched.lock().unwrap().is_empty());
}
