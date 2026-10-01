use std::sync::Arc;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::{MemoryBotProviderStore, ProviderBindingProjection};
use bcs_service_api::bot_provider::{BotConnectionMode, DownlinkDetectionSource};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::{BotRegistryCoreService, BotDeliveryTarget, ProviderAuthMode, ProviderBotBindingRepoPort,
    ProviderBotConnectionMode, ProviderBotCoreService, ProviderCoreService, RegisterProviderBotParams};

#[test]
fn provider_management_keeps_metadata_persistence_behind_the_core_contract() {
    let application = include_str!("../src/application/provider.rs");
    assert!(!application.contains("BotProviderRepoPort"), "Provider application must not access the metadata repository directly");
}

#[tokio::test]
async fn deleting_unmigrated_gateway_also_disables_legacy_binding() {
    use bcs_service_api::{BotCapabilities, BotRepoPort, ProviderBotBinding};
    let dir = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    bots.register_with_owner_and_token("legacy".into(), BotCapabilities::default(), "alice", "test-legacy-runtime").await.unwrap();
    let providers = Arc::new(MemoryProviderStore::new());
    providers.insert_binding(ProviderBotBinding {
        bot_uuid: "legacy".into(), provider_id: "provider-a".into(), provider_bot_ref: "legacy-ref".into(),
        webhook_url: None, disabled: false, created_at: 1, updated_at: 1,
    }).await.unwrap();
    let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
    let projection = Arc::new(ProviderBindingProjection::new(providers.clone(), metadata.clone(), DownlinkDetectionSource::Binding));
    let registry = BotCore::with_provider_repos(bots, providers.clone(), providers.clone(), projection)
        .with_bot_provider_repo(metadata.clone());
    assert!(registry.soft_delete("legacy").await);
    assert!(providers.get_binding_by_bot_uuid("legacy").await.unwrap().unwrap().disabled);
    assert!(metadata.get_provider_bot("legacy").await.unwrap().unwrap().is_deleted);
    assert!(registry.try_get("legacy").await.unwrap().is_none());
}

#[tokio::test]
async fn switching_upstream_preserves_membership_and_rejects_takeover_before_owner_edges() {
    use bcs_bot::Bot;
    use bcs_relation::RelationCore;
    use bcs_service_api::{BotManagementService, RelationCoreService, SwitchDeliveryToProviderCommand};
    for source in [DownlinkDetectionSource::Binding, DownlinkDetectionSource::BotConnectionMode] {
        let dir = tempfile::tempdir().unwrap();
        let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
        let providers = Arc::new(MemoryProviderStore::new());
        let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
        let projection = Arc::new(ProviderBindingProjection::new(providers.clone(), metadata.clone(), source));
        let registry = Arc::new(BotCore::with_provider_repos(bots, providers.clone(), providers.clone(), projection.clone())
            .with_bot_provider_repo(metadata.clone()));
        let admin = ProviderCore::new(providers.clone(), providers.clone(), projection, registry.clone())
            .with_bot_provider_repo(metadata.clone());
        let provider = admin.register_provider("Provider".into(), Some("https://example.org/hook".into()), ProviderAuthMode::StaticBearer, "alice".into(), None, None).await.unwrap();
        let other = admin.register_provider("Other".into(), Some("https://example.org/hook".into()), ProviderAuthMode::StaticBearer, "bob".into(), None, None).await.unwrap();
        let (upstream, _) = admin.register_provider_bot_with_bot_uuid(&provider.provider.provider_id, &provider.provider_admin_token,
            RegisterProviderBotParams { bot_name: "Original".into(), provider_bot_ref: "external:alice".into(), owners: vec!["alice".into()],
                connection_mode: ProviderBotConnectionMode::Plugin, ..Default::default() }).await.unwrap();
        let token = registry.load_token(&upstream.bot_uuid).await;
        let relation = Arc::new(RelationCore::memory());
        let bot = Bot::new(registry.clone()).with_bot_core(registry.clone()).with_relation(relation.clone());
        assert!(bot.switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: upstream.bot_uuid.clone(), provider_id: other.provider.provider_id, provider_bot_ref: "external:bob".into(),
            name: None, summary: None,
        }).await.is_err());
        assert!(relation.get_edge("human_bob", &upstream.bot_uuid, &bcs_config::resolve_env_str()).await.unwrap().is_none(),
            "a rejected Provider takeover must not create an owner edge");
        bot.switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: upstream.bot_uuid.clone(), provider_id: provider.provider.provider_id, provider_bot_ref: "external:alice".into(),
            name: None, summary: None,
        }).await.unwrap();
        assert_eq!(metadata.get_provider_bot(&upstream.bot_uuid).await.unwrap().unwrap().connection_mode, BotConnectionMode::Gateway);
        assert!(providers.get_binding_by_bot_uuid(&upstream.bot_uuid).await.unwrap().is_some());
        assert_eq!(registry.load_token(&upstream.bot_uuid).await, token);
    }
}

#[tokio::test]
async fn admin_modes_and_gateway_mutations_keep_both_views_consistent() {
    use bcs_bot::{BotControlPlaneCore, ProviderManagement};
    use bcs_service_api::{BotControlPlaneCoreService, DeleteProviderBotCommand, ProviderManagementService};
    for source in [DownlinkDetectionSource::Binding, DownlinkDetectionSource::BotConnectionMode] {
        let dir = tempfile::tempdir().unwrap();
        let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
        let providers = Arc::new(MemoryProviderStore::new());
        let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
        let projection = Arc::new(ProviderBindingProjection::new(providers.clone(), metadata.clone(), source));
        let registry = Arc::new(BotCore::with_provider_repos(bots.clone(), providers.clone(), providers.clone(), projection.clone())
            .with_bot_provider_repo(metadata.clone()));
        let control_plane = BotControlPlaneCore::new(bots, providers.clone(), projection.clone())
            .with_bot_provider_repo(metadata.clone());
        let admin = ProviderCore::new(providers.clone(), providers.clone(), projection.clone(), registry.clone())
            .with_bot_provider_repo(metadata.clone());
        let provider = admin.register_provider("Provider".into(), Some("https://example.org/hook".into()), ProviderAuthMode::StaticBearer, "alice".into(), None, None).await.unwrap();
        let params = |reference: &str, mode| RegisterProviderBotParams {
            bot_name: "Test Bot".into(), provider_bot_ref: reference.into(), owners: vec!["alice".into()],
            connection_mode: mode, ..Default::default()
        };
        let (plugin, token) = admin.register_provider_bot_with_bot_uuid(&provider.provider.provider_id, &provider.provider_admin_token,
            params("plugin", ProviderBotConnectionMode::Plugin)).await.unwrap();
        assert!(token.is_none());
        assert_eq!(metadata.get_provider_bot(&plugin.bot_uuid).await.unwrap().unwrap().connection_mode, BotConnectionMode::Plugin);
        assert!(providers.get_binding_by_bot_uuid(&plugin.bot_uuid).await.unwrap().is_none());
        assert!(matches!(registry.resolve_delivery_target(&plugin.bot_uuid).await.unwrap(), BotDeliveryTarget::WebSocket { .. }));
        let view = control_plane.get(&plugin.bot_uuid, &bcs_config::resolve_env_str()).await.unwrap().unwrap();
        assert_eq!(view.provider.unwrap().provider_id, provider.provider.provider_id);
        let management = ProviderManagement::new(Arc::new(admin.clone()), Arc::new(admin.clone()), registry.clone(),
            Arc::new(bcs_relation::RelationCore::memory()));
        assert!(admin.delete_registered_provider_bot(&provider.provider.provider_id, "invalid-admin-token", "plugin").await.is_err());
        let deleted = management.delete_provider_bot(DeleteProviderBotCommand {
            provider_id: provider.provider.provider_id.clone(), provider_admin_token: provider.provider_admin_token.clone(),
            provider_bot_ref: "plugin".into(), allow_unbound_owner_suffixed_bot: false,
        }).await.unwrap();
        assert!(deleted.deleted);
        assert!(metadata.get_provider_bot(&plugin.bot_uuid).await.unwrap().unwrap().is_deleted);

        let (gateway, _) = admin.register_provider_bot_with_bot_uuid(&provider.provider.provider_id, &provider.provider_admin_token,
            params("gateway", ProviderBotConnectionMode::Gateway)).await.unwrap();
        let before = registry.load_token(&gateway.bot_uuid).await.unwrap();
        let (replay, token) = admin.register_provider_bot_with_bot_uuid(&provider.provider.provider_id, &provider.provider_admin_token,
            params("gateway", ProviderBotConnectionMode::Gateway)).await.unwrap();
        assert_eq!(gateway.bot_uuid, replay.bot_uuid);
        assert!(token.is_none());
        assert_eq!(registry.load_token(&gateway.bot_uuid).await.as_deref(), Some(before.as_str()));
        admin.update_provider_bot_webhook(&provider.provider.provider_id, &provider.provider_admin_token, "gateway", Some("https://example.org/new".into())).await.unwrap();
        assert_eq!(metadata.get_provider_bot(&gateway.bot_uuid).await.unwrap().unwrap().webhook_url, providers.get_binding_by_bot_uuid(&gateway.bot_uuid).await.unwrap().unwrap().webhook_url);
        assert!(matches!(registry.resolve_delivery_target(&gateway.bot_uuid).await.unwrap(), BotDeliveryTarget::HttpProvider { .. }));
        admin.set_provider_bot_disabled(&provider.provider.provider_id, &gateway.bot_uuid, &provider.provider_admin_token, true).await.unwrap();
        assert!(metadata.get_provider_bot(&gateway.bot_uuid).await.unwrap().unwrap().is_deleted);
        assert!(providers.get_binding_by_bot_uuid(&gateway.bot_uuid).await.unwrap().unwrap().disabled);
        assert!(registry.try_get(&gateway.bot_uuid).await.unwrap().is_none());
    }
}
