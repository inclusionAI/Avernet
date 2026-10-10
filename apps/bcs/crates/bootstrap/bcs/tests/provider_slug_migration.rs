use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn provider_slug_upgrade_preserves_legacy_rows_and_migration_history() {
    let db = LocalSqliteDbPlugin::new().unwrap();
    db.execute(DbStatement::new("CREATE TABLE bcs_providers (
        provider_id TEXT NOT NULL, env TEXT NOT NULL, name TEXT NOT NULL,
        config TEXT NOT NULL, created_by TEXT NOT NULL, owners TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0, gmt_create TEXT DEFAULT CURRENT_TIMESTAMP,
        gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (env, provider_id))")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_providers
        (provider_id, env, name, config, created_by, owners) VALUES ('old', 'local', 'Old', '{}', 'alice', '[]')"))
        .await.unwrap();
    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
    let history = db.query(DbStatement::new("SELECT version, name, checksum FROM bcs_schema_migrations ORDER BY version"))
        .await.unwrap();
    assert_eq!(history.len(), 34);
    bcs::migrations::run_sqlite_migrations(&db).await.unwrap();
    assert_eq!(db.query(DbStatement::new("SELECT version, name, checksum FROM bcs_schema_migrations ORDER BY version"))
        .await.unwrap(), history);
    let rows = db.query(DbStatement::new("SELECT provider_id, name, slug FROM bcs_providers"))
        .await.unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].get("provider_id"), Some(&DbValue::String("old".into())));
    assert_eq!(rows[0].get("name"), Some(&DbValue::String("Old".into())));
    assert_eq!(rows[0].get("slug"), Some(&DbValue::Null));
    let indices = db.query(DbStatement::new("PRAGMA index_list(bcs_providers)")).await.unwrap();
    assert!(indices.iter().any(|row| row.get("name") == Some(&DbValue::String("uk_bcs_providers_env_slug".into()))));
}
