//! Integration tests for the V1 Invitation + Friendship facade.
//!
//! Exercises both `InvitationService` and `FriendshipService` impls against the
//! real in-memory store stack (GroupCore / BotCore / SessionManagementService
//! / FriendCore / FriendRequestCore / RelationCore), mirroring the sibling
//! `bcs-app-group` / `bcs-app-session` test harnesses.

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_domain::{
    invite_token_encode, InviteTargetType, InviteTokenPayload,
};
use bcs_friend::{FriendCore, FriendRequestCore};
use bcs_group::application::invite::InviteServiceImpl;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::invite::InviteService;
use bcs_service_api::application::session::{CreateOrReactivateCommand, SessionManagementService};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, ApplicationError, AuthenticatedCaller,
    AuthenticatedUserIdentity, CreateBotFriendRequest,
    CreateGroupInvitation, CreateSessionInvitation, DeleteResult, FriendshipService,
    FriendRequestDirection, FriendRequestStatus, Friendship, InvitationService,
    InvitationState, InvitationTargetType, ListBotFriendRequests, ListBotFriendships, Page,
    RejectFriendRequest, DeleteBotFriendship,
};
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, FriendCoreService, Group, GroupCoreService,
    GroupKind, GroupStatus, GroupStrategy, Participant, ParticipantRole, SessionKind,
};
use bcs_service_api::{
    FriendRequest as DomainFriendRequest, FriendRequestCoreService,
    FriendRequestDirection as DomainFriendRequestDirection,
    FriendRequestStatus as DomainFriendRequestStatus, ServiceError, ServiceResult,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

use bcs_app_invitation::{
    InvitationFriendshipServiceConfig, InvitationFriendshipServiceImpl,
};

const SECRET: &[u8] = b"test-invite-secret-32-bytes-long!!";

struct Fixture {
    service: InvitationFriendshipServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    friends: Arc<FriendCore>,
    sessions: Arc<SessionManagementServiceImpl>,
    invite: Arc<dyn InviteService>,
}

impl Fixture {
    async fn new() -> Self {
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let bots = Arc::new(BotCore::memory());
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let friend_requests = Arc::new(FriendRequestCore::memory(
            friends.clone(),
            bots.clone(),
        ));
        let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo.clone(),
        ));
        // Legacy `InviteService` implementation: V1 `accept_invitation` pivoted
        // to delegate `join_*_by_invite` here (Vcj6H) so the accept path
        // creates a Human Participant exactly the way legacy invite links do.
        // `NoopSystemMessageService` covers the `join_session_by_invite`
        // notification path without forcing a real dispatcher into the
        // fixture.
        let invite: Arc<dyn InviteService> = Arc::new(InviteServiceImpl {
            registry: bots.clone(),
            group: groups.clone(),
            session: sessions.clone(),
            system_message: Arc::new(NoopSystemMessageService),
            token_secret: SECRET.to_vec(),
            default_ttl_seconds: 3600,
            base_url: None,
            group_link_url: None,
            session_link_url: None,
        });
        let service = InvitationFriendshipServiceImpl::new(
            friends.clone(),
            friend_requests.clone(),
            groups.clone(),
            sessions.clone(),
            bots.clone(),
            invite.clone(),
            SECRET.to_vec(),
            InvitationFriendshipServiceConfig {
                default_ttl_seconds: 3600,
            },
        );
        Self {
            service,
            groups,
            bots,
            friends,
            sessions,
            invite,
        }
    }

    /// Build a V1 facade backed by the fixture's cores but substituting a
    /// custom `FriendRequestCoreService`. Used to exercise failure-propagation
    /// paths (e.g. a store whose `try_list_requests` errors) without touching
    /// the legacy default fixture wiring.
    fn build_service(
        &self,
        friend_requests: Arc<dyn FriendRequestCoreService>,
    ) -> InvitationFriendshipServiceImpl {
        InvitationFriendshipServiceImpl::new(
            self.friends.clone(),
            friend_requests,
            self.groups.clone(),
            self.sessions.clone(),
            self.bots.clone(),
            self.invite.clone(),
            SECRET.to_vec(),
            InvitationFriendshipServiceConfig {
                default_ttl_seconds: 3600,
            },
        )
    }

    async fn add_bot(&self, bot_uuid: &str) {
        self.add_bot_with_visibility(bot_uuid, "public").await;
    }

    /// Register a bot with `protected` visibility so friend requests targeting
    /// it stay `Pending` (the core auto-accepts only when the target is
    /// `public`).
    async fn add_protected_bot(&self, bot_uuid: &str) {
        self.add_bot_with_visibility(bot_uuid, "protected").await;
    }

    async fn add_bot_with_visibility(&self, bot_uuid: &str, visibility: &str) {
        let owner = Self::bot_owner(bot_uuid);
        self.bots
            .register(
                bot_uuid.to_string(),
                BotCapabilities {
                    name: Some(bot_uuid.to_string()),
                    visibility: visibility.into(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_uuid, owner, true)
            .await
            .expect("assign test Bot owner");
    }

    async fn store_group(&self, group_id: &str, driver: &str) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.originator = Some(Self::human_actor_id("staff-1"));
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
        self.groups.upsert(group).await.expect("store group");
    }

    /// Helper for Vcj6P: store a group whose `status` is set to a non-active
    /// value so the V1 mint paths reject with `conflict`.
    async fn store_group_with_status(
        &self,
        group_id: &str,
        driver: &str,
        status: GroupStatus,
    ) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.originator = Some(Self::human_actor_id("staff-1"));
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
        group.status = status;
        self.groups.upsert(group).await.expect("store group");
    }

    /// Helper for Vcj6M: store a DM parent group so a session invitation mint
    /// is rejected with `forbidden`.
    async fn store_dm_group(&self, group_id: &str, driver: &str, other: &str) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![
                Participant::bot(driver, ParticipantRole::Driver),
                Participant::bot(other, ParticipantRole::Consultant),
            ],
        );
        group.originator = Some(Self::human_actor_id("staff-1"));
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
        group.group_kind = GroupKind::Dm;
        self.groups.upsert(group).await.expect("store dm group");
    }

    async fn set_group_status(&self, group_id: &str, status: GroupStatus) {
        let mut group = self.groups.get(group_id).await.expect("group present");
        group.status = status;
        self.groups.upsert(group).await.expect("update group status");
    }

    async fn create_session(&self, group_id: &str, driver: &str) -> String {
        let group = self
            .groups
            .get(group_id)
            .await
            .expect("group exists for session");
        let params = NewSessionParams {
            session_kind: SessionKind::Chat,
            participants: vec![Participant::bot(driver, ParticipantRole::Driver)],
            group_version: Some(group.version),
            caller_id: Some(driver.to_string()),
            caller_principal: Some(driver.to_string()),
            input: None,
            created_by: Some(driver.to_string()),
            session_title: Some(format!("{group_id}-session")),
            id: None,
            meta: None,
        };
        let outcome = self
            .sessions
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: group_id.to_string(),
                session_id: None,
                params,
            })
            .await
            .expect("create session");
        outcome.session.id
    }

    fn bot_owner(bot_uuid: &str) -> &str {
        match bot_uuid {
            "bot-a" => "staff-1",
            "bot-b" => "staff-2",
            "bot-c" => "staff-3",
            "bot-x" => "staff-x",
            _ => "staff-owner",
        }
    }

    fn bot_principal(bot_uuid: &str) -> AuthenticatedCaller {
        Self::human_principal(Self::bot_owner(bot_uuid))
    }

    fn human_principal(subject_id: &str) -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: "dev".into(),
            user: Some(AuthenticatedUserIdentity {
                id: subject_id.to_string(),
                username: subject_id.to_string(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    fn human_principal_with_display(subject_id: &str, display_name: &str) -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: "dev".into(),
            user: Some(AuthenticatedUserIdentity {
                id: subject_id.to_string(),
                username: subject_id.to_string(),
                display_name: Some(display_name.to_string()),
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    fn bot_only_caller(bot_uuid: &str) -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: "dev".into(),
            user: None,
            bot: Some(bcs_service_api::application::v1::AuthenticatedBotIdentity {
                bot_uuid: bot_uuid.into(),
                owner_id: "staff-1".into(),
                app_id: 1,
                agent_code: "agent".into(),
            }),
            app: None,
            access_key: None,
        }
    }

    /// Actor ID legacy `ensure_human` records for the given staff_no. Used in
    /// tests to assert the joining Human participant lands under
    /// `human_<staff_no>` matching legacy convention.
    fn human_actor_id(staff_no: &str) -> String {
        format!("human_{staff_no}")
    }
}

fn assert_code(error: ApplicationError, expected: &str) {
    assert_eq!(error.code(), expected);
}

// ---------------------------------------------------------------------------
// Stub [`FriendRequestCoreService`] whose `try_list_requests` always errors,
// used to assert the V1 facade propagates persistence failures as
// `ApplicationError::Internal` (HTTP 500) instead of a 200 empty page.
// ---------------------------------------------------------------------------

struct FailingListFriendRequestCore;

#[async_trait]
impl FriendRequestCoreService for FailingListFriendRequestCore {
    async fn create_request(
        &self,
        _from_bot: &str,
        _to_bot: &str,
    ) -> ServiceResult<DomainFriendRequest> {
        Err(ServiceError::InternalError("not configured".into()))
    }

    async fn accept_request(&self, _request_id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn reject_request(&self, _request_id: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_request(&self, request_id: &str) -> ServiceResult<DomainFriendRequest> {
        Err(ServiceError::FriendRequestNotFound(request_id.to_string()))
    }

    async fn list_requests(
        &self,
        _bot_id: &str,
        _direction: DomainFriendRequestDirection,
        _status_filter: Option<DomainFriendRequestStatus>,
    ) -> Vec<DomainFriendRequest> {
        Vec::new()
    }

    async fn try_list_requests(
        &self,
        _bot_id: &str,
        _direction: DomainFriendRequestDirection,
        _status_filter: Option<DomainFriendRequestStatus>,
    ) -> ServiceResult<Vec<DomainFriendRequest>> {
        Err(ServiceError::InternalError(
            "simulated friend-request list failure".into(),
        ))
    }

    async fn cancel_pending_requests(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

// ── InvitationService ─────────────────────────────────────────────────

#[tokio::test]
async fn create_group_invitation_manager_ok() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: Some(1800),
        })
        .await
        .expect("manager may create invitation");

    assert_eq!(invitation.target_type, InvitationTargetType::Group);
    assert_eq!(invitation.target_id, "grp-1");
    assert_eq!(invitation.state, InvitationState::Pending);
    assert!(invitation.expires_at.is_some());
    assert!(invitation.created_at > 0);

    // The token must decode to a V1 payload carrying target_type = Group.
    let payload = bcs_domain::invite_token_decode_and_verify(&invitation.token, SECRET)
        .expect("token decodes");
    assert_eq!(payload.id, "grp-1");
    assert_eq!(payload.target_type, Some(InviteTargetType::Group));
}

#[tokio::test]
async fn create_group_invitation_non_manager_forbidden() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.store_group("grp-1", "bot-a").await;

    let error = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::bot_principal("bot-b"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect_err("non-manager is forbidden");

    assert!(matches!(error, ApplicationError::Forbidden(_)));
}

#[tokio::test]
async fn create_group_invitation_dm_group_rejected() {
    // VaGQI: DM (DirectMessage) groups are pairwise (participant_count=2);
    // minting an invitation would let a third participant join via accept, so
    // the facade must reject the mint, mirroring the legacy invite service.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;

    // Seed a DM group whose driver is bot-a (the manager).
    let mut group = Group::new(
        "dm-1",
        "bot-a",
        vec![
            Participant::bot("bot-a", ParticipantRole::Driver),
            Participant::bot("bot-b", ParticipantRole::Consultant),
        ],
    );
    group.originator = Some("bot-a".to_string());
    group.label = Some("dm-1".to_string());
    group.group_strategy = GroupStrategy::Chat;
    group.group_kind = GroupKind::Dm;
    fx.groups.upsert(group).await.expect("store dm group");

    let error = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::bot_principal("bot-a"),
            group_id: "dm-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect_err("DM groups reject invitation minting");

    // Legacy invite service rejects DM with Forbidden; V1 mirrors it.
    assert!(
        matches!(error, ApplicationError::Forbidden(_)),
        "expected Forbidden, got {error:?}",
    );
    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn create_session_invitation_manager_ok() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx.create_session("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            caller: Fixture::bot_principal("bot-a"),
            session_id: session_id.clone(),
            expires_in_seconds: None,
        })
        .await
        .expect("group manager may create session invitation");

    assert_eq!(invitation.target_type, InvitationTargetType::Session);
    assert_eq!(invitation.target_id, session_id);

    let payload = bcs_domain::invite_token_decode_and_verify(&invitation.token, SECRET)
        .expect("token decodes");
    assert_eq!(payload.id, session_id);
    assert_eq!(payload.target_type, Some(InviteTargetType::Session));
}

#[tokio::test]
async fn create_group_invitation_inactive_group_rejected() {
    // Vcj6P: legacy `create_group_invite_token` L151-153 rejects "group is not
    // active". V1 mirrors it so a Completed/Closed/Error group cannot mint an
    // invitation token.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group_with_status("grp-1", "bot-a", GroupStatus::Completed).await;

    let error = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect_err("inactive group rejects mint");

    assert!(
        matches!(error, ApplicationError::Conflict { .. }),
        "expected Conflict, got {error:?}",
    );
    assert_eq!(error.code(), "conflict");
}

#[tokio::test]
async fn create_session_invitation_dm_parent_group_rejected() {
    // Vcj6M: legacy `create_session_invite_token` L186-189 rejects DM parent
    // groups. V1 mirrors it so session invitations on pairwise DM targets are
    // not minted (a third participant cannot join a DM).
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.store_dm_group("dm-1", "bot-a", "bot-b").await;
    let session_id = fx.create_session("dm-1", "bot-a").await;

    let error = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            caller: Fixture::bot_principal("bot-a"),
            session_id: session_id.clone(),
            expires_in_seconds: None,
        })
        .await
        .expect_err("DM parent group rejects session invitation mint");

    assert!(
        matches!(error, ApplicationError::Forbidden(_)),
        "expected Forbidden, got {error:?}",
    );
    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn create_session_invitation_inactive_parent_group_rejected() {
    // Vcj6P: the session invitation path's parent group shares the same
    // lifecycle as the group, so mirror the inactive guard on the parent
    // here too. The session itself is still `Active`; only the parent group
    // transitions to `Completed`.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx.create_session("grp-1", "bot-a").await;
    fx.set_group_status("grp-1", GroupStatus::Completed).await;

    let error = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            caller: Fixture::bot_principal("bot-a"),
            session_id,
            expires_in_seconds: None,
        })
        .await
        .expect_err("inactive parent group rejects session invitation mint");

    assert!(
        matches!(error, ApplicationError::Conflict { .. }),
        "expected Conflict, got {error:?}",
    );
    assert_eq!(error.code(), "conflict");
}

#[tokio::test]
async fn accept_invitation_human_joins_group() {
    // Vcj6H: V1 accept pivoted to legacy Human-only. A Human Principal accepts
    // via `staff_no` (subject id); the legacy `join_group_by_invite` path
    // creates a Human Participant (Consultant role, Present mode), not a Bot.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("create invitation");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token: invitation.token,
        })
        .await
        .expect("human accepts");

    assert_eq!(result.target_type, InvitationTargetType::Group);
    assert_eq!(result.target_id, "grp-1");
    assert!(result.joined);
    assert_eq!(result.already_joined, Some(false));

    let group = fx.groups.get("grp-1").await.expect("group present");
    let actor = Fixture::human_actor_id("staff-1");
    let joined_as = group
        .participants
        .iter()
        .find(|p| p.bot_uuid == actor)
        .expect("human participant is now a member");
    assert_eq!(joined_as.role, ParticipantRole::Consultant);
    assert_eq!(joined_as.actor_kind, bcs_service_api::ActorKind::Human);
    assert_eq!(
        joined_as.mode,
        Some(bcs_service_api::ParticipantMode::Present)
    );
}

#[tokio::test]
async fn accept_invitation_human_display_name_provides_nick_name() {
    // Vcj6H (companion): when the Human Principal carries a `display_name`, the
    // V1 facade forwards it as `nick_name` to the legacy `JoinByInviteCommand`,
    // and the legacy `join_group_by_invite` writes the Participant `bot_name`
    // from that nick_name — not from the staff_no or fallback username.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("create invitation");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal_with_display("staff-1", "Alice"),
            token: invitation.token,
        })
        .await
        .expect("human accepts with display_name");
    assert!(result.joined);

    let group = fx.groups.get("grp-1").await.expect("group present");
    let joined_as = group
        .participants
        .iter()
        .find(|p| p.bot_uuid == Fixture::human_actor_id("staff-1"))
        .expect("alice is now a participant");
    assert_eq!(joined_as.bot_name.as_deref(), Some("Alice"));
    assert_eq!(joined_as.actor_kind, bcs_service_api::ActorKind::Human);
}

#[tokio::test]
async fn accept_invitation_caller_without_user_rejected() {
    // Vcj6H: a valid Caller without User may not accept invitations. In
    // particular, Bot owner_id is not a Human fallback.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.store_group("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("create invitation");

    let error = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::bot_only_caller("bot-b"),
            token: invitation.token,
        })
        .await
        .expect_err("caller without User rejected");

    assert!(
        matches!(error, ApplicationError::Forbidden(_)),
        "expected Forbidden, got {error:?}",
    );
    assert_eq!(error.code(), "forbidden");

    // Regression guard: no participant landed on the group.
    let group = fx.groups.get("grp-1").await.expect("group present");
    assert!(
        !group
            .participants
            .iter()
            .any(|p| p.bot_uuid == "bot-b" || p.bot_uuid == "human_bot-b"),
        "bot principal accept must not write a participant, got {group:?}",
    );
}

#[tokio::test]
async fn accept_invitation_expired_is_gone() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;

    let expired = invite_token_encode(
        &InviteTokenPayload {
            v: 1,
            id: "grp-1".to_string(),
            exp: 1, // far in the past
            target_type: Some(InviteTargetType::Group),
        },
        SECRET,
    );

    let error = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token: expired,
        })
        .await
        .expect_err("expired token is Gone");

    assert!(matches!(error, ApplicationError::Gone { .. }));
    assert_eq!(error.code(), "invitation_expired");
}

#[tokio::test]
async fn accept_invitation_legacy_token_without_target_type_rejected() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;

    let legacy = invite_token_encode(
        &InviteTokenPayload {
            v: 1,
            id: "grp-1".to_string(),
            exp: now_secs() + 3600,
            target_type: None,
        },
        SECRET,
    );

    let error = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token: legacy,
        })
        .await
        .expect_err("legacy token rejected");

    assert_code(error, "invalid_request");
}

#[tokio::test]
async fn accept_invitation_already_member_is_idempotent() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("create invitation");

    fx.service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token: invitation.token.clone(),
        })
        .await
        .expect("first accept");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token: invitation.token,
        })
        .await
        .expect("second accept is idempotent");

    assert!(!result.joined);
    assert_eq!(result.already_joined, Some(true));
}

#[tokio::test]
async fn accept_invitation_session_target_joins() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx.create_session("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            caller: Fixture::bot_principal("bot-a"),
            session_id: session_id.clone(),
            expires_in_seconds: None,
        })
        .await
        .expect("create session invitation");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token: invitation.token,
        })
        .await
        .expect("human joins session");

    assert_eq!(result.target_type, InvitationTargetType::Session);
    assert!(result.joined);
    let session = fx
        .sessions
        .get(&session_id)
        .await
        .expect("session lookup")
        .expect("session present");
    let actor = Fixture::human_actor_id("staff-1");
    assert!(session.participants.iter().any(|p| p.bot_uuid == actor));
    let joined = session
        .participants
        .iter()
        .find(|p| p.bot_uuid == actor)
        .expect("human participant present");
    assert_eq!(joined.role, ParticipantRole::Consultant);
    assert_eq!(joined.actor_kind, bcs_service_api::ActorKind::Human);
}

#[tokio::test]
async fn accept_invitation_target_group_deleted_returns_invitation_not_found() {
    // V1 `acceptInvitation` 404 contract declares only `invitation_not_found`;
    // when the invitation token points at a Group that no longer exists, the
    // facade must NOT surface a `group_not_found` code (which would leak the
    // target type and expose an undeclared error code).
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;

    // Mint a V1 invitation token against a group id that was never stored,
    // simulating "target deleted before accept".
    let token = invite_token_encode(
        &InviteTokenPayload {
            v: 1,
            id: "grp-deleted".to_string(),
            exp: now_secs() + 3600,
            target_type: Some(InviteTargetType::Group),
        },
        SECRET,
    );

    let error = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token,
        })
        .await
        .expect_err("missing group target is invitation_not_found");

    assert!(
        matches!(error, ApplicationError::NotFound { .. }),
        "expected ApplicationError::NotFound, got {error:?}",
    );
    assert_eq!(error.code(), "invitation_not_found");
    // Regression guard: the old code path surfaced `group_not_found`. Ensure
    // the target-type leak does not silently return.
    assert_ne!(error.code(), "group_not_found");
}

#[tokio::test]
async fn accept_invitation_target_session_deleted_returns_invitation_not_found() {
    // V1 `acceptInvitation` 404 contract declares only `invitation_not_found`;
    // when the invitation token points at a Session that no longer exists, the
    // facade must NOT surface a `session_not_found` code (which would leak the
    // target type and expose an undeclared error code).
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;

    // Mint a V1 invitation token against a session id that was never stored,
    // simulating "target deleted before accept".
    let token = invite_token_encode(
        &InviteTokenPayload {
            v: 1,
            id: "session-deleted".to_string(),
            exp: now_secs() + 3600,
            target_type: Some(InviteTargetType::Session),
        },
        SECRET,
    );

    let error = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token,
        })
        .await
        .expect_err("missing session target is invitation_not_found");

    assert!(
        matches!(error, ApplicationError::NotFound { .. }),
        "expected ApplicationError::NotFound, got {error:?}",
    );
    assert_eq!(error.code(), "invitation_not_found");
    // Regression guard: the old code path surfaced `session_not_found`. Ensure
    // the target-type leak does not silently return.
    assert_ne!(error.code(), "session_not_found");
}

// ── FriendshipService ─────────────────────────────────────────────────

#[tokio::test]
async fn list_friendships_sorted_desc_with_pagination() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.add_bot("bot-c").await;

    fx.friends
        .add_friendship("bot-a", "bot-b")
        .await
        .expect("add bot-b friendship");
    // Sleep so the second friendship has a strictly greater created_at.
    tokio::time::sleep(Duration::from_millis(25)).await;
    fx.friends
        .add_friendship("bot-a", "bot-c")
        .await
        .expect("add bot-c friendship");

    let page = fx
        .service
        .list_bot_friendships(ListBotFriendships {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            offset: 0,
            limit: 100,
        })
        .await
        .expect("list friendships");

    assert_eq!(page.total, 2);
    // created_at DESC: the bot-c friendship (added later) comes first.
    assert_eq!(page.items[0].friend_bot_uuid, "bot-c");
    assert_eq!(page.items[1].friend_bot_uuid, "bot-b");

    let page_two = fx
        .service
        .list_bot_friendships(ListBotFriendships {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            offset: 1,
            limit: 1,
        })
        .await
        .expect("list friendships page 2");
    assert_eq!(page_two.total, 2);
    assert_eq!(page_two.items.len(), 1);
    assert_eq!(page_two.items[0].friend_bot_uuid, "bot-b");
}

#[tokio::test]
async fn list_friendships_non_owner_forbidden() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-x").await;

    let error = fx
        .service
        .list_bot_friendships(ListBotFriendships {
            caller: Fixture::bot_principal("bot-x"),
            bot_uuid: "bot-a".to_string(),
            offset: 0,
            limit: 10,
        })
        .await
        .expect_err("non-owner forbidden");

    assert!(matches!(error, ApplicationError::Forbidden(_)));
}

#[tokio::test]
async fn list_friendships_does_not_use_authenticated_bot_owner_as_human() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;

    let error = fx
        .service
        .list_bot_friendships(ListBotFriendships {
            caller: Fixture::bot_only_caller("bot-a"),
            bot_uuid: "bot-a".to_string(),
            offset: 0,
            limit: 10,
        })
        .await
        .expect_err("Bot identity must not supply Human ownership");

    assert_eq!(error.code(), "forbidden");
}

#[tokio::test]
async fn remove_friendship_is_idempotent() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.friends
        .add_friendship("bot-a", "bot-b")
        .await
        .expect("add friendship");

    let first = fx
        .service
        .delete_bot_friendship(DeleteBotFriendship {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            friend_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("remove friendship");
    assert!(first.deleted);

    let second = fx
        .service
        .delete_bot_friendship(DeleteBotFriendship {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            friend_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("remove again");
    assert!(!second.deleted);
}

#[tokio::test]
async fn create_friend_request_bot_self() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_protected_bot("bot-c").await;

    let request = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
        })
        .await
        .expect("create request");

    assert_eq!(request.from_bot_uuid, "bot-a");
    assert_eq!(request.to_bot_uuid, "bot-c");
    assert_eq!(request.status, FriendRequestStatus::Pending);
    assert!(request.request_id.len() > 0);
    let _ = request.message; // optional field present (None)
}

#[tokio::test]
async fn create_friend_request_cannot_add_self() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-a".to_string(),
        })
        .await
        .expect_err("cannot add self");

    assert_code(error, "cannot_add_self");
}

#[tokio::test]
async fn create_friend_request_duplicate_is_conflict() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_protected_bot("bot-c").await;

    fx.service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
        })
        .await
        .expect("first request");

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
        })
        .await
        .expect_err("duplicate conflict");

    assert_code(error, "friend_request_already_exists");
}

#[tokio::test]
async fn create_friend_request_unknown_target_is_not_found() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-ghost".to_string(),
        })
        .await
        .expect_err("unknown target bot");

    assert_code(error, "bot_not_found");
}

#[tokio::test]
async fn list_friend_requests_direction_filter_and_sort() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_protected_bot("bot-b").await;
    fx.add_protected_bot("bot-c").await;

    fx.service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("a -> b");
    tokio::time::sleep(Duration::from_millis(25)).await;
    fx.service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
        })
        .await
        .expect("a -> c");

    // bot-a's sent: two requests, newest first (bot-c before bot-b).
    let sent = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            direction: FriendRequestDirection::Sent,
            status: None,
            offset: 0,
            limit: 100,
        })
        .await
        .expect("list sent");
    assert_eq!(sent.total, 2);
    assert_eq!(sent.items[0].to_bot_uuid, "bot-c");
    assert_eq!(sent.items[1].to_bot_uuid, "bot-b");

    // bot-b's received: one request.
    let received = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: Fixture::bot_principal("bot-b"),
            bot_uuid: "bot-b".to_string(),
            direction: FriendRequestDirection::Received,
            status: None,
            offset: 0,
            limit: 100,
        })
        .await
        .expect("list received");
    assert_eq!(received.total, 1);
    assert_eq!(received.items[0].from_bot_uuid, "bot-a");

    // Pagination: first page of size 1 returns only the newest.
    let paged = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            direction: FriendRequestDirection::Sent,
            status: None,
            offset: 0,
            limit: 1,
        })
        .await
        .expect("list sent paged");
    assert_eq!(paged.total, 2);
    assert_eq!(paged.items.len(), 1);
    assert_eq!(paged.items[0].to_bot_uuid, "bot-c");
}

#[tokio::test]
async fn list_bot_friend_requests_propagates_repo_failure_as_internal() {
    // When the friend-request store's `try_list_requests` fails, the V1 facade
    // must surface the failure as `ApplicationError::Internal` (HTTP 500)
    // rather than masking it as an empty 200 page (the legacy `list_requests`
    // swallowing behavior).
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;

    let failing: Arc<dyn FriendRequestCoreService> = Arc::new(FailingListFriendRequestCore);
    let service = fx.build_service(failing);

    let error = service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            direction: FriendRequestDirection::Sent,
            status: None,
            offset: 0,
            limit: 10,
        })
        .await
        .expect_err("list must propagate repo failure");
    assert!(
        matches!(error, ApplicationError::Internal(_)),
        "expected ApplicationError::Internal, got {error:?}",
    );
    assert_eq!(error.code(), "internal_error");
}

#[tokio::test]
async fn accept_friend_request_receiver_ok() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_protected_bot("bot-b").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("create request");

    let accepted = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id.clone(),
        })
        .await
        .expect("receiver accepts");

    assert_eq!(accepted.status, FriendRequestStatus::Accepted);
    assert_eq!(accepted.request_id, created.request_id);
}

#[tokio::test]
async fn accept_friend_request_non_receiver_forbidden() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_protected_bot("bot-b").await;
    fx.add_bot("bot-c").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("create request");

    let error = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-c"),
            request_id: created.request_id,
        })
        .await
        .expect_err("non-receiver forbidden");

    assert!(matches!(error, ApplicationError::Forbidden(_)));
}

#[tokio::test]
async fn accept_friend_request_cannot_accept_rejected() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_protected_bot("bot-b").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("create request");

    fx.service
        .reject_friend_request(RejectFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id.clone(),
        })
        .await
        .expect("reject first");

    let error = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id,
        })
        .await
        .expect_err("cannot accept rejected");

    assert_code(error, "conflict");
}

#[tokio::test]
async fn reject_friend_request_receiver_ok_and_sender_forbidden() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_protected_bot("bot-b").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("create request");

    // Sender may not reject (only the receiver may).
    let sender_err = fx
        .service
        .reject_friend_request(RejectFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            request_id: created.request_id.clone(),
        })
        .await
        .expect_err("sender cannot reject");
    assert!(matches!(sender_err, ApplicationError::Forbidden(_)));

    let rejected = fx
        .service
        .reject_friend_request(RejectFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id,
        })
        .await
        .expect("receiver rejects");
    assert_eq!(rejected.status, FriendRequestStatus::Rejected);
}

#[tokio::test]
async fn friendship_page_shape_is_identity_projected() {
    // Sanity-check the V1 Friendship projection carries the same fields as the
    // domain edge (no rename surprises), exercising Page<Friendship>.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.friends.add_friendship("bot-a", "bot-b").await.unwrap();

    let page: Page<Friendship> = fx
        .service
        .list_bot_friendships(ListBotFriendships {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            offset: 0,
            limit: 10,
        })
        .await
        .expect("list");
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].bot_uuid, "bot-a");
    assert_eq!(page.items[0].friend_bot_uuid, "bot-b");
    assert!(page.items[0].created_at > 0);

    // DeleteResult + FriendRequest are reachable and shaped as expected.
    let del = fx
        .service
        .delete_bot_friendship(DeleteBotFriendship {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            friend_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("remove");
    assert_eq!(del, DeleteResult { deleted: true });
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}
