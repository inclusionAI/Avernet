use super::*;
use bcs_service_api::{CreateStateMachineRerun, CreateStateMachineRerunOutcome};

fn nodes(run_id: &str) -> Vec<StateMachineNodeRun> {
    (0..2048).map(|index| {
        let mut node = test_node();
        node.run_id = run_id.into();
        node.node_id = format!("node-{index}");
        node.delivery_request_id = None;
        node.bot_delivery_run_id = None;
        node.artifact_text = (index % 2 == 0).then(|| "non-empty value".into());
        node
    }).collect()
}

#[tokio::test]
async fn mysql_large_one_shot_and_rerun_use_bounded_statement_counts() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    store.create_run_if_session_idle(test_run(), nodes("sm-run-1")).await.unwrap();
    let mut run = test_run(); run.rerun_of = Some("source".into());
    store.create_rerun_if_session_idle(CreateStateMachineRerun {
        source_run_id: "source".into(), run, nodes: nodes("sm-run-1"), reactivate_service_session: false,
    }).await.unwrap();
    let transactions = db.transactions.lock().await;
    for (steps, expected_overhead) in transactions.iter().zip([2, 4]) {
        assert_eq!(steps.len(), expected_overhead + 43);
        let writes = &steps[expected_overhead..];
        let mut total_nodes = 0;
        for step in writes {
            let DbTransactionStep::Execute(stmt) = step else { panic!("node insert required"); };
            assert!(stmt.sql().contains("WHERE EXISTS"));
            assert!(stmt.params().len() <= 818);
            total_nodes += (stmt.params().len() - 2) / 17;
        }
        assert_eq!(total_nodes, 2048);
    }
}

#[tokio::test]
async fn sqlite_batched_nodes_preserve_guard_and_atomicity() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    contract(db.as_ref(), &store).await.unwrap();
}

pub(super) async fn contract(db: &dyn DbPlugin, store: &MySqlCollaborationStore) -> Result<(), Box<dyn std::error::Error>> {
    for session in ["batch-session", "batch-rollback-session"] {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_group_sessions (env, session_id, group_id, participants) VALUES ('test', ?, 'group-1', '[]')",
            vec![DbValue::from(session)],
        )).await?;
    }
    let mut run = test_run(); run.run_id = "batch-run".into(); run.session_id = "batch-session".into();
    let expected = nodes(&run.run_id);
    assert!(store.create_run_if_session_idle(run.clone(), expected.clone()).await?);
    let actual = store.list_node_runs(&run.run_id).await?;
    assert_eq!(actual.len(), 2048);
    for node in actual {
        let expected = expected.iter().find(|item| item.node_id == node.node_id).unwrap();
        assert_eq!(node.artifact_text, expected.artifact_text);
        assert_eq!(node.node_timeout_ms, expected.node_timeout_ms);
        assert_eq!(node.assignee_bot_id, expected.assignee_bot_id);
    }
    let mut blocked = run.clone(); blocked.run_id = "batch-blocked".into();
    assert!(!store.create_run_if_session_idle(blocked.clone(), nodes(&blocked.run_id)).await?);
    assert!(store.list_node_runs(&blocked.run_id).await?.is_empty());

    let mut invalid = run.clone(); invalid.run_id = "batch-rollback".into(); invalid.session_id = "batch-rollback-session".into();
    let mut duplicates = nodes(&invalid.run_id);
    duplicates[64].node_id = duplicates[0].node_id.clone();
    assert!(store.create_run_if_session_idle(invalid.clone(), duplicates).await.is_err());
    assert!(store.get_run(&invalid.run_id).await?.is_none());
    assert!(store.list_node_runs(&invalid.run_id).await?.is_empty());

    store.save_run_snapshot(&run, 7, &test_definition(), None, None).await?;
    db.execute(DbStatement::new("UPDATE bcs_state_machine_runs SET status = 'failed' WHERE env = 'test' AND run_id = 'batch-run'")).await?;
    let mut child = run.clone(); child.run_id = "batch-rerun".into(); child.rerun_of = Some(run.run_id.clone());
    let mut duplicates = nodes(&child.run_id);
    duplicates[64].node_id = duplicates[0].node_id.clone();
    let mut command = CreateStateMachineRerun {
        source_run_id: run.run_id, run: child.clone(), nodes: duplicates, reactivate_service_session: false,
    };
    assert!(store.create_rerun_if_session_idle(command.clone()).await.is_err());
    assert!(store.get_run(&child.run_id).await?.is_none());
    assert!(store.list_node_runs(&child.run_id).await?.is_empty());
    assert!(store.get_run_snapshot(&child.run_id).await?.is_none());
    command.nodes = nodes(&child.run_id);
    assert!(matches!(store.create_rerun_if_session_idle(command).await?, CreateStateMachineRerunOutcome::Created));
    assert_eq!(store.list_node_runs(&child.run_id).await?.len(), 2048);
    assert!(store.get_run_snapshot(&child.run_id).await?.is_some());
    Ok(())
}
