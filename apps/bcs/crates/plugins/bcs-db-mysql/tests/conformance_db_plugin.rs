use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
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
        manager.close().await;
    }
}
