use super::*;
use bcs_event_store::MemoryEventStore;
use bcs_service_api::{FinishStateMachineJudge, StateMachineJudgeClaim, StateMachineJudgeResult, StateMachineFailureAction, JudgeDecision};

fn decision() -> JudgeDecision {
    JudgeDecision { outcome: "done".into(), reason: "saved".into(), confidence: 1.0,
        checked_criteria: Vec::new(), retry_instruction: String::new(), raw_response: None }
}

fn finish(claim: StateMachineJudgeClaim, at: u64) -> StateMachineEventfulTransition {
    StateMachineEventfulTransition::FinishJudge(FinishStateMachineJudge {
        claim, result: StateMachineJudgeResult::Completed(decision()), completed_at_ms: at, event: None,
    })
}

fn lost(result: bcs_service_api::ServiceResult<bool>) {
    assert!(matches!(result, Ok(false) | Err(bcs_service_api::ServiceError::Conflict(_))), "{result:?}");
}

pub(super) async fn judge_contract(store: &dyn StateMachineRunRepoPort, audit: &dyn CollaborationEventRepoPort) {
    let mut run = test_run();
    run.run_id = "judge-contract".into();
    run.status = StateMachineRunStatus::Running;
    let nodes = ["result", "retry", "legacy", "cancel"].into_iter().map(|node| {
        serde_json::from_value::<StateMachineNodeRun>(json!({"run_id": run.run_id, "node_id": node,
            "status": "running", "attempt": 0, "max_attempts": 3})).unwrap()
    }).collect();
    store.create_run(run, nodes).await.unwrap();
    let run = "judge-contract";
    assert!(store.claim_node_judging(run, "result", 0, "empty".into(), 10, 30).await.unwrap().is_none());
    assert!(store.begin_node_judging(run, "result", 0, "Original".into(), Some("human".into())).await.unwrap());
    for (artifact, responder) in [("original", Some("human")), ("Original ", Some("human")), ("Original", None)] {
        assert!(!store.begin_node_judging(run, "result", 0, artifact.into(), responder.map(str::to_string)).await.unwrap());
    }
    assert!(!store.begin_node_judging(run, "result", 1, "Original".into(), Some("human".into())).await.unwrap());
    assert!(store.begin_node_judging(run, "result", 0, "Original".into(), Some("human".into())).await.unwrap());
    let (a, b) = tokio::join!(
        store.claim_node_judging(run, "result", 0, "a".into(), 10, 30),
        store.claim_node_judging(run, "result", 0, "b".into(), 10, 30));
    let owners: Vec<_> = [a.unwrap(), b.unwrap()].into_iter().flatten().collect();
    assert_eq!(owners.len(), 1);
    let old = owners[0].clone();
    assert!(!store.record_node_artifact_if_running(run, "result", 0, "overwrite".into()).await.unwrap());
    assert!(!store.record_human_response_if_running(run, "result", 0, "overwrite".into(), "other".into()).await.unwrap());
    assert!(!store.record_human_response_if_running(run, "result", 0, "Original".into(), "human".into()).await.unwrap());
    assert!(!store.complete_node_attempt(run, "result", 0, "done".into(), "overwrite".into(), None, 20).await.unwrap());
    lost(store.commit_eventful_transition(finish(old.clone(), 30)).await);
    let new = store.claim_node_judging(run, "result", 0, "new".into(), 30, 60).await.unwrap().unwrap();
    assert!(new.token > old.token);
    assert!(!store.release_node_judging(&old).await.unwrap());
    lost(store.commit_eventful_transition(finish(old, 31)).await);
    assert!(store.commit_eventful_transition(finish(new.clone(), 40)).await.unwrap());
    lost(store.commit_eventful_transition(finish(new.clone(), 41)).await);
    assert!(!store.release_node_judging(&new).await.unwrap());
    let saved = store.get_node_run(run, "result").await.unwrap().unwrap();
    assert_eq!(saved.artifact_text.as_deref(), Some("Original"));
    assert_eq!(saved.responded_by.as_deref(), Some("human"));
    assert_eq!(saved.outcome.as_deref(), Some("done"));
    assert_eq!(saved.completed_at, Some(40));
    assert_eq!(audit.list_events_by_run_and_type(run, "state_machine.judge.completed").await.unwrap().len(), 1);

    assert!(store.begin_node_judging(run, "retry", 0, "first".into(), None).await.unwrap());
    let failed = store.claim_node_judging(run, "retry", 0, "fail".into(), 10, 30).await.unwrap().unwrap();
    assert!(store.commit_eventful_transition(StateMachineEventfulTransition::FinishJudge(FinishStateMachineJudge {
        claim: failed.clone(), completed_at_ms: 20, event: None, result: StateMachineJudgeResult::Failed {
            error: "judge failed".into(), action: StateMachineFailureAction::Retry, details: json!({"reason": "judge_failed"}),
        },
    })).await.unwrap());
    assert_eq!(store.get_node_attempt_failure(run, "retry", 0).await.unwrap().unwrap().action, Some(StateMachineFailureAction::Retry));
    assert!(store.schedule_node_retry(run, "retry", 0, 1).await.unwrap());
    assert!(store.mark_node_running_if_run_active(run, "retry", 1, "retry-delivery".into(), 21).await.unwrap());
    assert!(store.claim_node_judging(run, "retry", 1, "premature".into(), 21, 60).await.unwrap().is_none());
    assert!(store.begin_node_judging(run, "retry", 1, "second".into(), None).await.unwrap());
    let retried = store.claim_node_judging(run, "retry", 1, "new-attempt".into(), 22, 60).await.unwrap().unwrap();
    assert!(retried.token > failed.token);
    lost(store.commit_eventful_transition(finish(failed, 23)).await);
    assert!(store.release_node_judging(&retried).await.unwrap());
    let released = store.claim_node_judging(run, "retry", 1, "after-release".into(), 24, 60).await.unwrap().unwrap();
    assert!(released.token > retried.token);
    // Artifact-only historical rows are not evidence for Judge takeover.
    store.record_node_artifact_if_running(run, "legacy", 0, "legacy".into()).await.unwrap();
    assert!(store.claim_node_judging(run, "legacy", 0, "legacy".into(), 20, 60).await.unwrap().is_none());
    assert!(!store.begin_node_judging(run, "legacy", 0, "legacy".into(), None).await.unwrap());
    store.begin_node_judging(run, "cancel", 0, "cancel".into(), None).await.unwrap();
    let cancel = store.claim_node_judging(run, "cancel", 0, "cancel".into(), 10, 60).await.unwrap().unwrap();
    store.update_run_status(run, StateMachineRunStatus::Aborted, None, None, 25, Some(25)).await.unwrap();
    lost(store.commit_eventful_transition(finish(cancel, 26)).await);
    assert!(store.claim_node_judging(run, "retry", 1, "cancelled".into(), 60, 90).await.unwrap().is_none());
}

#[tokio::test]
async fn memory_judge_input_lease_and_fencing_contract() {
    let store = MemoryCollaborationStore::new();
    judge_contract(&store, &store).await;
}

#[tokio::test]
async fn sqlite_judge_input_lease_and_fencing_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db, "test".into());
    judge_contract(&store, &store).await;
}

#[tokio::test]
async fn sqlite_judge_result_audit_and_public_event_commit_atomically() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    let mut run = test_run(); run.status = StateMachineRunStatus::Running;
    let id = run.run_id.clone();
    let node = serde_json::from_value(json!({"run_id": id, "node_id": "judge", "status": "running", "attempt": 0})).unwrap();
    store.create_run(run, vec![node]).await.unwrap();
    store.begin_node_judging(&id, "judge", 0, "saved".into(), None).await.unwrap();
    let claim = store.claim_node_judging(&id, "judge", 0, "owner".into(), 10, 100).await.unwrap().unwrap();
    let command = StateMachineEventfulTransition::FinishJudge(FinishStateMachineJudge {
        claim: claim.clone(), result: StateMachineJudgeResult::Completed(decision()), completed_at_ms: 30,
        event: Some(public_event("judge-completion", "state_machine.node.completed", None)),
    });
    db.execute(DbStatement::new("CREATE TRIGGER fail_judge_audit BEFORE INSERT ON bcs_collaboration_events BEGIN SELECT RAISE(ABORT, 'audit failure'); END")).await.unwrap();
    assert!(store.commit_eventful_transition(command.clone()).await.is_err());
    assert_eq!(store.get_node_run(&id, "judge").await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
    assert!(DbEventStore::sqlite(db.clone()).get_event("judge-completion", "test").await.unwrap().is_none());
    db.execute(DbStatement::new("DROP TRIGGER fail_judge_audit")).await.unwrap();
    assert!(store.commit_eventful_transition(command).await.unwrap());
    assert_eq!(store.list_events_by_run_and_type(&id, "state_machine.judge.completed").await.unwrap().len(), 1);
    assert!(DbEventStore::sqlite(db).get_event("judge-completion", "test").await.unwrap().is_some());
}

#[tokio::test]
async fn memory_judge_public_event_failure_leaves_input_and_lease_intact() {
    // A missing event store must not accidentally commit the Node or audit.
    let store = MemoryCollaborationStore::new();
    let mut run = test_run(); run.status = StateMachineRunStatus::Running;
    let id = run.run_id.clone();
    store.create_run(run, vec![serde_json::from_value(json!({"run_id": id, "node_id": "judge", "status": "running", "attempt": 0})).unwrap()]).await.unwrap();
    store.begin_node_judging(&id, "judge", 0, "saved".into(), None).await.unwrap();
    let claim = store.claim_node_judging(&id, "judge", 0, "owner".into(), 10, 100).await.unwrap().unwrap();
    let command = StateMachineEventfulTransition::FinishJudge(FinishStateMachineJudge {
        claim, result: StateMachineJudgeResult::Completed(decision()), completed_at_ms: 30,
        event: Some(public_event("judge-memory", "state_machine.node.completed", None)),
    });
    assert!(store.commit_eventful_transition(command.clone()).await.is_err());
    assert_eq!(store.get_node_run(&id, "judge").await.unwrap().unwrap().status, StateMachineNodeStatus::Running);
    assert!(store.list_events_by_run_and_type(&id, "state_machine.judge.completed").await.unwrap().is_empty());
    assert!(store.with_event_store(Arc::new(MemoryEventStore::new())).commit_eventful_transition(command).await.unwrap());
}
