use super::*;
use super::invitations::map_invite_token_error;

#[test]
fn invite_token_errors_map_to_stable_v1_codes() {
    assert_eq!(
        map_invite_token_error(InviteTokenError::Expired).code(),
        "invitation_expired"
    );
    assert_eq!(
        map_invite_token_error(InviteTokenError::InvalidEncoding).code(),
        "invalid_request"
    );
    assert_eq!(
        map_invite_token_error(InviteTokenError::InvalidSignature).code(),
        "invalid_request"
    );
    assert_eq!(
        map_invite_token_error(InviteTokenError::UnsupportedVersion).code(),
        "invalid_request"
    );
    assert_eq!(
        map_invite_token_error(InviteTokenError::MalformedPayload("bad".into())).code(),
        "invalid_request"
    );
}

#[test]
fn service_errors_map_to_stable_v1_codes() {
    assert_eq!(
        map_service_error(ServiceError::FriendRequestNotFound("r1".into())).code(),
        "friend_request_not_found"
    );
    assert_eq!(
        map_service_error(ServiceError::CannotAddSelf).code(),
        "cannot_add_self"
    );
    assert_eq!(
        map_service_error(ServiceError::PendingRequestExists {
            request_id: "r2".into(),
            from_bot: None,
            to_bot: None,
        })
        .code(),
        "friend_request_already_exists"
    );
    assert_eq!(
        map_service_error(ServiceError::CannotAcceptRejected).code(),
        "conflict"
    );
    assert_eq!(
        map_service_error(ServiceError::CannotRejectAccepted).code(),
        "conflict"
    );
    assert_eq!(
        map_service_error(ServiceError::Conflict("dup".into())).code(),
        "conflict"
    );
    assert_eq!(
        map_service_error(ServiceError::PrivateBotCannotCollaborate).code(),
        "forbidden"
    );
    assert_eq!(
        map_service_error(ServiceError::BotHidden("bot-1".into())).code(),
        "forbidden"
    );
}
