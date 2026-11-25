//! Takeover, resume and the SENT policy.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use bcs_dist_connector::delayed::{self, NEED_CONTRACT, UnresolvedKind};
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::{DistConnectorCoreService, Report, ReportCursor};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::dispatch::*;
use bcs_service_api::port::repo::distributed::inbox::InboxProducerPort;
use bcs_service_api::port::repo::distributed::ownership::Liveness;
use rig::*;
use serde_json::json;
use std::sync::Arc;

/// A former holder (conn-01) takes the call to `phase` and then dies.
async fn former_holder(rig: &Rig, spec: DispatchSpec, phase: DispatchPhase) -> Box<dyn HeldDispatchPort> {
    rig.worker.create_spec(spec).await;
    let mut held = acquire(&queue(&rig.store), &instance("conn-01")).await;
    if phase != DispatchPhase::Claimed {
        held.begin_send(start_op()).await.unwrap();
    }
    if phase == DispatchPhase::Accepted {
        held.record_acceptance(accepted(start_op())).await.unwrap();
    }
    held
}

fn spec_with(change: impl FnOnce(&mut DownstreamCapabilities)) -> DispatchSpec {
    let mut spec = dispatch_spec(&did());
    change(&mut spec.capabilities);
    spec
}

fn dead_holders(rig: &mut Rig) {
    rig.membership = Arc::new(FixedLiveness(Liveness::Dead));
}

#[tokio::test]
async fn a_crash_between_final_and_finish_is_completed_by_the_next_holder() {
    let mut rig = Rig::new();
    let _old = former_holder(&rig, dispatch_spec(&did()), DispatchPhase::Accepted).await;
    // The former holder admitted the final event and died before `finish`.
    let rules = bcs_dist_connector::rules::DistConnectorRules;
    let view = serde_json::from_value::<DispatchView>({
        let mut doc = rig.doc().await;
        doc.as_object_mut().unwrap().remove("connector");
        doc
    })
    .unwrap();
    let mut cursor = ReportCursor { holder_epoch: HolderEpoch(1), last_seq: InputSeq(7) };
    let Report::Key(first) = rules.report(&view, &mut cursor, AgentEventKind::Final, Some("evt-9".into()), Payload::Inline(json!({"state": "completed"}))) else {
        panic!("final is a key event");
    };
    rig.inbox().admit(first.clone()).await.unwrap();
    assert_eq!(rig.phase().await, "accepted");

    // A new holder takes over, re-reads the remote task and re-reports.
    dead_holders(&mut rig);
    rig.agent.on_task(found(TaskStatus::Completed, json!({"state": "completed", "artifacts": [{"artifactId": "a-1"}]})));
    rig.worker.notify().await;
    let _running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;

    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["outcome"], "completed");
    assert_eq!(doc["execution"]["holder"]["epoch"], 2);
    assert_eq!(doc["execution"]["holder"]["instance"]["node"], "conn-03");
    // Same identity, different bytes: the first report won, nothing was added.
    let reported = rig.inbox_events().await;
    assert_eq!(reported.len(), 1, "the final event took effect once");
    assert_eq!(reported[0].0.input_id, first.input.input_id);
    assert_eq!(reported[0].1.holder_epoch, HolderEpoch(1));
    assert_eq!(rig.agent.count("start"), 0, "an accepted call is never sent again");
    assert_eq!(rig.agent.count("query_task"), 1);
}

#[tokio::test]
async fn a_takeover_of_an_accepted_call_reattaches_and_restarts_seq_at_one() {
    let mut rig = Rig::new();
    let _old = former_holder(&rig, dispatch_spec(&did()), DispatchPhase::Accepted).await;
    dead_holders(&mut rig);
    rig.agent.on_task(found(TaskStatus::Working, json!({"task": {"id": "rt-77c2"}})));
    let events = rig.agent.subscribe_stream();
    rig.worker.notify().await;
    let _running = rig.start();
    until("subscribed", || async { rig.agent.count("subscribe") == 1 }).await;
    events.send(delta("more")).unwrap();
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    let reported = rig.inbox_events().await;
    assert_eq!(rig.kinds().await, vec!["snapshot", "delta", "final"]);
    assert_eq!(
        reported.iter().map(|(_, e)| (e.holder_epoch.0, e.seq.0)).collect::<Vec<_>>(),
        vec![(2, 1), (2, 2), (2, 3)],
        "the new holder generation reports from seq 1"
    );
    assert_eq!(rig.agent.count("start"), 0);
}

#[tokio::test]
async fn a_possibly_sent_call_is_not_resent_without_a_capability() {
    let mut rig = Rig::new();
    let _old = former_holder(&rig, dispatch_spec(&did()), DispatchPhase::Sending).await;
    dead_holders(&mut rig);
    rig.worker.notify().await;
    let running = rig.start();
    until("the call is held and unresolved", || async { running.pending_effects().await == 1 }).await;
    settle().await;
    assert_eq!(rig.agent.count("start"), 0, "no blind resend");
    assert_eq!(rig.agent.count("query_request"), 0);
    assert_eq!(rig.phase().await, "sending", "it stays in its phase");
    assert_eq!(running.active().await, 1, "still held and polled");
    assert_eq!(rig.doc().await["execution"]["holder"]["epoch"], 2);

    // A cancel from the Worker's deadline is seen, but unknown is never
    // promoted to an end.
    rig.worker.cancel().await;
    until("the cancel is observed", || async {
        rig.doc().await["execution"]["cancellation"]["kind"] == "outcome_unknown"
    })
    .await;
    settle().await;
    assert_eq!(rig.phase().await, "sending");
    assert_eq!(rig.agent.count("cancel"), 0, "no remote task to cancel");
    assert!(rig.kinds().await.is_empty());
}

#[tokio::test]
async fn an_idempotent_start_is_resent_with_the_same_request_id() {
    let mut rig = Rig::new();
    let _old = former_holder(&rig, spec_with(|c| c.idempotent_start = true), DispatchPhase::Sending).await;
    dead_holders(&mut rig);
    let events = rig.agent.start_stream();
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    assert!(rig.agent.calls()[0].starts_with(&format!("start {DID}:start ")), "{:?}", rig.agent.calls());
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("start"), 1);
}

#[tokio::test]
async fn an_agent_that_can_be_asked_is_asked_instead_of_resent() {
    let mut rig = Rig::new();
    let _old = former_holder(&rig, spec_with(|c| c.query_by_request = true), DispatchPhase::Sending).await;
    dead_holders(&mut rig);
    rig.agent.on_request(found(TaskStatus::Working, json!({"task": {}})));
    rig.agent.on_task(found(TaskStatus::Working, json!({"task": {"id": "rt-77c2"}})));
    let events = rig.agent.subscribe_stream();
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    assert_eq!(rig.agent.count("start"), 0);
    assert!(rig.agent.calls()[0].starts_with(&format!("query_request {DID}:start")));
    assert_eq!(rig.doc().await["execution"]["remote"]["task_id"], "rt-77c2");
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
}

#[tokio::test]
async fn an_agent_that_does_not_know_the_request_leaves_the_call_unresolved() {
    let mut rig = Rig::new();
    let _old = former_holder(&rig, spec_with(|c| c.query_by_request = true), DispatchPhase::Sending).await;
    dead_holders(&mut rig);
    rig.agent.on_request(bcs_service_api::port::dist_agent::QueryOutcome::NotFound);
    rig.worker.notify().await;
    let running = rig.start();
    until("unresolved", || async { running.pending_effects().await == 1 }).await;
    assert_eq!(rig.agent.count("start"), 0, "not found is no licence to resend");
    assert_eq!(rig.phase().await, "sending");
}

#[tokio::test]
async fn an_unknown_first_send_follows_the_same_policy() {
    // No capability: unresolved after one attempt.
    let rig = Rig::new();
    rig.worker.create().await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Unknown);
    let running = rig.start();
    until("unresolved", || async { running.pending_effects().await == 1 }).await;
    settle().await;
    assert_eq!(rig.agent.count("start"), 1);
    assert_eq!(rig.phase().await, "sending");

    // Idempotent: sent again, same request id, then accepted.
    let rig = Rig::new();
    rig.worker.create_spec(spec_with(|c| c.idempotent_start = true)).await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Unknown);
    let events = rig.agent.start_stream();
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    let calls = rig.agent.calls();
    assert_eq!(rig.agent.count("start"), 2);
    assert_eq!(calls[0].split(' ').nth(1), calls[1].split(' ').nth(1), "same request id");
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
}

#[tokio::test]
async fn a_rejected_resend_does_not_fail_a_start_that_may_have_arrived() {
    let rig = Rig::new();
    rig.worker.create_spec(spec_with(|c| c.idempotent_start = true)).await;
    rig.worker.notify().await;
    // The first send may have reached the agent; only the resend is refused.
    rig.agent.on_start(Reply::Unknown);
    rig.agent.on_start(Reply::Rejected("http_401"));
    let _running = rig.start();
    until("the resend was refused", || async { rig.agent.count("start") == 2 }).await;
    tokio::time::sleep(std::time::Duration::from_millis(150)).await;
    assert_eq!(rig.phase().await, "sending", "unresolved, not failed");
    assert!(rig.kinds().await.is_empty(), "no error event for a call whose task may be running");
    assert_eq!(rig.agent.count("start"), 2);
}

#[tokio::test]
async fn a_definite_not_sent_is_retried_three_times_then_unresolved() {
    let rig = Rig::new();
    rig.worker.create().await;
    rig.worker.notify().await;
    for _ in 0..4 {
        rig.agent.on_start(Reply::NotSent);
    }
    let running = rig.start();
    until("unresolved", || async { running.pending_effects().await == 1 }).await;
    settle().await;
    assert_eq!(rig.agent.count("start"), 4, "one attempt and three retries");
    assert_eq!(rig.phase().await, "sending");
    assert!(rig.kinds().await.is_empty(), "nothing is reported for a transport failure");

    // Nothing reached the agent, so the Worker's cancel simply ends the call.
    rig.worker.cancel().await;
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["outcome"], "canceled");
    assert_eq!(doc["connector"]["finish_evidence"], "canceled_before_send");
    let reported = rig.inbox_events().await;
    assert_eq!(reported[0].1.payload, Payload::Inline(json!({"state": "canceled", "code": "canceled_before_send"})));
    assert_eq!(rig.agent.count("cancel"), 0);
    until("the actor is gone", || async { running.active().await == 0 }).await;
}

#[tokio::test]
async fn a_not_sent_start_succeeds_on_a_retry() {
    let rig = Rig::new();
    rig.worker.create().await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::NotSent);
    rig.agent.on_start(Reply::NotSent);
    let events = rig.agent.start_stream();
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    assert_eq!(rig.agent.count("start"), 3);
    assert_eq!(running.pending_effects().await, 0);
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
}

#[tokio::test]
async fn owned_by_me_without_an_actor_resumes_under_a_new_generation() {
    let rig = Rig::new();
    rig.worker.create().await;
    // This instance holds the call but runs no actor for it (its claim answer
    // was lost, or the actor died).
    let orphan = acquire(&queue(&rig.store), &instance("conn-03")).await;
    assert_eq!(orphan.holder().epoch, HolderEpoch(1));
    let events = rig.agent.start_stream();
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    assert_eq!(rig.doc().await["execution"]["holder"]["epoch"], 2, "resume advances the generation");
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.inbox_events().await[0].1.holder_epoch, HolderEpoch(2));
}

#[tokio::test]
async fn a_holder_that_lost_the_call_stops_and_writes_nothing() {
    let mut rig = Rig::new();
    rig.config.poll_interval = std::time::Duration::from_secs(30); // it will not find out by polling
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;

    // Another Connector takes the call over (this one was only cut off).
    let other = queue_with(&rig.store, Arc::new(FixedLiveness(Liveness::Dead)), config(1));
    let ClaimOutcome::Acquired(mut second) = other.claim(claim_request(&instance("conn-07"))).await.unwrap() else {
        panic!("no takeover");
    };
    let version = stored(&rig.store).await.1;
    assert_eq!(running.active().await, 1, "the old actor does not know yet");

    // The old holder still receives the agent's final event. Its key event
    // is harmless (same identity for every holder), but its `finish` is
    // fenced: it stops and the dispatch is untouched.
    events.send(final_event()).unwrap();
    until("the old actor stops", || async { running.active().await == 0 }).await;
    assert_eq!(stored(&rig.store).await.1, version, "a lost holder writes nothing to the dispatch");
    assert_eq!(rig.phase().await, "accepted");
    assert_eq!(rig.doc().await["execution"]["holder"]["instance"]["node"], "conn-07");
    let reported = rig.inbox_events().await;
    assert_eq!(reported.len(), 1);
    assert_eq!(reported[0].1.holder_epoch, HolderEpoch(1), "tagged with the old holder epoch");

    // The new holder's own report of the same final takes effect once.
    second.finish(FinalDispatchOutcome::Completed, "by the new holder".into()).await.unwrap();
    assert_eq!(rig.doc().await["connector"]["finish_evidence"], "by the new holder");
}

#[tokio::test]
async fn an_actor_that_dies_is_resumed_under_a_new_generation() {
    let rig = Rig::new();
    rig.worker.create_spec(spec_with(|c| c.idempotent_start = true)).await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Panic);
    let events = rig.agent.start_stream();
    let running = rig.start();
    until("accepted by the resumed actor", || async { rig.phase().await == "accepted" }).await;
    assert_eq!(rig.doc().await["execution"]["holder"]["epoch"], 2);
    assert_eq!(rig.agent.count("start"), 2, "the idempotent start is sent again with the same id");
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    until("idle", || async { running.active().await == 0 }).await;
}

#[tokio::test]
async fn a_failed_resume_is_retried_until_it_succeeds() {
    let rig = Rig::new();
    rig.worker.create_spec(spec_with(|c| c.idempotent_start = true)).await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Panic);
    let events = rig.agent.start_stream();
    let running = rig.start();
    until("the first start was attempted", || async { rig.agent.count("start") == 1 }).await;
    // The resume's first write fails; the call must not be forgotten.
    rig.store.fail_write(1);
    until("accepted after a retried resume", || async { rig.phase().await == "accepted" }).await;
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    let _ = running;
}

#[tokio::test]
async fn a_resume_owed_from_a_failure_never_replaces_the_actor_that_settled_it() {
    let mut rig = Rig::new();
    // Long enough for a hint to arrive between two rounds of the main loop.
    rig.config.receive_timeout = std::time::Duration::from_millis(1000);
    rig.worker.create_spec(spec_with(|c| c.idempotent_start = true)).await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Panic);
    let events = rig.agent.start_stream();
    let _running = rig.start();
    until("the first start was attempted", || async { rig.agent.count("start") == 1 }).await;
    // The resume of the dead actor fails once and is owed.
    let writes = || rig.store.count("json.mutate") + rig.store.count("zset.add");
    rig.store.fail_write(1);
    let before = writes();
    until("the resume failed", || async { writes() > before }).await;
    // A hint for the same call arrives before the retry and resumes it.
    rig.worker.notify().await;
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    // Several rounds later the owed retry must not have replaced that actor:
    // one claim, one resume, and the resumed actor ends the call itself.
    tokio::time::sleep(std::time::Duration::from_millis(2500)).await;
    assert_eq!(rig.doc().await["execution"]["holder"]["epoch"], 2, "the running actor was replaced");
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("start"), 2);
}

#[tokio::test]
async fn a_resume_that_landed_without_an_answer_is_resumed_again() {
    let rig = Rig::new();
    rig.worker.create_spec(spec_with(|c| c.idempotent_start = true)).await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Panic);
    let events = rig.agent.start_stream();
    let running = rig.start();
    until("the first start was attempted", || async { rig.agent.count("start") == 1 }).await;
    // The resume's claim write lands, but its read-back fails: the engine
    // gets no handle although the dispatch now names a newer generation of
    // this process. It must not forget the call.
    rig.store.fail_get(2);
    until("accepted after the lost resume", || async { rig.phase().await == "accepted" }).await;
    assert_eq!(rig.doc().await["execution"]["holder"]["epoch"], 3, "one lost resume, one more");
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    until("idle", || async { running.active().await == 0 }).await;
}

#[tokio::test]
async fn a_request_id_lookup_that_already_has_the_result_ends_the_call() {
    // The agent can be asked by request id but offers no way to follow a task.
    let mut rig = Rig::new();
    let spec = spec_with(|c| {
        c.query_by_request = true;
        c.subscribe = false;
        c.query_by_task = false;
    });
    let _old = former_holder(&rig, spec, DispatchPhase::Sending).await;
    dead_holders(&mut rig);
    rig.agent.on_request(found(TaskStatus::Completed, json!({"artifacts": [{"artifactId": "a-1"}]})));
    rig.worker.notify().await;
    let running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.doc().await["execution"]["outcome"], "completed");
    assert_eq!(rig.doc().await["execution"]["remote"]["task_id"], "rt-77c2", "acceptance is recorded first");
    assert_eq!(rig.kinds().await, vec!["final"]);
    assert_eq!(rig.agent.count("start"), 0);
    until("idle", || async { running.active().await == 0 }).await;
}

#[tokio::test]
async fn a_takeover_of_a_call_whose_request_is_gone_fails_it_cleanly() {
    // Nothing was ever sent (the former holder died right after its claim)
    // and the request blob no longer exists.
    let mut rig = Rig::new();
    let mut spec = dispatch_spec(&did());
    spec.request = Payload::Ref(BlobRef { key: "gone".into(), digest: Digest("sha256:x".into()) });
    let _old = former_holder(&rig, spec, DispatchPhase::Claimed).await;
    dead_holders(&mut rig);
    rig.worker.notify().await;
    let _running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("start"), 0);
    assert_eq!(rig.doc().await["execution"]["outcome"], "failed");
    assert_eq!(rig.doc().await["connector"]["finish_evidence"], "request_unavailable");
}

#[tokio::test]
async fn report_unresolved_needs_contract_d2() {
    for kind in [UnresolvedKind::Uncertain, UnresolvedKind::NotSent] {
        assert_eq!(
            delayed::report_unresolved(&did(), kind).await,
            Err(StoreError::NotApplied { code: NEED_CONTRACT.into() })
        );
    }
    assert_eq!(NEED_CONTRACT, "need_contract_not_implemented");
}
