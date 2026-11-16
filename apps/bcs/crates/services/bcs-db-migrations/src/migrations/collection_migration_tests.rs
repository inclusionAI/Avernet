use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

async fn fresh_db() -> LocalSqliteDbPlugin {
    let db = LocalSqliteDbPlugin::new().expect("open in-memory sqlite");
    run_sqlite_bootstrap_tables(&db).await.expect("bootstrap");
    run_sqlite_versioned_migrations(&db)
        .await
        .expect("versioned");
    db
}

#[tokio::test]
async fn fresh_db_has_session_participants_collected_column() {
    let db = fresh_db().await;
    let cols = sqlite_table_columns(&db, "bcs_session_participants")
        .await
        .unwrap();
    assert!(
        cols.iter().any(|c| c == "collected"),
        "bcs_session_participants must have a collected column on fresh DB; got {cols:?}"
    );
}

#[tokio::test]
async fn fresh_db_has_session_participants_collected_at_column() {
    let db = fresh_db().await;
    let cols = sqlite_table_columns(&db, "bcs_session_participants")
        .await
        .unwrap();
    assert!(
        cols.iter().any(|c| c == "collected_at"),
        "bcs_session_participants must have a collected_at column on fresh DB; got {cols:?}"
    );
}

#[tokio::test]
async fn ensure_function_adds_collected_to_legacy_table() {
    let db = LocalSqliteDbPlugin::new().expect("open in-memory sqlite");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_session_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            bot_uuid TEXT NOT NULL,
            role TEXT NOT NULL,
            env TEXT NOT NULL DEFAULT 'prod'
        )",
    ))
    .await
    .unwrap();
    run_sqlite_bootstrap_tables(&db)
        .await
        .expect("bootstrap repairs legacy table");
    let cols = sqlite_table_columns(&db, "bcs_session_participants")
        .await
        .unwrap();
    assert!(
        cols.iter().any(|c| c == "collected"),
        "ensure function must add collected to legacy bcs_session_participants; got {cols:?}"
    );
    assert!(
        cols.iter().any(|c| c == "collected_at"),
        "ensure function must add collected_at to legacy bcs_session_participants; got {cols:?}"
    );
}
