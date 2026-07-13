use std::sync::Arc;

use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_organization_store::{DbOrganizationStore, MemoryOrganizationRepo};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
mod bootstrap_migrations;

#[tokio::test]
async fn memory_organization_repo_contract() {
    bcs_test_support::contract::repo::organization_repo_contract_tests(
        &MemoryOrganizationRepo::new(),
    )
    .await;
}

#[tokio::test]
async fn sqlite_organization_repo_contract() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("run sqlite migrations");
    let repo = DbOrganizationStore::sqlite(db);
    bcs_test_support::contract::repo::organization_repo_contract_tests(&repo).await;
}
