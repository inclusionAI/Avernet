//! One call from hint to end, through the real providers and admission.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::inbox::InboxProducerPort;
use rig::*;
use serde_json::json;

#[tokio::test]
async fn a_call_runs_from_hint_to_end_in_the_contract_order() {
    let rig = Rig::new().await;
    // The agent's whole answer is already waiting when the start returns.
    let events = rig.agent.start_stream();
    events.send(delta("Q3 ")).unwrap();
    events.send(delta("grew")).unwrap();
    events.send(final_event()).unwrap();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;

    // Send intent was durable before the network call, under a stable request id.
    assert_eq!(
        rig.agent.calls()[0],
        format!(
            "start {DID}:start agent=agent-analyst payload={} context=None phase=sending cancel=not_sent input_sending=null",
            json!({"parts": [{"text": "compare"}]})
        )
    );
    // The remote task was durable before the first event was reported.
    let calls = rig.store.calls();
    let accepted = calls.iter().filter(|c| c.starts_with("json.mutate") && c.contains(":disp:")).nth(2).unwrap();
    let accepted_at = calls.iter().position(|c| c == accepted).unwrap();
    let first_report = calls.iter().position(|c| c.starts_with("stream.append") && c.contains(":inbox:")).unwrap();
    assert!(
        calls[..first_report].iter().filter(|c| c.starts_with("json.mutate") && c.contains(":disp:")).count() >= 3,
        "claim, send intent and acceptance precede the first report (acceptance at {accepted_at}, report at {first_report})"
    );
    assert_eq!(rig.doc().await["execution"]["remote"]["task_id"], "rt-77c2");

    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["outcome"], "completed");
    assert_eq!(doc["connector"]["finish_evidence"], "final_admitted");
    assert_eq!(doc["execution"]["holder"]["epoch"], 1);

    // (did, holder_epoch, seq): seq starts at 1 and counts every reported event.
    let reported = rig.inbox_events().await;
    assert_eq!(rig.kinds().await, vec!["delta", "delta", "final"]);
    for (n, (envelope, event)) in reported.iter().enumerate() {
        assert_eq!(event.did.0, DID);
        assert_eq!(event.holder_epoch, HolderEpoch(1));
        assert_eq!(event.seq, InputSeq(n as u64 + 1));
        assert_eq!(envelope.tid.0, "s0042.7f3a9c.b21e");
    }
    // The key event was admitted under its stable identity; deltas were not.
    let final_id = &reported[2].0.input_id;
    assert!(final_id.0.starts_with("ae:"));
    assert_eq!(
        rig.inbox()
            .lookup(&bcs_dist_test_runner::scope(), final_id)
            .await
            .unwrap()
            .unwrap()
            .input,
        reported[2].0
    );
    assert!(reported[0].0.input_id.0.starts_with("as-"));

    // The final event was admitted before the dispatch was finished.
    let calls = rig.store.calls();
    let admitted = calls
        .iter()
        .position(|c| c.starts_with("document.create") && c.contains(":admission:"))
        .unwrap();
    let ended = calls.iter().rposition(|c| c.starts_with("json.mutate") && c.contains(":disp:")).unwrap();
    assert!(admitted < ended, "final before finish");

    until("the actor is gone", || async { running.active().await == 0 }).await;
    assert!(held_members(&rig.store, &instance("conn-03")).await.is_empty());
    assert_eq!(rig.agent.count("start"), 1, "no second send");
    assert_eq!(running.pending_effects().await, 0);
}

#[tokio::test]
async fn the_hint_is_acked_only_after_the_claim_decision() {
    let rig = Rig::new().await;
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    // The first claim fails at its first write: no decision, so no ack.
    rig.store.fail_write(1);
    let _running = rig.start();
    // The hint stays pending and comes back through the idle takeover.
    until("the call is claimed on redelivery", || async { rig.phase().await == "accepted" }).await;
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("start"), 1);
}

#[tokio::test]
async fn hints_for_calls_with_nothing_to_do_are_acked_and_dropped() {
    let rig = Rig::new().await;
    // A hint for a dispatch that does not exist, and a repeat for an ended one.
    rig.worker.notify_did(&Did("s0042.7f3a9c.b21e.n9.a1".into())).await;
    rig.worker.create().await;
    rig.agent.on_start(Reply::Completed(Payload::Inline(json!({"state": "completed"}))));
    rig.worker.notify().await;
    let running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    rig.worker.notify().await;
    rig.worker.notify().await;
    settle().await;
    assert_eq!(rig.agent.count("start"), 1, "late and repeated hints are harmless");
    assert_eq!(running.active().await, 0);
    // Nothing is left pending in the group.
    let mut board = rig.board_config();
    board.reclaim_min_idle = std::time::Duration::ZERO;
    let mut other = bcs_service_api::port::repo::distributed::dispatch::DispatchQueuePort::notifications(
        &queue_with(&rig.store, rig.membership.clone(), board),
        pool(),
        instance("conn-99"),
    )
    .await
    .unwrap();
    let left = other
        .receive(std::num::NonZeroU32::new(10).unwrap(), std::time::Duration::from_millis(40))
        .await
        .unwrap();
    assert!(left.is_empty(), "{left:?}");
}

#[tokio::test]
async fn a_synchronous_answer_ends_the_call_without_a_remote_task() {
    let rig = Rig::new().await;
    rig.worker.create().await;
    rig.worker.notify().await;
    rig.agent
        .on_start(Reply::Completed(Payload::Inline(json!({"state": "completed", "message": {"parts": [{"text": "42"}]}}))));
    let _running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["outcome"], "completed");
    assert_eq!(doc["connector"]["finish_evidence"], "sync_result");
    assert_eq!(rig.kinds().await, vec!["final"]);
}

#[tokio::test]
async fn a_definite_rejection_is_reported_as_an_error_and_ends_failed() {
    let rig = Rig::new().await;
    rig.worker.create().await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Rejected("http_400"));
    let _running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    let doc = rig.doc().await;
    assert_eq!(doc["execution"]["outcome"], "failed");
    assert_eq!(doc["connector"]["finish_evidence"], "rejected:http_400");
    let reported = rig.inbox_events().await;
    assert_eq!(rig.kinds().await, vec!["error"]);
    assert_eq!(reported[0].1.payload, Payload::Inline(json!({"state": "rejected", "code": "http_400"})));
    assert_eq!(rig.agent.count("start"), 1, "a rejection is not retried");
}

#[tokio::test]
async fn remote_canceled_and_failed_are_errors_with_the_state_in_the_payload() {
    for (state, outcome) in [("canceled", "canceled"), ("failed", "failed"), ("rejected", "failed")] {
        let rig = Rig::new().await;
        let events = rig.agent.start_stream();
        rig.worker.create().await;
        rig.worker.notify().await;
        let _running = rig.start();
        until("accepted", || async { rig.phase().await == "accepted" }).await;
        events.send(event(AgentEventKind::Error, json!({"state": state, "code": "agent"}))).unwrap();
        until("the call ends", || async { rig.phase().await == "ended" }).await;
        assert_eq!(rig.doc().await["execution"]["outcome"], outcome, "{state}");
        let reported = rig.inbox_events().await;
        assert_eq!(kind_name(&reported[0].1.kind), "error");
        assert_eq!(state_of(&reported[0].1), state);
    }
}

#[tokio::test]
async fn a_broken_stream_is_not_an_agent_result() {
    let rig = Rig::new().await;
    let first = rig.agent.start_stream();
    let second = rig.agent.subscribe_stream();
    // The re-attach reads the task first (query_by_task), then subscribes.
    rig.agent.on_task(found(TaskStatus::Working, json!({"task": {"id": "rt-77c2"}})));
    rig.worker.create().await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    first.send(delta("a")).unwrap();
    first.send(Err(bcs_service_api::port::dist_agent::AgentReadError { code: "reset".into() })).unwrap();
    until("resubscribed", || async { rig.agent.count("subscribe") == 1 }).await;
    assert_eq!(rig.phase().await, "accepted", "a transport failure ends nothing");
    assert!(!rig.kinds().await.contains(&"error".to_owned()));
    second.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.kinds().await, vec!["delta", "snapshot", "final"]);
    assert_eq!(rig.agent.count("start"), 1);
    assert!(rig.agent.calls().iter().any(|c| c.starts_with("subscribe rt-77c2 after=false")));
}

#[tokio::test]
async fn an_agent_without_subscribe_is_followed_by_polling() {
    let rig = Rig::new().await;
    let spec = dispatch_spec(&did());
    rig.agent.change_capabilities(|c| c.subscribe = false);
    rig.worker.create_spec(spec).await;
    rig.worker.notify().await;
    rig.agent.on_start(Reply::Accepted);
    rig.agent.on_task(found(TaskStatus::Working, json!({"task": {"id": "rt-77c2"}})));
    rig.agent.on_task(found(TaskStatus::Working, json!({"task": {"id": "rt-77c2"}})));
    rig.agent.on_task(found(TaskStatus::Completed, json!({"state": "completed", "artifacts": []})));
    let _running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    // An unchanged snapshot is reported once.
    assert_eq!(rig.kinds().await, vec!["snapshot", "final"]);
    assert_eq!(rig.agent.count("subscribe"), 0);
}

#[tokio::test]
async fn large_payloads_go_to_blob_storage_and_requests_are_loaded_from_it() {
    let mut rig = Rig::new().await;
    rig.config.max_inline_bytes = 64;
    let events = rig.agent.start_stream();
    // The Worker stored the request by reference.
    let bytes = serde_json::to_vec(&json!({"parts": [{"text": "big request"}]})).unwrap();
    let reference = bcs_dist_encoding::blob_reference("req/b21e/n2", &bytes);
    rig.blobs.blobs.lock().unwrap().insert(reference.key.clone(), bytes);
    let mut spec = dispatch_spec(&did());
    spec.request = Payload::Ref(reference);
    spec.downstream_context = Some("s0042.7f3a9c:0".into());
    rig.worker.create_spec(spec).await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    assert!(rig.agent.calls()[0].contains("s0042.7f3a9c:0"));
    assert!(rig.agent.calls()[0].contains("big request"), "{:?}", rig.agent.calls());

    let text = "x".repeat(200);
    events.send(delta(&text)).unwrap();
    events.send(event(AgentEventKind::Final, json!({"state": "completed", "artifacts": [text]}))).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    let reported = rig.inbox_events().await;
    for (_, event) in &reported {
        let Payload::Ref(blob) = &event.payload else {
            panic!("payload above the inline limit was not offloaded");
        };
        assert!(blob.key.starts_with(&format!("bcn/agent-event/{DID}/")), "{}", blob.key);
        assert!(rig.blobs.blobs.lock().unwrap().contains_key(&blob.key));
    }
    // The terminal state was read before the payload was offloaded.
    assert_eq!(rig.doc().await["execution"]["outcome"], "completed");
}

#[tokio::test]
async fn a_missing_or_corrupt_request_blob_fails_the_call_without_sending() {
    for stored_bytes in [None, Some(br#"{"parts":[{"text":"corrupt"}]}"#.to_vec())] {
        let rig = Rig::new().await;
        let reference = bcs_dist_encoding::blob_reference("request", br#"{"parts":[{"text":"original"}]}"#);
        if let Some(bytes) = stored_bytes {
            rig.blobs.blobs.lock().unwrap().insert(reference.key.clone(), bytes);
        }
        let mut spec = dispatch_spec(&did());
        spec.request = Payload::Ref(reference);
        rig.worker.create_spec(spec).await;
        rig.worker.notify().await;
        let _running = rig.start();
        until("the call ends", || async { rig.phase().await == "ended" }).await;
        assert_eq!(rig.agent.count("start"), 0);
        let reported = rig.inbox_events().await;
        assert_eq!(reported[0].1.payload, Payload::Inline(json!({"state": "failed", "code": "request_unavailable"})));
        assert_eq!(rig.doc().await["execution"]["outcome"], "failed");
        // No send intent was recorded between claim and end.
        assert_eq!(stored(&rig.store).await.1, 1_000_000_001);
    }
}

#[tokio::test]
async fn a_conflicting_key_event_is_surfaced_and_does_not_end_the_call() {
    use bcs_service_api::core::dist_connector::{DistConnectorCoreService, Report, ReportCursor};
    let rig = Rig::new().await;
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    // Persist a real conflicting admission in the current, scoped store.
    let view: DispatchView = serde_json::from_value(rig.doc().await).unwrap();
    let mut cursor = ReportCursor {
        holder_epoch: HolderEpoch(1),
        last_seq: InputSeq(0),
    };
    let Report::Key(first) = bcs_dist_connector::rules::DistConnectorRules.report(
        &view,
        &mut cursor,
        AgentEventKind::Final,
        None,
        Payload::Inline(json!({"state":"other"})),
    ) else {
        panic!("expected final")
    };
    rig.inbox().admit_connector(first).await.unwrap();
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    events.send(final_event()).unwrap();
    settle().await;
    assert_eq!(rig.phase().await, "accepted", "not ended on a conflict");
    assert_eq!(
        rig.kinds().await,
        vec!["final"],
        "only the original event remains"
    );
}
