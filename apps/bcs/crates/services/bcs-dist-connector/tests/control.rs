//! Human replies and cancels the Worker writes into the dispatch.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use bcs_dist_model::*;
use bcs_service_api::port::dist_agent::CancelOutcome;
use bcs_service_api::port::repo::distributed::ownership::Liveness;
use rig::*;
use serde_json::json;
use std::sync::Arc;

async fn accepted_call(rig: &Rig) -> (Running, EventTx) {
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    (running, events)
}

#[tokio::test]
async fn a_reply_continues_the_same_call_on_the_same_remote_task() {
    let rig = Rig::new();
    let (_running, events) = accepted_call(&rig).await;
    events.send(input_required()).unwrap();
    drop(events); // the agent's stream ends at the question
    until("the question is reported", || async { rig.kinds().await == vec!["input_required"] }).await;
    let (envelope, question) = rig.inbox_events().await.remove(0);
    let AgentEventKind::InputRequired { interaction } = &question.kind else {
        panic!("kind");
    };
    assert!(interaction.0.starts_with("ia:"), "derived, not the adapter's placeholder");
    assert_eq!(interaction.0[3..], envelope.input_id.0[3..]);
    settle().await;
    assert_eq!(rig.agent.count("subscribe"), 0, "no re-attach while a question is open");

    // The Worker queues the human reply on the same dispatch.
    let answers = rig.agent.input_stream();
    rig.worker.push(pending(1)).await;
    until("the reply is confirmed", || async {
        rig.doc().await["execution"]["confirmed_input_through"] == 1
    })
    .await;
    let call = rig.agent.calls().into_iter().find(|c| c.starts_with("input")).unwrap();
    assert!(call.starts_with(&format!("input {DID}:in:1 task=rt-77c2 context=Some(\"ctx-91\")")), "{call}");
    assert!(call.contains(r#"{"parts":[{"text":"A"}]}"#), "{call}");
    assert!(call.ends_with("phase=accepted cancel=not_sent input_sending=1"), "intent was durable before the send: {call}");
    let doc = rig.doc().await;
    assert_eq!(doc["control"]["pending_input"].as_array().unwrap().len(), 1, "removal is the Worker's job");
    assert_eq!(doc["execution"]["holder"]["epoch"], 1, "the same call, the same holder");

    answers.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.kinds().await, vec!["input_required", "final"]);
    assert_eq!(rig.agent.count("input"), 1);
    assert_eq!(rig.agent.count("start"), 1);
}

#[tokio::test]
async fn a_second_question_round_gets_a_new_identity() {
    let rig = Rig::new();
    let (_running, events) = accepted_call(&rig).await;
    events.send(input_required()).unwrap();
    until("first question", || async { rig.kinds().await.len() == 1 }).await;
    let answers = rig.agent.input_stream();
    rig.worker.push(pending(1)).await;
    until("confirmed", || async { rig.doc().await["execution"]["confirmed_input_through"] == 1 }).await;
    answers.send(input_required()).unwrap();
    until("second question", || async { rig.kinds().await.len() == 2 }).await;
    let reported = rig.inbox_events().await;
    assert_ne!(reported[0].0.input_id, reported[1].0.input_id);
}

#[tokio::test]
async fn an_input_with_an_unknown_outcome_is_not_confirmed_or_resent() {
    let rig = Rig::new();
    let (running, _events) = accepted_call(&rig).await;
    rig.agent.on_input(Reply::Unknown);
    rig.worker.push(pending(1)).await;
    rig.worker.push(pending(2)).await;
    until("the input is blocked", || async { running.pending_effects().await == 1 }).await;
    settle().await;
    assert_eq!(rig.agent.count("input"), 1, "an ambiguous send is not repeated");
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["confirmed_input_through"], serde_json::Value::Null);
    assert_eq!(doc["control"]["pending_input"].as_array().unwrap().len(), 2, "the input is retained");
    assert_eq!(rig.phase().await, "accepted");
}

#[tokio::test]
async fn an_idempotent_input_is_resent_until_accepted() {
    let rig = Rig::new();
    let mut spec = dispatch_spec(&did());
    spec.capabilities.idempotent_input = true;
    let events = rig.agent.start_stream();
    rig.worker.create_spec(spec).await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    rig.agent.on_input(Reply::Unknown);
    rig.agent.on_input(Reply::NotSent);
    rig.agent.on_input(Reply::Accepted);
    rig.worker.push(pending(1)).await;
    until("confirmed", || async { rig.doc().await["execution"]["confirmed_input_through"] == 1 }).await;
    let ids: Vec<String> = rig
        .agent
        .calls()
        .iter()
        .filter(|c| c.starts_with("input"))
        .map(|c| c.split(' ').nth(1).unwrap().to_owned())
        .collect();
    assert_eq!(ids, vec![format!("{DID}:in:1"); 3], "same request id on every attempt");
    drop(events);
}

#[tokio::test]
async fn a_rejected_input_is_not_confirmed_and_blocks_later_ones() {
    let rig = Rig::new();
    let (running, _events) = accepted_call(&rig).await;
    rig.agent.on_input(Reply::Rejected("http_422"));
    rig.worker.push(pending(1)).await;
    rig.worker.push(pending(2)).await;
    until("blocked", || async { running.pending_effects().await == 1 }).await;
    settle().await;
    assert_eq!(rig.agent.count("input"), 1);
    assert_eq!(rig.doc().await["execution"]["confirmed_input_through"], serde_json::Value::Null);
}

#[tokio::test]
async fn a_new_holder_does_not_resend_an_input_the_former_holder_may_have_sent() {
    let mut rig = Rig::new();
    rig.worker.create().await;
    let mut old = acquire(&queue(&rig.store), &instance("conn-01")).await;
    old.begin_send(start_op()).await.unwrap();
    old.record_acceptance(accepted(start_op())).await.unwrap();
    rig.worker.push(pending(1)).await;
    old.begin_send(input_op(1)).await.unwrap(); // intent recorded, then it died

    rig.membership = Arc::new(FixedLiveness(Liveness::Dead));
    rig.agent.on_task(found(TaskStatus::InputRequired, json!({"state": "input_required"})));
    rig.worker.notify().await;
    let running = rig.start();
    until("blocked", || async { running.pending_effects().await == 1 }).await;
    settle().await;
    assert_eq!(rig.agent.count("input"), 0, "maybe sent, not idempotent: not sent again");
    assert_eq!(rig.doc().await["execution"]["confirmed_input_through"], serde_json::Value::Null);
}

#[tokio::test]
async fn a_cancel_is_carried_out_and_confirmed() {
    let rig = Rig::new();
    let (running, _events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::ConfirmedCanceled);
    rig.worker.cancel().await;
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    let call = rig.agent.calls().into_iter().find(|c| c.starts_with("cancel")).unwrap();
    assert!(call.starts_with("cancel cancel-1 task=rt-77c2"), "{call}");
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["outcome"], "canceled");
    assert_eq!(doc["execution"]["cancellation"]["kind"], "confirmed_canceled");
    let reported = rig.inbox_events().await;
    assert_eq!(reported[0].1.payload, Payload::Inline(json!({"state": "canceled", "code": "agent_canceled"})));
    assert!(call.contains("cancel=sending"), "the cancel intent was durable before the network call: {call}");
    until("the actor is gone", || async { running.active().await == 0 }).await;
    assert_eq!(rig.agent.count("cancel"), 1);
}

#[tokio::test]
async fn an_accepted_cancel_is_not_a_canceled_call() {
    let rig = Rig::new();
    let (running, events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::RequestAccepted);
    rig.worker.cancel().await;
    until("observed", || async {
        rig.doc().await["execution"]["cancellation"]["kind"] == "request_accepted"
    })
    .await;
    settle().await;
    assert_eq!(rig.phase().await, "accepted", "accepted is not confirmed");
    assert_eq!(rig.agent.count("cancel"), 1, "not sent again");
    assert_eq!(running.pending_effects().await, 0);
    // The agent ends the task itself.
    events.send(event(AgentEventKind::Error, json!({"state": "canceled", "code": "agent_canceled"}))).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.doc().await["execution"]["outcome"], "canceled");
}

#[tokio::test]
async fn a_cancel_with_an_unknown_outcome_is_tried_again() {
    let rig = Rig::new();
    let (running, _events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::OutcomeUnknown { code: "timeout".into() });
    rig.agent.on_cancel(CancelOutcome::ConfirmedCanceled);
    rig.worker.cancel().await;
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("cancel"), 2);
    let second = rig.agent.calls().into_iter().filter(|c| c.starts_with("cancel")).nth(1).unwrap();
    assert!(second.contains("cancel=sending"), "{second}");
    let _ = running;
    assert_eq!(rig.doc().await["execution"]["outcome"], "canceled");
}

#[tokio::test]
async fn a_cancel_the_agent_cannot_carry_out_is_recorded_as_refused() {
    let rig = Rig::new();
    let mut spec = dispatch_spec(&did());
    spec.capabilities.cancel = false;
    let events = rig.agent.start_stream();
    rig.worker.create_spec(spec).await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    rig.worker.cancel().await;
    until("recorded", || async {
        rig.doc().await["execution"]["cancellation"] == json!({"kind": "rejected", "code": "cancel_unsupported"})
    })
    .await;
    settle().await;
    assert_eq!(rig.agent.count("cancel"), 0);
    assert_eq!(rig.phase().await, "accepted", "the call keeps running");
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
}

#[tokio::test]
async fn a_cancel_of_a_finished_task_reports_the_agents_own_result() {
    let rig = Rig::new();
    let (_running, _events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::AlreadyFinished(FinalDispatchOutcome::Completed));
    // The first read of the finished task fails; the call must wait for its
    // real result, not end on an empty one.
    rig.agent.on_task(bcs_service_api::port::dist_agent::QueryOutcome::Unavailable { code: "http_503".into() });
    rig.agent.on_task(found(TaskStatus::Completed, json!({"artifacts": [{"artifactId": "a-1"}]})));
    rig.worker.cancel().await;
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["outcome"], "completed", "the agent finished before the cancel");
    assert_eq!(doc["execution"]["cancellation"], json!({"kind": "already_finished", "outcome": "completed"}));
    assert_eq!(rig.kinds().await, vec!["final"]);
    let reported = rig.inbox_events().await;
    assert_eq!(
        reported[0].1.payload,
        Payload::Inline(json!({"state": "completed", "artifacts": [{"artifactId": "a-1"}]})),
        "the agent's own result, read on a later tick"
    );
    assert_eq!(rig.agent.count("cancel"), 1, "the cancel is not sent again while the result is read");
}

#[tokio::test]
async fn a_finished_task_that_is_gone_ends_on_the_cancel_answer() {
    let rig = Rig::new();
    let (_running, _events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::AlreadyFinished(FinalDispatchOutcome::Completed));
    // The agent dropped the finished task: asking again cannot help.
    rig.agent.on_task(bcs_service_api::port::dist_agent::QueryOutcome::NotFound);
    rig.worker.cancel().await;
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.doc().await["execution"]["outcome"], "completed");
    assert_eq!(rig.kinds().await, vec!["final"]);
    assert_eq!(rig.inbox_events().await[0].1.payload, Payload::Inline(json!({"state": "completed"})));
    assert_eq!(rig.agent.count("query_task"), 1, "a definite answer is not asked for again");
}

#[tokio::test]
async fn a_finished_task_whose_read_lags_is_read_a_bounded_number_of_times() {
    let rig = Rig::new();
    let (_running, _events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::AlreadyFinished(FinalDispatchOutcome::Failed));
    // The task read never catches up with the cancel answer.
    rig.agent.on_task(found(TaskStatus::Working, json!({"task": {"id": "rt-77c2"}})));
    rig.worker.cancel().await;
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.doc().await["execution"]["outcome"], "failed");
    assert_eq!(rig.kinds().await, vec!["snapshot", "error"], "the lagging state is reported once");
    let reads = rig.agent.count("query_task");
    assert_eq!(reads, rig.config.not_sent_retries as usize + 1, "bounded");
    assert_eq!(rig.agent.count("cancel"), 1);
}

#[tokio::test]
async fn a_canceled_task_read_with_a_stored_snapshot_ends_as_canceled() {
    let rig = Rig::new();
    let (_running, _events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::AlreadyFinished(FinalDispatchOutcome::Canceled));
    // The snapshot is stored by reference: it carries no state word.
    rig.agent.on_task(bcs_service_api::port::dist_agent::QueryOutcome::Found {
        remote: remote(),
        status: TaskStatus::Canceled,
        snapshot: Payload::Ref(BlobRef { key: "bcn/snapshot/rt-77c2".into(), digest: Digest("sha256:00".into()) }),
    });
    rig.worker.cancel().await;
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.doc().await["execution"]["outcome"], "canceled", "the status decides, not the payload shape");
    assert_eq!(rig.kinds().await, vec!["error"]);
    // The event itself says so: the Worker reads the end from the payload.
    let Payload::Inline(payload) = rig.inbox_events().await[0].1.payload.clone() else {
        panic!("the state word must be inline");
    };
    assert_eq!(payload["state"], "canceled");
    assert_eq!(payload["task_ref"]["key"], "bcn/snapshot/rt-77c2");
}

#[tokio::test]
async fn a_cancel_before_anything_was_sent_ends_the_call_without_the_agent() {
    let rig = Rig::new();
    rig.worker.create().await;
    rig.worker.cancel().await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert!(rig.agent.calls().is_empty(), "{:?}", rig.agent.calls());
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["outcome"], "canceled");
    let reported = rig.inbox_events().await;
    assert_eq!(reported[0].1.payload, Payload::Inline(json!({"state": "canceled", "code": "canceled_before_send"})));
}

#[tokio::test]
async fn a_cancel_that_arrives_while_the_start_is_on_the_wire_is_carried_out_at_once() {
    let mut rig = Rig::new();
    rig.config.poll_interval = std::time::Duration::from_secs(30); // no poll will help
    let release = rig.agent.gate_start();
    let _events = rig.agent.start_stream();
    rig.agent.on_cancel(CancelOutcome::ConfirmedCanceled);
    rig.worker.create().await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("the start is on the wire", || async { rig.agent.count("start") == 1 }).await;
    rig.worker.cancel().await;
    release.send(()).unwrap();
    // Recording the acceptance re-reads the dispatch and returns the cancel.
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("cancel"), 1);
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["remote"]["task_id"], "rt-77c2", "accepted first, then canceled");
    assert_eq!(doc["execution"]["outcome"], "canceled");
}

#[tokio::test]
async fn a_renotification_makes_the_holder_look_at_once() {
    let mut rig = Rig::new();
    rig.config.poll_interval = std::time::Duration::from_secs(30); // polling alone would be too slow
    let (_running, _events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::ConfirmedCanceled);
    rig.worker.cancel().await;
    rig.worker.notify().await; // the Worker re-posts the did
    until("the call ends", || async { rig.phase().await == "ended" }).await;
}

#[tokio::test]
async fn a_waiting_call_notices_when_the_agent_moves_on_without_a_reply() {
    // auth-required: the user signs in out of band, the agent resumes and
    // completes. No reply ever flows through the Connector.
    let rig = Rig::new();
    let (_running, events) = accepted_call(&rig).await;
    rig.agent.on_task(found(TaskStatus::AuthRequired, json!({"state": "auth_required"})));
    events
        .send(event(
            AgentEventKind::AuthRequired { interaction: InteractionId("x".into()) },
            json!({"state": "auth_required"}),
        ))
        .unwrap();
    drop(events);
    until("the question is reported", || async { rig.kinds().await == vec!["auth_required"] }).await;
    settle().await;
    assert_eq!(rig.kinds().await, vec!["auth_required"], "the same question is not reported again");
    assert!(rig.agent.count("query_task") >= 2, "the waiting call keeps looking at the task");

    rig.agent.tasks.lock().unwrap().clear();
    rig.agent.on_task(found(TaskStatus::Completed, json!({"artifacts": []})));
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.kinds().await, vec!["auth_required", "final"]);
    assert_eq!(rig.doc().await["execution"]["outcome"], "completed");
    // The state word comes from the task status even if the snapshot lacks it.
    assert_eq!(state_of(&rig.inbox_events().await[1].1), "completed");
}

#[tokio::test]
async fn a_cancel_accepted_while_a_question_is_open_is_followed_to_the_end() {
    let rig = Rig::new();
    let (_running, events) = accepted_call(&rig).await;
    rig.agent.on_task(found(TaskStatus::InputRequired, json!({"state": "input_required"})));
    events.send(input_required()).unwrap();
    drop(events);
    until("the question is reported", || async { rig.kinds().await.len() == 1 }).await;
    rig.agent.on_cancel(CancelOutcome::RequestAccepted);
    rig.worker.cancel().await;
    until("observed", || async {
        rig.doc().await["execution"]["cancellation"]["kind"] == "request_accepted"
    })
    .await;
    assert_eq!(rig.phase().await, "accepted");
    // The agent cancels the task a little later; there is no stream to say so.
    rig.agent.tasks.lock().unwrap().clear();
    rig.agent.on_task(found(TaskStatus::Canceled, json!({"code": "agent_canceled"})));
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.doc().await["execution"]["outcome"], "canceled");
}

#[tokio::test]
async fn a_call_that_cannot_be_observed_still_gets_its_cancel() {
    // The agent offers neither subscribe nor a task query, but it can cancel.
    let rig = Rig::new();
    let mut spec = dispatch_spec(&did());
    spec.capabilities.subscribe = false;
    spec.capabilities.query_by_task = false;
    rig.worker.create_spec(spec).await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Accepted);
    let running = rig.start();
    until("held but blind", || async { running.pending_effects().await == 1 }).await;
    assert_eq!(rig.phase().await, "accepted");

    rig.agent.on_cancel(CancelOutcome::ConfirmedCanceled);
    rig.worker.cancel().await;
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("cancel"), 1, "the cancel is sent, not just recorded as unknown");
    assert_eq!(rig.doc().await["execution"]["cancellation"]["kind"], "confirmed_canceled");
    assert_eq!(rig.doc().await["execution"]["outcome"], "canceled");
}

#[tokio::test]
async fn a_recorded_cancel_result_is_completed_by_the_next_holder() {
    // The former holder recorded "confirmed canceled" and died before it
    // reported the terminal event; the agent no longer knows the task.
    let mut rig = Rig::new();
    rig.worker.create().await;
    let mut old = acquire(&queue(&rig.store), &instance("conn-01")).await;
    old.begin_send(start_op()).await.unwrap();
    old.record_acceptance(accepted(start_op())).await.unwrap();
    rig.worker.cancel().await;
    old.record_cancel(CancelObservation::Sending).await.unwrap();
    old.record_cancel(CancelObservation::ConfirmedCanceled).await.unwrap();

    rig.membership = Arc::new(FixedLiveness(Liveness::Dead));
    rig.agent.on_task(bcs_service_api::port::dist_agent::QueryOutcome::NotFound);
    rig.worker.notify().await;
    let _running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("cancel"), 0, "the cancel is not sent a second time");
    assert_eq!(rig.doc().await["execution"]["outcome"], "canceled");
    let reported = rig.inbox_events().await;
    assert_eq!(reported.last().unwrap().1.payload, Payload::Inline(json!({"state": "canceled", "code": "agent_canceled"})));
}

fn polled_call(rig: &Rig) -> DispatchSpec {
    let mut spec = dispatch_spec(&did());
    spec.capabilities.subscribe = false;
    rig.agent.on_start(Reply::Accepted);
    rig.agent.on_input(Reply::Accepted);
    spec
}

#[tokio::test]
async fn a_stale_read_of_the_answered_question_is_not_a_new_question() {
    // A query-only agent that has not processed the reply yet keeps showing
    // the question that was just answered.
    let rig = Rig::new();
    rig.worker.create_spec(polled_call(&rig)).await;
    rig.worker.notify().await;
    rig.agent.on_task(found(TaskStatus::InputRequired, json!({"message": {"messageId": "m-1", "parts": [{"text": "A or B?"}]}})));
    let _running = rig.start();
    until("the question", || async { rig.kinds().await == vec!["input_required"] }).await;
    rig.worker.push(pending(1)).await;
    until("the reply is confirmed", || async { rig.doc().await["execution"]["confirmed_input_through"] == 1 }).await;
    let polls = rig.agent.count("query_task");
    until("polled again after the reply", || async { rig.agent.count("query_task") >= polls + 3 }).await;
    assert_eq!(rig.kinds().await, vec!["input_required"], "the agent asked nothing new");
}

#[tokio::test]
async fn a_question_asked_again_after_a_reply_is_a_new_interaction() {
    let rig = Rig::new();
    rig.worker.create_spec(polled_call(&rig)).await;
    rig.worker.notify().await;
    let question = |id: &str| found(TaskStatus::InputRequired, json!({"message": {"messageId": id, "parts": [{"text": "A or B?"}]}}));
    rig.agent.on_task(question("m-1"));
    let _running = rig.start();
    until("the first question", || async { rig.kinds().await == vec!["input_required"] }).await;
    rig.worker.push(pending(1)).await;
    until("the reply is confirmed", || async { rig.doc().await["execution"]["confirmed_input_through"] == 1 }).await;
    // The agent works on the reply, then asks the same thing again: a new
    // message of its own, so not a repeat of what was reported.
    rig.agent.tasks.lock().unwrap().clear();
    rig.agent.on_task(found(TaskStatus::Working, json!({"task": {"id": "rt-77c2"}})));
    until("working is seen", || async { rig.kinds().await.contains(&"snapshot".to_owned()) }).await;
    rig.agent.tasks.lock().unwrap().clear();
    rig.agent.on_task(question("m-2"));
    until("the second question", || async {
        rig.kinds().await.iter().filter(|k| *k == "input_required").count() == 2
    })
    .await;
    let questions: Vec<_> = rig
        .inbox_events()
        .await
        .into_iter()
        .filter(|(_, event)| kind_name(&event.kind) == "input_required")
        .collect();
    assert_ne!(questions[0].0.input_id, questions[1].0.input_id, "round two has its own identity");
}

#[tokio::test]
async fn an_unknown_cancel_is_not_resent_on_every_tick() {
    let mut rig = Rig::new();
    rig.config.backoff_min = std::time::Duration::from_secs(30);
    rig.config.backoff_max = std::time::Duration::from_secs(60);
    let (running, _events) = accepted_call(&rig).await;
    rig.agent.on_cancel(CancelOutcome::OutcomeUnknown { code: "http_503".into() });
    rig.agent.on_cancel(CancelOutcome::ConfirmedCanceled);
    rig.worker.cancel().await;
    until("the first attempt", || async { rig.agent.count("cancel") == 1 }).await;
    let writes = rig.store.count("json.mutate");
    settle().await; // several poll intervals
    assert_eq!(rig.agent.count("cancel"), 1, "the retry waits out its backoff");
    assert_eq!(rig.store.count("json.mutate"), writes, "and writes nothing meanwhile");
    assert_eq!(running.pending_effects().await, 1, "the open cancel is visible");
    assert_eq!(rig.phase().await, "accepted");
}

#[tokio::test]
async fn an_idempotent_input_is_resent_a_bounded_number_of_times() {
    let rig = Rig::new();
    let mut spec = dispatch_spec(&did());
    spec.capabilities.idempotent_input = true;
    let _events = rig.agent.start_stream();
    rig.worker.create_spec(spec).await;
    rig.worker.notify().await;
    let running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    for _ in 0..10 {
        rig.agent.on_input(Reply::Unknown);
    }
    rig.worker.push(pending(1)).await;
    until("blocked after the last resend", || async { running.pending_effects().await == 1 }).await;
    settle().await;
    assert_eq!(rig.agent.count("input"), 4, "one send and three resends");
    assert_eq!(rig.doc().await["execution"]["confirmed_input_through"], serde_json::Value::Null);
}
