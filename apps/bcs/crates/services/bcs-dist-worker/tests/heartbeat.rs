#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "runtime timing assertions"
)]
mod support;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_worker::{WorkerConfig, WorkerRuntime};
use bcs_service_api::{
    port::dist_runtime::DistRuntimeControlPort,
    port::repo::distributed::{common::*, ownership::*},
};
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use support::*;
struct SlowMembers {
    store: Arc<bcs_dist_store::coordination::CoordinationStore>,
    renewals: AtomicUsize,
    pages: AtomicUsize,
}
#[async_trait]
impl MembershipPort for SlowMembers {
    async fn register(
        &self,
        instance: Instance,
        role: RuntimeRole,
        address: String,
        ttl: Duration,
    ) -> Result<MembershipLease, StoreError> {
        self.store.register(instance, role, address, ttl).await
    }
    async fn renew(&self, lease: &MembershipLease, ttl: Duration) -> Result<Renewal, StoreError> {
        self.renewals.fetch_add(1, Ordering::SeqCst);
        self.store.renew(lease, ttl).await
    }
    async fn set_draining(&self, lease: &MembershipLease) -> Result<Renewal, StoreError> {
        self.store.set_draining(lease).await
    }
    async fn retire(&self, lease: &MembershipLease) -> Result<(), StoreError> {
        self.store.retire(lease).await
    }
    async fn liveness(&self, instance: &Instance) -> Result<Liveness, StoreError> {
        self.store.liveness(instance).await
    }
    async fn members(
        &self,
        _role: RuntimeRole,
        _page: PageRequest,
    ) -> Result<Page<MemberRecord>, StoreError> {
        self.pages.fetch_add(1, Ordering::SeqCst);
        tokio::time::sleep(Duration::from_millis(400)).await;
        Ok(Page {
            items: vec![],
            next: Some(PageToken("next".into())),
        })
    }
}
#[tokio::test(start_paused = true)]
async fn successful_slow_scan_does_not_starve_heartbeats() {
    let world = World::new();
    let members = Arc::new(SlowMembers {
        store: world.store.clone(),
        renewals: AtomicUsize::new(0),
        pages: AtomicUsize::new(0),
    });
    let runtime = WorkerRuntime::start(
        "slow".into(),
        "http://slow.example.test:8080".into(),
        WorkerConfig {
            shards: vec![ShardId(42)],
            max_member_pages: 16,
            ..WorkerConfig::default()
        },
        members.clone(),
        world.store.clone(),
        world.store.clone(),
        Arc::new(Inbox(world.data.clone())),
        world.scheduling.clone(),
        world.peers.clone(),
        Arc::new(|_| Ok(initial())),
    )
    .await
    .unwrap();
    let (stop, receiver) = tokio::sync::watch::channel(false);
    let task = {
        let runtime = runtime.clone();
        tokio::spawn(async move { runtime.run(receiver).await })
    };
    tokio::time::sleep(Duration::from_secs(6)).await;
    assert!(members.pages.load(Ordering::SeqCst) >= 4);
    assert!(members.renewals.load(Ordering::SeqCst) >= 5);
    assert!(runtime.snapshot().await.unwrap().ready);
    stop.send(true).unwrap();
    task.await.unwrap().unwrap();
}
