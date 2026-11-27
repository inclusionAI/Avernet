//! The holder's writes: versions, transitions, fencing, lost and failed writes.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod common;

use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use bcs_service_api::port::repo::distributed::dispatch::*;
use common::*;
use std::sync::Arc;

async fn held() -> (Arc<Recorder>, WorkerFixture, Box<dyn HeldDispatchPort>) {
    let store = Arc::new(Recorder::default());
    let worker = WorkerFixture::new(&store);
    worker.create().await;
    let held = acquire(&queue(&store), &instance("conn-03")).await;
    store.clear();
    (store, worker, held)
}

fn invalid(code: &str) -> HolderError {
    HolderError::InvalidTransition { code: code.into() }
}

#[tokio::test]
async fn a_send_proof_that_does_not_match_the_intent_is_refused() {
    // Plan T6: the durable proof is written with the intent it belongs to —
    // an input proof (or a request id that says something else) must never
    // be recorded as the proof of a start, nor the other way around.
    let (store, _worker, mut held) = held().await;
    assert_eq!(
        held.begin_send(start_op(), proof(&input_op(1))).await,
        Err(invalid("send_proof_mismatch")),
        "an input proof never vouches for a start"
    );
    assert_eq!(held.begin_send(input_op(1), proof(&start_op())).await, Err(invalid("send_proof_mismatch")));
    // Nothing was written: the document is untouched and the start intent
    // alone still records nothing.
    let (_doc, version) = stored(&store).await;
    assert_eq!(version, 1_000_000_000, "a refused proof writes nothing");
    assert_eq!(held.view().execution.send_proof, None);
    // The matching proof still writes normally.
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    assert!(held.view().execution.send_proof.is_some());
}

#[tokio::test]
async fn one_call_from_send_to_end() {
    let (store, _worker, mut held) = held().await;
    let base = 1_000_000_000;

    let permit = held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    assert_eq!(permit.operation, start_op());
    assert_eq!(&permit.holder, held.holder());
    assert_eq!(permit.did, did());
    let (doc, version) = stored(&store).await;
    assert_eq!((doc["execution"]["phase"].as_str(), version), (Some("sending"), base + 1));

    // Send intent is already durable: a repeat is a fenced read, no write.
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    assert_eq!(stored(&store).await.1, base + 1);

    assert_eq!(held.record_acceptance(accepted(start_op())).await.unwrap(), None);
    let (doc, version) = stored(&store).await;
    assert_eq!(version, base + 2);
    assert_eq!(doc["execution"]["phase"], "accepted");
    assert_eq!(doc["execution"]["remote"], serde_json::json!({"task_id": "rt-77c2", "context_id": "ctx-91"}));
    assert_eq!(held.view().execution.remote, Some(remote()));
    // Same remote again: nothing to write.
    held.record_acceptance(accepted(start_op())).await.unwrap();
    assert_eq!(stored(&store).await.1, base + 2);

    assert_eq!(
        held.finish(FinalDispatchOutcome::Completed, "final_admitted".into()).await.unwrap(),
        IdempotentWrite::Applied
    );
    let (doc, version) = stored(&store).await;
    assert_eq!(version, base + 3);
    assert_eq!(doc["execution"]["phase"], "ended");
    assert_eq!(doc["execution"]["outcome"], "completed");
    assert_eq!(doc["connector"]["finish_evidence"], "final_admitted");
    assert!(held_members(&store, &instance("conn-03")).await.is_empty(), "unlisted after ENDED");
    // Unlisted only after the ENDED write.
    let calls = store.calls();
    let ended = calls.iter().rposition(|c| c.starts_with("json.mutate")).unwrap();
    let removed = calls.iter().position(|c| c.starts_with("zset.remove")).unwrap();
    assert!(ended < removed, "{calls:?}");

    assert_eq!(
        held.finish(FinalDispatchOutcome::Completed, "again".into()).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(
        held.finish(FinalDispatchOutcome::Failed, "other".into()).await.unwrap(),
        IdempotentWrite::ContentConflict
    );
    assert_eq!(stored(&store).await.1, base + 3);
    // The Worker's objects were never written by the holder.
    assert_eq!(store.writes_to("disp"), 3);
}

#[tokio::test]
async fn transitions_out_of_order_are_refused_without_a_write() {
    let (store, worker, mut held) = held().await;
    assert_eq!(held.record_acceptance(accepted(start_op())).await, Err(invalid("acceptance_not_allowed_in_phase")));
    assert_eq!(held.begin_send(input_op(1), proof(&input_op(1))).await, Err(invalid("input_not_allowed_in_phase")));
    assert_eq!(held.record_acceptance(accepted(input_op(1))).await, Err(invalid("acceptance_of_input")));
    assert_eq!(
        held.confirm_input(InputSeq(1), accepted(start_op())).await,
        Err(invalid("confirm_evidence_mismatch"))
    );
    assert_eq!(
        held.confirm_input(InputSeq(1), accepted(input_op(1))).await,
        Err(invalid("confirm_not_allowed_in_phase"))
    );
    assert_eq!(stored(&store).await.1, 1_000_000_000);

    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    held.record_acceptance(accepted(start_op())).await.unwrap();
    assert_eq!(held.begin_send(start_op(), proof(&start_op())).await, Err(invalid("start_not_allowed_in_phase")));
    let mut elsewhere = accepted(start_op());
    elsewhere.remote.task_id = "rt-other".into();
    assert_eq!(held.record_acceptance(elsewhere).await, Err(invalid("remote_task_mismatch")));
    assert_eq!(held.begin_send(input_op(1), proof(&input_op(1))).await, Err(invalid("input_not_pending")));
    worker.push(pending(1)).await;
    worker.push(pending(2)).await;
    assert_eq!(held.begin_send(input_op(2), proof(&input_op(2))).await, Err(invalid("input_out_of_order")));
    assert_eq!(
        held.confirm_input(InputSeq(2), accepted(input_op(2))).await,
        Err(invalid("input_gap")),
        "an unconfirmed earlier input is never skipped"
    );
}

#[tokio::test]
async fn inputs_are_confirmed_as_a_gap_free_prefix() {
    let (store, worker, mut held) = held().await;
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    held.record_acceptance(accepted(start_op())).await.unwrap();
    worker.push(pending(1)).await;
    worker.push(pending(2)).await;
    let view = held.refresh().await.unwrap();
    assert_eq!(view.control.pending_input.len(), 2);

    held.begin_send(input_op(1), proof(&input_op(1))).await.unwrap();
    let (doc, before) = stored(&store).await;
    assert_eq!(doc["connector"]["input_sending"], 1);
    held.begin_send(input_op(1), proof(&input_op(1))).await.unwrap();
    assert_eq!(stored(&store).await.1, before, "repeat needs no write");

    assert_eq!(
        held.confirm_input(InputSeq(1), accepted(input_op(1))).await.unwrap(),
        IdempotentWrite::Applied
    );
    let (doc, _) = stored(&store).await;
    assert_eq!(doc["execution"]["confirmed_input_through"], 1);
    // The Connector does not remove the delivered entry; it only hides it.
    assert_eq!(doc["control"]["pending_input"].as_array().unwrap().len(), 2);
    assert_eq!(
        held.view().control.pending_input.iter().map(|p| p.seq.0).collect::<Vec<_>>(),
        vec![2]
    );
    assert_eq!(
        held.confirm_input(InputSeq(1), accepted(input_op(1))).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );

    held.begin_send(input_op(2), proof(&input_op(2))).await.unwrap();
    held.confirm_input(InputSeq(2), accepted(input_op(2))).await.unwrap();
    let (doc, _) = stored(&store).await;
    assert_eq!(doc["execution"]["confirmed_input_through"], 2);
    assert_eq!(doc["connector"]["input_sending"], 2);
    assert!(held.refresh().await.unwrap().control.pending_input.is_empty());
}

#[tokio::test]
async fn acceptance_returns_a_cancel_written_meanwhile() {
    let (store, worker, mut held) = held().await;
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    // The Worker writes the cancel while the call is on the wire.
    assert!(worker.cancel().await);
    assert_eq!(stored(&store).await.1, 1_000_000_002);
    let cancel = held.record_acceptance(accepted(start_op())).await.unwrap();
    assert_eq!(cancel, Some(cancel_intent()));
    let (doc, version) = stored(&store).await;
    assert_eq!(version, 1_000_000_003, "read + 1 on top of the Worker's write");
    assert_eq!(doc["control"]["cancel"]["request_id"], "cancel-1", "the Worker's field survives");
    assert_eq!(doc["execution"]["phase"], "accepted");
    assert_eq!(held.view().control.cancel, Some(cancel_intent()));
}

#[tokio::test]
async fn cancel_observations_only_move_forward() {
    let (store, _worker, mut held) = held().await;
    assert_eq!(
        held.record_cancel(CancelObservation::NotSent).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(held.record_cancel(CancelObservation::Sending).await.unwrap(), IdempotentWrite::Applied);
    assert_eq!(stored(&store).await.0["execution"]["cancellation"]["kind"], "sending");
    assert_eq!(held.record_cancel(CancelObservation::Sending).await.unwrap(), IdempotentWrite::AlreadyApplied);
    assert_eq!(held.record_cancel(CancelObservation::NotSent).await, Err(invalid("cancel_observation_backward")));

    // An unknown or refused attempt may be tried again.
    held.record_cancel(CancelObservation::OutcomeUnknown).await.unwrap();
    held.record_cancel(CancelObservation::Sending).await.unwrap();
    held.record_cancel(CancelObservation::Rejected { code: "busy".into() }).await.unwrap();
    held.record_cancel(CancelObservation::RequestAccepted).await.unwrap();
    // An accepted request only moves on to a definite result.
    assert_eq!(held.record_cancel(CancelObservation::Sending).await, Err(invalid("cancel_observation_backward")));
    assert_eq!(
        held.record_cancel(CancelObservation::OutcomeUnknown).await,
        Err(invalid("cancel_observation_backward"))
    );
    held.record_cancel(CancelObservation::ConfirmedCanceled).await.unwrap();
    assert_eq!(stored(&store).await.0["execution"]["cancellation"]["kind"], "confirmed_canceled");
    // A definite result is never replaced.
    assert_eq!(
        held.record_cancel(CancelObservation::OutcomeUnknown).await,
        Err(invalid("cancel_observation_backward"))
    );
    assert_eq!(held.view().execution.cancellation, CancelObservation::ConfirmedCanceled);
}

#[tokio::test]
async fn refresh_shows_worker_writes_and_fences() {
    let (store, worker, mut held) = held().await;
    assert_eq!(held.view().control.cancel, None);
    worker.cancel().await;
    let view = held.refresh().await.unwrap();
    assert_eq!(view.control.cancel, Some(cancel_intent()));
    assert_eq!(held.view(), &view);
    assert_eq!(store.writes_to("disp"), 0, "refresh never writes");

    // The document is gone (e.g. expired): the call is lost, not an error loop.
    use bcs_store_api::json::{JsonMutation, JsonStorePlugin};
    use bcs_store_api::{Expiry, WriteCondition};
    store
        .model
        .mutate_json(&disp_key(), WriteCondition::Any, JsonMutation::Delete, Expiry::Preserve)
        .await
        .unwrap();
    assert_eq!(held.refresh().await, Err(HolderError::Lost));
}

#[tokio::test]
async fn a_rejected_write_is_redone_from_a_fresh_read() {
    let (store, _worker, mut held) = held().await;
    store.reject_json(2);
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    let (doc, version) = stored(&store).await;
    assert_eq!((doc["execution"]["phase"].as_str(), version), (Some("sending"), 1_000_000_001));
    let calls = store.calls();
    assert_eq!(calls.iter().filter(|c| c.starts_with("json.get")).count(), 3, "one read per attempt: {calls:?}");

    // Sustained contention is bounded and reported as not applied.
    store.reject_json(100);
    let error = held.record_acceptance(accepted(start_op())).await.unwrap_err();
    assert_eq!(
        error,
        HolderError::Store(StoreError::NotApplied { code: "disp_write_contended".into() })
    );
    assert_eq!(stored(&store).await.1, 1_000_000_001);
}

#[tokio::test]
async fn every_write_survives_a_lost_response() {
    let (store, worker, mut held) = held().await;

    store.lose_write(1);
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    assert_eq!(stored(&store).await.1, 1_000_000_001, "applied once, not twice");

    store.lose_write(1);
    held.record_acceptance(accepted(start_op())).await.unwrap();
    assert_eq!(stored(&store).await.1, 1_000_000_002);

    worker.push(pending(1)).await; // +1
    store.lose_write(1);
    held.begin_send(input_op(1), proof(&input_op(1))).await.unwrap();
    assert_eq!(stored(&store).await.1, 1_000_000_004);

    store.lose_write(1);
    assert_eq!(
        held.confirm_input(InputSeq(1), accepted(input_op(1))).await.unwrap(),
        IdempotentWrite::Applied,
        "the lost write was this call's own"
    );
    assert_eq!(stored(&store).await.1, 1_000_000_005);

    store.lose_write(1);
    assert_eq!(held.record_cancel(CancelObservation::Sending).await.unwrap(), IdempotentWrite::Applied);
    assert_eq!(stored(&store).await.1, 1_000_000_006);

    store.lose_write(1);
    assert_eq!(
        held.finish(FinalDispatchOutcome::Completed, "ok".into()).await.unwrap(),
        IdempotentWrite::Applied
    );
    let (doc, version) = stored(&store).await;
    assert_eq!(version, 1_000_000_007);
    assert_eq!(doc["execution"]["phase"], "ended");
}

#[tokio::test]
async fn every_write_reports_a_definite_failure() {
    let (store, worker, mut held) = held().await;
    let failed = |result: Result<(), HolderError>| {
        assert!(
            matches!(result, Err(HolderError::Store(StoreError::NotApplied { .. }))),
            "{result:?}"
        );
    };

    store.fail_write(1);
    failed(held.begin_send(start_op(), proof(&start_op())).await.map(|_| ()));
    assert_eq!(stored(&store).await.1, 1_000_000_000);
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();

    store.fail_write(1);
    failed(held.record_acceptance(accepted(start_op())).await.map(|_| ()));
    held.record_acceptance(accepted(start_op())).await.unwrap();

    worker.push(pending(1)).await;
    store.fail_write(1);
    failed(held.begin_send(input_op(1), proof(&input_op(1))).await.map(|_| ()));
    held.begin_send(input_op(1), proof(&input_op(1))).await.unwrap();

    store.fail_write(1);
    failed(held.confirm_input(InputSeq(1), accepted(input_op(1))).await.map(|_| ()));
    store.fail_write(1);
    failed(held.record_cancel(CancelObservation::Sending).await.map(|_| ()));
    store.fail_write(1);
    failed(held.finish(FinalDispatchOutcome::Completed, "ok".into()).await.map(|_| ()));
    let (doc, _) = stored(&store).await;
    assert_eq!(doc["execution"]["phase"], "accepted", "a failed write is never reported as success");
    assert_eq!(held_members(&store, &instance("conn-03")).await, vec![DID.to_owned()]);

    // The held-index removal failing after ENDED does not undo the end.
    store.fail_write(2);
    assert_eq!(
        held.finish(FinalDispatchOutcome::Completed, "ok".into()).await.unwrap(),
        IdempotentWrite::Applied
    );
    assert_eq!(held_members(&store, &instance("conn-03")).await, vec![DID.to_owned()]);
}

#[tokio::test]
async fn a_read_failure_is_an_error_not_a_lost_holder() {
    use bcs_store_api::json::{JsonMutation, JsonStorePlugin};
    use bcs_store_api::{Expiry, WriteCondition};
    let (store, _worker, mut held) = held().await;
    // A document this version cannot read must not look like a lost call.
    let (mut doc, version) = stored(&store).await;
    doc["spec"]["schema_version"] = serde_json::json!(9);
    store
        .model
        .mutate_json(
            &disp_key(),
            WriteCondition::Version(bcs_store_api::Version(version)),
            JsonMutation::Replace(doc),
            Expiry::Preserve,
        )
        .await
        .unwrap();
    assert_eq!(
        held.refresh().await,
        Err(HolderError::Store(StoreError::UnsupportedFormat { format: 9 }))
    );
}

#[tokio::test]
async fn an_input_intent_of_a_former_holder_is_reported_as_maybe_sent() {
    use bcs_service_api::core::dist_connector::INPUT_MAYBE_SENT;
    use bcs_service_api::port::repo::distributed::ownership::Liveness;
    let (store, worker, mut first) = held().await;
    first.begin_send(start_op(), proof(&start_op())).await.unwrap();
    first.record_acceptance(accepted(start_op())).await.unwrap();
    worker.push(pending(1)).await;
    worker.push(pending(2)).await;
    first.begin_send(input_op(1), proof(&input_op(1))).await.unwrap();
    // The same handle may ask again: its own intent.
    first.begin_send(input_op(1), proof(&input_op(1))).await.unwrap();

    // The holder dies after recording intent; another Connector takes over.
    let q = queue_with(&store, Arc::new(FixedLiveness(Liveness::Dead)), config(1));
    let ClaimOutcome::Acquired(mut second) = q.claim(claim_request(&instance("conn-07"))).await.unwrap() else {
        panic!("no takeover");
    };
    let before = stored(&store).await.1;
    assert_eq!(second.begin_send(input_op(1), proof(&input_op(1))).await, Err(invalid(INPUT_MAYBE_SENT)));
    assert_eq!(stored(&store).await.1, before, "no write");
    // Once it is confirmed, the next input is a fresh send for the new holder.
    second.confirm_input(InputSeq(1), accepted(input_op(1))).await.unwrap();
    second.begin_send(input_op(2), proof(&input_op(2))).await.unwrap();
    assert_eq!(stored(&store).await.0["connector"]["input_sending"], 2);
}

#[tokio::test]
async fn a_write_that_did_not_land_is_made_again() {
    let (store, _worker, mut held) = held().await;
    store.drop_write(1); // not applied, outcome unknown
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    let (doc, version) = stored(&store).await;
    assert_eq!((doc["execution"]["phase"].as_str(), version), (Some("sending"), 1_000_000_001));
    assert_eq!(store.count("json.mutate"), 2);

    store.drop_write(1);
    assert_eq!(
        held.finish(FinalDispatchOutcome::Failed, "x".into()).await.unwrap(),
        IdempotentWrite::Applied
    );
    assert_eq!(stored(&store).await.0["execution"]["phase"], "ended");
}

#[tokio::test]
async fn a_failed_read_after_an_unknown_write_stays_unknown() {
    let (store, _worker, mut held) = held().await;
    store.lose_write(1); // applied, response lost
    store.fail_get(2); // 1 = before the write, 2 = the re-read
    let error = held.finish(FinalDispatchOutcome::Completed, "ok".into()).await.unwrap_err();
    assert_eq!(
        error,
        HolderError::Store(StoreError::OutcomeUnknown { operation_id: DID.into() }),
        "never reported as definitely not applied"
    );
    assert_eq!(stored(&store).await.0["execution"]["phase"], "ended");
    // A retry settles it.
    assert_eq!(
        held.finish(FinalDispatchOutcome::Completed, "ok".into()).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );
}

#[tokio::test]
async fn an_input_the_worker_never_queued_cannot_be_confirmed() {
    let (store, _worker, mut held) = held().await;
    held.begin_send(start_op(), proof(&start_op())).await.unwrap();
    held.record_acceptance(accepted(start_op())).await.unwrap();
    assert_eq!(
        held.confirm_input(InputSeq(1), accepted(input_op(1))).await,
        Err(invalid("input_not_pending"))
    );
    assert_eq!(stored(&store).await.0["execution"]["confirmed_input_through"], serde_json::Value::Null);
}
