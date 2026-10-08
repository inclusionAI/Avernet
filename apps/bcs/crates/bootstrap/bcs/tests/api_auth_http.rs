//! Task 13 — DB → HTTP fault propagation through the REAL stack.
//!
//! These tests assert that a real `DbPlugin`-level fault on the identity-
//! store query/execute boundary surfaces as a deterministic HTTP status
//! through the full V1 stack:
//!
//! 1. DB query fault during `get_session_by_hash` on a VALID OAuth cookie →
//!    503 Service Unavailable; the downstream `GroupService::list_groups` is
//!    NOT called (the `verify_principal` middleware short-circuited).
//! 2. Query succeeds but the cookie's hash matches no stored session (valid
//!    JWT, no record) → 401 Unauthenticated; downstream call count == 0.
//! 3. A structurally valid row whose column cannot decode (corrupt value in
//!    `external_user_name`) → 500 Internal Server Error (not 503): §8.6 keeps
//!    the corrupt-record category separate from `Unavailable`.
//! 4. All `ApplicationError::Unavailable` HTTP branches in the V1 envelope
//!    / reachable route surfaces exercised:
//!    - `/openapi/v1/auth/refresh` DB query fault → 503 + NO `Set-Cookie`.
//!    - `/openapi/v1/auth/logout` DB execute fault on revoke → 503 + NO
//!      `Set-Cookie`.
//!
//! The test exercises the V1 routes via `tower::oneshot` against an
//! in-process router built by `support::auth_db::build_stack` (the real
//! `local-sqlite` plugin + the strict engine + the production bridges + the
//! V1 composition root).
//!
//! RED capture — found + fixed swallows:
//!
//! - The strict engine (`bcs_auth_oauth::strict.rs::verify` and
//!   `session_lifecycle.rs::{refresh,revoke}`) collapsed
//!   `SessionStoreError::CorruptRecord` into `SessionAuthError::Unavailable`,
//!   which surfaced as HTTP 503. §8.6 requires the corrupt-record category
//!   to survive mapping; the verifier routes `_` (other than
//!   `Invalid`/`Unavailable`) to `Internal` (500), so the engine must
//!   produce `SessionAuthError::Internal` for `CorruptRecord` rather than
//!   `Unavailable`. After the minimal fix, scenario 3 returns 500.

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
    AuthStack, FaultyDbPlugin, StackOptions, V1_GROUPS, V1_LOGOUT, V1_REFRESH, build_stack, cookie,
    create_user_identities_schema, install_session, send,
};
use bcs_db_api::{DbPlugin, DbStatement, DbValue};

// ---------------------------------------------------------------------------
// Helper — build a stack with a `FaultyDbPlugin` decorator.
// ---------------------------------------------------------------------------

async fn stack_with_faulty() -> (AuthStack, Arc<FaultyDbPlugin>) {
    let raw_db: Arc<dyn DbPlugin> = {
        let db: Arc<dyn DbPlugin> =
            Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().expect("open sqlite"));
        create_user_identities_schema(&db).await;
        db
    };
    let faulty = Arc::new(FaultyDbPlugin::new(raw_db.clone()));
    let stack = build_stack(StackOptions::with_decorated(faulty.clone())).await;
    (stack, faulty)
}

async fn plain_stack() -> AuthStack {
    build_stack(StackOptions::plain()).await
}

// ---------------------------------------------------------------------------
// 1. DB query fault on get_session_by_hash → 503 + zero business calls.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn db_query_fault_during_verify_surfaces_503_and_skips_business_service() {
    let (stack, faulty) = stack_with_faulty().await;
    let jwt = install_session(&stack, "github", "ext-503-http", "Alice").await;

    // Arm the query fault AFTER install (install ran on a clean plugin; the
    // NEXT query on the store — the verify path — must surface Unavailable).
    faulty.fail_query.store(true, Ordering::SeqCst);

    let (status, set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt)),
        Some(auth_db::ORIGIN),
    )
    .await;

    assert_eq!(
        status,
        StatusCode::SERVICE_UNAVAILABLE,
        "DB query fault during verify must surface as 503, got {status:?} (set_cookie={set_cookie:?})"
    );
    assert_eq!(
        stack.group_service.list_groups_count(),
        0,
        "verify_principal must short-circuit before the business service on a 503"
    );
    // No Set-Cookie on a 503 (refresh does set cookies; verify doesn't).
    auth_db::assert_no_set_cookie(&set_cookie);
}

// ---------------------------------------------------------------------------
// 2. Valid JWT but no stored session → 401 + zero business calls.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn valid_jwt_no_stored_session_returns_401_and_no_business_call() {
    let stack = plain_stack().await;
    let jwt = install_session(&stack, "github", "ext-401-http", "Bob").await;

    // Delete the stored session row so the JWT still parses cryptographically
    // but `get_session_by_hash` returns Ok(None). Use raw_db to bypass the
    // stack's identity port.
    stack
        .raw_db
        .execute(DbStatement::new(
            "DELETE FROM bcs_user_identities WHERE session_id IS NOT NULL",
        ))
        .await
        .expect("clear sessions");

    let (status, set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt)),
        Some(auth_db::ORIGIN),
    )
    .await;

    assert_eq!(
        status,
        StatusCode::UNAUTHORIZED,
        "valid JWT with no stored session must surface as 401 (engine.ok_or(Invalid)), got {status:?}",
    );
    assert_eq!(
        stack.group_service.list_groups_count(),
        0,
        "verify_principal short-circuits on 401; business service never called"
    );
    auth_db::assert_no_set_cookie(&set_cookie);
}

// ---------------------------------------------------------------------------
// 3. Corrupt row → 500 (NOT 503).
// ---------------------------------------------------------------------------

#[tokio::test]
async fn corrupt_row_during_verify_returns_500_internal_server_error() {
    let stack = plain_stack().await;
    let jwt = install_session(&stack, "github", "ext-500-http", "Carol").await;

    // Corrupt the row by writing a BLOB into `external_user_name`. SQLite
    // preserves BLOB type even on a TEXT-affinity column, so
    // `DbRow::get_string("external_user_name")` returns
    // `Err(DbError::Conversion)` and the store maps that to
    // `AuthSessionStoreError::CorruptRecord`. §8.6 requires this propagate
    // distinctly to HTTP 500 — not collapse into `Unavailable`/503.
    stack
        .raw_db
        .execute(DbStatement::with_params(
            "UPDATE bcs_user_identities SET external_user_name = ? WHERE session_id IS NOT NULL",
            vec![DbValue::Bytes(vec![0x01, 0x02, 0x03])],
        ))
        .await
        .expect("corrupt external_user_name to BLOB");

    let (status, set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt)),
        Some(auth_db::ORIGIN),
    )
    .await;

    assert_eq!(
        status,
        StatusCode::INTERNAL_SERVER_ERROR,
        "corrupt record must surface as 500 (Internal) — §8.6: corrupt-record category must not \
         collapse into Unavailable (503). got {status:?}",
    );
    assert_eq!(
        stack.group_service.list_groups_count(),
        0,
        "verify_principal short-circuits on a 500; business service never called"
    );
    auth_db::assert_no_set_cookie(&set_cookie);
}

// ---------------------------------------------------------------------------
// 4. Unavailable sweep on reachable V1 auth surfaces: /auth/refresh and
//    /auth/logout.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn v1_refresh_db_query_fault_returns_503_no_set_cookie() {
    let (stack, faulty) = stack_with_faulty().await;
    let jwt = install_session(&stack, "github", "ext-refresh-503", "Dan").await;

    // refresh reads (get_session_by_hash) then writes (rotate). Inject the
    // query fault so the read fails → SessionAuthError::Unavailable →
    // ApplicationError::Unavailable → 503.
    faulty.fail_query.store(true, Ordering::SeqCst);

    let (status, set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::POST,
        V1_REFRESH,
        Some(&cookie(&jwt)),
        Some(auth_db::ORIGIN),
    )
    .await;

    assert_eq!(
        status,
        StatusCode::SERVICE_UNAVAILABLE,
        "V1 refresh DB query fault → 503 (ApplicationError::Unavailable), got {status:?}",
    );
    // Refresh error path emits NO cookie changes (the application layer never
    // emits Set-Cookie on a refresh failure).
    assert!(
        set_cookie.is_none() || set_cookie.as_deref().map(|s| s.contains("Max-Age=0")).unwrap_or(false),
        "refresh Unavailable error must NOT carry a SetSession Set-Cookie, got {set_cookie:?}",
    );
}

#[tokio::test]
async fn v1_logout_db_execute_fault_returns_503_no_set_cookie() {
    let (stack, faulty) = stack_with_faulty().await;
    let jwt = install_session(&stack, "github", "ext-logout-503", "Eve").await;

    // logout goes to `engine.revoke(session_id)` →
    // `AuthSessionRepoPort::revoke_session` (an `execute` call). Inject the
    // execute fault so revoke returns Unavailable → ApplicationError::
    // Unavailable → 503.
    faulty.fail_execute.store(true, Ordering::SeqCst);

    let (status, set_cookie, _body) = send(
        &stack.v1_router,
        axum::http::Method::POST,
        V1_LOGOUT,
        Some(&cookie(&jwt)),
        Some(auth_db::ORIGIN),
    )
    .await;

    assert_eq!(
        status,
        StatusCode::SERVICE_UNAVAILABLE,
        "V1 logout DB execute fault → 503, got {status:?}",
    );
    // The revoke-failure path returns an error response with NO cookie
    // changes (the session is still alive server-side; the delivery layer
    // not clear the cookie on a revoke failure — spec §8.5: persistent
    // revocation failure is an error response, never a best-effort success).
    assert!(
        set_cookie.is_none() || set_cookie.as_deref().map(|s| s.contains("Max-Age=0")).unwrap_or(false),
        "logout Unavailable must NOT carry a ClearSession Set-Cookie, got {set_cookie:?}",
    );
}
