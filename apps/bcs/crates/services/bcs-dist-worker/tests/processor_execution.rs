#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    reason = "processor behavior assertions"
)]
mod processor_support;
mod support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{result::*, session::*};
use processor_support::*;
use std::{
    sync::{Arc, atomic::Ordering},
    time::Duration,
};

#[tokio::test]
async fn real_providers_archive_then_publish_terminal_before_starting_next_task() {
    let runner = Arc::new(FinishingRunner {
        text: 100,
        ..Default::default()
    });
    let f = Fixture::new(runner.clone()).await;
    let first = f.admit("first").await;
    let second = f.admit("second").await;
    f.warm().await;
    let mut observed_awaiting = false;
    for _ in 0..80 {
        f.step().await.unwrap();
        let checkpoint = f.checkpoint().await;
        if checkpoint
            .tasks
            .iter()
            .any(|t| t.tid == first && matches!(t.phase, TaskPhase::AwaitingResult { .. }))
        {
            observed_awaiting = true;
            assert_ne!(
                f.snapshot(&first).await.content.status,
                TaskStatus::Completed
            );
        }
        if runner.calls.load(Ordering::SeqCst) > 1 {
            assert_eq!(
                f.snapshot(&first).await.content.status,
                TaskStatus::Completed
            );
        }
        if f.snapshot(&second).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert!(observed_awaiting);
    assert_eq!(runner.calls.load(Ordering::SeqCst), 2);
    assert_eq!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(
        f.snapshot(&second).await.content.status,
        TaskStatus::Completed
    );
    assert!(
        f.archive
            .get_final(&spec().scope, &first)
            .await
            .unwrap()
            .is_some()
    );
}

#[tokio::test]
async fn lost_commit_response_keeps_the_decision_and_large_blob_identity() {
    let runner = Arc::new(FinishingRunner {
        text: 300000,
        ..Default::default()
    });
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("large").await;
    f.warm().await;
    // Commit the queue admission, then freeze the Runner decision.
    for _ in 0..30 {
        f.step().await.unwrap();
        if runner.calls.load(Ordering::SeqCst) == 1 {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    f.sessions.lose_commit.store(true, Ordering::SeqCst);
    let mut lost = false;
    for _ in 0..40 {
        let result = f.step().await;
        if !f.sessions.lost.lock().unwrap().is_empty() {
            assert_eq!(result.unwrap_err().code, "store_outcome_unknown");
            lost = true;
            break;
        }
        result.unwrap();
    }
    assert!(lost, "candidate never reached the injected commit");
    assert_eq!(f.processor.observations().await.unwrap().unknown_commits, 1);
    let frozen = f.sessions.lost.lock().unwrap()[0].clone();
    assert_eq!(
        frozen.candidate.outbox.as_ref().unwrap().created_at,
        Some(TimestampMillis(1000))
    );
    assert!(matches!(
        frozen.candidate.tasks[0]
            .finalization
            .as_ref()
            .unwrap()
            .record
            .snapshot,
        Payload::Ref(_)
    ));
    assert_ne!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    tokio::time::sleep(Duration::from_millis(210)).await;
    for _ in 0..30 {
        f.step().await.unwrap();
        if f.snapshot(&tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    assert_eq!(f.processor.observations().await.unwrap().unknown_commits, 0);
    let archived = f
        .archive
        .get_final(&spec().scope, &tid)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        archived,
        frozen.candidate.tasks[0]
            .finalization
            .as_ref()
            .unwrap()
            .record
    );
}

#[tokio::test]
async fn output_quota_discards_the_entire_decision_and_does_not_poison_next_task() {
    let runner = Arc::new(FinishingRunner {
        text: 1_048_575,
        ..Default::default()
    });
    let f = Fixture::new(runner.clone()).await;
    let first = f.admit("over-quota").await;
    let second = f.admit("next").await;
    f.warm().await;
    for _ in 0..100 {
        f.step().await.unwrap();
        if f.snapshot(&second).await.content.status == TaskStatus::Failed {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 2);
    for tid in [first, second] {
        let snapshot = f.snapshot(&tid).await;
        assert_eq!(snapshot.content.status, TaskStatus::Failed);
        assert_eq!(snapshot.output_usage, OutputUsage::default());
        assert_eq!(
            snapshot.content.snapshot,
            Payload::Inline(serde_json::json!({"format_version":1,"units":[]}))
        );
        let record = f
            .archive
            .get_final(&spec().scope, &tid)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            record.result,
            Payload::Inline(serde_json::json!({"code":"OutputQuotaExceeded"}))
        );
    }
    assert!(
        !f.coordination
            .load(&spec().sid)
            .await
            .unwrap()
            .unwrap()
            .recovery
            .quarantined
    );
}

#[tokio::test]
async fn canceling_the_processing_future_keeps_its_unknown_commit_for_resolution() {
    let runner = Arc::new(FinishingRunner {
        text: 100,
        ..Default::default()
    });
    let f = Arc::new(Fixture::new(runner.clone()).await);
    let tid = f.admit("cancel-future").await;
    f.warm().await;
    for _ in 0..30 {
        f.step().await.unwrap();
        if runner.calls.load(Ordering::SeqCst) == 1 {
            break;
        }
    }
    f.sessions.pause_commit.store(true, Ordering::SeqCst);
    let running = tokio::spawn({
        let f = f.clone();
        async move { f.processor.tick().await }
    });
    tokio::time::timeout(Duration::from_secs(2), f.sessions.entered.notified())
        .await
        .unwrap();
    running.abort();
    assert!(running.await.unwrap_err().is_cancelled());
    // DB committed, caller future vanished before any confirmation/effects.
    assert!(matches!(
        f.checkpoint().await.tasks[0].phase,
        TaskPhase::AwaitingResult { .. }
    ));
    assert_ne!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    for _ in 0..40 {
        f.step().await.unwrap();
        if f.snapshot(&tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
}

#[tokio::test]
async fn takeover_after_archive_replays_the_frozen_result_without_running_the_reducer() {
    let runner = Arc::new(FinishingRunner {
        text: 70000,
        ..Default::default()
    });
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit("takeover").await;
    f.warm().await;
    for _ in 0..40 {
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
    let record = f
        .archive
        .get_final(&spec().scope, &tid)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        f.checkpoint().await.tasks[0].phase,
        TaskPhase::AwaitingResult { .. }
    ));
    let original_owner = f
        .coordination
        .load(&spec().sid)
        .await
        .unwrap()
        .unwrap()
        .owner;
    f.model.advance(Duration::from_secs(10));
    let (replacement, processor) = f.replacement(runner.clone()).await;
    for _ in 0..4 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        replacement.tick().await.unwrap();
    }
    assert_ne!(
        f.coordination
            .load(&spec().sid)
            .await
            .unwrap()
            .unwrap()
            .owner,
        original_owner
    );
    for _ in 0..50 {
        replacement.tick().await.unwrap();
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
        record
    );
}

#[tokio::test]
async fn release_failure_retries_the_release_instead_of_starting_another_round() {
    let runner = Arc::new(FinishingRunner {
        text: 100,
        ..Default::default()
    });
    let f = Fixture::new(runner).await;
    let tid = f.admit("release").await;
    f.warm().await;
    f.sessions.fail_release.store(true, Ordering::SeqCst);
    let mut failed = false;
    for _ in 0..80 {
        if let Err(error) = f.step().await {
            assert_eq!(error.code, "store_not_applied");
            failed = true;
            break;
        }
    }
    assert!(failed);
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
    assert_eq!(f.sessions.release_calls.load(Ordering::SeqCst), 1);
    tokio::time::sleep(Duration::from_millis(210)).await;
    f.step().await.unwrap();
    assert_eq!(f.sessions.release_calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn full_reader_capacity_rebuilds_without_losing_pending_input_or_unknown_commit() {
    let runner = Arc::new(FinishingRunner {
        text: 100,
        ..Default::default()
    });
    let f = Fixture::with_reader_limits(runner.clone(), 1, 2, 4).await;
    let first = f.admit("reader-first").await;
    let mut other = spec();
    other.sid = Sid("s0001.other".into());
    let second = f.admit_for(other, "reader-second").await;
    f.warm().await;
    // First tick recovers both. Second reads one Session and freezes admission,
    // while the other remains idle, causing the next reader visit to fail.
    f.step().await.unwrap();
    f.step().await.unwrap();
    f.consumer.fail.store(true, Ordering::SeqCst);
    let error = f.step().await.unwrap_err();
    assert_eq!(error.code, "store_not_applied");
    assert_eq!(f.consumer.opens.load(Ordering::SeqCst), 2);
    for _ in 0..100 {
        f.step().await.unwrap();
        if f.snapshot(&first).await.content.status == TaskStatus::Completed
            && f.snapshot(&second).await.content.status == TaskStatus::Completed
        {
            break;
        }
    }
    assert_eq!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(
        f.snapshot(&second).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn a_locally_released_session_can_be_reacquired_under_the_same_owner() {
    let runner = Arc::new(FinishingRunner {
        text: 10,
        ..Default::default()
    });
    let f = Fixture::new(runner.clone()).await;
    let first = f.admit("initial").await;
    f.warm().await;
    for _ in 0..80 {
        f.step().await.unwrap();
        if f.sessions.release_calls.load(Ordering::SeqCst) > 0 {
            break;
        }
    }
    assert_eq!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    assert!(f.sessions.release_calls.load(Ordering::SeqCst) > 0);
    let previous = f
        .coordination
        .load(&spec().sid)
        .await
        .unwrap()
        .unwrap()
        .owner;
    let second = f.admit("after-release").await;
    // Local fairness cooldown uses monotonic time, independent of business time.
    tokio::time::sleep(Duration::from_millis(550)).await;
    for _ in 0..80 {
        f.step().await.unwrap();
        if f.snapshot(&second).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(
        f.coordination
            .load(&spec().sid)
            .await
            .unwrap()
            .unwrap()
            .owner,
        previous
    );
    assert_eq!(
        f.snapshot(&second).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(runner.calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn busy_session_read_ahead_does_not_exhaust_receipts_for_a_healthy_sibling() {
    let runner = Arc::new(FinishingRunner {
        text: 10,
        ..Default::default()
    });
    let f = Fixture::with_reader_limits(runner.clone(), 1, 2, 3).await;
    let slow = f.admit("slow").await;
    let mut other = spec();
    other.sid = Sid("s0001.other".into());
    let healthy = f.admit_for(other, "healthy").await;
    *f.sessions.reject_session.lock().unwrap() = Some(spec().sid);
    f.warm().await;
    for _ in 0..100 {
        if let Err(error) = f.step().await {
            assert_eq!(error.code, "store_not_applied");
        }
        if f.snapshot(&healthy).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(
        f.snapshot(&healthy).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(
        f.snapshot(&slow).await.content.status,
        TaskStatus::Submitted
    );
    // A healthy reader never had to fail/rebuild just to reclaim ignored receipts.
    assert_eq!(f.consumer.opens.load(Ordering::SeqCst), 1);
    *f.sessions.reject_session.lock().unwrap() = None;
    tokio::time::sleep(Duration::from_millis(210)).await;
    for _ in 0..80 {
        f.step().await.unwrap();
        if f.snapshot(&slow).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(
        f.snapshot(&slow).await.content.status,
        TaskStatus::Completed
    );
}

#[tokio::test]
async fn mixed_failure_and_delivery_retains_the_healthy_input_even_if_rewatch_fails() {
    let runner = Arc::new(FinishingRunner {
        text: 10,
        ..Default::default()
    });
    let f = Fixture::new(runner).await;
    f.admit("unhealthy-watch").await;
    let mut healthy_spec = spec();
    healthy_spec.sid = Sid("s0001.other".into());
    let healthy = f.admit_for(healthy_spec, "mixed-healthy").await;
    f.warm().await;
    f.step().await.unwrap();
    *f.consumer.failed_watch.lock().unwrap() = Some(spec().sid);
    *f.consumer.mixed_failure.lock().unwrap() = Some(spec().sid);
    for _ in 0..80 {
        if let Err(error) = f.step().await {
            assert_eq!(error.code, "store_not_applied");
        }
        if f.snapshot(&healthy).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(
        f.snapshot(&healthy).await.content.status,
        TaskStatus::Completed
    );
}
