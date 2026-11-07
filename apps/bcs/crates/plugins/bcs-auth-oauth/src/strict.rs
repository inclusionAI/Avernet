//! Strict OAuth session engine — verified reads and shared invariants
//! (V1 API auth plugin chain, Task 8).
//!
//! [`OAuthSessionEngine`] is the single strict check-and-issue authority for
//! OAuth login sessions (plan §8.5): every read verifies the JWT signature
//! AND claim integrity, rejects tokens whose `env` differs from the
//! injected deployment environment BEFORE any storage access, and requires
//! the claims' `session_id`/`revision` to name the CURRENT stored version.
//! The environment is never taken from unverified claims to select storage:
//! the scope is always built from the INJECTED env.
//!
//! The issuing side (install/refresh/revoke) lives in
//! [`crate::session_lifecycle`]; it signs BEFORE the compare-and-swap and
//! hands a credential out only AFTER the CAS Applied.
//!
//! Redaction (plan §8.4): this crate never logs tokens or claims; errors are
//! the closed [`bcs_auth_api::session::SessionAuthError`] categories carrying
//! fixed stable text only.

use std::sync::Arc;

use bcs_auth_api::session::{
    AuthSessionIdentityPort, IssuedSession, SessionAuthError, SessionScope, SessionSnapshot,
    SessionStoreError,
};
use bcs_jwt::{token_hash, OAuthSessionClaims, OAuthSessionJwt};

/// The maximum representable session revision: SQL persists revisions in a
/// signed BIGINT column, so an engine-side bump past `i64::MAX` (or past
/// any u64) is an internal failure, never a wrapped or negative version.
pub(crate) const MAX_REVISION: u64 = i64::MAX as u64;

/// Strict OAuth session lifecycle engine over the Task 5 identity port.
///
/// Constructed with the pinned interface
/// `OAuthSessionEngine::new(jwt, identities, env, idle_secs)`. Holds no
/// `Debug` impl on purpose: the JWT service embeds the HMAC signing secret.
pub struct OAuthSessionEngine {
    pub(crate) jwt: OAuthSessionJwt,
    pub(crate) identities: Arc<dyn AuthSessionIdentityPort>,
    pub(crate) env: String,
    pub(crate) idle_secs: u64,
}

impl OAuthSessionEngine {
    /// Create the engine bound to one deployment environment and idle
    /// window. `env` is trusted configuration: presented tokens whose
    /// `claims.env` disagrees are refused before any storage access.
    pub fn new(
        jwt: OAuthSessionJwt,
        identities: Arc<dyn AuthSessionIdentityPort>,
        env: String,
        idle_secs: u64,
    ) -> Self {
        Self {
            jwt,
            identities,
            env,
            idle_secs,
        }
    }

    /// Verify a presented session JWT and return the trusted snapshot of
    /// its CURRENT version.
    ///
    /// Order (binding, plan §8.5):
    /// 1. strict JWT verification at the injected instant — signature,
    ///    claim integrity (legacy sid-less tokens fail here), and `exp`;
    /// 2. reject `claims.env` != the INJECTED env — before storage, so a
    ///    token's own environment claim can never select other storage;
    /// 3. resolve the CURRENT version by the token's SHA-256 fingerprint
    ///    under the trusted scope — unbound superseded/revoked/expired
    ///    tokens are simply invalid;
    /// 4. require the claims to name exactly the stored version's
    ///    `session_id` and `revision`.
    pub async fn verify(&self, token: &str, now: u64) -> Result<SessionSnapshot, SessionAuthError> {
        let claims = self
            .jwt
            .verify_at(token, now)
            .map_err(|_| SessionAuthError::Invalid)?;
        self.ensure_injected_env(&claims)?;
        let scope = self.claims_scope(&claims);
        let snapshot = match self
            .identities
            .get_session_by_hash(&scope, &token_hash(token), now)
            .await
        {
            Ok(Some(snapshot)) => snapshot,
            Ok(None) => return Err(SessionAuthError::Invalid),
            Err(error) => return Err(map_store_error(error)),
        };
        if snapshot.version.session_id != claims.session_id
            || snapshot.version.revision != claims.revision
        {
            // The trusted record outvotes the token: a foreign or stale
            // session instance is invalid here.
            return Err(SessionAuthError::Invalid);
        }
        Ok(snapshot)
    }

    /// Trusted scope for ALREADY-VERIFIED claims: the internal subject id,
    /// the recorded source provider, and the INJECTED environment — never
    /// the claims' own `env`.
    pub(crate) fn claims_scope(&self, claims: &OAuthSessionClaims) -> SessionScope {
        SessionScope {
            user_id: claims.sub.clone(),
            provider: claims.src.clone(),
            env: self.env.clone(),
        }
    }

    /// Environment gate: fail fast (Forbidden) before any storage access.
    pub(crate) fn ensure_injected_env(&self, claims: &OAuthSessionClaims) -> Result<(), SessionAuthError> {
        if claims.env == self.env {
            Ok(())
        } else {
            Err(SessionAuthError::Forbidden)
        }
    }
}

/// Map the closed store-error contract onto the engine's closed categories:
/// a dependency outage is `Unavailable` (503-side), persisted corruption is
/// an `Internal` failure — neither is ever folded into a denial.
pub(crate) fn map_store_error(error: SessionStoreError) -> SessionAuthError {
    match error {
        SessionStoreError::Unavailable => SessionAuthError::Unavailable,
        SessionStoreError::CorruptRecord => SessionAuthError::Internal,
    }
}

/// Advance a revision by exactly `checked_add(1)` within the SQL signed
/// BIGINT bound. Any overflow is an `Internal` failure BEFORE a CAS is
/// attempted — a wrapped or negative revision must never reach the store.
pub(crate) fn next_revision(current: u64) -> Result<u64, SessionAuthError> {
    let Some(next) = current.checked_add(1) else {
        return Err(SessionAuthError::Internal);
    };
    if next > MAX_REVISION {
        return Err(SessionAuthError::Internal);
    }
    Ok(next)
}

/// Extend the trusted window: `now + idle_secs` with a checked add — an
/// overflowing expiry is an `Internal` failure before any write.
pub(crate) fn window_expiry(now: u64, idle_secs: u64) -> Result<u64, SessionAuthError> {
    now.checked_add(idle_secs).ok_or(SessionAuthError::Internal)
}

/// Random NEW session id from the OS CSPRNG (uuid v4, dash-less lowercase
/// hex). 32 chars — fits the storage `VARCHAR(64)` with headroom
/// (forward constraint from the storage conformance review).
pub(crate) fn new_session_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// Sign session claims. A signing failure is an `Internal` engine failure;
/// the error never carries claim or key material.
pub(crate) fn sign_claims(
    jwt: &OAuthSessionJwt,
    claims: &OAuthSessionClaims,
) -> Result<String, SessionAuthError> {
    jwt.sign(claims).map_err(|_| SessionAuthError::Internal)
}

/// Pending issuance state shared by install/refresh (kept private to the
/// issuing path): the signed token plus the version it names.
pub(crate) struct SignedVersion {
    pub token: String,
    pub hash: String,
    pub session_id: String,
    pub revision: u64,
    pub expires_at: u64,
}

/// Convenience alias used by the issuing path; the credential is handed to
/// the caller only after the CAS Applied (see [`crate::session_lifecycle`]).
pub(crate) fn issued(session: SignedVersion) -> IssuedSession {
    IssuedSession {
        token: session.token,
        expires_at: session.expires_at,
    }
}
