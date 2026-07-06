use std::time::{Duration, SystemTime, UNIX_EPOCH};

use bcs_cache_redis::RedisCachePlugin;
use bcs_config::RedisCacheLoader;
use bcs_test_support::{CachePluginContractOptions, cache_plugin_contract_tests_with_options};

#[tokio::test]
#[ignore = "requires a reachable Redis-compatible endpoint; set BCS_REDIS_* env vars"]
async fn redis_plugin_passes_contract() {
    let config = RedisCacheLoader::config_from_env()
        .unwrap_or_else(|err| panic!("load BCS_REDIS_* config: {}", err));
    let zone = std::env::var("BCS_REDIS_ZONE").unwrap_or_else(|_| "default".to_string());
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    let options = CachePluginContractOptions::new(
        format!("bcs-cache-redis-contract:{}:", unique),
        Duration::from_secs(1),
        Duration::from_secs(2),
    );
    let plugin = RedisCachePlugin::connect(config, zone)
        .await
        .unwrap_or_else(|err| panic!("connect to Redis-compatible cache: {}", err));

    cache_plugin_contract_tests_with_options(&plugin, options).await;
}
