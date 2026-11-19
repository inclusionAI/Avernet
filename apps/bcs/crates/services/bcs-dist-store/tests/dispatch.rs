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
        f.board.claim(other.clone()).await.unwrap(),
        ClaimOutcome::HeldByOther(_)
    ));
    *f.membership.0.lock().unwrap() = Liveness::Alive;
    let resume = ResumeOwnedDispatch {
        did: spec().did,
        previous: old.holder().clone(),
        request_id: RequestId("resume".into()),
    };
    assert!(matches!(
        f.board.resume_owned(resume.clone()).await.unwrap(),
        ClaimOutcome::Acquired(_)
    ));
    assert!(matches!(
        f.board.resume_owned(resume).await.unwrap(),
        ClaimOutcome::OwnedByMe(_)
    ));
    assert!(matches!(
        old.begin_send(start()).await,
        Err(HolderError::Lost)
    ));
    *f.membership.0.lock().unwrap() = Liveness::Dead;
    assert!(matches!(
        f.board.claim(other).await.unwrap(),
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
    held.begin_send(start()).await.unwrap();
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
    assert!(held.begin_send(input(2)).await.is_err());
    held.begin_send(input(1)).await.unwrap();
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
    held.begin_send(start()).await.unwrap();
    held.begin_send(start()).await.unwrap();
    held.record_acceptance(acceptance(start())).await.unwrap();
    assert!(matches!(
        held.begin_send(start()).await,
        Err(HolderError::InvalidTransition { .. })
    ));
    let op = SendOperation::Input {
        seq: InputSeq(1),
        request_id: RequestId("input-once".into()),
    };
    held.begin_send(op.clone()).await.unwrap();
    held.confirm_input(InputSeq(1), acceptance(op.clone()))
        .await
        .unwrap();
    assert!(matches!(
        held.begin_send(op.clone()).await,
        Err(HolderError::InvalidTransition { .. })
    ));
    assert_eq!(
        held.confirm_input(InputSeq(1), acceptance(op))
            .await
            .unwrap(),
        IdempotentWrite::AlreadyApplied
    );
}
