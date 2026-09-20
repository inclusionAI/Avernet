use bcs_bot::core::provider_registration::ProviderRegistrationCore;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderRegistrationStore, MemoryProviderStore};
use bcs_relation::RelationCore;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{ProviderAuthMode, ProviderCoreService};
use bcs_test_support::contract::provider_registration_core::provider_registration_core_service_contract_tests;
use std::sync::Arc;

#[tokio::test]
async fn conformance_scoped_provider_registration_core() {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(MemoryProviderStore::new());
    let registry = Arc::new(BotCore::with_provider_repos(
        Arc::new(MemoryBotRepo::with_base_dir(dir.path().into())),
        store.clone(),
        store.clone(),
        store.clone(),
    ));
    let provider = ProviderCore::new(
        store.clone(),
        store.clone(),
        store.clone(),
        registry.clone(),
    )
    .register_provider(
        "Contract Provider".into(),
        None,
        ProviderAuthMode::StaticBearer,
        "owner".into(),
        None,
        None,
    )
    .await
    .unwrap()
    .provider;
    let core = ProviderRegistrationCore::new(
        store.clone(),
        store.clone(),
        store,
        Arc::new(MemoryProviderRegistrationStore::new()),
        registry,
        Arc::new(RelationCore::new()),
        "prod".into(),
        vec![],
        OutboundUrlGuard::strict(),
    );
    provider_registration_core_service_contract_tests(&core, &provider.provider_id, "owner").await;
}
