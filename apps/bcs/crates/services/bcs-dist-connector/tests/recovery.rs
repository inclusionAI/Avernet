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
    let rig = Rig::new();
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
    until("the old actor stops", || async { running.active().await == 0 }).await;
    let version = stored(&rig.store).await.1;
    // A late terminal event on the old stream changes nothing in `disp`.
    let _ = events.send(final_event());
    settle().await;
    assert_eq!(stored(&rig.store).await.1, version);
    assert_eq!(rig.phase().await, "accepted");
    second.finish(FinalDispatchOutcome::Completed, "by the new holder".into()).await.unwrap();
    assert_eq!(rig.doc().await["connector"]["finish_evidence"], "by the new holder");
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
