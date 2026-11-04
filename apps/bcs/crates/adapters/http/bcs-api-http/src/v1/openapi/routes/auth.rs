//! V1 auth flow HTTP adapter (plan §8.2/§8.4).
//!
//! Thin by contract: THIS layer extracts the temporary challenge cookie,
//! the session cookie, the request method, and the request Origin; calls
//! the injected secure [`AuthService`]; and consumes the reply's
//! [`BrowserCookieChange`] instructions into SEPARATE appended
//! `Set-Cookie` headers (never a merged comma form). All business
//! semantics — provider enablement, the browser-bound one-time state,
//! code exchange, session install/rotate/revoke — live in the injected
//! application service.
//!
//! CSRF boundary: the callback GET is protected by the bound nonce plus
//! the one-time state, NOT by the write-CSRF path. `refresh`/`logout` are
//! cookie-authenticated unsafe writes and run Task 7's pinned-provenance
//! Origin check (a cookie-kind context against `api.auth.trusted_browser_origins`
//! via [`TrustedBrowserOrigins`]; no origins composed → fail closed). A
//! Gateway identity can NEVER substitute for the cookie on these routes.
//!
//! Cookie contract (plan §8.4): production challenge =
//! `__Host-bcs_oauth_login`, Path=/, HttpOnly, Secure, SameSite=Lax,
//! Max-Age=300; plain-HTTP local development uses a different,
//! non-`__Host` name; the session cookie keeps the existing `bcs_session`
//! name and flags; every flow response carries `Cache-Control: no-store`.

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, HeaderValue, StatusCode},
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use bcs_auth_api::BCS_SESSION_COOKIE;
use bcs_service_api::application::v1::{
    project_current_user, ApplicationError, AuthenticatedUserQuery, AuthFlowReply,
    BrowserLoginBinding, BuildLoginUrls, CompleteOAuthLogin, LogoutData,
    PresentedSession, SessionRenewalData,
};
use serde::Deserialize;
use serde_json::json;

use crate::v1::common::{
    application_error_response, encode_cookie_change, ApiState, Envelope, ErrorResponse,
    RequestId,
};

/// The pinned cookie-kind provenance for the OAuth session flows: the
/// request either presents the `bcs_session` cookie (whose value is a
/// session credential) or it does not — no other source can substitute.
mod csrf_kind {
    use crate::v1::common::{AuthenticationContext, CredentialKind};

    pub const SOURCE: &str = "oauth_session";

    pub fn context() -> AuthenticationContext {
        AuthenticationContext {
            source: SOURCE.to_string(),
            credential_kind: CredentialKind::OAuthSessionCookie,
        }
    }
}

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/url", get(login_urls))
        .route("/callback/{provider}", get(callback))
        .route("/user", get(current_user))
        .route("/refresh", post(refresh))
        .route("/logout", post(logout))
}

#[derive(Deserialize)]
struct CallbackParams {
    code: Option<String>,
    auth_code: Option<String>,
    state: String,
}

/// Apply the reply's cookie instructions as SEPARATE `Set-Cookie` headers
/// (append, never insert/merge) and stamp `Cache-Control: no-store`
/// (plan Task 11 step 3 — the login family never carries an authorized
/// GET in a shared cache).
fn apply_flow_reply<T>(
    reply: AuthFlowReply<T>,
    secure: bool,
    on_success: impl FnOnce(T) -> axum::response::Response,
    on_error: impl FnOnce(ApplicationError) -> axum::response::Response,
) -> axum::response::Response {
    let result = reply.result;
    let mut response = match result {
        Ok(payload) => on_success(payload),
        Err(error) => {
            let response = on_error(error);
            response
        }
    };
    for change in &reply.cookie_changes {
        // APPEND: success sets the session WHILE clearing the challenge —
        // both must survive as distinct Set-Cookie headers.
        response
            .headers_mut()
            .append(axum::http::header::SET_COOKIE, encode_cookie_change(change, secure));
    }
    response
        .headers_mut()
        .insert(axum::http::header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

/// Read the Origin header under the strict rules Task 7 fixed: none,
/// exactly one, and valid UTF-8 — or the caller must reject.
fn strict_origin<'a>(headers: &'a HeaderMap) -> Result<Option<&'a str>, ()> {
    let mut values = headers.get_all(axum::http::header::ORIGIN).iter();
    let Some(first) = values.next() else {
        return Ok(None);
    };
    if values.next().is_some() {
        return Err(());
    }
    first.to_str().map(Some).map_err(|_| ())
}

/// Shorthand replyless responses keep `ErrorResponse` for JSON handlers.
fn envelope_error(request_id: &RequestId, error: ApplicationError) -> axum::response::Response {
    application_error_response(request_id, error).into_response()
}

async fn login_urls(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;

    let callback_base_url = callback_base_url(&state);
    let reply = auth_service
        .login_urls(BuildLoginUrls { callback_base_url })
        .await;
    let success_request_id = request_id.0.clone();
    Ok(apply_flow_reply(
        reply,
        secure_transport(&state),
        move |result| Json(Envelope::success(20_000, "OK", result, success_request_id)).into_response(),
        |error| envelope_error(&request_id, error),
    ))
}

async fn callback(
    State(state): State<ApiState>,
    Path(provider): Path<String>,
    Query(params): Query<CallbackParams>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;

    // The one-time state plus the REQUIRED nonce cookie binding protects
    // this GET (plan §8.4): missing / duplicated / empty challenge cookie
    // is an `invalid_state` rejection BEFORE the service is entered. A
    // nonce in the query can never substitute.
    let nonce = match extract_login_challenge(&headers, secure_transport(&state)) {
        Ok(nonce) => nonce,
        Err(()) => {
            tracing::warn!(category = "invalid_state", "callback rejected: no browser challenge cookie");
            return Ok(envelope_error(
                &request_id,
                ApplicationError::invalid(
                    "invalid_state",
                    "browser login challenge cookie is missing or invalid",
                ),
            ));
        }
    };

    let reply = auth_service
        .complete_login(CompleteOAuthLogin {
            provider,
            code: params.code,
            auth_code: params.auth_code,
            state: params.state,
            callback_base_url: callback_base_url(&state),
            browser_binding: BrowserLoginBinding { nonce },
        })
        .await;
    Ok(apply_flow_reply(
        reply,
        secure_transport(&state),
        |location| {
            (
                StatusCode::FOUND,
                [("location", location.location)],
            )
                .into_response()
        },
        |error| envelope_error(&request_id, error),
    ))
}

async fn current_user(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);

    // Bootstrap selects the compat current-user chain or the explicit V1
    // chain before this call. Exactly one verifier runs, with no fallback
    // on failure; protected APIs always retain their own verifier.
    let verifier = state.auth_user_verifier.as_ref().unwrap_or(&state.principal_verifier);
    let verified = match verifier
        .verify(&headers)
        .await
    {
        Ok(verified) => verified,
        Err(error) => return Err(application_error_response(&request_id, verification_error(error))),
    };
    let query = AuthenticatedUserQuery {
        caller: verified.caller,
        provider_label: verified.authentication_context.source,
        avatar: verified.avatar,
    };
    let result = match state.auth_service.as_ref() {
        Some(auth_service) => auth_service.current_user(query).await,
        // No OAuth configured: still project the Human caller — the
        // login family fails closed, `/user` does not (plan §8.2).
        None => project_current_user(&query),
    };
    let user = result.map_err(|error| application_error_response(&request_id, error))?;

    Ok((
        [("cache-control", "no-store")],
        Json(Envelope::success(20_000, "OK", user, request_id.0)),
    ))
}

async fn refresh(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;

    // Cookie AUTH: the presented bcs_session cookie is the only
    // credential this route rotates; a Gateway identity can never
    // substitute (plan §8.2).
    let token = match presented_session_cookie(&headers) {
        Ok(Some(token)) => token,
        Ok(None) => {
            return Ok(envelope_error(&request_id, ApplicationError::Unauthenticated));
        }
        Err(()) => {
            tracing::warn!(category = "unauthenticated", "refresh rejected: malformed session cookie");
            return Ok(envelope_error(&request_id, ApplicationError::Unauthenticated));
        }
    };
    // Pinned-provenance CSRF (plan §7.4): unsafe method + cookie kind.
    if let Err(error) = cookie_unsafe_csrf(&state, &headers).await {
        return Ok(envelope_error(&request_id, error));
    }

    let reply = auth_service.refresh_session(PresentedSession { token }).await;
    let success_request_id = request_id.0.clone();
    Ok(apply_flow_reply(
        reply,
        secure_transport(&state),
        move |_: SessionRenewalData| {
            Json(Envelope::success(20_000, "OK", json!({}), success_request_id)).into_response()
        },
        |error| envelope_error(&request_id, error),
    ))
}

async fn logout(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let auth_service = auth_service(&state, &request_id)?;

    // Missing cookie is the valid idempotent boundary; an EMPTY cookie
    // value is a malformed credential — an error, never an empty token
    // handed to revoke.
    let session = match presented_session_cookie(&headers) {
        Ok(option) => option.map(|token| PresentedSession { token }),
        Err(()) => {
            tracing::warn!(category = "unauthenticated", "logout rejected: malformed session cookie");
            return Ok(envelope_error(&request_id, ApplicationError::Unauthenticated));
        }
    };
    // Revoke-by-session-instance happens in the service; a persistence
    // failure propagates — logout never reports success on a failed
    // write (plan §8.2). Only a request that actually carries the
    // cookie credential is subject to the cookie-Origin gate.
    if session.is_some() {
        if let Err(error) = cookie_unsafe_csrf(&state, &headers).await {
            return Ok(envelope_error(&request_id, error));
        }
    }

    let reply = auth_service.logout(session).await;
    let success_request_id = request_id.0.clone();
    Ok(apply_flow_reply(
        reply,
        secure_transport(&state),
        move |_: LogoutData| {
            Json(Envelope::success(20_000, "OK", json!({}), success_request_id)).into_response()
        },
        |error| envelope_error(&request_id, error),
    ))
}

/// The required injected service for the login family; unconfigured OAuth
/// fails closed with `auth_not_configured` (plan §8.2) EXCEPT for
/// `/user`, which never goes through this helper.
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

async fn cookie_unsafe_csrf(state: &ApiState, headers: &HeaderMap) -> Result<(), ApplicationError> {
    let Some(trusted) = state.trusted_browser_origins.as_ref() else {
        // No configured origins: fail closed for cookie unsafe writes
        // (plan §8.3). NEVER infer an allowance from Host/Referer.
        tracing::warn!(category = "forbidden", "cookie unsafe write rejected: no trusted browser origins");
        return Err(ApplicationError::forbidden_code(
            "browser_origin_untrusted",
            "request origin is not trusted for cookie-authenticated writes",
        ));
    };
    let origin = strict_origin(headers).map_err(|_| {
        tracing::warn!(category = "forbidden", "cookie unsafe write rejected: malformed Origin header");
        ApplicationError::forbidden_code(
            "browser_origin_untrusted",
            "request origin is not trusted for cookie-authenticated writes",
        )
    })?;
    let method = axum::http::Method::POST;
    if trusted.validate(&method, origin, &csrf_kind::context()).is_err() {
        tracing::warn!(category = "forbidden", "cookie unsafe write rejected: untrusted origin");
        return Err(
            ApplicationError::forbidden_code(
                "browser_origin_untrusted",
                "request origin is not trusted for cookie-authenticated writes",
            ),
        );
    }
    Ok(())
}

/// Extract the `bcs_session` cookie under the carried adapter
/// obligations. `None` = no cookie at all; `Err(())` = the cookie exists
/// but its value is EMPTY or appears twice (malformed credential, never
/// an empty token handed to the service).
fn presented_session_cookie(headers: &HeaderMap) -> Result<Option<String>, ()> {
    let mut found: Option<String> = None;
    for value in headers.get_all(axum::http::header::COOKIE) {
        let raw = value.to_str().map_err(|_| ())?;
        for pair in raw.split(';') {
            let mut parts = pair.splitn(2, '=');
            let name = match parts.next() {
                Some(name) => name.trim(),
                None => continue,
            };
            if name != BCS_SESSION_COOKIE {
                continue;
            }
            let value = match parts.next() {
                Some(value) => value.trim(),
                None => return Err(()),
            };
            if value.is_empty() || found.replace(value.to_string()).is_some() {
                return Err(());
            }
        }
    }
    Ok(found)
}

/// The unique, REQUIRED browser-challenge cookie value (plan §8.4):
/// `Err(())` for missing / duplicated / empty; a query nonce never
/// substitutes. `secure` selects the ONE accepted name for this transport
/// mode (`__Host-` in production HTTPS, the separate local name over
/// plain HTTP): the OTHER mode's name is NEVER accepted, so over
/// production HTTPS a `Domain`-scoped `bcs_oauth_login` cookie planted by
/// a hostile subdomain cannot satisfy the binding (Login CSRF).
fn extract_login_challenge(headers: &HeaderMap, secure: bool) -> Result<String, ()> {
    let expected_name = crate::v1::common::challenge_cookie_name(secure);
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

fn verification_error(error: crate::v1::common::PrincipalVerificationError) -> ApplicationError {
    use crate::v1::common::PrincipalVerificationError;
    match error {
        PrincipalVerificationError::Missing | PrincipalVerificationError::Invalid(_) => {
            ApplicationError::Unauthenticated
        }
        PrincipalVerificationError::Forbidden(_) => ApplicationError::forbidden_code(
            "forbidden",
            "authenticated sources rejected the presented credentials",
        ),
        // Mirror the principal middleware's two-arm 5xx taxonomy (spec
        // §7.4): the Bot identity store fault keeps its long-standing
        // 502 / `bot_identity_unavailable` mapping, while the strict-auth
        // dependency outage is the 503 / `auth_dependency_unavailable`
        // class — current_user must not answer differently from the
        // middleware for the same variant.
        PrincipalVerificationError::Unavailable => ApplicationError::bad_gateway(
            "bot_identity_unavailable",
            "Bot identity store unavailable",
        ),
        PrincipalVerificationError::DependencyUnavailable => ApplicationError::unavailable(
            "auth_dependency_unavailable",
            "authentication dependency unavailable",
        ),
        PrincipalVerificationError::Internal(_) => ApplicationError::internal(
            "authentication internal failure",
        ),
    }
}

fn callback_base_url(state: &ApiState) -> String {
    format!(
        "{}/callback",
        state.auth_public_base_url.trim_end_matches('/')
    )
}

/// Production (`https`) vs local plain-HTTP cookie attribute set.
fn secure_transport(state: &ApiState) -> bool {
    state
        .auth_public_base_url
        .trim_start()
        .to_ascii_lowercase()
        .starts_with("https://")
}
