//! Health, capacity, drain and membership of the engine.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use async_trait::async_trait;
use bcs_dist_connector::ConnectorEngine;
use bcs_dist_model::*;
use bcs_service_api::port::dist_runtime::DistRuntimeControlPort;
use bcs_service_api::port::repo::distributed::common::{Page, PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::ownership::*;
use bcs_service_api::types::dist_runtime::DrainOutcome;
use rig::*;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

fn second_did() -> Did {
    Did("s0042.7f3a9c.b21e.n3.a1".into())
}

#[tokio::test]
async fn snapshot_reports_readiness_and_work() {
    let rig = Rig::new();
    let engine = rig.engine();
    let idle = engine.snapshot().await.unwrap();
    assert_eq!(idle.instance, instance("conn-03"));
    assert!(!idle.ready, "not ready before the loop opened the feed");
    assert_eq!((idle.active_work, idle.pending_effects, idle.draining), (0, 0, false));

    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("ready with one call", || async {
        let s = running.engine.snapshot().await.unwrap();
        s.ready && s.active_work == 1
    })
    .await;
    events.send(final_event()).unwrap();
    until("idle again", || async { running.active().await == 0 }).await;
}

#[tokio::test]
async fn at_capacity_no_hints_are_read() {
    let mut rig = Rig::new();
    rig.config.max_active_calls = 1;
    let first = rig.agent.start_stream();
    let second = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.create_spec(dispatch_spec(&second_did())).await;
    rig.worker.notify().await;
    rig.worker.notify_did(&second_did()).await;
    let running = rig.start();
    until("the first call is accepted", || async { rig.agent.count("start") == 1 }).await;
    settle().await;
    assert_eq!(rig.agent.count("start"), 1, "the second hint waits while the Connector is full");
    assert_eq!(running.active().await, 1);
    first.send(final_event()).unwrap();
    until("the second call starts once there is room", || async { rig.agent.count("start") == 2 }).await;
    second.send(final_event()).unwrap();
    until("both ended", || async { running.active().await == 0 }).await;
}

#[tokio::test]
async fn drain_stops_claiming_and_leaves_running_calls_held() {
    let rig = Rig::new();
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;

    let outcome = running.engine.drain(Duration::from_millis(60)).await.unwrap();
    assert_eq!(outcome, DrainOutcome::DeadlineReached { remaining: 1 });
    let snapshot = running.engine.snapshot().await.unwrap();
    assert!(snapshot.draining && !snapshot.ready);
    assert_eq!(snapshot.active_work, 1, "the call is not aborted");
    // D8: the release step is a stub; the call stays held and recoverable.
    assert_eq!(*rig.recovery.released.lock().unwrap(), vec![did()]);
    assert_eq!(rig.phase().await, "accepted");
    assert_eq!(rig.doc().await["execution"]["holder"]["instance"]["node"], "conn-03");
    assert_eq!(held_members(&rig.store, &instance("conn-03")).await, vec![DID.to_owned()]);
    assert!(running.task.await.unwrap().is_ok(), "the loop ended cleanly");

    // No new claims after drain.
    rig.worker.create_spec(dispatch_spec(&second_did())).await;
    rig.worker.notify_did(&second_did()).await;
    settle().await;
    assert_eq!(rig.agent.count("start"), 1);

    // The held call can still finish; a second drain then reports drained.
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(running.engine.drain(Duration::from_millis(200)).await.unwrap(), DrainOutcome::Drained);
}

#[tokio::test]
async fn drain_returns_as_soon_as_held_calls_end() {
    let rig = Rig::new();
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let engine = running.engine.clone();
    let drain = tokio::spawn(async move { engine.drain(Duration::from_secs(5)).await });
    tokio::time::sleep(Duration::from_millis(20)).await;
    events.send(final_event()).unwrap();
    let outcome = tokio::time::timeout(Duration::from_secs(2), drain).await.unwrap().unwrap().unwrap();
    assert_eq!(outcome, DrainOutcome::Drained);
    assert!(rig.recovery.released.lock().unwrap().is_empty());
}

#[tokio::test]
async fn invalid_settings_are_refused() {
    let rig = Rig::new();
    let mut config = rig.config.clone();
    config.max_active_calls = 0;
    assert_eq!(
        ConnectorEngine::new(config, rig.ports()).map(|_| ()).unwrap_err().code,
        "connector_config_invalid"
    );
}

/// A membership provider that works until it is told the lease is lost.
#[derive(Default)]
struct Leases {
    calls: Mutex<Vec<String>>,
    renewals: AtomicUsize,
    lose_after: usize,
}

#[async_trait]
impl MembershipPort for Leases {
    async fn register(&self, instance: Instance, role: RuntimeRole, ttl: Duration) -> Result<MembershipLease, StoreError> {
        self.calls.lock().unwrap().push(format!("register {} {role:?} {}", instance.node, ttl.as_secs()));
        Ok(MembershipLease { instance })
    }
    async fn renew(&self, _: &MembershipLease, _: Duration) -> Result<Renewal, StoreError> {
        let n = self.renewals.fetch_add(1, Ordering::SeqCst);
        Ok(if n >= self.lose_after { Renewal::Lost } else { Renewal::Renewed })
    }
    async fn set_draining(&self, lease: &MembershipLease) -> Result<Renewal, StoreError> {
        self.calls.lock().unwrap().push(format!("draining {}", lease.instance.node));
        Ok(Renewal::Renewed)
    }
    async fn liveness(&self, _: &Instance) -> Result<Liveness, StoreError> {
        Ok(Liveness::Unknown)
    }
    async fn members(&self, _: RuntimeRole, _: PageRequest) -> Result<Page<Instance>, StoreError> {
        Ok(Page { items: Vec::new(), next: None })
    }
}

#[tokio::test]
async fn the_connector_registers_renews_and_marks_draining() {
    let mut rig = Rig::new();
    let leases = Arc::new(Leases { lose_after: usize::MAX, ..Leases::default() });
    rig.membership = leases.clone();
    let running = rig.start();
    until("renewed", || async { leases.renewals.load(Ordering::SeqCst) >= 2 }).await;
    assert_eq!(leases.calls.lock().unwrap()[0], "register conn-03 Connector 3");
    assert_eq!(running.engine.drain(Duration::from_millis(50)).await.unwrap(), DrainOutcome::Drained);
    assert!(leases.calls.lock().unwrap().contains(&"draining conn-03".to_owned()));
}

#[tokio::test]
async fn a_lost_registration_stops_claiming_but_not_held_calls() {
    let mut rig = Rig::new();
    let leases = Arc::new(Leases { lose_after: 3, ..Leases::default() });
    rig.membership = leases.clone();
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let stopped = tokio::time::timeout(Duration::from_secs(2), running.task).await.unwrap().unwrap();
    assert_eq!(stopped.unwrap_err().code, "membership_lost");
    let snapshot = running.engine.snapshot().await.unwrap();
    assert!(!snapshot.ready);
    assert_eq!(snapshot.active_work, 1, "the held call continues; its dispatch fences it");
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
}

#[tokio::test]
async fn the_pending_membership_stub_does_not_stop_the_connector() {
    // MembershipPort provider pending from the executor team: registration
    // fails with need_contract and the Connector runs without it.
    let rig = Rig::new();
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    assert!(running.engine.snapshot().await.unwrap().ready);
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
}

#[tokio::test]
async fn the_registration_is_kept_alive_during_the_drain_grace_period() {
    let mut rig = Rig::new();
    let leases = Arc::new(Leases { lose_after: usize::MAX, ..Leases::default() });
    rig.membership = leases.clone();
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let engine = running.engine.clone();
    let drain = tokio::spawn(async move { engine.drain(Duration::from_secs(5)).await });
    until("the loop stopped", || async { running.task.is_finished() }).await;
    let before = leases.renewals.load(Ordering::SeqCst);
    // The main loop no longer runs, yet the held call's instance must not
    // look dead to other Connectors while it waits.
    until("renewed during the grace period", || async { leases.renewals.load(Ordering::SeqCst) >= before + 3 }).await;
    events.send(final_event()).unwrap();
    assert_eq!(drain.await.unwrap().unwrap(), DrainOutcome::Drained);
}
