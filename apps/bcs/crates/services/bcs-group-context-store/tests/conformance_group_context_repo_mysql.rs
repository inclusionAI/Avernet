#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use bcs_domain::{GroupContextPolicy, ScopeLevel};
use bcs_group_context_store::{GroupContextRepo, NewGroupContextEntry, SqlGroupContextStore};
use chrono::{TimeZone, Utc};
use mysql_async::Opts;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to a disposable MySQL database; CI runs this contract"]
async fn mysql_group_context_timestamps_roundtrip_in_utc_and_non_utc_sessions() {
    let mysql_url = std::env::var("BCS_TEST_MYSQL_URL")
        .expect("BCS_TEST_MYSQL_URL must be set for the ignored MySQL contract");
    let opts = Opts::from_url(&mysql_url).expect("valid BCS_TEST_MYSQL_URL");
    let database = opts
        .db_name()
        .expect("BCS_TEST_MYSQL_URL includes a database name");
    let mysql_pass = opts.pass().map(str::to_string);
    let mut config = MysqlDbConfig::new()
        .with_database(database)
        .with_connection(MysqlConnectionConfig {
            connection_type: "direct".to_string(),
            host: Some(opts.ip_or_hostname().to_string()),
            port: Some(opts.tcp_port()),
            user: opts.user().map(str::to_string),
            password: mysql_pass,
            extra: BTreeMap::new(),
        })
        .with_statement_protocol(StatementProtocol::Text);
    config.pool_size = 1;
    config.min_pool_size = 1;

    let manager = MysqlDbManager::new(config)
        .await
        .expect("open MySQL contract datasource");
    let plugin = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
    apply_mysql_group_context_migration(plugin.as_ref()).await;

    for (timezone, suffix) in [("+00:00", "utc"), ("+08:00", "plus-08")] {
        set_session_timezone(plugin.as_ref(), timezone).await;
        clear_group_contexts(plugin.as_ref()).await;
        assert_group_context_roundtrip(plugin.clone(), suffix).await;
    }

    manager.close().await;
}

async fn apply_mysql_group_context_migration(db: &dyn DbPlugin) {
    let migration = include_str!("../../../../migrations/mysql/033_group_context.sql");
    for statement in migration
        .split(';')
        .map(str::trim)
        .filter(|sql| !sql.is_empty())
    {
        db.execute(DbStatement::new(statement))
            .await
            .unwrap_or_else(|error| {
                panic!("apply group context migration statement: {error}\n{statement}")
            });
    }
}

async fn set_session_timezone(db: &dyn DbPlugin, timezone: &str) {
    db.execute(DbStatement::new(format!(
        "SET SESSION time_zone = '{timezone}'"
    )))
    .await
    .expect("set MySQL session timezone");
}

async fn clear_group_contexts(db: &dyn DbPlugin) {
    db.execute(DbStatement::new("DELETE FROM bcs_group_contexts"))
        .await
        .expect("clear disposable group context test table");
}

async fn assert_group_context_roundtrip(plugin: Arc<MysqlDbPlugin>, suffix: &str) {
    let repo = SqlGroupContextStore::oceanbase(plugin);
    let valid_from = Utc
        .timestamp_millis_opt(1_728_374_400_000)
        .single()
        .unwrap();
    let valid_to = Utc
        .timestamp_millis_opt(1_728_374_460_000)
        .single()
        .unwrap();
    let name = format!("timezone_{suffix}");
    let scope = format!("scope-{suffix}");

    let first = repo
        .create_active(NewGroupContextEntry {
            context_id: format!("ctx-{suffix}"),
            name: name.clone(),
            scope_level: ScopeLevel::Session,
            scope: scope.clone(),
            content: "v1".into(),
            visible_to: GroupContextPolicy::users(["judge_bot"]),
            collect_from: GroupContextPolicy::users(["judge_bot"]),
            supersedes: None,
            change_reason: None,
            valid_from,
            valid_to: Some(valid_to),
            tx_time: valid_from,
        })
        .await
        .expect("create active group context under the test timezone");

    assert_eq!(first.valid_from, valid_from);
    assert_eq!(first.valid_to, Some(valid_to));
    assert_eq!(first.tx_time, valid_from);

    let loaded = repo
        .get_active(&name, &scope)
        .await
        .expect("load active group context")
        .expect("active group context exists");
    assert_eq!(loaded.id, first.id);
    assert_eq!(loaded.valid_from, valid_from);
    assert_eq!(loaded.valid_to, Some(valid_to));
    assert_eq!(loaded.tx_time, valid_from);

    let second = repo
        .supersede_active(
            &name,
            &scope,
            first.id,
            NewGroupContextEntry {
                context_id: first.context_id.clone(),
                name: name.clone(),
                scope_level: ScopeLevel::Session,
                scope: scope.clone(),
                content: "v2".into(),
                visible_to: GroupContextPolicy::users(["judge_bot"]),
                collect_from: GroupContextPolicy::users(["judge_bot"]),
                supersedes: Some(first.id),
                change_reason: None,
                valid_from: valid_to,
                valid_to: None,
                tx_time: valid_to,
            },
        )
        .await
        .expect("supersede active group context under the test timezone");

    assert_eq!(second.valid_from, valid_to);
    assert_eq!(second.valid_to, None);
    assert_eq!(second.tx_time, valid_to);

    let versions = repo
        .list_versions(&first.context_id)
        .await
        .expect("load group context versions");
    let old = versions
        .iter()
        .find(|entry| entry.id == first.id)
        .expect("superseded row still exists");
    assert!(old.is_superseded);
    assert_eq!(old.gmt_superseded, Some(valid_to));

}
