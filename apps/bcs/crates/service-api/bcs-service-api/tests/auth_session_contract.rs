//! Strict auth-session repository contract boundary tests.
//!
//! These live in `bcs-service-api` itself (NOT `bcs-test-support`) because they
//! verify only the type-level contract: trait object safety, scope equality,
//! and that the success-typed write/revoke enums are distinct from the
//! `AuthSessionStoreError` variants. The operation-sequence conformance
//! harness that exercises a concrete implementation lives in
//! `bcs-test-support::contract::repo::auth_session` and is wired by store
//! crates starting with Task 3.

use bcs_service_api::port::repo::auth_session::{
    AuthSessionRepoPort, AuthSessionRevoke, AuthSessionStoreError, AuthSessionWrite,
};

/// A `&dyn AuthSessionRepoPort` must be usable without specifying a concrete
/// implementation — the trait must be object-safe and live under
/// `port::repo::auth_session`.
fn _assert_trait_object_safe(_repo: Option<&dyn AuthSessionRepoPort>) {}

#[test]
fn environment_is_part_of_session_scope() {
    use bcs_service_api::port::repo::auth_session::AuthSessionScope;
    let a = AuthSessionScope { user_id: "u".into(), provider: "github".into(), env: "dev".into() };
    let b = AuthSessionScope { env: "prod".into(), ..a.clone() };
    assert_ne!(a, b);
}

#[test]
fn error_categories_are_distinct_from_write_and_revoke_outcomes() {
    // The two store-error variants must not be interchangeable with the
    // success-typed `AuthSessionWrite` / `AuthSessionRevoke` enums: the write
    // path returns `Applied` / `Conflict` on `Ok`, never an error, and a
    // missing/unknown-hash revoke returns `NotCurrent` on `Ok`, never an
    // error. Only storage failure (`Unavailable`) and invalid persisted
    // identity (`CorruptRecord`) are errors.
    let unavailable = AuthSessionStoreError::Unavailable;
    let corrupt = AuthSessionStoreError::CorruptRecord;
    assert_ne!(unavailable.to_string(), corrupt.to_string());
    assert_eq!(unavailable.to_string(), "identity store unavailable");
    assert_eq!(corrupt.to_string(), "invalid persisted identity");

    // `Conflict` (write CAS failure) is a success-typed enum, not an error:
    // it is observably distinct from both error variants.
    let conflict = AuthSessionWrite::Conflict;
    let applied = AuthSessionWrite::Applied;
    assert_ne!(conflict, applied);
    // Ensure the write outcome variants are not falsely equated with errors:
    // the error enum has exactly two units and the only cross-enum equality
    // observable at the type level is that they are different types entirely.
    let _write_outcomes: [&AuthSessionWrite; 2] = [&applied, &conflict];

    let revoked = AuthSessionRevoke::Revoked;
    let not_current = AuthSessionRevoke::NotCurrent;
    assert_ne!(revoked, not_current);
    let _revoke_outcomes: [&AuthSessionRevoke; 2] = [&revoked, &not_current];
}
