use bcs_service_api::application::v1::{
    AuthorizeGroupSessionConnection, AuthorizedGroupSessionConnection,
    GroupSessionConnectionBinding, GroupSessionConnectionError, GroupSessionConnectionService,
    IssueGroupSessionConnectionToken, IssuedGroupSessionConnectionToken,
    VerifyGroupSessionConnectionToken, GROUP_SESSION_WS_TOKEN_TTL_SECONDS,
};
use bcs_service_api::port::{
    GroupSessionTokenClaims, GroupSessionTokenError, GroupSessionTokenPort,
    GroupSessionTokenScope, IssuedGroupSessionToken,
};

#[test]
fn connection_binding_keeps_one_exact_session_scope() {
    let binding = GroupSessionConnectionBinding {
        tenant: Some("tenant-a".into()),
        user_id: "user-a".into(),
        group_id: "group-a".into(),
        session_id: "session-a".into(),
    };

    assert_eq!(binding.tenant.as_deref(), Some("tenant-a"));
    assert_eq!(binding.user_id, "user-a");
    assert_eq!(binding.group_id, "group-a");
    assert_eq!(binding.session_id, "session-a");
    assert_eq!(GROUP_SESSION_WS_TOKEN_TTL_SECONDS, 300);
}

#[test]
fn connection_contracts_remain_transport_neutral_and_object_safe() {
    fn accepts_connection_service(_: &dyn GroupSessionConnectionService) {}
    fn accepts_token_port(_: &dyn GroupSessionTokenPort) {}

    let _ = accepts_connection_service;
    let _ = accepts_token_port;
    let _ = size_of::<IssueGroupSessionConnectionToken>();
    let _ = size_of::<VerifyGroupSessionConnectionToken>();
    let _ = size_of::<IssuedGroupSessionConnectionToken>();
    let _ = size_of::<AuthorizeGroupSessionConnection>();
    let _ = size_of::<AuthorizedGroupSessionConnection>();
    let _ = size_of::<GroupSessionConnectionError>();
    let _ = size_of::<GroupSessionTokenScope>();
    let _ = size_of::<GroupSessionTokenClaims>();
    let _ = size_of::<IssuedGroupSessionToken>();
    let _ = size_of::<GroupSessionTokenError>();
}
use std::mem::size_of;
