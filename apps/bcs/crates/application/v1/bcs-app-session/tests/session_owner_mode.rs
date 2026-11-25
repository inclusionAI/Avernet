use std::sync::Arc;

use bcs_app_session::SessionServiceImpl;
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_friend::FriendCore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity, SessionService,
    UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
use bcs_service_api::types::MessageViewScope;
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, Participant,
    ParticipantMode, ParticipantRole,
};
use bcs_session::{SessionLaunchApplication, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{
    NoopCollaborationRuntimeService, NoopGroupMessageHistoryService, NoopSystemMessageService,
    authority_hook_for,
};

fn human(id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        user: Some(AuthenticatedUserIdentity {
            id: id.into(), username: id.into(), display_name: None, full_name: None,
        }),
        tenant: Some("tenant-a".into()), bot: None, app: None, access_key: None,
    }
}

async fn fixture() -> (SessionServiceImpl, Arc<MemorySessionRepo>, String) {
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
    // Ownership arrives as the historical `created_by` fact only, so the
    // legacy compat window is armed exactly as production defaults —
    // the authority hook resolves it into the current control edge.
    let bot_repo = Arc::new(MemoryBotRepo::new());
    bot_repo.set_legacy_created_by_fallback(true);
    let bots = Arc::new(BotCore::with_repo(bot_repo.clone()));
    for (id, owner) in [("driver", "manager"), ("owned", "alice"), ("other", "bob")] {
        bots.register(id.into(), BotCapabilities::default()).await.unwrap();
        bots.save_created_by(id, owner, true).await.unwrap();
    }
    let mut group = Group::new("g1", "driver", vec![
        Participant::bot("driver", ParticipantRole::Driver),
    ]);
    group.originator = Some("human_manager".into());
    groups.upsert(group).await.unwrap();
    let repo = Arc::new(MemorySessionRepo::new());
    let session = repo.create("g1", NewSessionParams {
        participants: vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("owned", ParticipantRole::Consultant),
            Participant::bot("other", ParticipantRole::Consultant),
        ],
        group_version: Some(1),
        created_by: Some("human_manager".into()),
        ..Default::default()
    }).await.unwrap();
    let sessions = Arc::new(SessionManagementServiceImpl::new(repo.clone(), group_repo));
    let runtime = Arc::new(NoopCollaborationRuntimeService);
    let system = Arc::new(NoopSystemMessageService);
    let launch = Arc::new(SessionLaunchApplication::new(
        bots.clone(), groups.clone(), sessions.clone(), runtime.clone(), system.clone(),
    ));
    let service = SessionServiceImpl::new(
        launch, sessions, groups, bots, Arc::new(FriendCore::memory()),
        repo.clone(), Arc::new(NoopGroupMessageHistoryService), runtime, system,
    )
    .with_bot_authority_hook(authority_hook_for(bot_repo));
    (service, repo, session.id)
}

#[tokio::test]
async fn owner_can_change_only_its_existing_bot_mode() {
    let (service, repo, session_id) = fixture().await;
    for mode in [ParticipantMode::Muted, ParticipantMode::Auto] {
        let result = service.update_participant(UpdateSessionParticipant {
            caller: human("alice"), session_id: session_id.clone(), bot_uuid: "owned".into(),
            mode: Some(mode), message_view_scope: None,
        }).await.expect("owner can change its Session Bot mode without management rights");
        assert_eq!(result.mode, mode);
        assert_eq!(repo.get(&session_id).await.unwrap().participants.iter()
            .find(|p| p.bot_uuid == "owned").unwrap().mode, Some(mode));
    }
}

#[tokio::test]
async fn ownership_does_not_grant_other_mutations() {
    let (service, repo, session_id) = fixture().await;
    for (target, scope) in [("other", None), ("owned", Some(MessageViewScope::Full))] {
        let error = service.update_participant(UpdateSessionParticipant {
            caller: human("alice"), session_id: session_id.clone(), bot_uuid: target.into(),
            mode: Some(ParticipantMode::Muted), message_view_scope: scope,
        }).await.expect_err("ownership grants only the target mode permission");
        assert_eq!(error.code(), if scope.is_some() {
            "message_view_scope_forbidden"
        } else {
            "forbidden"
        });
    }
    let error = service.update(UpdateSession {
        caller: human("alice"), session_id: session_id.clone(), title: Some("not allowed".into()),
    }).await.expect_err("owner is not a Session manager");
    assert!(matches!(error, ApplicationError::Forbidden(_)));
    let stored = repo.get(&session_id).await.unwrap();
    assert!(stored.participants.iter().all(|p| p.mode != Some(ParticipantMode::Muted)));
}

#[tokio::test]
async fn absent_owned_bot_is_not_added_by_mode_update() {
    let (service, repo, session_id) = fixture().await;
    repo.remove_participant(&session_id, "owned").await.unwrap();
    let error = service.update_participant(UpdateSessionParticipant {
        caller: human("alice"), session_id: session_id.clone(), bot_uuid: "owned".into(),
        mode: Some(ParticipantMode::Muted), message_view_scope: None,
    }).await.expect_err("mode update must not add an absent Bot or disclose membership");
    assert!(matches!(error, ApplicationError::Forbidden(_)));
    assert!(!repo.get(&session_id).await.unwrap().participants.iter().any(|p| p.bot_uuid == "owned"));
}
