//! Migration coverage for the Bot avatar column (SQLite 040).
//!
//! Contract of version `040_bot_avatar`:
//!
//! 1. `bcs_bots.avatar_url` is a nullable TEXT column added to fresh and
//!    legacy databases alike;
//! 2. a legacy row keeps its name, description (`bot_info`), credential
//!    (`session_token`) and Owner (`created_by`) byte-exact across the
//!    upgrade and its avatar stays NULL (no backfill);
//! 3. repeated runner invocations never rewrite migration history.

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn avatar_column_is_nullable_and_history_is_stable() {
    let db = LocalSqliteDbPlugin::new().unwrap();
    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
    let columns = db.query(DbStatement::new("PRAGMA table_info(bcs_bots)"))
        .await.unwrap();
    let avatar = columns.iter().find(|r|
        r.get("name") == Some(&DbValue::String("avatar_url".into()))).unwrap();
    assert_eq!(avatar.get("notnull"), Some(&DbValue::I64(0)));
    let statement = || DbStatement::new(
        "SELECT version,name,checksum FROM bcs_schema_migrations ORDER BY version");
    let before = db.query(statement()).await.unwrap();
    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
    assert_eq!(db.query(statement()).await.unwrap(), before);
}

#[tokio::test]
async fn avatar_upgrade_preserves_legacy_bot_rows_and_migration_history() {
    let db = LocalSqliteDbPlugin::new().unwrap();
    // A pre-040 local Bot table. Only the columns this test seeds and
    // checks, plus the columns the bootstrap indexes reference, are
    // declared; no committed production DDL is copied or edited here.
    // name / bot_info / session_token / created_by carry the Bot name,
    // description, credential and Owner this test must protect.
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_uuid TEXT NOT NULL,
            env TEXT DEFAULT NULL,
            name TEXT NOT NULL DEFAULT '',
            bot_info TEXT DEFAULT NULL,
            session_token TEXT DEFAULT NULL,
            created_by TEXT DEFAULT NULL,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            agent_code TEXT DEFAULT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        )",
    ))
    .await.unwrap();
    db.execute(DbStatement::new(
        "INSERT INTO bcs_bots (bot_uuid, env, name, bot_info, session_token, created_by) \
        VALUES ('legacy-bot', 'local', 'Legacy Bot', '{\"description\":\"kept\"}', \
        'legacy-token', 'alice')",
    ))
    .await.unwrap();

    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();

    let row_sql = "SELECT name, bot_info, session_token, created_by, avatar_url \
        FROM bcs_bots WHERE bot_uuid = 'legacy-bot'";
    let rows = db.query(DbStatement::new(row_sql)).await.unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].get("name"), Some(&DbValue::String("Legacy Bot".into())));
    assert_eq!(
        rows[0].get("bot_info"),
        Some(&DbValue::String("{\"description\":\"kept\"}".into()))
    );
    assert_eq!(rows[0].get("session_token"), Some(&DbValue::String("legacy-token".into())));
    assert_eq!(rows[0].get("created_by"), Some(&DbValue::String("alice".into())));
    assert_eq!(rows[0].get("avatar_url"), Some(&DbValue::Null));
    let columns = db.query(DbStatement::new("PRAGMA table_info(bcs_bots)"))
        .await.unwrap();
    let avatar = columns.iter().find(|r|
        r.get("name") == Some(&DbValue::String("avatar_url".into()))).unwrap();
    assert_eq!(avatar.get("notnull"), Some(&DbValue::I64(0)));

    // The full chain must be recorded exactly once, and a second runner
    // invocation changes neither history nor the preserved Bot row.
    let history_sql = "SELECT version, name, checksum FROM bcs_schema_migrations ORDER BY version";
    let history = db.query(DbStatement::new(history_sql)).await.unwrap();
    assert_eq!(history.len(), bcs::migrations::sqlite_target_version() as usize);
    let record = history
        .iter()
        .find(|r| r.get("version") == Some(&DbValue::I64(40)))
        .unwrap();
    assert_eq!(record.get("name"), Some(&DbValue::String("bot_avatar".into())));
    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
    assert_eq!(db.query(DbStatement::new(history_sql)).await.unwrap(), history);
    assert_eq!(db.query(DbStatement::new(row_sql)).await.unwrap(), rows);
}
