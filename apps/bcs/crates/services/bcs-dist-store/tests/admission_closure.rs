#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "closure protocol tests"
)]
mod common;
mod control_support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{admission::*, control::*, inbox::*};
use common::*;
use control_support::*;
#[tokio::test]
async fn closure_cannot_complete_before_actual_archive_and_terminal_publication_then_reopens_once()
{
    let f = Fixture::new().await;
    assert!(matches!(f.admit("first").await, AdmitOutcome::Accepted(_)));
    let closure = f.isolate("isolate").await;
    // The MVP has no shard-wide admission journal to enumerate.  A closure
    // therefore starts with no admission obligations and may complete from the
    // session checkpoint alone.
    let mut certificate = None;
    for _ in 0..16 {
        let work = f.control.advance_closure(&f.owner, &closure).await.unwrap();
        if work.certificate.is_some() {
            certificate = work.certificate;
            break;
        }
    }
    let certificate = certificate.unwrap();
    assert_eq!(certificate.completed_through, closure.journal_cut);
    f.store
        .record_closure_complete(f.guard().await, certificate.clone())
        .await
        .unwrap();
    let authorization = RecoveryAuthorization {
        guard: f.guard().await,
        isolation_id: RequestId("isolate".into()),
        completion: certificate,
        request_id: RequestId("resume".into()),
    };
    assert!(matches!(
        f.store.begin_recovery(authorization.clone()).await.unwrap(),
        ControlOutcome::Applied(_)
    ));
    let guard = f.guard().await;
    let GateReopenOutcome::Opened(receipt) = f
        .control
        .reopen_gate(guard.clone(), RequestId("resume".into()))
        .await
        .unwrap()
    else {
        panic!()
    };
    assert_eq!(receipt.opened_generation, 2);
    assert!(matches!(
        f.control
            .reopen_gate(guard.clone(), RequestId("resume".into()))
            .await
            .unwrap(),
        GateReopenOutcome::AlreadyOpened(_)
    ));
    assert!(matches!(
        f.store
            .confirm_recovery(guard, receipt.clone())
            .await
            .unwrap(),
        ControlOutcome::Applied(_)
    ));
    assert!(matches!(
        f.store
            .load_control(&f.owner.sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .mode,
        ControlMode::Normal
    ));
    assert!(matches!(
        f.store.begin_recovery(authorization.clone()).await.unwrap(),
        ControlOutcome::AlreadyApplied(_)
    ));
    let mut changed = authorization;
    changed.completion.evidence_digest = Digest("changed".into());
    assert!(matches!(
        f.store.begin_recovery(changed).await.unwrap(),
        ControlOutcome::ContentConflict
    ));
    assert!(matches!(
        f.store
            .confirm_recovery(
                ControlGuard {
                    owner: f.owner.clone(),
                    expected_head: receipt.head,
                    expected_revision: receipt.control_revision
                },
                receipt
            )
            .await
            .unwrap(),
        ControlOutcome::AlreadyApplied(_)
    ));
}
#[tokio::test]
async fn unauthorized_close_and_fabricated_completion_are_rejected() {
    let f = Fixture::new().await;
    assert_eq!(
        f.control
            .close_gate(f.guard().await, 1, RequestId("fake".into()))
            .await
            .unwrap(),
        GateCloseOutcome::Conflict
    );
    let closure = f.isolate("isolate").await;
    let certificate = ClosureCertificate {
        completed_through: closure.journal_cut,
        closure,
        terminal_tasks: 0,
        evidence_digest: Digest("forged".into()),
    };
    assert!(matches!(
        f.store
            .record_closure_complete(f.guard().await, certificate)
            .await
            .unwrap(),
        ControlOutcome::NotReady
    ));
}

#[tokio::test]
async fn a_new_isolation_fences_an_old_recovery_and_closes_the_actual_open_generation() {
    let f = Fixture::new().await;
    let closure = f.isolate("first").await;
    let mut certificate = None;
    for _ in 0..4 {
        certificate = f
            .control
            .advance_closure(&f.owner, &closure)
            .await
            .unwrap()
            .certificate;
        if certificate.is_some() {
            break;
        }
    }
    f.store
        .record_closure_complete(f.guard().await, certificate.clone().unwrap())
        .await
        .unwrap();
    f.store
        .begin_recovery(RecoveryAuthorization {
            guard: f.guard().await,
            isolation_id: RequestId("first".into()),
            completion: certificate.unwrap(),
            request_id: RequestId("resume-first".into()),
        })
        .await
        .unwrap();
    let stale_guard = f.guard().await;
    let GateReopenOutcome::Opened(receipt) = f
        .control
        .reopen_gate(stale_guard.clone(), RequestId("resume-first".into()))
        .await
        .unwrap()
    else {
        panic!()
    };
    assert!(matches!(
        f.admit("during-recovery").await,
        AdmitOutcome::Accepted(_)
    ));
    let failure = AttemptIdentity {
        id: RequestId("new-failure".into()),
        head: stale_guard.expected_head,
        state_digest: Digest("state".into()),
        input_digest: Digest("input".into()),
        context_digest: Digest("context".into()),
        frozen_now: TimestampMillis(101),
    };
    assert!(matches!(
        f.store
            .isolate(
                f.guard().await,
                failure,
                AttemptOutcome::Deterministic {
                    code: "NecessaryEffectConflict".into(),
                    isolation_id: RequestId("second".into()),
                    safe_to_finalize: true
                }
            )
            .await
            .unwrap(),
        ControlOutcome::Applied(_)
    ));
    assert!(matches!(
        f.store
            .confirm_recovery(stale_guard.clone(), receipt)
            .await
            .unwrap(),
        ControlOutcome::NotReady
    ));
    assert!(matches!(
        f.control
            .reopen_gate(stale_guard, RequestId("resume-first".into()))
            .await
            .unwrap(),
        GateReopenOutcome::NotReady
    ));
    let generation = f.control.gate_state(&f.owner).await.unwrap().generation;
    assert_eq!(generation, 2);
    let GateCloseOutcome::Closed(second) = f
        .control
        .close_gate(f.guard().await, generation, RequestId("second".into()))
        .await
        .unwrap()
    else {
        panic!()
    };
    assert_eq!(second.journal_cut, closure.journal_cut);
    assert_eq!(second.generation, 2);
    let page = f
        .control
        .closure_obligations(
            &second,
            bcs_service_api::port::repo::distributed::common::PageRequest {
                after: None,
                limit: std::num::NonZeroU32::new(128).unwrap(),
            },
        )
        .await
        .unwrap();
    assert!(page.items.is_empty());
}

#[tokio::test]
async fn closure_rechecks_tasks_added_by_independent_finalization_after_scan_started() {
    use bcs_service_api::port::repo::distributed::session::*;
    use bcs_store_api::document::DocumentStorePlugin;
    let f = Fixture::new().await;
    let closure = f.isolate("first").await;
    assert!(
        f.control
            .advance_closure(&f.owner, &closure)
            .await
            .unwrap()
            .certificate
            .is_none()
    );
    let metadata = task();
    f.storage
        .create_document(
            &f.layout.task(&metadata.tid).unwrap(),
            &serde_json::to_value(&metadata).unwrap(),
        )
        .await
        .unwrap();
    let key = f.layout.ckpt(&f.owner.sid).unwrap();
    let mut raw = f.storage.read_document(&key).await.unwrap().unwrap();
    raw.value["checkpoint"]["tasks"] = serde_json::to_value(vec![TaskProgress {
        steps: 0,
        tid: metadata.tid,
        phase: TaskPhase::Done,
        input: Payload::Inline(serde_json::Value::Null),
        result_ref: None,
        output_limits: metadata.output_limits,
        output_usage: metadata.output_usage,
        publication: SnapshotProgress::default(),
        finalization: None,
    }])
    .unwrap();
    f.storage
        .replace_document(&key, raw.version, &raw.value)
        .await
        .unwrap();
    for _ in 0..3 {
        assert!(
            f.control
                .advance_closure(&f.owner, &closure)
                .await
                .unwrap()
                .certificate
                .is_none()
        );
    }
}

#[tokio::test]
async fn corrupt_progress_cursor_cannot_manufacture_a_completion_certificate() {
    use bcs_store_api::document::DocumentStorePlugin;
    let f = Fixture::new().await;
    let closure = f.isolate("first").await;
    f.control.advance_closure(&f.owner, &closure).await.unwrap();
    let key = f
        .layout
        .closure_progress(&f.owner.sid, &closure.operation_id)
        .unwrap();
    let mut raw = f.storage.read_document(&key).await.unwrap().unwrap();
    raw.value["cursor"] = (closure.journal_cut + 1).into();
    f.storage
        .replace_document(&key, raw.version, &raw.value)
        .await
        .unwrap();
    assert!(f.control.advance_closure(&f.owner, &closure).await.is_err());
}

#[tokio::test]
async fn isolation_commit_cannot_rewrite_a_frozen_terminal_result() {
    use bcs_service_api::port::repo::distributed::session::*;
    let f = Fixture::new().await;
    f.admit("first").await;
    f.isolate("isolate").await;
    f.terminal(&Tid(TID.into()), "isolate").await;
    let mut checkpoint = f
        .store
        .load(&f.owner.sid)
        .await
        .unwrap()
        .unwrap()
        .checkpoint;
    let previous = checkpoint.commit_seq;
    checkpoint.commit_seq = CommitSeq(previous.0 + 1);
    checkpoint
        .tasks
        .front_mut()
        .unwrap()
        .finalization
        .as_mut()
        .unwrap()
        .record
        .result = Payload::Inline(serde_json::json!({"rewritten":true}));
    let id = CommitId("rewrite".into());
    let digest = Digest("rewrite".into());
    checkpoint.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: id.clone(),
        source_commit: checkpoint.commit_seq,
        content_digest: digest.clone(),
        effects: vec![],
    });
    assert!(
        f.store
            .commit(CommitRequest {
                purpose: CommitPurpose::Isolation {
                    operation_id: RequestId("isolate".into())
                },
                owner: f.owner.clone(),
                expected_commit_seq: previous,
                commit_id: id,
                content_digest: digest,
                candidate: checkpoint
            })
            .await
            .is_err()
    );
}

#[tokio::test]
async fn creator_paused_before_initial_task_is_completed_after_closure_and_resume() {
    use bcs_service_api::port::repo::distributed::task_feed::TaskReaderPort;
    let f = std::sync::Arc::new(Fixture::new().await);
    let pause = f.storage.pause_after(
        "before.document.create",
        &f.layout.task(&Tid(TID.into())).unwrap(),
        1,
    );
    let creator = tokio::spawn({
        let f = f.clone();
        async move { f.admit("paused").await }
    });
    pause.reached().await;
    let closure = f.isolate("isolate").await;
    // The admission document is not guaranteed to exist until the caller's
    // admission CAS succeeds, so closure does not manufacture a journal
    // obligation for this paused pre-create request.
    for _ in 0..4 {
        assert!(f.control.advance_closure(&f.owner, &closure).await.unwrap().obligation.is_none());
    }
    pause.resume();
    let result = creator.await.unwrap();
    assert!(matches!(result, AdmitOutcome::Accepted(_) | AdmitOutcome::Quarantined));
    let task = f
        .feed
        .load_snapshot(&scope(), &Tid(TID.into()))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(task.content.status, TaskStatus::Submitted);
    assert!(task.content.result_ref.is_none());
}

#[tokio::test]
async fn delayed_ordinary_commit_cannot_cross_an_isolation_recovery_cycle_at_the_same_head() {
    use bcs_service_api::port::repo::distributed::session::*;
    let f = Fixture::new().await;
    let mut checkpoint = f
        .store
        .load(&f.owner.sid)
        .await
        .unwrap()
        .unwrap()
        .checkpoint;
    checkpoint.commit_seq = CommitSeq(1);
    let id = CommitId("old-ordinary".into());
    let digest = Digest("old".into());
    checkpoint.outbox = Some(CommittedBatch {
        created_at: None,
        commit_id: id.clone(),
        source_commit: CommitSeq(1),
        content_digest: digest.clone(),
        effects: vec![],
    });
    let request = CommitRequest {
        purpose: CommitPurpose::Ordinary {
            control_revision: 0,
        },
        owner: f.owner.clone(),
        expected_commit_seq: CommitSeq(0),
        commit_id: id,
        content_digest: digest,
        candidate: checkpoint,
    };
    let closure = f.isolate("isolate").await;
    resume(&f, &closure, "resume").await;
    assert!(
        matches!(f.store.commit(request).await,Err(bcs_service_api::port::repo::distributed::common::StoreError::NotApplied { code }) if code=="session_control_paused")
    );
}

#[tokio::test]
async fn recovering_owner_change_requires_fresh_authorization_and_then_can_resume() {
    use bcs_store_api::document::DocumentStorePlugin;
    for opened in [false, true] {
        let f = Fixture::new().await;
        let closure = f.isolate("first").await;
        let certificate = complete(&f, &closure).await;
        f.store
            .record_closure_complete(f.guard().await, certificate.clone())
            .await
            .unwrap();
        f.store
            .begin_recovery(RecoveryAuthorization {
                guard: f.guard().await,
                isolation_id: closure.operation_id.clone(),
                completion: certificate.clone(),
                request_id: RequestId("old".into()),
            })
            .await
            .unwrap();
        let stale = f.guard().await;
        if opened {
            assert!(matches!(
                f.control
                    .reopen_gate(stale.clone(), RequestId("old".into()))
                    .await
                    .unwrap(),
                GateReopenOutcome::Opened(_)
            ));
        }
        // Simulate the independently tested exact-owner seal; observe installs the
        // new scheduling owner before the new application-authorized request.
        let mut owner = f.owner.clone();
        owner.owner.epoch = WorkerEpoch(2);
        owner.owner.instance.incarnation = "two".into();
        let key = f.layout.ckpt(&owner.sid).unwrap();
        let mut raw = f.storage.read_document(&key).await.unwrap().unwrap();
        raw.value["owner"] = serde_json::to_value(&owner.owner).unwrap();
        f.storage
            .replace_document(&key, raw.version, &raw.value)
            .await
            .unwrap();
        f.inbox.observe(&owner).await.unwrap();
        assert!(matches!(
            f.control
                .reopen_gate(stale, RequestId("old".into()))
                .await
                .unwrap(),
            GateReopenOutcome::Fenced
        ));
        let guard = f.guard().await;
        assert!(matches!(
            f.store
                .begin_recovery(RecoveryAuthorization {
                    guard,
                    isolation_id: closure.operation_id,
                    completion: certificate,
                    request_id: RequestId("new".into())
                })
                .await
                .unwrap(),
            ControlOutcome::Applied(_)
        ));
        let guard = f.guard().await;
        let GateReopenOutcome::Opened(receipt) = f
            .control
            .reopen_gate(guard.clone(), RequestId("new".into()))
            .await
            .unwrap()
        else {
            panic!()
        };
        assert_eq!(receipt.opened_generation, 2);
        assert!(matches!(
            f.store.confirm_recovery(guard, receipt).await.unwrap(),
            ControlOutcome::Applied(_)
        ));
    }
}

#[tokio::test]
async fn old_successful_recovery_is_not_reported_as_open_after_new_isolation() {
    let f = Fixture::new().await;
    let closure = f.isolate("first").await;
    let receipt = resume(&f, &closure, "old").await;
    let second = f.isolate("second").await;
    assert!(matches!(
        f.control
            .reopen_gate(
                ControlGuard {
                    owner: f.owner.clone(),
                    expected_head: receipt.head,
                    expected_revision: receipt.control_revision
                },
                RequestId("old".into())
            )
            .await
            .unwrap(),
        GateReopenOutcome::NotReady
    ));
    assert_eq!(
        f.control.gate_state(&f.owner).await.unwrap().closure,
        Some(second)
    );
}

#[tokio::test]
async fn quarantined_replay_attempt_is_bound_to_the_actual_committed_effect() {
    use bcs_service_api::port::repo::distributed::session::SessionStorePort;
    let f = Fixture::new().await;
    f.admit("first").await;
    f.isolate("isolate").await;
    f.terminal(&Tid(TID.into()), "isolate").await;
    let checkpoint = f
        .store
        .load(&f.owner.sid)
        .await
        .unwrap()
        .unwrap()
        .checkpoint;
    let effect = checkpoint.outbox.as_ref().unwrap().effects[0].clone();
    let attempt = AttemptIdentity {
        id: RequestId("replay".into()),
        head: checkpoint.commit_seq,
        state_digest: Digest("state".into()),
        input_digest: effect.content_digest.clone(),
        context_digest: Digest("context".into()),
        frozen_now: TimestampMillis(101),
    };
    assert!(
        f.store
            .begin_replay_attempt(
                f.guard().await,
                attempt.clone(),
                effect.id.clone(),
                Digest("wrong".into())
            )
            .await
            .is_err()
    );
    assert!(matches!(
        f.store
            .begin_replay_attempt(
                f.guard().await,
                attempt.clone(),
                effect.id.clone(),
                effect.content_digest.clone()
            )
            .await
            .unwrap(),
        ControlOutcome::Applied(_)
    ));
    f.store
        .finish_attempt(
            f.guard().await,
            attempt.id,
            AttemptOutcome::Succeeded {
                decision_digest: effect.content_digest,
            },
        )
        .await
        .unwrap();
    assert!(matches!(
        f.store
            .load_control(&f.owner.sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .mode,
        ControlMode::Quarantined { .. }
    ));
}

#[tokio::test]
async fn upgrading_isolation_preserves_the_previous_close_cut_and_failure_evidence() {
    let f = Fixture::new().await;
    let closure = f.isolate("first").await;
    let attempt = AttemptIdentity {
        id: RequestId("archive-conflict".into()),
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
            AttemptOutcome::Uninterpretable {
                code: "ArchiveConflict".into(),
                isolation_id: RequestId("blocked".into()),
            },
        )
        .await
        .unwrap();
    let prior = f
        .store
        .get_isolation(&f.owner.sid, &closure.operation_id)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(prior.closure, Some(closure));
    assert_eq!(prior.failed_attempt, RequestId("attempt-first".into()));
    assert!(matches!(
        f.store
            .load_control(&f.owner.sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .mode,
        ControlMode::Blocked { .. }
    ));
}
