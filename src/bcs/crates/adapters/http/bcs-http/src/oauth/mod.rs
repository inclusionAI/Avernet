//! OAuth HTTP routes: /auth/url, /auth/callback/{provider}, /auth/logout.
//!
//! These routes are shared across all OAuth providers. Provider-specific
//! logic is delegated to the `OAuthProvider` trait implementations injected
//! into `OAuthRouteState`.

pub mod state;

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use bcs_auth_api::{extract_session_cookie, OAuthConfig, OAuthProvider, UserIdentityPort, BCS_SESSION_COOKIE};
use bcs_jwt::{Claims, JwtService};

use crate::oauth::state::OAuthStateStore;

/// Shared state for OAuth routes.
pub struct OAuthRouteState {
    pub jwt_service: JwtService,
    pub user_port: Arc<dyn UserIdentityPort>,
    pub providers: HashMap<String, Arc<dyn OAuthProvider>>,
    pub state_store: OAuthStateStore,
    pub config: OAuthConfig,
    /// Non-OAuth fallback identity source. When a request has no valid
    /// `bcs_session` cookie (or OAuth is not configured at all),
    /// `current_user_handler` resolves the caller via this chain — e.g. the
    /// local mock plugin. `None` only in contract-test state that never serves
    /// real traffic.
    pub auth_chain: Option<Arc<bcs_auth_api::AuthPluginChain>>,
}

impl OAuthRouteState {
    pub fn new(
        jwt_secret: &str,
        user_port: Arc<dyn UserIdentityPort>,
        providers: HashMap<String, Arc<dyn OAuthProvider>>,
        config: OAuthConfig,
        auth_chain: Option<Arc<bcs_auth_api::AuthPluginChain>>,
    ) -> Self {
        Self {
            jwt_service: JwtService::new(jwt_secret),
            user_port,
            providers,
            state_store: OAuthStateStore::new(Duration::from_secs(300)), // 5 min TTL
            config,
            auth_chain,
        }
    }

    /// Identity-only state for the no-OAuth case: `/auth/user` is backed solely
    /// by the auth chain. The `JwtService` is unused on this path — the cookie
    /// lookup is only attempted when `config.jwt_secret` is non-empty, which this
    /// state leaves empty, so no cookie is ever verified here.
    pub fn new_chain_only(
        user_port: Arc<dyn UserIdentityPort>,
        auth_chain: Arc<bcs_auth_api::AuthPluginChain>,
    ) -> Self {
        Self {
            jwt_service: JwtService::new(""),
            user_port,
            providers: HashMap::new(),
            state_store: OAuthStateStore::new(Duration::from_secs(300)), // 5 min TTL
            config: OAuthConfig::default(),
            auth_chain: Some(auth_chain),
        }
    }
}

/// Build the OAuth router for the shared `/auth/*` endpoints, bound to the
/// given `OAuthRouteState`.
pub fn routes(state: Arc<OAuthRouteState>) -> Router {
    Router::new()
        .route("/auth/url", get(auth_url_handler))
        .route("/auth/callback/{provider}", get(callback_handler))
        .route("/auth/logout", post(logout_handler))
        .route("/auth/refresh", post(refresh_handler))
        .route("/auth/user", get(current_user_handler))
        .route("/auth/user/{user_id}", get(get_user_handler))
        .with_state(state)
}

/// Identity-only router: mounts just `GET /auth/user`. Used when no OAuth
/// provider is configured but an auth chain exists, so non-OAuth callers
/// (e.g. the local mock plugin) can still ask "who am I?" without registering
/// the OAuth protocol routes that would otherwise 404/empty.
pub fn identity_routes(state: Arc<OAuthRouteState>) -> Router {
    Router::new()
        .route("/auth/user", get(current_user_handler))
        .with_state(state)
}

/// GET /auth/url — Return all enabled OAuth provider login URLs.
#[derive(Serialize)]
pub struct AuthUrlResponse {
    pub providers: Vec<ProviderUrl>,
}

#[derive(Serialize)]
pub struct ProviderUrl {
    pub name: String,
    pub url: String,
}

pub async fn auth_url_handler(
    State(state): State<Arc<OAuthRouteState>>,
) -> Json<AuthUrlResponse> {
    let mut providers = Vec::new();
    // Sort for deterministic output
    let mut names: Vec<&str> = state.providers.keys().map(|s| s.as_str()).collect();
    names.sort();
    for name in names {
        if let Some(provider) = state.providers.get(name) {
            let csrf_state = state.state_store.generate(name).await;
            let redirect_uri = format!("{}/auth/callback/{}", state.config.base_url, name);
            let url = provider.auth_url(&csrf_state, &redirect_uri);
            providers.push(ProviderUrl {
                name: name.to_string(),
                url,
            });
        }
    }
    Json(AuthUrlResponse { providers })
}

/// GET /auth/callback/{provider}?code=...&state=...
pub async fn callback_handler(
    State(state): State<Arc<OAuthRouteState>>,
    Path(provider_name): Path<String>,
    axum::extract::Query(params): axum::extract::Query<CallbackParams>,
) -> impl IntoResponse {
    // 1. Validate state (CSRF)
    let provider_key = match state.state_store.consume(&params.state).await {
        Ok(key) => key,
        Err(e) => {
            warn!(error = %e, "OAuth callback: invalid state");
            return (StatusCode::BAD_REQUEST, "invalid state").into_response();
        }
    };

    // 2. Verify provider matches state
    if provider_key != provider_name {
        warn!(expected = %provider_key, got = %provider_name, "OAuth callback: provider mismatch");
        return (StatusCode::BAD_REQUEST, "provider mismatch").into_response();
    }

    // 3. Find provider
    let provider = match state.providers.get(&provider_name) {
        Some(p) => Arc::clone(p),
        None => {
            return (StatusCode::NOT_FOUND, "provider not found").into_response();
        }
    };

    // 4. Exchange code for token (Alipay uses auth_code instead of code)
    let code = match params.code.or(params.auth_code) {
        Some(c) => c,
        None => {
            warn!("OAuth callback: missing code or auth_code");
            return (StatusCode::BAD_REQUEST, "missing code or auth_code").into_response();
        }
    };
    let redirect_uri = format!("{}/auth/callback/{}", state.config.base_url, provider_name);
    let token = match provider.exchange_code(&code, &redirect_uri).await {
        Ok(t) => t,
        Err(e) => {
            warn!(error = %e, provider = %provider_name, "OAuth token exchange failed");
            return (StatusCode::INTERNAL_SERVER_ERROR, "token exchange failed").into_response();
        }
    };

    // 5. Get user info
    let user_info = match provider.get_user_info(&token).await {
        Ok(u) => u,
        Err(e) => {
            warn!(error = %e, provider = %provider_name, "OAuth userinfo failed");
            return (StatusCode::INTERNAL_SERVER_ERROR, "userinfo request failed").into_response();
        }
    };

    // 6. Ensure identity in database, partitioned by the configured runtime env.
    let user_id = match state
        .user_port
        .ensure_identity(
            &provider_name,
            &user_info.id,
            user_info.name.as_deref(),
            user_info.avatar.as_deref(),
            &state.config.env,
        )
        .await
    {
        Ok(id) => id,
        Err(e) => {
            warn!(error = %e, "ensure_identity failed");
            return (StatusCode::INTERNAL_SERVER_ERROR, "identity creation failed").into_response();
        }
    };

    // 7. Issue JWT
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let claims = Claims {
        sub: user_id,
        src: provider_name.clone(),
        iat: now,
        exp: now + state.config.idle_timeout_secs(),
    };
    let jwt = match state.jwt_service.sign(&claims) {
        Ok(j) => j,
        Err(e) => {
            warn!(error = %e, "JWT signing failed");
            return (StatusCode::INTERNAL_SERVER_ERROR, "session creation failed").into_response();
        }
    };

    // 7b. Persist the JWT fingerprint (SHA-256) so the session can be looked
    // up / single-session-enforced later. Only the hash is stored — the raw
    // JWT lives solely in the client cookie. This bind is load-bearing for
    // single-session + revocation, so a failure here aborts the login.
    if let Err(e) = state
        .user_port
        .update_token(&claims.sub, &bcs_jwt::token_hash(&jwt), claims.exp)
        .await
    {
        warn!(error = %e, "update_token failed; aborting login");
        return (StatusCode::INTERNAL_SERVER_ERROR, "session creation failed").into_response();
    }

    info!(provider = %provider_name, user_id = %claims.sub, "OAuth login successful");

    // 8. Set cookie and redirect
    let cookie = session_cookie(&jwt, state.config.cookie_secure);
    (
        StatusCode::FOUND,
        [
            ("location", "/?login=success".to_string()),
            ("set-cookie", cookie),
        ],
    )
        .into_response()
}

/// Build the `Set-Cookie` value carrying the session JWT. `secure` controls the
/// `Secure` attribute — must be `false` over plain HTTP (local dev) or browsers
/// drop the cookie.
fn session_cookie(jwt: &str, secure: bool) -> String {
    let secure_attr = if secure { "; Secure" } else { "" };
    format!("{BCS_SESSION_COOKIE}={jwt}; HttpOnly{secure_attr}; SameSite=Lax; Path=/")
}

/// Build the `Set-Cookie` value that clears the session cookie.
fn clear_session_cookie(secure: bool) -> String {
    let secure_attr = if secure { "; Secure" } else { "" };
    format!("{BCS_SESSION_COOKIE}=; HttpOnly{secure_attr}; SameSite=Lax; Path=/; Max-Age=0")
}

#[derive(Deserialize)]
pub struct CallbackParams {
    /// Standard OAuth 2.0 authorization code (Google, GitHub, WeChat).
    pub code: Option<String>,
    /// Alipay uses `auth_code` instead of `code`.
    pub auth_code: Option<String>,
    /// CSRF state parameter.
    pub state: String,
}

/// POST /auth/logout — Clear the session cookie and revoke the session.
///
/// Clearing the cookie alone does not stop a copy of the JWT from being
/// replayed, so we also clear the stored token hash for the user, which
/// invalidates the JWT server-side on the next request (the hot-path hash
/// match fails). Best-effort: cookie is always cleared even if revocation
/// fails.
pub async fn logout_handler(
    State(state): State<Arc<OAuthRouteState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Some(jwt) = extract_session_cookie(&headers) {
        if let Ok(claims) = state.jwt_service.verify(&jwt) {
            if let Err(e) = state.user_port.update_token(&claims.sub, "", 0).await {
                warn!(error = %e, user_id = %claims.sub, "logout: token revocation failed");
            }
        }
    }
    let cookie = clear_session_cookie(state.config.cookie_secure);
    (StatusCode::OK, [("set-cookie", cookie)])
}

/// POST /auth/refresh — Sliding-window session renewal.
///
/// This is the only place sessions are renewed. The hot-path auth chain is
/// read-only, so renewal cannot happen there (no way to return `Set-Cookie`).
/// Accepts a still-valid or recently-expired cookie, re-signs a fresh JWT,
/// rebinds the stored hash (so the old JWT is immediately invalidated), and
/// returns the new cookie. The presented JWT must currently be the bound
/// session, otherwise renewal is refused.
pub async fn refresh_handler(
    State(state): State<Arc<OAuthRouteState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let jwt = match extract_session_cookie(&headers) {
        Some(t) => t,
        None => return (StatusCode::UNAUTHORIZED, "not authenticated").into_response(),
    };

    // Tolerate a recently-expired token within the idle window: verify the
    // signature but not exp, so a client whose cookie just lapsed can still
    // renew. Hard-expiry beyond renewal is enforced by the hash-bind below
    // only being valid while the stored token_expire_at has not been cleared.
    let claims = match state.jwt_service.verify_no_exp(&jwt) {
        Ok(c) => c,
        Err(_) => return (StatusCode::UNAUTHORIZED, "not authenticated").into_response(),
    };

    // Confirm the presented JWT is the current bound session before renewing.
    match state.user_port.get_identity_by_token(&bcs_jwt::token_hash(&jwt)).await {
        Ok(Some(info)) if info.user_id == claims.sub => {}
        Ok(_) => return (StatusCode::UNAUTHORIZED, "not authenticated").into_response(),
        Err(e) => {
            warn!(error = %e, "refresh: identity lookup failed");
            return (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response();
        }
    };

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let new_claims = Claims {
        sub: claims.sub.clone(),
        src: claims.src.clone(),
        iat: now,
        exp: now + state.config.idle_timeout_secs(),
    };
    let new_jwt = match state.jwt_service.sign(&new_claims) {
        Ok(j) => j,
        Err(e) => {
            warn!(error = %e, "refresh: JWT signing failed");
            return (StatusCode::INTERNAL_SERVER_ERROR, "session renewal failed").into_response();
        }
    };

    // Rebind the stored hash to the new JWT; the old JWT is now invalid.
    if let Err(e) = state
        .user_port
        .update_token(&new_claims.sub, &bcs_jwt::token_hash(&new_jwt), new_claims.exp)
        .await
    {
        warn!(error = %e, "refresh: update_token failed");
        return (StatusCode::INTERNAL_SERVER_ERROR, "session renewal failed").into_response();
    }

    let cookie = session_cookie(&new_jwt, state.config.cookie_secure);
    (StatusCode::OK, [("set-cookie", cookie)]).into_response()
}

/// Response body for `GET /auth/user` and `GET /auth/user/{user_id}`.
#[derive(Serialize)]
pub struct UserInfoResponse {
    pub user_id: String,
    pub name: Option<String>,
    pub provider: String,
    pub avatar: Option<String>,
}

/// GET /auth/user — "Who am I?"
///
/// Identity is resolved solely through the request-time auth chain, which
/// already encapsulates every authentication source:
///
/// - `oauth_session` plugin: verifies the `bcs_session` cookie JWT against the
///   identity store and carries `user_name` / `avatar` from the resolved
///   `UserIdentityInfo` row (no extra IO beyond the chain itself).
/// - `local` plugin (non-OAuth / mock): resolves a principal from config or
///   `X-Mock-*` headers.
///
/// This keeps `/auth/user` source-agnostic: new authentication plugins work
/// here without changes, and there is no duplicated JWT/cookie logic. 401 is
/// returned when the chain yields no identity OR yields a principal whose
/// `user_id` is `None`/empty (a non-human principal is not a human login;
/// returning 200 with an empty user_id would make the caller appear logged
/// in), preserving the `require_authentication` semantics.
pub async fn current_user_handler(
    State(state): State<Arc<OAuthRouteState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let Some(chain) = state.auth_chain.as_ref() else {
        return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "not authenticated"})))
            .into_response();
    };

    match chain.authenticate(&headers).await {
        Ok(result) => match result.principal {
            // A principal without a non-empty `user_id` is not a human login
            // (e.g. a bot-only principal). `/auth/user` is the "who am I?"
            // endpoint for human users: returning 200 with an empty user_id
            // would make the frontend treat the caller as logged in. Treat
            // `None` / empty the same as an anonymous request.
            Some(principal)
                if principal
                    .user_id
                    .as_deref()
                    .is_some_and(|id| !id.is_empty()) =>
            {
                (StatusCode::OK, Json(UserInfoResponse {
                    user_id: principal.user_id.unwrap(),
                    name: principal.user_name,
                    provider: principal
                        .source_name
                        .unwrap_or_else(|| "chain".to_string()),
                    avatar: principal.avatar,
                }))
                    .into_response()
            }
            _ => (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "not authenticated"})))
                .into_response(),
        },
        Err(e) => {
            warn!(error = %e, "auth chain failed in /auth/user");
            (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response()
        }
    }
}

/// GET /auth/user/{user_id} — Look up a user by ID (self only).
///
/// The path `user_id` must match the JWT subject; otherwise 403.
pub async fn get_user_handler(
    State(state): State<Arc<OAuthRouteState>>,
    Path(user_id): Path<String>,
    headers: HeaderMap,
) -> impl IntoResponse {
    // 1. Extract JWT from cookie, verify signature + expiration
    let jwt = match extract_session_cookie(&headers) {
        Some(t) => t,
        None => return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "not authenticated"}))).into_response(),
    };

    let claims = match state.jwt_service.verify(&jwt) {
        Ok(c) => c,
        Err(_) => return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "not authenticated"}))).into_response(),
    };

    // 2. Authorize: path user_id must match JWT subject
    if claims.sub != user_id {
        return (StatusCode::FORBIDDEN, Json(serde_json::json!({"error": "forbidden"}))).into_response();
    }

    // 3. Look up user by user_id
    match state.user_port.get_identity_by_user_id(&user_id).await {
        Ok(Some(info)) => (StatusCode::OK, Json(UserInfoResponse {
            user_id: info.user_id,
            // Prefer the internal display name; fall back to external.
            name: info.user_name.or(info.external_user_name),
            provider: info.auth_source,
            avatar: info.avatar,
        })).into_response(),
        Ok(None) => (StatusCode::NOT_FOUND, Json(serde_json::json!({"error": "not found"}))).into_response(),
        Err(e) => {
            warn!(error = %e, "get_identity_by_user_id failed");
            (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use axum::body::to_bytes;
    use bcs_auth_api::{AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, AuthSource};
    use bcs_test_support::{NoopAuthPlugin, NoopUserIdentityPort};

    /// A chain plugin that always yields a principal with the given `user_id`
    /// (which may be `None`), so the empty/missing-user_id branch of
    /// `current_user_handler` is reachable without depending on any specific
    /// config-driven plugin's input validation.
    struct FixedUserPlugin {
        user_id: Option<String>,
    }

    #[async_trait]
    impl AuthPlugin for FixedUserPlugin {
        fn can_authenticate(&self, _headers: &HeaderMap) -> bool {
            true
        }
        async fn authenticate(
            &self,
            _headers: &HeaderMap,
        ) -> Result<Option<AuthPrincipal>, AuthError> {
            let mut p = AuthPrincipal::new(AuthSource::Local);
            p.user_id = self.user_id.clone();
            Ok(Some(p))
        }
        fn priority(&self) -> u8 {
            10
        }
        fn name(&self) -> &'static str {
            "fixed"
        }
    }

    fn chain_with(user_id: Option<String>) -> Arc<AuthPluginChain> {
        Arc::new(AuthPluginChain::new(vec![
            Box::new(FixedUserPlugin { user_id }),
            Box::new(NoopAuthPlugin),
        ]))
    }

    async fn run_current_user(chain: Arc<AuthPluginChain>) -> (StatusCode, serde_json::Value) {
        let state = Arc::new(OAuthRouteState::new_chain_only(
            Arc::new(NoopUserIdentityPort),
            chain,
        ));
        let resp = current_user_handler(State(state), HeaderMap::new())
            .await
            .into_response();
        let status = resp.status();
        let bytes = to_bytes(resp.into_body(), usize::MAX).await.expect("body");
        let body = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
        (status, body)
    }

    /// A principal whose `user_id` is `None` is not a human login — `/auth/user`
    /// must NOT return 200 with an empty user_id.
    #[tokio::test]
    async fn current_user_rejects_principal_without_user_id() {
        let (status, body) = run_current_user(chain_with(None)).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "no user_id => 401, got {body}");
    }

    /// A principal whose `user_id` is an empty string must likewise be treated
    /// as not authenticated, not as a logged-in user with id "".
    #[tokio::test]
    async fn current_user_rejects_principal_with_empty_user_id() {
        let (status, body) = run_current_user(chain_with(Some(String::new()))).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "empty user_id => 401, got {body}");
    }

    /// Regression guard: a populated user_id still returns 200.
    #[tokio::test]
    async fn current_user_accepts_principal_with_user_id() {
        let (status, body) = run_current_user(chain_with(Some("u-123".to_string()))).await;
        assert_eq!(status, StatusCode::OK, "real user_id => 200, got {body}");
        assert_eq!(body["user_id"], "u-123");
    }
}
