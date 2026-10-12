//! `TeamManagerCredentialVerifierPort` — the trusted team-manager service
//! credential verification port (plan Task 13, spec §6.1/§12.1).
//!
//! The trusted platform's team-manager synchronization lane is NOT
//! anonymous: every write carries a service credential whose SIGNATURE,
//! purpose, and claim shape are verified before any business command is
//! built. Per the Gate 0 record (spec §1.3), the production driver
//! reuses the pure HS256 signature boundary in `bcs-jwt` with a DEDICATED
//! purpose (`team_manager_sync` — register/group-session tokens never
//! verify here), and returns only the
//! [`crate::types::VerifiedTeamManagerService`] (service id, env, and
//! the verified Bot/team/operation allow scopes) that business commands
//! carry; authorization NEVER keys off a raw service id string from a
//! request body.
//!
//! The credential value itself never enters audit rows, business logs, or
//! persisted commands — implementations must not derive Debug output
//! that exposes the signing key, and callers drop the credential as soon
//! as [`Self::verify`] returns.
//!
//! Fail-closed rules for implementations:
//! - a missing/blank credential is the transport's 401 (never a verify
//!   call);
//! - an unverifiable signature, an expired credential, a foreign
//!   purpose, or an invalid claim shape is a FORBIDDEN service failure
//!   (the 403 `invalid_manager_sync_source` fixed code), never a silent
//!   `None`;
//! - verification is pure (no I/O), so the port is synchronous.

use crate::ServiceResult;
use crate::types::team_manager_sync::VerifiedTeamManagerService;

/// Verifier of trusted team-manager service credentials (spec §6.1).
pub trait TeamManagerCredentialVerifierPort: Send + Sync {
    /// Verify one credential and yield the verified service identity +
    /// scopes. The returned value is the ONLY accepted actor shape for
    /// the team-sync lane; nothing about a request body may influence it.
    fn verify(&self, credential: &str) -> ServiceResult<VerifiedTeamManagerService>;
}

/// Fail-closed default (spec §13.1: Noop denies): an assembly that forgot
/// to wire the real verifier never falls back to accepting credentials.
pub struct NoopTeamManagerCredentialVerifierPort;

impl TeamManagerCredentialVerifierPort for NoopTeamManagerCredentialVerifierPort {
    fn verify(&self, _credential: &str) -> ServiceResult<VerifiedTeamManagerService> {
        Err(crate::ServiceError::Authority(
            crate::types::error::AuthorityError::Forbidden(
                "team-manager credential verification is not configured; \
                 every team-manager sync request is denied"
                    .to_string(),
            ),
        ))
    }
}