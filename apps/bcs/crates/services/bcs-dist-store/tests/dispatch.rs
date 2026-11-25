#![allow(clippy::unwrap_used, clippy::expect_used, reason = "provider tests")]
mod common;
mod dispatch_support;
mod task_feed_support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, dispatch::*, ownership::*};
use bcs_store_api::zset::ZSetStorePlugin;
use dispatch_support::*;

#[tokio::test]
async fn creation_retains_provenance_and_repairs_missing_schedule() {
    let f = Fixture::new().await;
    let request = f.create().await;
    f.base
        .storage
        .remove(&f.base.layout.sched(ShardId(42)), &[spec().did.0.clone()])
        .await
        .unwrap();
    f.base.commit(2, vec![]).await;
    assert_eq!(
        f.reopen().create(request.clone()).await.unwrap(),
        IdempotentWrite::AlreadyApplied
    );
    assert_eq!(
        f.board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap()
            .items,
        vec![spec().did]
    );
    let mut changed = request;
    changed.spec.attempt = 2;
    assert_eq!(
        f.board.create(changed).await.unwrap(),
        IdempotentWrite::ContentConflict
    );
}

#[tokio::test]
async fn a_persisted_create_intent_survives_failure_and_independent_scan() {
    let f = Fixture::new().await;
    let spec = spec();
    let source = f
        .base
        .commit(
            1,
            vec![
                bcs_service_api::port::repo::distributed::session::EffectKind::CreateDispatch {
                    spec: spec.clone(),
                },
            ],
        )
        .await
        .remove(0);
    f.base.storage.fail_write(2);
    assert!(matches!(
        f.board
            .create(CreateCommittedDispatch {
                source,
                spec: spec.clone()
            })
            .await,
        Err(StoreError::OutcomeUnknown { .. })
    ));
    let due = f
        .reopen()
        .scan_due(ShardId(42), TimestampMillis(1), page())
        .await
        .unwrap();
    assert!(due.items.contains(&spec.did));
    assert!(f.board.get(&spec.did).await.unwrap().is_some());
}

#[tokio::test]
async fn unknown_holder_cannot_be_taken_over_and_resume_fences_old_actor() {
    let f = Fixture::new().await;
    f.create().await;
    let mut old = f.claim().await;
    *f.membership.0.lock().unwrap() = Liveness::Unknown;
    let other = DispatchClaimRequest {
        did: spec().did,
        me: Instance {
            node: "other".into(),
            incarnation: "two".into(),
        },
        request_id: RequestId("other".into()),
    };
    assert!(matches!(
        f.queue.claim(other.clone()).await.unwrap(),
        ClaimOutcome::HeldByOther(_)
    ));
    *f.membership.0.lock().unwrap() = Liveness::Alive;
    let resume = ResumeOwnedDispatch {
        did: spec().did,
        previous: old.holder().clone(),
        request_id: RequestId("resume".into()),
    };
    assert!(matches!(
        f.queue.resume_owned(resume.clone()).await.unwrap(),
        ClaimOutcome::Acquired(_)
    ));
    assert!(matches!(
        f.queue.resume_owned(resume).await.unwrap(),
        ClaimOutcome::OwnedByMe(_)
    ));
    assert!(matches!(
        old.begin_send(start(), proof(&start())).await,
        Err(HolderError::Lost)
    ));
    *f.membership.0.lock().unwrap() = Liveness::Dead;
    assert!(matches!(
        f.queue.claim(other).await.unwrap(),
        ClaimOutcome::Acquired(_)
    ));
}

#[tokio::test]
async fn input_confirmation_requires_send_intent_and_contiguous_acceptance() {
    let f = Fixture::new().await;
    f.create().await;
    let one = f.input(2, 1).await;
    f.board.push_input(one.clone()).await.unwrap();
    let two = f.input(3, 2).await;
    f.board.push_input(two).await.unwrap();
    let mut held = f.claim().await;
    held.begin_send(start(), proof(&start())).await.unwrap();
    held.record_acceptance(acceptance(start())).await.unwrap();
    let input = |seq| SendOperation::Input {
        seq: InputSeq(seq),
        request_id: RequestId(format!("send-{seq}")),
    };
    assert!(
        held.confirm_input(InputSeq(1), acceptance(input(1)))
            .await
            .is_err()
    );
    assert!(held.begin_send(input(2), proof(&input(2))).await.is_err());
    held.begin_send(input(1), proof(&input(1))).await.unwrap();
    held.confirm_input(InputSeq(1), acceptance(input(1)))
        .await
        .unwrap();
    f.base.commit(4, vec![]).await;
    assert_eq!(
        f.reopen().push_input(one).await.unwrap(),
        ControlOutcome::AlreadyApplied
    );
    assert_eq!(held.refresh().await.unwrap().control.pending_input.len(), 1);
}

#[tokio::test]
async fn old_scan_cannot_hide_cancel_and_terminal_evidence_remains_until_supervision() {
    let f = Fixture::new().await;
    f.create().await;
    let old = f.board.get(&spec().did).await.unwrap().unwrap();
    let cancel = f.cancel(2).await;
    f.board.request_cancel(cancel).await.unwrap();
    assert_eq!(
        f.board
            .reschedule(&spec().did, old.revision, TimestampMillis(999999))
            .await
            .unwrap(),
        RecheckOutcome::NeedsRecheck
    );
    let mut held = f.claim().await;
    held.record_cancel(CancelObservation::OutcomeUnknown)
        .await
        .unwrap();
    assert_ne!(
        held.refresh().await.unwrap().execution.phase,
        DispatchPhase::Ended
    );
    held.finish(FinalDispatchOutcome::Canceled, "confirmed-cancel".into())
        .await
        .unwrap();
    let ended = f.board.get(&spec().did).await.unwrap().unwrap();
    assert_eq!(ended.terminal_evidence.as_deref(), Some("confirmed-cancel"));
    assert!(
        f.board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap()
            .items
            .contains(&spec().did)
    );
    assert_eq!(
        f.board
            .reschedule(&spec().did, ended.revision, TimestampMillis(1))
            .await
            .unwrap(),
        RecheckOutcome::Ended
    );
    assert!(
        f.board
            .scan_due(ShardId(42), TimestampMillis(1), page())
            .await
            .unwrap()
            .items
            .is_empty()
    );
}

#[tokio::test]
async fn accepted_operations_never_grant_another_network_send_permit() {
    let f = Fixture::new().await;
    f.create().await;
    let input = f.input(2, 1).await;
    f.board.push_input(input).await.unwrap();
    let mut held = f.claim().await;
    held.begin_send(start(), proof(&start())).await.unwrap();
    held.begin_send(start(), proof(&start())).await.unwrap();
    held.record_acceptance(acceptance(start())).await.unwrap();
    assert!(matches!(
        held.begin_send(start(), proof(&start())).await,
        Err(HolderError::InvalidTransition { .. })
    ));
    let op = SendOperation::Input {
        seq: InputSeq(1),
        request_id: RequestId("input-once".into()),
    };
    held.begin_send(op.clone(), proof(&op)).await.unwrap();
    held.confirm_input(InputSeq(1), acceptance(op.clone()))
        .await
        .unwrap();
    assert!(matches!(
        held.begin_send(op.clone(), proof(&op)).await,
        Err(HolderError::InvalidTransition { .. })
    ));
    assert_eq!(
        held.confirm_input(InputSeq(1), acceptance(op))
            .await
            .unwrap(),
        IdempotentWrite::AlreadyApplied
    );
}

#[tokio::test]
async fn later_cancels_share_one_obligation_without_losing_request_identity() {
    let f = Fixture::new().await;
    f.create().await;
    let first = f.cancel_with(2, "runner", "retry attempt").await;
    assert_eq!(
        f.board.request_cancel(first.clone()).await.unwrap(),
        ControlOutcome::Applied
    );
    let mut held = f.claim().await;
    held.record_cancel(CancelObservation::OutcomeUnknown)
        .await
        .unwrap();
    let task = f.cancel_with(3, "task", "user canceled task").await;
    assert_eq!(
        f.board.request_cancel(task.clone()).await.unwrap(),
        ControlOutcome::Applied
    );
    let active = f.board.get(&first.did).await.unwrap().unwrap();
    assert_eq!(active.view.control.cancel, Some(first.intent.clone()));
    assert_eq!(active.view.execution.cancellation, CancelObservation::OutcomeUnknown);
    assert_eq!(
        f.board.request_cancel(f.cancel_with(4, "task", "changed").await).await.unwrap(),
        ControlOutcome::ContentConflict
    );
    assert_eq!(
        f.reopen().request_cancel(task).await.unwrap(),
        ControlOutcome::AlreadyApplied
    );

    held.finish(FinalDispatchOutcome::Canceled, "remote-confirmed".into())
        .await
        .unwrap();
    let late = f.cancel_with(5, "late", "terminal check").await;
    assert_eq!(
        f.board.request_cancel(late.clone()).await.unwrap(),
        ControlOutcome::SupersededTerminal {
            outcome: FinalDispatchOutcome::Canceled,
            evidence: "remote-confirmed".into(),
        }
    );
    assert_eq!(
        f.board.request_cancel(f.cancel_with(6, "late", "changed").await).await.unwrap(),
        ControlOutcome::ContentConflict
    );
    assert_eq!(
        f.reopen().request_cancel(late).await.unwrap(),
        ControlOutcome::SupersededTerminal {
            outcome: FinalDispatchOutcome::Canceled,
            evidence: "remote-confirmed".into(),
        }
    );
    assert_eq!(
        f.board.request_cancel(f.cancel_with(7, "runner", "changed").await).await.unwrap(),
        ControlOutcome::ContentConflict
    );
}

/// Interleave a real Worker cancel after the Connector has read, before it claims.
#[tokio::test]
async fn takeover_preserves_a_concurrent_worker_cancel_in_the_same_document() {
    use bcs_store_api::json::JsonStorePlugin;
    let f = Fixture::new().await;
    f.create().await;
    let key = f.base.layout.disp(&spec().did).unwrap();
    let cancel = f.cancel(2).await;
    let pause = f.base.storage.pause_after("json.get", &key, 1);
    let queue = f.queue.clone();
    let claim = tokio::spawn(async move {
        queue
            .claim(DispatchClaimRequest {
                did: spec().did,
                me: me(),
                request_id: RequestId("interleaved".into()),
            })
            .await
    });
    pause.reached().await;
    f.board.request_cancel(cancel.clone()).await.unwrap();
    pause.resume();
    let ClaimOutcome::Acquired(mut held) = claim.await.unwrap().unwrap() else {
        panic!("claim failed")
    };
    assert_eq!(
        held.refresh().await.unwrap().control.cancel,
        Some(cancel.intent)
    );
    let raw = f.base.storage.get_json(&key).await.unwrap().unwrap();
    assert_eq!(raw.version.0, 1_000_000_000);
    assert_eq!(raw.value["spec"]["did"], spec().did.0);
    assert_eq!(raw.value["receipts"].as_array().unwrap().len(), 2);
    assert!(raw.value.get("view").is_none() && raw.value.get("data").is_none());
}

/// Plan T7 slice 4: the store-level `Quarantined` and verification handoff
/// read semantics — the consumable seam handed to T8. A handed-off,
/// deadline-quarantined call is exposed through `DispatchControlPort::get`
/// with its verification record and its `Quarantined` marker, stays in a
/// non-terminal phase with no outcome, and never answers a normal claim.
#[tokio::test]
async fn a_quarantined_handoff_is_readable_and_never_normally_claimable() {
    let f = Fixture::new().await;
    f.board.create(f.create().await).await.unwrap();
    let mut held = f.claim().await;
    held.begin_send(start(), proof(&start())).await.unwrap();
    held.hand_off_verification(VerificationHandoffDraft {
        attempt: 1,
        key: VerificationKey::ByRequest {
            request_id: RequestId(format!("{}:start", spec().did.0)),
        },
        method: VerificationMethod::Retain,
        next_due: TimestampMillis(10),
        backoff_ms: 1000,
    })
    .await
    .unwrap();
    // The verifier claims through the independent epoch line and quarantines
    // at the deadline; nothing terminal is crossed doing so.
    let mut verifier = match f
        .queue
        .claim_verification(VerificationClaimRequest {
            did: spec().did,
            me: me(),
            request_id: RequestId("v1".into()),
        })
        .await
        .unwrap()
    {
        VerificationClaimOutcome::Acquired(handle) => handle,
        _other => panic!("claim_verification must acquire"),
    };
    verifier
        .record_quarantine(TimestampMillis(5000), "stop_verification_deadline")
        .await
        .unwrap();
    assert_eq!(
        verifier
            .record_quarantine(TimestampMillis(6000), "stop_verification_deadline")
            .await
            .unwrap(),
        IdempotentWrite::AlreadyApplied,
        "the durable quarantine record is idempotent"
    );
    // The consumable read seam (T8 maps the HTTP surface onto exactly this).
    let record = f.board.get(&spec().did).await.unwrap().unwrap();
    assert!(record.verification.is_some(), "the handoff is exposed");
    assert!(record.quarantined.is_some(), "the quarantine record is exposed");
    assert_eq!(record.quarantined.as_ref().unwrap().reason, "stop_verification_deadline");
    assert_ne!(record.view.execution.phase, DispatchPhase::Ended, "no terminal crossing");
    assert_eq!(record.view.execution.outcome, None);
    // The retained WORKING-facing state stays intact for the reader.
    assert!(record.terminal_evidence.is_none());
    // Normal claims refuse the verification mode outright.
    match f
        .queue
        .claim(DispatchClaimRequest {
            did: spec().did,
            me: me(),
            request_id: RequestId("late".into()),
        })
        .await
        .unwrap()
    {
        ClaimOutcome::Verification => {}
        _other => panic!("normal claims must refuse verification mode"),
    }
    // The by-request read keeps the original task state half-open: the same
    // read semantics repeat identically after a reopen (restart).
    let record = f.reopen().get(&spec().did).await.unwrap().unwrap();
    assert!(record.quarantined.is_some());
    assert_ne!(record.view.execution.phase, DispatchPhase::Ended);
}
