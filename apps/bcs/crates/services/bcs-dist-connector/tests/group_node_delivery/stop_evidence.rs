//! Stop-evidence vectors: frames, query results and cancel-time reads of
//! another task are never this call's result or stop evidence, and a
//! Completed status only becomes a product Final through the verified
//! complete task read.
use crate::harness::*;
use bcs_dist_model::*;
use bcs_service_api::port::dist_agent::{AgentReadError, CancelOutcome};
use rig::*;
use serde_json::json;
use std::sync::Arc;

// --------------------------------------------------------------------------
// T6 case 5: evidence of another task — a query result, a terminal stream
// frame, a cancel-time task read — is never saved as this call's result or
// stop evidence, so the Worker (T7) still refuses to release the call.
// --------------------------------------------------------------------------

#[tokio::test]
async fn wrong_remote_ref_is_not_stop_evidence() {
    let agent = Arc::new(GrantedAgent::all(Arc::new(ScriptedAgent::default())));
    let rig = rig_with(agent.clone()).await;
    let spec = node_spec("n2", 1);
    let did = spec.did.clone();
    rig.rig.worker.create_spec(spec).await;
    rig.agent.inner.on_start(Reply::Accepted);
    rig.rig.worker.notify_did(&did).await;
    let running = rig.start();
    until("accepted", || async { rig.phase(&did).await == "accepted" }).await;

    // 1. Another task's terminal query result answers a task poll.
    rig.agent.inner.tasks.lock().unwrap().push_back(foreign_found(TaskStatus::Completed, complete_result()));
    until("the poisoned poll happened", || async { rig.agent.inner.count("query_task") >= 1 }).await;
    settle().await;
    assert!(
        rig.inbox_of(&did).await.is_empty(),
        "another task's query result is not this call's stop evidence"
    );
    assert_ne!(rig.phase(&did).await, "ended");

    // 2. A terminal frame of another task on the event stream is dropped whole.
    poison_subscribe(
        &rig.agent.inner,
        AgentEventKind::Error,
        json!({"state": "canceled", "code": "agent_canceled"}),
    );
    until("the poisoned stream was read", || async { rig.agent.inner.count("subscribe") >= 1 }).await;
    settle().await;
    assert!(rig.inbox_of(&did).await.is_empty(), "another task's terminal frame is not reported");
    assert_ne!(rig.phase(&did).await, "ended");

    // 3. The cancel answer says "already completed", but the read that
    //    would confirm it keeps answering with another task's result.
    rig.agent.inner.cancels.lock().unwrap().clear();
    rig.agent.inner.on_cancel(CancelOutcome::AlreadyFinished(FinalDispatchOutcome::Completed));
    rig.agent.inner.tasks.lock().unwrap().clear();
    rig.agent.inner.tasks.lock().unwrap().push_back(foreign_found(TaskStatus::Completed, complete_result()));
    worker_cancel(&rig.rig, &did).await;
    until("the cancel-time read happened", || async { rig.agent.inner.count("query_task") >= 5 }).await;
    settle().await;
    assert_eq!(rig.phase(&did).await, "accepted", "T7 still refuses to release the call");
    assert!(
        rig.inbox_of(&did)
            .await
            .iter()
            .all(|event| !matches!(event.kind, AgentEventKind::Final | AgentEventKind::Error)),
        "no product or stop fact of a foreign task is admitted for A"
    );

    // The real task's answer is still honored under the same identities.
    rig.agent.inner.tasks.lock().unwrap().clear();
    rig.agent.inner
        .tasks
        .lock()
        .unwrap()
        .push_back(own_found(TaskStatus::Completed, complete_result()));
    until("the own task completes the call", || async { rig.phase(&did).await == "ended" }).await;
    running.task.abort();
}

// --------------------------------------------------------------------------
// T6 case 6: a Completed status on a normal stream is only a stop fact. The
// product Final is admitted once, from the verified complete task read,
// and nothing is stitched onto the streamed deltas.
// --------------------------------------------------------------------------

#[tokio::test]
async fn normal_stream_final_requires_complete_result() {
    let agent = Arc::new(GrantedAgent::all(Arc::new(ScriptedAgent::default())));
    let rig = rig_with(agent.clone()).await;
    let spec = node_spec("n2", 1);
    let did = spec.did.clone();
    let stream = rig.agent.inner.start_stream();
    rig.rig.worker.create_spec(spec).await;
    rig.rig.worker.notify_did(&did).await;
    let running = rig.start();
    until("accepted", || async { rig.phase(&did).await == "accepted" }).await;

    // Deltas flow, then the agent completes with no product in the frame.
    stream.send(delta("part one")).unwrap();
    stream.send(delta("part two")).unwrap();
    stream.send(event(AgentEventKind::Snapshot, empty_completed())).unwrap();
    // The task read answers with the complete result, once asked for.
    rig.agent.inner
        .tasks
        .lock()
        .unwrap()
        .push_back(own_found(TaskStatus::Completed, complete_result()));
    until("the complete result is in", || async { rig.phase(&did).await == "ended" }).await;

    let events = rig.inbox_of(&did).await;
    let kinds: Vec<&str> = events.iter().map(|e| kind_name(&e.kind)).collect();
    assert_eq!(kinds, vec!["delta", "delta", "snapshot", "final"], "{kinds:?}");
    let final_event = events.last().unwrap();
    match &final_event.payload {
        Payload::Inline(value) => {
            assert_eq!(value["state"], "completed");
            let artifacts = value["artifacts"].as_array().unwrap();
            assert_eq!(artifacts.len(), 2, "the complete ordered artifact checklist");
            assert_eq!(artifacts[0]["artifactId"], "a-1");
            assert_eq!(artifacts[1]["artifactId"], "a-2");
            assert_eq!(
                artifacts[1]["parts"].as_array().unwrap().len(),
                2,
                "all parts of the result reach the Runner"
            );
            assert!(value.get("result_digest").is_some(), "the content digest travels with the result");
        }
        other => panic!("the final was offloaded or empty: {other:?}"),
    }
    assert_eq!(
        events.iter().filter(|e| matches!(e.kind, AgentEventKind::Final)).count(),
        1,
        "one Final, admitted once"
    );
    running.task.abort();
}

// --------------------------------------------------------------------------
// T6 case 7b (review round 1): the recovery's term is the dispatch
// deadline. When it is exhausted with no verified complete result in
// sight, the call closes with a bounded `result_incomplete` failure and
// the proven stop fact stays durable — read- and replayable — while no
// product Final ever appears.
// --------------------------------------------------------------------------

#[tokio::test]
async fn result_incomplete_close_keeps_the_proven_stop_fact() {
    let agent = Arc::new(GrantedAgent::all(Arc::new(ScriptedAgent::default())));
    let rig = rig_with(agent).await;
    let mut spec = node_spec("n2", 1);
    // The guarantee window ended before the stop fact was ever observed:
    // no recovery can hope to finish inside it.
    spec.deadline = TimestampMillis(NOW - 1_000);
    let did = spec.did.clone();
    let stream = rig.agent.inner.start_stream();
    rig.rig.worker.create_spec(spec).await;
    rig.rig.worker.notify_did(&did).await;
    let running = rig.start();
    until("accepted", || async { rig.phase(&did).await == "accepted" }).await;

    // The agent completes with no product in the frame. The stop fact is
    // recorded, but its recovery has no task answer to verify (the fixture
    // answers nothing) and no term left on the dispatch clock.
    stream.send(event(AgentEventKind::Snapshot, empty_completed())).unwrap();
    until("the deadline closes the call", || async { rig.phase(&did).await == "ended" }).await;

    let doc = rig.doc(&did).await;
    assert_eq!(doc["execution"]["phase"], "ended");
    assert_eq!(doc["execution"]["outcome"], "failed", "a bounded failure, never a fabricated completion");
    let events = rig.inbox_of(&did).await;
    let kinds: Vec<&str> = events.iter().map(|e| kind_name(&e.kind)).collect();
    assert_eq!(kinds, vec!["snapshot", "error"], "{kinds:?}");
    // The stop fact itself stays durable and readable.
    assert_eq!(state_of(&events[0]), "completed", "the proven stop fact survives the close");
    // The bounded failure names exactly what is missing.
    match &events[1].payload {
        Payload::Inline(value) => {
            assert_eq!(value["state"], "failed");
            assert_eq!(value["code"], "result_incomplete", "bounded result-incomplete business failure");
        }
        other => panic!("{other:?}"),
    }
    // No product Final was ever admitted.
    assert!(
        events.iter().all(|event| !matches!(event.kind, AgentEventKind::Final)),
        "no Final without a verified complete result"
    );
    running.task.abort();
}

// --------------------------------------------------------------------------
// T6 case 7: the stream's tail is lost after half a result; the connector
// recovers the whole artifact set from the complete task read, and the one
// Final is reported once under its stable identity.
// --------------------------------------------------------------------------

#[tokio::test]
async fn lost_artifact_tail_recovers_by_get() {
    let agent = Arc::new(GrantedAgent::all(Arc::new(ScriptedAgent::default())));
    let rig = rig_with(agent.clone()).await;
    let spec = node_spec("n2", 1);
    let did = spec.did.clone();
    let stream = rig.agent.inner.start_stream();
    rig.rig.worker.create_spec(spec).await;
    rig.rig.worker.notify_did(&did).await;
    let running = rig.start();
    until("accepted", || async { rig.phase(&did).await == "accepted" }).await;

    // Half a result streams out, then the stream dies mid-flight.
    stream.send(delta("part one")).unwrap();
    stream.send(delta("part two")).unwrap();
    stream.send(Err(AgentReadError { code: "connection_reset".into() })).unwrap();
    // The task has meanwhile completed with the whole result.
    rig.agent.inner
        .tasks
        .lock()
        .unwrap()
        .push_back(own_found(TaskStatus::Completed, complete_result()));
    until("recovered through a task read", || async { rig.agent.inner.count("query_task") >= 1 }).await;
    until("the call ends recovered", || async { rig.phase(&did).await == "ended" }).await;

    let events = rig.inbox_of(&did).await;
    let finals: Vec<_> = events.iter().filter(|e| matches!(e.kind, AgentEventKind::Final)).collect();
    assert_eq!(finals.len(), 1, "the recovery reports the same final once");
    match &finals[0].payload {
        Payload::Inline(value) => {
            assert_eq!(value["state"], "completed");
            assert_eq!(value["artifacts"].as_array().unwrap().len(), 2, "the whole tail is back");
        }
        other => panic!("{other:?}"),
    }
    running.task.abort();
}
