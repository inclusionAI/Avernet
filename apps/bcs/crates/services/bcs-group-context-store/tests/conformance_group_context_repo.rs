mod common;

use bcs_db_api::{DbPlugin, DbStatement, DbValue, db_get_column_opt};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::GroupContextOwners;
use bcs_group_context_store::{GroupContextRepo, MemoryGroupContextRepo, SqlGroupContextStore};
use std::sync::Arc;

async fn sqlite_repo() -> SqlGroupContextStore {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    apply_sqlite_group_context_migration(db.as_ref()).await;
    SqlGroupContextStore::sqlite(db)
}

async fn apply_sqlite_group_context_migration(db: &dyn DbPlugin) {
    for migration in [
        include_str!("../../../../migrations/sqlite/034_group_context.sql"),
        include_str!("../../../../migrations/sqlite/035_group_context_owners.sql"),
    ] {
        for sql in migration
            .split(';')
            .map(str::trim)
            .filter(|sql| !sql.is_empty())
        {
            db.execute(DbStatement::new(sql))
                .await
                .expect("migration sql");
        }
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
async fn memory_persists_owners_for_each_version() {
    common::owners_are_persisted_per_version(&MemoryGroupContextRepo::new()).await;
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
async fn sqlite_persists_owners_for_each_version() {
    let repo = sqlite_repo().await;
    common::owners_are_persisted_per_version(&repo).await;
}

#[tokio::test]
async fn sqlite_maps_legacy_null_owners_and_preserves_null_when_superseding() {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    apply_sqlite_group_context_migration(db.as_ref()).await;
    db.execute(DbStatement::new(
        r#"
        INSERT INTO bcs_group_contexts
            (context_id, name, scope_level, scope, content, creator, visible_to_json,
             collect_from_json, supersedes, change_reason, valid_from, valid_to,
             is_superseded, gmt_superseded, tx_time, gmt_create, gmt_modified)
        VALUES ('ctx-legacy-null-owners', 'legacy_owners', 'session', 'sess-legacy',
                'v1', 'writer-a', '{"userIds":[]}', '{"userIds":["writer-a"]}',
                NULL, NULL, '2026-10-19 00:00:00.000', NULL, 0, NULL,
                '2026-10-19 00:00:00.000', '2026-10-19 00:00:00.000',
                '2026-10-19 00:00:00.000')
        "#,
    ))
    .await
    .expect("seed legacy row");

    let repo = SqlGroupContextStore::sqlite(db.clone());
    let legacy = repo
        .get_active("legacy_owners", "sess-legacy")
        .await
        .expect("load legacy row")
        .expect("active row");
    assert_eq!(legacy.owners, GroupContextOwners::new(Vec::<String>::new()));

    let mut second_params = common::entry(
        "ctx-legacy-null-owners",
        "legacy_owners",
        "sess-legacy",
        "v2",
    );
    second_params.creator = "writer-b".into();
    second_params.owners = GroupContextOwners::new(Vec::<String>::new());
    second_params.supersedes = Some(legacy.id);
    let second = repo
        .supersede_active("legacy_owners", "sess-legacy", legacy.id, second_params)
        .await
        .expect("supersede legacy empty owners");
    assert_eq!(second.owners, GroupContextOwners::new(Vec::<String>::new()));

    let rows = db
        .query(DbStatement::with_params(
            "SELECT owners_json FROM bcs_group_contexts WHERE id = ?",
            vec![DbValue::from(second.id)],
        ))
        .await
        .expect("load replacement owners representation");
    let row = rows.first().expect("replacement owners representation row");
    assert_eq!(
        db_get_column_opt::<String>(row, "owners_json").expect("read owners json"),
        None,
        "inherited empty owners remain SQL NULL"
    );
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
