use std::sync::Arc;

use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
use bcs_service_api::{Participant, ParticipantRole, SessionKind};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[tokio::test]
async fn memory_session_repo_passes_session_repo_contract() {
    let repo = MemorySessionRepo::new();
    bcs_test_support::contract::repo::session_repo_port_contract_tests(&repo).await;
}

/// Exercises the `MySqlSessionStore` through its SQLite flavor (same code path
/// as a MySQL/OceanBase deployment, just a different dialect). This covers the
/// SQL-backed create/collect/uncollect/list_collected_by_group/collected_at_map
/// implementations that the memory-only contract test cannot reach.
#[tokio::test]
async fn sqlite_session_repo_passes_session_repo_contract() {
    let db = sqlite_db().await;
    let repo = MySqlSessionStore::sqlite(db, "dev".to_string());
    bcs_test_support::contract::repo::session_repo_port_contract_tests(&repo).await;
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("run sqlite migrations");
    db
}

#[tokio::test]
async fn memory_session_metrics_snapshot_port_contract() {
    let repo = MemorySessionRepo::new();
    repo.create(
        "metrics-group",
        NewSessionParams {
            session_kind: SessionKind::ServiceInvocation,
            participants: vec![Participant::bot("driver", ParticipantRole::Driver)],
            ..Default::default()
        },
    )
    .await
    .expect("create session");

    bcs_test_support::contract::port::group_session_metrics_snapshot_port_contract_tests(&repo)
        .await;
}