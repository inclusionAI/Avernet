//! Strict atomic auth-session repository contract checks (V1 API auth plugin
//! chain).
//!
//! These checks pin the `AuthSessionRepoPort` vocabulary at the source-contract
//! level: trait object safety, environment-scoped session equality, and the
//! closed write/revoke outcome and store-error categories. The behavioral
//! conformance harness lives in `bcs-test-support`
//! (`auth_session_repo_port_contract_tests`); concrete repository
//! implementations acquire a live harness caller from their
//! `conformance_*` drivers.

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRevoke, AuthSessionStoreError, AuthSessionVersion, AuthSessionWrite,
};

fn assert_auth_session_repo_port_is_object_safe(
    _via_module: Option<&dyn bcs_service_api::port::repo::auth_session::AuthSessionRepoPort>,
    _via_reexport: Option<&dyn bcs_service_api::port::repo::AuthSessionRepoPort>,
) {
}

#[test]
fn auth_session_repo_port_is_object_safe() {
    assert_auth_session_repo_port_is_object_safe(None, None);
}

#[test]
fn environment_is_part_of_session_scope() {
    use bcs_service_api::port::repo::auth_session::AuthSessionScope;
    let a = AuthSessionScope { user_id: "u".into(), provider: "github".into(), env: "dev".into() };
    let b = AuthSessionScope { env: "prod".into(), ..a.clone() };
    assert_ne!(a, b);
}

#[test]
fn auth_session_version_discriminates_by_revision_and_hash() {
    use bcs_service_api::port::repo::auth_session::AuthSessionScope;
    let scope = AuthSessionScope { user_id: "u".into(), provider: "github".into(), env: "dev".into() };
    let base = AuthSessionVersion { session_id: "s1".into(), revision: 1, token_hash: "h".into() };
    let bumped = AuthSessionVersion { revision: 2, ..base.clone() };
    let rehash = AuthSessionVersion { token_hash: "h2".into(), ..base.clone() };
    assert_ne!(base, bumped, "revision is part of the version identity");
    assert_ne!(base, rehash, "token hash is part of the version identity");
    assert_eq!(base, AuthSessionVersion { session_id: "s1".into(), revision: 1, token_hash: "h".into() });
    // Scope stays comparable so snapshot equality checks in the harness work.
    assert_eq!(scope.clone(), scope);
}

#[test]
fn auth_session_write_and_revoke_outcomes_are_closed_categories() {
    assert_ne!(AuthSessionWrite::Applied, AuthSessionWrite::Conflict);
    assert_ne!(AuthSessionRevoke::Revoked, AuthSessionRevoke::NotCurrent);
}

#[test]
fn auth_session_store_error_categories_carry_no_storage_details() {
    fn assert_is_plain_error_category<E: std::error::Error>(_: &E) {}
    let unavailable = AuthSessionStoreError::Unavailable;
    let corrupt = AuthSessionStoreError::CorruptRecord;
    assert_is_plain_error_category(&unavailable);
    assert_is_plain_error_category(&corrupt);
    // Fixed category text: no SQL fragments, endpoints, or credential
    // material ever enter the error display.
    assert_eq!(unavailable.to_string(), "identity store unavailable");
    assert_eq!(corrupt.to_string(), "invalid persisted identity");
}

#[test]
fn contract_harness_is_pinned_in_test_support() {
    // No live caller yet: concrete implementations arrive later. This pins
    // the harness entry point so conformance drivers can rely on it.
    use bcs_test_support::contract::repo::auth_session_repo_port_contract_tests;
    let _ = auth_session_repo_port_contract_tests;
}
