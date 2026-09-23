//! Strict OAuth session engine — per-request `verify` and login `install`.
//!
//! [`OAuthSessionEngine`] is the V1 auth vertical slice's session lifecycle
//! core. It wraps a [`OAuthSessionJwt`] signer and an
//! [`AuthSessionIdentityPort`] store, plus the session-issuer environment
//! partition (`env`) and the per-session idle-timeout (`idle_secs`). The
//! engine holds no `axum`, no HTTP, no header types — those belong to the
//! delivery adapter (`bcs-http` / source providers), never to the plugin.
//!
//! # Module split (`strict.rs` vs `session_lifecycle.rs`)
//!
//! The impl block for [`OAuthSessionEngine`] is split:
//! - `strict.rs` (this file): the constructor, [`Self::verify`], and
//!   [`Self::install`]. Per-request verification and the login install path
//!   share the same "fresh JWT + CAS install" reasoning but are read-mostly
//!   vs write-once-login.
//! - `session_lifecycle.rs`: [`Self::refresh`] and [`Self::revoke`]. Those
//!   form the rotating/terminating lifecycle and share the rotate-CAS and
//!   revoke-by-`session_id` helper reasoning.
//!
//! Both files `impl OAuthSessionEngine { ... }` vie the same struct; the
//! split keeps each file focused on one lifecycle phase (issue/verify vs
//! rotate/revoke).
//!
//! # Hash function
//!
//! The session binding uses [`bcs_jwt::token_hash`] — lowercase hex SHA-256
//! of the signed JWT string. This is the same function the legacy
//! `verify_oauth_session` path uses, and it is the canonical hash exposed
//! by `bcs-jwt` for JWT fingerprints. The engine, the identity-row store,
//! and the verifier all use the SAME hash function — never a local
//! re-implementation.
//!
//! # Error taxonomy (`SessionAuthError`)
//!
//! The engine's only error type is [`bcs_auth_api::SessionAuthError`]. It
//! is payload-free:
//! - [`SessionAuthError::Invalid`]: bad signature, expired token, unknown
//!   hash (revoked/never-installed/rotated-away), env mismatch, sid/
//!   revision mismatch between the JWT and the store snapshot. Visibility:
//!   401 unauthorized.
//! - [`SessionAuthError::Unavailable`]: the store returned
//!   [`SessionStoreError::Unavailable`]; the engine never wraps it.
//!   Visibility: 503 service unavailable.
//! - [`SessionAuthError::Internal`]: a numeric overflow
//!   (`u64 + 1` on a store-saturated revision) or a JWT sign failure.
//!   Visibility: 500 internal server error.
//! - [`SessionAuthError::Conflict`]: install CAS conflict (someone else
//!   installed/rotated/revoke-bumped past the revision the engine read
//!   before its own CAS). Visibility: 409 conflict. The engine never
//!   retries an install conflict implicitly.
//! - [`SessionAuthError::Forbidden`]: reserved for the HTTP origin /
//!   provider-allowlist layer. The engine never emits it.
//!
//! `verify` only ever returns `Invalid` or `Unavailable`. `install` may
//! return `Invalid`? No — it does not validate a token; it can return
//! `Unavailable`, `Internal` (overflow/sign), or `Conflict`. `refresh` may
//! return `Invalid`, `Unavailable`, or `Internal` (overflow/sign) — per
//! spec a CAS conflict on `refresh` surfaces as `Invalid` (401). `revoke`
//! may return `Invalid`, `Unavailable`, or `Internal` (sign failure); it
//! never returns `Conflict` (revoke is idempotent — the store's
//! [`SessionRevoke::NotCurrent`] is success-typed).

use std::sync::Arc;

use bcs_auth_api::{
    AuthSessionIdentityPort, InstallSession, IssuedSession, SessionAuthError, SessionScope,
    SessionVersion, SessionWrite,
};
use bcs_jwt::{OAuthSessionClaims, OAuthSessionJwt, token_hash};

/// Strict OAuth session engine.
///
/// Owns the JWT signer, the identity/session store port, the environment
/// partition, and the idle-timeout window. Stateless beyond those — every
/// method is a pure composition over `now` plus the explicit inputs.
pub struct OAuthSessionEngine {
    pub(crate) jwt: OAuthSessionJwt,
    pub(crate) identities: Arc<dyn AuthSessionIdentityPort>,
    pub(crate) env: String,
    /// Session lifetime in seconds. `exp = now + idle_secs` at issue; the
    /// idle timeout is counted from issue (NOT from last activity) and is
    /// refreshed on every [`Self::refresh`] call.
    pub(crate) idle_secs: u64,
}

impl OAuthSessionEngine {
    /// Construct the engine. The caller owns the JWT secret, the
    /// identity/session port, the env partition (e.g. `"local"` / `"prod"`),
    /// and the idle-lifetime in seconds.
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

    /// Per-request strict verification.
    ///
    /// Enforces: signature + claim completeness + `exp > now` (via
    /// [`OAuthSessionJwt::verify_at`]); `claims.env == self.env`; the
    /// presented JWT's hash resolves to a live store snapshot whose
    /// `session_id` and `revision` match the JWT's claims exactly. On
    /// success returns the store's [`bcs_auth_api::SessionSnapshot`]
    /// (carrying `display_name`/`avatar` from the identity row).
    ///
    /// `Forbidden`/`Conflict` are NOT returned by `verify`. The error surface
    /// is:
    /// - `Invalid`: bad/expired/mismatched JWT, hash not in the store, sid/
    ///   revision mismatch.
    /// - `Unavailable`: the underlying store query failed with
    ///   [`SessionStoreError::Unavailable`] (DB backend fault), as required
    ///   by spec §8.6 (HTTP 503).
    /// - `Internal`: the underlying store returned
    ///   [`SessionStoreError::CorruptRecord`] (a structurally valid row whose
    ///   fields cannot decode); per spec §8.6 it MUST NOT be collapsed into
    ///   `Unavailable`/503, so the engine surfaces it as `Internal` (500).
    pub async fn verify(
        &self,
        token: &str,
        now: u64,
    ) -> Result<bcs_auth_api::SessionSnapshot, SessionAuthError> {
        let claims = self.jwt.verify_at(token, now).map_err(|_| SessionAuthError::Invalid)?;
        if claims.env != self.env {
            return Err(SessionAuthError::Invalid);
        }
        let scope = claims_scope(&claims);
        let hash = token_hash(token);
        let snapshot = self
            .identities
            .get_session_by_hash(&scope, &hash, now)
            .await
            .map_err(map_store_read_error)?
            .ok_or(SessionAuthError::Invalid)?;
        if snapshot.version.session_id != claims.session_id
            || snapshot.version.revision != claims.revision
        {
            return Err(SessionAuthError::Invalid);
        }
        Ok(snapshot)
    }

    /// Install a NEW login session for `scope`.
    ///
    /// The caller has already ensured the identity exists (the
    /// [`AuthSessionIdentityPort::ensure_identity`] call is the
    /// multi-provider entry and belongs to the Task 10/11 facade, NOT the
    /// engine). This method:
    /// 1. Reads the current stored revision for `scope`.
    /// 2. Derives the next revision via `expected.checked_add(1)` —
    ///    an overflow returns [`SessionAuthError::Internal`].
    /// 3. Allocates a fresh `session_id` (uuid v4, OS CSPRNG).
    /// 4. Signs a JWT with `iat = now`, `exp = now + idle_secs`, and the
    ///    optional `display_name` baked into the `name` claim.
    /// 5. CAS-installs the binding against the read revision via
    ///    [`AuthSessionIdentityPort::install_login_session`]. A CAS
    ///    conflict is surfaced as [`SessionAuthError::Conflict`] — the
    ///    engine never retries implicitly. A storage fault is
    ///    [`SessionAuthError::Unavailable`].
    /// 6. Returns the signed token and its `expires_at` ONLY after the
    ///    store reports [`SessionWrite::Applied`].
    pub async fn install(
        &self,
        scope: SessionScope,
        display_name: Option<String>,
        now: u64,
    ) -> Result<IssuedSession, SessionAuthError> {
        let expected = self
            .identities
            .read_session_revision(&scope)
            .await
            .map_err(map_store_read_error)?;
        let next_revision = expected
            .checked_add(1)
            .ok_or(SessionAuthError::Internal)?;
        let session_id = allocate_session_id();
        let exp = now.saturating_add(self.idle_secs);
        let claims = OAuthSessionClaims {
            sub: scope.user_id.clone(),
            src: scope.provider.clone(),
            env: scope.env.clone(),
            session_id: session_id.clone(),
            revision: next_revision,
            iat: now,
            exp,
            name: display_name,
        };
        let token = self
            .jwt
            .sign(&claims)
            .map_err(|_| SessionAuthError::Internal)?;
        let hash = token_hash(&token);
        let install = InstallSession {
            scope: scope.clone(),
            expected_revision: expected,
            next: SessionVersion {
                session_id: session_id.clone(),
                revision: next_revision,
                token_hash: hash,
            },
            expires_at: exp,
        };
        let outcome = self
            .identities
            .install_login_session(install)
            .await
            .map_err(|_| SessionAuthError::Unavailable)?;
        match outcome {
            SessionWrite::Applied => Ok(IssuedSession { token, expires_at: exp }),
            SessionWrite::Conflict => Err(SessionAuthError::Conflict),
        }
    }
}

/// Reconstruct the [`SessionScope`] from a verified set of JWT claims.
pub(crate) fn claims_scope(claims: &OAuthSessionClaims) -> SessionScope {
    SessionScope {
        user_id: claims.sub.clone(),
        provider: claims.src.clone(),
        env: claims.env.clone(),
    }
}

/// Map a strict store READ error to the engine's auth-error taxonomy.
///
/// Spec §8.6: corrupt or unreadable persisted identity MUST NOT collapse
/// into `Unavailable`/503 — it propagates as `Internal` (500). A genuine
/// storage backend failure (DB down, timeout) remains `Unavailable` (503).
/// This helper is used by every read path — `verify`'s `get_session_by_hash`
/// and `install`'s `read_session_revision` — so the corrupt-record category
/// survives end-to-end.
pub(crate) fn map_store_read_error(
    error: bcs_auth_api::SessionStoreError,
) -> SessionAuthError {
    match error {
        bcs_auth_api::SessionStoreError::Unavailable => SessionAuthError::Unavailable,
        bcs_auth_api::SessionStoreError::CorruptRecord => SessionAuthError::Internal,
    }
}

/// Allocate a fresh `session_id` for a NEW install via the OS CSPRNG
/// ( uuid v4 → 12-char base62, matching
/// `bcs-user-identity::generate_user_id`). Refresh preserves the
/// existing `session_id` and does NOT call this.
fn allocate_session_id() -> String {
    const ALPHABET: &[u8] = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    let bytes = uuid::Uuid::new_v4().into_bytes();
    let mut s = String::with_capacity(12);
    for b in bytes.iter().take(12) {
        s.push(ALPHABET[(*b as usize) % ALPHABET.len()] as char);
    }
    s
}
