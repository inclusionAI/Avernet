use axum::{
    extract::{Path, RawQuery, State},
    http::{HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use bcs_service_api::application::v1::{
    ApplicationError, AuthProviderUrlList, AuthUserInfo, AuthenticatedUserQuery,
    BrowserLoginBinding, BuildLoginUrls, CompleteOAuthLogin, PresentedSession,
};
use serde_json::json;

use crate::v1::common::{
    ApiState, AuthenticationContext, CookieProtocol, CredentialKind, Envelope, ErrorResponse,
    PrincipalVerificationError, RequestId, application_error_response, no_store,
    strict_unique_cookie_value, unique_cookie_value,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/url", get(login_urls))
        .route("/callback/{provider}", get(callback))
        .route("/user", get(current_user))
        .route("/refresh", post(refresh))
        .route("/logout", post(logout))
}

/// Parsed `GET /callback/{provider}` query (review 2026-09-20 #3): the raw
/// query string is parsed IN the handler so that every missing / blank /
/// duplicated / undecodable parameter surfaces through the standard V1
/// error envelope instead of the generic Axum `Query` extractor rejection
/// (contract §5: bad callback query → 400 envelope, not a plain 400).
struct ParsedCallbackQuery {
    code: Option<String>,
    auth_code: Option<String>,
    state: Option<String>,
    /// A known query key appeared more than once — ambiguous input.
    duplicated: bool,
}

impl ParsedCallbackQuery {
    fn parse(raw_query: Option<&str>) -> Self {
        let mut parsed = Self {
            code: None,
            auth_code: None,
            state: None,
            duplicated: false,
        };
        if let Some(query) = raw_query {
            for (key, value) in url::form_urlencoded::parse(query.as_bytes()) {
                match key.as_ref() {
                    // A repeated carrier key is ambiguous input, not a
                    // last-one-wins override (contract §4: malformed
                    // credentials reject).
                    "code" => {
                        if parsed.code.is_some() {
                            parsed.duplicated = true;
                        } else {
                            parsed.code = Some(value.into_owned());
                        }
                    }
                    "auth_code" => {
                        if parsed.auth_code.is_some() {
                            parsed.duplicated = true;
                        } else {
                            parsed.auth_code = Some(value.into_owned());
                        }
                    }
                    "state" => {
                        if parsed.state.is_some() {
                            parsed.duplicated = true;
                        } else {
                            parsed.state = Some(value.into_owned());
                        }
                    }
                    // Providers append unrelated redirect parameters
                    // (e.g. `error=access_denied`); unknown keys are not
                    // part of the credential set and stay ignored.
                    _ => {}
                }
            }
        }
        parsed
    }
}

fn cookie_protocol(state: &ApiState) -> CookieProtocol {
    CookieProtocol::from_public_base_url(&state.auth_public_base_url)
}

fn forbidden(request_id: &RequestId) -> ErrorResponse {
    // The detail is intentionally not echoed here; the distinguishing cause
    // (no origins configured vs. mismatch) is logged by the caller.
    ErrorResponse::forbidden(request_id.0.clone())
}

/// Cookie-Origin rule for cookie-backed unsafe requests (spec §7.4/§8.3):
/// refresh/logout presenting a `bcs_session` cookie MUST carry a matching
/// Origin. A Gateway identity NEVER substitutes for the cookie, and a
/// missing/untrusted Origin is a 403 — fail-closed. When no trusted-origin
/// set is configured (neither `api.auth.trusted_browser_origins` nor the
/// compat-mode `cors.allowed_origins` injection), every cookie-backed
/// refresh/logout is rejected; this compat-mode tightening is documented in
/// the Task 11 report and the spec §9 兼容性范围修正.
fn check_cookie_origin(
    state: &ApiState,
    request_id: &RequestId,
    origin: Option<&HeaderValue>,
) -> Result<(), ErrorResponse> {
    let origins = state.trusted_browser_origins.as_ref().ok_or_else(|| {
        tracing::warn!(
            request_id = %request_id.0,
            "cookie-backed auth request rejected: no trusted browser origins configured"
        );
        forbidden(request_id)
    })?;
    // The presented credential here is always the OAuth session cookie:
    // refresh/logout authorize the COOKIE, never a Gateway principal.
    let context = AuthenticationContext {
        source: "oauth_session".to_string(),
        credential_kind: CredentialKind::OAuthSessionCookie,
    };
    origins
        .validate(&axum::http::Method::POST, origin, &context)
        .map_err(|_| {
            tracing::warn!(
                request_id = %request_id.0,
                "cookie-backed auth request rejected: origin mismatch"
            );
            forbidden(request_id)
        })
}

async fn login_urls(State(state): State<ApiState>, headers: HeaderMap) -> Response {
    let request_id = RequestId::from_headers(&headers);
    let service = match auth_service(&state, &request_id) {
        Ok(service) => service,
        Err(response) => return response.into_response(),
    };

    let cookie = cookie_protocol(&state);
    let mut cookie_headers = HeaderMap::new();
    let reply = service
        .login_urls(BuildLoginUrls {
            callback_base_url: callback_base_url(&state),
        })
        .await;
    cookie.apply_cookie_changes(&mut cookie_headers, &reply.cookie_changes);

    match reply.result {
        Ok(providers) => {
            // V1 JSON envelope unchanged: data keeps the {providers: [...]}
            // shape the auth facade has always returned.
            let mut response = (
                StatusCode::OK,
                Json(Envelope::success(
                    20_000,
                    "OK",
                    AuthProviderUrlList { providers },
                    request_id.0,
                )),
            )
                .into_response();
            append_cookies(&mut response, cookie_headers);
            no_store(response.headers_mut());
            response
        }
        Err(error) => {
            let mut response = application_error_response(&request_id, error).into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
    }
}

async fn callback(
    State(state): State<ApiState>,
    Path(provider): Path<String>,
    RawQuery(raw_query): RawQuery,
    headers: HeaderMap,
) -> Response {
    let request_id = RequestId::from_headers(&headers);
    let service = match auth_service(&state, &request_id) {
        Ok(service) => service,
        Err(response) => return response.into_response(),
    };
    let cookie = cookie_protocol(&state);

    // Bad callback query (duplicated carrier keys) → standard V1 400
    // envelope (review 2026-09-20 #3).
    let params = ParsedCallbackQuery::parse(raw_query.as_deref());
    if params.duplicated {
        return application_error_response(
            &request_id,
            ApplicationError::invalid(
                "invalid_request",
                "callback query carries duplicated parameters",
            ),
        )
        .into_response();
    }

    // The CSRF `state` is REQUIRED on every callback (contract §5). A
    // missing or blank state is a bad callback query, answered through the
    // standard V1 envelope — never an Axum extractor 400.
    let callback_state = match params.state.filter(|value| !value.trim().is_empty()) {
        Some(state) => state,
        None => {
            return application_error_response(
                &request_id,
                ApplicationError::invalid("invalid_state", "state is missing or blank"),
            )
            .into_response();
        }
    };

    // Browser-binding cookie (spec §8.4): missing / duplicate / empty →
    // invalid_state reject BEFORE any service interaction. The query-string
    // nonce can never substitute for the cookie.
    let binding_nonce = match unique_cookie_value(&headers, cookie.challenge_cookie_name()) {
        Some(nonce) => nonce,
        None => {
            return application_error_response(
                &request_id,
                ApplicationError::invalid(
                    "invalid_state",
                    "browser login binding is missing or invalid",
                ),
            )
            .into_response();
        }
    };

    // The authorization code is REQUIRED (validation Task 10 deferred to this
    // adapter): a callback without code/auth_code cannot start an exchange.
    let code = match params.code.clone().or_else(|| params.auth_code.clone()) {
        Some(code) if !code.trim().is_empty() => code,
        _ => {
            return application_error_response(
                &request_id,
                ApplicationError::invalid("missing_code", "missing code or auth_code"),
            )
            .into_response();
        }
    };

    let mut cookie_headers = HeaderMap::new();
    let reply = service
        .complete_login(CompleteOAuthLogin {
            provider,
            code,
            auth_code: params.auth_code,
            state: callback_state,
            callback_base_url: callback_base_url(&state),
            browser_binding: BrowserLoginBinding { nonce: binding_nonce },
        })
        .await;
    cookie.apply_cookie_changes(&mut cookie_headers, &reply.cookie_changes);

    match reply.result {
        Ok(redirect) => {
            let mut response = (StatusCode::FOUND, [("location", redirect.location)]).into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
        Err(error) => {
            let mut response = application_error_response(&request_id, error).into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
    }
}

async fn current_user(State(state): State<ApiState>, headers: HeaderMap) -> Response {
    let request_id = RequestId::from_headers(&headers);

    // 1. The NEW verifier chain first. TERMINAL verifier failures are
    //    propagated per the chain semantics (contract §5): only Missing may
    //    proceed to the legacy compatibility projection — Invalid must not
    //    fall into another identity chain, and Unavailable/Forbidden/Internal
    //    must surface as 503/403/500 instead of a misleading 401 "logged
    //    out". A verified identity without a Human part is a defined 403:
    //    this surface answers user info, it never re-runs another chain.
    match state.principal_verifier.verify(&headers).await {
        Ok(identity) if identity.caller.user.is_some() => {
            let provider_label = identity.authentication_context.source.clone();
            if let Some(service) = state.auth_service.as_ref() {
                let query = AuthenticatedUserQuery {
                    caller: identity.caller,
                    provider_label,
                    avatar: identity.display.avatar.clone(),
                };
                return match service.current_user(query).await {
                    Ok(info) => user_info_response(info, &request_id),
                    Err(error) => application_error_response(&request_id, error).into_response(),
                };
            }
            // No OAuth service configured: project the caller at the
            // delivery boundary (same semantics as the service projection;
            // "/user 仍能通过新 verifier 返回 Human").
            let user = identity.caller.user.expect("user presence checked above");
            let name = user.display_name.clone().or_else(|| user.full_name.clone());
            return user_info_response(
                AuthUserInfo {
                    user_id: user.id,
                    name,
                    provider: provider_label,
                    avatar: identity.display.avatar.clone(),
                },
                &request_id,
            );
        }
        Ok(_) => {
            // Verified, but the caller is not a Human (e.g. a Gateway bot
            // principal). Defined outcome, no legacy-chain replay.
            return application_error_response(
                &request_id,
                ApplicationError::forbidden("authenticated identity is not a human user"),
            )
            .into_response();
        }
        Err(PrincipalVerificationError::Missing) => { /* fall through to the legacy projection */ }
        Err(PrincipalVerificationError::Invalid) => {
            return application_error_response(&request_id, ApplicationError::Unauthenticated)
                .into_response();
        }
        Err(PrincipalVerificationError::Forbidden) => {
            return application_error_response(
                &request_id,
                ApplicationError::forbidden("authenticated source is not allowed"),
            )
            .into_response();
        }
        Err(PrincipalVerificationError::Unavailable) => {
            return application_error_response(
                &request_id,
                ApplicationError::Unavailable("session verification is unavailable".to_string()),
            )
            .into_response();
        }
        Err(PrincipalVerificationError::Internal) => {
            tracing::warn!(
                request_id = %request_id.0,
                "principal verifier internal error in auth user"
            );
            return application_error_response(
                &request_id,
                ApplicationError::internal("principal verification failed"),
            )
            .into_response();
        }
    }

    // 2. Old non-OAuth fallback (reached ONLY on Missing): resolve the Human
    //    through the legacy auth chain (old response shape preserved). A
    //    chain failure stays 500; "no human identity" propagates 401.
    if let Some(projection) = state.chain_user_projection.as_ref() {
        return match projection.current_user(&headers).await {
            Ok(info) => user_info_response(info, &request_id),
            Err(ApplicationError::Internal(detail)) => {
                tracing::warn!(
                    request_id = %request_id.0,
                    error = %detail,
                    "auth chain failed in OpenAPI auth user"
                );
                application_error_response(
                    &request_id,
                    ApplicationError::internal("auth chain failed"),
                )
                .into_response()
            }
            Err(error) => application_error_response(&request_id, error).into_response(),
        };
    }

    // 3. Nothing resolved. Preserve the legacy statuses: an unconfigured
    //    OAuth surface stays `auth_not_configured` 404; a configured surface
    //    answers `unauthenticated` 401.
    let error = if state.auth_service.is_none() {
        ApplicationError::not_found("auth_not_configured", "OAuth is not configured")
    } else {
        ApplicationError::Unauthenticated
    };
    application_error_response(&request_id, error).into_response()
}

fn user_info_response(info: AuthUserInfo, request_id: &RequestId) -> Response {
    (
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", info, request_id.0.clone())),
    )
        .into_response()
}

async fn refresh(State(state): State<ApiState>, headers: HeaderMap) -> Response {
    let request_id = RequestId::from_headers(&headers);
    let service = match auth_service(&state, &request_id) {
        Ok(service) => service,
        Err(response) => return response.into_response(),
    };
    let cookie = cookie_protocol(&state);

    // The session is parsed ONLY from the request cookie (never a JSON
    // body). Strict carrier extraction (review 2026-09-20 #6): truly absent
    // and present-but-malformed (blank / duplicate) both reject with 401;
    // the malformed case must never be classified as "no cookie".
    let token = match strict_unique_cookie_value(&headers, cookie.session_cookie_name()) {
        Ok(Some(token)) => token,
        _ => {
            return application_error_response(&request_id, ApplicationError::Unauthenticated)
                .into_response();
        }
    };
    if let Err(response) = check_cookie_origin(&state, &request_id, headers.get("origin")) {
        return response.into_response();
    }

    let mut cookie_headers = HeaderMap::new();
    let reply = service.refresh_session(PresentedSession { token }).await;
    cookie.apply_cookie_changes(&mut cookie_headers, &reply.cookie_changes);

    match reply.result {
        Ok(_renewal) => {
            let mut response = (
                StatusCode::OK,
                Json(Envelope::success(20_000, "OK", json!({}), request_id.0)),
            )
                .into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
        Err(error) => {
            let mut response = application_error_response(&request_id, error).into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
    }
}

async fn logout(State(state): State<ApiState>, headers: HeaderMap) -> Response {
    let request_id = RequestId::from_headers(&headers);
    let service = match auth_service(&state, &request_id) {
        Ok(service) => service,
        Err(response) => return response.into_response(),
    };
    let cookie = cookie_protocol(&state);

    // Same cookie-origin rule as refresh; a request that TRULY carries no
    // session cookie is not cookie-backed (idempotent logout stays
    // allowed, spec §8.5 revoke semantics). A PRESENT-but-malformed
    // carrier (blank / duplicated `bcs_session`) is NOT the idempotent
    // boundary (review 2026-09-20 #6): reject — the service must not
    // receive `logout(None)` while the user's real session keeps living.
    let token = match strict_unique_cookie_value(&headers, cookie.session_cookie_name()) {
        Ok(token) => token,
        Err(_) => {
            return application_error_response(&request_id, ApplicationError::Unauthenticated)
                .into_response();
        }
    };
    if token.is_some() {
        if let Err(response) = check_cookie_origin(&state, &request_id, headers.get("origin")) {
            return response.into_response();
        }
    }

    let mut cookie_headers = HeaderMap::new();
    let reply = service
        .logout(token.map(|token| PresentedSession { token }))
        .await;
    cookie.apply_cookie_changes(&mut cookie_headers, &reply.cookie_changes);

    match reply.result {
        Ok(()) => {
            let mut response = (
                StatusCode::OK,
                Json(Envelope::success(20_000, "OK", json!({}), request_id.0)),
            )
                .into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
        Err(error) => {
            // Persistent revocation failure is an ERROR (spec §8.5): no
            // success envelope, no promise the session is gone.
            let mut response = application_error_response(&request_id, error).into_response();
            append_cookies(&mut response, cookie_headers);
            response
        }
    }
}

/// Append every translated `Set-Cookie` header. `cookie_headers` is a
/// single-name HeaderMap, so only its FIRST entry carries a
/// `HeaderName` — subsequent entries of the same name come back with
/// `name = None` and MUST still be appended (distinct Set-Cookie headers
/// are never merged).
fn append_cookies(response: &mut Response, cookie_headers: HeaderMap) {
    for (_, value) in cookie_headers {
        response
            .headers_mut()
            .append(axum::http::header::SET_COOKIE, value);
    }
}

fn auth_service<'a>(
    state: &'a ApiState,
    request_id: &RequestId,
) -> Result<&'a std::sync::Arc<dyn bcs_service_api::application::v1::AuthService>, ErrorResponse> {
    state.auth_service.as_ref().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::not_found("auth_not_configured", "OAuth is not configured"),
        )
    })
}

fn callback_base_url(state: &ApiState) -> String {
    format!(
        "{}/callback",
        state.auth_public_base_url.trim_end_matches('/')
    )
}
