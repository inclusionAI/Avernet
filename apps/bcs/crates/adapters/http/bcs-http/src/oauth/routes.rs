//! Legacy OAuth HTTP route handlers (`/auth/*`, plan Task 11).
//!
//! Thin by contract: THIS layer extracts the temporary challenge cookie,
//! the session cookie, the request method, and the Origin header, then
//! calls the injected secure [`AuthService`] (LEGACY flow namespace) and
//! consumes the reply's cookie instructions. The response SHAPES of the
//! legacy entry (plain-text errors, the `/auth/url` JSON provider list,
//! the 302 callback redirect, the `bcs_session` set/clear cookies) are
//! preserved; the business semantics behind them are the secured,
//! browser-bound, atomic session lifecycle shared with the V1 entry.

use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use tracing::warn;

use bcs_auth_api::{extract_session_cookie, BCS_SESSION_COOKIE};
use bcs_service_api::application::v1::{
    ApplicationError, AuthFlowReply, BrowserCookieChange, BrowserLoginBinding, BuildLoginUrls,
    CompleteOAuthLogin, PresentedSession,
};

use super::{OAuthRouteState, challenge_cookie_name};

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

/// GET /auth/url — issue the browser-bound provider login URLs and the
/// temporary challenge cookie that binds the browser to the pending batch.
#[derive(Serialize)]
pub struct AuthUrlResponse {
    pub providers: Vec<ProviderUrl>,
}

#[derive(Serialize)]
pub struct ProviderUrl {
    pub name: String,
    pub url: String,
}

/// Encode one cookie instruction into a `Set-Cookie` header value. The
/// production challenge uses the fixed `__Host-` name; plain HTTP (local
/// development) uses the separate non-`__Host` name — never a downgraded
/// `__Host-` cookie.
pub(super) fn encode_cookie_change(change: &BrowserCookieChange, secure: bool) -> axum::http::HeaderValue {
    let secure_attr = if secure { "; Secure" } else { "" };
    let value = match change {
        BrowserCookieChange::SetLoginChallenge { nonce, .. } => format!(
            "{}={nonce}; Path=/; HttpOnly{secure_attr}; SameSite=Lax; Max-Age={}",
            challenge_cookie_name(secure),
            super::CHALLENGE_MAX_AGE_SECS
        ),
        BrowserCookieChange::ClearLoginChallenge => format!(
            "{}=; Path=/; HttpOnly{secure_attr}; SameSite=Lax; Max-Age=0",
            challenge_cookie_name(secure)
        ),
        BrowserCookieChange::SetSession { token, .. } => {
            format!("{BCS_SESSION_COOKIE}={token}; HttpOnly{secure_attr}; SameSite=Lax; Path=/")
        }
        BrowserCookieChange::ClearSession => format!(
            "{BCS_SESSION_COOKIE}=; HttpOnly{secure_attr}; SameSite=Lax; Path=/; Max-Age=0"
        ),
    };
    axum::http::HeaderValue::from_str(&value).expect("encoded Set-Cookie value is header-safe")
}

/// Consume the reply: run the success/error mapping LAST, then APPEND each
/// cookie instruction as its own `Set-Cookie` header (append, never insert:
/// success sets the session WHILE clearing the challenge) and stamp
/// `Cache-Control: no-store` — credentials never live in a shared cache.
fn apply_flow_reply<T>(
    reply: AuthFlowReply<T>,
    config: &bcs_auth_api::OAuthConfig,
    on_success: impl FnOnce(T) -> axum::response::Response,
    on_error: impl FnOnce(ApplicationError) -> axum::response::Response,
) -> axum::response::Response {
    let result = reply.result;
    let mut response = match result {
        Ok(payload) => on_success(payload),
        Err(error) => on_error(error),
    };
    for change in &reply.cookie_changes {
        // The session override must never downgrade an HTTPS challenge.
        // Apply the same public-URL mode for both challenge set and clear.
        let secure = match change {
            BrowserCookieChange::SetLoginChallenge { .. }
            | BrowserCookieChange::ClearLoginChallenge => {
                bcs_auth_api::OAuthConfig::default_cookie_secure(&config.base_url)
            }
            BrowserCookieChange::SetSession { .. } | BrowserCookieChange::ClearSession => {
                config.cookie_secure
            }
        };
        response.headers_mut().append(
            axum::http::header::SET_COOKIE,
            encode_cookie_change(change, secure),
        );
    }
    response.headers_mut().insert(
        axum::http::header::CACHE_CONTROL,
        axum::http::HeaderValue::from_static("no-store"),
    );
    response
}

/// Legacy plain-text error responses (shape preserved from the pre-security
/// entry): stable category text only — never third-party or storage text.
fn legacy_error(error: ApplicationError) -> axum::response::Response {
    let (status, body): (StatusCode, &'static str) = match &error {
        ApplicationError::Unauthenticated => (StatusCode::UNAUTHORIZED, "not authenticated"),
        ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. } => {
            (StatusCode::FORBIDDEN, "forbidden")
        }
        ApplicationError::NotFound { .. } => (StatusCode::NOT_FOUND, "provider not found"),
        ApplicationError::InvalidInput { code, .. } if code == "invalid_login_state" => {
            (StatusCode::BAD_REQUEST, "invalid state")
        }
        ApplicationError::InvalidInput { code, .. } if code == "invalid_callback_base_url" => {
            (StatusCode::BAD_REQUEST, "invalid state")
        }
        ApplicationError::InvalidInput { code, .. } if code == "missing_code" => {
            (StatusCode::BAD_REQUEST, "missing code or auth_code")
        }
        ApplicationError::BadGateway { code, .. } if code == "token_exchange_failed" => (
            StatusCode::INTERNAL_SERVER_ERROR,
            "token exchange failed",
        ),
        ApplicationError::BadGateway { code, .. } if code == "userinfo_request_failed" => (
            StatusCode::INTERNAL_SERVER_ERROR,
            "userinfo request failed",
        ),
        ApplicationError::Conflict { .. } => {
            (StatusCode::CONFLICT, "login session conflict")
        }
        ApplicationError::InvalidInput { .. } => (StatusCode::BAD_REQUEST, "invalid request"),
        _ => (StatusCode::INTERNAL_SERVER_ERROR, "internal error"),
    };
    (status, body).into_response()
}

/// The unique, REQUIRED browser-challenge cookie value (plan §8.4):
/// missing / duplicated / empty is an `invalid state` rejection BEFORE the
/// service is entered; a nonce in the query never substitutes. `secure`
/// selects the ONE accepted name for this transport mode — the other
/// mode's name is NEVER accepted, so a `Domain`-scoped local-name cookie
/// planted by a hostile subdomain cannot satisfy the production binding.
pub(super) fn extract_login_challenge(headers: &HeaderMap, secure: bool) -> Result<String, ()> {
    let expected_name = challenge_cookie_name(secure);
    let mut found: Option<String> = None;
    for value in headers.get_all(axum::http::header::COOKIE) {
        let raw = value.to_str().map_err(|_| ())?;
        for pair in raw.split(';') {
            let mut parts = pair.splitn(2, '=');
            let name = match parts.next() {
                Some(name) => name.trim(),
                None => continue,
            };
            let is_challenge = name == expected_name;
            if !is_challenge {
                continue;
            }
            let value = match parts.next() {
                Some(value) => value.trim().to_string(),
                None => return Err(()),
            };
            if value.is_empty() || found.replace(value).is_some() {
                return Err(());
            }
        }
    }
    found.ok_or(())
}

/// Present the session cookie under the carried adapter obligations:
/// `None` = no cookie at all (the valid idempotent boundary), `Err(())` =
/// the cookie exists but is EMPTY (a malformed credential — never an
/// empty token handed to revoke/refresh).
pub(super) fn presented_session_cookie(headers: &HeaderMap) -> Result<Option<String>, ()> {
    // `extract_session_cookie` returns the raw value if the cookie exists;
    // an empty value must be treated as invalid without reaching the
    // engine.
    let cookie = extract_session_cookie(headers);
    match cookie {
        None => Ok(None),
        Some(value) if value.is_empty() => Err(()),
        Some(value) => Ok(Some(value)),
    }
}

/// The injected service for the mounted login family. `None` only exists on
/// the identity-only mount (serving `/auth/user` alone), so reaching a login
/// route without a service is an internal misconfiguration.
fn flow_service(state: &OAuthRouteState) -> Result<Arc<dyn bcs_service_api::application::v1::AuthService>, axum::response::Response> {
    match state.require_auth_service() {
        Some(service) => Ok(Arc::clone(service)),
        None => {
            warn!(request_id = %bcs_observability::CurrentRequestId, category = "internal_error", "login family route reached without an injected auth service");
            Err((StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response())
        }
    }
}

/// Cookie-kind Origin gate for the legacy unsafe writes (refresh/logout,
/// plan §8.3 compatibility tightening): the request must present exactly
/// one UTF-8 Origin that is an EXACT member of the bootstrap-injected
/// trusted set. Missing Origin, duplicated/malformed Origin, an
/// untrusted Origin, and an empty trusted set all REJECT. No inference
/// from Host/Referer, and a Gateway identity never substitutes.
pub(super) fn cookie_unsafe_origin_allowed(headers: &HeaderMap, trusted: &[String]) -> bool {
    if trusted.is_empty() {
        return false;
    }
    let mut values = headers.get_all(axum::http::header::ORIGIN).iter();
    let Some(first) = values.next() else {
        return false;
    };
    if values.next().is_some() {
        return false;
    }
    match first.to_str() {
        Ok(origin) => trusted.iter().any(|allowed| allowed == origin),
        Err(_) => false,
    }
}

pub async fn auth_url_handler(
    State(state): State<Arc<OAuthRouteState>>,
) -> impl IntoResponse {
    let Ok(service) = flow_service(&state) else {
        return (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response();
    };
    let callback_base_url = format!("{}/auth/callback", state.config.base_url.trim_end_matches('/'));
    let reply = service.login_urls(BuildLoginUrls { callback_base_url }).await;
    apply_flow_reply(
        reply,
        &state.config,
        |result| {
            let providers = result
                .providers
                .into_iter()
                .map(|provider| ProviderUrl { name: provider.name, url: provider.url })
                .collect();
            Json(AuthUrlResponse { providers }).into_response()
        },
        legacy_error,
    )
}

pub async fn callback_handler(
    State(state): State<Arc<OAuthRouteState>>,
    Path(provider_name): Path<String>,
    axum::extract::Query(params): axum::extract::Query<CallbackParams>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let Ok(service) = flow_service(&state) else {
        return (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response();
    };

    // The temporary cookie binding comes FIRST: the callback GET is
    // protected by this binding plus the one-time state, never by the
    // write-CSRF path.
    let nonce = match extract_login_challenge(&headers, state.challenge_cookie_secure()) {
        Ok(nonce) => nonce,
        Err(()) => {
            warn!(request_id = %bcs_observability::CurrentRequestId, category = "invalid_state", "OAuth callback: browser challenge cookie missing or invalid");
            return (StatusCode::BAD_REQUEST, "invalid state").into_response();
        }
    };

    let callback_base_url = format!("{}/auth/callback", state.config.base_url.trim_end_matches('/'));
    let reply = service
        .complete_login(CompleteOAuthLogin {
            provider: provider_name,
            code: params.code,
            auth_code: params.auth_code,
            state: params.state,
            callback_base_url,
            browser_binding: BrowserLoginBinding { nonce },
        })
        .await;
    apply_flow_reply(
        reply,
        &state.config,
        |location| (StatusCode::FOUND, [("location", location.location)]).into_response(),
        legacy_error,
    )
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

/// POST /auth/logout — revoke the presented OAuth session by session
/// instance, THEN clear the cookie. A persistence failure is never
/// reported as success (plan §8.2); a request with no cookie at all is
/// the valid idempotent boundary that never touches the port.
pub async fn logout_handler(
    State(state): State<Arc<OAuthRouteState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let Ok(service) = flow_service(&state) else {
        return (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response();
    };
    let session = match presented_session_cookie(&headers) {
        Ok(option) => option.map(|token| PresentedSession { token }),
        Err(()) => {
            warn!(request_id = %bcs_observability::CurrentRequestId, category = "unauthenticated", "logout rejected: empty session cookie");
            return (StatusCode::UNAUTHORIZED, "not authenticated").into_response();
        }
    };
    if session.is_some() && !cookie_unsafe_origin_allowed(&headers, &state.trusted_origins) {
        warn!(request_id = %bcs_observability::CurrentRequestId, category = "forbidden", "logout rejected: cookie origin not trusted");
        return (StatusCode::FORBIDDEN, "forbidden").into_response();
    }
    let reply = service.logout(session).await;
    apply_flow_reply(
        reply,
        &state.config,
        |_| StatusCode::OK.into_response(),
        legacy_error,
    )
}

/// POST /auth/refresh — rotate the presented OAuth session via the
/// engine's compare-and-swap lifecycle. The browser cookie is the ONLY
/// credential that authorizes this write; a Gateway identity never
/// substitutes, and the cookie Origin must be trusted.
pub async fn refresh_handler(
    State(state): State<Arc<OAuthRouteState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let Ok(service) = flow_service(&state) else {
        return (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response();
    };
    let token = match presented_session_cookie(&headers) {
        Ok(Some(token)) => token,
        Ok(None) => {
            return (StatusCode::UNAUTHORIZED, "not authenticated").into_response();
        }
        Err(()) => {
            warn!(request_id = %bcs_observability::CurrentRequestId, category = "unauthenticated", "refresh rejected: empty session cookie");
            return (StatusCode::UNAUTHORIZED, "not authenticated").into_response();
        }
    };
    if !cookie_unsafe_origin_allowed(&headers, &state.trusted_origins) {
        warn!(request_id = %bcs_observability::CurrentRequestId, category = "forbidden", "refresh rejected: cookie origin not trusted");
        return (StatusCode::FORBIDDEN, "forbidden").into_response();
    }
    let reply = service.refresh_session(PresentedSession { token }).await;
    apply_flow_reply(
        reply,
        &state.config,
        |_| StatusCode::OK.into_response(),
        legacy_error,
    )
}

/// Response body for `GET /auth/user` and `GET /auth/user/{user_id}`.
#[derive(Serialize)]
pub struct UserInfoResponse {
    pub user_id: String,
    pub name: Option<String>,
    pub provider: String,
    pub avatar: Option<String>,
}

/// GET /auth/user — "Who am I?" (legacy identity reader, unchanged).
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
        Err(error) => {
            // AuthError is a closed, sanitized taxonomy (lookup failure
            // categories) — log the category marker, never a raw chain body.
            warn!(request_id = %bcs_observability::CurrentRequestId, category = "auth_chain_failed", "auth chain failed in /auth/user");
            let _ = &error;
            (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response()
        }
    }
}

/// GET /auth/user/{user_id} — Look up a user by ID (self only).
///
/// The presented `bcs_session` cookie is verified through the injected
/// auth chain — the SAME strict delegated path `/auth/user` uses: the
/// `oauth_session` source delegates to the STRICT engine (signature +
/// claim integrity + env binding + sid/revision + revocation, plan
/// §6/§8.5), with its one legacy `Option` compatibility exit preserved at
/// the boundary (a revoked, rotated-out, expired, or otherwise denied
/// credential is "not authenticated", not an error). The path `user_id`
/// must match the STRICTLY VERIFIED session subject; otherwise 403. The
/// response shapes stay the legacy ones.
pub async fn get_user_handler(
    State(state): State<Arc<OAuthRouteState>>,
    Path(user_id): Path<String>,
    headers: HeaderMap,
) -> impl IntoResponse {
    // 1. Strict delegated verification via the auth chain (the raw
    //    `JwtService::verify` — signature + expiry only — is gone: it
    //    accepted revoked and rotated-out cookies).
    let Some(chain) = state.auth_chain.as_ref() else {
        return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "not authenticated"}))).into_response();
    };
    let principal = match chain.authenticate(&headers).await {
        // The chain yields `Ok(None)`/empty user_id for a non-human or a
        // denied credential: exactly the legacy "not authenticated" shape.
        Ok(result) => match result.principal {
            Some(principal)
                if principal
                    .user_id
                    .as_deref()
                    .is_some_and(|id| !id.is_empty()) =>
            {
                principal
            }
            _ => {
                return (StatusCode::UNAUTHORIZED, Json(serde_json::json!({"error": "not authenticated"}))).into_response()
            }
        },
        Err(_error) => {
            // AuthError is a closed, sanitized taxonomy (lookup failure
            // categories) — log the category marker, never a raw chain body.
            warn!(request_id = %bcs_observability::CurrentRequestId, category = "auth_chain_failed", "auth chain failed in /auth/user/{user_id}");
            return (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response();
        }
    };

    // 2. Authorize: path user_id must match the verified session subject
    if principal.user_id.as_deref() != Some(user_id.as_str()) {
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
        Err(_) => {
            warn!(request_id = %bcs_observability::CurrentRequestId, category = "identity_lookup_failed", "get_identity_by_user_id failed");
            (StatusCode::INTERNAL_SERVER_ERROR, "internal error").into_response()
        }
    }
}
