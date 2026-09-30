use bcs_cache_local::InMemoryCachePlugin;
use bcs_test_support::cache_plugin_contract_tests;

#[tokio::test]
async fn in_memory_cache_plugin_passes_contract() {
    let plugin = InMemoryCachePlugin::new();
    cache_plugin_contract_tests(&plugin).await;
}
