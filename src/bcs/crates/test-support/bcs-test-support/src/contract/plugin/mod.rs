//! Plugin contract harnesses.

use bcs_cache_api::CachePlugin;
use bcs_db_api::DbPlugin;
use bcs_service_api::port::secret::{SecretAccessPort, SecretRecord};

pub async fn cache_plugin_contract_tests<P: CachePlugin>(plugin: &P) {
    crate::cache_plugin_contract_tests(plugin).await;
}

pub async fn db_plugin_contract_tests<P: DbPlugin>(plugin: &P) {
    crate::db_plugin_contract_tests(plugin).await;
}

pub async fn secret_access_contract_tests<P, F>(plugin: &P, seed: F)
where
    P: SecretAccessPort,
    F: FnOnce() -> SecretRecord,
{
    crate::secret_access_contract_tests(plugin, seed).await;
}
