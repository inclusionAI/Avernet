//! Plan T7 slice 4: the fenced verification handoff, the verifier lane and
//! the Quarantined read semantics.
//!
//! The named vectors:
//! - `verification_handoff_fences_live_connector`: a live Connector hands an
//!   unverifiable call off through one claim-fenced CAS; the normal slot is
//!   freed, normal claims refuse verification mode, and another verifier
//!   claims through the independent epoch line.
//! - `late_evidence_after_short_retries` (the T6 request-index profile): the
//!   index runs late, the system restarts, then actively finds the original
//!   task by request id, verifies the stop and completes the call — no
//!   injected proof, no second start.
//! - The standard `ByRequest=Unsupported` profile stays isolated, warns and
//!   keeps the obligation; other contexts stay executable; the
//!   stop-verification deadline saves an independent `Quarantined` record
//!   without crossing the terminal threshold; restart repeats the same
//!   assertions; a late real stop proof still completes the closure.
//! - The crash windows (before the CAS, after the CAS before the index,
//!   after a verifier claim) never lose the obligation and never double-write.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use bcs_dist_connector::config::WallClock;
use bcs_dist_connector::{ConnectorConfig, ConnectorEngine};
use bcs_dist_model::*;
use bcs_service_api::port::dist_agent::QueryOutcome;
use bcs_service_api::port::repo::distributed::ownership::Liveness;
use bcs_service_api::port::repo::distributed::dispatch::*;
use bcs_service_api::port::repo::distributed::common::PageRequest;
use rig::*;
use serde_json::json;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

/// A controllable wall clock: the store's inbox clock stays fixed, while the
/// engine advances through the stop-verification deadline deterministically.
struct MutableClock(std::sync::atomic::AtomicI64);
impl WallClock for MutableClock {
    fn now(&self) -> i64 {
        self.0.load(std::sync::atomic::Ordering::SeqCst)
    }
}

fn start_request_id(spec: &DispatchSpec) -> RequestId {
    RequestId(format!("{}:start", spec.did.0))
}

fn spec_query_by_request(did: &Did) -> DispatchSpec {
    let mut spec = dispatch_spec(did);
    spec.capabilities.query_by_request = true;
    spec.capabilities.query_by_task = true;
    spec
}

async fn doc_of(store: &bcs_test_support::store_model::Model, did: &Did) -> serde_json::Value {
    use bcs_store_api::json::JsonStorePlugin;
    store
        .get_json(&layout().disp(did).unwrap())
        .await
        .unwrap()
        .unwrap()
        .value
}

/// An engine configuration with a mutable clock. Verifications run on their
/// own capacity line: `max_active_calls` bounds normal claims only.
fn engine_config(clock: Arc<MutableClock>, node: &str, verifications: u32) -> ConnectorConfig {
    let mut config = ConnectorConfig::new(instance(node), pool());
    config.clock = clock;
    config.poll_interval = Duration::from_millis(25);
    config.receive_timeout = Duration::from_millis(30);
    config.backoff_min = Duration::from_millis(5);
    config.backoff_max = Duration::from_millis(20);
    config.membership_renew = Duration::from_millis(20);
    config.verification_interval = Duration::from_millis(15);
    config.verification_backoff_min = Duration::from_millis(10);
    config.max_verifications = verifications as usize;
    config
}

fn run_engine(engine: &ConnectorEngine) -> tokio::task::JoinHandle<Result<(), bcs_service_api::types::dist_runtime::RuntimeError>> {
    let looped = engine.clone();
    tokio::spawn(async move { looped.run().await })
}

fn deadline_clock() -> Arc<MutableClock> {
    Arc::new(MutableClock(std::sync::atomic::AtomicI64::new(NOW)))
}

/// The rig engine's handoff pacing: with the fixed store clock the due
/// obligation must stay inside the test's patience.
fn handoff_pacing(rig: &mut Rig) {
    rig.config.verification_backoff_min = Duration::from_millis(10);
    rig.config.verification_backoff_max = Duration::from_millis(40);
    rig.config.verification_interval = Duration::from_millis(10);
}

fn canceled(task: &str) -> QueryOutcome {
    QueryOutcome::Found {
        remote: RemoteTaskRef { task_id: task.into(), context_id: Some("ctx-late".into()) },
        status: TaskStatus::Canceled,
        snapshot: Payload::Inline(json!({"state": "canceled"})),
    }
}

#[tokio::test]
async fn verification_handoff_fences_live_connector() {
    let mut rig = Rig::new().await;
    let clock = deadline_clock();
    // The original Connector stays alive, but runs no verifier lane.
    rig.config.clock = clock.clone();
    rig.config.max_verifications = 0;
    handoff_pacing(&mut rig);
    let strict = spec_query_by_request(&did());
    rig.worker.create_spec(strict.clone()).await;
    // The start outcome is unknown; the short-term by-request retries stay
    // unanswered (the index runs late).
    rig.agent.on_start(Reply::Unknown);
    for _ in 0..8 {
        rig.agent.on_request(QueryOutcome::NotFound);
    }
    rig.worker.notify().await;
    let live = rig.engine();
    let _loop = run_engine(&live);
    until("the handoff is registered", || async {
        doc_of(&rig.store.model, &did()).await["execution"]["verification"].is_object()
    })
    .await;
    let doc = doc_of(&rig.store.model, &did()).await;
    let execution = &doc["execution"];
    // One claim-fenced CAS registered the handoff: the stable query key is
    // the original request id, the method is query, the due obligation is
    // armed, and the normal holder line moved past the old holder.
    assert_eq!(execution["holder"]["instance"]["node"], "conn-03");
    assert!(
        execution["holder"]["epoch"].as_u64().unwrap() > 1,
        "the holder epoch moved: the old holder's writes are fenced"
    );
    assert_eq!(execution["phase"], "sending", "no terminal crossing");
    assert_eq!(
        execution["verification"]["key"]["kind"], "by_request",
        "the stable query key is the original request identity"
    );
    assert_eq!(
        execution["verification"]["key"]["request_id"].as_str().unwrap(),
        start_request_id(&strict).0
    );
    assert_eq!(
        execution["verification"]["method"], "query",
        "the request-index profile keeps query as the approved method"
    );
    assert!(
        doc["due"].as_i64().unwrap() > 0,
        "the pending scheduling obligation is durable"
    );
    // The actor exited after the read-back: the normal slot is free.
    until("the normal slot is free", || async {
        bcs_service_api::port::dist_runtime::DistRuntimeControlPort::snapshot(&live).await.unwrap().active_work == 0
    })
    .await;
    assert_eq!(rig.agent.count("start"), 1);

    // A repeated old hint is routed to the verification lane, never back
    // into a normal claim: no second start, no slot reoccupation.
    rig.worker.notify().await;
    settle().await;
    assert_eq!(rig.agent.count("start"), 1);
    assert_eq!(bcs_service_api::port::dist_runtime::DistRuntimeControlPort::snapshot(&live).await.unwrap().active_work, 0);

    // Normal claims — including by the still-live original Connector — and
    // the no-actor rebuild path both refuse verification mode.
    let queue = queue_with(&rig.store, rig.membership.clone(), rig.board_config());
    let claim = queue
        .claim(DispatchClaimRequest {
            did: did(),
            me: instance("conn-03"),
            request_id: RequestId("again".into()),
        })
        .await
        .unwrap();
    assert_eq!(outcome_name(&claim), "verification");
    let resume = queue
        .resume_owned(ResumeOwnedDispatch {
            did: did(),
            previous: Holder { instance: instance("conn-03"), epoch: HolderEpoch(1) },
            request_id: RequestId("again".into()),
        })
        .await
        .unwrap();
    assert_eq!(outcome_name(&resume), "verification");

    // A second Connector — a different verifier, the original still alive —
    // completes the handoff through the independent epoch line.
    rig.agent.on_request(canceled("rt-late"));
    let verifier = ConnectorEngine::new(
        engine_config(deadline_clock(), "conn-04", 8),
        rig.ports(),
    )
    .unwrap();
    let _verifier_loop = run_engine(&verifier);
    until("the verifier completes the call", || async {
        doc_of(&rig.store.model, &did()).await["execution"]["phase"] == "ended"
    })
    .await;
    let doc = doc_of(&rig.store.model, &did()).await;
    let execution = &doc["execution"];
    // Real stop evidence, never an injected proof: the verifier read the task
    // by request, reported the stop and finished the call.
    assert_eq!(execution["outcome"], "canceled");
    assert!(execution["verification"]["claim"].is_object(), "the takeover epoch is durable");
    assert_eq!(
        execution["verification"]["claim"]["instance"]["node"], "conn-04",
        "another verifier claimed while the original Connector stayed alive"
    );
    assert_eq!(
        execution["verification"]["claim"]["epoch"].as_u64().unwrap(), 1,
        "no double claim: exactly one verification epoch was written"
    );
    assert_eq!(rig.agent.count("start"), 1, "no second start anywhere");
}

#[tokio::test]
async fn late_evidence_after_short_retries() {
    let mut rig = Rig::new().await;
    let clock = deadline_clock();
    rig.config.clock = clock.clone();
    // One normal slot: the handed-off call must free it for the other context.
    rig.config.max_active_calls = 1;
    rig.config.max_verifications = 0;
    handoff_pacing(&mut rig);
    let strict = spec_query_by_request(&did());
    let other = {
        let mut spec = dispatch_spec(&Did("s0099.11aa.c1.n2.a1".into()));
        spec.sid = Sid("s0099.11aa".into());
        spec.tid = Tid("s0099.11aa.c1".into());
        spec
    };
    rig.worker.create_spec(strict.clone()).await;
    rig.worker.create_spec(other.clone()).await;
    // The unknown call exhausts its short-term by-request retries.
    rig.agent.on_start(Reply::Unknown);
    for _ in 0..8 {
        rig.agent.on_request(QueryOutcome::NotFound);
    }
    // The other context executes normally once the slot is free.
    rig.agent.on_start(Reply::Accepted);
    rig.agent.on_remote(RemoteTaskRef { task_id: "rt-other".into(), context_id: None });
    let other_events = rig.agent.subscribe_stream();
    let live = rig.engine();
    let loop_task = run_engine(&live);
    rig.worker.notify().await;
    until("the handoff frees the normal slot", || async {
        doc_of(&rig.store.model, &did()).await["execution"]["verification"].is_object()
            && bcs_service_api::port::dist_runtime::DistRuntimeControlPort::snapshot(&live).await.unwrap().active_work == 0
    })
    .await;
    rig.worker.notify_did(&other.did).await;
    until("the other context executes on the freed slot", || async {
        rig.agent.count("subscribe") == 1
    })
    .await;

    // Restart after the other context's first dispatch: the crashed engine
    // goes away, and the verifier system comes back as another instance.
    loop_task.abort();
    let c_clock = deadline_clock();
    let system = ConnectorEngine::new(
        engine_config(c_clock.clone(), "conn-05", 8),
        rig.ports(),
    )
    .unwrap();
    let _system_loop = run_engine(&system);
    // Time passes after the restart: the queued obligation becomes due, and
    // the request index only now resolves the original start.
    c_clock.0.store(NOW + 100, std::sync::atomic::Ordering::SeqCst);
    // The index becomes queryable only now: the system ACTIVELY finds the
    // original task and verifies the stop.
    rig.agent.on_request(canceled("rt-strict"));
    until("the system actively verifies the original task", || async {
        doc_of(&rig.store.model, &did()).await["execution"]["phase"] == "ended"
    })
    .await;
    let doc = doc_of(&rig.store.model, &did()).await;
    assert_eq!(doc["execution"]["outcome"], "canceled", "the stop was verified, not assumed");
    assert_eq!(
        doc["execution"]["remote"]["task_id"], "rt-strict",
        "the found original task was recorded"
    );
    assert_eq!(rig.agent.count("start"), 2, "exactly the two original starts, no resend");
    // The other context stayed put: its call is still held, not restarted.
    assert!(
        doc_of(&rig.store.model, &other.did).await["execution"]["phase"] != "ended",
        "the restarted system never re-sent the other context"
    );
    other_events.send(delta("still running")).unwrap();
}

#[tokio::test]
async fn by_request_unsupported_keeps_isolation_and_the_deadline_quarantines() {
    let mut rig = Rig::new().await;
    let clock = deadline_clock();
    rig.config.clock = clock.clone();
    rig.config.max_verifications = 0;
    handoff_pacing(&mut rig);
    let isolated = spec_query_by_request(&did());
    rig.worker.create_spec(isolated.clone()).await;
    // The standard A2A profile: no negotiated request-id index, so the
    // by-request lookup answers `Unsupported` every time.
    rig.agent.on_start(Reply::Unknown);
    for _ in 0..16 {
        rig.agent.on_request(QueryOutcome::Unsupported);
    }
    rig.worker.notify().await;
    let live = rig.engine();
    let _loop = run_engine(&live);
    until("the obligation is retained", || async {
        doc_of(&rig.store.model, &did()).await["execution"]["verification"].is_object()
    })
    .await;
    let doc = doc_of(&rig.store.model, &did()).await;
    assert_ne!(doc["execution"]["phase"], "ended", "a safe unknown is never recovered");
    assert!(doc["execution"]["remote"].is_null(), "no fabricated acceptance");
    assert_eq!(doc["execution"]["outcome"], serde_json::Value::Null);
    assert_eq!(rig.agent.count("start"), 1);

    // Other contexts stay executable while the isolated one only warns.
    let other = {
        let mut spec = dispatch_spec(&Did("s0099.11aa.c1.n2.a1".into()));
        spec.sid = Sid("s0099.11aa".into());
        spec.tid = Tid("s0099.11aa.c1".into());
        spec
    };
    rig.worker.create_spec(other.clone()).await;
    rig.agent.on_start(Reply::Accepted);
    rig.agent.on_remote(RemoteTaskRef { task_id: "rt-open".into(), context_id: None });
    let other_events = rig.agent.subscribe_stream();
    rig.worker.notify_did(&other.did).await;
    until("another context executes", || async { rig.agent.count("subscribe") == 1 }).await;

    // The stop-verification deadline passes: an independent `Quarantined`
    // record is saved without crossing the terminal threshold.
    let past = NOW + 300_000 + 10;
    clock.0.store(past, std::sync::atomic::Ordering::SeqCst);
    let system = ConnectorEngine::new(
        engine_config(Arc::new(MutableClock(std::sync::atomic::AtomicI64::new(past))), "conn-06", 8),
        rig.ports(),
    )
    .unwrap();
    let _system_loop = run_engine(&system);
    until("the call is quarantined", || async {
        doc_of(&rig.store.model, &did()).await["execution"]["quarantined"].is_object()
    })
    .await;
    let doc = doc_of(&rig.store.model, &did()).await;
    let execution = &doc["execution"];
    assert_ne!(execution["phase"], "ended", "no terminal crossing");
    assert_eq!(execution["outcome"], serde_json::Value::Null);
    assert_eq!(execution["quarantined"]["reason"], "stop_verification_deadline");
    assert_eq!(rig.agent.count("start"), 2, "the two original starts only; no resend");
    // Restart repeats the same assertions: the record survives, the call
    // stays isolated and non-terminal.
    let restarted = ConnectorEngine::new(
        engine_config(Arc::new(MutableClock(std::sync::atomic::AtomicI64::new(past))), "conn-07", 8),
        rig.ports(),
    )
    .unwrap();
    let _restarted_loop = run_engine(&restarted);
    settle().await;
    let doc = doc_of(&rig.store.model, &did()).await;
    assert_ne!(doc["execution"]["phase"], "ended");
    assert_eq!(
        doc["execution"]["quarantined"]["reason"], "stop_verification_deadline",
        "the quarantine record repeated across the restart"
    );
    assert_eq!(rig.agent.count("start"), 2, "still no resend after the restart");

    // A late real stop proof completes the closure.
    rig.agent.on_request(canceled("rt-late-proof"));
    until("the late stop proof closes the call", || async {
        doc_of(&rig.store.model, &did()).await["execution"]["phase"] == "ended"
    })
    .await;
    let doc = doc_of(&rig.store.model, &did()).await;
    assert_eq!(doc["execution"]["outcome"], "canceled");
    assert!(
        doc["execution"]["quarantined"].is_object(),
        "the quarantine history stays durable after the real close"
    );
    assert_eq!(rig.agent.count("start"), 2, "closure needed no resend");
    // The other context still holds its accepted call, untouched.
    other_events.send(delta("fine")).unwrap();
    assert!(doc_of(&rig.store.model, &other.did).await["execution"]["phase"] != "ended");
}

#[tokio::test]
async fn handoff_crash_windows_never_lose_the_obligation() {
    let mut rig = Rig::new().await;
    let spec = spec_query_by_request(&did());
    rig.worker.create_spec(spec.clone()).await;

    // Before the CAS: a crash leaves the ordinary uncertain call; a fresh
    // claim resumes it (plan T6 semantics), no obligation is lost.
    {
        let mut first = acquire(&queue(&rig.store), &instance("conn-01")).await;
        first.begin_send(start_op(), proof(&start_op())).await.unwrap();
    }
    // The former holder is dead: a fresh generation may take the call over.
    rig.membership = Arc::new(FixedLiveness(Liveness::Dead));
    let queue = queue_with(&rig.store, rig.membership.clone(), rig.board_config());
    let second = queue
        .claim(DispatchClaimRequest {
            did: did(),
            me: instance("conn-02"),
            request_id: RequestId("r2".into()),
        })
        .await
        .unwrap();
    let mut held = match second {
        ClaimOutcome::Acquired(held) => held,
        other => panic!("a fresh claim must resume pre-CAS work: {}", outcome_name(&other)),
    };

    // After the CAS, before the index write: the obligation (mode, key,
    // method, due) is durable in the authoritative dispatch document, and
    // the old holder's further writes are fenced.
    held.hand_off_verification(VerificationHandoffDraft {
        attempt: 1,
        key: VerificationKey::ByRequest { request_id: start_request_id(&spec) },
        method: VerificationMethod::Query,
        next_due: TimestampMillis(NOW),
        backoff_ms: 10,
    })
    .await
    .unwrap();
    assert!(
        held.refresh().await.is_err(),
        "the old holder's writes are rejected after the handoff CAS"
    );
    assert!(
        queue
            .due_verifications(
                TimestampMillis(NOW),
                PageRequest { after: None, limit: NonZeroU32::new(8).unwrap() },
            )
            .await
            .unwrap()
            .items
            .is_empty(),
        "the crash left the index unwritten, while the document holds the obligation",
    );
    let doc = doc_of(&rig.store.model, &did()).await;
    assert_eq!(doc["due"], json!(NOW), "the authoritative obligation survived");
    assert_eq!(
        doc["execution"]["verification"]["key"]["kind"], "by_request",
        "the mode, key and method were registered by the one CAS"
    );
    // The index writer — the same repair the Supervisor performs from the
    // authoritative state alone — restores the entry.
    queue.index_verification(&did(), TimestampMillis(NOW)).await.unwrap();
    let due = queue
        .due_verifications(
            TimestampMillis(NOW),
            PageRequest { after: None, limit: NonZeroU32::new(8).unwrap() },
        )
        .await
        .unwrap();
    assert_eq!(due.items.len(), 1, "the index was repaired from the document");
    assert_eq!(due.items[0], did());

    // After a verifier claim: a crash mid-verification never double-writes;
    // the next verifier takes the next epoch, and the crashed verifier's
    // old-epoch writes are rejected.
    let mut stale = match queue
        .claim_verification(VerificationClaimRequest {
            did: did(),
            me: instance("conn-03"),
            request_id: RequestId("v1".into()),
        })
        .await
        .unwrap()
    {
        VerificationClaimOutcome::Acquired(handle) => handle,
        _other => panic!("the first verifier claims"),
    };
    match queue
        .claim_verification(VerificationClaimRequest {
            did: did(),
            me: instance("conn-04"),
            request_id: RequestId("v2".into()),
        })
        .await
        .unwrap()
    {
        VerificationClaimOutcome::Acquired(_) => {}
        _other => panic!("another verifier must claim over a crashed one"),
    }
    assert!(
        stale.refresh().await.is_err(),
        "an old-epoch verifier write is rejected"
    );
    let doc = doc_of(&rig.store.model, &did()).await;
    assert_eq!(
        doc["execution"]["verification"]["claim"]["instance"]["node"], "conn-04",
        "no double claim: the later epoch owns the line"
    );
    assert_eq!(
        doc["execution"]["verification"]["claim"]["epoch"].as_u64().unwrap(), 2,
        "claims advance the independent epoch line one at a time"
    );
}
