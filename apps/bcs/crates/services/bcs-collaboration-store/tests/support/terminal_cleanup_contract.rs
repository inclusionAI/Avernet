use super::*;
use bcs_service_api::{StateMachineDispatchPayload, StateMachineDispatchTarget, StateMachineDispatchStatus as DispatchStatus,
    StateMachineDispatchResult, StateMachineOpeningPayload, StateMachineChatResultPayload, StateMachineResultPublishCommand,
    StateMachineChatResultStatus as ChatStatus, StateMachineChatResultOutcome};

pub(super) async fn terminal_cleanup_contract(store: &dyn StateMachineRunRepoPort) {
    for (case, status) in [("a-running", StateMachineRunStatus::Running), ("b-completed", StateMachineRunStatus::Completed),
        ("c-failed", StateMachineRunStatus::Failed), ("d-aborted", StateMachineRunStatus::Aborted)] {
        let mut run = test_run(); run.run_id = format!("cleanup-{case}"); run.session_id = format!("cleanup-session-{case}");
        run.status = StateMachineRunStatus::Running; run.created_at = 100;
        let ids = ["a-pending", "b-delivering", "c-delivered", "judge"];
        let nodes = ids.into_iter().map(|id| {
            let mut node = test_node(); node.run_id = run.run_id.clone(); node.node_id = id.into(); node.attempt = 0;
            node.status = StateMachineNodeStatus::Pending; node.node_timeout_ms = Some(1000); node.assignee_bot_id = Some("bot".into());
            node.artifact_text = None; node
        }).collect();
        store.create_run(run.clone(), nodes).await.unwrap();
        let r = run.run_id.as_str();
        let opening = StateMachineOpeningPayload { run_id: r.into(), group_id: run.group_id.clone(), session_id: run.session_id.clone(),
            client_msg_id: format!("{r}:000-panel"), content: "saved opening".into(), component: None, created_at_ms: 100 };
        assert!(store.save_run_opening(opening.clone()).await.unwrap());
        let mut claims = Vec::new();
        for id in ids {
            let request = format!("smnode-{r}-{id}-0");
            store.mark_node_running_if_run_active(r, id, 0, request.clone(), 100).await.unwrap();
            if id == "judge" { continue; }
            let payload = StateMachineDispatchPayload { run_id: r.into(), node_id: id.into(), attempt: 0,
                group_id: run.group_id.clone(), session_id: run.session_id.clone(), assignee_bot_id: "bot".into(), delivery_request_id: request.clone(),
                target: StateMachineDispatchTarget::WebSocket, request: json!({"id": request, "prompt": "saved"}), started_at_ms: 100, deadline_ms: 1100 };
            assert!(store.save_node_dispatch(payload).await.unwrap());
            let claim = store.claim_node_dispatch(r, id, 0, "original".into(), 101, 1000).await.unwrap().unwrap();
            if id != "a-pending" { assert!(store.begin_node_dispatch_send(&claim, 102).await.unwrap()); }
            if id == "c-delivered" { assert!(store.finish_node_dispatch(&claim, StateMachineDispatchResult::Accepted, 103).await.unwrap()); }
            claims.push(claim);
        }
        assert!(store.begin_node_judging(r, "judge", 0, "immutable judge input".into(), Some("human".into())).await.unwrap());
        let judge = store.claim_node_judging(r, "judge", 0, "original".into(), 101, 1000).await.unwrap().unwrap();
        assert_eq!(store.cleanup_terminal_run_checkpoints(r, 32).await.unwrap(), 0);
        if status != StateMachineRunStatus::Running {
            store.update_run_status(r, status, Some("saved result".into()), Some("saved reason".into()), 104, Some(104)).await.unwrap();
        }
        let saved_run = serde_json::to_value(store.get_run(r).await.unwrap()).unwrap();
        let saved_nodes = serde_json::to_value(store.list_node_runs(r).await.unwrap()).unwrap();
        assert_eq!(store.cleanup_terminal_run_checkpoints(r, 0).await.unwrap(), 0);
        let changed = store.cleanup_terminal_run_checkpoints(r, 1).await.unwrap();
        assert!(changed <= 2);
        for _ in 0..5 { store.cleanup_terminal_run_checkpoints(r, 1).await.unwrap(); }
        assert_eq!(store.cleanup_terminal_run_checkpoints(r, 32).await.unwrap(), 0);
        assert_eq!(serde_json::to_value(store.get_run(r).await.unwrap()).unwrap(), saved_run);
        assert_eq!(serde_json::to_value(store.list_node_runs(r).await.unwrap()).unwrap(), saved_nodes);
        assert_eq!(store.get_run_opening(r).await.unwrap().unwrap().payload, opening);
        for claim in claims {
            let saved = store.get_node_dispatch(r, &claim.payload.node_id, 0).await.unwrap().unwrap();
            assert_eq!(saved.payload, claim.payload);
            if claim.payload.node_id == "c-delivered" {
                assert_eq!(saved.status, DispatchStatus::Delivered); assert_eq!(saved.delivered_at_ms, Some(103));
            } else if status != StateMachineRunStatus::Running {
                assert_eq!(saved.status, DispatchStatus::Superseded); assert!(saved.lease_owner.is_none());
                assert_eq!(saved.lease_token, claim.token);
                assert!(!store.begin_node_dispatch_send(&claim, 105).await.unwrap());
                assert!(!store.finish_node_dispatch(&claim, StateMachineDispatchResult::Accepted, 105).await.unwrap());
            }
        }
        if status != StateMachineRunStatus::Running {
            assert!(!store.release_node_judging(&judge).await.unwrap());
            assert!(store.claim_node_judging(r, "judge", 0, "late".into(), 1001, 1100).await.unwrap().is_none());
        }
    }
    let page = store.list_terminal_runs_for_cleanup(Some("cleanup-"), 2).await.unwrap();
    assert_eq!(page, vec!["cleanup-b-completed", "cleanup-c-failed"]);
    assert_eq!(store.list_terminal_runs_for_cleanup(page.last().map(String::as_str), 1).await.unwrap(), vec!["cleanup-d-aborted"]);
    assert!(store.list_terminal_runs_for_cleanup(None, 0).await.unwrap().is_empty());

    let repair_ids = vec!["cleanup-b-completed".into(), "cleanup-c-failed".into(), "cleanup-d-aborted".into()];
    assert_eq!(store.list_unrepaired_history_runs(&repair_ids).await.unwrap(), repair_ids);
    assert!(store.confirm_terminal_history_repair("cleanup-a-running", 200).await.is_err());
    assert!(store.confirm_terminal_history_repair("missing-run", 200).await.is_err());
    for run in &repair_ids {
        let opening = store.get_run_opening(run).await.unwrap().unwrap();
        let (first, second) = tokio::join!(store.confirm_terminal_history_repair(run, 200), store.confirm_terminal_history_repair(run, 201));
        first.unwrap(); second.unwrap();
        store.cleanup_terminal_run_checkpoints(run, 32).await.unwrap();
        let saved = store.get_run_opening(run).await.unwrap().unwrap();
        assert_eq!(saved.payload, opening.payload);
        assert_eq!(saved.delivered_at_ms, opening.delivered_at_ms);
    }
    assert!(store.list_unrepaired_history_runs(&repair_ids).await.unwrap().is_empty());
    assert!(store.list_unrepaired_history_runs(&[]).await.unwrap().is_empty());
    assert!(store.list_unrepaired_history_runs(&vec!["run".into(); 33]).await.is_err());

    for case in ["pending", "delivering", "delivered", "failed"] {
        let mut run = test_run(); run.run_id = format!("cleanup-publication-{case}"); run.session_id = format!("cleanup-publication-session-{case}");
        run.status = StateMachineRunStatus::Running; run.created_by = Some("bot".into());
        store.create_run(run.clone(), Vec::new()).await.unwrap();
        let r = run.run_id.as_str();
        let payload = StateMachineChatResultPayload { command: StateMachineResultPublishCommand { run_id: r.into(),
            group_id: run.group_id.clone(), session_id: run.session_id.clone(), sender_bot_id: "bot".into(), content: "saved Chat output".into(), created_at_ms: 100 }, deadline_ms: 1000 };
        store.save_chat_result(payload.clone()).await.unwrap();
        let claim = store.claim_chat_result(r, "owner".into(), 101, 900).await.unwrap().unwrap();
        if case != "pending" { store.begin_chat_result_send(&claim, 102).await.unwrap(); }
        if matches!(case, "delivered" | "failed") {
            store.finish_chat_result(&claim, if case == "delivered" { StateMachineChatResultOutcome::Published }
                else { StateMachineChatResultOutcome::Failed { error: "original error".into() } }, 103).await.unwrap();
        }
        store.update_run_status(r, StateMachineRunStatus::Aborted, None, None, 104, Some(104)).await.unwrap();
        let (a, b) = tokio::join!(store.cleanup_terminal_run_checkpoints(r, 1), store.cleanup_terminal_run_checkpoints(r, 1));
        assert_eq!(a.unwrap() + b.unwrap(), usize::from(matches!(case, "pending" | "delivering")));
        let saved = store.get_chat_result(r).await.unwrap().unwrap();
        assert_eq!(saved.payload, payload); assert!(saved.lease_owner.is_none()); assert_eq!(saved.lease_token, claim.token);
        assert_eq!(saved.status, match case { "delivered" => ChatStatus::Delivered, "failed" => ChatStatus::Failed, _ => ChatStatus::Superseded });
        if case == "failed" { assert_eq!(saved.error.as_deref(), Some("original error")); }
        assert!(!store.begin_chat_result_send(&claim, 105).await.unwrap());
        assert!(!store.finish_chat_result(&claim, StateMachineChatResultOutcome::Published, 105).await.unwrap());
    }
}

#[tokio::test]
async fn memory_terminal_cleanup_contract() { terminal_cleanup_contract(&MemoryCollaborationStore::new()).await; }

#[tokio::test]
async fn sqlite_terminal_cleanup_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into()); terminal_cleanup_contract(&store).await;
    assert!(MySqlCollaborationStore::sqlite(db.clone(), "other".into()).list_terminal_runs_for_cleanup(None, 32).await.unwrap().is_empty());
    assert_eq!(MySqlCollaborationStore::sqlite(db.clone(), "other".into()).cleanup_terminal_run_checkpoints("cleanup-a-running", 32).await.unwrap(), 0);
    for kind in ["im_terminal", "startup_failure"] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_collaboration_delivery_checkpoints (env, operation_key, aggregate_kind, aggregate_id, operation_kind, payload_json, status, created_at_ms) VALUES ('test', ?, 'state_machine_run', 'cleanup-b-completed', ?, '{}', 'pending', 100)", vec![DbValue::from(kind), DbValue::from(kind)])).await.unwrap();
    }
    store.cleanup_terminal_run_checkpoints("cleanup-b-completed", 32).await.unwrap();
    let rows = db.query(DbStatement::new("SELECT status FROM bcs_collaboration_delivery_checkpoints WHERE operation_kind IN ('im_terminal', 'startup_failure')")).await.unwrap();
    assert_eq!(rows.len(), 2);
    for row in rows { assert_eq!(bcs_db_api::db_get_column::<String>(&row, "status").unwrap(), "pending"); }
}
