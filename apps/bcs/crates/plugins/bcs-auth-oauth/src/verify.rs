//! OAuth session verification for the plugin chain.
//!
//! [`verify_oauth_session`] is the read-only hot path: it delegates the
//! presented `bcs_session` cookie to the STRICT engine (`signature +
//! claim integrity + injected env + hash binding + sid/revision +
//! revocation`) and maps the closed strict categories to the legacy
//! `Option` shape at exactly ONE explicitly-marked compatibility exit.
//! Legacy sid-less JWTs are REJECTED by this path — the strict verifier
//! refuses tokens without the required `env`/`session_id`/`revision`
//! claims; those sessions must be re-issued through the engine's install.
//!
//! The hot path does NOT re-sign or write: renewal belongs to the refresh
//! endpoint, which uses the engine's compare-and-swap lifecycle
//! ([`crate::OAuthSessionEngine::refresh`]).
//!
//! [`verify_oauth_session_unsafe_construction_transition`] is the
//! explicitly-marked UNSAFE-CONSTRUCTION TRANSITION SEAM scheduled for
//! DELETION (see its docs).

use axum::http::HeaderMap;
use bcs_jwt::{token_hash, JwtService};

use bcs_auth_api::session::SessionAuthError;
use bcs_auth_api::{
    extract_session_cookie, AuthError, AuthPrincipal, AuthSource, UserIdentityPort,
};

use crate::strict::OAuthSessionEngine;

/// Shared JWT verification for OAuth plugins via the STRICT engine.
///
/// Returns:
/// - `Ok(Some(principal))` — strictly verified session (sid/revision/env/
///   revocation all enforced by [`OAuthSessionEngine::verify`])
/// - `Ok(None)` — ** LEGACY COMPATIBILITY EXIT (the only Option mapping) **
///   no cookie, or a strict denial (`Invalid` — bad signature, expired,
///   missing legacy-required claims, unbound/rotated-out/revoked hash,
///   snapshot version mismatch; `Forbidden` — foreign-env token)
/// - `Err(AuthError)` — dependency/internal faults (`Unavailable`,
///   `Internal`), propagated as errors, NEVER folded into `Ok(None)`
pub async fn verify_oauth_session(
    engine: &OAuthSessionEngine,
    headers: &HeaderMap,
) -> Result<Option<AuthPrincipal>, AuthError> {
    // 1. Extract cookie — absent means anonymous, not an error.
    let Some(token) = extract_session_cookie(headers) else {
        return Ok(None);
    };

    // The engine owns the strict semantics; wall clock enters ONLY here,
    // at this boundary exit (strict paths take an injected `now`).
    let now = wall_now_secs();
    let snapshot = match engine.verify(token.as_str(), now).await {
        Ok(snapshot) => snapshot,
        // == LEGACY COMPATIBILITY EXIT: strict denials map to the old
        // Option shape here and NOWHERE ELSE. A rejected credential is
        // "not authenticated", not a hard error; dependency faults below
        // are deliberately NOT part of this arm.
        Err(SessionAuthError::Invalid) | Err(SessionAuthError::Forbidden) => return Ok(None),
        Err(SessionAuthError::Unavailable) => {
            return Err(AuthError::LookupFailed(
                "session identity store unavailable".to_string(),
            ));
        }
        Err(SessionAuthError::Internal) => {
            return Err(AuthError::LookupFailed(
                "session verification internal failure".to_string(),
            ));
        }
        // Reads never produce the install-race Conflict; keep the match
        // closed so a future category change must consciously re-decide.
        Err(SessionAuthError::Conflict) => {
            return Err(AuthError::LookupFailed(
                "unexpected session write conflict during verification".to_string(),
            ));
        }
    };

    // 2. Build the principal from the TRUSTED snapshot. Note (plan §7.3):
    // `username` passes through as-is — possibly empty for a nameless
    // identity. The internal-ID display fallback belongs to the
    // delivery/current-user projection (Tasks 11/12), NOT to the
    // verification path.
    let mut principal =
        AuthPrincipal::new(AuthSource::OAuth(snapshot.scope.provider.clone()));
    principal.user_id = Some(snapshot.scope.user_id.clone());
    principal.user_name = (!snapshot.username.is_empty()).then(|| snapshot.username.clone());
    principal.avatar = snapshot.avatar.clone();
    // The trusted session window actually verified (unix seconds on the
    // snapshot), converted to the runtime-only ingress milliseconds —
    // seconds -> milliseconds, the only direction the boundary allows.
    principal.human_expires_at_ms = Some(snapshot.expires_at.saturating_mul(1000));

    Ok(Some(principal))
}

/// UNSAFE-CONSTRUCTION TRANSITION SEAM — ** scheduled for DELETION in the
/// HTTP entrypoint migration (Task 11)**.
///
/// This is the PRE-strict verification shape: it accepts LEGACY sid-less
/// JWTs (`bcs_jwt::Claims`) bound by a stored token hash, i.e. tokens whose
/// construction never went through [`OAuthSessionEngine::install`]. It must
/// stay until every legacy issuer (old callback writers, legacy fixtures)
/// is synced to the strict lifecycle; afterwards the unsafe construction
/// path is removed and production sessions verify ONLY via
/// [`verify_oauth_session`].
///
/// Every call site is discoverable by grepping
/// `unsafe_construction_transition` (function name and plugin variant).
pub async fn verify_oauth_session_unsafe_construction_transition(
    headers: &HeaderMap,
    jwt_secret: &str,
    user_port: &dyn UserIdentityPort,
) -> Result<Option<AuthPrincipal>, AuthError> {
    // 1. Extract cookie
    let token = match extract_session_cookie(headers) {
        Some(t) => t,
        None => return Ok(None),
    };

    // 2. Verify JWT signature + exp (no IO)
    // Any JWT verification failure (expired, bad signature, malformed token)
    // is treated as "not authenticated" — not a hard error.
    let svc = JwtService::new(jwt_secret);
    let claims = match svc.verify(&token) {
        Ok(c) => c,
        Err(_) => {
            return Ok(None);
        }
    };

    // 3. Confirm this JWT is the *current* session for the user by matching
    //    its fingerprint against the stored hash (IO). A mismatch means the
    //    token was superseded (single-session) or revoked on logout (cleared
    //    hash) — treat as unauthenticated, not a hard error.
    let presented_hash = token_hash(&token);
    let info = match user_port.get_identity_by_token(&presented_hash).await {
        Ok(Some(info)) if info.user_id == claims.sub => info,
        Ok(_) => return Ok(None),
        Err(e) => {
            return Err(AuthError::LookupFailed(format!(
                "identity lookup failed: {}",
                e
            )));
        }
    };

    // 4. Build principal. `claims.src` is the provider that issued this
    // session JWT; since we signed it, the name is already trusted and is
    // carried through verbatim — no per-provider allowlist here, so new
    // providers need no change to this crate. Display fields (`user_name`,
    // `avatar`) are carried from the identity row resolved in step 3 — the
    // store already returned them, so no extra IO is needed here.
    let mut principal = AuthPrincipal::new(AuthSource::OAuth(claims.src.clone()));
    principal.user_id = Some(claims.sub.clone());
    principal.user_name = info.user_name.or(info.external_user_name);
    principal.avatar = info.avatar;
    // The trusted session window actually verified above (unix seconds on the
    // JWT). The strict caller-identity extraction converts it to the
    // runtime-only ingress expiry; without it a display-only principal never
    // becomes caller material. Converted seconds -> milliseconds here, the
    // only direction the boundary allows.
    principal.human_expires_at_ms = Some(claims.exp.saturating_mul(1000));

    Ok(Some(principal))
}

/// Wall-clock seconds at the boundary exit. The strict engine and stores
/// always take an injected `now`; only this legacy-facing adapter reads
/// the clock.
fn wall_now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default()
}
