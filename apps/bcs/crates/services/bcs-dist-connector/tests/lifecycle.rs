//! One call from hint to end, through the real providers and admission.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use bcs_dist_model::*;
use rig::*;
use serde_json::json;

#[tokio::test]
async fn a_call_runs_from_hint_to_end_in_the_contract_order() {
    let rig = Rig::new();
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    let running = rig.start();

    until("the agent is called", || async { rig.agent.count("start") == 1 }).await;
    // Send intent was durable before the network call, under a stable request id.
    assert_eq!(
        rig.agent.calls()[0],
        format!("start {DID}:start agent=agent-analyst payload={} phase=sending", json!({"parts": [{"text": "compare"}]}))
    );
    until("acceptance is recorded", || async { rig.phase().await == "accepted" }).await;
    assert_eq!(rig.doc().await["execution"]["remote"]["task_id"], "rt-77c2");

    events.send(delta("Q3 ")).unwrap();
    events.send(delta("grew")).unwrap();
    events.send(final_event()).unwrap();
    until("the call ends", || async { rig.phase().await == "ended" }).await;

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
    let admit = layout().admit(&Sid("s0042.7f3a9c".into()), final_id).unwrap();
    assert!(bcs_store_api::kv::KvStorePlugin::get(&rig.store.model, &admit).await.unwrap().is_some());
    assert!(reported[0].0.input_id.0.starts_with("as-"));

    // The final event was admitted before the dispatch was finished.
    let calls = rig.store.calls();
    let admitted = calls.iter().position(|c| c.starts_with("kv.put_if_absent")).unwrap();
    let ended = calls.iter().rposition(|c| c.starts_with("json.mutate") && c.contains(":disp:")).unwrap();
    assert!(admitted < ended, "final before finish");

    until("the actor is gone", || async { running.active().await == 0 }).await;
    assert!(held_members(&rig.store, &instance("conn-03")).await.is_empty());
    assert_eq!(rig.agent.count("start"), 1, "no second send");
    assert_eq!(running.pending_effects().await, 0);
}

#[tokio::test]
async fn the_hint_is_acked_only_after_the_claim_decision() {
    let rig = Rig::new();
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
    let rig = Rig::new();
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
    let rig = Rig::new();
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
    let rig = Rig::new();
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
        let rig = Rig::new();
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
    let rig = Rig::new();
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
    let rig = Rig::new();
    let mut spec = dispatch_spec(&did());
    spec.capabilities.subscribe = false;
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
    let mut rig = Rig::new();
    rig.config.max_inline_bytes = 64;
    let events = rig.agent.start_stream();
    // The Worker stored the request by reference.
    let reference = BlobRef { key: "req/b21e/n2".into(), digest: Digest("sha256:r".into()) };
    rig.blobs
        .blobs
        .lock()
        .unwrap()
        .insert(reference.key.clone(), serde_json::to_vec(&json!({"parts": [{"text": "big request"}]})).unwrap());
    let mut spec = dispatch_spec(&did());
    spec.request = Payload::Ref(reference);
    rig.worker.create_spec(spec).await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
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
async fn a_missing_request_blob_fails_the_call_without_sending() {
    let rig = Rig::new();
    let mut spec = dispatch_spec(&did());
    spec.request = Payload::Ref(BlobRef { key: "gone".into(), digest: Digest("sha256:x".into()) });
    rig.worker.create_spec(spec).await;
    rig.worker.notify().await;
    let _running = rig.start();
    until("the call ends", || async { rig.phase().await == "ended" }).await;
    assert_eq!(rig.agent.count("start"), 0);
    let reported = rig.inbox_events().await;
    assert_eq!(reported[0].1.payload, Payload::Inline(json!({"state": "failed", "code": "request_unavailable"})));
    assert_eq!(rig.doc().await["execution"]["outcome"], "failed");
}

#[tokio::test]
async fn a_conflicting_key_event_is_surfaced_and_does_not_end_the_call() {
    use bcs_dist_connector::identity::{KeyEvent, key_input_id};
    use bcs_dist_store::codec::AdmitRecord;
    let rig = Rig::new();
    let events = rig.agent.start_stream();
    rig.worker.create().await;
    rig.worker.notify().await;
    // Another content is already admitted under the final event's identity.
    let sid = Sid("s0042.7f3a9c".into());
    let input_id = key_input_id(&did(), KeyEvent::Final);
    let record = AdmitRecord { digest: Digest("sha256:other".into()), sid: sid.clone(), tid: Tid("s0042.7f3a9c.b21e".into()) };
    bcs_store_api::kv::KvStorePlugin::put_if_absent(
        &rig.store.model,
        &layout().admit(&sid, &input_id).unwrap(),
        &record.encode().unwrap(),
        bcs_store_api::Expiry::Persistent,
    )
    .await
    .unwrap();
    let _running = rig.start();
    until("accepted", || async { rig.phase().await == "accepted" }).await;
    events.send(final_event()).unwrap();
    settle().await;
    assert_eq!(rig.phase().await, "accepted", "not ended on a conflict");
    assert!(rig.kinds().await.is_empty(), "a conflicting event is not written");
}
