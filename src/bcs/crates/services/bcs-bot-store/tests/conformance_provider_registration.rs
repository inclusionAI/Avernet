use std::sync::Arc;

use bcs_bot_store::{DbProviderRegistrationStore, MemoryProviderRegistrationStore};
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_test_support::contract::provider_registration_repo_port_contract_tests;

#[tokio::test]
async fn conformance_memory_provider_registration_repo() {
    provider_registration_repo_port_contract_tests(&MemoryProviderRegistrationStore::new()).await;
    provider_registration_repo_port_contract_tests(&MemoryProviderRegistrationStore::default())
        .await;
}

#[tokio::test]
async fn conformance_sqlite_provider_registration_repo() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    let migration = include_str!("../../../../migrations/sqlite/030_provider_registrations.sql");
    for sql in migration
        .split(';')
        .map(str::trim)
        .filter(|sql| !sql.is_empty())
    {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    let repo = DbProviderRegistrationStore::new(db, DbSqlFlavor::Sqlite, "dev".into());
    provider_registration_repo_port_contract_tests(&repo).await;
}
