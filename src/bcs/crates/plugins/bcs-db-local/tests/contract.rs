use bcs_db_local::LocalSqliteDbPlugin;
use bcs_test_support::db_plugin_contract_tests;

#[tokio::test]
async fn local_sqlite_db_plugin_passes_contract() {
    let plugin = match LocalSqliteDbPlugin::new() {
        Ok(plugin) => plugin,
        Err(err) => panic!("create local sqlite db plugin: {}", err),
    };
    db_plugin_contract_tests(&plugin).await;
}
