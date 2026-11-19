#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "runtime fault assertions"
)]
mod support;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_worker::{WorkerConfig, WorkerRuntime};
use bcs_service_api::{
    port::{
        dist_runtime::DistRuntimeControlPort,
        repo::distributed::{common::*, ownership::*},
    },
    types::dist_runtime::*,
};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::Duration,
};
use support::*;
struct Members {
    store: Arc<bcs_dist_store::coordination::CoordinationStore>,
    failed: AtomicBool,
    calls: AtomicUsize,
    renewals: AtomicUsize,
}
#[async_trait]
impl MembershipPort for Members {
    async fn register(
        &self,
        i: Instance,
        r: RuntimeRole,
        a: String,
        ttl: Duration,
    ) -> Result<MembershipLease, StoreError> {
        self.store.register(i, r, a, ttl).await
    }
    async fn renew(&self, l: &MembershipLease, ttl: Duration) -> Result<Renewal, StoreError> {
        self.renewals.fetch_add(1, Ordering::SeqCst);
        self.store.renew(l, ttl).await
    }
    async fn set_draining(&self, l: &MembershipLease) -> Result<Renewal, StoreError> {
        self.store.set_draining(l).await
    }
    async fn retire(&self, l: &MembershipLease) -> Result<(), StoreError> {
        self.store.retire(l).await
    }
    async fn liveness(&self, i: &Instance) -> Result<Liveness, StoreError> {
        self.store.liveness(i).await
    }
    async fn members(
        &self,
        r: RuntimeRole,
        p: PageRequest,
    ) -> Result<Page<MemberRecord>, StoreError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if self.failed.load(Ordering::SeqCst) {
            return Err(StoreError::NotApplied {
                code: "member_index_unavailable".into(),
            });
        }
        self.store.members(r, p).await
    }
}
fn members(world: &World) -> Arc<Members> {
    Arc::new(Members {
        store: world.store.clone(),
        failed: AtomicBool::new(false),
        calls: AtomicUsize::new(0),
        renewals: AtomicUsize::new(0),
    })
}
async fn start(
    world: &World,
    members: Arc<Members>,
    owners: Arc<dyn ShardLeasePort>,
    shards: Vec<ShardId>,
) -> Arc<WorkerRuntime> {
    let a = WorkerRuntime::start(
        "a".into(),
        "http://a.example.test:8080".into(),
        WorkerConfig {
            shards,
            ..WorkerConfig::default()
        },
        members,
        owners,
        world.sessions.clone(),
        Arc::new(Inbox(world.data.clone())),
        world.scheduling.clone(),
        world.peers.clone(),
        Arc::new(|_| Ok(initial())),
    )
    .await
    .unwrap();
    world
        .peers
        .workers
        .lock()
        .unwrap()
        .insert("a".into(), a.clone());
    a
}
#[tokio::test(start_paused = true)]
async fn member_discovery_failure_does_not_stop_an_accepted_transfer() {
    let world = World::new();
    let members = members(&world);
    let a = start(
        &world,
        members.clone(),
        world.store.clone(),
        vec![ShardId(42)],
    )
    .await;
    let b = world.worker("b").await;
    world.claim("a").await;
    for n in 0..70 {
        world.pending(&Sid(format!("s0042.transfer{n:03}"))).await;
    }
    warm(&[a.clone(), b]).await;
    for _ in 0..3 {
        a.tick().await.unwrap();
    }
    for s in a.acquired_sessions().unwrap() {
        a.recovery_completed(&s.ownership).await.unwrap();
    }
    *world.sessions.transfer_delay.lock().unwrap() = Duration::from_millis(20);
    let fail = {
        let members = members.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(100)).await;
            members.failed.store(true, Ordering::SeqCst);
            members.calls.load(Ordering::SeqCst)
        })
    };
    assert_eq!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::Drained
    );
    assert_eq!(members.calls.load(Ordering::SeqCst), fail.await.unwrap());
    assert_eq!(world.sessions.released.lock().unwrap().len(), 70);
}
struct SlowIncoming {
    store: Arc<bcs_dist_store::coordination::CoordinationStore>,
    reject: AtomicBool,
}
#[async_trait]
impl ShardLeasePort for SlowIncoming {
    async fn current(&self, s: ShardId) -> Result<Option<ShardRecord>, StoreError> {
        self.store.current(s).await
    }
    async fn claim(&self, r: ClaimShard) -> Result<LeaseClaim, StoreError> {
        self.store.claim(r).await
    }
    async fn handoff(&self, _r: HandoffRequest) -> Result<HandoffOutcome, StoreError> {
        if self.reject.load(Ordering::SeqCst) {
            return Ok(HandoffOutcome::Rejected);
        }
        tokio::time::sleep(Duration::from_millis(400)).await;
        Err(StoreError::OutcomeUnknown {
            operation_id: "incoming".into(),
        })
    }
    async fn source_done(&self, c: &ShardClaim, i: &RequestId) -> Result<bool, StoreError> {
        self.store.source_done(c, i).await
    }
    async fn clear_handoff(&self, c: &ShardClaim, i: &RequestId) -> Result<bool, StoreError> {
        self.store.clear_handoff(c, i).await
    }
}
#[tokio::test(start_paused = true)]
async fn drain_renews_while_confirming_many_incoming_requests() {
    let world = World::new();
    let members = members(&world);
    let a = start(
        &world,
        members.clone(),
        Arc::new(SlowIncoming {
            store: world.store.clone(),
            reject: AtomicBool::new(false),
        }),
        vec![ShardId(42)],
    )
    .await;
    let b = world.worker("b").await;
    let source = world.claim("b").await;
    warm(&[a.clone(), b]).await;
    for n in 0..8 {
        a.heartbeat().await.unwrap();
        let request = HandoffRequest {
            request_id: RequestId(format!("pending{n}")),
            from: source.clone(),
            target: a.instance().clone(),
            grace: Duration::from_secs(20),
        };
        assert!(a.accept_handoff(request).await.is_err());
    }
    let before = members.renewals.load(Ordering::SeqCst);
    let started = tokio::time::Instant::now();
    assert!(matches!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    assert!(members.renewals.load(Ordering::SeqCst) >= before + 3);
    assert!(started.elapsed() <= Duration::from_secs(5));
}

#[tokio::test(start_paused = true)]
async fn incoming_unknown_then_read_only_rejection_cannot_be_reported_drained() {
    let world = World::new();
    let members = members(&world);
    let owners = Arc::new(SlowIncoming {
        store: world.store.clone(),
        reject: AtomicBool::new(false),
    });
    let a = start(&world, members, owners.clone(), vec![ShardId(42)]).await;
    let b = world.worker("b").await;
    let source = world.claim("b").await;
    warm(&[a.clone(), b]).await;
    let request = HandoffRequest {
        request_id: RequestId("late-write".into()),
        from: source,
        target: a.instance().clone(),
        grace: Duration::from_secs(20),
    };
    assert!(a.accept_handoff(request.clone()).await.is_err());
    owners.reject.store(true, Ordering::SeqCst);
    assert!(a.accept_handoff(request).await.is_err());
    assert!(matches!(
        a.drain(Duration::from_secs(3)).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    assert_eq!(
        world.store.liveness(a.instance()).await.unwrap(),
        Liveness::Alive
    );
}

#[tokio::test(start_paused = true)]
async fn slow_transfer_prefix_does_not_starve_later_quiescent_sessions() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    for n in 0..40 {
        let sid = Sid(format!("s0042.bad{n:03}"));
        world.pending(&sid).await;
        world.sessions.failed_transfers.lock().unwrap().push(sid);
    }
    let healthy = Sid("s0042.healthy".into());
    world.pending(&healthy).await;
    warm(&[a.clone(), b]).await;
    a.tick().await.unwrap();
    for s in a.acquired_sessions().unwrap() {
        a.recovery_completed(&s.ownership).await.unwrap();
    }
    assert!(matches!(
        a.drain(Duration::from_secs(3)).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    assert!(
        world
            .sessions
            .released
            .lock()
            .unwrap()
            .iter()
            .any(|s| s.sid == healthy)
    );
    assert!(!world.data.watched.lock().unwrap().contains(&healthy));
    assert_eq!(world.data.watched.lock().unwrap().len(), 40);
}

#[tokio::test(start_paused = true)]
async fn local_release_cannot_forget_an_in_flight_session_transfer() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    world.pending(&sid()).await;
    warm(&[a.clone(), b]).await;
    let owner = a.acquired_sessions().unwrap()[0].ownership.clone();
    a.recovery_completed(&owner).await.unwrap();
    world.sessions.hold_transfer.store(true, Ordering::SeqCst);
    let draining = {
        let a = a.clone();
        tokio::spawn(async move { a.drain(Duration::from_secs(3)).await })
    };
    world.sessions.transfer_entered.notified().await;
    assert!(a.release_session(&owner).await.is_err());
    world.sessions.failed_transfers.lock().unwrap().push(sid());
    world.sessions.transfer_release.notify_one();
    assert!(matches!(
        draining.await.unwrap().unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    assert!(world.sessions.released.lock().unwrap().is_empty());
    assert_eq!(world.data.watched.lock().unwrap().as_slice(), &[sid()]);
}

struct SerialIncoming {
    store: Arc<bcs_dist_store::coordination::CoordinationStore>,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
    calls: AtomicUsize,
}
#[async_trait]
impl ShardLeasePort for SerialIncoming {
    async fn current(&self, s: ShardId) -> Result<Option<ShardRecord>, StoreError> {
        self.store.current(s).await
    }
    async fn claim(&self, r: ClaimShard) -> Result<LeaseClaim, StoreError> {
        self.store.claim(r).await
    }
    async fn handoff(&self, _r: HandoffRequest) -> Result<HandoffOutcome, StoreError> {
        if self.calls.fetch_add(1, Ordering::SeqCst) == 0 {
            self.entered.notify_one();
            self.release.notified().await;
            Ok(HandoffOutcome::Rejected)
        } else {
            Err(StoreError::OutcomeUnknown {
                operation_id: "overlapping-write".into(),
            })
        }
    }
    async fn source_done(&self, c: &ShardClaim, i: &RequestId) -> Result<bool, StoreError> {
        self.store.source_done(c, i).await
    }
    async fn clear_handoff(&self, c: &ShardClaim, i: &RequestId) -> Result<bool, StoreError> {
        self.store.clear_handoff(c, i).await
    }
}
#[tokio::test(start_paused = true)]
async fn same_id_incoming_retries_serialize_and_retired_waiters_only_confirm() {
    let world = World::new();
    let owners = Arc::new(SerialIncoming {
        store: world.store.clone(),
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Notify::new(),
        calls: AtomicUsize::new(0),
    });
    let a = start(&world, members(&world), owners.clone(), vec![ShardId(42)]).await;
    let b = world.worker("b").await;
    let source = world.claim("b").await;
    warm(&[a.clone(), b]).await;
    let request = HandoffRequest {
        request_id: RequestId("same-id".into()),
        from: source,
        target: a.instance().clone(),
        grace: Duration::from_secs(20),
    };
    let first = {
        let a = a.clone();
        let request = request.clone();
        tokio::spawn(async move { a.accept_handoff(request).await })
    };
    owners.entered.notified().await;
    let retry = {
        let a = a.clone();
        tokio::spawn(async move { a.accept_handoff(request).await })
    };
    for _ in 0..4 {
        tokio::task::yield_now().await;
    }
    assert_eq!(owners.calls.load(Ordering::SeqCst), 1);
    owners.release.notify_one();
    assert_eq!(first.await.unwrap().unwrap(), HandoffOutcome::Rejected);
    assert_eq!(retry.await.unwrap().unwrap(), HandoffOutcome::Rejected);
    assert_eq!(owners.calls.load(Ordering::SeqCst), 1);
    assert_eq!(a.snapshot().await.unwrap().active_work, 0);
}

#[tokio::test(start_paused = true)]
async fn queued_retry_cannot_modify_a_replacement_incoming_entry() {
    let world = World::new();
    let owners = Arc::new(SerialIncoming {
        store: world.store.clone(),
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Notify::new(),
        calls: AtomicUsize::new(0),
    });
    let a = start(&world, members(&world), owners.clone(), vec![ShardId(42)]).await;
    let b = world.worker("b").await;
    let source = world.claim("b").await;
    warm(&[a.clone(), b]).await;
    let request = HandoffRequest {
        request_id: RequestId("reused-id".into()),
        from: source,
        target: a.instance().clone(),
        grace: Duration::from_secs(20),
    };
    let first = a.accept_handoff(request.clone());
    tokio::pin!(first);
    assert!(futures::poll!(&mut first).is_pending());
    let old_waiter = a.accept_handoff(request.clone());
    tokio::pin!(old_waiter);
    assert!(futures::poll!(&mut old_waiter).is_pending());
    owners.release.notify_one();
    assert_eq!(first.await.unwrap(), HandoffOutcome::Rejected);
    // Reinsert the same ID with a fresh gate and an uncertain write, before the
    // old waiter is polled again. Identity equality alone cannot distinguish it.
    assert!(a.accept_handoff(request).await.is_err());
    assert_eq!(old_waiter.await.unwrap(), HandoffOutcome::Rejected);
    assert_eq!(owners.calls.load(Ordering::SeqCst), 2);
    assert_eq!(a.snapshot().await.unwrap().active_work, 1);
}
