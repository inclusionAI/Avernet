#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "real-provider isolation assertions"
)]
mod processor_support;
mod support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{control::*, result::*};
use processor_support::*;
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};

#[tokio::test]
async fn first_invalid_decision_closes_admission_and_finishes_every_accepted_task_without_rerunning()
 {
    let runner = Arc::new(InvalidRunner {
        calls: AtomicUsize::new(0),
        corrupt: false,
    });
    let f = Fixture::new(runner.clone()).await;
    let first = f.admit("bad").await;
    let second = f.admit("unconsumed").await;
    f.warm().await;
    for _ in 0..120 {
        let _ = f.step().await;
        tokio::time::sleep(Duration::from_millis(5)).await;
        if let Some(control) = f.coordination.load_control(&spec().sid).await.unwrap()
            && matches!(control.control.mode, ControlMode::Quarantined { ref isolation } if isolation.completion.is_some())
        {
            break;
        }
    }
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(control.control.mode,ControlMode::Quarantined { ref isolation } if isolation.completion.is_some()),
        "{control:?}"
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    for tid in [&first, &second] {
        assert_eq!(f.snapshot(tid).await.content.status, TaskStatus::Rejected);
        assert!(
            f.archive
                .get_final(&spec().scope, tid)
                .await
                .unwrap()
                .is_some()
        );
    }
    assert_eq!(f.checkpoint().await.tasks.len(), 0);
    use bcs_service_api::port::dist_runtime::DistWorkerIsolationControlPort;
    let ControlMode::Quarantined { isolation } = control.control.mode else {
        panic!()
    };
    let request = RecoveryAuthorization {
        guard: ControlGuard {
            owner: control.owner,
            expected_head: control.head,
            expected_revision: control.control.revision,
        },
        isolation_id: isolation.operation_id,
        completion: isolation.completion.unwrap(),
        request_id: RequestId("authorized-resume".into()),
    };
    let mut wrong = spec().scope;
    wrong.tenant = TenantId("other".into());
    assert_eq!(
        f.processor
            .resume(&wrong, request.clone())
            .await
            .unwrap_err()
            .code,
        "session_control_scope"
    );
    assert!(
        matches!(f.processor.resume(&spec().scope,request.clone()).await.unwrap(),ControlOutcome::Applied(ref c) if matches!(c.control.mode,ControlMode::Normal))
    );
    assert!(
        matches!(f.processor.resume(&spec().scope,request).await.unwrap(),ControlOutcome::AlreadyApplied(ref c) if matches!(c.control.mode,ControlMode::Normal))
    );
}
#[tokio::test]
async fn unsupported_runner_state_is_blocked_without_fabricating_task_failure() {
    let runner = Arc::new(InvalidRunner {
        calls: AtomicUsize::new(0),
        corrupt: true,
    });
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("blocked").await;
    f.warm().await;
    for _ in 0..45 {
        let _ = f.step().await;
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(control.control.mode, ControlMode::Blocked { .. }),
        "{control:?}"
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Submitted);
    assert!(
        f.archive
            .get_final(&spec().scope, &tid)
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn undecodable_checkpoint_is_blocked_by_successor_without_replacing_business_body() {
    use bcs_store_api::document::DocumentStorePlugin;
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::new(runner.clone()).await;
    f.admit("corrupt").await;
    f.warm().await;
    let key = f.layout.ckpt(&spec().sid).unwrap();
    let mut raw = f.model.read_document(&key).await.unwrap().unwrap();
    raw.value["checkpoint"]["runner_state"] = serde_json::json!({"invalid":"shape"});
    let body = raw.value["checkpoint"].clone();
    f.model
        .replace_document(&key, raw.version, &raw.value)
        .await
        .unwrap();
    f.model.advance(Duration::from_secs(10));
    let (replacement, processor) = f.replacement(runner.clone()).await;
    for _ in 0..4 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        let _ = replacement.tick().await;
    }
    for _ in 0..12 {
        let _ = processor.tick().await;
        let _ = replacement.tick().await;
    }
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(control.control.mode,ControlMode::Blocked { ref isolation } if isolation.closure.is_some()),
        "{control:?}"
    );
    assert_eq!(
        f.model.read_document(&key).await.unwrap().unwrap().value["checkpoint"],
        body
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn lost_failure_reply_and_takeover_keep_the_same_isolation_and_do_not_rerun_runner() {
    let runner = Arc::new(InvalidRunner {
        calls: AtomicUsize::new(0),
        corrupt: false,
    });
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("lost-failure").await;
    f.warm().await;
    for _ in 0..25 {
        if f.step().await.is_err() && runner.calls.load(Ordering::SeqCst) == 1 {
            break;
        }
    }
    tokio::time::sleep(Duration::from_millis(210)).await;
    f.model.lose_next_response();
    assert_eq!(
        f.processor.tick().await.unwrap_err().code,
        "store_outcome_unknown"
    );
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    let ControlMode::Quarantined {
        isolation: original,
    } = control.control.mode
    else {
        panic!("failure was not persisted")
    };
    f.model.advance(Duration::from_secs(10));
    let (runtime, processor) = f.replacement(runner.clone()).await;
    for _ in 0..4 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        runtime.tick().await.unwrap();
    }
    for _ in 0..100 {
        runtime.tick().await.unwrap();
        processor.tick().await.unwrap();
        let control = f
            .coordination
            .load_control(&spec().sid)
            .await
            .unwrap()
            .unwrap();
        if matches!(control.control.mode,ControlMode::Quarantined {ref isolation} if isolation.completion.is_some())
        {
            break;
        }
    }
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(control.control.mode,ControlMode::Quarantined {ref isolation} if isolation.operation_id==original.operation_id && isolation.completion.is_some()),
        "{control:?}"
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Rejected);
}

#[tokio::test]
async fn isolation_fences_an_already_prepared_ordinary_candidate_and_closes_it_independently() {
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("prepared").await;
    f.warm().await;
    for _ in 0..25 {
        f.step().await.unwrap();
        if runner.calls.load(Ordering::SeqCst) == 1 {
            break;
        }
    }
    let c = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    let guard = ControlGuard {
        owner: c.owner.clone(),
        expected_head: c.head,
        expected_revision: c.control.revision,
    };
    let attempt = AttemptIdentity {
        id: RequestId("external-failure".into()),
        head: c.head,
        state_digest: c.checkpoint_digest,
        input_digest: Digest("i".into()),
        context_digest: Digest("c".into()),
        frozen_now: TimestampMillis(1000),
    };
    f.coordination
        .isolate(
            guard,
            attempt,
            AttemptOutcome::Deterministic {
                code: "diagnostic".into(),
                isolation_id: RequestId("isolation".into()),
                safe_to_finalize: true,
            },
        )
        .await
        .unwrap();
    for _ in 0..80 {
        let _ = f.step().await;
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Rejected);
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn a_registered_deadline_occurrence_cannot_be_rearmed_by_returning_its_same_identity() {
    let runner = Arc::new(DeadlineRunner(AtomicUsize::new(0)));
    let f = Fixture::new(runner.clone()).await;
    f.admit("deadline").await;
    f.warm().await;
    for _ in 0..45 {
        f.step().await.unwrap();
    }
    assert_eq!(
        runner.0.load(Ordering::SeqCst),
        2,
        "one Start plus one timeout occurrence"
    );
}

#[tokio::test]
async fn changed_business_body_at_the_same_head_is_blocked_without_overwriting_evidence() {
    use bcs_store_api::document::DocumentStorePlugin;
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("body-change").await;
    f.warm().await;
    for _ in 0..10 {
        f.step().await.unwrap();
        if f.checkpoint().await.commit_seq == CommitSeq(1) {
            break;
        }
    }
    let key = f.layout.ckpt(&spec().sid).unwrap();
    let mut raw = f.model.read_document(&key).await.unwrap().unwrap();
    raw.value["checkpoint"]["runner_state"] =
        serde_json::json!({"tid":tid,"state":{"format_version":1,"value":"damaged state"}});
    let body = raw.value["checkpoint"].clone();
    f.model
        .replace_document(&key, raw.version, &raw.value)
        .await
        .unwrap();
    for _ in 0..12 {
        let _ = f.step().await;
    }
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(control.control.mode,ControlMode::Blocked {ref isolation} if isolation.closure.is_some()),
        "{control:?}"
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        f.model.read_document(&key).await.unwrap().unwrap().value["checkpoint"],
        body
    );
}

#[tokio::test]
async fn successful_effect_history_does_not_freeze_the_clock_for_a_future_deadline() {
    let runner = Arc::new(FutureDeadlineRunner(AtomicUsize::new(0)));
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("future").await;
    f.warm().await;
    for _ in 0..18 {
        f.step().await.unwrap();
    }
    assert_eq!(runner.0.load(Ordering::SeqCst), 1);
    f.clock.0.store(3000, Ordering::SeqCst);
    // Released Sessions re-enter after the bounded local fairness cooldown.
    tokio::time::sleep(Duration::from_millis(550)).await;
    for _ in 0..55 {
        f.step().await.unwrap();
        if f.snapshot(&tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(runner.0.load(Ordering::SeqCst), 2);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
}
#[tokio::test]
async fn takeover_of_a_pending_replay_after_transient_failure_keeps_the_episode_identity() {
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("transient-replay").await;
    f.warm().await;
    f.publisher.lose_append.store(true, Ordering::SeqCst);
    for _ in 0..40 {
        if f.step().await.is_err() {
            break;
        }
    }
    let prior = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap()
        .control
        .active_attempt
        .unwrap();
    assert!(
        matches!(prior.outcome, Some(AttemptOutcome::Transient { .. })),
        "{prior:?}"
    );
    // Confirmed replay now yields its local slot, including the runtime's
    // 500 ms reacquisition cooldown, before starting the next replay episode.
    tokio::time::sleep(Duration::from_millis(550)).await;
    f.step().await.unwrap();
    let pending = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap()
        .control
        .active_attempt
        .unwrap();
    assert!(pending.outcome.is_none());
    assert_ne!(pending.identity.id, prior.identity.id);
    f.model.advance(Duration::from_secs(10));
    let (runtime, processor) = f.replacement(runner.clone()).await;
    for _ in 0..4 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        runtime.tick().await.unwrap();
    }
    for _ in 0..65 {
        runtime.tick().await.unwrap();
        processor.tick().await.unwrap();
        if f.snapshot(&tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    let attempt = f
        .coordination
        .get_attempt(&spec().sid, &pending.identity.id)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        attempt.outcome,
        Some(AttemptOutcome::Succeeded { .. })
    ));
    assert_eq!(attempt.identity, pending.identity);
}

#[tokio::test]
async fn isolation_discards_a_frozen_decision_waiting_on_a_ref_before_authorized_resume() {
    use bcs_service_api::port::dist_runtime::DistWorkerIsolationControlPort;
    let bytes = serde_json::to_vec(&serde_json::json!({"done":true})).unwrap();
    let reference = bcs_dist_encoding::blob_reference("test-result", &bytes);
    let runner = Arc::new(RefRunner {
        calls: AtomicUsize::new(0),
        result: reference.clone(),
    });
    let f = Fixture::new(runner.clone()).await;
    f.archive.put_immutable(reference, bytes).await.unwrap();
    let tid = f.admit("frozen-ref").await;
    f.warm().await;
    f.blobs.fail_read.store(true, Ordering::SeqCst);
    for _ in 0..30 {
        if f.step().await.is_err() {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    quarantine(&f, "discard-frozen").await;
    tokio::time::sleep(Duration::from_millis(210)).await;
    for _ in 0..100 {
        f.step().await.unwrap();
        let c = f
            .coordination
            .load_control(&spec().sid)
            .await
            .unwrap()
            .unwrap();
        if matches!(c.control.mode,ControlMode::Quarantined {ref isolation} if isolation.completion.is_some())
        {
            break;
        }
    }
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Rejected);
    let c = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    let ControlMode::Quarantined { isolation } = c.control.mode else {
        panic!()
    };
    f.processor
        .resume(
            &spec().scope,
            RecoveryAuthorization {
                guard: ControlGuard {
                    owner: c.owner,
                    expected_head: c.head,
                    expected_revision: c.control.revision,
                },
                isolation_id: isolation.operation_id,
                completion: isolation.completion.unwrap(),
                request_id: RequestId("resume-after-ref".into()),
            },
        )
        .await
        .unwrap();
    for _ in 0..20 {
        f.step().await.unwrap();
    }
    assert!(matches!(
        f.coordination
            .load_control(&spec().sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .mode,
        ControlMode::Normal
    ));
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
}
#[tokio::test]
async fn quarantine_and_takeover_after_archive_preserve_the_first_frozen_completed_result() {
    let runner = Arc::new(FinishingRunner {
        text: 70000,
        ..Default::default()
    });
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("archive-isolation").await;
    f.warm().await;
    for _ in 0..45 {
        f.step().await.unwrap();
        if f.archive
            .get_final(&spec().scope, &tid)
            .await
            .unwrap()
            .is_some()
        {
            break;
        }
    }
    let original = f
        .archive
        .get_final(&spec().scope, &tid)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        f.checkpoint().await.tasks[0].phase,
        bcs_service_api::port::repo::distributed::session::TaskPhase::AwaitingResult { .. }
    ));
    quarantine(&f, "after-archive").await;
    f.model.advance(Duration::from_secs(10));
    let (runtime, processor) = f.replacement(runner.clone()).await;
    for _ in 0..4 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        runtime.tick().await.unwrap();
    }
    for _ in 0..70 {
        runtime.tick().await.unwrap();
        processor.tick().await.unwrap();
        if f.snapshot(&tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    assert_eq!(
        f.archive
            .get_final(&spec().scope, &tid)
            .await
            .unwrap()
            .unwrap(),
        original
    );
}

#[tokio::test]
async fn succeeded_uncommitted_runner_keeps_its_frozen_time_across_old_outbox_replay() {
    let runner = Arc::new(FutureDeadlineRunner(AtomicUsize::new(0)));
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("frozen-time").await;
    f.warm().await;
    for _ in 0..18 {
        f.step().await.unwrap();
    }
    f.clock.0.store(3000, Ordering::SeqCst);
    // Released Sessions re-enter after the bounded local fairness cooldown.
    tokio::time::sleep(Duration::from_millis(550)).await;
    for _ in 0..25 {
        f.step().await.unwrap();
        if runner.0.load(Ordering::SeqCst) == 2 {
            break;
        }
    }
    let checkpoint = f.checkpoint().await;
    assert!(!checkpoint.outbox.as_ref().unwrap().effects.is_empty());
    let attempt = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap()
        .control
        .active_attempt
        .unwrap();
    assert!(
        matches!(attempt.outcome, Some(AttemptOutcome::Succeeded { .. }))
            && attempt.replay.is_none()
    );
    assert_eq!(attempt.identity.frozen_now, TimestampMillis(3000));
    f.model.advance(Duration::from_secs(10));
    f.clock.0.store(9000, Ordering::SeqCst);
    let (runtime, processor) = f.replacement(runner.clone()).await;
    for _ in 0..4 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        runtime.tick().await.unwrap();
    }
    for _ in 0..65 {
        runtime.tick().await.unwrap();
        processor.tick().await.unwrap();
        if f.snapshot(&tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    assert_eq!(
        f.archive
            .get_final(&spec().scope, &tid)
            .await
            .unwrap()
            .unwrap()
            .finalized_at,
        TimestampMillis(3000)
    );
    assert_eq!(
        f.coordination
            .get_attempt(&spec().sid, &attempt.identity.id)
            .await
            .unwrap()
            .unwrap()
            .identity,
        attempt.identity
    );
}
#[tokio::test]
async fn unknown_replay_begin_superseded_by_isolation_uses_a_fresh_authorized_episode() {
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::new(runner).await;
    let tid = f.admit("begin-unknown-isolate").await;
    f.warm().await;
    for _ in 0..30 {
        f.step().await.unwrap();
        if f.checkpoint().await.commit_seq == CommitSeq(2) {
            break;
        }
    }
    let mut begun = None;
    for _ in 0..4 {
        f.model.lose_next_response();
        assert_eq!(
            f.processor.tick().await.unwrap_err().code,
            "store_outcome_unknown"
        );
        let active = f
            .coordination
            .load_control(&spec().sid)
            .await
            .unwrap()
            .unwrap()
            .control
            .active_attempt
            .unwrap();
        if active.replay.is_some() && active.outcome.is_none() {
            begun = Some(active);
            break;
        }
        tokio::time::sleep(Duration::from_millis(210)).await;
    }
    let begun = begun.expect("durable replay begin with lost reply");
    quarantine(&f, "supersede-begin").await;
    tokio::time::sleep(Duration::from_millis(210)).await;
    for _ in 0..90 {
        f.step().await.unwrap();
        let c = f
            .coordination
            .load_control(&spec().sid)
            .await
            .unwrap()
            .unwrap();
        if matches!(c.control.mode,ControlMode::Quarantined {ref isolation} if isolation.completion.is_some())
        {
            break;
        }
    }
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(control.control.mode,ControlMode::Quarantined {ref isolation} if isolation.operation_id.0=="supersede-begin" && isolation.completion.is_some()),
        "{control:?}"
    );
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    assert!(
        f.coordination
            .get_attempt(&spec().sid, &begun.identity.id)
            .await
            .unwrap()
            .is_some()
    );
}

#[tokio::test]
async fn historical_success_after_a_racing_isolation_never_authorizes_the_ordinary_candidate() {
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("success-isolation-race").await;
    f.warm().await;
    f.control
        .isolate_after_runner_success
        .store(true, Ordering::SeqCst);
    for _ in 0..95 {
        let _ = f.step().await;
        tokio::time::sleep(Duration::from_millis(5)).await;
        let c = f
            .coordination
            .load_control(&spec().sid)
            .await
            .unwrap()
            .unwrap();
        if matches!(c.control.mode,ControlMode::Quarantined {ref isolation} if isolation.completion.is_some())
        {
            break;
        }
    }
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Rejected);
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn historical_begin_reply_after_isolation_does_not_call_runner() {
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("begin-authorization-race").await;
    f.warm().await;
    f.control.isolate_after_begin.store(true, Ordering::SeqCst);
    for _ in 0..95 {
        let _ = f.step().await;
        tokio::time::sleep(Duration::from_millis(5)).await;
        if f.snapshot(&tid).await.content.status == TaskStatus::Rejected {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 0);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Rejected);
}
#[tokio::test]
async fn historical_replay_begin_reply_after_blocking_does_not_call_effect_ports() {
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::new(runner).await;
    let tid = f.admit("replay-authorization-race").await;
    f.warm().await;
    f.control
        .block_after_replay_begin
        .store(true, Ordering::SeqCst);
    for _ in 0..70 {
        let _ = f.step().await;
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    assert_eq!(f.publisher.append_calls.load(Ordering::SeqCst), 0);
    assert!(
        f.archive
            .get_final(&spec().scope, &tid)
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        matches!(f.coordination.load_control(&spec().sid).await.unwrap().unwrap().control.mode,ControlMode::Blocked {ref isolation} if isolation.closure.is_some())
    );
}
