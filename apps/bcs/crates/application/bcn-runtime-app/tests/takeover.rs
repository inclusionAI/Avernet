mod common;
use bcn_runtime_api::{
    application::*,
    port::{Clock, repo::*},
};
use bcn_runtime_model::*;
use common::*;
use std::{sync::Arc, sync::atomic::Ordering};
#[tokio::test]
async fn before_checkpoint_restarts_from_inbox_and_after_checkpoint_republishes_outbox() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("crash")).await.unwrap();
    let fence = f
        .repo
        .claim_shard(session_shard(&task.session_id), node("dead"), None)
        .await
        .unwrap();
    let spec = f.repo.load_spec(&task.session_id).await.unwrap().unwrap();
    let runner = f.runners().remove(0);
    let cp = f
        .repo
        .activate(spec.clone(), runner.initialize(&spec).unwrap(), fence)
        .await
        .unwrap();
    let input = f
        .repo
        .read_batch(&task.session_id, 0, 1)
        .await
        .unwrap()
        .remove(0);
    let transition = runner.reduce(&cp.state, &input.input).unwrap();
    f.repo
        .commit(SessionCommit {
            session_id: task.session_id.clone(),
            fence: cp.fence,
            expected_revision: cp.revision,
            consumed_input: 1,
            transition,
        })
        .await
        .unwrap();
    let w = f.worker("new");
    w.step(&task.session_id).await.unwrap();
    assert!(
        !f.repo
            .load(&task.session_id)
            .await
            .unwrap()
            .unwrap()
            .pending_effects
            .is_empty()
    );
    for now in [2000, 3000, 3999] {
        f.clock.set(now);
        w.step(&task.session_id).await.unwrap();
        assert_eq!(
            f.repo
                .read_owner(&session_shard(&task.session_id))
                .await
                .unwrap()
                .unwrap()
                .owner,
            node("dead")
        );
    }
    f.clock.set(4000);
    w.step(&task.session_id).await.unwrap();
    let cp = f.repo.load(&task.session_id).await.unwrap().unwrap();
    assert!(cp.pending_effects.is_empty());
    assert_eq!(cp.fence.owner, node("new"));
    let t = Arc::new(Transport::new(vec![Ok(AgentEvent::Completed {
        output: text("recovered"),
    })]));
    let c = f.connector("c", t);
    f.pump(&w, &c, 12).await;
    assert_eq!(
        g.get(caller(), task.task_id).await.unwrap().status,
        TaskStatus::Completed
    );
}
#[tokio::test]
async fn prepared_can_send_but_may_have_sent_without_handle_is_never_resent() {
    for sent in [false, true] {
        let f = Fixture::new();
        let g = f.gateway();
        let task = g.submit(caller(), request("phase")).await.unwrap();
        let w = f.worker("w");
        w.step(&task.session_id).await.unwrap();
        let command = f
            .repo
            .receive(
                &scope(),
                &node("dead-c"),
                CommandConsumerKind::Connector,
                None,
                1,
            )
            .await
            .unwrap()
            .items
            .remove(0);
        let mut record = f
            .repo
            .claim(&command.command_id, node("dead-c"))
            .await
            .unwrap();
        f.repo
            .ack(&command.command_id, &node("dead-c"))
            .await
            .unwrap();
        if sent {
            record = f
                .repo
                .mark_may_have_sent(&record.fence, record.revision)
                .await
                .unwrap();
        }
        let t = Arc::new(Transport::new(vec![Ok(AgentEvent::Completed {
            output: text("fresh"),
        })]));
        let c = f.connector("new-c", t.clone());
        for now in [1000, 2000, 3000, 3999] {
            f.clock.set(now);
            c.tick().await.unwrap();
            assert_eq!(
                f.repo
                    .read(&record.fence.dispatch_id)
                    .await
                    .unwrap()
                    .unwrap()
                    .fence
                    .owner,
                node("dead-c")
            );
        }
        f.clock.set(4000);
        f.pump(&w, &c, 20).await;
        let task = g.get(caller(), task.task_id).await.unwrap();
        assert_eq!(t.sends.load(Ordering::SeqCst), if sent { 0 } else { 1 });
        assert_eq!(
            task.status,
            if sent {
                TaskStatus::Failed
            } else {
                TaskStatus::Completed
            }
        );
    }
}
#[tokio::test]
async fn known_handle_is_saved_before_next_read_and_recovers_by_query_without_resend() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("known")).await.unwrap();
    let w = f.worker("w");
    w.step(&task.session_id).await.unwrap();
    let t = Arc::new(Transport::new(vec![
        Ok(AgentEvent::Accepted { handle: handle() }),
        Ok(AgentEvent::Delta {
            text: "later".into(),
        }),
    ]));
    let c = f.connector("old-c", t.clone());
    c.tick().await.unwrap();
    c.tick().await.unwrap();
    let record = DispatchStore::scan_active(f.repo.as_ref(), &scope(), None, 10)
        .await
        .unwrap()
        .items
        .remove(0);
    assert_eq!(record.handle, Some(handle()));
    assert_eq!(
        f.repo
            .read_projection(&record.fence.dispatch_id)
            .await
            .unwrap()
            .unwrap(),
        vec![]
    );
    drop(c);
    let new = f.connector("new-c", t.clone());
    for _ in 0..20 {
        f.clock.set(Clock::now_ms(f.clock.as_ref()) + 500);
        new.tick().await.unwrap();
        w.step(&task.session_id).await.unwrap();
        w.tick().await.unwrap();
    }
    let done = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(done.status, TaskStatus::Completed);
    assert_eq!(done.output, text("queried"));
    assert_eq!(t.sends.load(Ordering::SeqCst), 1);
    assert!(t.queries.load(Ordering::SeqCst) > 0);
    assert!(
        f.repo
            .publish_reset(
                &record.fence,
                ConnectorProjection {
                    task_id: done.task_id,
                    output: text("stale"),
                    expected_cursor: EventCursor {
                        epoch: 0,
                        sequence: 0
                    }
                },
                EventId::new(scope(), "stale")
            )
            .await
            .is_err()
    );
}
#[tokio::test]
async fn heartbeat_failure_restarts_full_warmup_and_unknown_never_authorizes_takeover() {
    use common::faults::LivenessFault;
    use std::sync::atomic::AtomicBool;
    let mut f = Fixture::new();
    let gate = Arc::new(LivenessFault {
        inner: f.ports.liveness.clone(),
        fail: AtomicBool::new(false),
        unknown: AtomicBool::new(false),
    });
    f.ports.liveness = gate.clone();
    let g = f.gateway();
    let task = g.submit(caller(), request("warmup")).await.unwrap();
    let shard = session_shard(&task.session_id);
    f.repo
        .claim_shard(shard.clone(), node("dead"), None)
        .await
        .unwrap();
    let w = f.worker("new");
    for now in [1000, 2000] {
        f.clock.set(now);
        w.step(&task.session_id).await.unwrap();
    }
    f.clock.set(3000);
    gate.fail.store(true, Ordering::SeqCst);
    assert!(w.step(&task.session_id).await.is_err());
    gate.fail.store(false, Ordering::SeqCst);
    for now in [4000, 5000, 6000] {
        f.clock.set(now);
        w.step(&task.session_id).await.unwrap();
        assert_eq!(
            f.repo.read_owner(&shard).await.unwrap().unwrap().owner,
            node("dead")
        );
    }
    f.clock.set(7000);
    gate.unknown.store(true, Ordering::SeqCst);
    w.step(&task.session_id).await.unwrap();
    assert_eq!(
        f.repo.read_owner(&shard).await.unwrap().unwrap().owner,
        node("dead")
    );
    f.clock.set(8000);
    gate.unknown.store(false, Ordering::SeqCst);
    w.step(&task.session_id).await.unwrap();
    assert_eq!(
        f.repo.read_owner(&shard).await.unwrap().unwrap().owner,
        node("new")
    );
}
#[tokio::test]
async fn poison_quarantine_is_durable_across_worker_incarnations() {
    use bcn_runtime_api::core::SessionRunner;
    use bcn_runtime_app::DefaultWorker;
    struct Poison;
    impl SessionRunner for Poison {
        fn kind(&self) -> RunnerKind {
            RunnerKind::DirectA2a
        }
        fn validate(&self, s: &SessionSpec) -> RuntimeResult<()> {
            bcn_session_runner::DirectA2aRunner.validate(s)
        }
        fn initialize(&self, s: &SessionSpec) -> RuntimeResult<RunnerState> {
            bcn_session_runner::DirectA2aRunner.initialize(s)
        }
        fn reduce(&self, _: &RunnerState, _: &SessionInput) -> RuntimeResult<Transition> {
            Err(RuntimeError::InvalidInput("poison fixture".into()))
        }
    }
    let f = Fixture::new();
    let task = f
        .gateway()
        .submit(caller(), request("poison"))
        .await
        .unwrap();
    let first = DefaultWorker::new(
        node("p0"),
        f.ports.clone(),
        vec![Arc::new(Poison)],
        Arc::new(NoJudge),
        f.clock.clone(),
    )
    .unwrap();
    assert!(first.step(&task.session_id).await.is_err());
    for n in 1..=3 {
        let current = f
            .repo
            .read_owner(&session_shard(&task.session_id))
            .await
            .unwrap()
            .unwrap();
        f.repo
            .transfer_shard(current, node(&format!("p{n}")))
            .await
            .unwrap();
        let w = DefaultWorker::new(
            node(&format!("p{n}")),
            f.ports.clone(),
            vec![Arc::new(Poison)],
            Arc::new(NoJudge),
            f.clock.clone(),
        )
        .unwrap();
        assert!(w.step(&task.session_id).await.is_err());
    }
    let recovery = f
        .repo
        .recovery_status(&task.session_id)
        .await
        .unwrap()
        .unwrap();
    assert!(recovery.quarantined);
    assert_eq!(recovery.takeovers, 3);
    assert_eq!(
        f.repo
            .load(&task.session_id)
            .await
            .unwrap()
            .unwrap()
            .consumed_input,
        0
    );
    assert_eq!(
        f.repo
            .read_batch(&task.session_id, 0, 10)
            .await
            .unwrap()
            .len(),
        1
    );
}
#[tokio::test]
async fn restored_deadline_uses_original_absolute_time_after_restart() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("deadline")).await.unwrap();
    let w = f.worker("w");
    w.step(&task.session_id).await.unwrap();
    let cp = f.repo.load(&task.session_id).await.unwrap().unwrap();
    assert_eq!(cp.deadlines[0].at_ms, 61000);
    let command = f
        .repo
        .receive(
            &scope(),
            &node("connector"),
            CommandConsumerKind::Connector,
            None,
            1,
        )
        .await
        .unwrap()
        .items
        .remove(0);
    let r = f
        .repo
        .claim(&command.command_id, node("connector"))
        .await
        .unwrap();
    let r = f
        .repo
        .mark_may_have_sent(&r.fence, r.revision)
        .await
        .unwrap();
    let cursor = f
        .repo
        .read_snapshot(&task.task_id)
        .await
        .unwrap()
        .unwrap()
        .cursor;
    f.repo
        .publish_reset(
            &r.fence,
            ConnectorProjection {
                task_id: task.task_id.clone(),
                output: text("partial before timeout"),
                expected_cursor: cursor,
            },
            EventId::new(scope(), "partial"),
        )
        .await
        .unwrap();
    f.repo
        .transfer_shard(cp.fence, node("restarted"))
        .await
        .unwrap();
    f.clock.set(70000);
    let restarted = f.worker("restarted");
    restarted.step(&task.session_id).await.unwrap();
    restarted.tick().await.unwrap();
    restarted.step(&task.session_id).await.unwrap();
    let failed = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(failed.status, TaskStatus::Failed);
    assert_eq!(failed.error, Some(RuntimeError::ExecutionUnknown));
    assert_eq!(failed.output, text("partial before timeout"));
}
