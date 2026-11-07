//! History authorization must use the authenticated caller, not a requested view.
use super::*;
use bcs_bot::ProviderCore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::{MemoryBotProviderStore, ProviderBindingProjection};
use bcs_service_api::bot_provider::DownlinkDetectionSource;
use bcs_service_api::{BotCapabilities, BotRepoPort, ProviderAuthMode,
    ProviderBotConnectionMode, ProviderBotCoreService, ProviderCollaborationScope,
    ProviderCoreService, RegisterProviderBotParams};

#[tokio::test]
async fn same_owner_history_cannot_be_read_by_public_or_foreign_viewers() {
    let dir = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let providers = Arc::new(MemoryProviderStore::new());
    let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
    let (mut service, messages, sessions, fallback, session_id) = service_fixture(GroupStrategy::Chat, 0, 0, vec![]).await;
    let metadata_port: Arc<dyn bcs_service_api::port::repo::bot_provider::BotProviderRepoPort> = metadata.clone();
    let group_repo = Arc::new(bcs_group::MemoryGroupRepo::new());
    providers.bind_group_scope_source(group_repo.clone(), Arc::downgrade(&metadata_port));
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
        None, None, None, None, Some(ProviderCollaborationScope::SameOwner), None,
    ).await.unwrap();
    let (local, _) = admin.register_provider_bot_with_bot_uuid(
        &provider.provider.provider_id, &provider.provider_admin_token,
        RegisterProviderBotParams { bot_name: "Local".into(), provider_bot_ref: "local:alice".into(),
            owners: vec!["alice".into()], connection_mode: ProviderBotConnectionMode::Plugin,
            ..Default::default() },
    ).await.unwrap();
    bots.register_with_owner_and_token("cloud-alice".into(), BotCapabilities::default(), "alice", "fixture-cloud-token").await.unwrap();
    service.registry = registry;
    service.group = Arc::new(GroupCore::with_repo(group_repo));
    let members = vec![Participant::bot("cloud-alice", ParticipantRole::Driver),
        Participant::bot(&local.bot_uuid, ParticipantRole::Consultant)];
    service.group.upsert(Group::new("group-1", "cloud-alice", members.clone())).await.unwrap();
    for participant in members { sessions.add_participant(&session_id, participant).await.unwrap(); }
    append_history(&messages, "group-1", "", "cloud-alice", "group secret", None).await;
    append_history(&messages, "group-1", &session_id, "cloud-alice", "session secret", None).await;
    let human = |owner: &str| CallerContext::Human(HumanActor { actor_id: format!("human_{owner}"), staff_no: owner.into() });
    let group_read = |caller| GroupHistoryCommand { caller, ..group_cmd("group-1", Some("cloud-alice")) };
    let session_read = |caller| SessionHistoryCommand { caller, ..session_cmd("group-1", &session_id, Some("cloud-alice")) };

    for caller in [CallerContext::Public, human("bob")] {
        assert!(service.get_history(group_read(caller.clone())).await.is_err(),
            "requesting the owner's Bot view must not grant history access");
        assert!(service.get_session_history(session_read(caller)).await.is_err());
    }
    assert_eq!(fallback.group_calls().await, 0);
    assert_eq!(fallback.session_calls().await, 0);
    for caller in [human("alice"), CallerContext::Bot(BotActor { bot_uuid: "cloud-alice".into() })] {
        assert!(service.get_history(group_read(caller.clone())).await.unwrap().messages.iter().any(|m| m.content == "group secret"));
        assert!(service.get_session_history(session_read(caller)).await.unwrap().messages.iter().any(|m| m.content == "session secret"));
    }
    sessions.add_participant(&session_id, Participant::human("human_bob", ParticipantRole::Observer)).await.unwrap();
    assert!(service.get_session_history(session_read(human("alice"))).await.is_err(),
        "a historical grant must not expose a mixed-owner Session while the group is restricted");
    service.group.remove_participant("group-1", &local.bot_uuid).await.unwrap();
    assert!(service.get_session_history(session_read(human("bob"))).await.is_ok(),
        "a historical Session snapshot does not retain the removed group restriction");
}
