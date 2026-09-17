use super::*;
use bcs_service_api::{StateMachineDispatchStatus as Status, CancelStateMachineRunCommand};

async fn first(h: &Harness) -> (StateMachineRun, StateMachineNodeRun) {
    let run = h.store.list_running_runs(None, 1).await.unwrap().remove(0);
    let node = h.store.list_node_runs(&run.run_id).await.unwrap().into_iter().find(|node| node.status == StateMachineNodeStatus::Running).unwrap();
    (run, node)
}

#[tokio::test]
async fn unsent_dispatch_recovers_original_request_once_without_recompile() {
    for before_claim in [true, false] {
        let (mut h, runs) = harness(&[]).await;
        if before_claim { runs.fail_dispatch_claim.store(true, Ordering::SeqCst); }
        else { runs.fail_send_marker.store(true, Ordering::SeqCst); }
        assert!(h.runtime.start_state_machine_run(command(loop_yaml(3, false, false, 1), false)).await.is_err());
        let (run, node) = first(&h).await;
        let saved = h.store.get_node_dispatch(&run.run_id, &node.node_id, node.attempt).await.unwrap().unwrap();
        assert_eq!(saved.status, Status::Pending); assert!(saved.lease_owner.is_none());
        assert!(h.delivery.commands.lock().await.is_empty());
        let group = Arc::new(GroupStore::new()); let mut changed = test_group(); changed.label = Some("changed Group".into());
        group.upsert(changed).await.unwrap();
        h.definitions.hide_definitions.store(true, Ordering::SeqCst);
        h.runtime = CollaborationRuntime::new(h.definitions.clone(), h.store.clone(), runs, h.store.clone(), group,
            h.sessions.clone(), h.delivery.clone(), noop_judge()).with_experimental_fixed_loop_execution()
            .with_fixed_loop_limits(bcs_config_api::FixedLoopLimits { max_fixed_loop_iterations: 1, ..Default::default() });
        let (a, b) = tokio::join!(h.runtime.recover_state_machine_progression(None, 32), h.runtime.recover_state_machine_progression(None, 32));
        assert!(a.unwrap().failures.is_empty()); assert!(b.unwrap().failures.is_empty());
        recover(&h).await;
        let deliveries = h.delivery.commands.lock().await;
        assert_eq!(deliveries.len(), 1);
        assert_eq!(serde_json::to_value(&deliveries[0].frame).unwrap(), saved.payload.request);
        assert_eq!(deliveries[0].run_id, saved.payload.delivery_request_id);
        assert_eq!(h.store.get_node_dispatch(&run.run_id, &node.node_id, node.attempt).await.unwrap().unwrap().status, Status::Delivered);
    }
}

#[tokio::test]
async fn accepted_dispatch_with_lost_checkpoint_write_is_not_resent() {
    let (h, runs) = harness(&[]).await;
    runs.fail_dispatch_finish.store(true, Ordering::SeqCst);
    assert!(h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)).await.is_err());
    let (run, node) = first(&h).await;
    let saved = h.store.get_node_dispatch(&run.run_id, &node.node_id, node.attempt).await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Delivering); assert!(saved.lease_owner.is_none());
    recover(&h).await; recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(h.store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap().attempt, 0);
    terminal(&h, 0, &run, "accepted result").await.unwrap();
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    let saved = h.store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap();
    assert_eq!(saved.status, Status::Superseded);
}

#[tokio::test]
async fn crash_after_send_marker_waits_even_if_io_never_started() {
    let (h, runs) = harness(&[]).await;
    runs.pause_after_send_marker.store(true, Ordering::SeqCst);
    tokio::select! {
        result = h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)) => panic!("did not pause: {result:?}"),
        () = runs.startup_paused.notified() => {}
    }
    let (run, node) = first(&h).await;
    assert_eq!(h.store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap().status, Status::Delivering);
    recover(&h).await; recover(&h).await;
    assert!(h.delivery.commands.lock().await.is_empty());
    h.runtime.cancel_state_machine_run(CancelStateMachineRunCommand { run_id: run.run_id.clone(), reason: None }).await.unwrap();
    assert_eq!(h.store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap().status, Status::Superseded);
}

#[tokio::test]
async fn missing_dispatch_payload_is_diagnosed_without_guessing_or_sending() {
    let (h, runs) = harness(&[]).await;
    runs.fail_dispatch_save.store(true, Ordering::SeqCst);
    assert!(h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)).await.is_err());
    let page = h.runtime.recover_state_machine_progression(None, 32).await.unwrap();
    assert_eq!(page.failures.len(), 1); assert!(page.failures[0].error.contains("no persisted dispatch payload"));
    assert!(h.delivery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn ambiguous_dispatch_deadline_uses_original_attempt_retry_policy() {
    let (mut h, runs) = harness(&[]).await;
    // No configured Node timeout: only the original dispatch ambiguity deadline
    // applies, and accepted nodes retain their existing disabled timeout policy.
    h.runtime = h.runtime.with_provider_chat_run_timeout_ms(1000);
    runs.fail_dispatch_finish.store(true, Ordering::SeqCst);
    assert!(h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 2), false)).await.is_err());
    let (run, node) = first(&h).await;
    assert!(node.timeout_deadline_ms.is_none());
    let before = h.store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap();
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    h.runtime = h.runtime.with_provider_chat_run_timeout_ms(60_000);
    let remaining = before.payload.deadline_ms.saturating_sub(bcs_protocol::now_ms());
    tokio::time::sleep(Duration::from_millis(remaining + 10)).await;
    let (a, b) = tokio::join!(h.runtime.recover_state_machine_progression(None, 32), h.runtime.recover_state_machine_progression(None, 32));
    assert!(a.unwrap().failures.is_empty()); assert!(b.unwrap().failures.is_empty());
    recover(&h).await;
    let retried = h.store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap();
    assert_eq!(retried.attempt, 1); assert_eq!(retried.status, StateMachineNodeStatus::Running);
    assert!(retried.timeout_deadline_ms.is_none());
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    assert_eq!(h.store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap().status, Status::Superseded);
    // Late old-attempt result cannot complete the new attempt.
    terminal(&h, 0, &run, "late old result").await.unwrap();
    assert_eq!(h.store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap().attempt, 1);
    assert!(h.store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap().artifact_text.is_none());
}

#[tokio::test]
async fn saved_rejection_recovers_failure_without_retrying_bot() {
    let (mut h, runs) = harness(&[]).await;
    let rejected = Arc::new(RejectingDelivery::default());
    let group = Arc::new(GroupStore::new()); group.upsert(test_group()).await.unwrap();
    h.runtime = CollaborationRuntime::new(h.definitions.clone(), h.store.clone(), runs.clone(), h.store.clone(), group,
        h.sessions.clone(), rejected.clone(), noop_judge()).with_experimental_fixed_loop_execution()
        .with_message_repo(Arc::new(MemoryMessageRepo::new()));
    runs.fail_failure_write.store(true, Ordering::SeqCst);
    assert!(h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 3), false)).await.is_err());
    let (run, node) = first(&h).await;
    let failed = h.store.get_node_dispatch(&run.run_id, &node.node_id, 0).await.unwrap().unwrap();
    assert_eq!(failed.status, Status::Failed);
    recover(&h).await; recover(&h).await;
    let result = h.store.get_run(&run.run_id).await.unwrap().unwrap();
    assert_eq!(result.status, StateMachineRunStatus::Failed); assert_eq!(result.error, failed.error);
    assert_eq!(rejected.commands.lock().await.len(), 1);
    assert_eq!(h.store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap().attempt, 0);
}

#[tokio::test]
async fn accepted_dispatch_keeps_disabled_node_timeout_policy() {
    let (mut h, _) = harness(&[]).await;
    h.runtime = h.runtime.with_provider_chat_run_timeout_ms(1000);
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let node = started.view.nodes.iter().find(|node| node.status == StateMachineNodeStatus::Running).unwrap();
    assert!(node.timeout_deadline_ms.is_none());
    let saved = h.store.get_node_dispatch(&started.view.run.run_id, &node.node_id, 0).await.unwrap().unwrap();
    let remaining = saved.payload.deadline_ms.saturating_sub(bcs_protocol::now_ms());
    tokio::time::sleep(Duration::from_millis(remaining + 10)).await;
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert_eq!(h.store.get_node_run(&started.view.run.run_id, &node.node_id).await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
}
