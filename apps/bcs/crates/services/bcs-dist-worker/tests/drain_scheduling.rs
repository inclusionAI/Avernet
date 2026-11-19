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
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use support::*;
use tokio::time::Instant;

const CHECK: Duration = Duration::from_millis(500);

async fn ready(world: &World, a: &Arc<WorkerRuntime>, count: usize) {
    for n in 0..count {
        world.pending(&Sid(format!("s0042.bulk{n:04}"))).await;
    }
    for _ in 0..count.div_ceil(8) + 16 {
        a.tick().await.unwrap();
        if a.acquired_sessions().unwrap().len() == count {
            break;
        }
    }
    let sessions = a.acquired_sessions().unwrap();
    assert_eq!(sessions.len(), count);
    for s in sessions {
        a.recovery_completed(&s.ownership).await.unwrap();
    }
}

#[track_caller]
fn assert_backoff(times: &[Instant]) {
    assert!(times.len() >= 2, "the failure must actually be retried");
    for pair in times.windows(2) {
        assert!(
            pair[1].duration_since(pair[0]) >= CHECK,
            "retry bypassed per-item backoff"
        );
    }
}

fn assert_continuous_batches(calls: &[(Sid, Instant)], delay: Duration) {
    // Observe the next batch starting as soon as the last concurrent group of
    // the preceding batch completes, without an extra periodic scan sleep.
    for batch in 1..calls.len().div_ceil(32) {
        let gap = calls[batch * 32].1.duration_since(calls[batch * 32 - 1].1);
        assert!(
            gap < delay + CHECK / 2,
            "progressing batches slept for {gap:?}"
        );
    }
}

async fn bulk(delay: Duration) {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    warm(&[a.clone(), b]).await;
    ready(&world, &a, 3000).await;
    *world.sessions.transfer_delay.lock().unwrap() = delay;
    // Ample budget even for the old scheduler: the regression asserts gaps,
    // rather than relying on an arbitrary end-to-end speed threshold.
    let grace = Duration::from_secs(120) + delay * 3000;
    assert_eq!(a.drain(grace).await.unwrap(), DrainOutcome::Drained);
    let calls = world.sessions.transfer_calls.lock().unwrap();
    assert_eq!(calls.len(), 3000);
    assert_continuous_batches(&calls, delay);
    assert_eq!(world.sessions.released.lock().unwrap().len(), 3000);
}

#[tokio::test(start_paused = true)]
async fn three_thousand_zero_latency_sessions_have_no_inter_batch_scan_sleep() {
    bulk(Duration::ZERO).await;
}

#[tokio::test(start_paused = true)]
async fn three_thousand_delayed_sessions_have_no_inter_batch_scan_sleep() {
    bulk(Duration::from_millis(20)).await;
}

#[tokio::test(start_paused = true)]
async fn sole_temporarily_rejected_target_is_retried_after_cooldown() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    warm(&[a.clone(), b]).await;
    world.peers.rejected_nodes.lock().unwrap().push("b".into());
    let peers = world.peers.clone();
    let restore = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(2)).await;
        peers.rejected_nodes.lock().unwrap().clear();
    });
    assert_eq!(
        a.drain(Duration::from_secs(10)).await.unwrap(),
        DrainOutcome::Drained
    );
    restore.await.unwrap();
    let requests = world.peers.request_times.lock().unwrap().clone();
    assert_backoff(&requests.iter().map(|(_, t)| *t).collect::<Vec<_>>());
    assert!(
        requests
            .windows(2)
            .all(|p| p[0].0.request_id != p[1].0.request_id)
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
}

#[tokio::test(start_paused = true)]
async fn persistent_rejection_retries_are_bounded_until_deadline() {
    let world = World::new();
    let a = world.worker("a").await;
    let b = world.worker("b").await;
    world.claim("a").await;
    warm(&[a.clone(), b]).await;
    world.peers.rejected_nodes.lock().unwrap().push("b".into());
    let start = Instant::now();
    assert!(matches!(
        a.drain(Duration::from_secs(5)).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    assert!(start.elapsed() >= Duration::from_secs(4) && start.elapsed() <= Duration::from_secs(5));
    let requests = world.peers.request_times.lock().unwrap();
    assert_backoff(&requests.iter().map(|(_, t)| *t).collect::<Vec<_>>());
    assert!(requests.len() <= 10);
}

// Faults are injected at repository boundaries; normal operations retain the
// real coordination store and its CAS/lease checks.
struct FaultOwners {
    store: Arc<bcs_dist_store::coordination::CoordinationStore>,
    claims: Mutex<Vec<Instant>>,
    incoming: Mutex<Vec<Instant>>,
    reads: Mutex<Vec<(ShardId, Instant)>>,
}
#[async_trait]
impl ShardLeasePort for FaultOwners {
    async fn current(&self, s: ShardId) -> Result<Option<ShardRecord>, StoreError> {
        self.reads.lock().unwrap().push((s, Instant::now()));
        self.store.current(s).await
    }
    async fn claim(&self, r: ClaimShard) -> Result<LeaseClaim, StoreError> {
        if r.shard == ShardId(44) {
            self.claims.lock().unwrap().push(Instant::now());
            return Err(StoreError::OutcomeUnknown {
                operation_id: r.request_id.0,
            });
        }
        self.store.claim(r).await
    }
    async fn handoff(&self, r: HandoffRequest) -> Result<HandoffOutcome, StoreError> {
        self.incoming.lock().unwrap().push(Instant::now());
        Err(StoreError::OutcomeUnknown {
            operation_id: r.request_id.0,
        })
    }
    async fn source_done(&self, c: &ShardClaim, i: &RequestId) -> Result<bool, StoreError> {
        self.store.source_done(c, i).await
    }
    async fn clear_handoff(&self, c: &ShardClaim, i: &RequestId) -> Result<bool, StoreError> {
        self.store.clear_handoff(c, i).await
    }
}
struct Members {
    store: Arc<bcs_dist_store::coordination::CoordinationStore>,
    renewals: AtomicUsize,
}
#[async_trait]
impl MembershipPort for Members {
    async fn register(
        &self,
        i: Instance,
        r: RuntimeRole,
        a: String,
        t: Duration,
    ) -> Result<MembershipLease, StoreError> {
        self.store.register(i, r, a, t).await
    }
    async fn renew(&self, l: &MembershipLease, t: Duration) -> Result<Renewal, StoreError> {
        self.renewals.fetch_add(1, Ordering::SeqCst);
        self.store.renew(l, t).await
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
        self.store.members(r, p).await
    }
}

async fn mixed_failures(shard_budget: usize) {
    let world = World::new();
    let owners = Arc::new(FaultOwners {
        store: world.store.clone(),
        claims: Mutex::default(),
        incoming: Mutex::default(),
        reads: Mutex::default(),
    });
    let members = Arc::new(Members {
        store: world.store.clone(),
        renewals: AtomicUsize::new(0),
    });
    let shards = (42..=46).map(ShardId).collect::<Vec<_>>();
    let a = WorkerRuntime::start(
        "a".into(),
        "http://a.example.test".into(),
        WorkerConfig {
            shards: shards.clone(),
            max_shards_per_tick: shard_budget,
            max_active_sessions: 10_000,
            ..WorkerConfig::default()
        },
        members.clone(),
        owners.clone(),
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
    let b = world
        .worker_with_config(
            "b",
            WorkerConfig {
                shards,
                ..WorkerConfig::default()
            },
        )
        .await;
    let c = world
        .store
        .register(
            instance("c"),
            RuntimeRole::Worker,
            "http://c.example.test".into(),
            Duration::from_secs(60),
        )
        .await
        .unwrap();
    let mut source = None;
    for n in 42..=46 {
        let result = world
            .store
            .claim(ClaimShard {
                shard: ShardId(n),
                me: if n == 44 || n == 45 {
                    instance("c")
                } else {
                    a.instance().clone()
                },
                expected_epoch: None,
                request_id: RequestId(format!("setup-{n}")),
            })
            .await
            .unwrap();
        if n == 45 {
            let LeaseClaim::Claimed(claim) = result else {
                panic!("claim failed")
            };
            source = Some(claim);
        }
    }
    warm(&[a.clone(), b.clone()]).await;
    ready(&world, &a, 3000).await;
    let bad = Sid("s0046.failed-transfer".into());
    let cleanup = Sid("s0046.failed-cleanup".into());
    for sid in [&cleanup, &bad] {
        world.pending(sid).await;
    }
    for _ in 0..10 {
        a.tick().await.unwrap();
    }
    let failed_sessions = a.acquired_sessions().unwrap();
    assert_eq!(failed_sessions.len(), 2);
    for s in failed_sessions {
        a.recovery_completed(&s.ownership).await.unwrap();
    }
    world
        .sessions
        .failed_transfers
        .lock()
        .unwrap()
        .push(bad.clone());
    world
        .sessions
        .failed_releases
        .lock()
        .unwrap()
        .push(cleanup.clone());
    world.peers.unknown_shards.lock().unwrap().push(ShardId(43));
    assert!(
        a.accept_handoff(HandoffRequest {
            from: source.unwrap(),
            target: a.instance().clone(),
            request_id: RequestId("incoming-unknown".into()),
            grace: Duration::from_secs(60)
        })
        .await
        .is_err()
    );
    world.store.retire(&c).await.unwrap();
    // Let a become an eligible claimant for the now dead c, regardless of HRW rank.
    for _ in 0..10 {
        let _ = a.tick().await;
        if !owners.claims.lock().unwrap().is_empty() {
            break;
        }
        tokio::time::advance(Duration::from_millis(100)).await;
    }
    assert!(!owners.claims.lock().unwrap().is_empty());
    *world.sessions.transfer_delay.lock().unwrap() = Duration::from_millis(20);
    let start = Instant::now();
    let renewals = members.renewals.load(Ordering::SeqCst);
    owners.reads.lock().unwrap().clear();
    assert!(matches!(
        a.drain(Duration::from_secs(30)).await.unwrap(),
        DrainOutcome::DeadlineReached { .. }
    ));
    assert!(
        start.elapsed() >= Duration::from_secs(29) && start.elapsed() <= Duration::from_secs(30)
    );
    assert!(members.renewals.load(Ordering::SeqCst) > renewals + 10);
    let calls = world.sessions.transfer_calls.lock().unwrap();
    let healthy = calls
        .iter()
        .filter(|(s, _)| s.0.starts_with("s0042."))
        .cloned()
        .collect::<Vec<_>>();
    assert_eq!(healthy.len(), 3000);
    assert_continuous_batches(&healthy, Duration::from_millis(20));
    assert!(
        calls
            .iter()
            .any(|(s, t)| *s == bad && *t < healthy.last().unwrap().1)
    );
    assert_backoff(
        &calls
            .iter()
            .filter(|(s, _)| *s == bad)
            .map(|(_, t)| *t)
            .collect::<Vec<_>>(),
    );
    assert_backoff(
        &world
            .sessions
            .release_calls
            .lock()
            .unwrap()
            .iter()
            .filter(|(s, _)| *s == cleanup)
            .map(|(_, t)| *t)
            .collect::<Vec<_>>(),
    );
    assert_backoff(
        &owners
            .claims
            .lock()
            .unwrap()
            .iter()
            .copied()
            .filter(|t| *t >= start)
            .collect::<Vec<_>>(),
    );
    assert_backoff(
        &owners
            .incoming
            .lock()
            .unwrap()
            .iter()
            .copied()
            .filter(|t| *t >= start)
            .collect::<Vec<_>>(),
    );
    let peers = world.peers.request_times.lock().unwrap();
    let unknown = peers
        .iter()
        .filter(|(r, _)| r.from.shard == ShardId(43))
        .collect::<Vec<_>>();
    assert_backoff(&unknown.iter().map(|(_, t)| *t).collect::<Vec<_>>());
    assert!(unknown.windows(2).all(|p| p[0].0 == p[1].0));
    let reads = owners
        .reads
        .lock()
        .unwrap()
        .iter()
        .filter(|(s, t)| *s == ShardId(46) && *t >= start)
        .map(|(_, t)| *t)
        .collect::<Vec<_>>();
    let mut work = calls
        .iter()
        .filter(|(s, _)| s.0.starts_with("s0046."))
        .map(|(_, t)| *t)
        .collect::<Vec<_>>();
    work.extend(
        world
            .sessions
            .release_calls
            .lock()
            .unwrap()
            .iter()
            .filter(|(s, _)| *s == cleanup)
            .map(|(_, t)| *t),
    );
    // Different sessions may become due at different instants. Every owner
    // read must serve due work; fast healthy rounds alone must cause no reads.
    for (index, read) in reads.iter().enumerate() {
        let end = reads.get(index + 1).copied().unwrap_or(Instant::now());
        assert!(
            work.iter().any(|t| *t >= *read && *t < end),
            "owner reread without any due session work"
        );
    }
}

#[tokio::test(start_paused = true)]
async fn healthy_progress_does_not_accelerate_unrelated_unknown_or_failed_operations() {
    mixed_failures(32).await;
}

#[tokio::test(start_paused = true)]
async fn cooling_shard_cannot_consume_the_only_scan_slot_and_stall_healthy_progress() {
    mixed_failures(1).await;
}
