use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_relation_store::{DbRelationStore, MemoryRelationRepo};

#[tokio::test]
async fn memory_relation_repo_passes_relation_repo_contract() {
    let repo = MemoryRelationRepo::new();

    bcs_test_support::contract::repo::relation_repo_port_contract_tests(&repo).await;
}

#[tokio::test]
async fn db_relation_store_passes_relation_repo_contract() {
    let db = sqlite_db().await;
    let repo = DbRelationStore::sqlite(db);

    bcs_test_support::contract::repo::relation_repo_port_contract_tests(&repo).await;
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_actor_relations (
            from_id VARCHAR(128) NOT NULL,
            to_id VARCHAR(128) NOT NULL,
            env VARCHAR(32) NOT NULL,
            kinds BIGINT NOT NULL DEFAULT 0,
            allow BIGINT NOT NULL DEFAULT 0,
            deny BIGINT NOT NULL DEFAULT 0,
            is_creator INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (from_id, to_id, env)
        )",
    ))
    .await
    .expect("create relation table");
    db
}
