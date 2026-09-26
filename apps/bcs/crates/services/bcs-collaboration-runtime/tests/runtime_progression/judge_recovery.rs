use super::*;
use bcs_service_api::CollaborationEventRepoPort;

async fn install_judge(h: &mut Harness, runs: Arc<dyn StateMachineRunRepoPort>, judge: Arc<dyn JudgeEvaluatorPort>) {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.unwrap();
    h.runtime = test_runtime!(h.definitions.clone(), h.store.clone(), runs, h.store.clone(), group,
        h.sessions.clone(), h.delivery.clone(), judge)
        .with_session_channel_outbound(h.channel.clone()).with_loop_execution();
}

#[tokio::test]
async fn judge_recovers_saved_bot_input_without_new_attempt_or_bot_dispatch() {
    let (h, runs) = harness(&["again"]).await;
    let run = h.start(loop_yaml(3, true, false, 2), false).await.view.run;
    let plan = h.plan(&run.run_id).await;
    let id = iteration_id(&plan, 1);
    runs.fail_judge_claim.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &run, "persisted judge input").await.is_err());
    let saved = h.store.get_node_run(&run.run_id, &id).await.unwrap().unwrap();
    assert_eq!(saved.status, StateMachineNodeStatus::Running);
    assert_eq!(saved.artifact_text.as_deref(), Some("persisted judge input"));
    h.definitions.hide_definitions.store(true, Ordering::SeqCst);
    recover(&h).await;
    recover(&h).await;
    let completed = h.store.get_node_run(&run.run_id, &id).await.unwrap().unwrap();
    assert_eq!(completed.status, StateMachineNodeStatus::Completed);
    assert_eq!(completed.attempt, 0);
    assert_eq!(completed.outcome.as_deref(), Some("again"));
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    assert!(h.prompt(1).await.contains("iteration: 2"));
    assert!(h.prompt(1).await.contains("persisted judge input"));
    assert_eq!(h.store.list_events_by_run_and_type(&run.run_id, "state_machine.judge.completed").await.unwrap().len(), 1);
}

#[tokio::test]
async fn judge_recovers_saved_human_response_and_responder() {
    let (h, runs) = harness(&["again"]).await;
    let run = h.start(loop_yaml(3, true, true, 1), true).await.view.run;
    let id = iteration_id(&h.plan(&run.run_id).await, 1);
    runs.fail_judge_claim.store(true, Ordering::SeqCst);
    assert!(h.runtime.respond_human_node(RespondHumanNodeCommand {
        run_id: run.run_id.clone(), node_id: id.clone(), caller_actor_id: "human_1001".into(),
        content: "human immutable answer".into(), source: HumanResponseSource::Http,
    }).await.is_err());
    recover(&h).await;
    let node = h.store.get_node_run(&run.run_id, &id).await.unwrap().unwrap();
    assert_eq!(node.status, StateMachineNodeStatus::Completed);
    assert_eq!(node.responded_by.as_deref(), Some("human_1001"));
    assert_eq!(node.artifact_text.as_deref(), Some("human immutable answer"));
    let second = iteration_id(&h.plan(&run.run_id).await, 2);
    assert_eq!(h.store.get_node_run(&run.run_id, &second).await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
    assert!(h.delivery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn foreground_and_recovery_share_one_judge_claim_and_reject_changed_input() {
    let (mut h, runs) = harness(&[]).await;
    let judge = Arc::new(BlockingJudge::new("again"));
    install_judge(&mut h, runs, judge.clone()).await;
    let run = h.start(loop_yaml(3, true, false, 2), false).await.view.run;
    let (outcome, ()) = tokio::join!(terminal(&h, 0, &run, "first input"), async {
        judge.started.notified().await;
        recover(&h).await;
        terminal(&h, 0, &run, "changed duplicate").await.unwrap();
        assert_eq!(judge.requests.lock().await.len(), 1);
        assert_eq!(h.delivery.commands.lock().await.len(), 1);
        judge.release.notify_one();
    });
    outcome.unwrap();
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    assert!(h.prompt(1).await.contains("first input"));
    assert!(!h.prompt(1).await.contains("changed duplicate"));
}

#[tokio::test]
async fn expired_judge_owner_cannot_commit_after_takeover() {
    let (mut h, runs) = harness(&[]).await;
    let judge = Arc::new(BlockingJudge::new("done"));
    install_judge(&mut h, runs.clone(), judge.clone()).await;
    let run = h.start(loop_yaml(3, true, false, 2), false).await.view.run;
    let id = iteration_id(&h.plan(&run.run_id).await, 1);
    let (old, replacement) = tokio::join!(terminal(&h, 0, &run, "saved input"), async {
        judge.started.notified().await;
        // Advance the repository clock beyond the old lease, without waiting
        // for a real timeout or confusing a new attempt with takeover.
        let now = bcs_protocol::now_ms() + 1_000_000;
        let claim = h.store.claim_node_judging(&run.run_id, &id, 0, "replacement".into(), now, now + 100_000).await.unwrap().unwrap();
        judge.release.notify_one();
        claim
    });
    old.unwrap();
    assert_eq!(h.store.get_node_run(&run.run_id, &id).await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
    assert!(h.store.list_events_by_run_and_type(&run.run_id, "state_machine.judge.completed").await.unwrap().is_empty());
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    h.store.release_node_judging(&replacement).await.unwrap();
    install_runtime(&mut h, runs, &["again"]).await;
    recover(&h).await;
    assert!(h.prompt(1).await.contains("iteration: 2"));
}

#[tokio::test]
async fn judge_result_write_failure_releases_claim_and_retries_only_uncommitted_judge() {
    let (h, runs) = harness(&["done", "again"]).await;
    let run = h.start(loop_yaml(3, true, false, 2), false).await.view.run;
    runs.fail_judge_finish.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &run, "saved input").await.is_err());
    assert!(h.store.list_events_by_run_and_type(&run.run_id, "state_machine.judge.completed").await.unwrap().is_empty());
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    assert!(h.prompt(1).await.contains("iteration: 2"));
    let id = iteration_id(&h.plan(&run.run_id).await, 1);
    assert_eq!(h.store.get_node_run(&run.run_id, &id).await.unwrap().unwrap().attempt, 0);
}

#[tokio::test]
async fn recovered_judge_failure_saves_retry_decision_before_retry_write() {
    let (h, runs) = harness(&["invalid-outcome"]).await;
    let run = h.start(loop_yaml(3, true, false, 2), false).await.view.run;
    runs.fail_judge_claim.store(true, Ordering::SeqCst);
    assert!(terminal(&h, 0, &run, "saved input").await.is_err());
    runs.fail_retry.store(true, Ordering::SeqCst);
    assert_eq!(h.runtime.recover_state_machine_progression(None, 32).await.unwrap().failures.len(), 1);
    let id = iteration_id(&h.plan(&run.run_id).await, 1);
    let failed = h.store.get_node_attempt_failure(&run.run_id, &id, 0).await.unwrap().unwrap();
    assert_eq!(failed.action, Some(bcs_service_api::StateMachineFailureAction::Retry));
    recover(&h).await;
    assert_eq!(h.store.get_node_run(&run.run_id, &id).await.unwrap().unwrap().attempt, 1);
    assert_eq!(h.delivery.commands.lock().await.len(), 2);
    assert!(h.prompt(1).await.contains("iteration: 1"));
}

#[tokio::test]
async fn cancelled_run_rejects_inflight_judge_result_and_future_dispatch() {
    let (mut h, runs) = harness(&[]).await;
    let judge = Arc::new(BlockingJudge::new("again"));
    install_judge(&mut h, runs, judge.clone()).await;
    let run = h.start(loop_yaml(3, true, false, 2), false).await.view.run;
    let (finished, ()) = tokio::join!(terminal(&h, 0, &run, "saved input"), async {
        judge.started.notified().await;
        h.store.update_run_status(&run.run_id, StateMachineRunStatus::Aborted, None, None, 100, Some(100)).await.unwrap();
        judge.release.notify_one();
    });
    finished.unwrap();
    recover(&h).await;
    assert_eq!(h.delivery.commands.lock().await.len(), 1);
    assert!(h.store.list_events_by_run_and_type(&run.run_id, "state_machine.judge.completed").await.unwrap().is_empty());
    assert_eq!(h.store.get_run(&run.run_id).await.unwrap().unwrap().status, StateMachineRunStatus::Aborted);
}
