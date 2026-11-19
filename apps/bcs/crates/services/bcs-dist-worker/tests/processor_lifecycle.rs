#![allow(clippy::unwrap_used, reason = "public provider lifecycle assertions")]
mod lifecycle_support;
mod processor_support;
mod support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    control::*, dispatch::DispatchControlPort, inbox::*, result::*,
};
use lifecycle_support::*;
use processor_support::Fixture;
use std::sync::atomic::Ordering;

#[tokio::test(start_paused = true)]
async fn matching_interaction_on_another_queued_task_is_consumed_without_reducing() {
    let runner = Runner::new(Mode::Interaction);
    let f = Fixture::new(runner.clone()).await;
    let running = f.admit_for(session(), "running").await;
    let queued = f.admit_for(session(), "queued").await;
    f.warm().await;
    until_status(&f, &f.processor, &running, TaskStatus::InputRequired).await;
    for _ in 0..100 {
        drive(&f, &f.processor, 1).await;
        if f.checkpoint().await.tasks.len() == 2 {
            break;
        }
    }
    assert_eq!(f.checkpoint().await.tasks.len(), 2);
    let consumed = f.checkpoint().await.consumed;
    send(
        &f,
        &queued,
        "wrong-task-reply",
        InputBody::HumanReply {
            interaction: InteractionId("confirm".into()),
            payload: Payload::Inline(serde_json::json!("wrong task")),
        },
    )
    .await;
    until_consumed_changes(&f, &f.processor, consumed).await;
    assert_eq!(runner.calls(), 1);
    assert_eq!(
        f.snapshot(&running).await.content.status,
        TaskStatus::InputRequired
    );
    assert_eq!(
        f.snapshot(&queued).await.content.status,
        TaskStatus::Submitted
    );
    assert!(
        f.dispatch
            .get(&dispatch_id(&running))
            .await
            .unwrap()
            .unwrap()
            .view
            .control
            .pending_input
            .is_empty()
    );
}

#[tokio::test(start_paused = true)]
async fn human_reply_requires_current_interaction_and_replays_once_through_dispatch_and_archive() {
    let runner = Runner::new(Mode::Interaction);
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit_for(session(), "interaction").await;
    f.warm().await;
    until_status(&f, &f.processor, &tid, TaskStatus::InputRequired).await;
    assert_eq!(
        f.snapshot(&tid).await.content.interactions[0].id,
        InteractionId("confirm".into())
    );
    let consumed = f.checkpoint().await.consumed;
    send(
        &f,
        &tid,
        "wrong",
        InputBody::HumanReply {
            interaction: InteractionId("stale".into()),
            payload: Payload::Inline(serde_json::json!("ignored")),
        },
    )
    .await;
    until_consumed_changes(&f, &f.processor, consumed).await;
    assert_eq!(runner.calls(), 1);
    assert_eq!(
        f.snapshot(&tid).await.content.status,
        TaskStatus::InputRequired
    );
    let request = control(
        &tid,
        "reply",
        InputBody::HumanReply {
            interaction: InteractionId("confirm".into()),
            payload: Payload::Inline(serde_json::json!("approved")),
        },
    );
    assert!(matches!(
        f.inbox.admit(request.clone()).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    assert!(matches!(
        f.inbox.admit(request.clone()).await.unwrap(),
        AdmitOutcome::Duplicate(_)
    ));
    let mut conflict = request;
    conflict.input.body = InputBody::HumanReply {
        interaction: InteractionId("confirm".into()),
        payload: Payload::Inline(serde_json::json!("changed")),
    };
    assert_eq!(
        f.inbox.admit(conflict).await.unwrap(),
        AdmitOutcome::ContentConflict
    );
    until_status(&f, &f.processor, &tid, TaskStatus::Completed).await;
    let record = f
        .archive
        .get_final(&session().scope, &tid)
        .await
        .unwrap()
        .unwrap();
    let dispatch = f.dispatch.get(&dispatch_id(&tid)).await.unwrap().unwrap();
    assert_eq!(dispatch.view.control.pending_input.len(), 1);
    assert_eq!(
        dispatch.view.control.pending_input[0].payload,
        Payload::Inline(serde_json::json!("approved"))
    );
    assert_eq!(runner.calls(), 2);
    assert!(f.snapshot(&tid).await.content.interactions.is_empty());
    let consumed = f.checkpoint().await.consumed;
    send(
        &f,
        &tid,
        "late",
        InputBody::HumanReply {
            interaction: InteractionId("confirm".into()),
            payload: Payload::Inline(serde_json::json!("late")),
        },
    )
    .await;
    until_consumed_changes(&f, &f.processor, consumed).await;
    assert_eq!(runner.calls(), 2);
    assert_eq!(
        f.archive.get_final(&session().scope, &tid).await.unwrap(),
        Some(record)
    );
}

#[tokio::test(start_paused = true)]
async fn cancel_before_finish_is_independent_and_retires_only_after_dispatch_cancel_and_archive() {
    let runner = Runner::new(Mode::Interaction);
    let f = Fixture::new(runner.clone()).await;
    let tid = f.admit_for(session(), "cancel").await;
    f.warm().await;
    until_status(&f, &f.processor, &tid, TaskStatus::InputRequired).await;
    send(
        &f,
        &tid,
        "cancel-control",
        InputBody::Cancel {
            reason: Some("user canceled".into()),
        },
    )
    .await;
    until_status(&f, &f.processor, &tid, TaskStatus::Canceled).await;
    let record = f
        .archive
        .get_final(&session().scope, &tid)
        .await
        .unwrap()
        .unwrap();
    let dispatch = f.dispatch.get(&dispatch_id(&tid)).await.unwrap().unwrap();
    assert!(dispatch.view.control.cancel.is_some());
    assert!(
        dispatch.view.execution.outcome.is_none(),
        "local cancel is not remote completion"
    );
    assert_eq!(
        runner.calls(),
        1,
        "independent finalizer must not reduce Cancel"
    );
    let consumed = f.checkpoint().await.consumed;
    send(
        &f,
        &tid,
        "reply-after-cancel",
        InputBody::HumanReply {
            interaction: InteractionId("confirm".into()),
            payload: Payload::Inline(serde_json::json!("too late")),
        },
    )
    .await;
    until_consumed_changes(&f, &f.processor, consumed).await;
    assert_eq!(runner.calls(), 1);
    assert_eq!(
        f.archive.get_final(&session().scope, &tid).await.unwrap(),
        Some(record)
    );
    assert!(f.checkpoint().await.tasks.is_empty());
}

#[tokio::test(start_paused = true)]
async fn task_timeout_and_step_limit_finalize_without_isolation_and_allow_next_task() {
    for timeout in [true, false] {
        let runner = Runner::new(Mode::Wait);
        let f = Fixture::new(runner.clone()).await;
        let mut s = session();
        s.limits.max_task_steps = if timeout { 100 } else { 1 };
        let first = f.admit_for(s.clone(), "first").await;
        let second = f.admit_for(s, "second").await;
        f.warm().await;
        until_status(&f, &f.processor, &first, TaskStatus::Working).await;
        drive(&f, &f.processor, 20).await;
        if timeout {
            f.clock.0.store(61_000, Ordering::SeqCst);
        } else {
            delta(&f, &first, 1, "step").await;
        }
        until_status(&f, &f.processor, &first, TaskStatus::Failed).await;
        until_status(&f, &f.processor, &second, TaskStatus::Working).await;
        let record = f
            .archive
            .get_final(&session().scope, &first)
            .await
            .unwrap()
            .unwrap();
        let encoded = serde_json::to_string(&record).unwrap();
        assert!(
            encoded.contains(if timeout {
                "TaskDeadlineExceeded"
            } else {
                "TaskStepLimitExceeded"
            }),
            "{encoded}"
        );
        assert_eq!(
            runner.calls(),
            2,
            "one Start per task; limit is enforced before reduce"
        );
        assert!(
            f.dispatch
                .get(&dispatch_id(&first))
                .await
                .unwrap()
                .unwrap()
                .view
                .control
                .cancel
                .is_some()
        );
        assert!(matches!(
            f.coordination
                .load_control(&session().sid)
                .await
                .unwrap()
                .unwrap()
                .control
                .mode,
            ControlMode::Normal
        ));
    }
}

#[tokio::test(start_paused = true)]
async fn frozen_finish_wins_late_cancel_and_archive_or_terminal_publish_failure_blocks_next_task() {
    let runner = Runner::new(Mode::Finish);
    let f = Fixture::new(runner.clone()).await;
    let output = Publisher::new(&f);
    let results = Results::new(&f);
    results.unavailable.store(true, Ordering::SeqCst);
    let p = processor(&f, runner.clone(), output.clone(), results.clone());
    let first = f.admit_for(session(), "first").await;
    let second = f.admit_for(session(), "second").await;
    f.warm().await;
    for _ in 0..80 {
        let _ = f.runtime.tick().await;
        let _ = p.tick().await;
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }
    assert!(!results.requests.lock().unwrap().is_empty());
    assert!(
        f.archive
            .get_final(&session().scope, &first)
            .await
            .unwrap()
            .is_none()
    );
    assert_ne!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(runner.calls(), 1);
    send(
        &f,
        &first,
        "late-cancel",
        InputBody::Cancel { reason: None },
    )
    .await;
    results.unavailable.store(false, Ordering::SeqCst);
    results.lose_reply.store(true, Ordering::SeqCst);
    output.reject_terminal.store(true, Ordering::SeqCst);
    for _ in 0..80 {
        let _ = f.runtime.tick().await;
        let _ = p.tick().await;
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }
    let frozen = f
        .archive
        .get_final(&session().scope, &first)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(runner.calls(), 1);
    assert_eq!(
        f.snapshot(&second).await.content.status,
        TaskStatus::Submitted
    );
    assert_ne!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    let requests = results.requests.lock().unwrap().clone();
    assert!(requests.len() >= 3);
    assert!(
        requests.iter().all(|r| r == &requests[0]),
        "every archive retry preserves the complete frozen record and source"
    );
    output.reject_terminal.store(false, Ordering::SeqCst);
    until_status(&f, &p, &second, TaskStatus::Completed).await;
    assert_eq!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(runner.calls(), 2);
    assert_eq!(
        f.archive.get_final(&session().scope, &first).await.unwrap(),
        Some(frozen)
    );
}
