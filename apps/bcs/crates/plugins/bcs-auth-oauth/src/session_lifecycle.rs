//! Atomic OAuth session lifecycle issuance: install, refresh, revoke
//! (V1 API auth plugin chain, Task 8; plan §8.5).
//!
//! Every issuing path is a compare-and-swap: the engine signs the next
//! JWT BEFORE attempting the CAS, and hands the credential out only AFTER
//! the CAS reports `Applied`. A lost LOGIN race is the 409-side
//! [`SessionAuthError::Conflict`] (the browser restarts its login batch);
//! a lost REFRESH race is the 401-side [`SessionAuthError::Invalid`]
//! (re-authenticate) — NEVER a retried unconditional write.
//!
//! Refresh preserves the session id and bumps exactly one revision, so
//! even same-second refreshes produce different tokens (the revision is in
//! the claims). Revoke verifies the signature (skipping only `exp`) plus
//! the injected env and revokes BY SID — it does not require the presented
//! token's hash to still be live and therefore also kills every rotated
//! descendant of the instance, while a NEW login (different sid) stays
//! untouched.
//!
//! The engine deliberately does NOT guess Origin or provider allowlists —
//! those belong to the HTTP adapter and the source wrappers (Tasks 11/12).

use bcs_auth_api::session::{
    InstallSession, IssuedSession, RotateSession, SessionAuthError, SessionRevoke, SessionScope,
    SessionVersion, SessionWrite,
};
use bcs_jwt::{token_hash, OAuthSessionClaims};

use crate::strict::{
    issued, map_store_error, new_session_id, next_revision, sign_claims, window_expiry,
    OAuthSessionEngine, SignedVersion,
};

impl OAuthSessionEngine {
    /// Install a NEW login session for an already-confirmed scope.
    ///
    /// The caller (the OAuth callback bridge) has verified the provider
    /// result and called `ensure_identity` first; `scope` carries the
    /// internal user id plus the trusted provider/env. This method:
    /// 1. guards `scope.env` against the injected env (Forbidden);
    /// 2. reads the current revision;
    /// 3. generates a random NEW sid, signs the JWT, and CAS-installs at
    ///    `expected_revision` — a lost login race is `Conflict`
    ///    (409-side), never an implicit overwrite.
    pub async fn install(
        &self,
        scope: SessionScope,
        display_name: Option<String>,
        now: u64,
    ) -> Result<IssuedSession, SessionAuthError> {
        if scope.env != self.env {
            return Err(SessionAuthError::Forbidden);
        }
        let revision = self
            .identities
            .read_session_revision(&scope)
            .await
            .map_err(map_store_error)?;
        let next_rev = next_revision(revision)?;
        let expires_at = window_expiry(now, self.idle_secs)?;

        // Sign BEFORE the CAS; the credential is handed out only after
        // `Applied`. The signed claims name the NEW random instance and
        // the INJECTED env.
        let session_id = new_session_id();
        let claims = OAuthSessionClaims {
            sub: scope.user_id.clone(),
            src: scope.provider.clone(),
            env: self.env.clone(),
            session_id: session_id.clone(),
            revision: next_rev,
            iat: now,
            exp: expires_at,
            name: display_name,
        };
        let token = sign_claims(&self.jwt, &claims)?;
        let signed = SignedVersion {
            hash: token_hash(&token),
            token,
            session_id,
            revision: next_rev,
            expires_at,
        };

        let command = InstallSession {
            scope,
            expected_revision: revision,
            next: SessionVersion {
                session_id: signed.session_id.clone(),
                revision: signed.revision,
                token_hash: signed.hash.clone(),
            },
            expires_at: signed.expires_at,
        };
        match self
            .identities
            .install_login_session(command)
            .await
            .map_err(map_store_error)?
        {
            SessionWrite::Applied => Ok(issued(signed)),
            SessionWrite::Conflict => Err(SessionAuthError::Conflict),
        }
    }

    /// Refresh (rotate) the session named by `token`.
    ///
    /// Binding algorithm (plan §8.5):
    /// ```text
    /// claims = jwt.verify_at(token, now)
    /// reject claims.env != injected env
    /// snapshot = get_session_by_hash(scope, sha256(token), now)
    /// require claims.sid/revision == snapshot.sid/revision
    /// next_revision = checked_add(snapshot.revision, 1)
    /// sign next JWT with same sid, next_revision, injected env
    /// CAS rotate(expected snapshot, next hash/expiry)
    /// Conflict -> Invalid; storage failure -> Unavailable
    /// return IssuedSession only after Applied
    /// ```
    pub async fn refresh(&self, token: &str, now: u64) -> Result<IssuedSession, SessionAuthError> {
        self.refresh_with_provider_check(token, now, |_| true).await
    }

    /// Rotate only if the entry's provider policy accepts the STRICTLY verified
    /// session. The registering wrapper owns the policy; the engine runs it
    /// after signature/env/hash/version checks and before signing or CAS.
    /// Rejection is Forbidden, spends one SELECT and performs no write. Allowed
    /// refresh still spends one SELECT plus one CAS; no second verification or
    /// retry is introduced. Legacy callers use `refresh` without a source filter.
    pub async fn refresh_with_provider_check(
        &self,
        token: &str,
        now: u64,
        accepts_provider: impl FnOnce(&str) -> bool + Send,
    ) -> Result<IssuedSession, SessionAuthError> {
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
            return Err(SessionAuthError::Invalid);
        }

        if !accepts_provider(&snapshot.scope.provider) {
            return Err(SessionAuthError::Forbidden);
        }

        let next_rev = next_revision(snapshot.version.revision)?;
        let expires_at = window_expiry(now, self.idle_secs)?;
        // Same sid, next revision: a same-second refresh still produces a
        // different token because the revision is signed into the claims.
        let claims = OAuthSessionClaims {
            sub: snapshot.scope.user_id.clone(),
            src: snapshot.scope.provider.clone(),
            env: self.env.clone(),
            session_id: snapshot.version.session_id.clone(),
            revision: next_rev,
            iat: now,
            exp: expires_at,
            name: claims.name.clone(),
        };
        let next_token = sign_claims(&self.jwt, &claims)?;
        let signed = SignedVersion {
            hash: token_hash(&next_token),
            token: next_token,
            session_id: snapshot.version.session_id.clone(),
            revision: next_rev,
            expires_at,
        };

        let command = RotateSession {
            scope,
            expected: snapshot.version.clone(),
            next: SessionVersion {
                session_id: signed.session_id.clone(),
                revision: signed.revision,
                token_hash: signed.hash.clone(),
            },
            expires_at: signed.expires_at,
            now,
        };
        match self
            .identities
            .rotate_session(command)
            .await
            .map_err(map_store_error)?
        {
            SessionWrite::Applied => Ok(issued(signed)),
            SessionWrite::Conflict => Err(SessionAuthError::Invalid),
        }
    }

    /// Revoke the session instance named by `token` — logout.
    ///
    /// Verifies the signature with claim integrity but skips ONLY `exp`
    /// (`verify_for_revoke`): a stale browser may log out an expired
    /// instance. Enforces the injected env, then issues the BY-SID atomic
    /// revoke: the presented token's hash does NOT need to be live, and
    /// every rotated descendant of that sid dies with it — a NEW login
    /// (different sid) is never touched.
    pub async fn revoke(&self, token: &str) -> Result<SessionRevoke, SessionAuthError> {
        let claims = self
            .jwt
            .verify_for_revoke(token)
            .map_err(|_| SessionAuthError::Invalid)?;
        self.ensure_injected_env(&claims)?;
        let scope = self.claims_scope(&claims);
        self.identities
            .revoke_session(&scope, &claims.session_id)
            .await
            .map_err(map_store_error)
    }
}
