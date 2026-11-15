use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbPlugin, DbStatement, DbTransactionStep, DbTransactionStepResult, DbValue, DbError};
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use mysql_async::Opts;

#[path = "../../../test-support/legacy_log_capture.rs"]
mod legacy;

// CI always supplies this service. Ordinary local unit runs remain independent
// of external databases; a configured URL must connect successfully, never skip.
#[test]
fn coverage_mysql_protocols_rollback_failed_steps_and_preserve_parameter_values() {
    let url = match std::env::var("BCS_TEST_MYSQL_URL") {
        Ok(url) => url,
        Err(_) if std::env::var_os("CI").is_none() => {
            eprintln!("MySQL protocol regression not run: set BCS_TEST_MYSQL_URL");
            return;
        }
        Err(error) => panic!("CI requires BCS_TEST_MYSQL_URL for MySQL protocol regressions: {error}"),
    };
    legacy::isolated(|| {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let opts = Opts::from_url(&url).unwrap();
            for protocol in [StatementProtocol::Text, StatementProtocol::Prepared] {
                let mut config = MysqlDbConfig::new().with_database(opts.db_name().unwrap())
                    .with_statement_protocol(protocol).with_connection(MysqlConnectionConfig {
                        connection_type: "direct".into(), host: Some(opts.ip_or_hostname().into()), port: Some(opts.tcp_port()),
                        user: opts.user().map(str::to_string), password: opts.pass().map(str::to_string), extra: Default::default(),
                    });
                config.pool_size = 2; config.min_pool_size = 1;
                let manager = MysqlDbManager::new(config).await.unwrap();
                assert!(manager.is_enabled());
                assert!(manager.has_database("bcs"));
                assert_eq!(manager.database_names(), ["bcs"]);
                assert_eq!(manager.config().statement_protocol, protocol);
                assert!(matches!(manager.query("missing", "SELECT 1").await, Err(DbError::Backend(_))));
                let plugin = MysqlDbPlugin::new(manager.clone(), "bcs");
                assert_eq!(plugin.db(), "bcs");
                assert!(plugin.health_check().await.unwrap().healthy);
                // A normal table is necessary across pool connections; each
                // protocol uses a distinct, test-owned table and removes it.
                let table = format!("coverage_protocol_regression_{protocol}");
                manager.execute("bcs", &format!("DROP TABLE IF EXISTS {table}")).await.unwrap();
                manager.execute("bcs", &format!("CREATE TABLE {table} (id BIGINT PRIMARY KEY AUTO_INCREMENT, body TEXT, payload BLOB)")).await.unwrap();
                let text = "quoted ' text \\ and 中文 ?";
                let inserted = plugin.execute(DbStatement::with_params(format!("INSERT INTO {table} (body, payload) VALUES (?, ?)"),
                    vec![text.into(), DbValue::Bytes(vec![0, 255, 1])])).await.unwrap();
                assert_eq!(inserted.affected_rows, 1); assert!(inserted.last_insert_id.is_some());
                let rows = plugin.query(DbStatement::with_params(format!("SELECT body, payload FROM {table} WHERE id = ?"),
                    vec![DbValue::from(inserted.last_insert_id.unwrap() as i64)])).await.unwrap();
                assert_eq!(rows[0].get_string("body").unwrap().as_deref(), Some(text));
                assert_eq!(rows[0].get_bytes("payload").unwrap(), Some(vec![0, 255, 1]));
                for statement in [DbStatement::new("SELECT * FROM coverage_missing_table"),
                    DbStatement::with_params("SELECT * FROM coverage_missing_table WHERE id = ?", vec![1_i64.into()])] {
                    assert!(plugin.query(statement).await.is_err());
                }
                assert!(plugin.execute(DbStatement::new("INSERT INTO coverage_missing_table VALUES (1)")).await.is_err());
                assert!(plugin.execute(DbStatement::with_params("INSERT INTO coverage_missing_table VALUES (?)", vec![1_i64.into()])).await.is_err());
                let failed = plugin.transaction(vec![
                    DbTransactionStep::Execute(DbStatement::with_params(format!("INSERT INTO {table} (body) VALUES (?)"), vec!["must-rollback".into()])),
                    DbTransactionStep::Query(DbStatement::with_params("SELECT * FROM coverage_missing_table WHERE id = ?", vec![1_i64.into()])),
                ]).await;
                assert!(failed.is_err());
                let rows = plugin.query(DbStatement::new(format!("SELECT COUNT(*) AS n FROM {table}"))).await.unwrap();
                assert_eq!(rows[0].get_i64("n").unwrap(), Some(1), "failed transaction rolls back its successful prefix");
                let result = plugin.transaction(vec![
                    DbTransactionStep::Execute(DbStatement::with_params(format!("UPDATE {table} SET body = ? WHERE id = ?"), vec!["committed".into(), 1_i64.into()])),
                    DbTransactionStep::Query(DbStatement::with_params(format!("SELECT body FROM {table} WHERE id = ?"), vec![1_i64.into()])),
                ]).await.unwrap();
                let DbTransactionStepResult::Rows(rows) = &result[1] else { panic!("query result required"); };
                assert_eq!(rows[0].get_string("body").unwrap().as_deref(), Some("committed"));
                manager.execute_with("bcs", &format!("UPDATE {table} SET body = ? WHERE id = 1"), vec!["direct".into()]).await.unwrap();
                assert_eq!(manager.query_with("bcs", &format!("SELECT body FROM {table}"), vec![]).await.unwrap()[0].get::<String, _>(0).as_deref(), Some("direct"));
                // Prepared execution failures occur after preparation succeeds:
                // duplicate keys must still close the statement and roll back.
                assert!(plugin.execute(DbStatement::with_params(format!("INSERT INTO {table} (id) VALUES (?)"), vec![1_i64.into()])).await.is_err());
                assert!(plugin.transaction(vec![DbTransactionStep::Execute(DbStatement::with_params(
                    format!("INSERT INTO {table} (id) VALUES (?)"), vec![1_i64.into()]))]).await.is_err());
                let table_for_tx = table.clone();
                let body = manager.with_transaction("bcs", |tx| Box::pin(async move {
                    tx.execute(&format!("UPDATE {table_for_tx} SET body = 'unbound' WHERE id = 1"), vec![]).await?;
                    let rows = tx.query(&format!("SELECT body FROM {table_for_tx}"), vec![]).await?;
                    Ok(rows[0].get::<String, _>(0).unwrap())
                })).await.unwrap();
                assert_eq!(body, "unbound");
                assert!(manager.with_transaction("bcs", |tx| Box::pin(async move {
                    tx.query("SELECT * FROM coverage_missing_table", vec![]).await
                })).await.is_err());
                assert!(manager.with_transaction("bcs", |tx| Box::pin(async move {
                    tx.execute("INSERT INTO coverage_missing_table VALUES (1)", vec![]).await
                })).await.is_err());
                assert!(manager.with_transaction("bcs", |tx| Box::pin(async move {
                    tx.execute("INSERT INTO coverage_missing_table VALUES (?)", vec![mysql_async::Value::Int(1)]).await
                })).await.is_err());
                assert!(manager.execute_with("bcs", "INSERT INTO coverage_missing_table VALUES (?)", vec![mysql_async::Value::Int(1)]).await.is_err());
                manager.execute("bcs", &format!("DROP TABLE {table}")).await.unwrap();
                manager.close().await;
            }
            let rows = legacy::rows("bcs_db_mysql::manager");
            assert!(rows.iter().any(|row| row.message.contains("mysql sql error")));
            assert!(rows.iter().any(|row| row.message.contains("mysql transaction failed")));
            assert!(rows.iter().any(|row| row.message.contains("mysql transaction ok")));
        });
    });
}
