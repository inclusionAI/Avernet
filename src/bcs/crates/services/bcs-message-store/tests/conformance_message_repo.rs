use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_message_store::MemoryMessageRepo;
use bcs_message_store::MySqlMessageStore;
use bcs_test_support::contract::repo::message_repo_contract_tests;

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[tokio::test]
async fn memory_message_repo_passes_contract() {
    let repo = MemoryMessageRepo::new();
    message_repo_contract_tests(&repo).await;
}

#[tokio::test]
async fn sqlite_message_repo_passes_contract() {
    let db = sqlite_db().await;
    let repo = MySqlMessageStore::sqlite(db, "dev".to_string());
    message_repo_contract_tests(&repo).await;
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("run sqlite migrations");
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) \
         VALUES (?, ?, ?, ?)",
        vec![
            DbValue::from("contract-group:abcd1234"),
            DbValue::from("contract-group"),
            DbValue::from("dev"),
            DbValue::from("[]"),
        ],
    ))
    .await
    .expect("seed contract session");
    db
}
