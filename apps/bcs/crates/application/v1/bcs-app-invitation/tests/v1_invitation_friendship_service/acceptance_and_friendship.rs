use super::*;

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

// The fake models a paged service; the facade must not page/filter a second time.
#[derive(Default)]
struct FriendListConnect {
    fail: bool,
    total_override: Option<u64>,
}

#[async_trait]
impl bcs_service_api::application::connect::ConnectService for FriendListConnect {
    async fn create_connect(&self, _: &str, _: &str, _: Option<String>, _: Option<bcs_service_api::RequestAuthHeaders>) -> ServiceResult<bcs_service_api::application::connect::ConnectResult> { unreachable!() }
    async fn approve(&self, _: &str, _: &str, _: Option<bcs_service_api::RequestAuthHeaders>) -> ServiceResult<Vec<u64>> { unreachable!() }
    async fn reject(&self, _: &str, _: &str, _: Option<String>) -> ServiceResult<()> { unreachable!() }
    async fn cancel(&self, _: &str) -> ServiceResult<()> { unreachable!() }
    async fn get_request(&self, _: &str) -> ServiceResult<bcs_domain::edge_permission::PermissionRequest> { unreachable!() }
    async fn revoke_friend(&self, _: &str, _: &str, _: Option<bcs_service_api::RequestAuthHeaders>) -> ServiceResult<Vec<u64>> { unreachable!() }
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
