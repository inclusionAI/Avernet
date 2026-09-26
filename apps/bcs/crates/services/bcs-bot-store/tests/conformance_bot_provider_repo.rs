use std::sync::Arc;
use bcs_bot_store::{DbProviderStore, MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::{MemoryBotProviderStore, ProviderBindingProjection};
use bcs_service_api::bot_provider::DownlinkDetectionSource;
use bcs_test_support::contract::bot_provider::{bot_provider_repo_port_contract_tests, provider_bot_binding_repo_port_contract_tests};

#[path = "common/bot_provider.rs"]
mod database;

#[tokio::test]
async fn conformance_db_bot_provider_metadata_and_projection() {
    for source in [DownlinkDetectionSource::Binding, DownlinkDetectionSource::BotConnectionMode] {
    let store = Arc::new(DbProviderStore::sqlite(database::sqlite().await));
    let projection = ProviderBindingProjection::new(store.clone(), store.clone(), source);
    bot_provider_repo_port_contract_tests(store.as_ref(), &projection).await;
    provider_bot_binding_repo_port_contract_tests(&projection, store.as_ref()).await;
    }
}

#[tokio::test]
async fn conformance_memory_bot_provider_metadata_and_projection() {
    for source in [DownlinkDetectionSource::Binding, DownlinkDetectionSource::BotConnectionMode] {
    let dir = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let legacy = Arc::new(MemoryProviderStore::new());
    let metadata = Arc::new(MemoryBotProviderStore::new(bots, legacy.clone()));
    let projection = ProviderBindingProjection::new(legacy, metadata.clone(), source);
    bot_provider_repo_port_contract_tests(metadata.as_ref(), &projection).await;
    provider_bot_binding_repo_port_contract_tests(&projection, metadata.as_ref()).await;
    }
}
