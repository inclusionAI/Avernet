//! The fenced claim: one winner, held index order, takeover, lost responses.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod common;

use bcs_dist_model::*;
use bcs_dist_store::error::NEED_CONTRACT;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::dispatch::*;
use bcs_service_api::port::repo::distributed::ownership::Liveness;
use bcs_store_api::StoreCapability;
use common::*;
use std::sync::Arc;

fn store() -> Arc<Recorder> {
    Arc::new(Recorder::default())
}

#[tokio::test]
async fn first_claim_takes_the_call_at_the_claim_version() {
    let store = store();
    let worker = WorkerFixture::new(&store);
    assert!(worker.create().await);
    let me = instance("conn-03");
    store.clear();
    let held = acquire(&queue(&store), &me).await;

    assert_eq!(held.holder(), &Holder { instance: me.clone(), epoch: HolderEpoch(1) });
    assert_eq!(held.view().execution.phase, DispatchPhase::Claimed);
    let (doc, version) = stored(&store).await;
    assert_eq!(version, 1_000_000_000, "claim version = holder epoch x 10^9");
    assert_eq!(doc["execution"]["holder"]["epoch"], 1);
    assert_eq!(doc["execution"]["holder"]["instance"]["node"], "conn-03");
    assert_eq!(doc["execution"]["phase"], "claimed");
    assert_eq!(doc["connector"], serde_json::json!({"input_sending": null, "finish_evidence": null}));
    // The Worker's objects are untouched.
    assert_eq!(doc["spec"]["did"], DID);
    assert_eq!(doc["control"], serde_json::json!({"cancel": null, "pending_input": []}));
    assert_eq!(held_members(&store, &me).await, vec![DID.to_owned()]);

    // Listed as held before the claim write.
    let calls = store.calls();
    let add = calls.iter().position(|c| c.starts_with("zset.add")).unwrap();
    let write = calls.iter().position(|c| c.starts_with("json.mutate")).unwrap();
    assert!(add < write, "{calls:?}");
}

#[tokio::test]
async fn two_claimers_one_winner() {
    let store = store();
    WorkerFixture::new(&store).create().await;
    // Both read the unclaimed document before either writes: both then write
    // the same claim version, and equal is rejected for the second.
    store.interleave();
    let (a, b) = (instance("conn-a"), instance("conn-b"));
    let (qa, qb) = (queue(&store), queue(&store));
    let (ra, rb) = tokio::join!(qa.claim(claim_request(&a)), qb.claim(claim_request(&b)));
    let names = [outcome_name(&ra.unwrap()), outcome_name(&rb.unwrap())];
    assert_eq!(names.iter().filter(|n| **n == "acquired").count(), 1, "{names:?}");
    assert_eq!(names.iter().filter(|n| **n == "held_by_other").count(), 1, "{names:?}");
    let (doc, version) = stored(&store).await;
    assert_eq!(version, 1_000_000_000);
    let winner = doc["execution"]["holder"]["instance"]["node"].as_str().unwrap().to_owned();
    let (won, lost) = if winner == "conn-a" { (&a, &b) } else { (&b, &a) };
    assert!(held_members(&store, lost).await.is_empty(), "the loser unlists the call");
    assert_eq!(held_members(&store, won).await, vec![DID.to_owned()]);
    assert_eq!(store.count("json.mutate") + store.count("json.rejected"), 2, "both tried to claim");
}

#[tokio::test]
async fn a_lost_race_is_decided_from_a_fresh_read() {
    // The claim write is rejected (someone wrote since the read): re-read, re-decide.
    let store = store();
    WorkerFixture::new(&store).create().await;
    store.reject_json(1);
    let held = acquire(&queue(&store), &instance("conn-03")).await;
    assert_eq!(held.holder().epoch, HolderEpoch(1));
    assert_eq!(stored(&store).await.1, 1_000_000_000);

    // Rejected every time with no holder to report: a definite, retryable error.
    let other = Arc::new(Recorder::default());
    WorkerFixture::new(&other).create().await;
    other.reject_json(10);
    let error = match queue(&other).claim(claim_request(&instance("conn-03"))).await {
        Err(error) => error,
        Ok(outcome) => panic!("unexpected {}", outcome_name(&outcome)),
    };
    assert_eq!(error, StoreError::NotApplied { code: "disp_claim_contended".into() });
}

#[tokio::test]
async fn same_instance_gets_owned_by_me_not_a_second_handle() {
    let store = store();
    WorkerFixture::new(&store).create().await;
    let me = instance("conn-03");
    let q = queue(&store);
    let held = acquire(&q, &me).await;
    match q.claim(claim_request(&me)).await.unwrap() {
        ClaimOutcome::OwnedByMe(holder) => assert_eq!(&holder, held.holder()),
        other => panic!("{}", outcome_name(&other)),
    }
    assert_eq!(stored(&store).await.1, 1_000_000_000, "no second claim write");
    assert_eq!(held_members(&store, &me).await, vec![DID.to_owned()]);
}

#[tokio::test]
async fn missing_and_ended_calls_are_not_held() {
    let store = store();
    let me = instance("conn-03");
    let q = queue(&store);
    assert_eq!(outcome_name(&q.claim(claim_request(&me)).await.unwrap()), "not_found");
    assert!(held_members(&store, &me).await.is_empty());

    WorkerFixture::new(&store).create().await;
    let mut held = acquire(&q, &me).await;
    held.finish(FinalDispatchOutcome::Canceled, "canceled_before_send".into()).await.unwrap();
    assert_eq!(outcome_name(&q.claim(claim_request(&me)).await.unwrap()), "ended");
    assert_eq!(
        outcome_name(&q.claim(claim_request(&instance("conn-07"))).await.unwrap()),
        "ended"
    );
    assert!(held_members(&store, &instance("conn-07")).await.is_empty());
}

#[tokio::test]
async fn invalid_dispatch_id_fails_before_any_write() {
    let store = store();
    let mut request = claim_request(&instance("conn-03"));
    request.did = Did("no-shard".into());
    let error = match queue(&store).claim(request).await {
        Err(error) => error,
        Ok(_) => panic!("accepted"),
    };
    assert!(matches!(error, StoreError::Invalid { .. }));
    assert!(store.calls().is_empty());
}

#[tokio::test]
async fn pending_membership_means_held_by_other() {
    // MembershipPort provider pending from the executor team: the stub cannot
    // prove a holder dead, so another instance's call is never taken over.
    let store = store();
    WorkerFixture::new(&store).create().await;
    let q = queue(&store);
    let first = acquire(&q, &instance("conn-03")).await;
    let other = instance("conn-07");
    match q.claim(claim_request(&other)).await.unwrap() {
        ClaimOutcome::HeldByOther(holder) => assert_eq!(&holder, first.holder()),
        outcome => panic!("{}", outcome_name(&outcome)),
    }
    assert_eq!(stored(&store).await.1, 1_000_000_000);
    assert!(held_members(&store, &other).await.is_empty());
}

#[tokio::test]
async fn pending_membership_fails_with_need_contract() {
    use bcs_dist_connector_store::membership::PendingMembership;
    use bcs_service_api::port::repo::distributed::common::PageRequest;
    use bcs_service_api::port::repo::distributed::ownership::{MembershipLease, MembershipPort, RuntimeRole};
    use std::num::NonZeroU32;
    use std::time::Duration;
    let stub = PendingMembership;
    let need = StoreError::NotApplied { code: NEED_CONTRACT.into() };
    let me = instance("conn-03");
    let lease = MembershipLease { instance: me.clone() };
    let ttl = Duration::from_secs(3);
    assert_eq!(stub.register(me.clone(), RuntimeRole::Connector, String::new(), ttl).await, Err(need.clone()));
    assert_eq!(stub.renew(&lease, ttl).await, Err(need.clone()));
    assert_eq!(stub.set_draining(&lease).await, Err(need.clone()));
    assert_eq!(stub.liveness(&me).await, Err(need.clone()));
    let page = PageRequest { after: None, limit: NonZeroU32::MIN };
    assert_eq!(stub.members(RuntimeRole::Connector, page).await, Err(need));
}

#[tokio::test]
async fn only_a_dead_holder_is_taken_over() {
    for (liveness, taken) in [(Liveness::Alive, false), (Liveness::Unknown, false), (Liveness::Dead, true)] {
        let store = store();
        WorkerFixture::new(&store).create().await;
        let mut first = acquire(&queue(&store), &instance("conn-03")).await;
        first.begin_send(start_op()).await.unwrap();
        first.record_acceptance(accepted(start_op())).await.unwrap();

        let q = queue_with(&store, Arc::new(FixedLiveness(liveness)), config(1));
        let other = instance("conn-07");
        let outcome = q.claim(claim_request(&other)).await.unwrap();
        if !taken {
            assert_eq!(outcome_name(&outcome), "held_by_other", "{liveness:?}");
            assert!(first.refresh().await.is_ok());
            continue;
        }
        let ClaimOutcome::Acquired(mut second) = outcome else {
            panic!("dead holder not taken over");
        };
        assert_eq!(second.holder(), &Holder { instance: other.clone(), epoch: HolderEpoch(2) });
        let (doc, version) = stored(&store).await;
        assert_eq!(version, 2_000_000_000, "takeover jumps to the next band");
        // The phase and the remote task survive: the new holder re-attaches.
        assert_eq!(doc["execution"]["phase"], "accepted");
        assert_eq!(doc["execution"]["remote"]["task_id"], "rt-77c2");
        assert_eq!(second.view().execution.remote, Some(remote()));
        assert_eq!(held_members(&store, &other).await, vec![DID.to_owned()]);

        // The former holder can change nothing any more.
        assert_eq!(first.refresh().await, Err(HolderError::Lost));
        assert_eq!(
            first.finish(FinalDispatchOutcome::Completed, "late".into()).await,
            Err(HolderError::Lost)
        );
        assert_eq!(first.record_cancel(CancelObservation::Sending).await, Err(HolderError::Lost));
        assert_eq!(stored(&store).await.1, 2_000_000_000);
        second.finish(FinalDispatchOutcome::Completed, "ok".into()).await.unwrap();
        assert_eq!(stored(&store).await.1, 2_000_000_001);
    }
}

#[tokio::test]
async fn lost_claim_response_is_resolved_by_reading_back() {
    let store = store();
    WorkerFixture::new(&store).create().await;
    let me = instance("conn-03");
    store.lose_write(2); // 1 = held index, 2 = the claim
    let held = acquire(&queue(&store), &me).await;
    assert_eq!(held.holder().epoch, HolderEpoch(1));
    assert_eq!(stored(&store).await.1, 1_000_000_000);
    assert_eq!(held_members(&store, &me).await, vec![DID.to_owned()]);
}

#[tokio::test]
async fn definite_claim_failure_is_an_error_and_changes_nothing() {
    let store = store();
    WorkerFixture::new(&store).create().await;
    let me = instance("conn-03");
    store.fail_write(2);
    let error = match queue(&store).claim(claim_request(&me)).await {
        Err(error) => error,
        Ok(outcome) => panic!("{}", outcome_name(&outcome)),
    };
    assert!(matches!(error, StoreError::NotApplied { .. }), "{error:?}");
    assert_eq!(stored(&store).await.1, 1, "still as created");
    // Listed too much is allowed; a retry then claims normally.
    assert_eq!(held_members(&store, &me).await, vec![DID.to_owned()]);
    assert_eq!(acquire(&queue(&store), &me).await.holder().epoch, HolderEpoch(1));

    // A held-index failure stops the claim before the document is touched.
    let other = Arc::new(Recorder::default());
    WorkerFixture::new(&other).create().await;
    other.fail_write(1);
    assert!(queue(&other).claim(claim_request(&me)).await.is_err());
    assert_eq!(stored(&other).await.1, 1);
}

#[tokio::test]
async fn resume_owned_advances_the_generation() {
    let store = store();
    WorkerFixture::new(&store).create().await;
    let me = instance("conn-03");
    let q = queue(&store);
    let mut old = acquire(&q, &me).await;
    old.begin_send(start_op()).await.unwrap();
    let previous = old.holder().clone();
    let request = ResumeOwnedDispatch { did: did(), previous: previous.clone(), request_id: RequestId("r-2".into()) };

    let ClaimOutcome::Acquired(resumed) = q.resume_owned(request.clone()).await.unwrap() else {
        panic!("not resumed");
    };
    assert_eq!(resumed.holder(), &Holder { instance: me.clone(), epoch: HolderEpoch(2) });
    assert_eq!(resumed.view().execution.phase, DispatchPhase::Sending, "the phase is kept");
    assert_eq!(stored(&store).await.1, 2_000_000_000);
    assert_eq!(old.refresh().await, Err(HolderError::Lost), "the old generation is fenced");

    // A stale request (the holder moved on) does not claim again.
    match q.resume_owned(request).await.unwrap() {
        ClaimOutcome::OwnedByMe(holder) => assert_eq!(holder.epoch, HolderEpoch(2)),
        other => panic!("{}", outcome_name(&other)),
    }
    assert_eq!(stored(&store).await.1, 2_000_000_000);
}

#[tokio::test]
async fn resume_owned_reports_what_the_document_shows() {
    let store = store();
    let q = queue(&store);
    let previous = Holder { instance: instance("conn-03"), epoch: HolderEpoch(1) };
    let request = ResumeOwnedDispatch { did: did(), previous, request_id: RequestId("r".into()) };
    assert_eq!(outcome_name(&q.resume_owned(request.clone()).await.unwrap()), "not_found");

    WorkerFixture::new(&store).create().await;
    assert!(matches!(q.resume_owned(request.clone()).await, Err(StoreError::Invalid { .. })));

    let mut held = acquire(&q, &instance("conn-07")).await;
    assert_eq!(outcome_name(&q.resume_owned(request.clone()).await.unwrap()), "held_by_other");
    held.finish(FinalDispatchOutcome::Failed, "x".into()).await.unwrap();
    assert_eq!(outcome_name(&q.resume_owned(request).await.unwrap()), "ended");
}

#[tokio::test]
async fn startup_fails_without_a_required_capability() {
    use bcs_dist_connector_store::StoreDispatchQueue;
    use bcs_dist_connector_store::membership::PendingMembership;
    for capability in [
        StoreCapability::AtomicJson,
        StoreCapability::ZSet,
        StoreCapability::Stream,
        StoreCapability::StreamGroups,
    ] {
        let store = store();
        store.deny(capability);
        let result = StoreDispatchQueue::new(
            store.clone(),
            store.clone(),
            store.clone(),
            Arc::new(PendingMembership),
            layout(),
            Arc::new(FixedClock(NOW)),
            config(1),
        );
        match result {
            Err(StoreError::NotApplied { code }) => {
                assert_eq!(code, format!("missing_store_capability:{capability:?}"));
            }
            _ => panic!("started without {capability:?}"),
        }
    }
    let mut bad = config(1);
    bad.write_attempts = 0;
    let store = store();
    assert!(
        StoreDispatchQueue::new(
            store.clone(),
            store.clone(),
            store.clone(),
            Arc::new(PendingMembership),
            layout(),
            Arc::new(FixedClock(NOW)),
            bad,
        )
        .is_err()
    );
}

#[tokio::test]
async fn unsupported_document_version_is_refused() {
    let store = store();
    let mut spec = dispatch_spec(&did());
    spec.schema_version = 2;
    WorkerFixture::new(&store).create_spec(spec).await;
    let error = match queue(&store).claim(claim_request(&instance("conn-03"))).await {
        Err(error) => error,
        Ok(_) => panic!("claimed an unreadable document"),
    };
    assert_eq!(error, StoreError::UnsupportedFormat { format: 2 });
    assert_eq!(stored(&store).await.1, 1);
}

#[tokio::test]
async fn held_by_lists_the_instance_index() {
    use std::num::NonZeroU32;
    let store = store();
    WorkerFixture::new(&store).create().await;
    let me = instance("conn-03");
    let q = queue(&store);
    let _held = acquire(&q, &me).await;
    let limit = NonZeroU32::new(10).unwrap();
    assert_eq!(q.held_by(&me, limit).await.unwrap(), vec![did()]);
    assert!(q.held_by(&instance("conn-07"), limit).await.unwrap().is_empty());
}

/// A raw holder write, as a former holder landing it at `version`.
fn raw_execution(execution: serde_json::Value, version: u64) -> (String, bcs_store_api::WriteCondition, bcs_store_api::json::JsonMutation) {
    use bcs_store_api::json::{JsonEdit, JsonMutation};
    (
        disp_key(),
        bcs_store_api::WriteCondition::Newer(bcs_store_api::Version(version)),
        JsonMutation::Patch(vec![JsonEdit::Set { pointer: "/execution".into(), value: execution }]),
    )
}

#[tokio::test]
async fn a_takeover_never_undoes_the_former_holders_last_write() {
    // The "dead" holder was only slow: its finish lands between the
    // claimant's read and its claim.
    let store = store();
    WorkerFixture::new(&store).create().await;
    let mut first = acquire(&queue(&store), &instance("conn-03")).await;
    first.begin_send(start_op()).await.unwrap();
    first.record_acceptance(accepted(start_op())).await.unwrap();
    let (doc, version) = stored(&store).await;
    let mut ended = doc["execution"].clone();
    ended["phase"] = serde_json::json!("ended");
    ended["outcome"] = serde_json::json!("completed");
    let (key, condition, mutation) = raw_execution(ended, version + 1);
    store.before_next_mutate(key, condition, mutation);

    let q = queue_with(&store, Arc::new(FixedLiveness(Liveness::Dead)), config(1));
    let other = instance("conn-07");
    let outcome = q.claim(claim_request(&other)).await.unwrap();
    assert_eq!(outcome_name(&outcome), "ended", "an ended call is not run again");
    let (doc, _) = stored(&store).await;
    assert_eq!(doc["execution"]["phase"], "ended");
    assert_eq!(doc["execution"]["outcome"], "completed");
    assert!(held_members(&store, &other).await.is_empty());
}

#[tokio::test]
async fn a_takeover_starts_from_what_is_stored_after_the_claim() {
    // The former holder recorded the remote task between the claimant's read
    // and its claim: the new holder must re-attach, not send again.
    let store = store();
    WorkerFixture::new(&store).create().await;
    let mut first = acquire(&queue(&store), &instance("conn-03")).await;
    first.begin_send(start_op()).await.unwrap();
    let (doc, version) = stored(&store).await;
    let mut accepted_now = doc["execution"].clone();
    accepted_now["phase"] = serde_json::json!("accepted");
    accepted_now["remote"] = serde_json::json!({"task_id": "rt-77c2", "context_id": null});
    let (key, condition, mutation) = raw_execution(accepted_now, version + 1);
    store.before_next_mutate(key, condition, mutation);

    let q = queue_with(&store, Arc::new(FixedLiveness(Liveness::Dead)), config(1));
    let ClaimOutcome::Acquired(second) = q.claim(claim_request(&instance("conn-07"))).await.unwrap() else {
        panic!("no takeover");
    };
    assert_eq!(second.view().execution.phase, DispatchPhase::Accepted);
    assert_eq!(second.view().execution.remote.as_ref().map(|r| r.task_id.as_str()), Some("rt-77c2"));
    let (doc, version) = stored(&store).await;
    assert_eq!(version, 2_000_000_000);
    assert_eq!(doc["execution"]["phase"], "accepted");
    assert_eq!(doc["execution"]["holder"]["instance"]["node"], "conn-07");
}

#[tokio::test]
async fn a_claim_write_that_did_not_land_is_made_again() {
    let store = store();
    WorkerFixture::new(&store).create().await;
    let me = instance("conn-03");
    store.drop_write(2); // the claim: not applied, outcome unknown
    let held = acquire(&queue(&store), &me).await;
    assert_eq!(held.holder().epoch, HolderEpoch(1));
    assert_eq!(stored(&store).await.1, 1_000_000_000);
    assert_eq!(store.count("json.mutate"), 2, "the second attempt wrote it");
    // An unknown claim write may still land later: the call is never
    // unlisted on it (the index may list too much, never too little).
    assert_eq!(store.count("zset.remove"), 0, "{:?}", store.calls());
    assert_eq!(held_members(&store, &me).await, vec![DID.to_owned()]);
}

#[tokio::test]
async fn an_unreadable_document_after_an_unknown_claim_is_outcome_unknown() {
    let store = store();
    WorkerFixture::new(&store).create().await;
    let me = instance("conn-03");
    store.lose_write(2);
    store.fail_get(2); // 1 = the claim's read, 2 = the read-back
    let error = match queue(&store).claim(claim_request(&me)).await {
        Err(error) => error,
        Ok(outcome) => panic!("{}", outcome_name(&outcome)),
    };
    assert_eq!(error, StoreError::OutcomeUnknown { operation_id: DID.into() });
    // The claim landed; the index still lists it and a retry finds it.
    assert_eq!(held_members(&store, &me).await, vec![DID.to_owned()]);
    assert_eq!(outcome_name(&queue(&store).claim(claim_request(&me)).await.unwrap()), "owned_by_me");
}
