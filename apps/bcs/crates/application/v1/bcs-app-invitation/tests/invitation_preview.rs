//! Read-only invitation preflight against real in-memory authorization stores.
mod support;

use std::sync::Arc;
use async_trait::async_trait;
use bcs_app_invitation::{InvitationFriendshipServiceConfig, InvitationFriendshipServiceImpl};
use bcs_bot::BotCore;
use bcs_domain::{invite_token_encode, InviteTargetType, InviteTokenPayload};
use bcs_friend::FriendCore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_group::application::invite::InviteServiceImpl;
use bcs_service_api::application::session::{CreateOrReactivateCommand, SessionManagementService};
use bcs_service_api::application::v1::{
    AuthenticatedCaller, AuthenticatedUserIdentity, InvitationPreview, InvitationService,
    InvitationTargetType, PreviewInvitation,
};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::repo::NewSessionParams;
use bcs_service_api::{
    BotRegistryCoreService, Group, GroupCoreService, Participant, ParticipantRole,
    ServiceError, ServiceResult, SessionKind,
};
use bcs_service_api::types::SessionAuthorizationSnapshot;
use bcs_session::{SessionAuthorizationCoreImpl, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

const SECRET: &[u8] = b"preview-test-secret";

struct Fixture {
    service: InvitationFriendshipServiceImpl,
    groups: Arc<GroupCore>,
    group_repo: Arc<MemoryGroupRepo>,
    sessions: Arc<SessionManagementServiceImpl>,
    bots: Arc<BotCore>,
    invite: Arc<dyn bcs_service_api::InviteService>,
}

fn caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        user: Some(AuthenticatedUserIdentity {
            id: "viewer".into(), username: "viewer".into(), display_name: None, full_name: None,
        }),
        tenant: None, bot: None, app: None, access_key: None,
    }
}

fn token(target_type: Option<InviteTargetType>, id: &str, exp: u64) -> String {
    invite_token_encode(&InviteTokenPayload { v: 1, id: id.into(), exp, target_type }, SECRET)
}

impl Fixture {
    async fn new(member: bool) -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let session_repo = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(session_repo.clone(), group_repo.clone()));
        let bots = Arc::new(BotCore::memory());
        let friends = Arc::new(FriendCore::memory());
        let invite = Arc::new(InviteServiceImpl {
            registry: bots.clone(), group: groups.clone(), session: sessions.clone(),
            system_message: Arc::new(NoopSystemMessageService), token_secret: SECRET.to_vec(),
            default_ttl_seconds: 3600, base_url: None, group_link_url: None, session_link_url: None,
            bot_authority: None,
        });
        let service = InvitationFriendshipServiceImpl::new(
            friends, Arc::new(support::FakeFriendService::new()), groups.clone(), sessions.clone(),
            bots.clone(), invite.clone(), SECRET.to_vec(),
            InvitationFriendshipServiceConfig { default_ttl_seconds: 3600 },
        ).with_session_authorization_core(Arc::new(SessionAuthorizationCoreImpl::new(session_repo)));
        let mut group = Group::new("group", "bot", vec![Participant::bot("bot", ParticipantRole::Driver)]);
        // Being the originator alone is NOT direct membership.
        group.originator = Some("human_viewer".into());
        if member { group.participants.push(Participant::human("human_viewer", ParticipantRole::Consultant)); }
        groups.upsert(group).await.unwrap();
        Self { service, groups, group_repo, sessions, bots, invite }
    }

    async fn session(&self, member: bool) -> String {
        let mut participants = vec![Participant::bot("bot", ParticipantRole::Driver)];
        if member { participants.push(Participant::human("human_viewer", ParticipantRole::Consultant)); }
        self.sessions.create_or_reactivate(CreateOrReactivateCommand {
            group_id: "group".into(), session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat, participants, group_version: None,
                caller_id: Some("bot".into()), caller_principal: Some("bot".into()), input: None,
                created_by: Some("bot".into()), session_title: None, id: None, meta: None,
                message_visibility_version: 1,
            },
        }).await.unwrap().session.id
    }

    async fn preview(&self, kind: InviteTargetType, id: &str) -> InvitationPreview {
        self.service.preview_invitation(PreviewInvitation {
            caller: caller(), token: token(Some(kind), id, u64::MAX),
        }).await.unwrap()
    }
}

#[tokio::test]
async fn group_preview_checks_exact_human_membership_without_mutation() {
    for member in [false, true] {
        let fx = Fixture::new(member).await;
        let before = fx.groups.read_authorization_snapshot("group").await.unwrap();
        let preview = fx.preview(InviteTargetType::Group, "group").await;
        assert_eq!(preview.target_type, InvitationTargetType::Group);
        assert_eq!(preview.target_id, "group");
        assert_eq!(preview.group_id, "group");
        assert_eq!(preview.already_joined, member);
        assert_eq!(before, fx.groups.read_authorization_snapshot("group").await.unwrap());
        assert!(fx.bots.get("human_viewer").await.is_none(), "preview must not ensure/create Human");
    }
}

#[tokio::test]
async fn session_preview_does_not_confuse_parent_membership_with_session_membership() {
    for group_member in [false, true] {
        for member in [false, true] {
            let fx = Fixture::new(group_member).await;
            let id = fx.session(member).await;
            let before = fx.sessions.get(&id).await.unwrap().unwrap();
            let preview = fx.preview(InviteTargetType::Session, &id).await;
            assert_eq!(preview.target_type, InvitationTargetType::Session);
            assert_eq!(preview.target_id, id);
            assert_eq!(preview.group_id, "group");
            assert_eq!(preview.already_joined, member);
            let after = fx.sessions.get(&id).await.unwrap().unwrap();
            let facts = |ps: &[Participant]| ps.iter().map(|p|
                (p.bot_uuid.clone(), p.actor_kind, p.mode, p.message_view_scope)
            ).collect::<Vec<_>>();
            assert_eq!(facts(&before.participants), facts(&after.participants));
            assert!(fx.bots.get("human_viewer").await.is_none());
        }
    }
}

#[tokio::test]
async fn group_preview_reads_fresh_membership_not_the_display_cache() {
    use bcs_service_api::port::repo::GroupRepoPort;
    let fx = Fixture::new(true).await;
    let mut group = fx.groups.get("group").await.unwrap(); // warm display cache
    group.participants.retain(|p| p.bot_uuid != "human_viewer");
    fx.group_repo.upsert(group).await.unwrap(); // another instance removes viewer
    assert!(!fx.preview(InviteTargetType::Group, "group").await.already_joined);
}

#[tokio::test]
async fn session_preview_tracks_removal_and_never_falls_back_to_group_membership() {
    let fx = Fixture::new(true).await;
    let id = fx.session(true).await;
    assert!(fx.preview(InviteTargetType::Session, &id).await.already_joined);
    fx.sessions.remove_participant(&id, "human_viewer").await.unwrap();
    assert!(!fx.preview(InviteTargetType::Session, &id).await.already_joined);
}

#[tokio::test]
async fn preview_rejects_expired_invalid_and_legacy_tokens_even_for_members() {
    let fx = Fixture::new(true).await;
    for (value, expected) in [
        ("invalid".into(), "invalid_request"),
        (token(Some(InviteTargetType::Group), "group", 1), "invitation_expired"),
        (token(None, "group", u64::MAX), "invalid_request"),
    ] {
        let error = fx.service.preview_invitation(PreviewInvitation { caller: caller(), token: value }).await.unwrap_err();
        assert_eq!(error.code(), expected);
    }
}

#[tokio::test]
async fn preview_requires_authenticated_user() {
    let fx = Fixture::new(false).await;
    let error = fx.service.preview_invitation(PreviewInvitation {
        caller: AuthenticatedCaller { user: None, ..caller() }, token: token(Some(InviteTargetType::Group), "group", u64::MAX),
    }).await.unwrap_err();
    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn preview_reports_missing_group_session_and_session_parent() {
    let fx = Fixture::new(false).await;
    for kind in [InviteTargetType::Group, InviteTargetType::Session] {
        let error = fx.service.preview_invitation(PreviewInvitation {
            caller: caller(), token: token(Some(kind), "missing", u64::MAX),
        }).await.unwrap_err();
        assert_eq!(error.code(), "invitation_not_found");
    }
    let id = fx.session(false).await;
    fx.groups.delete("group").await.unwrap();
    let error = fx.service.preview_invitation(PreviewInvitation {
        caller: caller(), token: token(Some(InviteTargetType::Session), &id, u64::MAX),
    }).await.unwrap_err();
    assert_eq!(error.code(), "invitation_not_found");
}

struct FailingSnapshots;
#[async_trait]
impl SessionAuthorizationCoreService for FailingSnapshots {
    async fn read_authorization_snapshot(&self, _: &str) -> ServiceResult<Option<SessionAuthorizationSnapshot>> {
        Err(ServiceError::InternalError("snapshot unavailable".into()))
    }
}

#[tokio::test]
async fn preview_propagates_snapshot_failure_instead_of_returning_nonmember() {
    let fx = Fixture::new(true).await;
    let service = fx.service.with_session_authorization_core(Arc::new(FailingSnapshots));
    let error = service.preview_invitation(PreviewInvitation {
        caller: caller(), token: token(Some(InviteTargetType::Session), "session", u64::MAX),
    }).await.unwrap_err();
    assert_eq!(error.code(), "internal_error");
}

#[tokio::test]
async fn preview_uses_user_human_not_the_authenticated_bot_or_bot_shaped_human_id() {
    use bcs_service_api::application::v1::AuthenticatedBotIdentity;
    let fx = Fixture::new(false).await;
    let bot = AuthenticatedBotIdentity {
        bot_uuid: "bot".into(), owner_id: "viewer".into(), app_id: 1, agent_code: "bot".into(),
    };
    let query = PreviewInvitation {
        caller: AuthenticatedCaller { bot: Some(bot.clone()), ..caller() },
        token: token(Some(InviteTargetType::Group), "group", u64::MAX),
    };
    assert!(!fx.service.preview_invitation(query.clone()).await.unwrap().already_joined);
    // Never equate a Bot participant with the Human just because IDs match.
    fx.groups.add_participant("group", Participant::bot("human_viewer", ParticipantRole::Consultant)).await.unwrap();
    assert!(!fx.service.preview_invitation(query.clone()).await.unwrap().already_joined);
    let error = fx.service.preview_invitation(PreviewInvitation {
        caller: AuthenticatedCaller { user: None, bot: Some(bot), ..caller() }, ..query
    }).await.unwrap_err();
    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn preview_reports_unconfigured_group_snapshots_as_internal_error() {
    let fx = Fixture::new(false).await;
    let service = InvitationFriendshipServiceImpl::new(
        Arc::new(FriendCore::memory()), Arc::new(support::FakeFriendService::new()),
        Arc::new(bcs_test_support::NoopGroupCoreService), fx.sessions, fx.bots, fx.invite,
        SECRET.to_vec(), InvitationFriendshipServiceConfig { default_ttl_seconds: 3600 },
    );
    let error = service.preview_invitation(PreviewInvitation {
        caller: caller(), token: token(Some(InviteTargetType::Group), "group", u64::MAX),
    }).await.unwrap_err();
    assert_eq!(error.code(), "internal_error");
}
