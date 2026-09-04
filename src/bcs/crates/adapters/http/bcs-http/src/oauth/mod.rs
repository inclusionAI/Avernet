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
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use bcs_auth_api::{extract_session_cookie, OAuthConfig, OAuthProvider, UserIdentityPort, BCS_SESSION_COOKIE};
use bcs_jwt::{Claims, JwtService};
use bcs_service_api::application::v1::{
    ApplicationError, AuthProviderUrl as V1AuthProviderUrl, AuthProviderUrlList, AuthRedirect,
    AuthService, AuthUserInfo, BuildLoginUrls, CompleteOAuthLogin, LogoutResult, LogoutSession,
    ReadCurrentUser, RefreshSession, SessionRenewal,
};
use bcs_service_api::application::RequestAuthHeaders;

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


fn headers_from_request_auth(input: &RequestAuthHeaders) -> Result<HeaderMap, ApplicationError> {
    let mut headers = HeaderMap::new();
    if let Some(value) = input.authorization.as_ref() {
        let header_value = HeaderValue::from_str(value).map_err(|_| {
            ApplicationError::invalid("invalid_authorization_header", "authorization header is invalid")
        })?;
        headers.insert(axum::http::header::AUTHORIZATION, header_value);
    }
    if let Some(value) = input.cookie.as_ref() {
        let header_value = HeaderValue::from_str(value).map_err(|_| {
            ApplicationError::invalid("invalid_cookie_header", "cookie header is invalid")
        })?;
        headers.insert(axum::http::header::COOKIE, header_value);
    }
    for (name, value) in &input.forwarded_headers {
        let header_name = HeaderName::from_bytes(name.as_bytes()).map_err(|_| {
            ApplicationError::invalid("invalid_forwarded_header", "forwarded header name is invalid")
        })?;
        let header_value = HeaderValue::from_str(value).map_err(|_| {
            ApplicationError::invalid("invalid_forwarded_header", "forwarded header value is invalid")
        })?;
        headers.insert(header_name, header_value);
    }
    Ok(headers)
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

#[async_trait::async_trait]
impl AuthService for OAuthRouteState {
    async fn login_urls(
        &self,
        request: BuildLoginUrls,
    ) -> Result<AuthProviderUrlList, ApplicationError> {
        let callback_base_url = request.callback_base_url.trim().trim_end_matches('/');
        if callback_base_url.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_callback_base_url",
                "callback_base_url must not be empty",
            ));
        }
        let mut providers = Vec::new();
        let mut names: Vec<&str> = self.providers.keys().map(|s| s.as_str()).collect();
        names.sort();
        for name in names {
            if let Some(provider) = self.providers.get(name) {
                let csrf_state = self.state_store.generate(name).await;
                let redirect_uri = format!("{callback_base_url}/{name}");
                let url = provider.auth_url(&csrf_state, &redirect_uri);
                providers.push(V1AuthProviderUrl {
                    name: name.to_string(),
                    url,
                });
            }
        }
        Ok(AuthProviderUrlList { providers })
    }

    async fn complete_login(
        &self,
        request: CompleteOAuthLogin,
    ) -> Result<AuthRedirect, ApplicationError> {
        let callback_base_url = request.callback_base_url.trim().trim_end_matches('/');
        if callback_base_url.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_callback_base_url",
                "callback_base_url must not be empty",
            ));
        }

        let provider_key = self
            .state_store
            .consume(&request.state)
            .await
            .map_err(|_| ApplicationError::invalid("invalid_state", "invalid state"))?;

        if provider_key != request.provider {
            return Err(ApplicationError::invalid("provider_mismatch", "provider mismatch"));
        }

        let provider = self.providers.get(&request.provider).cloned().ok_or_else(|| {
            ApplicationError::not_found("provider_not_found", "provider not found")
        })?;

        let code = request.code.or(request.auth_code).ok_or_else(|| {
            ApplicationError::invalid("missing_code", "missing code or auth_code")
        })?;
        let redirect_uri = format!("{callback_base_url}/{}", request.provider);
        let token = provider.exchange_code(&code, &redirect_uri).await.map_err(|e| {
            warn!(error = %e, provider = %request.provider, "OAuth token exchange failed");
            ApplicationError::bad_gateway("token_exchange_failed", "token exchange failed")
        })?;

        let user_info = provider.get_user_info(&token).await.map_err(|e| {
            warn!(error = %e, provider = %request.provider, "OAuth userinfo failed");
            ApplicationError::bad_gateway("userinfo_request_failed", "userinfo request failed")
        })?;

        let user_id = self
            .user_port
            .ensure_identity(
                &request.provider,
                &user_info.id,
                user_info.name.as_deref(),
                user_info.avatar.as_deref(),
                &self.config.env,
            )
            .await
            .map_err(|e| {
                warn!(error = %e, "ensure_identity failed");
                ApplicationError::internal("identity creation failed")
            })?;

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let claims = Claims {
            sub: user_id,
            src: request.provider.clone(),
            iat: now,
            exp: now + self.config.idle_timeout_secs(),
            name: user_info.name.clone(),
        };
        let jwt = self.jwt_service.sign(&claims).map_err(|e| {
            warn!(error = %e, "JWT signing failed");
            ApplicationError::internal("session creation failed")
        })?;

        self.user_port
            .update_token(&claims.sub, &bcs_jwt::token_hash(&jwt), claims.exp)
            .await
            .map_err(|e| {
                warn!(error = %e, "update_token failed; aborting login");
                ApplicationError::internal("session creation failed")
            })?;

        info!(provider = %request.provider, user_id = %claims.sub, "OAuth login successful");
        Ok(AuthRedirect {
            location: self.config.success_redirect_location().to_string(),
            set_cookie: session_cookie(&jwt, self.config.cookie_secure),
        })
    }

    async fn current_user(&self, request: ReadCurrentUser) -> Result<AuthUserInfo, ApplicationError> {
        let Some(chain) = self.auth_chain.as_ref() else {
            return Err(ApplicationError::Unauthenticated);
        };
        let headers = headers_from_request_auth(&request.headers)?;
        match chain.authenticate(&headers).await {
            Ok(result) => match result.principal {
                Some(principal)
                    if principal
                        .user_id
                        .as_deref()
                        .is_some_and(|id| !id.is_empty()) =>
                {
                    Ok(AuthUserInfo {
                        user_id: principal.user_id.unwrap(),
                        name: principal.user_name,
                        provider: principal.source_name.unwrap_or_else(|| "chain".to_string()),
                        avatar: principal.avatar,
                    })
                }
                _ => Err(ApplicationError::Unauthenticated),
            },
            Err(e) => {
                warn!(error = %e, "auth chain failed in OpenAPI auth user");
                Err(ApplicationError::internal("auth chain failed"))
            }
        }
    }

    async fn refresh_session(&self, request: RefreshSession) -> Result<SessionRenewal, ApplicationError> {
        let headers = headers_from_request_auth(&request.headers)?;
        let jwt = extract_session_cookie(&headers).ok_or(ApplicationError::Unauthenticated)?;
        let claims = self
            .jwt_service
            .verify_no_exp(&jwt)
            .map_err(|_| ApplicationError::Unauthenticated)?;

        let info = match self.user_port.get_identity_by_token(&bcs_jwt::token_hash(&jwt)).await {
            Ok(Some(info)) if info.user_id == claims.sub => info,
            Ok(_) => return Err(ApplicationError::Unauthenticated),
            Err(e) => {
                warn!(error = %e, "refresh: identity lookup failed");
                return Err(ApplicationError::internal("identity lookup failed"));
            }
        };

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let iat = now.max(claims.iat.saturating_add(1));
        let new_claims = Claims {
            sub: claims.sub.clone(),
            src: claims.src.clone(),
            iat,
            exp: iat + self.config.idle_timeout_secs(),
            name: info.user_name.or(info.external_user_name),
        };
        let new_jwt = self.jwt_service.sign(&new_claims).map_err(|e| {
            warn!(error = %e, "refresh: JWT signing failed");
            ApplicationError::internal("session renewal failed")
        })?;
        self.user_port
            .update_token(&new_claims.sub, &bcs_jwt::token_hash(&new_jwt), new_claims.exp)
            .await
            .map_err(|e| {
                warn!(error = %e, "refresh: update_token failed");
                ApplicationError::internal("session renewal failed")
            })?;

        Ok(SessionRenewal {
            set_cookie: session_cookie(&new_jwt, self.config.cookie_secure),
        })
    }

    async fn logout(&self, request: LogoutSession) -> Result<LogoutResult, ApplicationError> {
        let headers = headers_from_request_auth(&request.headers)?;
        if let Some(jwt) = extract_session_cookie(&headers) {
            if let Ok(claims) = self.jwt_service.verify(&jwt) {
                if let Err(e) = self.user_port.update_token(&claims.sub, "", 0).await {
                    warn!(error = %e, user_id = %claims.sub, "logout: token revocation failed");
                }
            }
        }
        Ok(LogoutResult {
            set_cookie: clear_session_cookie(self.config.cookie_secure),
        })
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
        name: user_info.name.clone(),
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
            ("location", state.config.success_redirect_location().to_string()),
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
    let info = match state.user_port.get_identity_by_token(&bcs_jwt::token_hash(&jwt)).await {
        Ok(Some(info)) if info.user_id == claims.sub => info,
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
        name: info.user_name.or(info.external_user_name),
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
    use bcs_auth_api::{
        AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, AuthSource, UserIdentityInfo,
        UserIdentityPort,
    };
    use bcs_test_support::{MockOAuthProvider, NoopAuthPlugin, NoopUserIdentityPort};

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

    #[tokio::test]
    async fn auth_service_complete_login_issues_redirect_cookie_without_network() {
        let mut providers: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
        providers.insert(
            "google".to_string(),
            Arc::new(MockOAuthProvider::new("google", "external-123")),
        );
        let state = OAuthRouteState::new(
            "test-secret-key-at-least-32-bytes!!",
            Arc::new(NoopUserIdentityPort),
            providers,
            OAuthConfig {
                jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
                idle_timeout_minutes: 30,
                base_url: "https://bcs.example.com".to_string(),
                cookie_secure: false,
                env: "test".to_string(),
                success_redirect_path: "/".to_string(),
            },
            None,
        );

        let urls = state
            .login_urls(BuildLoginUrls {
                callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
            })
            .await
            .expect("login urls");
        let provider_url = &urls.providers[0].url;
        let state_param = provider_url
            .split('&')
            .find_map(|part| part.strip_prefix("state="))
            .expect("state query param")
            .to_string();

        let result = state
            .complete_login(CompleteOAuthLogin {
                provider: "google".to_string(),
                code: Some("auth-code".to_string()),
                auth_code: None,
                state: state_param,
                callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
            })
            .await
            .expect("complete login");

        assert_eq!(result.location, "/");
        assert!(result.set_cookie.starts_with("bcs_session="));
        assert!(result.set_cookie.contains("HttpOnly"));
        assert!(result.set_cookie.contains("SameSite=Lax"));

        // The issued `bcs_session` JWT must carry the user's display name so an
        // external JWT-only verifier (e.g. a gateway) can populate user_name.
        let jwt = result
            .set_cookie
            .strip_prefix("bcs_session=")
            .and_then(|rest| rest.split(';').next())
            .expect("bcs_session cookie value");
        let claims = JwtService::new("test-secret-key-at-least-32-bytes!!")
            .verify_no_exp(jwt)
            .expect("verify issued login jwt");
        assert_eq!(
            claims.name.as_deref(),
            Some("Mock User"),
            "login JWT must carry the provider's display name"
        );
    }


    #[tokio::test]
    async fn login_urls_rejects_empty_callback_base_url() {
        let mut providers: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
        providers.insert(
            "google".to_string(),
            Arc::new(MockOAuthProvider::new("google", "external-123")),
        );
        let state = OAuthRouteState::new(
            "test-secret-key-at-least-32-bytes!!",
            Arc::new(NoopUserIdentityPort),
            providers,
            OAuthConfig {
                jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
                idle_timeout_minutes: 30,
                base_url: "https://bcs.example.com".to_string(),
                cookie_secure: false,
                env: "test".to_string(),
                success_redirect_path: "/".to_string(),
            },
            None,
        );

        let err = state
            .login_urls(BuildLoginUrls {
                callback_base_url: "   ".to_string(),
            })
            .await
            .expect_err("empty callback base url should fail");

        assert_eq!(err.code(), "invalid_callback_base_url");
    }

    #[tokio::test]
    async fn complete_login_rejects_missing_code() {
        let mut providers: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
        providers.insert(
            "google".to_string(),
            Arc::new(MockOAuthProvider::new("google", "external-123")),
        );
        let state = OAuthRouteState::new(
            "test-secret-key-at-least-32-bytes!!",
            Arc::new(NoopUserIdentityPort),
            providers,
            OAuthConfig {
                jwt_secret: "test-secret-key-at-least-32-bytes!!".to_string(),
                idle_timeout_minutes: 30,
                base_url: "https://bcs.example.com".to_string(),
                cookie_secure: false,
                env: "test".to_string(),
                success_redirect_path: "/".to_string(),
            },
            None,
        );

        let urls = state
            .login_urls(BuildLoginUrls {
                callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
            })
            .await
            .expect("login urls");
        let provider_url = &urls.providers[0].url;
        let state_param = provider_url
            .split('&')
            .find_map(|part| part.strip_prefix("state="))
            .expect("state query param")
            .to_string();

        let err = state
            .complete_login(CompleteOAuthLogin {
                provider: "google".to_string(),
                code: None,
                auth_code: None,
                state: state_param,
                callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
            })
            .await
            .expect_err("missing code should fail");

        assert_eq!(err.code(), "missing_code");
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

    /// In-memory `UserIdentityPort` for refresh tests: binds one session token
    /// hash to a single user and serves its display name, so `refresh_session`
    /// (which reads `get_identity_by_token` and re-signs) can be exercised
    /// without `NoopUserIdentityPort` short-circuiting to `None`.
    struct BoundTokenIdentityPort {
        user_id: String,
        name: Option<String>,
        bound: tokio::sync::Mutex<Option<String>>,
    }

    impl BoundTokenIdentityPort {
        fn new(user_id: &str, name: Option<&str>) -> Self {
            Self {
                user_id: user_id.to_string(),
                name: name.map(str::to_string),
                bound: tokio::sync::Mutex::new(None),
            }
        }
    }

    #[async_trait]
    impl UserIdentityPort for BoundTokenIdentityPort {
        async fn ensure_identity(
            &self,
            _auth_source: &str,
            _external_user_id: &str,
            _external_user_name: Option<&str>,
            _avatar: Option<&str>,
            _env: &str,
        ) -> Result<String, AuthError> {
            Ok(self.user_id.clone())
        }

        async fn lookup_by_user_id(
            &self,
            _user_id: &str,
            _auth_source: &str,
        ) -> Result<Option<String>, AuthError> {
            Ok(Some(self.user_id.clone()))
        }

        async fn get_identity_by_token(
            &self,
            token_hash: &str,
        ) -> Result<Option<UserIdentityInfo>, AuthError> {
            let bound = self.bound.lock().await;
            if bound.as_deref() == Some(token_hash) {
                Ok(Some(UserIdentityInfo {
                    user_id: self.user_id.clone(),
                    auth_source: "google".to_string(),
                    user_name: self.name.clone(),
                    external_user_name: self.name.clone(),
                    avatar: None,
                }))
            } else {
                Ok(None)
            }
        }

        async fn get_identity_by_user_id(
            &self,
            _user_id: &str,
        ) -> Result<Option<UserIdentityInfo>, AuthError> {
            Ok(Some(UserIdentityInfo {
                user_id: self.user_id.clone(),
                auth_source: "google".to_string(),
                user_name: self.name.clone(),
                external_user_name: self.name.clone(),
                avatar: None,
            }))
        }

        async fn update_token(
            &self,
            _user_id: &str,
            token_hash: &str,
            _expire_at: u64,
        ) -> Result<(), AuthError> {
            *self.bound.lock().await = if token_hash.is_empty() {
                None
            } else {
                Some(token_hash.to_string())
            };
            Ok(())
        }
    }

    /// `refresh_session` must re-sign the JWT carrying the identity row's CURRENT
    /// display name (so an edited `user_name` re-syncs into the gateway-parsed
    /// cookie on sliding renewal), regardless of the presented JWT's own `name`.
    #[tokio::test]
    async fn refresh_session_emits_current_name_in_jwt() {
        let jwt_secret = "test-secret-key-at-least-32-bytes!!";
        let port = Arc::new(BoundTokenIdentityPort::new("u1", Some("The Name")));
        let state = OAuthRouteState::new(
            jwt_secret,
            port.clone(),
            HashMap::new(),
            OAuthConfig {
                jwt_secret: jwt_secret.to_string(),
                idle_timeout_minutes: 30,
                base_url: "https://bcs.example.com".to_string(),
                cookie_secure: false,
                env: "test".to_string(),
                success_redirect_path: "/".to_string(),
            },
            None,
        );

        // Issue and bind a login-style JWT; its `name` is irrelevant — refresh
        // re-derives the name from the identity row via `get_identity_by_token`.
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let svc = JwtService::new(jwt_secret);
        let login_jwt = svc
            .sign(&Claims {
                sub: "u1".to_string(),
                src: "google".to_string(),
                iat: now,
                exp: now + 60,
                name: None,
            })
            .expect("sign login jwt");
        port.update_token("u1", &bcs_jwt::token_hash(&login_jwt), now + 60)
            .await
            .expect("bind login token");

        let result = state
            .refresh_session(RefreshSession {
                headers: RequestAuthHeaders {
                    authorization: None,
                    cookie: Some(format!("bcs_session={login_jwt}")),
                    forwarded_headers: Vec::new(),
                },
            })
            .await
            .expect("refresh");
        assert!(result.set_cookie.starts_with("bcs_session="));

        let new_jwt = result
            .set_cookie
            .strip_prefix("bcs_session=")
            .and_then(|rest| rest.split(';').next())
            .expect("new bcs_session value");
        let claims = JwtService::new(jwt_secret)
            .verify_no_exp(new_jwt)
            .expect("verify renewed jwt");
        assert_eq!(claims.sub, "u1");
        assert_eq!(
            claims.name.as_deref(),
            Some("The Name"),
            "refresh must re-sign the JWT with the identity row's current name"
        );
    }
}
