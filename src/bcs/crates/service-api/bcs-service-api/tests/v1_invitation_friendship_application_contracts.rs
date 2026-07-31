use std::collections::BTreeSet;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, ApplicationError, AuthenticatedUser, CreateBotFriendRequest,
    CreateGroupInvitation, CreateSessionInvitation, DeleteResult, Friendship, FriendshipService,
    FriendRequest, FriendRequestDirection, FriendRequestStatus, Invitation, InvitationAcceptResult,
    InvitationService, InvitationState, InvitationTargetType, ListBotFriendRequests, ListBotFriendships,
    Page, Principal, RejectFriendRequest, DeleteBotFriendship,
};

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn create_session_invitation(
        &self,
        _command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn accept_invitation(
        &self,
        _command: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }
}

struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_bot_friendships(
        &self,
        _command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Ok(Page::empty(0, 20))
    }

    async fn delete_bot_friendship(
        &self,
        _command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Ok(DeleteResult { deleted: false })
    }

    async fn create_bot_friend_request(
        &self,
        _command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn list_bot_friend_requests(
        &self,
        _command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Ok(Page::empty(0, 20))
    }

    async fn accept_friend_request(
        &self,
        _command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }

    async fn reject_friend_request(
        &self,
        _command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not implemented"))
    }
}

#[test]
fn invitation_service_is_object_safe() {
    fn accepts_service(_: &dyn InvitationService) {}
    accepts_service(&NoopInvitationService);
}

#[test]
fn friendship_service_is_object_safe() {
    fn accepts_service(_: &dyn FriendshipService) {}
    accepts_service(&NoopFriendshipService);
}

#[test]
fn invitation_commands_carry_principal_and_no_raw_credentials() {
    let principal = Principal::bot("bot-1", "tenant-a", BTreeSet::new());
    let create_group = CreateGroupInvitation {
        principal: principal.clone(),
        group_id: "g1".into(),
        expires_in_seconds: Some(3600),
    };
    let create_session = CreateSessionInvitation {
        principal: principal.clone(),
        session_id: "s1".into(),
        expires_in_seconds: None,
    };
    let accept = AcceptInvitation {
        principal: principal.clone(),
        token: "tok-1".into(),
        bot_uuid: Some("bot-2".into()),
    };
    for cmd in [
        &create_group.principal as &Principal,
        &create_session.principal,
        &accept.principal,
    ] {
        let s = format!("{cmd:?}");
        assert!(!s.contains("Cookie") && !s.contains("Bearer") && !s.contains("sender"));
    }
    assert_eq!(create_group.group_id, "g1");
    assert_eq!(create_group.expires_in_seconds, Some(3600));
    assert_eq!(create_session.session_id, "s1");
    assert_eq!(accept.token, "tok-1");
    assert_eq!(accept.bot_uuid.as_deref(), Some("bot-2"));
}

#[test]
fn friendship_commands_carry_principal_and_no_raw_credentials() {
    let principal = Principal::bot("bot-1", "tenant-a", BTreeSet::new());
    let list_bot_friendships = ListBotFriendships {
        principal: principal.clone(),
        bot_uuid: "bot-1".into(),
        offset: 0,
        limit: 25,
    };
    let remove = DeleteBotFriendship {
        principal: principal.clone(),
        bot_uuid: "bot-1".into(),
        friend_bot_uuid: "bot-2".into(),
    };
    let create_req = CreateBotFriendRequest {
        principal: principal.clone(),
        bot_uuid: "bot-1".into(),
        to_bot_uuid: "bot-2".into(),
    };
    let list_reqs = ListBotFriendRequests {
        principal: principal.clone(),
        bot_uuid: "bot-1".into(),
        direction: FriendRequestDirection::Sent,
        status: Some(FriendRequestStatus::Pending),
        offset: 5,
        limit: 10,
    };
    let accept_req = AcceptFriendRequest {
        principal: principal.clone(),
        request_id: "req-1".into(),
    };
    let reject_req = RejectFriendRequest {
        principal,
        request_id: "req-1".into(),
    };
    for cmd in [
        &list_bot_friendships.principal as &Principal,
        &remove.principal,
        &create_req.principal,
        &list_reqs.principal,
        &accept_req.principal,
        &reject_req.principal,
    ] {
        let s = format!("{cmd:?}");
        assert!(!s.contains("Cookie") && !s.contains("Bearer") && !s.contains("sender"));
    }
    assert_eq!(list_bot_friendships.bot_uuid, "bot-1");
    assert_eq!(remove.friend_bot_uuid, "bot-2");
    assert_eq!(create_req.to_bot_uuid, "bot-2");
    assert_eq!(list_reqs.direction, FriendRequestDirection::Sent);
    assert_eq!(list_reqs.status, Some(FriendRequestStatus::Pending));
    assert_eq!(accept_req.request_id, "req-1");
}

#[test]
fn invitation_target_type_serializes_snake_case() {
    let group = serde_json::to_value(InvitationTargetType::Group).expect("serialize Group");
    let session = serde_json::to_value(InvitationTargetType::Session).expect("serialize Session");
    assert_eq!(group, "group");
    assert_eq!(session, "session");
    for raw in ["\"group\"", "\"session\""] {
        let parsed: InvitationTargetType =
            serde_json::from_str(raw).expect("deserialize InvitationTargetType");
        assert_eq!(serde_json::to_string(&parsed).unwrap(), raw);
    }
}

#[test]
fn invitation_state_serializes_snake_case() {
    let pending = serde_json::to_value(InvitationState::Pending).expect("serialize Pending");
    let accepted = serde_json::to_value(InvitationState::Accepted).expect("serialize Accepted");
    let expired = serde_json::to_value(InvitationState::Expired).expect("serialize Expired");
    assert_eq!(pending, "pending");
    assert_eq!(accepted, "accepted");
    assert_eq!(expired, "expired");
    for raw in ["\"pending\"", "\"accepted\"", "\"expired\""] {
        let parsed: InvitationState =
            serde_json::from_str(raw).expect("deserialize InvitationState");
        assert_eq!(serde_json::to_string(&parsed).unwrap(), raw);
    }
}

#[test]
fn friend_request_direction_is_narrower_than_domain() {
    let sent = serde_json::to_value(FriendRequestDirection::Sent).expect("serialize Sent");
    let received =
        serde_json::to_value(FriendRequestDirection::Received).expect("serialize Received");
    assert_eq!(sent, "sent");
    assert_eq!(received, "received");
    for raw in ["\"sent\"", "\"received\""] {
        let parsed: FriendRequestDirection =
            serde_json::from_str(raw).expect("deserialize FriendRequestDirection");
        assert_eq!(serde_json::to_string(&parsed).unwrap(), raw);
    }
    // V1 drops the domain `All` variant.
    assert!(serde_json::from_str::<FriendRequestDirection>("\"all\"").is_err());
}

#[test]
fn invitation_serializes_with_v1_field_names() {
    let invitation = Invitation {
        token: "tok-1".into(),
        target_type: InvitationTargetType::Group,
        target_id: "g1".into(),
        state: InvitationState::Pending,
        expires_at: Some(3600),
        created_at: 42,
    };
    let json = serde_json::to_value(&invitation).expect("serialize Invitation");
    assert_eq!(json["token"], "tok-1");
    assert_eq!(json["target_type"], "group");
    assert_eq!(json["target_id"], "g1");
    assert_eq!(json["state"], "pending");
    assert_eq!(json["expires_at"], 3600);
    assert_eq!(json["created_at"], 42);

    let without_expiry = Invitation {
        expires_at: None,
        ..invitation
    };
    let json = serde_json::to_value(&without_expiry).expect("serialize Invitation without expiry");
    assert!(json.get("expires_at").is_none());
}

#[test]
fn invitation_accept_result_serializes_with_v1_field_names() {
    let result = InvitationAcceptResult {
        target_type: InvitationTargetType::Session,
        target_id: "s1".into(),
        joined: true,
        already_joined: Some(false),
    };
    let json = serde_json::to_value(&result).expect("serialize InvitationAcceptResult");
    assert_eq!(json["target_type"], "session");
    assert_eq!(json["target_id"], "s1");
    assert_eq!(json["joined"], true);
    assert_eq!(json["already_joined"], false);

    let first_accept = InvitationAcceptResult {
        already_joined: None,
        ..result
    };
    let json = serde_json::to_value(&first_accept).expect("serialize first accept");
    assert!(json.get("already_joined").is_none());
}

#[test]
fn friendship_serializes_with_v1_field_names() {
    let friendship = Friendship {
        bot_uuid: "bot-1".into(),
        friend_bot_uuid: "bot-2".into(),
        created_at: 7,
    };
    let json = serde_json::to_value(&friendship).expect("serialize Friendship");
    assert_eq!(json["bot_uuid"], "bot-1");
    assert_eq!(json["friend_bot_uuid"], "bot-2");
    assert_eq!(json["created_at"], 7);
}

#[test]
fn friend_request_serializes_with_v1_field_names() {
    let request = FriendRequest {
        request_id: "req-1".into(),
        from_bot_uuid: "bot-1".into(),
        to_bot_uuid: "bot-2".into(),
        status: FriendRequestStatus::Pending,
        message: Some("hi".into()),
        created_at: 10,
        updated_at: 11,
    };
    let json = serde_json::to_value(&request).expect("serialize FriendRequest");
    assert_eq!(json["request_id"], "req-1");
    assert_eq!(json["from_bot_uuid"], "bot-1");
    assert_eq!(json["to_bot_uuid"], "bot-2");
    assert_eq!(json["status"], "pending");
    assert_eq!(json["message"], "hi");
    assert_eq!(json["created_at"], 10);
    assert_eq!(json["updated_at"], 11);

    let without_message = FriendRequest {
        message: None,
        ..request
    };
    let json = serde_json::to_value(&without_message).expect("serialize without message");
    assert!(json.get("message").is_none());
}

#[test]
fn human_principal_can_be_carried_in_invitation_command() {
    let human = Principal::human(
        AuthenticatedUser {
            id: "staff-1".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        },
        "tenant-a",
        BTreeSet::new(),
    );
    let command = AcceptInvitation {
        principal: human,
        token: "tok-1".into(),
        bot_uuid: None,
    };
    assert_eq!(command.principal.actor_id(), "human_staff-1");
    assert_eq!(command.token, "tok-1");
    assert!(command.bot_uuid.is_none());
}
