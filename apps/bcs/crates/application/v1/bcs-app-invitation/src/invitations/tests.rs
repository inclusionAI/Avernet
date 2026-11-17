//! Stable V1 error projections for failures returned by the legacy join facade.
use super::*;

#[test]
fn invite_join_failures_preserve_v1_error_categories_and_messages() {
    let forbidden = map_invite_use_case_error(InviteUseCaseError::Forbidden("provider scope denied".into()));
    assert_eq!(forbidden.code(), "forbidden");
    assert!(matches!(forbidden, ApplicationError::Forbidden(message) if message == "provider scope denied"));

    let missing = map_invite_use_case_error(InviteUseCaseError::NotFound("session-removed".into()));
    assert!(matches!(missing, ApplicationError::NotFound { code, message }
        if code == "invitation_not_found" && message == "Invitation target 'session-removed' was not found"));

    let conflict = map_invite_use_case_error(InviteUseCaseError::Conflict("group changed".into()));
    assert!(matches!(conflict, ApplicationError::Conflict { code, message }
        if code == "conflict" && message == "group changed"));

    let expired = map_invite_use_case_error(InviteUseCaseError::Expired);
    assert!(matches!(expired, ApplicationError::Gone { code, message }
        if code == "invitation_expired" && message == "invitation link has expired"));

    let invalid = map_invite_use_case_error(InviteUseCaseError::InvalidToken("wrong target type".into()));
    assert!(matches!(invalid, ApplicationError::InvalidInput { code, message }
        if code == "invalid_request" && message == "wrong target type"));
}

#[test]
fn invite_join_dependency_failures_remain_errors_not_success_or_nonmembership() {
    let login = map_invite_use_case_error(InviteUseCaseError::LoginRequired);
    assert!(matches!(login, ApplicationError::Internal(message)
        if message == "login required to accept invitation"));

    let storage = map_invite_use_case_error(InviteUseCaseError::Service(
        ServiceError::InternalError("join storage unavailable".into()),
    ));
    assert_eq!(storage.code(), "internal_error");
    assert!(matches!(storage, ApplicationError::Internal(message)
        if message.contains("join storage unavailable")));

    let denied = map_invite_use_case_error(InviteUseCaseError::Service(
        ServiceError::PrivateBotCannotCollaborate,
    ));
    assert_eq!(denied.code(), "forbidden");
}
