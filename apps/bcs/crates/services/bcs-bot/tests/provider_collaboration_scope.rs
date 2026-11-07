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
    let metadata_port: Arc<dyn bcs_service_api::port::repo::bot_provider::BotProviderRepoPort> = metadata.clone();
    let groups = Arc::new(bcs_group_store::MemoryGroupRepo::new());
    providers.bind_group_scope_source(groups.clone(), Arc::downgrade(&metadata_port));
    let projection = Arc::new(ProviderBindingProjection::new(
        providers.clone(), metadata.clone(), DownlinkDetectionSource::BotConnectionMode,
    ));
    let registry = Arc::new(BotCore::with_provider_repos(
        bots.clone(), providers.clone(), providers.clone(), projection.clone(),
    ).with_bot_provider_repo(metadata.clone()));
    let admin = ProviderCore::new(providers.clone(), providers.clone(), projection, registry.clone())
        .with_bot_provider_repo(metadata);
    let provider = admin.register_provider_with_policy(
        "Local".into(), None, ProviderAuthMode::StaticBearer, "alice".into(),
        None, None, None, None, Some(ProviderCollaborationScope::SameOwner), None,
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
        None, None, None, None, None, None, None, Some(ProviderCollaborationScope::Standard), None,
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
    use bcs_service_api::{Group, Participant, ParticipantRole, ProviderRepoPort, ServiceError};
    use bcs_service_api::port::repo::GroupRepoPort;
    let mut public = Group::new("public", &existing.bot_uuid, vec![Participant::bot(&existing.bot_uuid, ParticipantRole::Driver)]);
    public.visibility = "public".into(); groups.upsert(public).await.unwrap();
    assert!(matches!(admin.update_provider_with_policy(
        &standard.provider.provider_id, &standard.provider_admin_token, "alice",
        Some("Rejected".into()), None, None, None, None, None, None, Some(ProviderCollaborationScope::SameOwner), None,
    ).await, Err(ServiceError::Conflict(_))));
    assert_eq!(providers.get_provider(&standard.provider.provider_id).await.unwrap().unwrap().name, "Initially standard");
    groups.update_visibility("public", "private").await.unwrap();
    let foreign = vec![existing.bot_uuid, "human_bob".into()];
    assert_eq!(registry.collaboration_owner(&foreign).await.unwrap(), None);
    admin.update_provider_with_policy(
        &standard.provider.provider_id, &standard.provider_admin_token, "alice",
        None, None, None, None, None, None, None, Some(ProviderCollaborationScope::SameOwner), None,
    ).await.unwrap();
    let gate = providers.policy_gate();
    let lease = gate.read().await;
    let writer = providers.clone(); let id = standard.provider.provider_id.clone();
    let task = tokio::spawn(async move { writer.update_provider_metadata(&id, Some("Renamed"), None, None, 3).await });
    tokio::task::yield_now().await;
    assert!(!task.is_finished(), "policy writes must wait for in-flight group persistence");
    drop(lease); assert!(task.await.unwrap().is_ok());
    assert!(registry.collaboration_owner(&foreign).await.is_err(),
        "tightening Provider policy must affect already registered Bots immediately");

    // Reject an existing Bot before the legacy registration path merges its capabilities.
    bots.register_with_owner_and_token("imported".into(), BotCapabilities {
        name: Some("Original name".into()), ..Default::default()
    }, "alice", "fixture-imported-token").await.unwrap();
    let mut public = Group::new("import-public", "imported", vec![Participant::bot("imported", ParticipantRole::Driver)]);
    public.visibility = "public".into(); groups.upsert(public).await.unwrap();
    let params = || RegisterProviderBotParams {
        bot_uuid: Some("imported".into()), bot_name: "Changed name".into(), provider_bot_ref: "imported:alice".into(),
        owners: vec!["alice".into()], connection_mode: ProviderBotConnectionMode::Plugin, ..Default::default()
    };
    for mode in [ProviderBotConnectionMode::Plugin, ProviderBotConnectionMode::Gateway] {
        let mut request = params(); request.connection_mode = mode;
        if mode == ProviderBotConnectionMode::Gateway { request.webhook_url = Some("https://example.org/bot".into()); }
        assert!(matches!(admin.register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id, &provider.provider_admin_token, request,
        ).await, Err(ServiceError::Conflict(_))));
    }
    assert!(metadata_port.get_provider_bot("imported").await.unwrap().is_none());
    assert_eq!(bots.get("imported").await.unwrap().capabilities.name.as_deref(), Some("Original name"));
    assert_eq!(bots.load_token("imported").await.as_deref(), Some("fixture-imported-token"));
    groups.update_visibility("import-public", "private").await.unwrap();
    admin.register_provider_bot_with_bot_uuid(&provider.provider.provider_id, &provider.provider_admin_token, params()).await.unwrap();
    assert_eq!(metadata_port.get_provider_bot("imported").await.unwrap().unwrap().provider_id, provider.provider.provider_id);
    assert_eq!(bots.load_token("imported").await.as_deref(), Some("fixture-imported-token"));

}
