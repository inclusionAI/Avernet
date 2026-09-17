use super::*;
use bcs_service_api::{FailStateMachineStartup as Command, StateMachineMissingStartupFact as Missing,
    StateMachineFailureAction as Action, StateMachineOpeningPayload, StateMachineDispatchPayload, StateMachineDispatchTarget};

pub(super) async fn recovery_gap_contract(store: &dyn StateMachineRunRepoPort, definitions: &dyn StateMachineDefinitionRepoPort) {
    for (id, missing, repaired) in [("snapshot", Missing::Snapshot, false), ("opening", Missing::Opening, false),
        ("snapshot-repaired", Missing::Snapshot, true), ("opening-repaired", Missing::Opening, true), ("foreground", Missing::Snapshot, false)] {
        let mut run = test_run(); run.run_id = format!("gap-{id}"); run.session_id = format!("gap-session-{id}"); run.created_at = 100; run.status = StateMachineRunStatus::Pending;
        store.create_run(run.clone(), Vec::new()).await.unwrap();
        let mut command = Command { run_id: run.run_id.clone(), missing, failed_at_ms: 90_099, preparation_error: None };
        assert!(!store.fail_missing_startup(command.clone()).await.unwrap());
        assert!(store.get_startup_failure(&run.run_id).await.unwrap().is_none());
        if repaired {
            match missing {
                Missing::Snapshot => { let s = fixed_loop_snapshot_fixture(); definitions.save_run_snapshot(&run, 1, &s.definition, s.resolved_participant_bindings.as_ref(), s.execution_plan.as_ref()).await.unwrap(); }
                Missing::Opening => { assert!(store.save_run_opening(StateMachineOpeningPayload { run_id: run.run_id.clone(), group_id: run.group_id.clone(), session_id: run.session_id.clone(),
                    client_msg_id: format!("{}:000-panel", run.run_id), content: "original".into(), component: None, created_at_ms: 100 }).await.unwrap()); }
            }
        }
        command.failed_at_ms = if id == "foreground" { 101 } else { 90_100 };
        if id == "foreground" { command.preparation_error = Some("original write failure".into()); }
        let (a, b) = tokio::join!(store.fail_missing_startup(command.clone()), store.fail_missing_startup(command.clone()));
        assert_eq!(usize::from(a.unwrap()) + usize::from(b.unwrap()), usize::from(!repaired));
        let saved = store.get_run(&run.run_id).await.unwrap().unwrap();
        if repaired { assert_eq!(saved.status, StateMachineRunStatus::Pending); }
        else {
            assert_eq!(saved.status, StateMachineRunStatus::Failed);
            let fact = store.get_startup_failure(&run.run_id).await.unwrap().unwrap();
            assert_eq!(fact.session_activation_count, run.session_activation_count); assert_eq!(fact.missing, missing);
            assert_eq!(Some(fact.error), saved.error); assert_eq!(Some(fact.failed_at_ms), saved.completed_at);
            // A paused old creator cannot restart a failed Run or append its opening.
            assert!(!store.update_run_status(&run.run_id, StateMachineRunStatus::Running, None, None, 90_101, None).await.unwrap());
            assert!(!store.save_run_opening(StateMachineOpeningPayload { run_id: run.run_id.clone(), group_id: run.group_id.clone(), session_id: run.session_id.clone(),
                client_msg_id: format!("{}:000-panel", run.run_id), content: "late".into(), component: None, created_at_ms: 100 }).await.unwrap());
        }
    }
    for (id, timeout, max) in [("retry", Some(1000), 2), ("exhausted", Some(1000), 1), ("no-timeout", None, 3), ("legacy-no-start", None, 1), ("accepted", None, 1), ("repaired", None, 1), ("completed", None, 1), ("cancelled", None, 1)] {
        let mut run = test_run(); run.run_id = format!("gap-dispatch-{id}"); run.session_id = format!("gap-dispatch-session-{id}"); run.created_at = 100; run.status = StateMachineRunStatus::Running;
        let mut node = test_node(); node.run_id = run.run_id.clone(); node.node_id = "node".into(); node.attempt = 0;
        node.bot_delivery_run_id = None;
        node.status = StateMachineNodeStatus::Pending; node.node_timeout_ms = timeout; node.max_attempts = max; node.assignee_bot_id = Some("bot".into());
        if id == "legacy-no-start" { node.status = StateMachineNodeStatus::Running; node.started_at = None; node.timeout_deadline_ms = None; }
        if id == "accepted" { node.status = StateMachineNodeStatus::Running; node.started_at = Some(200); node.timeout_deadline_ms = None; node.bot_delivery_run_id = Some("original-provider-run".into()); }
        store.create_run(run.clone(), vec![node]).await.unwrap();
        let request = format!("smnode-{}-node-0", run.run_id);
        if !matches!(id, "legacy-no-start" | "accepted") { store.mark_node_running_if_run_active(&run.run_id, "node", 0, request.clone(), 200).await.unwrap(); }
        let deadline = if id == "legacy-no-start" { 90_100 } else { timeout.map_or(90_200, |t| 200 + t) };
        assert!(!store.fail_missing_dispatch(&run.run_id, "node", 0, deadline - 1).await.unwrap());
        let p = StateMachineDispatchPayload { run_id: run.run_id.clone(), node_id: "node".into(), attempt: 0, group_id: run.group_id.clone(), session_id: run.session_id.clone(),
            assignee_bot_id: "bot".into(), delivery_request_id: request.clone(), target: StateMachineDispatchTarget::WebSocket,
            request: json!({"id": request, "method": "chat.send"}), started_at_ms: 200, deadline_ms: deadline };
        if id == "repaired" { assert!(store.save_node_dispatch(p.clone()).await.unwrap()); }
        if id == "completed" { assert!(store.complete_node_attempt(&run.run_id, "node", 0, "done".into(), "original".into(), None, 201).await.unwrap()); }
        if id == "cancelled" { store.update_run_status(&run.run_id, StateMachineRunStatus::Aborted, None, None, 201, Some(201)).await.unwrap(); }
        let expected = matches!(id, "retry" | "exhausted" | "no-timeout" | "legacy-no-start");
        let (a, b) = tokio::join!(store.fail_missing_dispatch(&run.run_id, "node", 0, deadline), store.fail_missing_dispatch(&run.run_id, "node", 0, deadline));
        assert_eq!(usize::from(a.unwrap()) + usize::from(b.unwrap()), usize::from(expected), "{id}");
        if expected {
            let fact = store.get_node_attempt_failure(&run.run_id, "node", 0).await.unwrap().unwrap();
            assert_eq!(fact.action, Some(if id == "retry" { Action::Retry } else { Action::FailRun }));
            assert!(!store.save_node_dispatch(p).await.unwrap()); // late creator fenced
            assert!(!store.fail_missing_dispatch(&run.run_id, "node", 1, deadline).await.unwrap());
        }
    }
}

#[tokio::test]
async fn memory_recovery_gap_contract() { let store = MemoryCollaborationStore::new(); recovery_gap_contract(&store, &store).await; }
#[tokio::test]
async fn sqlite_recovery_gap_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into()); recovery_gap_contract(&store, &store).await;
    assert!(MySqlCollaborationStore::sqlite(db, "other".into()).get_startup_failure("gap-snapshot").await.unwrap().is_none());
}
