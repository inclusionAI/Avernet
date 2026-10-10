use std::sync::Arc;

use bcs_bot::{BotCore, ProviderCore, ProviderManagement};
use bcs_bot_store::MemoryProviderStore;
use bcs_service_api::{ProviderAuthMode, ProviderBasicInfo, ProviderCoreService,
    ProviderManagementService, ProviderRepoPort, ServiceError};
use bcs_test_support::NoopRelationCoreService;
use bcs_test_support::contract::provider_slug::{provider_core_service_contract_tests,
    provider_management_service_contract_tests};

#[tokio::test]
async fn conformance_provider_discovery_includes_disabled_and_legacy_versions() {
    let store = Arc::new(MemoryProviderStore::new());
    let registry = Arc::new(BotCore::memory());
    let core = Arc::new(ProviderCore::new(store.clone(), store.clone(), store.clone(), registry.clone()));
    let service = ProviderManagement::new(core.clone(), core.clone(), registry,
        Arc::new(NoopRelationCoreService));
    let registered = core.register_provider_with_slug("Legacy".into(), None,
        ProviderAuthMode::AgentPass, "alice".into(), None, None,
        Some("contract-legacy".into()), None).await.unwrap();
    let id = &registered.provider.provider_id;
    // Downlink disabled and Provider disabled are independent states. An absent
    // protocol_version in historical config resolves to 1.0 for discovery.
    store.update_provider_metadata(id, None,
        Some(r#"{"downlink":{"auth_mode":"agentpass","enabled":false}}"#), None, 2)
        .await.unwrap();
    let mut expected = ProviderBasicInfo {
        provider_id: id.clone(), slug: "contract-legacy".into(), name: "Legacy".into(),
        auth_mode: ProviderAuthMode::AgentPass, enabled: true, protocol_version: "1.0".into(),
        created_at: registered.provider.created_at, updated_at: 2,
    };
    provider_core_service_contract_tests(core.as_ref(), &expected).await;
    provider_management_service_contract_tests(&service, &expected).await;
    store.update_provider_disabled(id, true, 3).await.unwrap();
    expected.enabled = false;
    expected.updated_at = 3;
    provider_core_service_contract_tests(core.as_ref(), &expected).await;
    provider_management_service_contract_tests(&service, &expected).await;
}

#[tokio::test]
async fn corrupt_provider_config_is_an_error_at_both_boundaries() {
    let store = Arc::new(MemoryProviderStore::new());
    let registry = Arc::new(BotCore::memory());
    let core = Arc::new(ProviderCore::new(store.clone(), store.clone(), store.clone(), registry.clone()));
    let service = ProviderManagement::new(core.clone(), core.clone(), registry,
        Arc::new(NoopRelationCoreService));
    let registered = core.register_provider_with_slug("Corrupt".into(), None,
        ProviderAuthMode::StaticBearer, "alice".into(), None, None,
        Some("contract-corrupt".into()), None).await.unwrap();
    store.update_provider_metadata(&registered.provider.provider_id, None,
        Some("not json"), None, 2).await.unwrap();
    assert!(matches!(core.get_provider_by_slug("contract-corrupt").await,
        Err(ServiceError::InternalError(_))));
    assert!(matches!(service.get_provider_by_slug("contract-corrupt").await,
        Err(ServiceError::InternalError(_))));
}
