use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::{BotCapabilities, ProviderBotBinding, ProviderBotBindingRepoPort};

fn record(id: &str, mode: BotConnectionMode) -> BotProviderRecord {
    BotProviderRecord {
        bot_uuid: id.into(), provider_id: "provider-a".into(), provider_bot_ref: id.into(),
        connection_mode: mode, webhook_url: None, is_deleted: false,
    }
}

fn caps() -> BotCapabilities {
    BotCapabilities { name: Some("Provider Bot".into()), visibility: "protected".into(), ..Default::default() }
}

pub async fn bot_provider_repo_port_contract_tests(repo: &dyn BotProviderRepoPort, bindings: &dyn ProviderBotBindingRepoPort) {
    let upstream = record("upstream", BotConnectionMode::Plugin);
    repo.create_provider_bot(upstream.clone(), caps(), "owner-a", "test-upstream-runtime").await.unwrap();
    assert_eq!(repo.get_provider_bot("upstream").await.unwrap(), Some(upstream));
    assert!(bindings.get_binding_by_bot_uuid("upstream").await.unwrap().is_none());
    let gateway = record("gateway", BotConnectionMode::Gateway);
    repo.create_provider_bot(gateway.clone(), caps(), "owner-a", "test-gateway-runtime").await.unwrap();
    assert_eq!(repo.get_provider_bot("gateway").await.unwrap(), Some(gateway));
    assert!(bindings.get_binding_by_bot_uuid("gateway").await.unwrap().is_some());
    // Repeated affiliation is a no-op, not a timestamp/version update.
    for id in ["upstream", "gateway"] {
        let existing = repo.get_provider_bot(id).await.unwrap().unwrap();
        repo.attach_provider_bot(existing.clone()).await.unwrap();
        repo.attach_provider_bot(existing.clone()).await.unwrap();
        assert_eq!(repo.get_provider_bot(id).await.unwrap(), Some(existing));
    }

    let mut duplicate = record("other-id", BotConnectionMode::Gateway);
    duplicate.provider_bot_ref = "upstream".into();
    assert!(repo.create_provider_bot(duplicate, caps(), "owner-a", "test-other-runtime").await.is_err());
    assert!(repo.get_provider_bot("other-id").await.unwrap().is_none());

    repo.update_provider_webhook("provider-a", "gateway", Some("https://example.org/bot".into()), 2000).await.unwrap();
    let updated = repo.get_provider_bot("gateway").await.unwrap().unwrap();
    assert_eq!(updated.webhook_url.as_deref(), Some("https://example.org/bot"));
    assert_eq!(bindings.get_binding_by_bot_uuid("gateway").await.unwrap().unwrap().webhook_url, updated.webhook_url);
    repo.update_provider_webhook("provider-a", "gateway", updated.webhook_url.clone(), 2000).await.unwrap();
    repo.update_provider_webhook("provider-a", "gateway", None, 2000).await.unwrap();
    assert!(repo.get_provider_bot("gateway").await.unwrap().unwrap().webhook_url.is_none());
    assert!(bindings.get_binding_by_bot_uuid("gateway").await.unwrap().unwrap().webhook_url.is_none());
    assert!(repo.attach_provider_bot(updated).await.is_err(), "stale affiliation must not restore a previous webhook");
    assert!(repo.update_provider_webhook("provider-a", "upstream", Some("https://example.org/bot".into()), 2000).await.is_err());

    repo.delete_provider_bot("provider-a", "gateway", 3000).await.unwrap();
    assert!(repo.get_provider_bot("gateway").await.unwrap().unwrap().is_deleted);
    assert!(bindings.get_binding_by_bot_uuid("gateway").await.unwrap().unwrap().disabled);
    assert!(!repo.delete_provider_bot("provider-a", "gateway", 3000).await.unwrap());
    assert!(repo.update_provider_webhook("provider-a", "gateway", None, 3000).await.is_err());
    assert!(repo.create_provider_bot(record("gateway", BotConnectionMode::Gateway), caps(), "owner-a", "test-resurrection-runtime").await.is_err());
}

/// Binding consumer contract, exercised against the compatibility projection.
pub async fn provider_bot_binding_repo_port_contract_tests(repo: &dyn ProviderBotBindingRepoPort, bots: &dyn BotProviderRepoPort) {
    bots.create_provider_bot(record("projection", BotConnectionMode::Plugin), caps(), "owner-a", "test-projection-runtime").await.unwrap();
    let binding = ProviderBotBinding {
        bot_uuid: "projection".into(), provider_id: "provider-a".into(), provider_bot_ref: "projection".into(),
        webhook_url: None, disabled: false, created_at: 1000, updated_at: 1000,
    };
    assert!(repo.get_binding_by_bot_uuid("projection").await.unwrap().is_none());
    repo.insert_binding(binding).await.unwrap();
    assert_eq!(bots.get_provider_bot("projection").await.unwrap().unwrap().connection_mode, BotConnectionMode::Gateway);
    assert_eq!(repo.get_binding_by_provider_ref("provider-a", "projection").await.unwrap().unwrap().bot_uuid, "projection");
    assert!(repo.list_bindings_by_provider("provider-a").await.unwrap().iter().any(|binding| binding.bot_uuid == "projection"));
    assert!(repo.update_binding_webhook_url("other-provider", "projection", None, 2000).await.is_err());
    repo.update_binding_webhook_url("provider-a", "projection", Some("https://example.org/changed"), 2000).await.unwrap().unwrap();
    assert_eq!(bots.get_provider_bot("projection").await.unwrap().unwrap().webhook_url.as_deref(), Some("https://example.org/changed"));
    repo.update_binding_disabled("projection", true, 3000).await.unwrap().unwrap();
    assert!(bots.get_provider_bot("projection").await.unwrap().unwrap().is_deleted);
    assert!(repo.get_binding_by_bot_uuid("projection").await.unwrap().unwrap().disabled);
    assert!(repo.update_binding_disabled("projection", false, 4000).await.is_err());
}

/// The caller supplies one active Bot-owned Provider identity to delete.
pub async fn provider_bot_core_service_contract_tests(
    core: &dyn bcs_service_api::ProviderBotCoreService,
    provider_id: &str, admin_token: &str, provider_bot_ref: &str,
) {
    assert!(core.delete_registered_provider_bot(provider_id, "invalid-admin-token", provider_bot_ref).await.is_err());
    assert!(core.delete_registered_provider_bot("other-provider", admin_token, provider_bot_ref).await.is_err());
    let first = core.delete_registered_provider_bot(provider_id, admin_token, provider_bot_ref).await.unwrap().unwrap();
    assert!(first.deleted);
    let repeated = core.delete_registered_provider_bot(provider_id, admin_token, provider_bot_ref).await.unwrap().unwrap();
    assert_eq!(first.bot_uuid, repeated.bot_uuid);
    assert!(!repeated.deleted);
    assert!(core.delete_registered_provider_bot(provider_id, admin_token, "unknown-ref").await.unwrap().is_none());
}
