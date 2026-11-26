//! Health, capacity, drain and membership of the engine.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use async_trait::async_trait;
use bcs_dist_connector::ConnectorEngine;
use bcs_dist_model::*;
use bcs_service_api::port::dist_runtime::DistRuntimeControlPort;
use bcs_service_api::port::repo::distributed::common::{Page, PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::dispatch::{
    ClaimOutcome, CommandFeedPort, DispatchClaimRequest, DispatchQueuePort, ResumeOwnedDispatch,
};
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
    let rig = Rig::new().await;
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
    let mut rig = Rig::new().await;
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
async fn drain_stops_claiming_and_stops_calls_still_running_at_the_deadline() {
    let rig = Rig::new().await;
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;

    // The open event stream keeps the call from a safe point until the
    // deadline; with no peer, the partition is not handed over either.
    let outcome = running.engine.drain(Duration::from_millis(60)).await.unwrap();
    assert_eq!(outcome, DrainOutcome::DeadlineReached { remaining: 2 });
    let snapshot = running.engine.snapshot().await.unwrap();
    assert!(snapshot.draining && !snapshot.ready);
    assert_eq!(snapshot.active_work, 0, "the call's actor is stopped, not left running");
    // The dispatch stays held, so the partition's next owner (here: whoever
    // claims it once this instance is dead) takes it over.
    assert_eq!(rig.phase().await, "accepted");
    assert_eq!(rig.doc().await["execution"]["holder"]["instance"]["node"], "conn-03");
    assert_eq!(held_members(&rig.store).await, vec![DID.to_owned()]);
    assert!(running.task.await.unwrap().is_ok(), "the loop ended cleanly");

    // A stopped actor reports nothing more, and no new claims happen.
    let reported = rig.inbox_events().await.len();
    let _ = events.send(final_event());
    rig.worker.create_spec(dispatch_spec(&second_did())).await;
    rig.worker.notify_did(&second_did()).await;
    settle().await;
    assert_eq!(rig.inbox_events().await.len(), reported, "a stopped actor reports nothing more");
    assert_eq!(rig.agent.count("start"), 1);
    assert_eq!(rig.phase().await, "accepted");
    assert_eq!(
        running.engine.drain(Duration::from_millis(50)).await.unwrap(),
        DrainOutcome::DeadlineReached { remaining: 2 },
        "a later drain reports the one shutdown's outcome"
    );
}

#[tokio::test]
async fn drain_returns_as_soon_as_held_calls_end() {
    let rig = Rig::new().await;
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
    // No peer to take the partition: it is left for a claim once retired.
    assert_eq!(outcome, DrainOutcome::DeadlineReached { remaining: 1 });
    assert_eq!(rig.phase().await, "ended");
}

#[tokio::test]
async fn invalid_settings_are_refused() {
    let rig = Rig::new().await;
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
    /// Answers of the first registrations; afterwards they succeed.
    register_errors: Mutex<Vec<StoreError>>,
}

#[async_trait]
impl MembershipPort for Leases {
    async fn register(&self, instance: Instance, role: RuntimeRole, _: String, ttl: Duration) -> Result<MembershipLease, StoreError> {
        self.calls.lock().unwrap().push(format!("register {} {role:?} {}", instance.node, ttl.as_secs()));
        let mut errors = self.register_errors.lock().unwrap();
        if !errors.is_empty() {
            return Err(errors.remove(0));
        }
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
    async fn retire(&self, lease: &MembershipLease) -> Result<(), StoreError> {
        self.calls.lock().unwrap().push(format!("retire {}", lease.instance.node));
        Ok(())
    }
    async fn liveness(&self, _: &Instance) -> Result<Liveness, StoreError> {
        Ok(Liveness::Unknown)
    }
    async fn members(&self, _: RuntimeRole, _: PageRequest) -> Result<Page<MemberRecord>, StoreError> {
        Ok(Page { items: Vec::new(), next: None })
    }
}

#[tokio::test]
async fn the_connector_registers_renews_and_marks_draining() {
    let mut rig = Rig::new().await;
    let leases = Arc::new(Leases { lose_after: usize::MAX, ..Leases::default() });
    rig.membership = leases.clone();
    let running = rig.start();
    until("renewed", || async { leases.renewals.load(Ordering::SeqCst) >= 2 }).await;
    assert_eq!(leases.calls.lock().unwrap()[0], "register conn-03 Connector 3");
    assert_eq!(
        running.engine.drain(Duration::from_millis(50)).await.unwrap(),
        DrainOutcome::DeadlineReached { remaining: 1 },
        "no peer took the partition"
    );
    let calls = leases.calls.lock().unwrap().clone();
    let at = |call: &str| calls.iter().position(|c| c == call).unwrap_or_else(|| panic!("{call} missing: {calls:?}"));
    assert!(at("draining conn-03") < at("retire conn-03"), "marked draining first, retired at the end");
}

#[tokio::test]
async fn a_drain_that_stops_calls_retires_after_they_stopped() {
    let mut rig = Rig::new().await;
    let leases = Arc::new(Leases { lose_after: usize::MAX, ..Leases::default() });
    rig.membership = leases.clone();
    let _events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let outcome = running.engine.drain(Duration::from_millis(60)).await.unwrap();
    assert_eq!(outcome, DrainOutcome::DeadlineReached { remaining: 2 });
    assert_eq!(running.engine.snapshot().await.unwrap().active_work, 0);
    assert!(leases.calls.lock().unwrap().contains(&"retire conn-03".to_owned()));
}

#[tokio::test]
async fn a_refused_registration_fails_the_start() {
    let mut rig = Rig::new().await;
    let refusal = StoreError::Invalid { code: "registration".into() };
    let leases = Arc::new(Leases { lose_after: usize::MAX, register_errors: Mutex::new(vec![refusal]), ..Leases::default() });
    rig.membership = leases.clone();
    rig.worker.create().await;
    rig.worker.notify().await;
    let engine = ConnectorEngine::new(rig.config.clone(), rig.ports()).unwrap();
    assert_eq!(engine.run().await.unwrap_err().code, "membership_registration_refused");
    assert_eq!(rig.phase().await, "created", "nothing is claimed without a registration");
}

#[tokio::test]
async fn an_uncertain_registration_is_asked_again_before_any_claim() {
    let mut rig = Rig::new().await;
    let unknown = || StoreError::OutcomeUnknown { operation_id: "register".into() };
    let leases = Arc::new(Leases {
        lose_after: usize::MAX,
        register_errors: Mutex::new(vec![unknown(), unknown()]),
        ..Leases::default()
    });
    rig.membership = leases.clone();
    let _events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let registrations = leases.calls.lock().unwrap().iter().filter(|call| call.starts_with("register")).count();
    assert_eq!(registrations, 3, "asked again until it answered");
    assert!(leases.renewals.load(Ordering::SeqCst) >= 1, "a late registration is renewed before claiming");
}

#[tokio::test]
async fn a_registration_not_confirmed_within_its_lifetime_fails_the_start() {
    let mut rig = Rig::new().await;
    rig.config.membership_ttl = Duration::from_millis(40);
    // The first answer is lost; the lifetime then runs out unrenewed and the
    // provider refuses this incarnation for good.
    let mut answers = vec![StoreError::OutcomeUnknown { operation_id: "register".into() }];
    answers.extend((0..200).map(|_| StoreError::NotApplied { code: "registration_terminated".into() }));
    let leases = Arc::new(Leases { lose_after: usize::MAX, register_errors: Mutex::new(answers), ..Leases::default() });
    rig.membership = leases.clone();
    rig.worker.create().await;
    rig.worker.notify().await;
    let engine = ConnectorEngine::new(rig.config.clone(), rig.ports()).unwrap();
    let ended = tokio::time::timeout(Duration::from_secs(5), engine.run()).await.expect("the start must not hang");
    assert_eq!(ended.unwrap_err().code, "membership_lost");
    assert_eq!(rig.phase().await, "created");
}

#[tokio::test]
async fn a_lost_registration_stops_claiming_but_not_held_calls() {
    let mut rig = Rig::new().await;
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
    let rig = Rig::new().await;
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
    let mut rig = Rig::new().await;
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
    assert_eq!(drain.await.unwrap().unwrap(), DrainOutcome::DeadlineReached { remaining: 1 });
}

#[tokio::test]
async fn a_feed_outage_does_not_let_the_registration_expire() {
    use bcs_service_api::port::repo::distributed::dispatch::{
        ClaimOutcome, CommandDelivery, CommandFeedPort, DispatchClaimRequest, DispatchQueuePort, ResumeOwnedDispatch,
    };
    /// A queue whose command feed fails on every read.
    struct BrokenFeed;
    #[async_trait]
    impl CommandFeedPort for BrokenFeed {
        async fn receive(&mut self, _: std::num::NonZeroU32, _: Duration) -> Result<Vec<CommandDelivery>, StoreError> {
            Err(StoreError::NotApplied { code: "store_not_applied".into() })
        }
        async fn ack(&mut self, _: &CommandDelivery) -> Result<(), StoreError> {
            Ok(())
        }
    }
    struct BrokenQueue;
    #[async_trait]
    impl DispatchQueuePort for BrokenQueue {
        fn command_partitions(&self) -> u32 {
            1
        }
        fn partition_of(&self, _: &Did) -> u32 {
            0
        }
        async fn claim(&self, _: DispatchClaimRequest) -> Result<ClaimOutcome, StoreError> {
            Ok(ClaimOutcome::NotFound)
        }
        async fn resume_owned(&self, _: ResumeOwnedDispatch) -> Result<ClaimOutcome, StoreError> {
            Ok(ClaimOutcome::NotFound)
        }
        async fn notifications(&self, _: PoolId, _: Instance) -> Result<Box<dyn CommandFeedPort>, StoreError> {
            Ok(Box::new(BrokenFeed))
        }
    }
    let mut rig = Rig::new().await;
    // The feed backoff is far longer than the renewal period.
    rig.config.backoff_min = Duration::from_secs(20);
    rig.config.backoff_max = Duration::from_secs(60);
    let leases = Arc::new(Leases { lose_after: usize::MAX, ..Leases::default() });
    rig.membership = leases.clone();
    let mut ports = rig.ports();
    ports.queue = Arc::new(BrokenQueue);
    let engine = ConnectorEngine::new(rig.config.clone(), ports).unwrap();
    let looped = engine.clone();
    let task = tokio::spawn(async move { looped.run().await });
    until("renewals continue through the outage", || async { leases.renewals.load(Ordering::SeqCst) >= 4 }).await;
    assert!(!task.is_finished(), "the engine keeps running");
    task.abort();
}

/// A queue whose claims wait for a permit: holds the main loop inside a
/// claim for as long as a test needs.
struct GatedQueue {
    inner: Arc<dyn DispatchQueuePort>,
    gate: tokio::sync::Semaphore,
}

#[async_trait]
impl DispatchQueuePort for GatedQueue {
    fn command_partitions(&self) -> u32 {
        self.inner.command_partitions()
    }
    fn partition_of(&self, did: &Did) -> u32 {
        self.inner.partition_of(did)
    }
    async fn claim(&self, request: DispatchClaimRequest) -> Result<ClaimOutcome, StoreError> {
        let _permit = self.gate.acquire().await.unwrap();
        self.inner.claim(request).await
    }
    async fn resume_owned(&self, request: ResumeOwnedDispatch) -> Result<ClaimOutcome, StoreError> {
        self.inner.resume_owned(request).await
    }
    async fn notifications(&self, pool: PoolId, me: Instance) -> Result<Box<dyn CommandFeedPort>, StoreError> {
        self.inner.notifications(pool, me).await
    }
}

#[tokio::test]
async fn a_claim_that_wins_after_the_drain_began_starts_no_actor() {
    let mut rig = Rig::new().await;
    let leases = Arc::new(Leases { lose_after: usize::MAX, ..Leases::default() });
    rig.membership = leases.clone();
    let _events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let mut ports = rig.ports();
    let gated = Arc::new(GatedQueue { inner: ports.queue.clone(), gate: tokio::sync::Semaphore::new(0) });
    ports.queue = gated.clone();
    let engine = ConnectorEngine::new(rig.config.clone(), ports).unwrap();
    let looped = engine.clone();
    let task = tokio::spawn(async move { looped.run().await });
    until("registered", || async { leases.calls.lock().unwrap().iter().any(|c| c.starts_with("register")) }).await;
    settle().await;

    // The main loop is held inside the claim past the deadline. Draining
    // already refuses new actors, so the drain can retire at once.
    let outcome = engine.drain(Duration::from_millis(60)).await.unwrap();
    assert_eq!(outcome, DrainOutcome::DeadlineReached { remaining: 1 }, "only the partition");
    assert!(leases.calls.lock().unwrap().contains(&"retire conn-03".to_owned()));

    // The claim then wins, but no actor starts: the call stays held by this
    // (now dead) instance for another Connector to take over.
    gated.gate.add_permits(1);
    until("claimed", || async { rig.phase().await == "claimed" }).await;
    assert!(task.await.unwrap().is_ok());
    settle().await;
    assert_eq!(engine.snapshot().await.unwrap().active_work, 0);
    assert_eq!(rig.agent.count("start"), 0, "nothing is sent for a call claimed while draining");
    assert_eq!(rig.doc().await["execution"]["holder"]["instance"]["node"], "conn-03");
}

#[tokio::test]
async fn concurrent_drains_both_end_with_every_actor_stopped() {
    let mut rig = Rig::new().await;
    let leases = Arc::new(Leases { lose_after: usize::MAX, ..Leases::default() });
    rig.membership = leases.clone();
    let _events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let (first, second) = tokio::join!(
        running.engine.drain(Duration::from_millis(60)),
        running.engine.drain(Duration::from_millis(60)),
    );
    let outcomes = [first.unwrap(), second.unwrap()];
    // One shutdown, one outcome: the call aborted at the deadline and the
    // partition nobody took.
    let expected = DrainOutcome::DeadlineReached { remaining: 2 };
    assert_eq!(outcomes, [expected.clone(), expected], "{outcomes:?}");
    assert_eq!(running.engine.snapshot().await.unwrap().active_work, 0);
    assert!(leases.calls.lock().unwrap().contains(&"retire conn-03".to_owned()));
}
