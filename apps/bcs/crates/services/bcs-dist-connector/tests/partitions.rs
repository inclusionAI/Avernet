//! Partition ownership: claiming partitions, taking over their calls, and
//! handing them over on drain.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use bcs_dist_connector::engine::rank_candidates;
use bcs_dist_model::*;
use bcs_service_api::port::dist_runtime::DistRuntimeControlPort;
use bcs_service_api::port::repo::distributed::ownership::{HandoffOutcome, HandoffRequest, ShardLeasePort};
use bcs_service_api::types::dist_runtime::DrainOutcome;
use rig::*;
use std::sync::Arc;
use std::time::Duration;

fn members(rig: &mut Rig) -> Arc<Members> {
    let members = Arc::new(Members::default());
    rig.membership = members.clone();
    members
}

#[tokio::test]
async fn a_lone_connector_owns_the_partition_and_runs_its_calls() {
    let rig = Rig::new().await;
    let _events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    assert_eq!(rig.owners.owner(0), Some(instance("conn-03")));
}

#[tokio::test]
async fn a_live_owner_is_not_displaced_and_a_dead_ones_calls_are_taken_over() {
    let mut rig = Rig::new().await;
    let members = members(&mut rig);
    // conn-07 owns the partition and holds the call (listed in its index).
    rig.owners.set(0, &instance("conn-07"), 1);
    use bcs_service_api::port::repo::distributed::ownership::{MembershipPort, RuntimeRole};
    members
        .register(instance("conn-07"), RuntimeRole::Connector, "http://conn-07.example.test:8080".into(), Duration::from_secs(3))
        .await
        .unwrap();
    rig.worker.create().await;
    let _old = acquire(&queue(&rig.store), &instance("conn-07")).await;
    let _events = rig.agent.start_stream();
    rig.worker.notify().await;
    let running = rig.start();
    settle().await;
    assert_eq!(rig.owners.owner(0), Some(instance("conn-07")), "a live owner keeps its partition");
    assert_eq!(rig.agent.count("start"), 0, "its calls are not touched");
    assert_eq!(running.active().await, 0);

    // Once conn-07 is dead, conn-03 claims the partition and recovers the
    // listed call at once, without waiting for a re-posted hint.
    members.kill("conn-07");
    until("partition claimed", || async { rig.owners.owner(0) == Some(instance("conn-03")) }).await;
    until("call taken over", || async {
        rig.doc().await["execution"]["holder"]["instance"]["node"] == "conn-03"
    })
    .await;
    assert_eq!(rig.doc().await["execution"]["holder"]["epoch"], 2);
    until("started by the new owner", || async { rig.agent.count("start") == 1 }).await;
}

#[tokio::test]
async fn drain_stops_at_a_safe_point_and_hands_the_partition_to_a_peer() {
    let mut rig = Rig::new().await;
    let members = members(&mut rig);
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let source = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let target = rig.start_as("conn-07");
    until("the peer is ready", || async { target.engine.snapshot().await.unwrap().ready }).await;
    // The peer re-subscribes to the remote task when it takes the call over.
    let _resubscribed = rig.agent.subscribe_stream();

    let engine = source.engine.clone();
    let drain = tokio::spawn(async move { engine.drain(Duration::from_secs(5)).await });
    settle().await;
    assert!(!drain.is_finished(), "the open event stream is not a safe point");
    // The agent's turn ends: the stream closes and the actor stops there.
    drop(events);
    let outcome = tokio::time::timeout(Duration::from_secs(3), drain).await.unwrap().unwrap().unwrap();
    assert_eq!(outcome, DrainOutcome::Drained);
    assert_eq!(rig.peers.offers.lock().unwrap().clone(), vec!["0 -> conn-07".to_owned()]);
    let record = rig.owners.current(ShardId(0)).await.unwrap().unwrap();
    assert_eq!(record.claim.owner.instance, instance("conn-07"));
    assert_eq!(record.claim.owner.epoch, WorkerEpoch(2));

    // The new owner takes the call over right away and clears the handoff
    // mark once the source is gone.
    until("taken over by the peer", || async {
        rig.doc().await["execution"]["holder"]["instance"]["node"] == "conn-07"
    })
    .await;
    assert!(members.dead.lock().unwrap().contains(&"conn-03".to_owned()), "the source retired");
    until("handoff mark cleared", || async {
        rig.owners.current(ShardId(0)).await.unwrap().unwrap().handoff.is_none()
    })
    .await;
}

#[tokio::test]
async fn a_peer_that_does_not_take_the_partition_is_skipped_for_the_next_one() {
    use bcs_service_api::port::repo::distributed::ownership::{MembershipPort, RuntimeRole};
    let mut rig = Rig::new().await;
    let members = members(&mut rig);
    let source = rig.start();
    until("owns the partition", || async { rig.owners.owner(0) == Some(instance("conn-03")) }).await;
    // Two live peers; the best-ranked one does not answer (no engine runs
    // behind its record), the other takes the partition.
    let ranked = rank_candidates(0, &[instance("conn-a"), instance("conn-b")]);
    let (silent, next) = (&ranked[0], &ranked[1]);
    members
        .register(silent.clone(), RuntimeRole::Connector, "http://silent.example.test".into(), Duration::from_secs(3))
        .await
        .unwrap();
    let peer = rig.start_as(&next.node);
    until("peer ready", || async { peer.engine.snapshot().await.unwrap().ready }).await;

    let outcome = source.engine.drain(Duration::from_secs(2)).await.unwrap();
    assert_eq!(outcome, DrainOutcome::Drained);
    let offers = rig.peers.offers.lock().unwrap().clone();
    assert_eq!(offers, vec![format!("0 -> {}", silent.node), format!("0 -> {}", next.node)]);
    assert_eq!(rig.owners.owner(0), Some(next.clone()));
}

#[tokio::test]
async fn a_handoff_for_another_instance_or_while_draining_is_not_committed() {
    let rig = Rig::new().await;
    let running = rig.start();
    until("owns the partition", || async { rig.owners.owner(0) == Some(instance("conn-03")) }).await;
    let from = rig.owners.current(ShardId(0)).await.unwrap().unwrap().claim;
    let offer = |target: &str| HandoffRequest {
        request_id: RequestId(format!("offer-{target}")),
        from: from.clone(),
        target: instance(target),
        grace: Duration::from_secs(1),
    };
    assert_eq!(running.engine.accept_handoff(offer("conn-99")).await.unwrap(), HandoffOutcome::Fenced);
    running.engine.drain(Duration::from_millis(10)).await.unwrap();
    // While draining nothing is committed: an offer whose source still owns
    // the partition is refused.
    assert_eq!(running.engine.accept_handoff(offer("conn-03")).await.unwrap(), HandoffOutcome::Rejected);
    assert_eq!(rig.owners.current(ShardId(0)).await.unwrap().unwrap().claim, from);
}

#[test]
fn hrw_order_is_stable_and_complete() {
    let all = [instance("a"), instance("b"), instance("c")];
    let first = rank_candidates(3, &all);
    let reversed: Vec<Instance> = all.iter().rev().cloned().collect();
    assert_eq!(rank_candidates(3, &reversed), first);
    assert_eq!(first.len(), 3);
    // Different partitions spread over different first choices.
    let firsts: std::collections::BTreeSet<String> =
        (0..32).map(|p| rank_candidates(p, &all)[0].node.clone()).collect();
    assert_eq!(firsts.len(), 3);
}

/// Owner records whose reads can be held open, to stall an ownership round.
struct StalledOwners {
    inner: MemoryOwners,
    stalled: std::sync::atomic::AtomicBool,
}

#[async_trait::async_trait]
impl ShardLeasePort for StalledOwners {
    async fn claim(
        &self,
        request: bcs_service_api::port::repo::distributed::ownership::ClaimShard,
    ) -> Result<bcs_service_api::port::repo::distributed::ownership::LeaseClaim, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.claim(request).await
    }
    async fn current(
        &self,
        shard: ShardId,
    ) -> Result<Option<bcs_service_api::port::repo::distributed::ownership::ShardRecord>, bcs_service_api::port::repo::distributed::common::StoreError> {
        while self.stalled.load(std::sync::atomic::Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        self.inner.current(shard).await
    }
    async fn handoff(&self, request: HandoffRequest) -> Result<HandoffOutcome, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.handoff(request).await
    }
    async fn source_done(
        &self,
        target: &bcs_service_api::port::repo::distributed::ownership::ShardClaim,
        id: &RequestId,
    ) -> Result<bool, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.source_done(target, id).await
    }
    async fn clear_handoff(
        &self,
        target: &bcs_service_api::port::repo::distributed::ownership::ShardClaim,
        id: &RequestId,
    ) -> Result<bool, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.clear_handoff(target, id).await
    }
}

#[tokio::test]
async fn a_stalled_ownership_round_does_not_stop_the_renewals() {
    use std::sync::atomic::Ordering;
    let mut rig = Rig::new().await;
    let members = members(&mut rig);
    rig.config.peer_timeout = Duration::from_secs(30);
    let owners = Arc::new(StalledOwners { inner: rig.owners.clone(), stalled: true.into() });
    let mut ports = rig.ports();
    ports.partitions = owners.clone();
    let engine = bcs_dist_connector::ConnectorEngine::new(rig.config.clone(), ports).unwrap();
    let looped = engine.clone();
    let task = tokio::spawn(async move { looped.run().await });
    // The main loop is stuck in its first ownership round; the heartbeat
    // still renews every `membership_renew` (20 ms here).
    until("renewed while the round is stalled", || async { members.renewals.load(Ordering::SeqCst) >= 5 }).await;
    assert!(!task.is_finished());
    owners.stalled.store(false, Ordering::SeqCst);
    until("the round completes", || async { rig.owners.owner(0) == Some(instance("conn-03")) }).await;
    task.abort();
}

/// A queue whose claims wait for a permit.
struct HeldClaims {
    inner: Arc<dyn bcs_service_api::port::repo::distributed::dispatch::DispatchQueuePort>,
    gate: tokio::sync::Semaphore,
}

#[async_trait::async_trait]
impl bcs_service_api::port::repo::distributed::dispatch::DispatchQueuePort for HeldClaims {
    fn command_partitions(&self) -> u32 {
        self.inner.command_partitions()
    }
    fn partition_of(&self, did: &Did) -> u32 {
        self.inner.partition_of(did)
    }
    async fn claim(
        &self,
        request: bcs_service_api::port::repo::distributed::dispatch::DispatchClaimRequest,
    ) -> Result<bcs_service_api::port::repo::distributed::dispatch::ClaimOutcome, bcs_service_api::port::repo::distributed::common::StoreError> {
        let _permit = self.gate.acquire().await.unwrap();
        self.inner.claim(request).await
    }
    async fn resume_owned(
        &self,
        request: bcs_service_api::port::repo::distributed::dispatch::ResumeOwnedDispatch,
    ) -> Result<bcs_service_api::port::repo::distributed::dispatch::ClaimOutcome, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.resume_owned(request).await
    }
    async fn notifications(
        &self,
        pool: PoolId,
        me: Instance,
    ) -> Result<Box<dyn bcs_service_api::port::repo::distributed::dispatch::CommandFeedPort>, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.notifications(pool, me).await
    }
    async fn held_in(&self, pool: &PoolId, partition: u32, limit: std::num::NonZeroU32) -> Result<Vec<Did>, bcs_service_api::port::repo::distributed::common::StoreError> {
        self.inner.held_in(pool, partition, limit).await
    }
}

#[tokio::test]
async fn a_claim_still_running_keeps_the_partition_from_moving() {
    let mut rig = Rig::new().await;
    let _members = members(&mut rig);
    rig.worker.create().await;
    let mut ports = rig.ports();
    let held = Arc::new(HeldClaims { inner: ports.queue.clone(), gate: tokio::sync::Semaphore::new(0) });
    ports.queue = held.clone();
    let source = bcs_dist_connector::ConnectorEngine::new(rig.config.clone(), ports).unwrap();
    rig.peers.add(&source, "conn-03");
    let looped = source.clone();
    let task = tokio::spawn(async move { looped.run().await });
    until("owns the partition", || async { rig.owners.owner(0) == Some(instance("conn-03")) }).await;
    let peer = rig.start_as("conn-07");
    until("peer ready", || async { peer.engine.snapshot().await.unwrap().ready }).await;
    // A hint arrives; its claim is held open inside the main loop.
    rig.worker.notify().await;
    settle().await;

    let outcome = source.drain(Duration::from_millis(400)).await.unwrap();
    assert_eq!(outcome, DrainOutcome::DeadlineReached { remaining: 1 }, "the partition stays");
    assert!(rig.peers.offers.lock().unwrap().is_empty(), "nothing offered while a claim runs");
    assert_eq!(rig.owners.owner(0), Some(instance("conn-03")));
    held.gate.add_permits(1);
    assert!(task.await.unwrap().is_ok());
}
