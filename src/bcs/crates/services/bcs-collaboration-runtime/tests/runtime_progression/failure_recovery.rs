use super::*;
use bcs_service_api::StateMachineFailureAction;

async fn fail_terminal(h: &Harness, index: usize, run: &StateMachineRun) -> Result<bcs_service_api::HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
    let id = h.delivery.commands.lock().await[index].run_id.clone();
    h.runtime.handle_bot_terminal_event(bcs_service_api::HandleBotTerminalEventCommand {
        bot_id: "driver-bot".into(), run_id: id.clone(), event_type: "chat.event".into(),
        event_payload: json!({"run_id": id, "state": "error", "message": {"content": [{"type": "text", "text": "durable failure"}]}}),
        state: ChatEventState::Error, bcs_session_id: Some(run.session_id.clone()),
    }).await
}

#[tokio::test]
async fn saved_retry_recovers_same_loop_iteration_once_and_preserves_previous_result() {
    let (mut h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(3, false, false, 3), false).await;
    let run = &started.view.run;
    h.finish(0, run, "previous immutable result").await;
    let plan = h.plan(&run.run_id).await;
    let node_id = iteration_id(&plan, 2);
    runs.fail_retry.store(true, Ordering::SeqCst);
    assert!(fail_terminal(&h, 1, run).await.is_err());
    let failed = h.store.get_node_attempt_failure(&run.run_id, &node_id, 0).await.unwrap().unwrap();
    assert_eq!(failed.action, Some(StateMachineFailureAction::Retry));
    assert_eq!(failed.node.error.as_deref(), Some("durable failure"));
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    install_runtime(&mut h, runs, &[]).await;
    h.runtime = h.runtime.with_fixed_loop_limits(bcs_config_api::FixedLoopLimits { max_fixed_loop_iterations: 1, ..Default::default() });
    let (a, b) = tokio::join!(h.runtime.recover_state_machine_progression(None, 32), h.runtime.recover_state_machine_progression(None, 32));
    assert!(a.unwrap().failures.is_empty());
    assert!(b.unwrap().failures.is_empty());
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 3);
    assert!(h.prompt(2).await.contains("iteration: 2"));
    assert_eq!(h.prompt(2).await.matches("previous immutable result").count(), 1);
    let node = h.store.get_node_run(&run.run_id, &node_id).await.unwrap().unwrap();
    assert_eq!((node.status, node.attempt), (StateMachineNodeStatus::Running, 1));
    // A late event for the original delivery cannot fail the new attempt.
    fail_terminal(&h, 1, run).await.unwrap();
    assert_eq!(h.delivery.commands.lock().await.len(), 3);
    h.finish(2, run, "second result").await;
    let next = h.store.get_node_run(&run.run_id, &iteration_id(&plan, 3)).await.unwrap().unwrap();
    assert_eq!((next.status, next.attempt), (StateMachineNodeStatus::Running, 0));
}

#[tokio::test]
async fn exhausted_failure_recovers_run_and_session_without_retrying_bot() {
    let (mut h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let run = &started.view.run;
    runs.fail_run_failure.store(true, Ordering::SeqCst);
    assert!(fail_terminal(&h, 0, run).await.is_err());
    let node_id = iteration_id(&h.plan(&run.run_id).await, 1);
    let saved = h.store.get_node_attempt_failure(&run.run_id, &node_id, 0).await.unwrap().unwrap();
    assert_eq!(saved.action, Some(StateMachineFailureAction::FailRun));
    install_runtime(&mut h, runs, &[]).await;
    recover(&h).await;
    recover(&h).await;
    let after = h.store.get_run(&run.run_id).await.unwrap().unwrap();
    assert_eq!(after.status, StateMachineRunStatus::Failed);
    assert_eq!(after.error.as_deref(), Some("durable failure"));
    assert_eq!(h.sessions.get(&run.session_id).await.unwrap().unwrap().status, SessionStatus::Completed);
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(serde_json::to_value(saved.node).unwrap(), serde_json::to_value(h.store.get_node_run(&run.run_id, &node_id).await.unwrap().unwrap()).unwrap());
}

#[tokio::test]
async fn rejected_dispatch_stays_fatal_even_with_remaining_attempts() {
    let (mut h, runs) = harness(&[]).await;
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.unwrap();
    let rejecting = Arc::new(RejectingDelivery::default());
    h.runtime = test_runtime!(h.definitions.clone(), h.store.clone(), runs.clone(), h.store.clone(), group,
        h.sessions.clone(), rejecting.clone(), Arc::new(SequencedJudge::new(Vec::new())))
        .with_experimental_fixed_loop_execution();
    runs.fail_run_failure.store(true, Ordering::SeqCst);
    assert!(h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 3), false)).await.is_err());
    let run = h.store.list_running_runs(None, 1).await.unwrap().remove(0);
    let node_id = iteration_id(&h.plan(&run.run_id).await, 1);
    assert_eq!(h.store.get_node_attempt_failure(&run.run_id, &node_id, 0).await.unwrap().unwrap().action, Some(StateMachineFailureAction::FailRun));
    install_runtime(&mut h, runs, &[]).await;
    recover(&h).await;
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Failed);
    assert!(h.delivery.commands.lock().await.is_empty());
    assert_eq!(rejecting.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn failure_write_errors_propagate_and_legacy_failure_is_not_guessed() {
    let (h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 3), false).await;
    let run = &started.view.run;
    let node_id = iteration_id(&h.plan(&run.run_id).await, 1);
    runs.fail_failure_write.store(true, Ordering::SeqCst);
    assert!(fail_terminal(&h, 0, run).await.is_err());
    assert_eq!(h.store.get_node_run(&run.run_id, &node_id).await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
    h.store.fail_node_attempt(&run.run_id, &node_id, 0, "legacy failure".into(), 100).await.unwrap();
    let page = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert_eq!(page.failures.len(), 1);
    assert!(page.failures[0].error.contains("no saved failure action"));
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
}

#[tokio::test]
async fn failed_retry_recovery_obeys_cancellation_cas() {
    let (h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 3), false).await;
    let run = &started.view.run;
    runs.fail_retry.store(true, Ordering::SeqCst);
    assert!(fail_terminal(&h, 0, run).await.is_err());
    runs.cancel_before_dispatch.store(true, Ordering::SeqCst);
    recover(&h).await;
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Aborted);
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn saved_judge_and_empty_output_failures_resume_without_rejudging() {
    for judged in [false, true] {
        let (mut h, runs) = harness(if judged { &["invalid"] } else { &[] }).await;
        let started = h.start(loop_yaml(2, judged, false, 3), false).await;
        let run = &started.view.run;
        runs.fail_retry.store(true, Ordering::SeqCst);
        assert!(terminal(&h, 0, run, if judged { "saved artifact" } else { "" }).await.is_err());
        let node_id = iteration_id(&h.plan(&run.run_id).await, 1);
        let saved = h.store.get_node_attempt_failure(&run.run_id, &node_id, 0).await.unwrap().unwrap();
        assert_eq!(saved.action, Some(StateMachineFailureAction::Retry));
        if judged { assert_eq!(saved.node.artifact_text.as_deref(), Some("saved artifact")); }
        install_runtime(&mut h, runs, &[]).await;
        recover(&h).await;
        assert_eq!(h.delivery.commands.lock().await.len(), 2);
        assert_eq!(h.store.get_node_run(&run.run_id, &node_id).await.unwrap().unwrap().attempt, 1);
    }
}
