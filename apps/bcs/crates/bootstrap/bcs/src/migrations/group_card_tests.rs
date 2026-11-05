use super::*;
use bcs_db_local::LocalSqliteDbPlugin;

#[tokio::test]
async fn group_card_upgrade_preserves_history_and_does_not_backfill_groups() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;
    run_sqlite_bootstrap_tables(&db).await?;
    for migration in SQLITE_VERSIONED_MIGRATIONS.iter().filter(|m| m.version <= 40) {
        apply_sqlite_migration(&db, migration).await?;
    }
    let history_sql = "SELECT * FROM bcs_schema_migrations WHERE version <= 40 ORDER BY version";
    let history = db.query(DbStatement::new(history_sql)).await?;
    db.execute(DbStatement::new(
        "INSERT INTO bcs_groups (group_id, env, status, driver_bot, label, group_strategy) \
         VALUES ('old-group', 'env-a', 'active', 'manager', '旧群', 'manager_worker')",
    )).await?;
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, history);
    let groups = db.query(DbStatement::new("SELECT label FROM bcs_groups WHERE group_id = 'old-group'")).await?;
    assert_eq!(groups[0].get_string("label")?.as_deref(), Some("旧群"));
    assert!(db.query(DbStatement::new("SELECT * FROM bcs_group_agent_cards")).await?.is_empty());
    let row = db.query(DbStatement::new("SELECT name FROM bcs_schema_migrations WHERE version = 41")).await?;
    assert_eq!(row[0].get_string("name")?.as_deref(), Some("group_agent_cards"));
    let columns = db.query(DbStatement::new("PRAGMA table_info(bcs_group_agent_cards)")).await?;
    let names = columns.iter().map(|row| db_get_column::<String>(row, "name")).collect::<DbResult<Vec<_>>>()?;
    assert_eq!(names, vec!["id", "gmt_create", "gmt_modified", "env", "group_id", "card_template"]);
    let insert = "INSERT INTO bcs_group_agent_cards (env, group_id, card_template) VALUES (?, ?, ?)";
    let long_json = serde_json::json!({"name": "群卡".repeat(40_000)}).to_string();
    let params = vec![DbValue::from("env-a"), DbValue::from("old-group"), DbValue::from(long_json.clone())];
    db.execute(DbStatement::with_params(insert, params.clone())).await?;
    assert!(db.execute(DbStatement::with_params(insert, params)).await.is_err());
    db.execute(DbStatement::with_params(insert, vec!["env-b".into(), "old-group".into(), "{}".into()])).await?;
    let saved = db.query(DbStatement::new("SELECT card_template FROM bcs_group_agent_cards WHERE env = 'env-a'")).await?;
    assert_eq!(saved[0].get_string("card_template")?, Some(long_json));
    Ok(())
}
