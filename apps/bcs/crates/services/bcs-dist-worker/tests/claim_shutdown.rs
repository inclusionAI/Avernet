#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "runtime race assertions"
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
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use support::*;
use tokio::sync::Notify;
struct DelayedClaim {
    store: Arc<bcs_dist_store::coordination::CoordinationStore>,
    entered: Notify,
    release: Notify,
    delay: AtomicBool,
}
#[async_trait]
impl ShardLeasePort for DelayedClaim {
    async fn current(&self, shard: ShardId) -> Result<Option<ShardRecord>, StoreError> {
        self.store.current(shard).await
    }
    async fn claim(&self, request: ClaimShard) -> Result<LeaseClaim, StoreError> {
        if self.delay.load(Ordering::SeqCst) {
            self.entered.notify_one();
            self.release.notified().await;
        }
        self.store.claim(request).await
    }
    async fn handoff(&self, request: HandoffRequest) -> Result<HandoffOutcome, StoreError> {
        self.store.handoff(request).await
    }
    async fn source_done(&self, target: &ShardClaim, id: &RequestId) -> Result<bool, StoreError> {
        self.store.source_done(target, id).await
    }
    async fn clear_handoff(&self, target: &ShardClaim, id: &RequestId) -> Result<bool, StoreError> {
        self.store.clear_handoff(target, id).await
    }
}
#[tokio::test(start_paused = true)]
async fn shutdown_counts_a_claim_already_admitted_before_its_response() {
    let world = World::new();
    let owners = Arc::new(DelayedClaim {
        store: world.store.clone(),
        entered: Notify::new(),
        release: Notify::new(),
        delay: AtomicBool::new(true),
    });
    let runtime = WorkerRuntime::start(
        "a".into(),
        "http://a.example.test:8080".into(),
        WorkerConfig {
            shards: vec![ShardId(42)],
            ..WorkerConfig::default()
        },
        world.store.clone(),
        owners.clone(),
        world.store.clone(),
        Arc::new(Inbox(world.data.clone())),
        world.scheduling.clone(),
        world.peers.clone(),
        Arc::new(|_| Ok(initial())),
    )
    .await
    .unwrap();
    for _ in 0..2 {
        tokio::time::advance(Duration::from_secs(1)).await;
        runtime.tick().await.unwrap();
    }
    tokio::time::advance(Duration::from_secs(1)).await;
    let task = {
        let runtime = runtime.clone();
        tokio::spawn(async move { runtime.tick().await })
    };
    owners.entered.notified().await;
    assert!(
        matches!(runtime.drain(Duration::from_millis(20)).await.unwrap(),DrainOutcome::DeadlineReached{remaining} if remaining>0)
    );
    // Deadline with outstanding operations must NOT retire the lifetime and
    // falsely claim they have been cancelled. No new round may start afterwards.
    assert_eq!(
        world.store.liveness(runtime.instance()).await.unwrap(),
        Liveness::Alive
    );
    owners.delay.store(false, Ordering::SeqCst);
    owners.release.notify_waiters();
    let _ = task.await.unwrap();
    assert!(!runtime.snapshot().await.unwrap().ready);
    assert!(runtime.heartbeat().await.is_err());
}
