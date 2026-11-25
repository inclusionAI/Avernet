//! Real-migration-chain fixture for the bot profile contract (bot-avatar
//! plan Task 3).
//!
//! The SQLite store under test must run against the complete committed
//! migration chain (including `040_bot_avatar`); the partial
//! `tests/common/bot_provider.rs::sqlite()` schema is intentionally NOT
//! used here because it only builds a subset of the historical columns.

pub async fn sqlite_repo() -> (
    std::sync::Arc<bcs_db_local::LocalSqliteDbPlugin>,
    bcs_bot_store::PersistentBotRepo,
) {
    let db = std::sync::Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    super::bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let repo = bcs_bot_store::PersistentBotRepo::with_sql_flavor(
        db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
    );
    (db, repo)
}
