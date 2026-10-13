use std::sync::Arc;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::{MemoryBotProviderStore, ProviderBindingProjection};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_group::application::invite::InviteServiceImpl;
use bcs_service_api::bot_provider::DownlinkDetectionSource;
use bcs_service_api::port::repo::{GroupRepoPort, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRepoPort, CreateInviteTokenCommand, Group, GroupCoreService,
    InviteService, JoinByInviteCommand, Participant, ParticipantRole, ProviderAuthMode,
    ProviderBotConnectionMode, ProviderBotCoreService, ProviderCollaborationScope,
    ProviderCoreService, RegisterProviderBotParams,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

#[tokio::test]
async fn human_invitation_is_restricted_only_while_the_local_bot_participates() {
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
            bot_name: "Local".into(), provider_bot_ref: "local:alice".into(),
            owners: vec!["alice".into()], connection_mode: ProviderBotConnectionMode::Plugin,
            ..Default::default()
        },
    ).await.unwrap();
    bots.register_with_owner_and_token("cloud".into(), BotCapabilities::default(), "alice", "fixture-cloud-token").await.unwrap();
    let repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let groups = Arc::new(GroupCore::with_repo(repo.clone()).with_collaboration_registry(registry.clone()));
    let mut group = Group::new("scope-group", "cloud", vec![
        Participant::bot("cloud", ParticipantRole::Driver),
        Participant::bot(&local.bot_uuid, ParticipantRole::Consultant),
    ]);
    group.visibility = "private".into();
    groups.upsert(group).await.unwrap();
    let sessions: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let invites = InviteServiceImpl {
        registry, group: groups.clone(),
        session: Arc::new(SessionManagementServiceImpl::new(sessions, repo)),
        system_message: Arc::new(NoopSystemMessageService),
        token_secret: b"fixture-scope-invite-secret".to_vec(), default_ttl_seconds: 3600,
        base_url: None, group_link_url: None, session_link_url: None,
    };
    let token = invites.create_group_invite_token(CreateInviteTokenCommand {
        caller_actor_id: Some("cloud".into()), caller_staff_no: None,
        target_id: "scope-group".into(), ttl_seconds: None,
    }).await.unwrap().invite_token;
    let join = |owner: &str| JoinByInviteCommand {
        token: token.clone(), staff_no: owner.into(), nick_name: None, message_view_scope: None,
    };
    assert!(invites.join_group_by_invite(join("bob")).await.is_err());
    assert!(groups.get("scope-group").await.unwrap().get_participant("human_bob").is_none());
    assert!(invites.join_group_by_invite(join("alice")).await.unwrap().joined);
    groups.remove_participant("scope-group", &local.bot_uuid).await.unwrap();
    assert!(invites.join_group_by_invite(join("bob")).await.unwrap().joined,
        "a valid invitation should resume existing behavior after the restricted Bot leaves");
}
