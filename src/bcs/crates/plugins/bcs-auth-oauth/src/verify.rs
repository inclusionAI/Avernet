//! Shared JWT verification logic for OAuth session authentication.
//!
//! Implements "先验后证" (verify-then-confirm):
//! 1. Extract `bcs_session` cookie or `Authorization: Bearer` — None if absent
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

use bcs_auth_api::{extract_bearer_token, extract_session_cookie, AuthError, AuthPrincipal, AuthSource, UserIdentityPort};

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
    // 1. Extract token: prefer the `bcs_session` cookie, fall back to
    //    `Authorization: Bearer <jwt>` so non-browser clients (CLI/API) can
    //    authenticate. No credential presented => not authenticated.
    let token = match extract_session_cookie(headers).or_else(|| extract_bearer_token(headers)) {
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

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};

    use async_trait::async_trait;
    use axum::http::{HeaderMap, HeaderValue};

    use bcs_auth_api::{
        AuthError, AuthPlugin, AuthSource, UserIdentityInfo, UserIdentityPort,
    };
    use bcs_jwt::{token_hash, Claims, JwtService};

    use crate::plugin::OAuthSessionPlugin;

    /// Minimal in-memory `UserIdentityPort` for verifying the read path.
    /// Only the methods exercised by `verify_oauth_session` are meaningfully
    /// implemented; the rest return `Ok(None)` / no-op.
    struct MemoryUserIdentity {
        // token_hash -> identity row
        by_token: Mutex<HashMap<String, UserIdentityInfo>>,
    }

    impl MemoryUserIdentity {
        fn new() -> Self {
            Self {
                by_token: Mutex::new(HashMap::new()),
            }
        }

        fn bind(&self, token_hash: &str, info: UserIdentityInfo) {
            self.by_token
                .lock()
                .unwrap()
                .insert(token_hash.to_string(), info);
        }
    }

    #[async_trait]
    impl UserIdentityPort for MemoryUserIdentity {
        async fn ensure_identity(
            &self,
            _auth_source: &str,
            _external_user_id: &str,
            _external_user_name: Option<&str>,
            _avatar: Option<&str>,
            _env: &str,
        ) -> Result<String, AuthError> {
            Err(AuthError::LookupFailed("not used in this test".into()))
        }

        async fn lookup_by_user_id(
            &self,
            _user_id: &str,
            _auth_source: &str,
        ) -> Result<Option<String>, AuthError> {
            Ok(None)
        }

        async fn get_identity_by_token(
            &self,
            token_hash: &str,
        ) -> Result<Option<UserIdentityInfo>, AuthError> {
            Ok(self.by_token.lock().unwrap().get(token_hash).cloned())
        }

        async fn get_identity_by_user_id(
            &self,
            _user_id: &str,
        ) -> Result<Option<UserIdentityInfo>, AuthError> {
            Ok(None)
        }

        async fn update_token(
            &self,
            _user_id: &str,
            _token_hash: &str,
            _expire_at: u64,
        ) -> Result<(), AuthError> {
            Ok(())
        }
    }

    /// A Bearer JWT presented via `Authorization: Bearer <jwt>` resolves to the
    /// bound principal — proving the cookie-or-bearer fallback works end to end
    /// through `OAuthSessionPlugin::authenticate` (extraction -> verify ->
    /// hash-bind -> principal).
    #[tokio::test]
    async fn bearer_jwt_resolves_to_principal() {
        let secret = "test-secret";
        let user_id = "u1a2b3c4d5e6";
        let provider = "password";

        // Sign a session JWT the way login does.
        let svc = JwtService::new(secret);
        let claims = Claims {
            sub: user_id.to_string(),
            src: provider.to_string(),
            iat: 0,
            exp: 9_999_999_999, // far future, never expires in this test
        };
        let jwt = svc.sign(&claims).unwrap();
        let hash = token_hash(&jwt);

        // Bind the token hash to a user row (mirrors what login/refresh writes).
        let port = Arc::new(MemoryUserIdentity::new());
        port.bind(
            &hash,
            UserIdentityInfo {
                user_id: user_id.to_string(),
                auth_source: provider.to_string(),
                user_name: Some("test-user".to_string()),
                external_user_name: None,
                avatar: None,
            },
        );

        // Plugin wired exactly as bootstrap wires it for the chain.
        let plugin = OAuthSessionPlugin::new(secret, port.clone());

        // No cookie — only the Bearer header (the CLI/API transport).
        let mut headers = HeaderMap::new();
        headers.insert(
            "authorization",
            HeaderValue::from_str(&format!("Bearer {jwt}")).unwrap(),
        );

        // can_authenticate must accept the Bearer-only shape.
        assert!(plugin.can_authenticate(&headers));

        let principal = plugin.authenticate(&headers).await.unwrap().expect("bearer JWT should resolve");
        assert_eq!(principal.user_id.as_deref(), Some(user_id));
        assert_eq!(
            principal.source_name.as_deref(),
            Some(provider),
            "source_name must carry the password provider from claims.src"
        );
    }

    /// Cookie remains the preferred transport and still resolves.
    #[tokio::test]
    async fn cookie_jwt_still_resolves() {
        let secret = "test-secret";
        let user_id = "cookie-user";
        let provider = "google";

        let svc = JwtService::new(secret);
        let claims = Claims {
            sub: user_id.to_string(),
            src: provider.to_string(),
            iat: 0,
            exp: 9_999_999_999,
        };
        let jwt = svc.sign(&claims).unwrap();
        let hash = token_hash(&jwt);

        let port = Arc::new(MemoryUserIdentity::new());
        port.bind(
            &hash,
            UserIdentityInfo {
                user_id: user_id.to_string(),
                auth_source: provider.to_string(),
                user_name: None,
                external_user_name: Some("Cookie User".to_string()),
                avatar: None,
            },
        );

        let plugin = OAuthSessionPlugin::new(secret, port);

        let mut headers = HeaderMap::new();
        headers.insert(
            "cookie",
            HeaderValue::from_str(&format!("bcs_session={jwt}")).unwrap(),
        );

        let principal = plugin.authenticate(&headers).await.unwrap().expect("cookie JWT should resolve");
        assert_eq!(principal.user_id.as_deref(), Some(user_id));
        assert_eq!(principal.source_name.as_deref(), Some(provider));
        // user_name falls back to external_user_name when user_name is None.
        assert_eq!(principal.user_name.as_deref(), Some("Cookie User"));
    }

    /// No cookie and no Bearer => anonymous (Ok(None)), not an error.
    #[tokio::test]
    async fn no_credential_is_anonymous() {
        let port = Arc::new(MemoryUserIdentity::new());
        let plugin = OAuthSessionPlugin::new("secret", port);
        let headers = HeaderMap::new();

        assert!(!plugin.can_authenticate(&headers));
        assert!(plugin.authenticate(&headers).await.unwrap().is_none());
    }

    /// A Bearer JWT whose hash is NOT bound (revoked / superseded) must not
    /// resolve — the hash-bind step (3) is still enforced for Bearer clients.
    #[tokio::test]
    async fn bearer_jwt_unbound_is_rejected() {
        let secret = "test-secret";
        let svc = JwtService::new(secret);
        let claims = Claims {
            sub: "ghost".to_string(),
            src: "password".to_string(),
            iat: 0,
            exp: 9_999_999_999,
        };
        let jwt = svc.sign(&claims).unwrap();

        // No update_token bind — the hash is unknown to the port.
        let port = Arc::new(MemoryUserIdentity::new());
        let plugin = OAuthSessionPlugin::new(secret, port);

        let mut headers = HeaderMap::new();
        headers.insert(
            "authorization",
            HeaderValue::from_str(&format!("Bearer {jwt}")).unwrap(),
        );

        assert!(plugin.can_authenticate(&headers));
        assert!(
            plugin.authenticate(&headers).await.unwrap().is_none(),
            "an unbound Bearer JWT must NOT resolve"
        );
    }

    /// Sanity-check the AuthSource name mapping used in step 4.
    #[test]
    fn oauth_source_name_is_provider() {
        assert_eq!(AuthSource::OAuth("password".to_string()).name(), "password");
    }
}
