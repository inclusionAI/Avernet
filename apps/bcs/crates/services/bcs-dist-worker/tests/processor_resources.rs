#![allow(clippy::unwrap_used, reason = "actual-provider resource assertions")]
mod processor_support;
mod support;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{budget::*, session::*};
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::*};
use bcs_service_api::{
    core::dist_runner::*,
    port::repo::distributed::{control::*, task_feed::*},
};
use processor_support::*;
use std::sync::Arc;
use std::sync::atomic::AtomicUsize;

async fn unavailable_outbox_yields_the_only_slot(unknown: bool) {
    let runner = Arc::new(FinishingRunner::default());
    let f = Fixture::with_reader_limits(runner.clone(), 1, 1, 4).await;
    let first = f.admit("unavailable-output").await;
    f.warm().await;
    // Fail the publication from a Runner result, after its candidate is durable.
    for _ in 0..80 {
        f.step().await.unwrap();
        if runner.calls.load(std::sync::atomic::Ordering::SeqCst) == 1 {
            break;
        }
    }
    assert_eq!(runner.calls.load(std::sync::atomic::Ordering::SeqCst), 1);
    *f.publisher.fail_session.lock().unwrap() = Some((spec().sid, unknown));
    for _ in 0..20 {
        if f.step().await.is_err() {
            break;
        }
    }
    let retained = f.checkpoint().await;
    let failed = f
        .publisher
        .append_requests
        .lock()
        .unwrap()
        .last()
        .unwrap()
        .clone();
    assert_eq!(failed.source.commit.sequence, retained.commit_seq);
    let mut sibling = spec();
    sibling.sid = Sid("s0001.healthy".into());
    let second = f.admit_for(sibling.clone(), "healthy-output").await;
    for _ in 0..240 {
        let _ = f.step().await;
        if f.snapshot(&second).await.content.status == TaskStatus::Completed {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert_eq!(
        f.snapshot(&second).await.content.status,
        TaskStatus::Completed,
        "a failed committed outbox monopolized the only Session slot"
    );
    assert_eq!(f.checkpoint().await, retained);
    assert_ne!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(runner.calls.load(std::sync::atomic::Ordering::SeqCst), 2);
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
    // Reacquisition enters Recovering; another failure must yield that slot too.
    for _ in 0..240 {
        let _ = f.step().await;
        if f.publisher
            .append_requests
            .lock()
            .unwrap()
            .iter()
            .filter(|request| **request == failed)
            .count()
            >= 2
        {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert!(
        f.publisher
            .append_requests
            .lock()
            .unwrap()
            .iter()
            .filter(|request| **request == failed)
            .count()
            >= 2
    );
    let before = f.publisher.append_requests.lock().unwrap().len();
    *f.publisher.fail_session.lock().unwrap() = None;
    for _ in 0..240 {
        let _ = f.step().await;
        if f.snapshot(&first).await.content.status == TaskStatus::Completed {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert_eq!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    assert!(
        f.publisher.append_requests.lock().unwrap()[before..].contains(&failed),
        "recovery must retry the original committed effect"
    );
    assert_eq!(runner.calls.load(std::sync::atomic::Ordering::SeqCst), 2);
}

#[tokio::test(start_paused = true)]
async fn not_applied_outbox_yields_capacity_and_recovers_without_rerunning() {
    unavailable_outbox_yields_the_only_slot(false).await;
}

#[tokio::test(start_paused = true)]
async fn unknown_outbox_yields_capacity_and_replays_the_same_effect() {
    unavailable_outbox_yields_the_only_slot(true).await;
}

#[tokio::test(start_paused = true)]
async fn canceled_replay_parking_finishes_cleanup_before_retrying_effects() {
    use std::sync::atomic::Ordering;
    use std::time::Duration;
    let runner = Arc::new(FinishingRunner::default());
    let f = Arc::new(Fixture::with_reader_limits(runner.clone(), 1, 1, 4).await);
    let first = f.admit("cancel-parking").await;
    f.warm().await;
    for _ in 0..80 {
        f.step().await.unwrap();
        if runner.calls.load(Ordering::SeqCst) == 1 {
            break;
        }
    }
    assert_eq!(runner.calls.load(Ordering::SeqCst), 1);
    *f.publisher.fail_session.lock().unwrap() = Some((spec().sid, true));
    f.sessions.pause_release.store(true, Ordering::SeqCst);
    let running = tokio::spawn({
        let f = f.clone();
        async move {
            for _ in 0..20 {
                let _ = f.processor.tick().await;
            }
        }
    });
    tokio::time::timeout(
        Duration::from_secs(2),
        f.sessions.release_entered.notified(),
    )
    .await
    .unwrap();
    running.abort();
    assert!(running.await.unwrap_err().is_cancelled());
    let retained = f.checkpoint().await;
    let appends = f.publisher.append_calls.load(Ordering::SeqCst);
    let releases = f.sessions.release_calls.load(Ordering::SeqCst);
    *f.publisher.fail_session.lock().unwrap() = None;
    // Local release already executed, but the canceled caller did not finish
    // removing runtime state. Retry that cleanup, not replay or Inbox ack.
    f.processor.tick().await.unwrap();
    assert_eq!(f.publisher.append_calls.load(Ordering::SeqCst), appends);
    assert_eq!(
        f.sessions.release_calls.load(Ordering::SeqCst),
        releases + 1
    );
    assert_eq!(f.checkpoint().await, retained);
    assert_ne!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    let mut sibling = spec();
    sibling.sid = Sid("s0001.healthy".into());
    let second = f.admit_for(sibling, "after-canceled-parking").await;
    for _ in 0..240 {
        let _ = f.step().await;
        if f.snapshot(&first).await.content.status == TaskStatus::Completed
            && f.snapshot(&second).await.content.status == TaskStatus::Completed
        {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
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

#[tokio::test(start_paused = true)]
async fn isolation_racing_failed_replay_parking_closes_the_gate_and_yields() {
    use std::sync::atomic::Ordering;
    use std::time::Duration;
    let f =
        Arc::new(Fixture::with_reader_limits(Arc::new(FinishingRunner::default()), 1, 1, 4).await);
    f.admit("isolated-replay").await;
    f.warm().await;
    *f.publisher.fail_session.lock().unwrap() = Some((spec().sid, true));
    f.publisher.pause_failure.store(true, Ordering::SeqCst);
    let running = tokio::spawn({
        let f = f.clone();
        async move {
            for _ in 0..40 {
                let _ = f.processor.tick().await;
            }
        }
    });
    tokio::time::timeout(
        Duration::from_secs(2),
        f.publisher.failure_entered.notified(),
    )
    .await
    .unwrap();
    let current = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    let attempt = AttemptIdentity {
        id: RequestId("isolate-during-output-failure".into()),
        head: current.head,
        state_digest: current.checkpoint_digest,
        input_digest: Digest("race".into()),
        context_digest: Digest("race".into()),
        frozen_now: TimestampMillis(1000),
    };
    assert!(matches!(
        f.coordination
            .isolate(
                ControlGuard {
                    owner: current.owner,
                    expected_head: current.head,
                    expected_revision: current.control.revision,
                },
                attempt,
                AttemptOutcome::Uninterpretable {
                    code: "injected_racing_isolation".into(),
                    isolation_id: RequestId("isolate-output".into()),
                },
            )
            .await
            .unwrap(),
        ControlOutcome::Applied(_)
    ));
    f.publisher.failure_resume.notify_one();
    running.await.unwrap();
    let retained = f.checkpoint().await;
    let appends = f.publisher.append_calls.load(Ordering::SeqCst);
    let mut sibling = spec();
    sibling.sid = Sid("s0001.healthy".into());
    let second = f.admit_for(sibling, "after-racing-isolation").await;
    for _ in 0..360 {
        let _ = f.step().await;
        let control = f
            .coordination
            .load_control(&spec().sid)
            .await
            .unwrap()
            .unwrap();
        if matches!(control.control.mode, ControlMode::Blocked { isolation }
            if isolation.closure.is_some())
            && f.snapshot(&second).await.content.status == TaskStatus::Completed
        {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(control.control.mode, ControlMode::Blocked { isolation }
        if isolation.closure.is_some()),
        "parking must observe the replacing control attempt"
    );
    assert_eq!(
        f.snapshot(&second).await.content.status,
        TaskStatus::Completed
    );
    assert_eq!(f.checkpoint().await, retained);
    let requests = f.publisher.append_requests.lock().unwrap();
    assert!(
        requests[appends..]
            .iter()
            .all(|request| request.source.commit.sid != spec().sid)
    );
}

struct SelectiveRunner {
    bad: InvalidRunner,
    good: FinishingRunner,
}
#[async_trait::async_trait]
impl DistRunnerCoreService for SelectiveRunner {
    fn validate_session(
        &self, _: &SessionSpec, _: &serde_json::Value,
    ) -> Result<Arc<dyn std::any::Any + Send + Sync>, RunnerError> {
        Ok(Arc::new(()))
    }
    async fn init(&self, context: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        if context.spec.sid == spec().sid {
            self.bad.init(context).await
        } else {
            self.good.init(context).await
        }
    }
    async fn reduce(
        &self,
        context: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        if context.spec.sid == spec().sid {
            self.bad.reduce(context, state, inputs).await
        } else {
            self.good.reduce(context, state, inputs).await
        }
    }
}

async fn isolated_session_yields_the_only_local_slot(blocked: bool) {
    let f = Fixture::with_reader_limits(
        Arc::new(SelectiveRunner {
            bad: InvalidRunner {
                calls: AtomicUsize::new(0),
                corrupt: blocked,
            },
            good: FinishingRunner::default(),
        }),
        1,
        1,
        8,
    )
    .await;
    f.admit("isolated").await;
    f.warm().await;
    for _ in 0..150 {
        let _ = f.step().await;
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        if let Some(c) = f.coordination.load_control(&spec().sid).await.unwrap()
            && match c.control.mode {
                ControlMode::Blocked { isolation } => isolation.closure.is_some(),
                ControlMode::Quarantined { isolation } => isolation.completion.is_some(),
                _ => false,
            }
        {
            break;
        }
    }
    let isolated = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        isolated.control.mode,
        ControlMode::Blocked { .. } | ControlMode::Quarantined { .. }
    ));
    let retained = f.checkpoint().await;
    let mut sibling = spec();
    sibling.sid = Sid("s0001.healthy".into());
    let tid = f.admit_for(sibling.clone(), "healthy").await;
    for _ in 0..200 {
        let _ = f.step().await;
        if f.feed
            .load_snapshot(&sibling.scope, &tid)
            .await
            .unwrap()
            .unwrap()
            .content
            .status
            == TaskStatus::Completed
        {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert_eq!(
        f.feed
            .load_snapshot(&sibling.scope, &tid)
            .await
            .unwrap()
            .unwrap()
            .content
            .status,
        TaskStatus::Completed
    );
    assert_eq!(f.checkpoint().await, retained);
    let after = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(after.control.mode, isolated.control.mode);
}

#[tokio::test(start_paused = true)]
async fn blocked_session_releases_capacity_without_discarding_durable_evidence() {
    isolated_session_yields_the_only_local_slot(true).await;
}
#[tokio::test(start_paused = true)]
async fn completed_quarantine_releases_capacity_without_automatic_resume() {
    isolated_session_yields_the_only_local_slot(false).await;
}

#[tokio::test(start_paused = true)]
async fn an_idle_owned_shard_does_not_hold_the_only_reader_forever() {
    let f = Fixture::with_shards(
        Arc::new(FinishingRunner::default()),
        1,
        1,
        4,
        vec![ShardId(1), ShardId(2)],
    )
    .await;
    let first = f.admit("reader-one").await;
    f.warm().await;
    for _ in 0..80 {
        let _ = f.step().await;
        if f.snapshot(&first).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(
        f.snapshot(&first).await.content.status,
        TaskStatus::Completed
    );
    let mut second = spec();
    second.sid = Sid("s0002.sibling".into());
    second.shard = ShardId(2);
    let tid = f.admit_for(second.clone(), "reader-two").await;
    for _ in 0..150 {
        let _ = f.step().await;
        if f.feed
            .load_snapshot(&second.scope, &tid)
            .await
            .unwrap()
            .unwrap()
            .content
            .status
            == TaskStatus::Completed
        {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert_eq!(
        f.feed
            .load_snapshot(&second.scope, &tid)
            .await
            .unwrap()
            .unwrap()
            .content
            .status,
        TaskStatus::Completed
    );
}

#[tokio::test(start_paused = true)]
async fn a_hot_session_yields_before_draining_all_its_inputs() {
    let f = Fixture::with_reader_limits(Arc::new(FinishingRunner::default()), 1, 1, 4).await;
    let mut hot = Vec::new();
    for i in 0..12 {
        hot.push(f.admit(&format!("hot-{i}")).await);
    }
    f.warm().await;
    let mut sibling = spec();
    sibling.sid = Sid("s0001.healthy".into());
    let tid = f.admit_for(sibling.clone(), "sibling").await;
    for _ in 0..400 {
        let _ = f.step().await;
        if f.feed
            .load_snapshot(&sibling.scope, &tid)
            .await
            .unwrap()
            .unwrap()
            .content
            .status
            == TaskStatus::Completed
        {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    assert_eq!(
        f.feed
            .load_snapshot(&sibling.scope, &tid)
            .await
            .unwrap()
            .unwrap()
            .content
            .status,
        TaskStatus::Completed
    );
    let mut incomplete = 0;
    for t in hot {
        if f.snapshot(&t).await.content.status != TaskStatus::Completed {
            incomplete += 1;
        }
    }
    assert!(
        incomplete > 0,
        "hot Session drained its entire backlog before allowing a sibling"
    );
}

#[tokio::test]
async fn invalid_session_or_output_limits_never_create_a_task() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    let mut bad_profile = metadata();
    bad_profile.output_limits.max_outbox_bytes = 65_537;
    let mut bad_timeout = spec();
    bad_timeout.limits.task_timeout_millis = u64::MAX;
    let mut large_binding = spec();
    large_binding.authorized_agents.push(AuthorizedAgent {
        pool: PoolId("pool".into()),
        agent: AgentId("x".repeat(30_000)),
    });
    for (case, definition, initial) in [
        ("oversized-outbox", spec(), bad_profile),
        ("oversized-binding", large_binding, metadata()),
        ("unrepresentable-timeout", bad_timeout, metadata()),
    ] {
        let result = f
            .inbox
            .admit(AdmitRequest {
                scope: definition.scope.clone(),
                input: InputEnvelope {
                    schema_version: 1,
                    sid: definition.sid.clone(),
                    tid: initial.tid.clone(),
                    input_id: InputId(case.into()),
                    business_digest: Digest(String::new()),
                    body: InputBody::User {
                        payload: Payload::Inline(serde_json::Value::Null),
                    },
                },
                create_session: Some(definition),
                create_task: Some(initial.clone()),
            })
            .await;
        assert!(
            matches!(result, Err(StoreError::Invalid { .. })),
            "{case}: {result:?}"
        );
        assert!(
            f.feed
                .load_snapshot(&initial.scope, &initial.tid)
                .await
                .unwrap()
                .is_none()
        );
        assert!(f.sessions.get_spec(&spec().sid).await.unwrap().is_none());
    }
}

#[tokio::test(start_paused = true)]
async fn authoritative_acquisition_respects_the_shared_session_quota() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    f.warm().await;
    f.admit("budget").await;
    assert!(f.scan_budget.claim(ScanClass::Discovery, 0, 64).unwrap());
    for _ in 0..3 {
        let _ = f.runtime.tick().await;
    }
    assert!(f.coordination.load(&spec().sid).await.unwrap().is_none());
    tokio::time::advance(std::time::Duration::from_secs(1)).await;
    for _ in 0..3 {
        let _ = f.runtime.tick().await;
    }
    assert!(f.coordination.load(&spec().sid).await.unwrap().is_some());
}

#[tokio::test(start_paused = true)]
async fn exhausted_scan_budget_defers_recovery_without_failing_tick() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    f.warm().await;
    f.admit("deferred-discovery").await;
    tokio::time::advance(std::time::Duration::from_secs(1)).await;
    assert!(f.scan_budget.claim(ScanClass::Discovery, 64, 0).unwrap());
    f.runtime.tick().await.unwrap();
    assert!(f.coordination.load(&spec().sid).await.unwrap().is_none());

    tokio::time::advance(std::time::Duration::from_millis(20)).await;
    f.runtime.tick().await.unwrap();
    assert!(f.coordination.load(&spec().sid).await.unwrap().is_some());
}

#[tokio::test(start_paused = true)]
async fn output_inline_threshold_does_not_change_the_input_capacity() {
    let f = Fixture::new(Arc::new(FinishingRunner::default())).await;
    let mut initial = metadata();
    initial.output_limits.inline_threshold_bytes = 128;
    let tid = initial.tid.clone();
    f.inbox
        .admit(AdmitRequest {
            scope: spec().scope,
            input: InputEnvelope {
                schema_version: 1,
                sid: spec().sid,
                tid: tid.clone(),
                input_id: InputId("small-output".into()),
                business_digest: Digest(String::new()),
                body: InputBody::User {
                    payload: Payload::Inline(serde_json::json!("x".repeat(200))),
                },
            },
            create_session: Some(spec()),
            create_task: Some(initial),
        })
        .await
        .unwrap();
    f.warm().await;
    for _ in 0..80 {
        let _ = f.step().await;
        if f.snapshot(&tid).await.content.status == TaskStatus::Completed {
            break;
        }
    }
    assert_eq!(f.snapshot(&tid).await.content.status, TaskStatus::Completed);
}
