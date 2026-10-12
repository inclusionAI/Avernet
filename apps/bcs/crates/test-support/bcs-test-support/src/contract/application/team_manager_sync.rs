//! `TeamManagerSyncService` shared conformance harness (plan Task 18 R25,
//! plan 公共命名: `team_manager_sync_service_contract_tests`).
//!
//! The driver constructs the PRODUCTION team-sync facade over a real
//! verifier (its own signing key) plus the shared
//! [`CountingBotAuthorityCore`]; this suite pins the store-independent
//! lane contract:
//!
//! - the credential lane is never anonymous: a blank credential is
//!   `Unauthenticated` and an unverifiable one is the fixed 403
//!   `invalid_manager_sync_source` code;
//! - structural command defects (blank identities, move without a target)
//!   are `invalid_request` 400s BEFORE the store (zero core writes);
//! - scope defects are the 403 `invalid_manager_sync_source` family with
//!   zero core writes (the store re-proves scopes inside its transaction).
//!
//! The deeper receipt/idempotency semantics live in the production crate's
//! own suites and the Task 7 store conformance.

use std::sync::Arc;

use bcs_service_api::application::v1::{
    ApplicationError, TeamManagerMemberRepair, TeamManagerOperationKind, TeamManagerSyncCommand,
    TeamManagerSyncService, VerifiedTeamService,
};
use bcs_service_api::port::TeamManagerCredentialVerifierPort;

use crate::CountingBotAuthorityCore;

/// Driver-supplied wiring bundle: the production facade, its verifier and
/// the counting authority core behind it, plus the facade's bound env.
pub struct TeamManagerSyncServiceHarness {
    /// The driver's `TeamManagerSyncServiceImpl` (or substitutable equivalent).
    pub service: Arc<dyn TeamManagerSyncService>,
    /// The verifier lane (the real bcs-jwt verifier over a dedicated key).
    pub verifier: Arc<dyn TeamManagerCredentialVerifierPort>,
    /// The counting authority core the driver constructed the facade with.
    pub core: Arc<CountingBotAuthorityCore>,
    /// The env the facade was bound to (scope checks compare to it).
    pub env: String,
}

/// One verified-service fixture with unrestricted scopes inside `env`.
fn unrestricted_service(env: &str) -> VerifiedTeamService {
    VerifiedTeamService {
        service_id: "conformance-sync-service".into(),
        env: env.to_string(),
        allowed_bots: None,
        allowed_teams: None,
        allowed_operations: None,
    }
}

fn sync_command(
    service: VerifiedTeamService,
    bot_id: &str,
    team_id: &str,
    operation: TeamManagerOperationKind,
) -> TeamManagerSyncCommand {
    TeamManagerSyncCommand {
        service,
        bot_id: bot_id.into(),
        team_id: team_id.into(),
        operation,
        manager_user_ids: vec!["conformance-user".into()],
        idempotency_key: "conformance-key".into(),
    }
}

/// The shared `TeamManagerSyncService` conformance suite.
pub async fn team_manager_sync_service_contract_tests(h: &TeamManagerSyncServiceHarness) {
    // Credential lane: never anonymous. A blank credential is
    // Unauthenticated (the transport's 401 lane), indistinguishable from a
    // missing header; an unverifiable credential is the fixed 403 code.
    let error = h
        .service
        .verify_service_credential("   ")
        .await
        .expect_err("blank credentials never verify");
    assert!(matches!(error, ApplicationError::Unauthenticated));

    let error = h
        .service
        .verify_service_credential("not-a-real-credential")
        .await
        .expect_err("garbage credentials never verify");
    assert!(
        matches!(
            &error,
            ApplicationError::ForbiddenCode { code, .. } if code == "invalid_manager_sync_source"
        ),
        "unverifiable credentials use the fixed deny code: {error:?}"
    );
    assert!(
        !matches!(&error, ApplicationError::Internal(message) if message.contains("not-a-real-credential")),
        "the raw credential never recurs in the error"
    );

    let writes_before = h.core.writes();

    // Structural defects are invalid_request 400s BEFORE any store write.
    let error = h
        .service
        .sync(sync_command(
            unrestricted_service(&h.env),
            "  ",
            "conformance-team",
            TeamManagerOperationKind::Sync,
        ))
        .await
        .expect_err("blank bot id is a structural defect");
    assert!(
        matches!(&error, ApplicationError::InvalidInput { code, .. } if code == "invalid_request"),
        "blank identities answer invalid_request: {error:?}"
    );

    let error = h
        .service
        .sync(sync_command(
            unrestricted_service(&h.env),
            "conformance-bot",
            "conformance-team",
            TeamManagerOperationKind::Move { new_team_id: " ".into() },
        ))
        .await
        .expect_err("blank move target is a structural defect");
    assert!(
        matches!(&error, ApplicationError::InvalidInput { code, .. } if code == "invalid_request"),
        "move-target defects answer invalid_request: {error:?}"
    );

    let error = h
        .service
        .sync(TeamManagerSyncCommand {
            service: unrestricted_service(&h.env),
            bot_id: "conformance-bot".into(),
            team_id: "conformance-team".into(),
            operation: TeamManagerOperationKind::Sync,
            manager_user_ids: vec!["conformance-user".into()],
            idempotency_key: " ".into(),
        })
        .await
        .expect_err("blank idempotency key is a structural defect");
    assert!(
        matches!(&error, ApplicationError::InvalidInput { code, .. } if code == "invalid_request"),
        "blank idempotency keys answer invalid_request: {error:?}"
    );

    // Scope defects: the verified service's env binding is re-proven
    // fail-closed before the store (a foreign-env credential is denied,
    // never "resolved" by the store's own env).
    let error = h
        .service
        .sync(sync_command(
            unrestricted_service("some-other-env"),
            "conformance-bot",
            "conformance-team",
            TeamManagerOperationKind::Sync,
        ))
        .await
        .expect_err("foreign-env credentials are denied");
    assert!(
        matches!(
            &error,
            ApplicationError::ForbiddenCode { code, .. } if code == "invalid_manager_sync_source"
        ),
        "scope failures use the fixed deny code: {error:?}"
    );
    assert!(
        !matches!(&error, ApplicationError::Internal(_)),
        "a scope denial is never a server error"
    );

    // Repairs share the same structural contract.
    let error = h
        .service
        .repair_add_team_member(TeamManagerMemberRepair {
            service: unrestricted_service(&h.env),
            bot_id: "conformance-bot".into(),
            team_id: "conformance-team".into(),
            user_id: " ".into(),
            idempotency_key: "conformance-repair-key".into(),
        })
        .await
        .expect_err("blank repair subject is a structural defect");
    assert!(
        matches!(&error, ApplicationError::InvalidInput { code, .. } if code == "invalid_request"),
        "repair defects answer invalid_request: {error:?}"
    );

    // None of the defect lanes above may have reached the store.
    assert_eq!(
        h.core.writes(),
        writes_before,
        "structural and scope defects never trigger a store write"
    );
}