//! Integration tests for the V1 Invitation + Friendship facade.
//!
//! Exercises both `InvitationService` and `FriendshipService` impls against the
//! real in-memory store stack (GroupCore / BotCore / SessionManagementService
//! / FriendCore / RelationCore), mirroring the sibling `bcs-app-group` /
//! `bcs-app-session` test harnesses. The `FriendshipService` cases inject the
//! shared `FakeFriendService` / `FakeConnectService` fakes (see
//! `tests/support`) because the facade is converged onto the legacy
//! `FriendService` use-case surface.

mod support;

use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_domain::{
    invite_token_encode, InviteTargetType, InviteTokenPayload,
};
use bcs_friend::FriendCore;
use bcs_group::application::invite::InviteServiceImpl;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::invite::{CreateInviteTokenCommand, InviteService};
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
    FriendRequest as DomainFriendRequest,
    FriendRequestDirection as DomainFriendRequestDirection, RequestAuthHeaders, ServiceError,
    ServiceResult,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopSystemMessageService;

use bcs_app_invitation::{
    InvitationFriendshipServiceConfig, InvitationFriendshipServiceImpl,
};
use support::{FakeConnectService, FakeFriendService, FriendCall};

const SECRET: &[u8] = b"test-invite-secret-32-bytes-long!!";

struct Fixture {
    service: InvitationFriendshipServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    friends: Arc<FriendCore>,
    sessions: Arc<SessionManagementServiceImpl>,
    invite: Arc<dyn InviteService>,
    authority_repo: Arc<MemoryBotRepo>,
    legacy_friend: Arc<FakeFriendService>,
    connect: Arc<FakeConnectService>,
}

impl Fixture {
    async fn new() -> Self {
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let bots = Arc::new(BotCore::memory());
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
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
        let authority_repo = Arc::new(MemoryBotRepo::new());
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
            bot_authority: Some(
                bcs_test_support::authority_hook_for(
                    authority_repo.clone(),
                ),
            ),
        });
        let legacy_friend = Arc::new(FakeFriendService::new());
        let connect = Arc::new(FakeConnectService::new().with_legacy_source(legacy_friend.clone()));
        let service = InvitationFriendshipServiceImpl::new(
            friends.clone(),
            legacy_friend.clone(),
            groups.clone(),
            sessions.clone(),
            bots.clone(),
            invite.clone(),
            SECRET.to_vec(),
            InvitationFriendshipServiceConfig {
                default_ttl_seconds: 3600,
            },
        )
        .with_bot_authority_hook(
            bcs_test_support::authority_hook_for(authority_repo.clone()),
        )
        .with_friend_connection_service(connect.clone());
        Self {
            service,
            groups,
            bots,
            friends,
            sessions,
            invite,
            authority_repo,
            legacy_friend,
            connect,
        }
    }

    async fn add_bot(&self, bot_uuid: &str) {
        self.add_bot_with_visibility(bot_uuid, "public").await;
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
        // Row 11: the CURRENT owner edge replaces the
        // historical `created_by` fact.
        bcs_test_support::grant_owner_edge(&self.authority_repo, bot_uuid, owner)
            .await;
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

    async fn store_legacy_group(
        &self,
        group_id: &str,
        driver_bot: &str,
        originator_actor: &str,
    ) {
        let mut group = Group::new(
            group_id,
            driver_bot,
            vec![Participant::bot(driver_bot, ParticipantRole::Driver)],
        );
        group.originator = Some(originator_actor.to_string());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
        self.groups.upsert(group).await.expect("store legacy group");
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
        self.create_session_with_participants(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        )
        .await
    }

    /// Like `create_session`, but seeds the session with the given
    /// participants (the driver is always included first).
    async fn create_session_with_participants(
        &self,
        group_id: &str,
        driver: &str,
        participants: Vec<Participant>,
    ) -> String {
        let group = self
            .groups
            .get(group_id)
            .await
            .expect("group exists for session");
        let params = NewSessionParams {
            session_kind: SessionKind::Chat,
            participants,
            group_version: Some(group.version),
            caller_id: Some(driver.to_string()),
            caller_principal: Some(driver.to_string()),
            input: None,
            created_by: Some(driver.to_string()),
            session_title: Some(format!("{group_id}-session")),
            id: None,
            meta: None,
            message_visibility_version: 1,
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
            tenant: Some("dev".into()),
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
            tenant: Some("dev".into()),
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
            tenant: Some("dev".into()),
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
async fn human_owner_of_legacy_group_originator_bot_can_create_group_invitation() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.store_legacy_group("grp-1", "bot-b", "bot-a")
        .await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            caller: Fixture::human_principal("staff-1"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("Human owner may act for the group originator Bot");

    assert_eq!(invitation.target_type, InvitationTargetType::Group);
    assert_eq!(invitation.target_id, "grp-1");
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
async fn create_session_invitation_session_participant_ok() {
    // Session invitations are gated on session membership only: a Human
    // Consultant participant — not the group driver, originator, or a bot
    // owner — may mint. staff-9 holds no group-level privilege on grp-1.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx
        .create_session_with_participants(
            "grp-1",
            "bot-a",
            vec![
                Participant::bot("bot-a", ParticipantRole::Driver),
                Participant::human(
                    Fixture::human_actor_id("staff-9"),
                    ParticipantRole::Consultant,
                ),
            ],
        )
        .await;

    let invitation = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            caller: Fixture::human_principal("staff-9"),
            session_id: session_id.clone(),
            expires_in_seconds: None,
        })
        .await
        .expect("session participant may create session invitation");

    assert_eq!(invitation.target_type, InvitationTargetType::Session);
    assert_eq!(invitation.target_id, session_id);

    let payload = bcs_domain::invite_token_decode_and_verify(&invitation.token, SECRET)
        .expect("token decodes");
    assert_eq!(payload.id, session_id);
    assert_eq!(payload.target_type, Some(InviteTargetType::Session));
}

#[tokio::test]
async fn create_session_invitation_human_owner_of_participant_bot_ok() {
    // A Human caller who is not itself a session participant may still mint
    // when one of the Human's owned Bots participates in the session.
    // staff-1 owns bot-a (see `Fixture::bot_owner`), which is the session's
    // driver participant.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_legacy_group("grp-1", "bot-a", "human_other")
        .await;
    let session_id = fx.create_session("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            caller: Fixture::human_principal("staff-1"),
            session_id: session_id.clone(),
            expires_in_seconds: None,
        })
        .await
        .expect("owner of a participant Bot may create session invitation");

    assert_eq!(invitation.target_type, InvitationTargetType::Session);
    assert_eq!(invitation.target_id, session_id);
}

#[tokio::test]
async fn create_session_invitation_non_participant_forbidden() {
    // A Human caller with no session membership of its own and no owned Bot in
    // the session is forbidden; group-level privilege does not substitute for
    // session membership.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_legacy_group("grp-1", "bot-a", "human_other")
        .await;
    let session_id = fx.create_session("grp-1", "bot-a").await;

    let error = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            caller: Fixture::human_principal("staff-9"),
            session_id: session_id.clone(),
            expires_in_seconds: None,
        })
        .await
        .expect_err("non-participant without a participant Bot is forbidden");

    assert!(
        matches!(error, ApplicationError::Forbidden(_)),
        "expected Forbidden, got {error:?}",
    );
    assert_eq!(error.code(), "forbidden");
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
            message_view_scope: None,
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
            message_view_scope: None,
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
            message_view_scope: None,
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
            message_view_scope: None,
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
            message_view_scope: None,
        })
        .await
        .expect_err("legacy token rejected");

    assert_code(error, "invalid_request");
}

#[tokio::test]
async fn accept_invitation_accepts_legacy_route_group_token() {
    // Compatibility: the legacy `bcs-http` invite routes now mint tokens that
    // carry `target_type`, so a token from `create_group_invite_link` is
    // accepted and routed by V1 `acceptInvitation`.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;

    let legacy = fx
        .invite
        .create_group_invite_token(CreateInviteTokenCommand {
            caller_actor_id: Some("bot-a".to_string()),
            caller_staff_no: None,
            target_id: "grp-1".to_string(),
            ttl_seconds: None,
        })
        .await
        .expect("legacy group invite link");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-9"),
            token: legacy.invite_token,
            message_view_scope: None,
        })
        .await
        .expect("V1 accepts the legacy-route group token");

    assert_eq!(result.target_type, InvitationTargetType::Group);
    assert_eq!(result.target_id, "grp-1");
    assert!(result.joined);

    let group = fx.groups.get("grp-1").await.expect("group present");
    let actor = Fixture::human_actor_id("staff-9");
    assert!(group.participants.iter().any(|p| p.bot_uuid == actor));
}

#[tokio::test]
async fn accept_invitation_accepts_legacy_route_session_token() {
    // Compatibility: a token from the legacy `create_session_invite_link`
    // route carries `target_type: session` and is accepted by V1
    // `acceptInvitation`, joining the human to the session.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx.create_session("grp-1", "bot-a").await;

    let legacy = fx
        .invite
        .create_session_invite_token(CreateInviteTokenCommand {
            caller_actor_id: Some("bot-a".to_string()),
            caller_staff_no: None,
            target_id: session_id.clone(),
            ttl_seconds: None,
        })
        .await
        .expect("legacy session invite link");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-9"),
            token: legacy.invite_token,
            message_view_scope: None,
        })
        .await
        .expect("V1 accepts the legacy-route session token");

    assert_eq!(result.target_type, InvitationTargetType::Session);
    assert_eq!(result.target_id, session_id);
    assert!(result.joined);

    let session = fx
        .sessions
        .get(&session_id)
        .await
        .expect("session lookup")
        .expect("session present");
    let actor = Fixture::human_actor_id("staff-9");
    assert!(session.participants.iter().any(|p| p.bot_uuid == actor));
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
            message_view_scope: None,
        })
        .await
        .expect("first accept");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            caller: Fixture::human_principal("staff-1"),
            token: invitation.token,
            message_view_scope: None,
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
    // staff-9 is the session participant who mints; staff-1 is the invitee.
    let session_id = fx
        .create_session_with_participants(
            "grp-1",
            "bot-a",
            vec![
                Participant::bot("bot-a", ParticipantRole::Driver),
                Participant::human(
                    Fixture::human_actor_id("staff-9"),
                    ParticipantRole::Consultant,
                ),
            ],
        )
        .await;

    let invitation = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            caller: Fixture::human_principal("staff-9"),
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
            message_view_scope: None,
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
            message_view_scope: None,
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
            message_view_scope: None,
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
async fn delete_bot_friendship_uses_connect_revoke() {
    // The V1 delete path is the connect `revoke_friend` call: `deleted` is
    // "the connect revoke returned edge ids", not a legacy friendships-row
    // delete.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.connect.add_friendship("bot-a", "bot-b");

    let first = fx
        .service
        .delete_bot_friendship(DeleteBotFriendship {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            friend_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("revoke friendship");
    assert!(first.deleted);

    let second = fx
        .service
        .delete_bot_friendship(DeleteBotFriendship {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            friend_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("revoke again");
    assert!(!second.deleted, "the connect revoke returned no edge ids");

    assert_eq!(
        fx.connect.revoke_calls(),
        vec![
            ("bot-a".to_string(), "bot-b".to_string()),
            ("bot-a".to_string(), "bot-b".to_string()),
        ],
    );
}

#[tokio::test]
async fn delete_bot_friendship_allows_either_endpoint() {
    // The contract allows either endpoint of the friendship to initiate the
    // delete: bot-a (not manageable by staff-2) is the primary bot_uuid, so
    // the facade falls back to the friend endpoint bot-b.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.connect.add_friendship("bot-a", "bot-b");

    let deleted = fx
        .service
        .delete_bot_friendship(DeleteBotFriendship {
            caller: Fixture::bot_principal("bot-b"),
            bot_uuid: "bot-a".to_string(),
            friend_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("the friend endpoint may manage the pair");
    assert!(deleted.deleted);
    assert_eq!(
        fx.connect.revoke_calls(),
        vec![("bot-a".to_string(), "bot-b".to_string())],
    );
}

#[tokio::test]
async fn delete_bot_friendship_rejects_unmanaged_endpoints() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.add_bot("bot-x").await;

    let error = fx
        .service
        .delete_bot_friendship(DeleteBotFriendship {
            caller: Fixture::bot_principal("bot-x"),
            bot_uuid: "bot-a".to_string(),
            friend_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect_err("neither endpoint is manageable");
    assert_eq!(error.code(), "forbidden");
    assert!(
        fx.connect.revoke_calls().is_empty(),
        "the control leg must run before the connect revoke",
    );
}

#[tokio::test]
async fn create_friend_request_forwards_facade_command_and_projects_result() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-c").await;

    let request = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
            request_auth: None,
        })
        .await
        .expect("create request");

    assert_eq!(request.request_id, "req-1");
    assert_eq!(request.from_bot_uuid, "bot-a");
    assert_eq!(request.to_bot_uuid, "bot-c");
    assert_eq!(request.status, FriendRequestStatus::Pending);
    assert_eq!(request.message, None);
    assert!(request.created_at > 0);
    // The legacy facade is called with the path Bot as the caller.
    assert_eq!(
        fx.legacy_friend.calls(),
        vec![FriendCall::Create {
            caller_actor_id: "bot-a".to_string(),
            to_bot: "bot-c".to_string(),
        }],
    );
}

#[tokio::test]
async fn create_friend_request_cannot_add_self() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.legacy_friend.fail_create_with(ServiceError::CannotAddSelf);

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-a".to_string(),
            request_auth: None,
        })
        .await
        .expect_err("cannot add self");

    assert_code(error, "cannot_add_self");
}

#[tokio::test]
async fn create_friend_request_duplicate_is_conflict() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-c").await;
    fx.legacy_friend
        .fail_create_with(ServiceError::PendingRequestExists {
            request_id: "req-existing".to_string(),
            from_bot: Some("bot-a".to_string()),
            to_bot: Some("bot-c".to_string()),
        });

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
            request_auth: None,
        })
        .await
        .expect_err("duplicate pending request");

    assert!(
        matches!(error, ApplicationError::Conflict { .. }),
        "expected Conflict, got {error:?}",
    );
    assert_code(error, "friend_request_already_exists");
}

#[tokio::test]
async fn create_friend_request_unknown_target_is_not_found() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.legacy_friend
        .fail_create_with(ServiceError::BotNotFound("bot-ghost".to_string()));

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-ghost".to_string(),
            request_auth: None,
        })
        .await
        .expect_err("unknown target bot");

    assert!(
        matches!(error, ApplicationError::NotFound { .. }),
        "expected NotFound, got {error:?}",
    );
    assert_code(error, "bot_not_found");
}

#[tokio::test]
async fn create_friend_request_hidden_target_is_not_found() {
    // The legacy facade disguises a hidden/private target as "not found"
    // (`ServiceError::BotNotFound`); the V1 route keeps the 404 shape.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.legacy_friend
        .fail_create_with(ServiceError::BotNotFound("bot-hidden".to_string()));

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-hidden".to_string(),
            request_auth: None,
        })
        .await
        .expect_err("hidden target is reported as unknown");

    assert_code(error, "bot_not_found");
}

#[tokio::test]
async fn create_maps_private_bot_cannot_collaborate_to_forbidden() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-c").await;
    fx.legacy_friend
        .fail_create_with(ServiceError::PrivateBotCannotCollaborate);

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
            request_auth: None,
        })
        .await
        .expect_err("private caller cannot collaborate");

    assert!(
        matches!(error, ApplicationError::Forbidden(_)),
        "expected Forbidden, got {error:?}",
    );
    assert_eq!(
        error.to_string(),
        format!("forbidden: {}", ServiceError::PrivateBotCannotCollaborate),
    );
}

#[tokio::test]
async fn create_maps_facade_forbidden_to_forbidden() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-c").await;
    fx.legacy_friend
        .forbid_create("target refuses friend requests");

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
            request_auth: None,
        })
        .await
        .expect_err("facade-level Forbidden");

    assert!(matches!(error, ApplicationError::Forbidden(_)));
    assert_code(error, "forbidden");
}

#[tokio::test]
async fn list_friend_requests_direction_filter_and_sort() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.add_bot("bot-c").await;

    // The legacy facade returns repo order (unsorted); the V1 projection must
    // sort `created_at` DESC with a `request_id` ASC tie-breaker.
    fx.legacy_friend.seed(DomainFriendRequest {
        id: "req-old".to_string(),
        from_bot: "bot-a".to_string(),
        to_bot: "bot-b".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: 100,
        updated_at: 100,
    });
    fx.legacy_friend.seed(DomainFriendRequest {
        id: "req-new".to_string(),
        from_bot: "bot-a".to_string(),
        to_bot: "bot-c".to_string(),
        status: FriendRequestStatus::Accepted,
        created_at: 300,
        updated_at: 300,
    });
    fx.legacy_friend.seed(DomainFriendRequest {
        id: "req-mid".to_string(),
        from_bot: "bot-a".to_string(),
        to_bot: "bot-b".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: 200,
        updated_at: 200,
    });
    fx.legacy_friend.seed(DomainFriendRequest {
        id: "req-tie".to_string(),
        from_bot: "bot-a".to_string(),
        to_bot: "bot-c".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: 300,
        updated_at: 300,
    });

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

    assert_eq!(sent.total, 4);
    let ids: Vec<&str> = sent.items.iter().map(|r| r.request_id.as_str()).collect();
    // created_at DESC; request_id ASC on the created_at tie.
    assert_eq!(ids, vec!["req-new", "req-tie", "req-mid", "req-old"]);
    // `accepted` stays the V1 word for the connect `approved` state.
    assert_eq!(sent.items[0].status, FriendRequestStatus::Accepted);
    assert_eq!(sent.items[1].status, FriendRequestStatus::Pending);

    // Status filter and direction are forwarded to the legacy facade.
    let accepted = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            direction: FriendRequestDirection::Sent,
            status: Some(FriendRequestStatus::Accepted),
            offset: 0,
            limit: 100,
        })
        .await
        .expect("list accepted");
    assert_eq!(accepted.total, 1);
    assert_eq!(accepted.items[0].request_id, "req-new");
    assert_eq!(accepted.items[0].status, FriendRequestStatus::Accepted);

    // Received lists the inbox of the queried Bot.
    let received = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: Fixture::bot_principal("bot-c"),
            bot_uuid: "bot-c".to_string(),
            direction: FriendRequestDirection::Received,
            status: None,
            offset: 0,
            limit: 100,
        })
        .await
        .expect("list received");
    assert_eq!(received.total, 2);
    assert!(
        received
            .items
            .iter()
            .all(|request| request.from_bot_uuid == "bot-a"),
    );

    // offset/limit slice the sorted list.
    let paged = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            direction: FriendRequestDirection::Sent,
            status: None,
            offset: 1,
            limit: 2,
        })
        .await
        .expect("list paged");
    assert_eq!(paged.total, 4);
    let ids: Vec<&str> = paged.items.iter().map(|r| r.request_id.as_str()).collect();
    // Sorted order is [req-new, req-tie, req-mid, req-old]; offset 1 skips the
    // newest row.
    assert_eq!(ids, vec!["req-tie", "req-mid"]);

    // The facade forwarded the exact caller/direction/status, not defaults.
    let lists: Vec<(String, DomainFriendRequestDirection, Option<FriendRequestStatus>)> = fx
        .legacy_friend
        .calls()
        .into_iter()
        .filter_map(|call| match call {
            FriendCall::List {
                caller_actor_id,
                direction,
                status,
            } => Some((caller_actor_id, direction, status)),
            _ => None,
        })
        .collect();
    assert_eq!(
        lists,
        vec![
            (
                "bot-a".to_string(),
                DomainFriendRequestDirection::Sent,
                None,
            ),
            (
                "bot-a".to_string(),
                DomainFriendRequestDirection::Sent,
                Some(FriendRequestStatus::Accepted),
            ),
            (
                "bot-c".to_string(),
                DomainFriendRequestDirection::Received,
                None,
            ),
            (
                "bot-a".to_string(),
                DomainFriendRequestDirection::Sent,
                None,
            ),
        ],
    );
}

#[tokio::test]
async fn list_bot_friend_requests_propagates_facade_failure_as_internal() {
    // A legacy list failure must surface as `ApplicationError::Internal`
    // (HTTP 500), never as a silently-empty 200 page.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.legacy_friend.fail_list_with(ServiceError::InternalError(
        "simulated friend-request list failure".into(),
    ));

    let error = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            direction: FriendRequestDirection::Sent,
            status: None,
            offset: 0,
            limit: 10,
        })
        .await
        .expect_err("list must propagate the facade failure");
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
    fx.add_bot("bot-b").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
            request_auth: None,
        })
        .await
        .expect("create request");

    let accepted = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id.clone(),
            request_auth: None,
        })
        .await
        .expect("receiver accepts");

    assert_eq!(accepted.request_id, created.request_id);
    assert_eq!(accepted.from_bot_uuid, "bot-a");
    assert_eq!(accepted.to_bot_uuid, "bot-b");
    assert_eq!(accepted.status, FriendRequestStatus::Accepted);
    assert_eq!(accepted.created_at, created.created_at);
    assert!(
        accepted.updated_at > created.created_at,
        "the decision stamps updated_at",
    );

    // The decision is forwarded with the resolved receiver as the caller.
    assert!(
        fx.legacy_friend.calls().contains(&FriendCall::Accept {
            caller_actor_id: "bot-b".to_string(),
            request_id: created.request_id.clone(),
            request_to_bot: Some("bot-b".to_string()),
        }),
        "calls: {:?}",
        fx.legacy_friend.calls(),
    );
    assert_eq!(
        fx.legacy_friend.status_of(&created.request_id),
        Some(FriendRequestStatus::Accepted),
    );
}

#[tokio::test]
async fn accept_friend_request_non_receiver_forbidden() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.add_bot("bot-c").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
            request_auth: None,
        })
        .await
        .expect("create request");

    let error = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-c"),
            request_id: created.request_id.clone(),
            request_auth: None,
        })
        .await
        .expect_err("non-receiver forbidden");

    assert!(matches!(error, ApplicationError::Forbidden(_)));
    // The control leg runs before the mutation: nothing was decided.
    assert!(
        !fx.legacy_friend
            .calls()
            .iter()
            .any(|call| matches!(call, FriendCall::Accept { .. })),
    );
    assert_eq!(
        fx.legacy_friend.status_of(&created.request_id),
        Some(FriendRequestStatus::Pending),
    );
}

#[tokio::test]
async fn accept_friend_request_cannot_accept_rejected() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
            request_auth: None,
        })
        .await
        .expect("create request");

    fx.service
        .reject_friend_request(RejectFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id.clone(),
            request_auth: None,
        })
        .await
        .expect("reject first");

    let error = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id.clone(),
            request_auth: None,
        })
        .await
        .expect_err("cannot accept a rejected request");

    assert!(
        matches!(error, ApplicationError::Conflict { .. }),
        "expected Conflict, got {error:?}",
    );
    assert_code(error, "conflict");
    assert_eq!(
        fx.legacy_friend.status_of(&created.request_id),
        Some(FriendRequestStatus::Rejected),
    );
}

#[tokio::test]
async fn accept_friend_request_unknown_id_is_not_found() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-b").await;

    let error = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: "req-missing".to_string(),
            request_auth: None,
        })
        .await
        .expect_err("unknown request id");

    assert!(
        matches!(error, ApplicationError::NotFound { .. }),
        "expected NotFound, got {error:?}",
    );
    assert_code(error, "friend_request_not_found");
}

#[tokio::test]
async fn reject_friend_request_receiver_ok_and_sender_forbidden() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
            request_auth: None,
        })
        .await
        .expect("create request");

    // Sender may not reject (only the receiver may).
    let sender_err = fx
        .service
        .reject_friend_request(RejectFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            request_id: created.request_id.clone(),
            request_auth: None,
        })
        .await
        .expect_err("sender cannot reject");
    assert!(matches!(sender_err, ApplicationError::Forbidden(_)));

    let rejected = fx
        .service
        .reject_friend_request(RejectFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id.clone(),
            request_auth: None,
        })
        .await
        .expect("receiver rejects");
    assert_eq!(rejected.request_id, created.request_id);
    assert_eq!(rejected.status, FriendRequestStatus::Rejected);
    assert!(
        fx.legacy_friend.calls().contains(&FriendCall::Reject {
            caller_actor_id: "bot-b".to_string(),
            request_id: created.request_id.clone(),
            request_to_bot: Some("bot-b".to_string()),
        }),
        "calls: {:?}",
        fx.legacy_friend.calls(),
    );
}

#[tokio::test]
async fn friendship_page_shape_is_identity_projected() {
    // Sanity-check the V1 Friendship projection carries the same fields as the
    // domain edge (no rename surprises), exercising Page<Friendship>.
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.friends.add_friendship("bot-a", "bot-b").await.unwrap();
    fx.connect.add_friendship("bot-a", "bot-b");

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

    // DeleteResult is reachable and shaped as expected.
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

// The fake models a paged service; the facade must not page/filter a second time.
#[derive(Default)]
struct FriendListConnect {
    fail: bool,
    total_override: Option<u64>,
}

#[async_trait]
impl bcs_service_api::application::connect::ConnectService for FriendListConnect {
    async fn create_connect(&self, _: &str, _: &str, _: Option<String>, _: Option<RequestAuthHeaders>) -> ServiceResult<bcs_service_api::application::connect::ConnectResult> { unreachable!() }
    async fn approve(&self, _: &str, _: &str, _: Option<RequestAuthHeaders>) -> ServiceResult<Vec<u64>> { unreachable!() }
    async fn reject(&self, _: &str, _: &str, _: Option<String>) -> ServiceResult<()> { unreachable!() }
    async fn cancel(&self, _: &str) -> ServiceResult<()> { unreachable!() }
    async fn get_request(&self, _: &str) -> ServiceResult<bcs_domain::edge_permission::PermissionRequest> { unreachable!() }
    async fn revoke_friend(&self, _: &str, _: &str, _: Option<RequestAuthHeaders>) -> ServiceResult<Vec<u64>> { unreachable!() }
    async fn list_requests(&self, _: &str, _: bcs_service_api::application::connect::RequestDirection, _: Option<bcs_domain::edge_permission::RequestStatus>, _: u32, _: u32) -> ServiceResult<bcs_service_api::application::connect::RequestsPage> { unreachable!() }
    async fn list_friends(&self, _: &str) -> ServiceResult<Vec<bcs_domain::edge_permission::FriendListEntry>> {
        panic!("V1 must not fetch the full friend list")
    }
    async fn list_friends_paginated(
        &self,
        actor: &str,
        query: bcs_service_api::application::connect::FriendListQuery,
    ) -> ServiceResult<bcs_service_api::application::connect::FriendEntriesPage> {
        assert_eq!(actor, "bot-1");
        if self.fail {
            return Err(ServiceError::InternalError("friend read failed".into()));
        }
        let mut items: Vec<_> = [
            ("human_2002", bcs_domain::ActorKind::Human),
            ("bot-2", bcs_domain::ActorKind::Bot),
            ("human_1001", bcs_domain::ActorKind::Human),
        ].into_iter().map(|(id, kind)| bcs_domain::edge_permission::FriendListEntry {
            actor_id: id.into(), kind, name: None, summary: None, is_online: false,
        }).filter(|entry| query.target_type.is_none_or(|kind| entry.kind == kind)).collect();
        items.sort_by(|a, b| a.actor_id.cmp(&b.actor_id));
        let total = self.total_override.unwrap_or(items.len() as u64);
        let items = items.into_iter().skip(query.offset as usize).take(query.limit as usize).collect();
        Ok(bcs_service_api::application::connect::FriendEntriesPage { items, total })
    }
}

#[tokio::test]
async fn friend_connections_forward_filter_and_pagination() {
    use bcs_service_api::application::v1::{FriendConnectionService, FriendConnectionActor, FriendConnectionActorType as Kind, ListFriendConnections};
    let fixture = Fixture::new().await;
    let service = fixture.service.with_friend_connection_service(Arc::new(FriendListConnect::default()));
    let command = |target_type, page, page_size| ListFriendConnections {
        caller: Fixture::bot_only_caller("bot-1"),
        actor: FriendConnectionActor { actor_type: Kind::Bot, id: "bot-1".into() },
        target_type, page, page_size,
    };
    let first = service.list_friend_connections(command(Some(Kind::Human), 1, 1)).await.unwrap();
    assert_eq!((first.total, first.page, first.page_size), (2, 1, 1));
    assert_eq!(first.items.len(), 1);
    assert_eq!(first.items[0].actor, FriendConnectionActor { actor_type: Kind::Human, id: "1001".into() });
    let second = service.list_friend_connections(command(Some(Kind::Human), 2, 1)).await.unwrap();
    assert_eq!(second.total, 2);
    assert_eq!(second.items[0].actor.id, "2002");
    let bots = service.list_friend_connections(command(Some(Kind::Bot), 1, 20)).await.unwrap();
    assert_eq!(bots.total, 1);
    assert_eq!(bots.items[0].actor.actor_type, Kind::Bot);
    let all = service.list_friend_connections(command(None, 1, 20)).await.unwrap();
    assert_eq!(all.total, 3);
    assert_eq!(all.items.len(), 3);
    for page in [3, u32::MAX] {
        let empty = service.list_friend_connections(command(Some(Kind::Human), page, 100)).await.unwrap();
        assert_eq!(empty.total, 2);
        assert!(empty.items.is_empty());
    }
    for (page, size) in [(0, 20), (1, 0), (1, 101)] {
        assert_eq!(service.list_friend_connections(command(None, page, size)).await.unwrap_err().code(), "invalid_request");
    }
    let mut unauthorized = command(None, 1, 20);
    unauthorized.actor.id = "another-bot".into();
    assert_eq!(service.list_friend_connections(unauthorized).await.unwrap_err().code(), "forbidden");
}

#[tokio::test]
async fn friend_connections_propagate_read_errors_and_count_overflow() {
    use bcs_service_api::application::v1::{FriendConnectionService, FriendConnectionActor, FriendConnectionActorType, ListFriendConnections};
    for connect in [
        FriendListConnect { fail: true, total_override: None },
        FriendListConnect { fail: false, total_override: Some(u64::from(u32::MAX) + 1) },
    ] {
        let fixture = Fixture::new().await;
        let service = fixture.service.with_friend_connection_service(Arc::new(connect));
        let error = service.list_friend_connections(ListFriendConnections {
            caller: Fixture::bot_only_caller("bot-1"),
            actor: FriendConnectionActor { actor_type: FriendConnectionActorType::Bot, id: "bot-1".into() },
            target_type: None, page: 1, page_size: 20,
        }).await.unwrap_err();
        assert_eq!(error.code(), "internal_error");
    }
}

/// F2: the V1 friendship commands forward the caller's inbound principal to
/// the legacy `FriendService` facade (and from there to the connect use case),
/// so downstream owner notifications authenticate as the acting human. It is
/// transport context only — the wire contract is unchanged.
#[tokio::test]
async fn friendship_commands_forward_the_inbound_request_auth() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;

    let create_auth = RequestAuthHeaders {
        authorization: Some("Bearer user-token".to_string()),
        cookie: Some("session=abc".to_string()),
        forwarded_headers: vec![(
            "x-avernet-principal".to_string(),
            "principal-token".to_string(),
        )],
    };
    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
            request_auth: Some(create_auth.clone()),
        })
        .await
        .expect("create request");

    let accept_auth = RequestAuthHeaders {
        authorization: Some("Bearer owner-token".to_string()),
        cookie: None,
        forwarded_headers: Vec::new(),
    };
    fx.service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: created.request_id.clone(),
            request_auth: Some(accept_auth.clone()),
        })
        .await
        .expect("receiver accepts");

    let second = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
            request_auth: None,
        })
        .await
        .expect("second create request");
    let reject_auth = RequestAuthHeaders {
        authorization: Some("Bearer another-owner-token".to_string()),
        cookie: None,
        forwarded_headers: Vec::new(),
    };
    fx.service
        .reject_friend_request(RejectFriendRequest {
            caller: Fixture::bot_principal("bot-b"),
            request_id: second.request_id.clone(),
            request_auth: Some(reject_auth.clone()),
        })
        .await
        .expect("receiver rejects");

    assert_eq!(
        fx.legacy_friend.request_auths(),
        vec![
            ("create", Some(create_auth)),
            ("accept", Some(accept_auth)),
            ("create", None),
            ("reject", Some(reject_auth)),
        ],
        "the V1 commands must forward request_auth verbatim to the facade"
    );
}

/// F4 regression: deciding a Bot↔Bot reverse pending row (from/to flipped,
/// `created_by == from_id` false) is legal and the mutation already happened.
/// The V1 response must come from the connect store, not from the legacy
/// facade listing — that listing drops the reverse row and used to turn a
/// successful decision into a 500.
#[tokio::test]
async fn accept_reverse_row_returns_decided_request_without_facade_listing() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;

    // The reverse row of a Bot↔Bot connect: B→A, created by A.
    fx.legacy_friend.seed(DomainFriendRequest {
        id: "reverse-1".to_string(),
        from_bot: "bot-b".to_string(),
        to_bot: "bot-a".to_string(),
        status: FriendRequestStatus::Pending,
        created_at: 900,
        updated_at: 900,
    });
    fx.legacy_friend.hide_from_listing("reverse-1");

    let accepted = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            caller: Fixture::bot_principal("bot-a"),
            request_id: "reverse-1".to_string(),
            request_auth: None,
        })
        .await
        .expect("deciding the reverse row must not fail after the mutation");

    assert_eq!(accepted.request_id, "reverse-1");
    assert_eq!(accepted.from_bot_uuid, "bot-b");
    assert_eq!(accepted.to_bot_uuid, "bot-a");
    assert_eq!(accepted.status, FriendRequestStatus::Accepted);
    assert_eq!(accepted.created_at, 900);
    assert!(
        accepted.updated_at > accepted.created_at,
        "the decision stamps updated_at"
    );
}
