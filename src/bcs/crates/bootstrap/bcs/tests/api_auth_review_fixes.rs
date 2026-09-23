//! Review 2026-09-20 (~/tmp/bcs-v1-api-auth-pr-review-2026-09-20.md) —
//! HTTP-level regression tests for the findings fixed in this PR iteration:
//!
//! 1. `/openapi/v1/auth/user` propagates the verifier's TERMINAL errors
//!    (#2): store fault → 503, provider outside the chain allowlist → 403,
//!    malformed session cookie → 401 — never the blanket 401 "logged out"
//!    the old `if let Ok` swallow produced.
//! 2. Ambiguous session-carrier extraction (#6): blank / duplicated
//!    `bcs_session` carriers reject on refresh AND logout (both the V1 and
//!    the legacy entrypoints) — the idempotent logout success stays
//!    reserved for requests that truly carry no cookie, so a duplicate
//!    carrier can never leave the user's live session unrevoke.
//! 3. `/auth/user` keeps returning the identity avatar under the explicit
//!    `[api.auth]` chain (#7): the strict snapshot's avatar travels as
//!    trusted DISPLAY metadata (`DisplayMetadata`), never as authorization
//!    input.
//! 4. Bad callback queries surface through the standard V1 error envelope
//!    (#3): missing / blank / duplicated query parameters are answered by
//!    the handler, not by a generic Axum extractor rejection.
//!
//! All tests run against the REAL stack harness in
//! `tests/support/auth_db.rs` (production V1 assembly, real sqlite plugin,
//! real strict engine, both mounted routers).

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

#[path = "support/auth_db.rs"]
mod auth_db;

use std::sync::atomic::Ordering;
use std::sync::Arc;

use axum::http::StatusCode;

use auth_db::{
    AuthStack, FaultyDbPlugin, StackOptions, V1_CALLBACK_GITHUB, V1_LOGOUT, V1_REFRESH, V1_USER,
    build_stack, cookie, create_user_identities_schema, install_session,
    install_session_with_avatar, send, LEGACY_LOGOUT, ORIGIN, PROVIDER,
};

async fn plain_stack() -> AuthStack {
    build_stack(StackOptions::plain()).await
}

async fn stack_with_faulty() -> (AuthStack, Arc<FaultyDbPlugin>) {
    let raw_db: Arc<dyn bcs_db_api::DbPlugin> = {
        let db: Arc<dyn bcs_db_api::DbPlugin> =
            Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().expect("open sqlite"));
        create_user_identities_schema(&db).await;
        db
    };
    let faulty = Arc::new(FaultyDbPlugin::new(raw_db.clone()));
    let stack = build_stack(StackOptions::with_decorated(faulty.clone())).await;
    (stack, faulty)
}

/// helper: extract `data.error_code` from the response body.
fn error_code(body: &Option<String>) -> String {
    let body = body.as_deref().unwrap_or("");
    serde_json::from_str::<serde_json::Value>(body)
        .expect("envelope body parses")
        .get("data")
        .and_then(|data| data.get("error_code"))
        .and_then(serde_json::Value::as_str)
        .expect("data.error_code present")
        .to_string()
}

// ---------------------------------------------------------------------------
// #2 — /auth/user terminal error propagation
// ---------------------------------------------------------------------------

/// OAuth store fault DURING the verify of a presented session cookie must
/// surface as 503 (Unavailable), not a misleading 401 "logged out".
#[tokio::test]
async fn auth_user_surfaces_store_fault_as_503() {
    let (stack, faulty) = stack_with_faulty().await;
    let jwt = install_session(&stack, PROVIDER, "ext-user-503", "Alice").await;

    // Arm the fault AFTER install; the NEXT store query is /auth/user's
    // verify path.
    faulty.fail_query.store(true, Ordering::SeqCst);

    let (status, set_cookie, body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_USER,
        Some(&cookie(&jwt)),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::SERVICE_UNAVAILABLE,
        "store fault during /auth/user verify must be 503, got {status:?} (body={body:?})"
    );
    assert_eq!(error_code(&body), "unavailable");
    auth_db::assert_no_set_cookie(&set_cookie);
}

/// A VALID session whose provider is NOT in the configured chain allowlist
/// (chain = ["github"], token src = "google") must surface the chain's
/// Forbidden as 403 — not the blanket 401.
#[tokio::test]
async fn auth_user_rejects_disallowed_provider_as_403() {
    let stack = plain_stack().await;
    // engine.install accepts any provider string; the CHAIN allowlist is
    // what must reject it.
    let foreign = install_session(&stack, "google", "ext-user-foreign", "Bob").await;

    let (status, _set_cookie, body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_USER,
        Some(&cookie(&foreign)),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::FORBIDDEN,
        "valid session of a non-enabled provider must be 403, got {status:?} (body={body:?})"
    );
    assert_eq!(error_code(&body), "forbidden");
}

/// A malformed session cookie on /auth/user stays the strict engine's 401
/// `unauthenticated` envelope (terminal — no fallback).
#[tokio::test]
async fn auth_user_rejects_malformed_cookie_as_401_envelope() {
    let stack = plain_stack().await;
    let (status, _set_cookie, body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_USER,
        Some("bcs_session=not-a-real-jwt"),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(error_code(&body), "unauthenticated");
}

// ---------------------------------------------------------------------------
// #7 — /auth/user preserves the avatar display field
// ---------------------------------------------------------------------------

#[tokio::test]
async fn auth_user_returns_session_avatar_as_trusted_display_data() {
    let stack = plain_stack().await;
    let jwt = install_session_with_avatar(
        &stack,
        PROVIDER,
        "ext-user-avatar",
        "Carol",
        Some("https://example.com/carol.png"),
    )
    .await;

    let (status, _set_cookie, body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_USER,
        Some(&cookie(&jwt)),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "body={body:?}");
    let body = body.expect("body present");
    let value: serde_json::Value = serde_json::from_str(&body).expect("json envelope");
    assert_eq!(value["data"]["avatar"], "https://example.com/carol.png");
    assert_eq!(value["data"]["provider"], PROVIDER);
}

// ---------------------------------------------------------------------------
// #6 — ambiguous carriers reject on refresh/logout (V1 + legacy)
// ---------------------------------------------------------------------------

/// Duplicated `bcs_session` carriers on logout must be REJECTED: the
/// service must not receive `logout(None)` (which reports a false success
/// while the user's real session keeps living server-side).
#[tokio::test]
async fn logout_with_duplicate_cookie_is_rejected_and_keeps_session_alive() {
    let stack = plain_stack().await;
    let jwt = install_session(&stack, PROVIDER, "ext-user-dup-logout", "Dave").await;

    let duplicated = format!("bcs_session={jwt}; bcs_session={jwt}", jwt = jwt);
    let (status, set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::POST,
        V1_LOGOUT,
        Some(&duplicated),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::UNAUTHORIZED,
        "duplicated carriers must reject, not take the no-cookie success path"
    );
    auth_db::assert_no_set_cookie(&set_cookie);

    // The original session is still LIVE: nothing was revoked.
    let (status, _set_cookie, body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_USER,
        Some(&cookie(&jwt)),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "session must survive the rejected logout; body={body:?}");
}

/// A blank `bcs_session=` carrier is a PRESENT-but-malformed credential:
/// logout rejects; it is not the idempotent no-cookie boundary.
#[tokio::test]
async fn logout_with_blank_cookie_is_rejected() {
    let stack = plain_stack().await;

    let (status, _set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::POST,
        V1_LOGOUT,
        Some("bcs_session="),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "blank carrier must reject");
}

/// The TRUE no-cookie logout keeps its idempotent 200 — the absent boundary
/// stays reserved for genuinely cookie-less requests.
#[tokio::test]
async fn logout_without_any_cookie_stays_idempotent_success() {
    let stack = plain_stack().await;

    let (status, _set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::POST,
        V1_LOGOUT,
        None,
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "cookie-less logout stays idempotent");
}

/// Duplicated carriers on refresh reject with 401 (V1).
#[tokio::test]
async fn refresh_with_duplicate_cookie_is_rejected() {
    let stack = plain_stack().await;
    let jwt = install_session(&stack, PROVIDER, "ext-user-dup-refresh", "Eve").await;

    let duplicated = format!("bcs_session={jwt}; bcs_session={jwt}", jwt = jwt);
    let (status, _set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::POST,
        V1_REFRESH,
        Some(&duplicated),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

/// The LEGACY entrypoint applies the same ambiguous-carrier rule.
#[tokio::test]
async fn legacy_logout_with_duplicate_cookie_is_rejected() {
    let stack = plain_stack().await;
    let jwt = install_session(&stack, PROVIDER, "ext-user-legacy-dup", "Frank").await;

    let duplicated = format!("bcs_session={jwt}; bcs_session={jwt}", jwt = jwt);
    let (status, _set_cookie, _body) = send(
        &stack.legacy_router,
        axum::http::Method::POST,
        LEGACY_LOGOUT,
        Some(&duplicated),
        Some(ORIGIN),
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
}

// ---------------------------------------------------------------------------
// #3 — bad callback queries answer through the V1 error envelope
// ---------------------------------------------------------------------------

/// A callback WITHOUT `state` must be answered by the handler with the
/// standard V1 400 envelope (`invalid_state`) — not a generic Axum
/// extractor rejection.
#[tokio::test]
async fn callback_without_state_uses_v1_error_envelope() {
    let stack = plain_stack().await;

    let (status, _set_cookie, body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        &format!("{V1_CALLBACK_GITHUB}?code=abc"),
        None,
        None,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(error_code(&body), "invalid_state");
}

/// A blank `state=` is the same reject.
#[tokio::test]
async fn callback_with_blank_state_uses_v1_error_envelope() {
    let stack = plain_stack().await;

    let (status, _set_cookie, body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        &format!("{V1_CALLBACK_GITHUB}?code=abc&state=%20"),
        None,
        None,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(error_code(&body), "invalid_state");
}

/// Duplicated `state` parameters are ambiguous input → `invalid_request`.
#[tokio::test]
async fn callback_with_duplicated_state_uses_v1_error_envelope() {
    let stack = plain_stack().await;

    let (status, _set_cookie, body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        &format!("{V1_CALLBACK_GITHUB}?code=abc&state=one&state=two"),
        None,
        None,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(error_code(&body), "invalid_request");
}

/// Unreadable Cookie headers must not become Missing/no-cookie success. An
/// active session is seeded to prove that rejected requests neither rotate
/// nor revoke it, including when a valid and invalid header coexist.
#[tokio::test]
async fn unreadable_cookie_headers_reject_on_both_session_entrypoints() {
    use axum::body::{Body, to_bytes};
    use axum::http::{HeaderValue, Request};
    use tower::ServiceExt;

    let stack = plain_stack().await;
    let jwt = install_session(&stack, PROVIDER, "ext-unreadable-cookie", "Alice").await;
    let valid = HeaderValue::from_str(&cookie(&jwt)).expect("valid cookie");
    let invalid = HeaderValue::from_bytes(b"bcs_session=\x80").expect("raw cookie");
    let mut combined = cookie(&jwt).into_bytes();
    combined.extend_from_slice(b"; other=\xff");
    for values in [
        vec![invalid.clone()],
        vec![invalid.clone(), valid.clone()],
        vec![valid, invalid],
        vec![HeaderValue::from_bytes(&combined).expect("combined cookie")],
    ] {
        for (router, path) in [
            (&stack.v1_router, V1_REFRESH),
            (&stack.v1_router, V1_LOGOUT),
            (&stack.legacy_router, auth_db::LEGACY_REFRESH),
            (&stack.legacy_router, LEGACY_LOGOUT),
        ] {
            let mut request = Request::builder()
                .method("POST")
                .uri(path)
                .header("origin", ORIGIN)
                .header("x-request-id", "unreadable-cookie")
                .body(Body::empty())
                .expect("request");
            for value in &values {
                request.headers_mut().append("cookie", value.clone());
            }
            let response = router.clone().oneshot(request).await.expect("response");
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED, "{path}");
            assert!(!response.headers().contains_key("set-cookie"), "{path}");
            if path.starts_with("/openapi/") {
                let bytes = to_bytes(response.into_body(), usize::MAX).await.expect("body");
                let body: serde_json::Value = serde_json::from_slice(&bytes).expect("envelope");
                assert_eq!(body["code"], 40_100);
                assert_eq!(body["data"]["error_code"], "unauthenticated");
                assert_eq!(body["request_id"], "unreadable-cookie");
            }
        }
    }
    let (status, _, _) = send(
        &stack.v1_router, axum::http::Method::GET, V1_USER,
        Some(&cookie(&jwt)), Some(ORIGIN),
    ).await;
    assert_eq!(status, StatusCode::OK, "rejected requests must not mutate the session");
}
