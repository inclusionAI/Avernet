use std::sync::Arc;

use bcs_collaboration_store::{MemoryCollaborationStore, MySqlCollaborationStore};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{StateMachineNodeRun, StateMachineNodeStatus, StateMachineRun};
use bcs_service_api::StateMachineRunRepoPort;
use serde_json::json;

use super::bootstrap_migrations;

async fn run_contract(store: &dyn StateMachineRunRepoPort) {
    for (id, status) in [("a", "running"), ("b", "pending"), ("c", "running"), ("d", "completed"), ("e", "failed"), ("f", "running"), ("g", "aborted")] {
        let run: StateMachineRun = serde_json::from_value(json!({
            "run_id": id, "definition_id": "def", "definition_version": 1, "group_id": "group",
            "session_id": format!("session-{id}"), "status": status, "input": {}, "created_at": 1, "updated_at": 1,
        })).unwrap();
        let nodes = ["pending", "ready", "retry_scheduled", "running", "completed", "failed", "skipped"]
            .into_iter().map(|status| serde_json::from_value::<StateMachineNodeRun>(json!({
                "run_id": id, "node_id": status, "status": status, "attempt": 0,
            })).unwrap()).collect();
        store.create_run(run, nodes).await.unwrap();
    }
    assert!(store.list_running_runs(None, 0).await.unwrap().is_empty());
    let first = store.list_running_runs(None, 2).await.unwrap();
    assert_eq!(first.iter().map(|run| run.run_id.as_str()).collect::<Vec<_>>(), ["a", "c"]);
    let last = store.list_running_runs(Some("c"), 2).await.unwrap();
    assert_eq!(last.iter().map(|run| run.run_id.as_str()).collect::<Vec<_>>(), ["f"]);
    assert!(store.list_running_runs(Some("z"), 2).await.unwrap().is_empty());
    assert_eq!(store.list_running_runs(Some("b"), 1).await.unwrap()[0].run_id, "c");
    for status in ["pending", "ready", "retry_scheduled", "running", "completed", "failed", "skipped"] {
        let skippable = matches!(status, "pending" | "ready" | "retry_scheduled");
        assert_eq!(store.skip_node("a", status, 20).await.unwrap(), skippable);
        assert!(!store.skip_node("a", status, 30).await.unwrap());
        if skippable {
            let node = store.get_node_run("a", status).await.unwrap().unwrap();
            assert_eq!(node.status, StateMachineNodeStatus::Skipped);
            assert_eq!(node.completed_at, Some(20));
        }
        assert!(!store.skip_node("g", status, 20).await.unwrap(), "cancelled Run must not mutate");
    }
}

#[tokio::test]
async fn memory_progression_repo_conforms() {
    run_contract(&MemoryCollaborationStore::new()).await;
}

#[tokio::test]
async fn sqlite_progression_repo_conforms_and_filters_environment() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    run_contract(&MySqlCollaborationStore::sqlite(db.clone(), "test".into())).await;
    let plan = bcs_db_api::DbPlugin::query(db.as_ref(), bcs_db_api::DbStatement::new(
        "EXPLAIN QUERY PLAN SELECT run_id FROM bcs_state_machine_runs WHERE env = 'test' AND status = 'running' AND record_status = 'active' AND run_id > 'a' ORDER BY run_id ASC LIMIT 2"
    )).await.unwrap();
    let details = plan.iter().map(|row| bcs_db_api::db_get_column::<String>(row, "detail").unwrap()).collect::<Vec<_>>().join("\n");
    assert!(details.contains("idx_sm_runs_progression"), "{details}");
    assert!(!details.contains("TEMP B-TREE"), "cursor scan must not sort historical rows: {details}");
    assert!(MySqlCollaborationStore::sqlite(db, "other".into()).list_running_runs(None, 10).await.unwrap().is_empty());
}
