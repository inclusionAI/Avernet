use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn complete_sqlite_chain_adds_a2a_columns_and_replays_without_duplicate_ddl() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    let rows = db.query(DbStatement::new("PRAGMA table_info(bcs_bots)")).await?;
    let names = rows.iter().map(|row| row.get_string("name").map(|value| value.unwrap())).collect::<DbResult<Vec<_>>>()?;
    for column in ["agent_card", "agent_card_url", "agent_card_revision"] {
        assert!(names.iter().any(|name| name == column), "missing {column}");
    }
    for removed in ["a2a_interface_json","a2a_credential_ref_json","agent_card_validated_at","agent_card_next_refresh_at","agent_card_refresh_error"] {
        assert!(!names.iter().any(|name|name==removed),"obsolete A2A field {removed}");
    }
    let rows = db.query(DbStatement::new("SELECT name FROM bcs_schema_migrations WHERE version=39")).await?;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].get_string("name")?.as_deref(), Some("bot_a2a"));
    assert!(check_sqlite_migrations(&db).await?.pending_versions.is_empty());
    Ok(())
}

#[tokio::test]
async fn normalized_a2a_schema_has_audit_columns_and_no_record_blobs() -> DbResult<()> {
    let db=LocalSqliteDbPlugin::new()?;run_sqlite_migrations(&db).await?;
    for table in ["bcs_a2a_tasks","bcs_a2a_run_bindings","bcs_a2a_contexts"] {
        let columns=sqlite_table_columns(&db,table).await?;
        for required in ["id","gmt_create","gmt_modified"] {assert!(columns.iter().any(|c|c==required));}
        assert!(!columns.iter().any(|c|c=="record_json" || c=="scope_json"));
    }
    assert!(!table_exists(&db,"bcs_a2a_runs").await?);
    assert!(sqlite_table_columns(&db,"bcs_a2a_contexts").await?.iter().any(|c|c=="session_id"));
    Ok(())
}
