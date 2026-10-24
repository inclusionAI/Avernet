use std::sync::Arc;
use bcs_bot::{BotCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::{MemoryBotProviderStore, ProviderBindingProjection};
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_friend::FriendCore;
use bcs_relation::RelationCore;
use bcs_service_api::core::{GroupMutationCommand, GroupMutationKind};
use bcs_service_api::types::{EventActor, EventActorType};
use bcs_group::application::invite::InviteServiceImpl;
use bcs_service_api::bot_provider::DownlinkDetectionSource;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRepoPort, CreateInviteTokenCommand, Group, GroupCoreService,
    InviteService, JoinByInviteCommand, Participant, ParticipantRole, ProviderAuthMode,
    ProviderBotConnectionMode, ProviderBotCoreService, ProviderCollaborationScope,
    ProviderCoreService, RegisterProviderBotParams, GroupMutableFieldsPatch, ServiceError,
    WorkbenchConnectCommand, WorkbenchSessionService, WorkbenchUseCaseError, ParticipantMode,
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
    let repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let metadata_port: Arc<dyn bcs_service_api::port::repo::bot_provider::BotProviderRepoPort> = metadata.clone();
    providers.bind_group_scope_source(repo.clone(), Arc::downgrade(&metadata_port));
    let projection = Arc::new(ProviderBindingProjection::new(
        providers.clone(), metadata.clone(), DownlinkDetectionSource::BotConnectionMode,
    ));
    let registry = Arc::new(BotCore::with_provider_repos(
        bots.clone(), providers.clone(), providers.clone(), projection.clone(),
    ).with_bot_provider_repo(metadata.clone()));
    let policy_gate = providers.policy_gate();
    let admin = ProviderCore::new(providers.clone(), providers, projection, registry.clone())
        .with_bot_provider_repo(metadata);
    let provider = admin.register_provider_with_policy(
        "Local".into(), None, ProviderAuthMode::StaticBearer, "alice".into(),
        None, None, None, None, Some(ProviderCollaborationScope::SameOwner), None,
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
    let groups = Arc::new(GroupCore::with_repo(repo.clone()).with_collaboration_registry(registry.clone()).with_policy_gate(policy_gate));
    let mut group = Group::new("scope-group", "cloud", vec![
        Participant::bot("cloud", ParticipantRole::Driver),
        Participant::bot(&local.bot_uuid, ParticipantRole::Consultant),
    ]);
    group.visibility = "private".into();
    let mut invalid = group.clone();
    invalid.id = "foreign-upsert".into();
    invalid.participants.push(Participant::human("human_bob", ParticipantRole::Observer));
    assert!(matches!(groups.upsert(invalid).await, Err(ServiceError::Forbidden(_))));
    assert!(groups.get("foreign-upsert").await.is_none());
    invalid = group.clone(); invalid.id = "public-upsert".into(); invalid.visibility = "public".into();
    assert!(matches!(groups.upsert(invalid).await, Err(ServiceError::Forbidden(_))));
    assert!(groups.get("public-upsert").await.is_none());
    groups.upsert(group).await.unwrap();
    assert!(matches!(groups.update_visibility("scope-group", "public").await, Err(ServiceError::Forbidden(_))));
    assert!(matches!(groups.patch_mutable_fields("scope-group", GroupMutableFieldsPatch {
        visibility: Some("public".into()), ..Default::default()
    }).await, Err(ServiceError::Forbidden(_))));
    for guarded in [false, true] {
        let outsider = Participant::human("human_bob", ParticipantRole::Observer);
        let error = if guarded { groups.add_participant_with_visibility_guard("scope-group", outsider, true).await }
            else { groups.add_participant("scope-group", outsider).await };
        assert!(matches!(error, Err(ServiceError::Forbidden(_))));
        assert!(groups.get("scope-group").await.unwrap().get_participant("human_bob").is_none());
    }
    let mut public = Group::new("legacy-public", "cloud", vec![Participant::bot("cloud", ParticipantRole::Driver)]);
    public.visibility = "public".into(); groups.upsert(public).await.unwrap();
    assert!(matches!(groups.add_participant("legacy-public", Participant::bot(&local.bot_uuid, ParticipantRole::Consultant)).await,
        Err(ServiceError::Forbidden(_))));
    assert!(groups.get("legacy-public").await.unwrap().get_participant(&local.bot_uuid).is_none());
    // The same legacy entry accepts an existing same-owner member in a private group.
    groups.add_participant("scope-group", Participant::bot("cloud", ParticipantRole::Driver)).await.unwrap();
    let sessions: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let session_management = Arc::new(SessionManagementServiceImpl::new(sessions.clone(), repo).with_collaboration_registry(registry.clone()));
    let workbench = GroupManagement::new(
        groups.clone(), registry.clone(), Arc::new(FriendCore::memory()), Arc::new(RelationCore::memory()),
        GroupConfig::default(), session_management.clone(), Arc::new(NoopSystemMessageService),
    );
    let invites = InviteServiceImpl {
        registry, group: groups.clone(),
        session: session_management.clone(),
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
    let mutation = |mutation| GroupMutationCommand {
        group_id: "scope-group".into(),
        actor: EventActor { actor_type: EventActorType::Human, id: "human_alice".into(), display_name: None },
        correlation_id: None, trace_id: None, mutation,
    };
    assert!(matches!(groups.mutate(mutation(GroupMutationKind::PatchMutableFields(
        GroupMutableFieldsPatch { visibility: Some("public".into()), ..Default::default() },
    ))).await, Err(ServiceError::Forbidden(_))));
    assert_eq!(groups.get("scope-group").await.unwrap().visibility, "private");
    assert!(matches!(groups.mutate(mutation(GroupMutationKind::AddParticipant {
        participant: Participant::human("human_bob", ParticipantRole::Observer), actor_is_public: true,
    })).await, Err(ServiceError::Forbidden(_))));

    let session = sessions.create("scope-group", NewSessionParams {
        id: Some("scope-group:abcdef12".into()),
        participants: groups.get("scope-group").await.unwrap().participants,
        ..Default::default()
    }).await.unwrap();
    use bcs_service_api::{SessionManagementService, SessionUseCaseError, CreateOrReactivateCommand};
    let outsider = Participant::human("human_bob", ParticipantRole::Observer);
    assert!(matches!(session_management.add_participant(&session.id, outsider).await,
        Err(SessionUseCaseError::Internal(ServiceError::Forbidden(_)))));
    assert!(!sessions.get(&session.id).await.unwrap().participants.iter().any(|p| p.bot_uuid == "human_bob"));
    assert!(session_management.authorize_collaboration(&session.id, "human_bob").await.is_err());
    assert!(session_management.authorize_collaboration(&session.id, "human_alice").await.is_ok());
    let status_before = sessions.get(&session.id).await.unwrap().status;
    assert!(session_management.create_or_reactivate(CreateOrReactivateCommand {
        group_id: "scope-group".into(), session_id: Some(session.id.clone()),
        params: NewSessionParams { caller_id: Some("human_bob".into()), ..Default::default() },
    }).await.is_err());
    assert_eq!(sessions.get(&session.id).await.unwrap().status, status_before);
    assert!(session_management.create_or_reactivate(CreateOrReactivateCommand {
        group_id: "unrelated-group".into(), session_id: Some(session.id.clone()), params: Default::default(),
    }).await.is_err());

    let connect = |owner: &str| WorkbenchConnectCommand {
        bound_actor_id: Some(format!("human_{owner}")), group_id: "scope-group".into(),
        session_id: Some(session.id.clone()), view_actor_id: None,
    };
    assert!(workbench.connect(connect("alice")).await.is_ok());
    // A stale/direct Session membership grant cannot bypass the Provider boundary.
    let mut bob = Participant::human("human_bob", ParticipantRole::Observer);
    bob.mode = Some(ParticipantMode::Present);
    sessions.add_participant(&session.id, bob).await.unwrap();
    let error = workbench.connect(connect("bob")).await.unwrap_err();
    assert!(matches!(error, WorkbenchUseCaseError::ForbiddenCollaborationScope));
    assert_eq!(error.code(), "forbidden_collaboration_scope");
    assert!(matches!(workbench.connect(connect("alice")).await,
        Err(WorkbenchUseCaseError::ForbiddenCollaborationScope)), "a mixed-owner Session must not be exposed even to the owner");

    groups.mutate(mutation(GroupMutationKind::RemoveParticipant {
        actor_id: local.bot_uuid.clone(), reason: "owner removed local Bot".into(),
    })).await.unwrap();
    assert!(session_management.authorize_collaboration(&session.id, "human_bob").await.is_ok());
    assert!(workbench.connect(connect("bob")).await.is_ok(),
        "the historical Session snapshot must not retain the removed group restriction");
    assert!(invites.join_group_by_invite(join("bob")).await.unwrap().joined,
        "a valid invitation should resume existing behavior after the restricted Bot leaves");
    assert!(matches!(groups.mutate(mutation(GroupMutationKind::AddParticipant {
        participant: Participant::bot(&local.bot_uuid, ParticipantRole::Consultant), actor_is_public: true,
    })).await, Err(ServiceError::Forbidden(_))), "rejoining must reject the now mixed-owner group");
}
