mod common;

use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_context_store::{MemoryGroupContextRepo, SqlGroupContextStore};
use std::sync::Arc;

async fn sqlite_repo() -> SqlGroupContextStore {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    apply_sqlite_group_context_migration(db.as_ref()).await;
    SqlGroupContextStore::sqlite(db)
}

async fn apply_sqlite_group_context_migration(db: &dyn DbPlugin) {
    for sql in include_str!("../../../../migrations/sqlite/034_group_context.sql")
        .split(';')
        .map(str::trim)
        .filter(|sql| !sql.is_empty())
    {
        db.execute(DbStatement::new(sql))
            .await
            .expect("migration sql");
    }
}

#[tokio::test]
async fn memory_duplicate_active_conflicts() {
    common::create_duplicate_active_conflicts(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn memory_supersede_preserves_history() {
    common::supersede_preserves_history(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn memory_supersede_preserves_multiple_history_versions() {
    common::supersede_preserves_multiple_history_versions(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn memory_supersede_stale_id_returns_conflict() {
    common::supersede_stale_id_returns_conflict(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn memory_lists_active_by_multiple_scopes() {
    common::list_active_by_multiple_scopes(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn memory_persists_creator_for_each_version() {
    common::creator_is_persisted_for_each_version(&MemoryGroupContextRepo::new()).await;
}

#[tokio::test]
async fn sqlite_duplicate_active_conflicts_from_partial_unique_index() {
    let repo = sqlite_repo().await;
    common::create_duplicate_active_conflicts(&repo).await;
}

#[tokio::test]
async fn sqlite_supersede_preserves_history() {
    let repo = sqlite_repo().await;
    common::supersede_preserves_history(&repo).await;
}

#[tokio::test]
async fn sqlite_supersede_preserves_multiple_history_versions() {
    let repo = sqlite_repo().await;
    common::supersede_preserves_multiple_history_versions(&repo).await;
}

#[tokio::test]
async fn sqlite_supersede_stale_id_returns_conflict() {
    let repo = sqlite_repo().await;
    common::supersede_stale_id_returns_conflict(&repo).await;
}

#[tokio::test]
async fn sqlite_lists_active_by_multiple_scopes() {
    let repo = sqlite_repo().await;
    common::list_active_by_multiple_scopes(&repo).await;
}

#[tokio::test]
async fn sqlite_persists_creator_for_each_version() {
    let repo = sqlite_repo().await;
    common::creator_is_persisted_for_each_version(&repo).await;
}

#[tokio::test]
async fn memory_expired_business_validity_still_current() {
    common::current_row_with_expired_business_validity_still_blocks_and_is_returned(
        &MemoryGroupContextRepo::new(),
    )
    .await;
}

#[tokio::test]
async fn sqlite_expired_business_validity_still_current() {
    let repo = sqlite_repo().await;
    common::current_row_with_expired_business_validity_still_blocks_and_is_returned(&repo).await;
}
