//! Integration tests for the V1 Invitation + Friendship facade.
//!
//! Exercises both `InvitationService` and `FriendshipService` impls against the
//! real in-memory store stack (GroupCore / BotCore / SessionManagementService
//! / FriendCore / FriendRequestCore / RelationCore), mirroring the sibling
//! `bcs-group-v1` / `bcs-session-v1` test harnesses.

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::Duration;

use bcs_bot::BotCore;
use bcs_domain::{
    invite_token_encode, InviteTargetType, InviteTokenPayload,
};
use bcs_friend::{FriendCore, FriendRequestCore};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::session::{CreateOrReactivateCommand, SessionManagementService};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, ApplicationError, AuthenticatedUser, CreateBotFriendRequest,
    CreateGroupInvitation, CreateSessionInvitation, DeleteResult, FriendshipService,
    FriendRequest, FriendRequestDirection, FriendRequestStatus, Friendship, InvitationService,
    InvitationState, InvitationTargetType, ListBotFriendRequests, ListBotFriendships, Page, Principal,
    RejectFriendRequest, DeleteBotFriendship,
};
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, FriendCoreService, Group, GroupCoreService,
    GroupStrategy, Participant, ParticipantRole, SessionKind,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;

use bcs_invitation_friendship_v1::{
    InvitationFriendshipServiceConfig, InvitationFriendshipServiceImpl,
};

const SECRET: &[u8] = b"test-invite-secret-32-bytes-long!!";

struct Fixture {
    service: InvitationFriendshipServiceImpl,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    friends: Arc<FriendCore>,
    friend_requests: Arc<FriendRequestCore>,
    sessions: Arc<SessionManagementServiceImpl>,
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
        let service = InvitationFriendshipServiceImpl::new(
            friends.clone(),
            friend_requests.clone(),
            groups.clone(),
            sessions.clone(),
            bots.clone(),
            relation.clone(),
            SECRET.to_vec(),
            InvitationFriendshipServiceConfig {
                relation_env: "dev".to_string(),
                default_ttl_seconds: 3600,
            },
        );
        Self {
            service,
            groups,
            bots,
            friends,
            friend_requests,
            sessions,
        }
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
    }

    async fn own_bot(&self, bot_uuid: &str, human_subject_id: &str) {
        self.bots
            .save_created_by(bot_uuid, human_subject_id, false)
            .await
            .expect("save created_by");
    }

    async fn store_group(&self, group_id: &str, driver: &str) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.originator = Some(driver.to_string());
        group.label = Some(group_id.to_string());
        group.group_strategy = GroupStrategy::Chat;
        self.groups.upsert(group).await.expect("store group");
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

    fn bot_principal(bot_uuid: &str) -> Principal {
        Principal::bot(bot_uuid, "dev", BTreeSet::new())
    }

    fn human_principal(subject_id: &str) -> Principal {
        Principal::human(
            AuthenticatedUser {
                id: subject_id.to_string(),
                username: subject_id.to_string(),
                display_name: None,
                full_name: None,
            },
            "dev",
            BTreeSet::new(),
        )
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
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-b"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect_err("non-manager is forbidden");

    assert!(matches!(error, ApplicationError::Forbidden(_)));
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
            principal: Fixture::bot_principal("bot-a"),
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
async fn accept_invitation_bot_self_joins_group() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.store_group("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            principal: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("create invitation");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            principal: Fixture::bot_principal("bot-b"),
            token: invitation.token,
            bot_uuid: None,
        })
        .await
        .expect("bot-b accepts");

    assert_eq!(result.target_type, InvitationTargetType::Group);
    assert_eq!(result.target_id, "grp-1");
    assert!(result.joined);
    assert_eq!(result.already_joined, Some(false));

    let group = fx.groups.get("grp-1").await.expect("group present");
    let joined_as = group
        .participants
        .iter()
        .find(|p| p.bot_uuid == "bot-b")
        .expect("bot-b is now a participant");
    assert_eq!(joined_as.role, ParticipantRole::Consultant);
}

#[tokio::test]
async fn accept_invitation_human_owned_bot_joins_group() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.own_bot("bot-b", "human-1").await;
    fx.store_group("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            principal: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("create invitation");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            principal: Fixture::human_principal("human-1"),
            token: invitation.token,
            bot_uuid: Some("bot-b".to_string()),
        })
        .await
        .expect("human-owned bot accepts");

    assert!(result.joined);
    assert!(fx.groups.get("grp-1").await.unwrap().participants.iter().any(|p| p.bot_uuid == "bot-b"));
}

#[tokio::test]
async fn accept_invitation_human_without_bot_uuid_rejected() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.store_group("grp-1", "bot-a").await;
    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            principal: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("create invitation");

    let error = fx
        .service
        .accept_invitation(AcceptInvitation {
            principal: Fixture::human_principal("human-1"),
            token: invitation.token,
            bot_uuid: None,
        })
        .await
        .expect_err("bot_uuid required for Human");

    assert_code(error, "invalid_request");
}

#[tokio::test]
async fn accept_invitation_expired_is_gone() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
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
            principal: Fixture::bot_principal("bot-b"),
            token: expired,
            bot_uuid: None,
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
    fx.add_bot("bot-b").await;
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
            principal: Fixture::bot_principal("bot-b"),
            token: legacy,
            bot_uuid: None,
        })
        .await
        .expect_err("legacy token rejected");

    assert_code(error, "invalid_request");
}

#[tokio::test]
async fn accept_invitation_already_member_is_idempotent() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_bot("bot-b").await;
    fx.store_group("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_group_invitation(CreateGroupInvitation {
            principal: Fixture::bot_principal("bot-a"),
            group_id: "grp-1".to_string(),
            expires_in_seconds: None,
        })
        .await
        .expect("create invitation");

    fx.service
        .accept_invitation(AcceptInvitation {
            principal: Fixture::bot_principal("bot-b"),
            token: invitation.token.clone(),
            bot_uuid: None,
        })
        .await
        .expect("first accept");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            principal: Fixture::bot_principal("bot-b"),
            token: invitation.token,
            bot_uuid: None,
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
    fx.add_bot("bot-b").await;
    fx.store_group("grp-1", "bot-a").await;
    let session_id = fx.create_session("grp-1", "bot-a").await;

    let invitation = fx
        .service
        .create_session_invitation(CreateSessionInvitation {
            principal: Fixture::bot_principal("bot-a"),
            session_id: session_id.clone(),
            expires_in_seconds: None,
        })
        .await
        .expect("create session invitation");

    let result = fx
        .service
        .accept_invitation(AcceptInvitation {
            principal: Fixture::bot_principal("bot-b"),
            token: invitation.token,
            bot_uuid: None,
        })
        .await
        .expect("bot-b joins session");

    assert_eq!(result.target_type, InvitationTargetType::Session);
    assert!(result.joined);
    let session = fx
        .sessions
        .get(&session_id)
        .await
        .expect("session lookup")
        .expect("session present");
    assert!(session.participants.iter().any(|p| p.bot_uuid == "bot-b"));
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
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-x"),
            bot_uuid: "bot-a".to_string(),
            offset: 0,
            limit: 10,
        })
        .await
        .expect_err("non-owner forbidden");

    assert!(matches!(error, ApplicationError::Forbidden(_)));
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
            principal: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            friend_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("remove friendship");
    assert!(first.deleted);

    let second = fx
        .service
        .delete_bot_friendship(DeleteBotFriendship {
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
        })
        .await
        .expect("first request");

    let error = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("a -> b");
    tokio::time::sleep(Duration::from_millis(25)).await;
    fx.service
        .create_bot_friend_request(CreateBotFriendRequest {
            principal: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-c".to_string(),
        })
        .await
        .expect("a -> c");

    // bot-a's sent: two requests, newest first (bot-c before bot-b).
    let sent = fx
        .service
        .list_bot_friend_requests(ListBotFriendRequests {
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-b"),
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
            principal: Fixture::bot_principal("bot-a"),
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
async fn accept_friend_request_receiver_ok() {
    let fx = Fixture::new().await;
    fx.add_bot("bot-a").await;
    fx.add_protected_bot("bot-b").await;

    let created = fx
        .service
        .create_bot_friend_request(CreateBotFriendRequest {
            principal: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("create request");

    let accepted = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            principal: Fixture::bot_principal("bot-b"),
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
            principal: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("create request");

    let error = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            principal: Fixture::bot_principal("bot-c"),
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
            principal: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("create request");

    fx.service
        .reject_friend_request(RejectFriendRequest {
            principal: Fixture::bot_principal("bot-b"),
            request_id: created.request_id.clone(),
        })
        .await
        .expect("reject first");

    let error = fx
        .service
        .accept_friend_request(AcceptFriendRequest {
            principal: Fixture::bot_principal("bot-b"),
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
            principal: Fixture::bot_principal("bot-a"),
            bot_uuid: "bot-a".to_string(),
            to_bot_uuid: "bot-b".to_string(),
        })
        .await
        .expect("create request");

    // Sender may not reject (only the receiver may).
    let sender_err = fx
        .service
        .reject_friend_request(RejectFriendRequest {
            principal: Fixture::bot_principal("bot-a"),
            request_id: created.request_id.clone(),
        })
        .await
        .expect_err("sender cannot reject");
    assert!(matches!(sender_err, ApplicationError::Forbidden(_)));

    let rejected = fx
        .service
        .reject_friend_request(RejectFriendRequest {
            principal: Fixture::bot_principal("bot-b"),
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
            principal: Fixture::bot_principal("bot-a"),
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
            principal: Fixture::bot_principal("bot-a"),
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
