use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement, DbTransactionStep,
    DbTransactionStepResult,
};
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

struct AlwaysFailDb;

#[async_trait]
impl DbPlugin for AlwaysFailDb {
    async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
        Err(DbError::Backend("forced session query failure".to_string()))
    }

    async fn execute(&self, _statement: DbStatement) -> DbResult<DbExecuteResult> {
        unreachable!("fallible list test does not execute statements")
    }

    async fn transaction(
        &self,
        _steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        unreachable!("fallible list test does not execute transactions")
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

#[tokio::test]
async fn mysql_session_list_propagates_query_failure() {
    let repo = MySqlSessionStore::new(Arc::new(AlwaysFailDb), "dev".to_string());

    let error = repo
        .try_list_by_group("group-1", None, 0, 10, None, None)
        .await
        .expect_err("session query failure must propagate");

    assert!(error.to_string().contains("forced session query failure"));
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
