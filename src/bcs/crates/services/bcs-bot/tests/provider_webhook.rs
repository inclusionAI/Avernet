use std::sync::Arc;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_service_api::{BotDeliveryTarget, BotRegistryCoreService, ProviderAuthMode,
    ProviderBotBinding, ProviderBotBindingRepoPort, ProviderBotCoreService,
    ProviderCoreService, RegisterProviderBotParams, ServiceError};
use serde_json::json;

struct Fixture {
    core: ProviderCore,
    registry: Arc<BotCore>,
    store: Arc<MemoryProviderStore>,
    bots: Arc<MemoryBotRepo>,
    provider_id: String,
    admin: String,
    _dir: tempfile::TempDir,
}

fn params(reference: &str, endpoint: Option<&str>) -> RegisterProviderBotParams {
    RegisterProviderBotParams {
        bot_name: reference.into(), provider_bot_ref: reference.into(),
        owners: vec!["alice".into()], webhook_url: endpoint.map(str::to_string),
        ..Default::default()
    }
}

#[tokio::test]
async fn bot_endpoint_can_be_replaced_and_restored_to_inheritance() {
    let f = fixture().await;
    let a = bot(&f, "a").await;
    let updated = f.core.update_provider_bot_webhook(&f.provider_id, &f.admin, "a",
        Some("https://a.example.com/webhook".into())).await.unwrap();
    assert_eq!(updated.binding.bot_uuid, a.bot_uuid);
    assert_eq!(url(&f, &a.bot_uuid).await, "https://a.example.com/webhook");
    f.core.update_provider_bot_webhook(&f.provider_id, &f.admin, "a", None).await.unwrap();
    assert_eq!(url(&f, &a.bot_uuid).await, "https://shared.example.com/webhook");
}

#[tokio::test]
async fn provider_without_default_requires_bot_endpoint_before_writing() {
    let f = fixture().await;
    let p = f.core.register_provider("Independent".into(), None,
        ProviderAuthMode::StaticBearer, "alice".into(), None, None).await.unwrap();
    let mut missing = params("missing", None);
    missing.bot_uuid = Some("missing-endpoint".into());
    assert!(f.core.register_provider_bot_with_bot_uuid(&p.provider.provider_id,
        &p.provider_admin_token, missing).await.is_err());
    assert!(f.registry.get("missing-endpoint").await.is_none());
    let a = f.core.register_provider_bot_with_bot_uuid(&p.provider.provider_id,
        &p.provider_admin_token, params("a", Some("https://a.example.com/webhook")))
        .await.unwrap().0;
    assert_eq!(url(&f, &a.bot_uuid).await, "https://a.example.com/webhook");
    assert!(f.core.update_provider_bot_webhook(&p.provider.provider_id,
        &p.provider_admin_token, "a", None).await.is_err());
    assert_eq!(url(&f, &a.bot_uuid).await, "https://a.example.com/webhook");
}

#[tokio::test]
async fn repeat_registration_never_silently_updates_an_endpoint() {
    let f = fixture().await;
    let original = params("a", Some("https://a.example.com/webhook"));
    let first = f.core.register_provider_bot_with_bot_uuid(&f.provider_id, &f.admin,
        original.clone()).await.unwrap().0;
    let repeat = f.core.register_provider_bot_with_bot_uuid(&f.provider_id, &f.admin,
        original).await.unwrap().0;
    assert_eq!(first, repeat);
    let error = f.core.register_provider_bot_with_bot_uuid(&f.provider_id, &f.admin,
        params("a", Some("https://other.example.com/webhook"))).await.unwrap_err();
    assert!(matches!(error, ServiceError::Conflict(_)));
    assert_eq!(bot(&f, "a").await.webhook_url, first.webhook_url);
}

#[tokio::test]
async fn endpoint_updates_require_provider_ownership_and_validate_urls() {
    let f = fixture().await;
    let a = bot(&f, "a").await;
    let other = f.core.register_provider("Other".into(), None,
        ProviderAuthMode::StaticBearer, "bob".into(), None, None).await.unwrap();
    for bad in ["", "  ", "file:///tmp/webhook", "http://127.0.0.1/webhook"] {
        assert!(f.core.update_provider_bot_webhook(&f.provider_id, &f.admin,
            "a", Some(bad.into())).await.is_err());
    }
    assert!(matches!(f.core.update_provider_bot_webhook(&f.provider_id,
        &other.provider_admin_token, "a", Some("https://evil.example.com".into()))
        .await.unwrap_err(), ServiceError::Forbidden(_)));
    assert_eq!(url(&f, &a.bot_uuid).await, "https://shared.example.com/webhook");
}

async fn fixture() -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(MemoryProviderStore::new());
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bots.clone(),
        store.clone(), store.clone(), store.clone()));
    let core = ProviderCore::new(store.clone(), store.clone(), store.clone(), registry.clone());
    let provider = core.register_provider("Provider".into(),
        Some("https://shared.example.com/webhook".into()), ProviderAuthMode::StaticBearer,
        "alice".into(), None, None).await.unwrap();
    Fixture { core, registry, store, bots, provider_id: provider.provider.provider_id,
        admin: provider.provider_admin_token, _dir: dir }
}

async fn bot(f: &Fixture, reference: &str) -> ProviderBotBinding {
    f.core.register_provider_bot_with_bot_uuid(&f.provider_id, &f.admin,
        RegisterProviderBotParams { bot_name: reference.into(),
            provider_bot_ref: reference.into(), owners: vec!["alice".into()],
            ..Default::default() }).await.unwrap().0
}

async fn url(f: &Fixture, id: &str) -> String {
    match f.registry.resolve_delivery_target(id).await.unwrap() {
        BotDeliveryTarget::HttpProvider { webhook_url, .. } => webhook_url,
        _ => panic!("expected HTTP provider target"),
    }
}

#[tokio::test]
async fn same_provider_bots_resolve_independent_endpoints() {
    let f = fixture().await;
    let a = bot(&f, "a").await;
    let b = bot(&f, "b").await;
    let legacy = bot(&f, "legacy").await;
    // Populate persisted bindings through their serialization contract.
    // The old model drops webhook_url, making this fail before implementation.
    for (binding, endpoint) in [(&a, "https://a.example.com/webhook"),
        (&b, "https://b.example.com/webhook")] {
        let mut value = serde_json::to_value(binding).unwrap();
        value["webhook_url"] = json!(endpoint);
        let updated: ProviderBotBinding = serde_json::from_value(value).unwrap();
        // Memory insertion is not an upsert; use a fresh store for the overlay.
        let overlay = Arc::new(MemoryProviderStore::new());
        overlay.insert_binding(updated).await.unwrap();
        let registry = BotCore::with_provider_repos(f.bots.clone(),
            f.store.clone(), f.store.clone(), overlay);
        let target = registry.resolve_delivery_target(&binding.bot_uuid).await.unwrap();
        assert!(matches!(target, BotDeliveryTarget::HttpProvider { webhook_url, .. }
            if webhook_url == endpoint));
    }
    assert_eq!(url(&f, &legacy.bot_uuid).await, "https://shared.example.com/webhook");
}

#[tokio::test]
async fn legacy_binding_without_any_endpoint_is_not_runtime_active() {
    let f = fixture().await;
    let p = f.core.register_provider("Independent".into(), None,
        ProviderAuthMode::StaticBearer, "alice".into(), None, None).await.unwrap();
    let a = f.core.register_provider_bot_with_bot_uuid(&p.provider.provider_id,
        &p.provider_admin_token, params("a", Some("https://a.example.com/webhook")))
        .await.unwrap().0;
    // Model an old or externally modified binding that violates the new write invariant.
    f.store.update_binding_webhook_url(&p.provider.provider_id, &a.bot_uuid, None, 1).await.unwrap();
    assert!(f.registry.resolve_delivery_target(&a.bot_uuid).await.is_err());
    assert!(f.registry.list_runtime_active_bot_ids(&[a.bot_uuid]).await.is_empty());
}

#[tokio::test]
async fn plugin_bot_rejects_override_before_registration() {
    let f = fixture().await;
    let mut request = params("plugin", Some("https://plugin.example.com/webhook"));
    request.connection_mode = bcs_service_api::ProviderBotConnectionMode::Plugin;
    request.bot_uuid = Some("plugin-endpoint".into());
    assert!(f.core.register_provider_bot_with_bot_uuid(&f.provider_id, &f.admin, request)
        .await.is_err());
    assert!(f.registry.get("plugin-endpoint").await.is_none());
}

#[tokio::test]
async fn bot_override_preserves_disabled_binding_provider_and_credential_guards() {
    use bcs_service_api::{ProviderRepoPort, ProviderCredentialRepoPort};
    let f = fixture().await;
    let a = f.core.register_provider_bot_with_bot_uuid(&f.provider_id, &f.admin,
        params("a", Some("https://a.example.com/webhook"))).await.unwrap().0;
    f.store.update_binding_disabled(&a.bot_uuid, true, 2).await.unwrap();
    assert!(f.registry.resolve_delivery_target(&a.bot_uuid).await.is_err());
    f.store.update_binding_disabled(&a.bot_uuid, false, 3).await.unwrap();
    f.store.update_provider_disabled(&f.provider_id, true, 4).await.unwrap();
    assert!(f.registry.resolve_delivery_target(&a.bot_uuid).await.is_err());
    assert!(f.core.update_provider_bot_webhook(&f.provider_id, &f.admin, "a",
        Some("https://replacement.example.com/webhook".into())).await.is_err());
    f.store.update_provider_disabled(&f.provider_id, false, 5).await.unwrap();
    f.store.update_credential_disabled(&f.provider_id, "downlink_bcs_to_provider", true, 6)
        .await.unwrap();
    assert!(f.registry.resolve_delivery_target(&a.bot_uuid).await.is_err());
    assert!(f.registry.list_runtime_active_bot_ids(&[a.bot_uuid]).await.is_empty());
}
