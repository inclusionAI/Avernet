use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

async fn through_previous_version(db: &dyn DbPlugin) -> DbResult<()> {
    run_sqlite_bootstrap_tables(db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|m| m.version <= 47) {
        apply_sqlite_migration(db, migration).await?;
    }
    Ok(())
}

const HISTORY: &str = "SELECT version, name, dialect, checksum, applied_at FROM bcs_schema_migrations WHERE version <= 47 ORDER BY version";

#[tokio::test]
async fn agent_invoke_upgrade_preserves_populated_edges_requests_and_history() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    through_previous_version(&db).await?;
    db.execute(DbStatement::new(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, originator_policy_type) VALUES ('dev', 'bot-a', 'bot-b', 'rules', 17, '[]', 'approved', 'any')"
    )).await?;
    db.execute(DbStatement::new(
        "INSERT INTO permission_requests (request_id, env, from_id, to_id, request_kind, requested_ref_id, status, created_by) VALUES ('old-request', 'dev', 'bot-a', 'bot-b', 'connect', 17, 'pending', 'bot-a')"
    )).await?;
    let history = db.query(DbStatement::new(HISTORY)).await?;
    let edges = db.query(DbStatement::new("SELECT * FROM edge_grants")).await?;
    let requests = db.query(DbStatement::new("SELECT * FROM permission_requests")).await?;
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(HISTORY)).await?, history);
    assert_eq!(db.query(DbStatement::new("SELECT * FROM edge_grants")).await?, edges);
    assert_eq!(db.query(DbStatement::new("SELECT * FROM permission_requests")).await?, requests);
    assert_eq!(current_sqlite_version(&db, true).await?, Some(49));
    let tables = db.query(DbStatement::new("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'agent_invoke_%' ORDER BY name")).await?;
    let names: Vec<String> = tables.iter().map(|r| db_get_column(r, "name")).collect::<DbResult<_>>()?;
    assert_eq!(names, ["agent_invoke_audit", "agent_invoke_facts", "agent_invoke_governance", "agent_invoke_grants", "agent_invoke_pending_pairs", "agent_invoke_receipts", "agent_invoke_requests", "agent_invoke_scopes"]);
    let rows = db.query(DbStatement::new("SELECT * FROM agent_invoke_scopes")).await?;
    assert!(rows.is_empty(), "schema installation cannot enable a writer or invent an eligible domain");
    Ok(())
}

#[tokio::test]
async fn agent_invoke_ddl_and_history_record_roll_back_together() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    through_previous_version(&db).await?;
    let before = db.query(DbStatement::new(HISTORY)).await?;
    db.execute(DbStatement::new("CREATE TRIGGER fail_agent_migration BEFORE INSERT ON bcs_schema_migrations WHEN NEW.version = 48 BEGIN SELECT RAISE(ABORT, 'injected migration record failure'); END")).await?;
    assert!(run_sqlite_migrations(&db).await.is_err());
    assert!(!table_exists(&db, "agent_invoke_scopes").await?);
    assert!(!table_exists(&db, "agent_invoke_audit").await?);
    assert!(applied_sqlite_migration(&db, 48).await?.is_none());
    assert_eq!(db.query(DbStatement::new(HISTORY)).await?, before);
    db.execute(DbStatement::new("DROP TRIGGER fail_agent_migration")).await?;
    run_sqlite_migrations(&db).await?;
    assert!(table_exists(&db, "agent_invoke_scopes").await?);
    assert_eq!(db.query(DbStatement::new(HISTORY)).await?, before);
    Ok(())
}

#[tokio::test]
async fn agent_invoke_scope_is_exclusive_and_fact_identity_is_byte_exact() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&db).await?;
    let insert = "INSERT INTO agent_invoke_scopes (namespace, env, revision, max_out_degree, max_in_degree, max_pending_out, max_pending_in, max_fact_ttl_ms) VALUES (?, 'dev', 1, 2, 2, 2, 2, 60000)";
    db.execute(DbStatement::with_params(insert, vec!["tenant-one".into()])).await?;
    assert!(db.execute(DbStatement::with_params(insert, vec!["tenant-two".into()])).await.is_err(), "legacy env cannot silently be shared by two tenant namespaces");
    for bot in ["bot-a", "BOT-A", "bot-a "] {
        db.execute(DbStatement::with_params("INSERT INTO agent_invoke_facts (scope_id, bot_id, revision, binding_generation, eligibility_generation, eligible, active, hidden, valid_until_ms) VALUES (1, ?, 1, 1, 1, 1, 1, 0, 100)", vec![bot.into()])).await?;
    }
    let rows = db.query(DbStatement::new("SELECT bot_id FROM agent_invoke_facts ORDER BY bot_id")).await?;
    assert_eq!(rows.len(), 3, "no case folding or PAD SPACE identity collapse");
    assert!(db.execute(DbStatement::new("UPDATE agent_invoke_facts SET revision = 0")).await.is_err());
    assert!(db.execute(DbStatement::new("UPDATE agent_invoke_scopes SET max_out_degree = 0")).await.is_err());
    Ok(())
}

#[test]
fn agent_invoke_mysql_schema_is_additive_bounded_and_default_closed() {
    let sql = include_str!("../../../../../migrations/mysql/050_agent_invoke_authorization.sql");
    let statements = bcs_db_api::split_sql_statements(sql);
    assert_eq!(statements.iter().filter(|s| s.contains("CREATE TABLE ")).count(), 8);
    assert!(!sql.contains("ALTER TABLE") && !sql.contains("DROP TABLE"));
    assert!(!statements.iter().any(|s| s.trim_start().starts_with("INSERT")));
    assert!(sql.contains("UNIQUE (env)"));
    assert!(sql.contains("PRIMARY KEY (scope_id, principal_hash, from_id, to_id)"));
    assert!(sql.contains("writer_enabled INTEGER NOT NULL DEFAULT 0"));
    assert!(sql.contains("runtime_enabled INTEGER NOT NULL DEFAULT 0"));
    assert!(sql.contains("COLLATE utf8mb4_0900_bin"));
    assert!(sql.contains("VARBINARY(128)"));
    assert_eq!(sql.matches("CREATE INDEX idx_agent_invoke_").count(), 9);
}
