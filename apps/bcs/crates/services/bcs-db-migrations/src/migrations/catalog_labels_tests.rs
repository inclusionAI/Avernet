use super::*;
use bcs_db_local::LocalSqliteDbPlugin;
#[tokio::test]
async fn upgrade_retains_history_and_accepts_legacy_queries_inserts_and_runner() -> DbResult<()> {
    let db=LocalSqliteDbPlugin::new()?;run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|m|m.version<=45){apply_sqlite_migration(&db,migration).await?;}
    let history_sql="SELECT * FROM bcs_schema_migrations WHERE version<=45 ORDER BY version";
    let history=db.query(DbStatement::new(history_sql)).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid,name,env,bot_info) VALUES ('old','Old Agent','test','{}')")).await?;
    db.execute(DbStatement::new("INSERT INTO bcs_group_agent_cards (env,group_id,card_template) VALUES ('test','old','{}')")).await?;
    run_sqlite_migrations(&db).await?;run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?,history);
    for table in ["bcs_bots","bcs_group_agent_cards"] {
        let rows=db.query(DbStatement::new(format!("SELECT catalog_labels FROM {table}"))).await?;
        assert_eq!(rows[0].get_string("catalog_labels")?,None);
    }
    // Pre-change column lists remain valid on the expanded schema.
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid,name,env,bot_info) VALUES ('legacy-insert','Legacy Agent','test','{}')")).await?;
    db.execute(DbStatement::new("UPDATE bcs_bots SET agent_card='{}',agent_card_revision=1 WHERE bot_uuid='old'")).await?;
    let old_read=db.query(DbStatement::new("SELECT bot_uuid,agent_card,agent_card_url,agent_card_revision FROM bcs_bots WHERE bot_uuid='old'")).await?;assert_eq!(old_read.len(),1);
    // Exact old-runner algorithm visits only its known migrations and validates their unchanged checksums.
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|m|m.version<=45) {
        validate_applied_sqlite_migration(migration,&applied_sqlite_migration(&db,migration.version).await?.unwrap())?;
        apply_sqlite_migration(&db,migration).await?;
    }
    assert_eq!(current_sqlite_version(&db,true).await?,Some(47));
    Ok(())
}
