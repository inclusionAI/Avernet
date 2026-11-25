use std::sync::Arc;

use bcs_app_group::GroupServiceImpl;
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_friend::FriendCore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    DeleteGroup, GetGroup, GroupPatch, GroupService, UpdateGroup,
};
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService, Participant, ParticipantRole,
};
use bcs_session::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{
    NoopCollaborationRuntimeService, NoopGroupManagementService, authority_hook_for,
};

fn human(id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: id.into(), username: id.into(), display_name: None, full_name: None,
        }),
        bot: None, app: None, access_key: None,
    }
}

fn bot(id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: id.into(), owner_id: "alice".into(), app_id: 1, agent_code: "test".into(),
        }),
        ..human("alice")
    }
}

struct Fixture {
    service: GroupServiceImpl,
    repo: Arc<MemorySessionRepo>,
    session_id: String,
}

async fn fixture() -> Fixture {
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
    for group_id in ["g1", "unrelated"] {
        let mut group = Group::new(group_id, "driver", vec![
            Participant::bot("driver", ParticipantRole::Driver),
        ]);
        group.originator = Some("human_manager".into());
        group.visibility = "private".into();
        groups.upsert(group).await.unwrap();
    }
    let repo = Arc::new(MemorySessionRepo::new());
    let session = repo.create("g1", NewSessionParams {
        participants: vec![
            Participant::human("human_carol", ParticipantRole::Observer),
            Participant::bot("owned", ParticipantRole::Consultant),
        ],
        group_version: Some(1), ..Default::default()
    }).await.unwrap();
    let service = GroupServiceImpl::new(
        groups, bots, Arc::new(FriendCore::memory()),
        Arc::new(SessionManagementWithRuntimeCleanup::new(
            Arc::new(SessionManagementServiceImpl::new(repo.clone(), group_repo)),
            Arc::new(NoopCollaborationRuntimeService),
        )),
        Arc::new(NoopGroupManagementService),
    )
    .with_bot_authority_hook(authority_hook_for(bot_repo));
    Fixture { service, repo, session_id: session.id }
}

#[tokio::test]
async fn human_session_member_can_read_group_detail_without_editing() {
    let fixture = fixture().await;
    fixture.service.get(GetGroup {
        caller: human("carol"), group_id: "g1".into(),
    }).await.expect("Human Session membership grants Group detail read");
    assert_read_only(&fixture.service, human("carol")).await;
}

#[tokio::test]
async fn owner_of_session_only_bot_can_read_group_detail_without_editing() {
    let fixture = fixture().await;
    fixture.service.get(GetGroup {
        caller: human("alice"), group_id: "g1".into(),
    }).await.expect("owned Bot Session membership grants Group detail read");
    assert_read_only(&fixture.service, human("alice")).await;
}

#[tokio::test]
async fn selected_bot_session_member_can_read_group_detail() {
    let fixture = fixture().await;
    fixture.service.get(GetGroup {
        caller: bot("owned"), group_id: "g1".into(),
    }).await.expect("selected Bot's Session membership grants Group detail read");
}

async fn assert_read_only(service: &GroupServiceImpl, caller: AuthenticatedCaller) {
    let error = service.update(UpdateGroup {
        caller: caller.clone(), group_id: "g1".into(),
        patch: GroupPatch { name: Some("not allowed".into()), ..Default::default() },
    }).await.expect_err("Session membership must not grant Group editing");
    assert!(matches!(error, ApplicationError::Forbidden(_)));
    let error = service.delete(DeleteGroup {
        caller, group_id: "g1".into(), acting_bot_id: None,
    }).await.expect_err("Session membership must not grant Group deletion");
    assert!(matches!(error, ApplicationError::Forbidden(_)));
}

#[tokio::test]
async fn unrelated_or_revoked_membership_does_not_grant_detail_read() {
    let fixture = fixture().await;
    for (caller, group_id) in [
        (human("alice"), "unrelated"), (human("bob"), "g1"),
        (human("relation-only"), "g1"), (bot("other"), "g1"),
    ] {
        let error = fixture.service.get(GetGroup { caller, group_id: group_id.into() })
            .await.expect_err("only a current same-Group Session relation grants detail read");
        assert!(matches!(error, ApplicationError::Forbidden(_)));
    }
    fixture.repo.remove_participant(&fixture.session_id, "owned").await.unwrap();
    fixture.repo.remove_participant(&fixture.session_id, "human_carol").await.unwrap();
    for caller in [human("alice"), human("carol"), bot("owned")] {
        let error = fixture.service.get(GetGroup { caller, group_id: "g1".into() })
            .await.expect_err("removed participation must revoke detail access immediately");
        assert!(matches!(error, ApplicationError::Forbidden(_)));
    }
}
