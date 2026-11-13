use super::*;

#[test]
fn coverage_mysql_placeholder_scanner_preserves_quoted_and_commented_question_marks() {
    let sql = r#"SELECT ?, 'it''s ?; \'quoted', "a""?\\b", `a``?`, -- ?
        ? # ?
        /* ? */ , ?"#;
    let expected = r#"SELECT 7, 'it''s ?; \'quoted', "a""?\\b", `a``?`, -- ?
        NULL # ?
        /* ? */ , 'last'"#;
    assert_eq!(render_sql(sql, &[Value::Int(7), Value::NULL, Value::from("last")]).unwrap(), expected);
    assert!(matches!(render_sql("SELECT ?, ?", &[Value::Int(1)]), Err(DbError::InvalidInput(_))));
    assert!(matches!(render_sql("SELECT '?'", &[Value::Int(1)]), Err(DbError::InvalidInput(_))));
    assert_eq!(render_sql("SELECT '?'", &[]).unwrap(), "SELECT '?'");
}

#[path = "../../../test-support/legacy_log_capture.rs"]
mod legacy;

#[test]
fn coverage_mysql_diagnostic_levels_use_exact_latency_boundaries() {
    legacy::isolated(|| {
        for duration in [100, 101, 1000, 1001] { log_sql_ok("fixture", "query", "SELECT 1", duration, 1, 0); }
        log_sql_error("fixture", "execute", "invalid SQL", 1, "syntax error");
        log_tx_ok("fixture", 200, 2); log_tx_ok("fixture", 201, 3);
        let rows = legacy::rows("bcs_db_mysql::manager");
        assert_eq!(rows.len(), 7);
        assert_eq!(rows.iter().map(|r| r.level).collect::<Vec<_>>(), [
            tracing::log::Level::Info, tracing::log::Level::Warn, tracing::log::Level::Warn,
            tracing::log::Level::Error, tracing::log::Level::Error, tracing::log::Level::Info, tracing::log::Level::Warn,
        ]);
        for row in &rows { row.assert_field("db", "fixture"); }
        rows[4].assert_field("error", "syntax error");
        rows[5].assert_field("statements", "2"); rows[6].assert_field("statements", "3");
    });
}

#[tokio::test]
async fn coverage_mysql_disabled_and_missing_datasources_fail_before_running_sql() {
    let mut manager = MysqlDbManager { pools: Arc::new(HashMap::new()),
        statement_executors: Arc::new(HashMap::new()), config: MysqlDbConfig::new(), enabled: false };
    let error = manager.query("absent", "SELECT 1").await.unwrap_err();
    assert!(error.to_string().contains("disabled"));
    assert!(manager.execute("absent", "SELECT 1").await.is_err());
    assert!(manager.query_with("absent", "SELECT ?", vec![Value::Int(1)]).await.is_err());
    assert!(manager.execute_with("absent", "SELECT ?", vec![Value::Int(1)]).await.is_err());
    assert!(manager.with_transaction::<_, ()>("absent", |_| Box::pin(async { panic!("disabled datasource cannot execute work") })).await.is_err());
    manager.enabled = true;
    assert!(manager.query("absent", "SELECT 1").await.unwrap_err().to_string().contains("not found"));
    assert!(manager.statement_executor("absent").err().unwrap().to_string().contains("not found"));
    assert!(!manager.has_database("absent"));
    assert!(manager.database_names().is_empty());
    manager.close().await;
}
