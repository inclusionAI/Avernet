//! OAuth HTTP routes: /auth/url, /auth/callback/{provider}, /auth/refresh,
//! /auth/logout, /auth/user, /auth/user/{user_id} (legacy entrypoint).
//!
//! Task 11 (spec §8.4/§8.5 + §9 兼容性范围修正): the legacy entrypoint is a
//! DELIVERY ADAPTER only. The on-the-wire business logic (state consumption,
//! code exchange, identity install, session rotation/revocation) lives in the
//! SHARED injected `bcs_service_api::application::v1::AuthService` — the same
//! secure service the V1 entrypoint uses, under the `legacy` flow namespace.
//! The old in-facade business logic (state store, exchange/identity/JWT
//! plumbing, unconditional `update_token` writes) is gone; there is no
//! bypass for writing attacker-chosen identity into the shared `bcs_session`.
//!
//! Preserved old shapes: `/auth/url` body, 302 redirect + `Set-Cookie`
//! callback envelope, refresh/logout plain statuses, `/auth/user[/{id}]`
//! responses. Documented tightenings: callbacks without the browser-binding
//! challenge cookie are rejected, refresh/logout require a matching Origin on
//! cookie-backed requests (fail-closed when no allow-list is configured),
//! and a persistent logout/refresh failure is an error response, never a
//! best-effort success.

use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use bcs_auth_api::{OAuthConfig, UserIdentityPort};
use bcs_jwt::JwtService;
use bcs_service_api::application::v1::{
    ApplicationError, AuthProviderUrlList, AuthService, BrowserLoginBinding, BuildLoginUrls,
    CompleteOAuthLogin, PresentedSession,
};

use crate::oauth::cookies::{
    CookieProtocol, no_store, origin_matches, strict_unique_cookie_value, unique_cookie_value,
};

/// Shared state for the legacy OAuth routes. Holds the injected secure
/// `AuthService`, the read-only identity reader for `/auth/user/{user_id}`,
/// the non-OAuth fallback chain for `/auth/user`, and the cookie protocol.
/// It implements NO auth business logic.
pub struct OAuthRouteState {
    /// The shared secure auth service (legacy flow namespace). `None` only
    /// for the identity-only mount, which serves just `/auth/user`.
    pub auth_service: Option<Arc<dyn AuthService>>,
    pub jwt_service: JwtService,
    pub user_port: Arc<dyn UserIdentityPort>,
    pub config: OAuthConfig,
    /// Non-OAuth fallback identity source for `/auth/user`. When a request
    /// has no valid `bcs_session` cookie (or OAuth is not configured at
    /// all), `current_user_handler` resolves the caller via this chain.
    pub auth_chain: Option<Arc<bcs_auth_api::AuthPluginChain>>,
    pub cookies: CookieProtocol,
    /// Compat-mode trusted browser origins (the finite exact
    /// `cors.allowed_origins` set injected by bootstrap; wildcard and `null`
    /// entries excluded). `None` means cookie-backed refresh/logout are
    /// rejected fail-closed.
    pub trusted_browser_origins: Option<Arc<Vec<String>>>,
}

impl OAuthRouteState {
    /// Full OAuth mount: the legacy entry of the shared secure service.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        auth_service: Arc<dyn AuthService>,
        jwt_secret: &str,
        user_port: Arc<dyn UserIdentityPort>,
        config: OAuthConfig,
        auth_chain: Option<Arc<bcs_auth_api::AuthPluginChain>>,
        trusted_browser_origins: Option<Arc<Vec<String>>>,
    ) -> Self {
        let cookies = CookieProtocol::from_base_url(&config.base_url);
        Self {
            auth_service: Some(auth_service),
            jwt_service: JwtService::new(jwt_secret),
            user_port,
            config,
            auth_chain,
            cookies,
            trusted_browser_origins,
        }
    }

    /// Identity-only state for the no-OAuth case: `/auth/user` is backed
    /// solely by the auth chain. The `JwtService` is unused on this path —
    /// the cookie lookup is only attempted when `config.jwt_secret` is
    /// non-empty, which this state leaves empty.
    pub fn new_chain_only(
        user_port: Arc<dyn UserIdentityPort>,
        auth_chain: Arc<bcs_auth_api::AuthPluginChain>,
    ) -> Self {
        Self {
            auth_service: None,
            jwt_service: JwtService::new(""),
            user_port,
            config: OAuthConfig::default(),
            auth_chain: Some(auth_chain),
            cookies: CookieProtocol::new(false),
            trusted_browser_origins: None,
        }
    }
}

/// Build the legacy OAuth router for the shared `/auth/*` endpoints.
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
/// (e.g. the local mock plugin) can still ask "who am I?" without mounting
/// the OAuth protocol routes.
pub fn identity_routes(state: Arc<OAuthRouteState>) -> Router {
    Router::new()
        .route("/auth/user", get(current_user_handler))
        .with_state(state)
}

/// Map application errors to the legacy plain-text error envelope. Statuses
/// and bodies preserve the pre-switchover behavior where the old facade had
/// an equivalent branch.
fn legacy_error_response(error: &ApplicationError) -> (StatusCode, &'static str) {
    let (status, body) = match error {
        ApplicationError::InvalidInput { code, .. } => match code.as_str() {
            "oauth_exchange_failed" => (StatusCode::INTERNAL_SERVER_ERROR, "token exchange failed"),
            "oauth_userinfo_failed" => {
                (StatusCode::INTERNAL_SERVER_ERROR, "userinfo request failed")
            }
            _ => (StatusCode::BAD_REQUEST, "invalid state"),
        },
        ApplicationError::NotFound { .. } => (StatusCode::NOT_FOUND, "provider not found"),
        ApplicationError::Unauthenticated => (StatusCode::UNAUTHORIZED, "not authenticated"),
        ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. } => {
            (StatusCode::FORBIDDEN, "forbidden")
        }
        ApplicationError::Unavailable(_) => (StatusCode::SERVICE_UNAVAILABLE, "internal error"),
        // Old plain-text provider failure bodies: keep the 500 + body the
        // pre-switchover facade emitted for exchange/userinfo faults.
        ApplicationError::BadGateway { code, .. } => {
            if code == "oauth_userinfo_failed" {
                (StatusCode::INTERNAL_SERVER_ERROR, "userinfo request failed")
            } else {
                (StatusCode::INTERNAL_SERVER_ERROR, "token exchange failed")
            }
        }
        ApplicationError::Internal(_)
        | ApplicationError::Conflict { .. }
        | ApplicationError::Gone { .. }
        | ApplicationError::QuotaExceeded { .. }
        | ApplicationError::PayloadTooLarge { .. }
        | ApplicationError::Unprocessable { .. } => {
            (StatusCode::INTERNAL_SERVER_ERROR, "internal error")
        }
    };
    (status, body)
}

/// Notify-and-map: every application error is logged with the request id so
/// diagnostics can correlate both entrypoints (old diagnostics contract).
fn legacy_error(error: &ApplicationError, context: &'static str) -> (StatusCode, &'static str) {
    warn!(
        request_id = %bcs_observability::CurrentRequestId,
        error = %error,
        "OAuth {context} failed"
    );
    legacy_error_response(error)
}

/// GET /auth/url — legacy provider login URL list with a challenge cookie.
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
    _headers_in: HeaderMap,
) -> axum::response::Response {
    let Some(service) = state.auth_service.as_ref() else {
        return (StatusCode::NOT_FOUND, "not found").into_response();
    };

    let mut cookie_headers = HeaderMap::new();
    let reply = service
        .login_urls(BuildLoginUrls {
            callback_base_url: legacy_callback_base_url(&state.config),
        })
        .await;
    state
        .cookies
        .apply_cookie_changes(&mut cookie_headers, &reply.cookie_changes);

    match reply.result {
        Ok(list) => {
            let providers = list
                .into_iter()
                .map(|provider| ProviderUrl {
                    name: provider.name,
                    url: provider.url,
                })
                .collect();
            let mut response = (StatusCode::OK, Json(AuthUrlResponse { providers })).into_response();
            append_cookies(&mut response, cookie_headers);
            no_store(response.headers_mut());
            response
        }
        Err(error) => {
            let (status, body) = legacy_error(&error, "login url");
            let mut response = (status, body).into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
    }
}

fn legacy_callback_base_url(config: &OAuthConfig) -> String {
    format!(
        "{}/auth/callback",
        config.base_url.trim().trim_end_matches('/')
    )
}

fn append_cookies(response: &mut axum::response::Response, cookie_headers: HeaderMap) {
    for (_, value) in cookie_headers {
        response
            .headers_mut()
            .append(axum::http::header::SET_COOKIE, value);
    }
}

/// GET /auth/callback/{provider}?code=...&state=...
pub async fn callback_handler(
    State(state): State<Arc<OAuthRouteState>>,
    Path(provider_name): Path<String>,
    params: axum::extract::Query<CallbackParams>,
    headers_in: HeaderMap,
) -> axum::response::Response {
    let CallbackParams { code: query_code, auth_code, state: query_state } = params.0;
    let Some(service) = state.auth_service.as_ref() else {
        return (StatusCode::NOT_FOUND, "not found").into_response();
    };

    // 1. Browser-binding cookie (spec §8.4): missing / duplicate / empty →
    //    invalid state reject BEFORE any service interaction. A query-string
    //    nonce can never substitute for the cookie.
    let binding_nonce = match unique_cookie_value(&headers_in, state.cookies.challenge_cookie_name())
    {
        Some(nonce) => nonce,
        None => {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                "OAuth callback: invalid state"
            );
            return (StatusCode::BAD_REQUEST, "invalid state").into_response();
        }
    };

    // 2. The authorization code is REQUIRED (Alipay sends `auth_code`).
    let Some(code) = query_code.or_else(|| auth_code.clone()) else {
        warn!(
            request_id = %bcs_observability::CurrentRequestId,
            "OAuth callback: missing code or auth_code"
        );
        return (StatusCode::BAD_REQUEST, "missing code or auth_code").into_response();
    };

    let mut cookie_headers = HeaderMap::new();
    let reply = service
        .complete_login(CompleteOAuthLogin {
            provider: provider_name,
            code,
            auth_code,
            state: query_state,
            callback_base_url: legacy_callback_base_url(&state.config),
            browser_binding: BrowserLoginBinding { nonce: binding_nonce },
        })
        .await;
    state
        .cookies
        .apply_cookie_changes(&mut cookie_headers, &reply.cookie_changes);

    match reply.result {
        Ok(redirect) => {
            info!("OAuth login successful");
            let mut response = (
                StatusCode::FOUND,
                [("location", redirect.location)],
            )
                .into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
        Err(error) => {
            let (status, body) = legacy_error(&error, "callback");
            let mut response = (status, body).into_response();
            // A matched consume ALWAYS clears the challenge, even when a
            // later step failed (spec §8.4).
            append_cookies(&mut response, cookie_headers);
            response
        }
    }
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

/// POST /auth/refresh — sliding session renewal via the shared service.
/// Requires the `bcs_session` cookie (never a JSON body, never a Gateway
/// identity) and, when a cookie is presented, a matching trusted Origin.
pub async fn refresh_handler(
    State(state): State<Arc<OAuthRouteState>>,
    headers: HeaderMap,
) -> axum::response::Response {
    let Some(service) = state.auth_service.as_ref() else {
        return (StatusCode::NOT_FOUND, "not found").into_response();
    };

    // Strict carrier extraction (review 2026-09-20 #6): truly absent AND
    // present-but-malformed (blank / duplicate) both reject; the malformed
    // case must never be classified as "no cookie".
    let token = match strict_unique_cookie_value(&headers, state.cookies.session_cookie_name()) {
        Ok(Some(token)) => token,
        _ => {
            return (StatusCode::UNAUTHORIZED, "not authenticated").into_response();
        }
    };
    if !check_cookie_origin(&state, headers.get("origin")) {
        return (StatusCode::FORBIDDEN, "forbidden").into_response();
    }

    let mut cookie_headers = HeaderMap::new();
    let reply = service
        .refresh_session(PresentedSession { token })
        .await;
    state
        .cookies
        .apply_cookie_changes(&mut cookie_headers, &reply.cookie_changes);

    match reply.result {
        Ok(_) => {
            let mut response = (StatusCode::OK, "ok").into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
        Err(error) => {
            let (status, body) = legacy_error(&error, "refresh");
            let mut response = (status, body).into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
    }
}

/// POST /auth/logout — revoke (when a session was presented) and clear the
/// cookie through the shared service. Without a cookie this stays the old
/// idempotent success; WITH a cookie it now requires a matching Origin, and
/// a persistent revocation failure is an error — no false success.
pub async fn logout_handler(
    State(state): State<Arc<OAuthRouteState>>,
    headers: HeaderMap,
) -> axum::response::Response {
    let Some(service) = state.auth_service.as_ref() else {
        return (StatusCode::NOT_FOUND, "not found").into_response();
    };

    // The idempotent no-cookie success stays reserved for requests that
    // TRULY carry no session carrier (spec §8.5 revoke semantics). A
    // PRESENT-but-malformed carrier (blank / duplicated `bcs_session`)
    // must reject (review 2026-09-20 #6) — never a false success that
    // leaves the user's real session alive server-side.
    let token = match strict_unique_cookie_value(&headers, state.cookies.session_cookie_name()) {
        Ok(token) => token,
        Err(_) => {
            return (StatusCode::UNAUTHORIZED, "not authenticated").into_response();
        }
    };
    if token.is_some() && !check_cookie_origin(&state, headers.get("origin")) {
        return (StatusCode::FORBIDDEN, "forbidden").into_response();
    }

    let mut cookie_headers = HeaderMap::new();
    let reply = service
        .logout(token.map(|token| PresentedSession { token }))
        .await;
    state
        .cookies
        .apply_cookie_changes(&mut cookie_headers, &reply.cookie_changes);

    match reply.result {
        Ok(()) => {
            let mut response = (StatusCode::OK, "ok").into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
        Err(error) => {
            let (status, body) = legacy_error(&error, "logout");
            let mut response = (status, body).into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
    }
}

/// Compat-mode Cookie-Origin rule (spec §7.4/§8.3): a cookie-backed unsafe
/// request MUST carry a matching Origin from the finite exact allow-list.
/// NO list configured → reject (fail-closed tightening, spec §9).
fn check_cookie_origin(state: &OAuthRouteState, origin: Option<&HeaderValue>) -> bool {
    match state.trusted_browser_origins.as_ref() {
        None => {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                "cookie-backed auth request rejected: no trusted browser origins configured"
            );
            false
        }
        Some(allowed) if origin_matches(allowed, origin) => true,
        Some(_) => {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                "cookie-backed auth request rejected: origin mismatch"
            );
            false
        }
    }
}

/// Response body for `GET /auth/user` and `GET /auth/user/{user_id}`.
#[derive(Serialize)]
pub struct UserInfoResponse {
    pub user_id: String,
    pub name: Option<String>,
    pub provider: String,
    pub avatar: Option<String>,
}

/// GET /auth/user — "Who am I?" (unchanged legacy behavior; this is the
/// non-OAuth fallback, NOT the OAuth-issued session's identity path).
pub async fn current_user_handler(
    State(state): State<Arc<OAuthRouteState>>,
    headers: HeaderMap,
) -> axum::response::Response {
    let Some(chain) = state.auth_chain.as_ref() else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error": "not authenticated"})),
        )
            .into_response();
    };

    match chain.authenticate(&headers).await {
        Ok(result) => match result.principal {
            // A principal without a non-empty `user_id` is not a human login
            // (e.g. a bot-only principal). `/auth/user` is the "who am I?"
            // endpoint for human users: returning 200 with an empty user_id
            // would make the frontend treat the caller as logged in.
            Some(principal)
                if principal
                    .user_id
                    .as_deref()
                    .is_some_and(|id| !id.is_empty()) =>
            {
                (
                    StatusCode::OK,
                    Json(UserInfoResponse {
                        user_id: principal.user_id.unwrap(),
                        name: principal.user_name,
                        provider: principal.source_name.unwrap_or_else(|| "chain".to_string()),
                        avatar: principal.avatar,
                    }),
                )
                    .into_response()
            }
            _ => (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "not authenticated"})),
            )
                .into_response(),
        },
        Err(e) => {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                error = %e,
                "auth chain failed in /auth/user"
            );
            (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response()
        }
    }
}

/// GET /auth/user/{user_id} — Look up a user by ID (self only).
/// The path `user_id` must match the JWT subject; otherwise 403.
/// (Unchanged legacy behavior; reads display info only, no session logic.)
pub async fn get_user_handler(
    State(state): State<Arc<OAuthRouteState>>,
    Path(user_id): Path<String>,
    headers: HeaderMap,
) -> axum::response::Response {
    // 1. Extract JWT from cookie, verify signature + expiration
    let Some(jwt) = bcs_auth_api::extract_session_cookie(&headers) else {
        return (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error": "not authenticated"})),
        )
            .into_response();
    };

    let claims = match state.jwt_service.verify(&jwt) {
        Ok(c) => c,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "not authenticated"})),
            )
                .into_response();
        }
    };

    // 2. Authorize: path user_id must match JWT subject
    if claims.sub != user_id {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"error": "forbidden"})),
        )
            .into_response();
    }

    // 3. Look up user by user_id (read-only display projection)
    match state.user_port.get_identity_by_user_id(&user_id).await {
        Ok(Some(info)) => (
            StatusCode::OK,
            Json(UserInfoResponse {
                user_id: info.user_id,
                // Prefer the internal display name; fall back to external.
                name: info.user_name.or(info.external_user_name),
                provider: info.auth_source,
                avatar: info.avatar,
            }),
        )
            .into_response(),
        Ok(None) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": "not found"})),
        )
            .into_response(),
        Err(e) => {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                error = %e,
                "get_identity_by_user_id failed"
            );
            (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response()
        }
    }
}
