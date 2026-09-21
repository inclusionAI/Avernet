use bcs_bot::core::provider_registration::ProviderRegistrationCore;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_relation::RelationCore;
use bcs_bot_store::provider::MemoryBotProviderStore;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{ProviderAuthMode, ProviderCoreService};
use bcs_test_support::contract::provider_registration_core::provider_registration_core_service_contract_tests;
use bcs_test_support::contract::bot_provider::provider_bot_core_service_contract_tests;
use std::sync::Arc;

#[tokio::test]
async fn conformance_scoped_provider_registration_core() {
    for auth_mode in [ProviderAuthMode::StaticBearer, ProviderAuthMode::ProviderAdmin, ProviderAuthMode::AgentPass] {
        exercise_registration_contract(auth_mode).await;
    }
}

async fn exercise_registration_contract(auth_mode: ProviderAuthMode) {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(MemoryProviderStore::new());
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), store.clone()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bots,
        store.clone(),
        store.clone(),
        store.clone(),
    ));
    let admin = ProviderCore::new(
        store.clone(),
        store.clone(),
        store.clone(),
        registry.clone(),
    ).with_bot_provider_repo(metadata.clone());
    let provider = admin.register_provider(
        "Contract Provider".into(),
        None,
        auth_mode,
        "owner".into(),
        None,
        None,
    )
    .await
    .unwrap();
    let core = ProviderRegistrationCore::new(
        store.clone(),
        store.clone(),
        store,
        metadata,
        registry,
        Arc::new(RelationCore::new()),
        "prod".into(),
        vec![],
        OutboundUrlGuard::strict(),
    );
    provider_registration_core_service_contract_tests(&core, &provider.provider.provider_id, "owner").await;
    provider_bot_core_service_contract_tests(&admin, &provider.provider.provider_id, &provider.provider_admin_token, "contract-upstream").await;
}
