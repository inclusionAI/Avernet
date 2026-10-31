use super::*;

#[tokio::test]
async fn disconnect_fails_only_matching_delivered_attempt_and_ignores_late_result() {
    let (h, _) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let run = started.view.run;
    let node = h.store.list_node_runs(&run.run_id).await.unwrap().into_iter()
        .find(|n| n.status == StateMachineNodeStatus::Running).unwrap();
    let bot = node.assignee_bot_id.as_deref().unwrap();
    h.runtime.bot_websocket_disconnected("unrelated-bot", bcs_protocol::now_ms()).await.unwrap();
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    h.runtime.bot_websocket_disconnected(bot, bcs_protocol::now_ms()).await.unwrap();
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Failed);
    let failed = h.store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap();
    assert_eq!(failed.status, StateMachineNodeStatus::Failed);
    h.runtime.bot_websocket_disconnected(bot, bcs_protocol::now_ms()).await.unwrap();
    terminal(&h, 0, &run, "late result").await.unwrap();
    assert!(h.store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap().artifact_text.is_none());
}

#[tokio::test]
async fn disconnect_does_not_fail_unsent_or_future_attempts() {
    let (h, runs) = harness(&[]).await;
    runs.fail_dispatch_claim.store(true, Ordering::SeqCst);
    assert!(h.runtime.start_state_machine_run(command(loop_yaml(2, false, false, 1), false)).await.is_err());
    let run = h.store.list_running_runs(None, 1).await.unwrap().remove(0);
    let node = h.store.list_node_runs(&run.run_id).await.unwrap().into_iter().find(|n| n.status == StateMachineNodeStatus::Running).unwrap();
    let bot = node.assignee_bot_id.as_deref().unwrap();
    h.runtime.bot_websocket_disconnected(bot, node.started_at.unwrap().saturating_sub(1)).await.unwrap();
    h.runtime.bot_websocket_disconnected(bot, bcs_protocol::now_ms()).await.unwrap();
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
}

#[tokio::test]
async fn websocket_disconnect_does_not_fail_http_provider_attempt() {
    let (h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let run = started.view.run;
    let node = h.store.list_node_runs(&run.run_id).await.unwrap().into_iter()
        .find(|n| n.status == StateMachineNodeStatus::Running).unwrap();
    runs.dispatch_is_http.store(true, Ordering::SeqCst);
    h.runtime.bot_websocket_disconnected(node.assignee_bot_id.as_deref().unwrap(), bcs_protocol::now_ms()).await.unwrap();
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
}

#[tokio::test]
async fn disconnect_terminal_write_failure_is_recovered_by_progression_scanner() {
    let (h, runs) = harness(&[]).await;
    let started = h.start(loop_yaml(2, false, false, 1), false).await;
    let run = started.view.run;
    let node = h.store.list_node_runs(&run.run_id).await.unwrap().into_iter()
        .find(|n| n.status == StateMachineNodeStatus::Running).unwrap();
    runs.fail_run_failure.store(true, Ordering::SeqCst);
    assert!(h.runtime.bot_websocket_disconnected(node.assignee_bot_id.as_deref().unwrap(), bcs_protocol::now_ms()).await.is_err());
    assert_eq!(h.store.get_node_run(&run.run_id, &node.node_id).await.unwrap().unwrap().status, StateMachineNodeStatus::Failed);
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Running);
    recover(&h).await;
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Failed);
}
