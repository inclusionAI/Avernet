//! Strict OAuth session engine — `refresh` and `revoke` lifecycle.
//!
//! Companion to [`crate::strict`]: same [`OAuthSessionEngine`] struct, the
//! rotation/revocation phase of the lifecycle. See the [`crate::strict`]
//! module docs for the file split and the engine-level error taxonomy.
//!
//! # `refresh` — minimal algorithm (per plan)
//!
//! ```text
//! claims = jwt.verify_at(token, now)            // signature + exp
//! reject claims.env != engine.env              // → Invalid
//! snapshot = identities.get_session_by_hash(scope, sha256(token), now)
//!                                               // None → Invalid; Unavailable → Unavailable
//! require claims.session_id == snapshot.version.session_id
//!          && claims.revision == snapshot.version.revision   // mismatch → Invalid
//! next_revision = snapshot.version.revision.checked_add(1)  // overflow → Internal
//! sign next JWT: SAME session_id, next_revision, same env, iat = now,
//!                exp = now + idle_secs
//! CAS rotate_session(expected: snapshot.version, next: SessionVersion{sid, next_revision, sha256(next_token)}, expires_at, now)
//! Conflict → Invalid;                          // per spec: refresh conflict → 401 Invalid
//! storage failure → Unavailable
//! return IssuedSession{token: next_token, expires_at} only after Applied
//! ```
//!
//! `refresh` does NOT touch [`AuthSessionIdentityPort::ensure_identity`];
//! identity creation belongs to the caller (engine install does NOT
//! either). It does NOT respect a "sliding renewal" cutoff — the caller
//! decides whether refresh is warranted; the engine just performs the
//! rotation if the JWT is still cryptographically valid and bound.
//!
//! # `revoke` — minimal algorithm (per plan)
//!
//! ```text
//! claims = jwt.verify_for_revoke(token)        // signature + claim completeness; exp SKIPPED
//! reject claims.env != engine.env              // → Invalid
//! revoke_session(scope, claims.session_id)     // by session_id, not by hash
//!                                               // Revoked or NotCurrent both OK (idempotent)
//! storage failure → Unavailable
//! ```
//!
//! `verify_for_revoke` skips ONLY the `exp` check (a user may log out after
//! `exp`); signature + claim completeness are still enforced (rejecting a
//! tampered signature or a token missing `session_id`/`revision`/`env` —
//! those are load-bearing for the revoke-by-`session_id` lookup).
//!
//! Revoke is by `session_id` (NOT by hash). The refreshed successor JWT
//! shares the original `session_id` with the install JWT, so revoking the
//! stale install JWT by `session_id` also kills any rotated successors that
//! have not yet been re-installed via `install` (a different `session_id`).

use bcs_auth_api::{
    IssuedSession, RotateSession, SessionAuthError, SessionRevoke, SessionVersion, SessionWrite,
};
use bcs_jwt::{OAuthSessionClaims, token_hash};

use crate::strict::OAuthSessionEngine;

impl OAuthSessionEngine {
    /// Rotate the binding to a fresh token.
    ///
    /// See the module docs for the algorithm. Returns
    /// [`IssuedSession`] only after the store's CAS reports [`SessionWrite::Applied`].
    pub async fn refresh(
        &self,
        token: &str,
        now: u64,
    ) -> Result<IssuedSession, SessionAuthError> {
        let claims = self.jwt.verify_at(token, now).map_err(|_| SessionAuthError::Invalid)?;
        if claims.env != self.env {
            return Err(SessionAuthError::Invalid);
        }
        let scope = crate::strict::claims_scope(&claims);
        let hash = token_hash(token);
        let snapshot = self
            .identities
            .get_session_by_hash(&scope, &hash, now)
            .await
            .map_err(crate::strict::map_store_read_error)?
            .ok_or(SessionAuthError::Invalid)?;
        if snapshot.version.session_id != claims.session_id
            || snapshot.version.revision != claims.revision
        {
            return Err(SessionAuthError::Invalid);
        }
        let next_revision = snapshot
            .version
            .revision
            .checked_add(1)
            .ok_or(SessionAuthError::Internal)?;
        let exp = now.saturating_add(self.idle_secs);
        let next_claims = OAuthSessionClaims {
            sub: claims.sub.clone(),
            src: claims.src.clone(),
            env: claims.env.clone(),
            session_id: snapshot.version.session_id.clone(),
            revision: next_revision,
            iat: now,
            exp,
            name: claims.name.clone(),
        };
        let next_token = self
            .jwt
            .sign(&next_claims)
            .map_err(|_| SessionAuthError::Internal)?;
        let next_hash = token_hash(&next_token);
        let rotate = RotateSession {
            scope,
            expected: snapshot.version.clone(),
            next: SessionVersion {
                session_id: snapshot.version.session_id.clone(),
                revision: next_revision,
                token_hash: next_hash,
            },
            expires_at: exp,
            now,
        };
        let outcome = self
            .identities
            .rotate_session(rotate)
            .await
            .map_err(|_| SessionAuthError::Unavailable)?;
        match outcome {
            SessionWrite::Applied => Ok(IssuedSession { token: next_token, expires_at: exp }),
            SessionWrite::Conflict => Err(SessionAuthError::Invalid),
        }
    }

    /// Revoke the session identified by `token`. Idempotent: both
    /// [`SessionRevoke::Revoked`] and [`SessionRevoke::NotCurrent`] are
    /// returned as `Ok`. Revokes by `session_id`, so the rotated successors
    /// (which share the same `session_id`) are also killed. Accepts an
    /// expired token (`exp` check is skipped); rejects a bad signature or
    /// missing `session_id`/`revision`/`env`.
    pub async fn revoke(&self, token: &str) -> Result<SessionRevoke, SessionAuthError> {
        let claims = self
            .jwt
            .verify_for_revoke(token)
            .map_err(|_| SessionAuthError::Invalid)?;
        if claims.env != self.env {
            return Err(SessionAuthError::Invalid);
        }
        let scope = crate::strict::claims_scope(&claims);
        self.identities
            .revoke_session(&scope, &claims.session_id)
            .await
            .map_err(|_| SessionAuthError::Unavailable)
    }
}
