//! Shared JWT verification logic for OAuth session authentication.
//!
//! Implements "先验后证" (verify-then-confirm):
//! 1. Extract `bcs_session` cookie — None if absent
//! 2. Verify JWT signature + exp (no IO) — None if expired/invalid sig
//! 3. Confirm the presented JWT is the current session by matching its
//!    fingerprint against the stored hash (IO) — None if mismatched/revoked
//! 4. Build `AuthPrincipal` with OAuth source
//!
//! This is the read-only hot path: it does NOT re-sign or write to the DB.
//! Sliding renewal lives in the dedicated `POST /auth/refresh` endpoint, which
//! is the only place that can return a fresh `Set-Cookie` to the client.

use axum::http::HeaderMap;
use bcs_jwt::{token_hash, JwtService};

use bcs_auth_api::{extract_session_cookie, AuthError, AuthPrincipal, AuthSource, UserIdentityPort};

/// Shared JWT verification for OAuth plugins.
///
/// Returns:
/// - `Ok(Some(principal))` — valid session, user confirmed
/// - `Ok(None)` — no cookie, expired, invalid signature, or user not found
/// - `Err(AuthError)` — unexpected failure (e.g. DB lookup error)
pub async fn verify_oauth_session(
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
    //    session JWT; since we signed it, the name is already trusted and is
    //    carried through verbatim — no per-provider allowlist here, so new
    //    providers need no change to this crate. Display fields (`user_name`,
    //    `avatar`) are carried from the identity row resolved in step 3 — the
    //    store already returned them, so no extra IO is needed here.
    let mut principal = AuthPrincipal::new(AuthSource::OAuth(claims.src.clone()));
    principal.user_id = Some(claims.sub.clone());
    principal.user_name = info.user_name.or(info.external_user_name);
    principal.avatar = info.avatar;

    Ok(Some(principal))
}
