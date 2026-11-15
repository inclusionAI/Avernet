use super::*;

// Repair path: a legacy DB that created the edge tables without gmt_* must
// get the audit columns backfilled by the idempotent rebuild in the migration.
#[tokio::test]
async fn edge_table_audit_columns_backfilled_for_legacy_db() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    // Legacy shape: edge_grants as built before the gmt_* audit-column
    // requirement landed (current bigint PK + real columns, minus gmt_create/gmt_modified).
    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT NOT NULL, \
         from_id TEXT NOT NULL, to_id TEXT NOT NULL, grant_kind TEXT NOT NULL, \
         grant_ref_id INTEGER NOT NULL, rules TEXT, status TEXT NOT NULL DEFAULT 'approved', \
         originator_policy_type TEXT NOT NULL DEFAULT 'any', originator_policy_data TEXT)",
    ))
    .await?;
    // add_sqlite_edge_permission_schema also ALTERs bcs_bots; give it a stub.
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, \
         PRIMARY KEY (bot_uuid, env))",
    ))
    .await?;
    // Re-running the edge-permission migration (v9) must ADD the gmt columns
    // via the idempotent rebuild repair (CREATE TABLE IF NOT EXISTS is a no-op
    // and SQLite forbids ADD COLUMN with a CURRENT_TIMESTAMP default).
    add_sqlite_edge_permission_schema(&db).await?;
    let columns = column_names(&db, "edge_grants").await?;
    assert!(columns.iter().any(|c| c == "gmt_create"));
    assert!(columns.iter().any(|c| c == "gmt_modified"));
    Ok(())
}
