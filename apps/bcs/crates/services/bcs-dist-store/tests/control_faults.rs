#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "closure protocol tests"
)]
mod common;
mod control_support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{admission::*, control::*};
use common::TID;
use control_support::*;
#[tokio::test]
async fn prepared_attempt_retention_does_not_block_a_concurrent_successful_outcome() {
    let f = std::sync::Arc::new(Fixture::new().await);
    let make_attempt = |id: &str| AttemptIdentity {
        id: RequestId(id.into()),
        head: CommitSeq(0),
        state_digest: Digest("state".into()),
        input_digest: Digest("input".into()),
        context_digest: Digest("context".into()),
        frozen_now: TimestampMillis(101),
    };
    f.store
        .begin_attempt(f.guard().await, make_attempt("running"))
        .await
        .unwrap();
    let failure = AttemptOutcome::Deterministic {
        code: "Conflict".into(),
        isolation_id: RequestId("isolation".into()),
        safe_to_finalize: true,
    };
    let pending = make_attempt("diagnostic");
    let guard = f.guard().await;
    let pause = f.storage.pause_after(
        "document.create",
        &f.layout
            .control_attempt(&f.owner.sid, &RequestId("running".into()))
            .unwrap(),
        1,
    );
    let task = tokio::spawn({
        let f = f.clone();
        let pending = pending.clone();
        let failure = failure.clone();
        async move { f.store.isolate(guard, pending, failure).await }
    });
    pause.reached().await;
    let outcome = AttemptOutcome::Succeeded {
        decision_digest: Digest("decision".into()),
    };
    f.store
        .finish_attempt(
            f.guard().await,
            RequestId("running".into()),
            outcome.clone(),
        )
        .await
        .unwrap();
    pause.resume();
    assert!(matches!(
        task.await.unwrap().unwrap(),
        ControlOutcome::RevisionConflict
    ));
    assert!(matches!(
        f.store
            .isolate(f.guard().await, pending, failure)
            .await
            .unwrap(),
        ControlOutcome::Applied(_)
    ));
    assert_eq!(
        f.store
            .get_attempt(&f.owner.sid, &RequestId("running".into()))
            .await
            .unwrap()
            .unwrap()
            .outcome,
        Some(outcome)
    );
}

#[tokio::test]
async fn lost_closure_progress_and_certificate_responses_resume_the_same_fixed_cut() {
    for lost in 1..=12 {
        let f = Fixture::new().await;
        f.admit("first").await;
        let closure = f.isolate("isolate").await;
        f.terminal(&Tid(TID.into()), "isolate").await;
        f.storage.lose_write(lost);
        let certificate = complete(&f, &closure).await;
        assert_eq!(certificate.closure, closure);
        assert_eq!(certificate.completed_through, closure.journal_cut);
        assert_eq!(complete(&f, &closure).await, certificate);
    }
}

#[tokio::test]
async fn stale_close_cannot_replace_a_new_isolation_after_its_authorization_read() {
    let f = std::sync::Arc::new(Fixture::new().await);
    let first = f.isolate("first").await;
    let guard = f.guard().await;
    // close_gate first reads the raw owner, then the control authorization.
    let pause = f
        .storage
        .pause_after("document.read", &f.layout.ckpt(&f.owner.sid).unwrap(), 2);
    let closing = tokio::spawn({
        let f = f.clone();
        async move {
            f.control
                .close_gate(guard, 1, RequestId("first".into()))
                .await
        }
    });
    pause.reached().await;
    let failure = AttemptIdentity {
        id: RequestId("second-attempt".into()),
        head: CommitSeq(0),
        state_digest: Digest("state".into()),
        input_digest: Digest("input".into()),
        context_digest: Digest("context".into()),
        frozen_now: TimestampMillis(101),
    };
    f.store
        .isolate(
            f.guard().await,
            failure,
            AttemptOutcome::Deterministic {
                code: "SecondFailure".into(),
                isolation_id: RequestId("second".into()),
                safe_to_finalize: true,
            },
        )
        .await
        .unwrap();
    let GateCloseOutcome::Closed(second) = f
        .control
        .close_gate(f.guard().await, 1, RequestId("second".into()))
        .await
        .unwrap()
    else {
        panic!()
    };
    pause.resume();
    assert!(matches!(
        closing.await.unwrap().unwrap(),
        GateCloseOutcome::Conflict
    ));
    assert_ne!(first.operation_id, second.operation_id);
    assert_eq!(
        f.control.gate_state(&f.owner).await.unwrap().closure,
        Some(second)
    );
}

#[tokio::test]
async fn isolation_history_keeps_completion_that_raced_its_prepared_snapshot() {
    let f = std::sync::Arc::new(Fixture::new().await);
    let closure = f.isolate("first").await;
    let certificate = complete(&f, &closure).await;
    let attempt = AttemptIdentity {
        id: RequestId("archive-conflict".into()),
        head: CommitSeq(0),
        state_digest: Digest("state".into()),
        input_digest: Digest("input".into()),
        context_digest: Digest("context".into()),
        frozen_now: TimestampMillis(101),
    };
    let outcome = AttemptOutcome::Uninterpretable {
        code: "ArchiveConflict".into(),
        isolation_id: RequestId("blocked".into()),
    };
    let guard = f.guard().await;
    let pause = f.storage.pause_after(
        "document.create",
        &f.layout
            .control_isolation(&f.owner.sid, &closure.operation_id)
            .unwrap(),
        1,
    );
    let pending = tokio::spawn({
        let f = f.clone();
        let attempt = attempt.clone();
        let outcome = outcome.clone();
        async move { f.store.isolate(guard, attempt, outcome).await }
    });
    pause.reached().await;
    f.store
        .record_closure_complete(f.guard().await, certificate.clone())
        .await
        .unwrap();
    pause.resume();
    assert!(matches!(
        pending.await.unwrap().unwrap(),
        ControlOutcome::RevisionConflict
    ));
    assert!(matches!(
        f.store
            .isolate(f.guard().await, attempt, outcome)
            .await
            .unwrap(),
        ControlOutcome::Applied(_)
    ));
    assert_eq!(
        f.store
            .get_isolation(&f.owner.sid, &closure.operation_id)
            .await
            .unwrap()
            .unwrap()
            .completion,
        Some(certificate)
    );
}

#[tokio::test]
async fn every_recovery_write_can_lose_its_response_without_refreshing_authorization() {
    use bcs_service_api::port::repo::distributed::common::StoreError;
    for lost in 1..=6 {
        let f = Fixture::new().await;
        let closure = f.isolate("first").await;
        let certificate = complete(&f, &closure).await;
        f.storage.lose_write(lost);
        let guard = f.guard().await;
        loop {
            match f
                .store
                .record_closure_complete(guard.clone(), certificate.clone())
                .await
            {
                Err(StoreError::OutcomeUnknown { .. }) => continue,
                Ok(ControlOutcome::Applied(_) | ControlOutcome::AlreadyApplied(_)) => break,
                other => panic!("{other:?}"),
            }
        }
        let authorization = RecoveryAuthorization {
            guard: f.guard().await,
            isolation_id: closure.operation_id.clone(),
            completion: certificate,
            request_id: RequestId("resume".into()),
        };
        loop {
            match f.store.begin_recovery(authorization.clone()).await {
                Err(StoreError::OutcomeUnknown { .. }) => continue,
                Ok(ControlOutcome::Applied(_) | ControlOutcome::AlreadyApplied(_)) => break,
                other => panic!("{other:?}"),
            }
        }
        let guard = f.guard().await;
        let receipt = loop {
            match f
                .control
                .reopen_gate(guard.clone(), RequestId("resume".into()))
                .await
            {
                Err(StoreError::OutcomeUnknown { .. }) => continue,
                Ok(GateReopenOutcome::Opened(r) | GateReopenOutcome::AlreadyOpened(r)) => break r,
                other => panic!("{other:?}"),
            }
        };
        loop {
            match f
                .store
                .confirm_recovery(guard.clone(), receipt.clone())
                .await
            {
                Err(StoreError::OutcomeUnknown { .. }) => continue,
                Ok(ControlOutcome::Applied(_) | ControlOutcome::AlreadyApplied(_)) => break,
                other => panic!("{other:?}"),
            }
        }
        assert_eq!(receipt.opened_generation, 2);
        assert!(matches!(
            f.store.begin_recovery(authorization).await.unwrap(),
            ControlOutcome::AlreadyApplied(_)
        ));
    }
}

#[tokio::test]
async fn close_and_cut_lost_responses_retry_the_original_operation() {
    use bcs_service_api::port::repo::distributed::common::StoreError;
    for lost in 1..=2 {
        let f = Fixture::new().await;
        f.admit("first").await;
        let attempt = AttemptIdentity {
            id: RequestId("failure".into()),
            head: CommitSeq(0),
            state_digest: Digest("state".into()),
            input_digest: Digest("input".into()),
            context_digest: Digest("context".into()),
            frozen_now: TimestampMillis(101),
        };
        f.store
            .isolate(
                f.guard().await,
                attempt,
                AttemptOutcome::Deterministic {
                    code: "failure".into(),
                    isolation_id: RequestId("isolate".into()),
                    safe_to_finalize: true,
                },
            )
            .await
            .unwrap();
        let guard = f.guard().await;
        f.storage.lose_write(lost);
        let closure = loop {
            match f
                .control
                .close_gate(guard.clone(), 1, RequestId("isolate".into()))
                .await
            {
                Err(StoreError::OutcomeUnknown { .. }) => continue,
                Ok(GateCloseOutcome::Closed(closure)) => break closure,
                other => panic!("{other:?}"),
            }
        };
        assert_eq!(closure.journal_cut, 1);
        assert_eq!(
            f.control.gate_state(&f.owner).await.unwrap().closure,
            Some(closure)
        );
    }
}

#[tokio::test]
async fn recovered_isolation_identity_cannot_be_reused_for_a_different_failure() {
    for via_finish in [false, true] {
        let f = Fixture::new().await;
        let closure = f.isolate("first").await;
        resume(&f, &closure, "resume").await;
        let prior = f
            .store
            .get_isolation(&f.owner.sid, &closure.operation_id)
            .await
            .unwrap()
            .unwrap();
        let attempt = AttemptIdentity {
            id: RequestId("different-attempt".into()),
            head: CommitSeq(0),
            state_digest: Digest("different".into()),
            input_digest: Digest("input".into()),
            context_digest: Digest("context".into()),
            frozen_now: TimestampMillis(102),
        };
        let outcome = AttemptOutcome::Deterministic {
            code: "DifferentFailure".into(),
            isolation_id: closure.operation_id.clone(),
            safe_to_finalize: true,
        };
        let result = if via_finish {
            f.store
                .begin_attempt(f.guard().await, attempt.clone())
                .await
                .unwrap();
            f.store
                .finish_attempt(f.guard().await, attempt.id, outcome)
                .await
                .unwrap()
        } else {
            f.store
                .isolate(f.guard().await, attempt, outcome)
                .await
                .unwrap()
        };
        assert!(matches!(result, ControlOutcome::ContentConflict));
        assert_eq!(
            f.store
                .get_isolation(&f.owner.sid, &closure.operation_id)
                .await
                .unwrap()
                .unwrap(),
            prior
        );
    }
}

#[tokio::test]
async fn pending_runner_context_survives_replaying_the_same_head_after_takeover() {
    use bcs_service_api::port::repo::distributed::{
        inbox::InboxSchedulingPort, session::SessionStorePort,
    };
    use bcs_store_api::document::DocumentStorePlugin;
    for fail in [false, true] {
        let f = Fixture::new().await;
        f.admit("first").await;
        let closure = f.isolate("first").await;
        f.terminal(&Tid(TID.into()), "first").await;
        resume(&f, &closure, "resume").await;
        let checkpoint = f
            .store
            .load(&f.owner.sid)
            .await
            .unwrap()
            .unwrap()
            .checkpoint;
        let effect = checkpoint.outbox.unwrap().effects[0].clone();
        let runner = AttemptIdentity {
            id: RequestId("pending-runner".into()),
            head: checkpoint.commit_seq,
            state_digest: Digest("state".into()),
            input_digest: Digest("input".into()),
            context_digest: Digest("context".into()),
            frozen_now: TimestampMillis(123),
        };
        f.store
            .begin_attempt(f.guard().await, runner.clone())
            .await
            .unwrap();
        let key = f.layout.ckpt(&f.owner.sid).unwrap();
        let mut raw = f.storage.read_document(&key).await.unwrap().unwrap();
        let mut successor = f.owner.clone();
        successor.owner.epoch = WorkerEpoch(2);
        successor.owner.instance.incarnation = "successor".into();
        raw.value["owner"] = serde_json::to_value(&successor.owner).unwrap();
        f.storage
            .replace_document(&key, raw.version, &raw.value)
            .await
            .unwrap();
        f.inbox.observe(&successor).await.unwrap();
        let replay = AttemptIdentity {
            id: RequestId("replay-before-runner".into()),
            head: checkpoint.commit_seq,
            state_digest: Digest("state".into()),
            input_digest: effect.content_digest.clone(),
            context_digest: Digest("effect".into()),
            frozen_now: TimestampMillis(456),
        };
        assert!(matches!(
            f.store
                .begin_replay_attempt(
                    f.guard().await,
                    replay.clone(),
                    effect.id,
                    effect.content_digest.clone()
                )
                .await
                .unwrap(),
            ControlOutcome::Applied(_)
        ));
        let outcome = if fail {
            AttemptOutcome::Uninterpretable {
                code: "EffectConflict".into(),
                isolation_id: RequestId("blocked".into()),
            }
        } else {
            AttemptOutcome::Succeeded {
                decision_digest: effect.content_digest,
            }
        };
        f.store
            .finish_attempt(f.guard().await, replay.id.clone(), outcome.clone())
            .await
            .unwrap();
        let snapshot = f.store.load_control(&f.owner.sid).await.unwrap().unwrap();
        if fail {
            assert!(matches!(snapshot.control.mode, ControlMode::Blocked { .. }));
        } else {
            assert_eq!(
                snapshot.control.active_attempt.as_ref().unwrap().identity,
                runner
            );
            assert!(
                snapshot
                    .control
                    .active_attempt
                    .as_ref()
                    .unwrap()
                    .outcome
                    .is_none()
            );
        }
        assert_eq!(
            f.store
                .get_attempt(&f.owner.sid, &runner.id)
                .await
                .unwrap()
                .unwrap()
                .identity,
            runner
        );
        assert_eq!(
            f.store
                .get_attempt(&f.owner.sid, &replay.id)
                .await
                .unwrap()
                .unwrap()
                .outcome,
            Some(outcome)
        );
    }
}

#[tokio::test]
async fn lost_transient_replay_finish_restores_runner_and_exposes_its_confirmed_outcome() {
    use bcs_service_api::port::repo::distributed::session::SessionStorePort;
    let f = Fixture::new().await;
    f.admit("first").await;
    let closure = f.isolate("first").await;
    f.terminal(&Tid(TID.into()), "first").await;
    resume(&f, &closure, "resume").await;
    let checkpoint = f
        .store
        .load(&f.owner.sid)
        .await
        .unwrap()
        .unwrap()
        .checkpoint;
    let effect = checkpoint.outbox.unwrap().effects[0].clone();
    let runner = AttemptIdentity {
        id: RequestId("pending".into()),
        head: checkpoint.commit_seq,
        state_digest: Digest("state".into()),
        input_digest: Digest("input".into()),
        context_digest: Digest("context".into()),
        frozen_now: TimestampMillis(123),
    };
    f.store
        .begin_attempt(f.guard().await, runner.clone())
        .await
        .unwrap();
    let mut replay = runner.clone();
    replay.id = RequestId("transient-replay".into());
    f.store
        .begin_replay_attempt(
            f.guard().await,
            replay.clone(),
            effect.id.clone(),
            effect.content_digest.clone(),
        )
        .await
        .unwrap();
    let guard = f.guard().await;
    let transient = AttemptOutcome::Transient {
        code: "temporary".into(),
    };
    f.storage.lose_write(1);
    assert!(matches!(
        f.store
            .finish_attempt(guard.clone(), replay.id.clone(), transient.clone())
            .await,
        Err(bcs_service_api::port::repo::distributed::common::StoreError::OutcomeUnknown { .. })
    ));
    let restarted = bcs_dist_store::coordination::CoordinationStore::new(
        f.storage.clone(),
        std::sync::Arc::new(bcs_test_support::store_model::Model::default()),
        f.storage.clone(),
        f.layout.clone(),
    )
    .unwrap();
    assert_eq!(
        restarted
            .get_attempt(&f.owner.sid, &replay.id)
            .await
            .unwrap()
            .unwrap()
            .outcome,
        Some(transient.clone())
    );
    assert_eq!(
        restarted
            .load_control(&f.owner.sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .active_attempt
            .unwrap()
            .identity,
        runner
    );
    assert!(matches!(
        restarted
            .finish_attempt(guard, replay.id.clone(), transient.clone())
            .await
            .unwrap(),
        ControlOutcome::AlreadyApplied(_)
    ));
    let mut next = replay.clone();
    next.id = RequestId("next-replay".into());
    restarted
        .begin_replay_attempt(
            f.guard().await,
            next.clone(),
            effect.id,
            effect.content_digest.clone(),
        )
        .await
        .unwrap();
    restarted
        .finish_attempt(
            f.guard().await,
            next.id,
            AttemptOutcome::Succeeded {
                decision_digest: effect.content_digest,
            },
        )
        .await
        .unwrap();
    assert_eq!(
        restarted
            .get_attempt(&f.owner.sid, &replay.id)
            .await
            .unwrap()
            .unwrap()
            .outcome,
        Some(transient)
    );
}

#[tokio::test]
async fn succeeded_runner_keeps_frozen_time_while_same_head_outbox_replays_before_commit() {
    use bcs_service_api::port::repo::distributed::session::SessionStorePort;
    let f = Fixture::new().await;
    f.admit("first").await;
    let closure = f.isolate("first").await;
    f.terminal(&Tid(TID.into()), "first").await;
    resume(&f, &closure, "resume").await;
    let checkpoint = f
        .store
        .load(&f.owner.sid)
        .await
        .unwrap()
        .unwrap()
        .checkpoint;
    let effect = checkpoint.outbox.unwrap().effects[0].clone();
    let runner = AttemptIdentity {
        id: RequestId("succeeded-runner".into()),
        head: checkpoint.commit_seq,
        state_digest: Digest("state".into()),
        input_digest: Digest("input".into()),
        context_digest: Digest("context".into()),
        frozen_now: TimestampMillis(123),
    };
    let success = AttemptOutcome::Succeeded {
        decision_digest: Digest("candidate".into()),
    };
    f.store
        .begin_attempt(f.guard().await, runner.clone())
        .await
        .unwrap();
    f.store
        .finish_attempt(f.guard().await, runner.id.clone(), success.clone())
        .await
        .unwrap();
    let mut replay = runner.clone();
    replay.id = RequestId("old-batch".into());
    replay.frozen_now = TimestampMillis(999);
    f.store
        .begin_replay_attempt(
            f.guard().await,
            replay.clone(),
            effect.id,
            effect.content_digest.clone(),
        )
        .await
        .unwrap();
    f.store
        .finish_attempt(
            f.guard().await,
            replay.id,
            AttemptOutcome::Succeeded {
                decision_digest: effect.content_digest,
            },
        )
        .await
        .unwrap();
    let restored = f
        .store
        .load_control(&f.owner.sid)
        .await
        .unwrap()
        .unwrap()
        .control
        .active_attempt
        .unwrap();
    assert_eq!(restored.identity, runner);
    assert_eq!(restored.outcome, Some(success));
}

#[tokio::test]
async fn already_opened_retry_is_fenced_if_owner_changes_after_authorization_read() {
    use bcs_service_api::port::repo::distributed::inbox::InboxSchedulingPort;
    use bcs_store_api::document::DocumentStorePlugin;
    let f = std::sync::Arc::new(Fixture::new().await);
    let closure = f.isolate("first").await;
    let certificate = complete(&f, &closure).await;
    f.store
        .record_closure_complete(f.guard().await, certificate.clone())
        .await
        .unwrap();
    f.store
        .begin_recovery(RecoveryAuthorization {
            guard: f.guard().await,
            isolation_id: closure.operation_id,
            completion: certificate,
            request_id: RequestId("resume".into()),
        })
        .await
        .unwrap();
    let guard = f.guard().await;
    f.control
        .reopen_gate(guard.clone(), RequestId("resume".into()))
        .await
        .unwrap();
    let key = f.layout.ckpt(&f.owner.sid).unwrap();
    let pause = f.storage.pause_after("document.read", &key, 1);
    let stale = tokio::spawn({
        let f = f.clone();
        async move {
            f.control
                .reopen_gate(guard, RequestId("resume".into()))
                .await
        }
    });
    pause.reached().await;
    let mut raw = f.storage.read_document(&key).await.unwrap().unwrap();
    let mut successor = f.owner.clone();
    successor.owner.epoch = WorkerEpoch(2);
    successor.owner.instance.incarnation = "two".into();
    raw.value["owner"] = serde_json::to_value(&successor.owner).unwrap();
    f.storage
        .replace_document(&key, raw.version, &raw.value)
        .await
        .unwrap();
    f.inbox.observe(&successor).await.unwrap();
    pause.resume();
    assert!(matches!(
        stale.await.unwrap().unwrap(),
        GateReopenOutcome::Fenced
    ));
}
