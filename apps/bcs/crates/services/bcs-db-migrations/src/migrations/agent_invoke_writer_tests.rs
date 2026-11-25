use super::*;
use bcs_db_local::LocalSqliteDbPlugin;
async fn previous(db: &dyn DbPlugin) -> DbResult<()> {
    run_sqlite_bootstrap_tables(db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|m| m.version <= 48)
    {
        apply_sqlite_migration(db, migration).await?;
    }
    Ok(())
}
#[tokio::test]
async fn command_fence_upgrade_preserves_legacy_and_does_not_invent_binding_proof() -> DbResult<()>
{
    let db = LocalSqliteDbPlugin::new()?;
    previous(&db).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid,name,env,actor_kind) VALUES ('retained-bot','retained','dev','bot')")).await?;
    db.execute(DbStatement::new("INSERT INTO edge_grants (env,from_id,to_id,grant_kind,grant_ref_id,rules,status,originator_policy_type) VALUES ('dev','retained-bot','target','rules',31,'[]','approved','any')")).await?;
    let history = db
        .query(DbStatement::new(
            "SELECT * FROM bcs_schema_migrations WHERE version<=48 ORDER BY version",
        ))
        .await?;
    let legacy = db
        .query(DbStatement::new("SELECT * FROM edge_grants"))
        .await?;
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(
        db.query(DbStatement::new("SELECT * FROM edge_grants"))
            .await?,
        legacy
    );
    assert_eq!(
        db.query(DbStatement::new(
            "SELECT * FROM bcs_schema_migrations WHERE version<=48 ORDER BY version"
        ))
        .await?,
        history
    );
    let rows = db
        .query(DbStatement::new(
            "SELECT agent_invoke_identity_revision FROM bcs_bots",
        ))
        .await?;
    assert_eq!(
        db_get_column::<u64>(&rows[0], "agent_invoke_identity_revision")?,
        1
    );
    for (table, columns) in [
        (
            "agent_invoke_grants",
            vec![
                "from_registration_id",
                "to_registration_id",
                "from_registration_revision",
                "to_registration_revision",
            ],
        ),
        (
            "agent_invoke_requests",
            vec![
                "requester_registration_id",
                "requester_registration_revision",
                "requester_delegation_revision",
            ],
        ),
        (
            "agent_invoke_facts",
            vec!["registration_id", "registration_revision"],
        ),
    ] {
        let actual = table_columns(&db, table).await?;
        for column in columns {
            assert!(actual.iter().any(|v| v == column), "{table}.{column}");
        }
    }
    assert_eq!(current_sqlite_version(&db, true).await?, Some(50));
    Ok(())
}
#[tokio::test]
async fn command_fence_ddl_and_history_are_atomic() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    previous(&db).await?;
    db.execute(DbStatement::new("CREATE TRIGGER fail_writer_history BEFORE INSERT ON bcs_schema_migrations WHEN NEW.version=49 BEGIN SELECT RAISE(ABORT,'injected writer history failure'); END")).await?;
    assert!(run_sqlite_migrations(&db).await.is_err());
    assert!(
        !table_columns(&db, "bcs_bots")
            .await?
            .iter()
            .any(|c| c == "agent_invoke_identity_revision")
    );
    assert!(
        !table_columns(&db, "agent_invoke_grants")
            .await?
            .iter()
            .any(|c| c == "from_registration_id")
    );
    assert!(applied_sqlite_migration(&db, 49).await?.is_none());
    db.execute(DbStatement::new("DROP TRIGGER fail_writer_history"))
        .await?;
    run_sqlite_migrations(&db).await?;
    Ok(())
}
#[test]
fn command_fence_mysql_migration_is_additive_and_keeps_index_budget() {
    let sql = include_str!("../../../../../migrations/mysql/051_agent_invoke_command_fences.sql");
    assert!(!sql.contains("DROP") && !sql.contains("TRIGGER"));
    assert!(sql.contains("created_by VARCHAR(256) NOT NULL"));
    assert!(sql.contains("env VARCHAR(64) NOT NULL"));
    assert!(sql.contains("requester_delegation_revision"));
    assert!(
        256 * 4 + 256 * 4 + 64 * 4 + 32 * 4 + 8 <= 3072,
        "existing five-part edge key stays below InnoDB limit after env widening"
    );
}
async fn table_columns(db: &dyn DbPlugin, table: &str) -> DbResult<Vec<String>> {
    db.query(DbStatement::new(format!("PRAGMA table_info({table})")))
        .await?
        .iter()
        .map(|row| db_get_column(row, "name"))
        .collect()
}
