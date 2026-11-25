#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "handoff confirmation assertions"
)]
mod support;
use bcs_dist_model::*;
use bcs_service_api::{
    port::{
        dist_runtime::DistRuntimeControlPort,
        repo::distributed::{ownership::*, session::*},
    },
    types::dist_runtime::DrainOutcome,
};
use std::time::Duration;
use support::*;

fn request(from: ShardClaim, target: Instance) -> HandoffRequest {
    HandoffRequest {
        request_id: RequestId("confirmed-handoff".into()),
        from,
        target,
        grace: Duration::from_secs(5),
    }
}

#[tokio::test(start_paused = true)]
async fn receiver_in_warmup_rejects_new_handoff_without_mutating_owner() {
    let world = World::new();
    let _a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    assert_eq!(
        b.accept_handoff(request(claim.clone(), world.instance("b")))
            .await
            .unwrap(),
        HandoffOutcome::Rejected
    );
    let current = world.store.current(ShardId(42)).await.unwrap().unwrap();
    assert_eq!(current.claim, claim);
    assert!(current.handoff.is_none());
}

#[tokio::test(start_paused = true)]
async fn lost_acceptance_reply_is_read_back_even_after_receiver_starts_shutdown() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    world.pending(&sid()).await;
    warm(&[a, b.clone()]).await;
    let request = request(claim.clone(), world.instance("b"));
    world.model.lose_next_response();
    assert_eq!(
        b.accept_handoff(request.clone()).await.unwrap_err().code,
        "store_outcome_unknown"
    );
    let accepted = world.store.current(ShardId(42)).await.unwrap().unwrap();
    assert_eq!(accepted.claim.owner.epoch, WorkerEpoch(2));
    let mut altered = request.clone();
    altered.grace += Duration::from_secs(1);
    assert_eq!(
        b.accept_handoff(altered.clone()).await.unwrap_err().code,
        "handoff_content_conflict"
    );
    assert!(matches!(
        b.drain(Duration::ZERO).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    // Shutdown retains the unknown request, including its retry deadline.
    assert_eq!(b.accept_handoff(request.clone()).await.unwrap_err().code, "handoff_confirmation_backoff");
    tokio::time::advance(Duration::from_millis(500)).await;
    assert_eq!(
        b.accept_handoff(request.clone()).await.unwrap(),
        HandoffOutcome::Accepted(accepted.clone())
    );
    assert_eq!(
        world.store.current(ShardId(42)).await.unwrap().unwrap(),
        accepted
    );
    // Confirming a shard handoff does not transfer or activate the source session.
    assert_eq!(
        world.store.load(&sid()).await.unwrap().unwrap().owner,
        claim.owner
    );
    assert!(b.acquired_sessions().unwrap().is_empty());
    // After confirmation removes the local pending entry, persisted content
    // validation still rejects a different request under the same identity.
    assert_eq!(
        b.accept_handoff(altered).await.unwrap_err().code,
        "store_invalid"
    );
    assert_eq!(
        world.store.current(ShardId(42)).await.unwrap().unwrap(),
        accepted
    );
}

#[tokio::test(start_paused = true)]
async fn stopped_receiver_fences_a_request_with_a_stale_source_epoch() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    warm(&[a, b.clone()]).await;
    b.drain(Duration::ZERO).await.unwrap();
    let mut stale = claim.clone();
    stale.owner.epoch = WorkerEpoch(0);
    assert_eq!(
        b.accept_handoff(request(stale, world.instance("b")))
            .await
            .unwrap(),
        HandoffOutcome::Fenced
    );
    assert_eq!(
        world
            .store
            .current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .claim,
        claim
    );
}

#[tokio::test(start_paused = true)]
async fn receiver_with_no_owner_record_never_confirms_a_handoff() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = ShardClaim {
        shard: ShardId(42),
        owner: bcs_service_api::port::repo::distributed::common::ShardOwner {
            instance: a.instance().clone(),
            epoch: WorkerEpoch(1),
        },
    };
    assert_eq!(
        b.accept_handoff(request(claim.clone(), world.instance("b")))
            .await
            .unwrap(),
        HandoffOutcome::Fenced
    );
    let mut target = claim;
    target.owner.instance = b.instance().clone();
    target.owner.epoch = WorkerEpoch(2);
    assert!(
        !b.handoff_done(a.instance().clone(), target, RequestId("missing".into()))
            .await
            .unwrap()
    );
    assert!(world.store.current(ShardId(42)).await.unwrap().is_none());
}

#[tokio::test(start_paused = true)]
async fn too_short_grace_is_rejected_without_taking_ownership() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    let claim = world.claim("a").await;
    warm(&[a, b.clone()]).await;
    let mut request = request(claim.clone(), world.instance("b"));
    request.grace = Duration::from_millis(500);
    assert_eq!(
        b.accept_handoff(request).await.unwrap(),
        HandoffOutcome::Rejected
    );
    assert_eq!(
        world
            .store
            .current(ShardId(42))
            .await
            .unwrap()
            .unwrap()
            .claim,
        claim
    );
    assert_eq!(b.snapshot().await.unwrap().active_work, 0);
}
