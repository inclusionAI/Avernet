use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use mysql_async::Opts;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn conformance_mysql_db_plugin_for_text_and_prepared_protocols() {
    let mysql_url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set when running the ignored MySQL contract");
    let opts = Opts::from_url(&mysql_url).expect("BCS_TEST_MYSQL_URL must be a valid MySQL URL");

    for protocol in [StatementProtocol::Text, StatementProtocol::Prepared] {
        let mut config = MysqlDbConfig::new()
            .with_database(
                opts.db_name()
                    .expect("BCS_TEST_MYSQL_URL must include a database name"),
            )
            .with_connection(MysqlConnectionConfig {
                connection_type: "direct".to_string(),
                host: Some(opts.ip_or_hostname().to_string()),
                port: Some(opts.tcp_port()),
                user: opts.user().map(str::to_string),
                password: opts.pass().map(str::to_string),
                extra: Default::default(),
            })
            .with_statement_protocol(protocol);
        config.pool_size = 4;
        config.min_pool_size = 1;

        let manager = MysqlDbManager::new(config)
            .await
            .expect("create MySQL contract manager");
        let plugin = MysqlDbPlugin::new(manager.clone(), "bcs");
        bcs_test_support::contract::plugin::db_plugin_contract_tests(&plugin).await;
        binary_collation_text_round_trip(&plugin).await;
        manager.close().await;
    }
}

async fn binary_collation_text_round_trip(plugin: &MysqlDbPlugin) {
    plugin.execute(DbStatement::new("CREATE TABLE IF NOT EXISTS contract_binary_collation (
        slug VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
        name VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        payload BLOB NOT NULL)")).await.unwrap();
    plugin.execute(DbStatement::new("DELETE FROM contract_binary_collation")).await.unwrap();
    plugin.execute(DbStatement::with_params(
        "INSERT INTO contract_binary_collation (slug, name, payload) VALUES (?, ?, ?)",
        vec!["first".into(), "提供商".into(), DbValue::Bytes(b"first".to_vec())],
    )).await.unwrap();
    // Exercise column metadata in both unbound text queries and bound queries
    // under each configured statement protocol.
    for statement in [
        DbStatement::new("SELECT slug, name, payload FROM contract_binary_collation"),
        DbStatement::with_params(
            "SELECT slug, name, payload FROM contract_binary_collation WHERE slug = ?",
            vec!["first".into()],
        ),
    ] {
        let rows = plugin.query(statement).await.unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].get_string("slug").unwrap().as_deref(), Some("first"));
        assert_eq!(rows[0].get_string("name").unwrap().as_deref(), Some("提供商"));
        assert_eq!(rows[0].get_bytes("payload").unwrap(), Some(b"first".to_vec()));
    }
}
