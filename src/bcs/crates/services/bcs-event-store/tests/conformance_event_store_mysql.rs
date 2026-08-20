#![allow(clippy::expect_used, clippy::unwrap_used)]

mod common;

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use bcs_event_store::DbEventStore;
use bcs_test_support::contract::repo::{
    event_delivery_repo_port_contract_tests, event_repo_port_contract_tests,
};
use mysql_async::Opts;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs this test against its MySQL service"]
async fn mysql_event_store_passes_contract() {
    let mysql_url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL contract");
    let opts = Opts::from_url(&mysql_url).expect("valid BCS_TEST_MYSQL_URL");
    let database = opts
        .db_name()
        .expect("BCS_TEST_MYSQL_URL includes a database name");
    let mut config = MysqlDbConfig::new()
        .with_database(database)
        .with_connection(MysqlConnectionConfig {
            connection_type: "direct".to_string(),
            host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string),
            password: opts.pass().map(str::to_string),
            extra: BTreeMap::new(),
        })
        .with_statement_protocol(StatementProtocol::Text);
    config.pool_size = 2;
    config.min_pool_size = 1;

    let manager = MysqlDbManager::new(config)
        .await
        .expect("open MySQL contract datasource");
    let plugin = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
    apply_eventing_migration(plugin.as_ref()).await;
    let repo = DbEventStore::mysql(plugin);

    event_repo_port_contract_tests(
        &repo,
        common::subscription("sub-mysql-contract"),
        common::append("evt-mysql-contract", "mysql:group:created", "group.created"),
    )
    .await;
    let mut delivery_subscription = common::subscription("sub-mysql-delivery");
    delivery_subscription.subscription.env = "contract-delivery-mysql".to_string();
    let mut delivery_append =
        common::append("evt-mysql-delivery", "mysql:delivery", "group.created");
    delivery_append.env = delivery_subscription.subscription.env.clone();
    event_delivery_repo_port_contract_tests(&repo, delivery_subscription, delivery_append).await;
    manager.close().await;
}

async fn apply_eventing_migration(db: &dyn DbPlugin) {
    let migration = include_str!("../../../../migrations/mysql/009_eventing.sql");
    for statement in migration
        .split(';')
        .map(str::trim)
        .filter(|sql| !sql.is_empty())
    {
        db.execute(DbStatement::new(statement))
            .await
            .unwrap_or_else(|error| {
                panic!("apply Eventing migration statement: {error}\n{statement}")
            });
    }
}
