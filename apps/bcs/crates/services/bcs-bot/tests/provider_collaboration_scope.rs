//! Provider policy is evaluated against current participants, with no group fence.
use std::sync::Arc;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::{MemoryBotProviderStore, ProviderBindingProjection};
use bcs_service_api::bot_provider::DownlinkDetectionSource;
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, BotRepoPort, ProviderAuthMode,
    ProviderBotConnectionMode, ProviderBotCoreService, ProviderCollaborationScope,
    ProviderCoreService, RegisterProviderBotParams,
};

#[tokio::test]
async fn current_membership_controls_scope_without_retaining_group_history() {
    let dir = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let providers = Arc::new(MemoryProviderStore::new());
    let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
    let projection = Arc::new(ProviderBindingProjection::new(
        providers.clone(), metadata.clone(), DownlinkDetectionSource::BotConnectionMode,
    ));
    let registry = Arc::new(BotCore::with_provider_repos(
        bots.clone(), providers.clone(), providers.clone(), projection.clone(),
    ).with_bot_provider_repo(metadata.clone()));
    let admin = ProviderCore::new(providers.clone(), providers, projection, registry.clone())
        .with_bot_provider_repo(metadata);
    let provider = admin.register_provider_with_policy(
        "Local".into(), None, ProviderAuthMode::StaticBearer, "alice".into(),
        None, None, None, None, Some(ProviderCollaborationScope::SameOwner),
    ).await.unwrap();
    let (local, _) = admin.register_provider_bot_with_bot_uuid(
        &provider.provider.provider_id, &provider.provider_admin_token,
        RegisterProviderBotParams {
            bot_name: "Local Bot".into(), provider_bot_ref: "local:alice".into(),
            owners: vec!["alice".into()], connection_mode: ProviderBotConnectionMode::Plugin,
            ..Default::default()
        },
    ).await.unwrap();
    bots.register_with_owner_and_token(
        "cloud-alice".into(), BotCapabilities::default(), "alice", "fixture-alice-token",
    ).await.unwrap();
    bots.register_with_owner_and_token(
        "cloud-bob".into(), BotCapabilities::default(), "bob", "fixture-bob-token",
    ).await.unwrap();

    let local_members = vec![local.bot_uuid.clone(), "cloud-alice".into(), "human_alice".into()];
    assert_eq!(registry.collaboration_owner(&local_members).await.unwrap(), Some("alice".into()));
    for guest in ["human_bob", "cloud-bob"] {
        let mut members = local_members.clone();
        members.push(guest.into());
        assert!(registry.collaboration_owner(&members).await.is_err());
    }

    // Removing the last restricted Bot removes this Provider boundary. Standard
    // group membership/invitation authorization still applies to these actors.
    let unrestricted_members = vec!["cloud-alice".into(), "human_alice".into(), "human_bob".into(), "cloud-bob".into()];
    assert_eq!(registry.collaboration_owner(&unrestricted_members).await.unwrap(), None);
    // Re-adding the restricted Bot immediately reapplies the live policy.
    let mut rejoined = unrestricted_members;
    rejoined.push(local.bot_uuid);
    assert!(registry.collaboration_owner(&rejoined).await.is_err());
    assert!(admin.update_provider_with_policy(
        &provider.provider.provider_id, &provider.provider_admin_token, "alice",
        None, None, None, None, None, None, None, Some(ProviderCollaborationScope::Standard),
    ).await.is_err(), "Provider administration cannot widen the boundary");

    let standard = admin.register_provider(
        "Initially standard".into(), None, ProviderAuthMode::StaticBearer, "alice".into(), None, None,
    ).await.unwrap();
    let (existing, _) = admin.register_provider_bot_with_bot_uuid(
        &standard.provider.provider_id, &standard.provider_admin_token,
        RegisterProviderBotParams {
            bot_name: "Existing".into(), provider_bot_ref: "existing:alice".into(), owners: vec!["alice".into()],
            connection_mode: ProviderBotConnectionMode::Plugin, ..Default::default()
        },
    ).await.unwrap();
    let foreign = vec![existing.bot_uuid, "human_bob".into()];
    assert_eq!(registry.collaboration_owner(&foreign).await.unwrap(), None);
    admin.update_provider_with_policy(
        &standard.provider.provider_id, &standard.provider_admin_token, "alice",
        None, None, None, None, None, None, None, Some(ProviderCollaborationScope::SameOwner),
    ).await.unwrap();
    assert!(registry.collaboration_owner(&foreign).await.is_err(),
        "tightening Provider policy must affect already registered Bots immediately");

}
