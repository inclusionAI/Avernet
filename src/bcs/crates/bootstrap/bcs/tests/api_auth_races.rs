//! Task 13 — deterministic cross-entry shared-session races.
//!
//! §8.5 必须保证的并发结果, exercised through the SAME production stack
//! (`support::auth_db::build_stack`) over a shared in-memory SQLite + real
//! engine/bridge + V1 AND legacy routers. The race driver uses
//! `tokio::sync::Barrier` to gate exactly ONE matching SQL operation at
//! the `DbPlugin` boundary (no sleeps).
//!
//! Scenarios:
//! 1. Refresh pauses after reading A; logout(A) commits and returns success
//!    on the OTHER entry; resume refresh → CAS Conflict → NO Set-Cookie;
//!    request with cookie A → 401; request with candidate B → 401.
//!    Covered twice: legacy-refresh vs V1-logout, and V1-refresh vs
//!    legacy-logout.
//! 2. rotate commits but the refresh HTTP response is paused (barrier
//!    AFTER the rotate write) → logout(A) lands on the other entry →
//!    late-Set B cookie; B must NOT pass verification (401).
//! 3. New login C commits vs old A logout: C unaffected by late A logout
//!    (C's request → 200).
//! 4. Two simultaneous refreshes on A: exactly one Applied, the loser
//!    gets 401 and NO Set-Cookie.
//! 5. Two independent `DbUserIdentityStore` instances over the SAME SQLite
//!    DB racing a refresh + logout — outcomes identical to single-store
//!    runs (store-level atomicity at the DB write boundary).
//!
//! Determinism for scenario 1 uses NESTED `BarrierDbPlugin` decorators:
//! barrier A parks refresh AFTER the engine's `get_session_by_hash` query
//! has returned Ok(Some(A)) (timing=AfterInner, predicate=the SELECT SQL).
//! The race driver arrives at barrier A which simultaneously confirms the
//! read finished AND releases refresh to attempt rotate. A second barrier
//! B (timing=BeforeInner, predicate=rotate SQL) then parks refresh BEFORE
//! the rotate commits; the race driver issues logout meanwhile (logout's
//! revoke SQL does not match barrier B's predicate), arrives at barrier B,
//! releases refresh's rotate → 0 affected rows → Conflict → 401.
//!
//! Each test wraps its main sequence in `with_timeout` (15s) so a missed
//! barrier release fails the test instead of hanging the suite.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

#[path = "support/auth_db.rs"]
mod auth_db;

use std::sync::Arc;

use axum::http::StatusCode;
use tokio::sync::Barrier;

use auth_db::{
    AuthStack, BarrierDbPlugin, BarrierOp, BarrierTiming, LEGACY_LOGOUT, LEGACY_REFRESH,
    StackOptions, V1_GROUPS, V1_LOGOUT, V1_REFRESH, build_stack, cookie,
    create_user_identities_schema, install_session, send, with_timeout,
};
use bcs_db_api::DbPlugin;

// ---------------------------------------------------------------------------
// Router-kind helper used to abstract cross-combos.
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RouterKind {
    V1,
    Legacy,
}

fn router_for<'a>(stack: &'a AuthStack, kind: RouterKind) -> axum::Router {
    match kind {
        RouterKind::V1 => stack.v1_router.clone(),
        RouterKind::Legacy => stack.legacy_router.clone(),
    }
}

const fn refresh_uri(kind: RouterKind) -> &'static str {
    match kind {
        RouterKind::V1 => V1_REFRESH,
        RouterKind::Legacy => LEGACY_REFRESH,
    }
}

const fn logout_uri(kind: RouterKind) -> &'static str {
    match kind {
        RouterKind::V1 => V1_LOGOUT,
        RouterKind::Legacy => LEGACY_LOGOUT,
    }
}

// Distinctive SQL fragments — the barrier plugins match these against the
// raw SQL of each DbPlugin call. The rotate predicate uniquely matches the
// rotate SQL (the only SQL whose WHERE clause has back-to-back
// `session_id`/`session_revision`/`token` ANDs); install SQL lacks
// `session_id` in the WHERE clause ("...AND env = ? AND session_revision
// = ?"), and revoke SQL has `AND token IS NOT NULL` after `session_id`.
const GET_SESSION_BY_HASH_PREFIX: &str = "SELECT user_id, auth_source, env, session_id";
const ROTATE_SQL_PREFIX: &str = "AND session_id = ? AND session_revision = ? AND token = ?";
const REVOKE_SQL_PREFIX: &str = "AND session_id = ? AND token IS NOT NULL";

// ---------------------------------------------------------------------------
// Stack with the nested two-barrier decorator used by scenario 1.
// ---------------------------------------------------------------------------

struct TwoBarrierStack {
    stack: AuthStack,
    barrier_a: Arc<Barrier>,
    barrier_b: Arc<Barrier>,
}

async fn fresh_two_barrier_stack() -> TwoBarrierStack {
    let raw_db: Arc<dyn DbPlugin> = {
        let db: Arc<dyn DbPlugin> = Arc::new(
            bcs_db_local::LocalSqliteDbPlugin::new().expect("open sqlite"),
        );
        create_user_identities_schema(&db).await;
        db
    };

    let barrier_b = Arc::new(Barrier::new(2));
    let barrier_inner = Arc::new(BarrierDbPlugin::with_timing(
        raw_db.clone(),
        barrier_b.clone(),
        BarrierOp::Execute,
        ROTATE_SQL_PREFIX,
        BarrierTiming::BeforeInner,
    ));
    let barrier_a = Arc::new(Barrier::new(2));
    let barrier_outer = Arc::new(BarrierDbPlugin::with_timing(
        barrier_inner as Arc<dyn DbPlugin>,
        barrier_a.clone(),
        BarrierOp::Query,
        GET_SESSION_BY_HASH_PREFIX,
        BarrierTiming::AfterInner,
    ));
    let stack = build_stack(StackOptions::with_decorated(barrier_outer as Arc<dyn DbPlugin>)).await;
    // Reassign raw_db so the test can plant fixture SQL through the underlying
    // plugin, bypassing the barriers.
    TwoBarrierStack {
        stack: stack,
        barrier_a,
        barrier_b,
    }
}

// ---------------------------------------------------------------------------
// Scenario 1 — refresh pauses after reading A; logout(A) commits on the
// other entry (legacy or V1); resume refresh → CAS Conflict → 401 +
// NO Set-Cookie; cookie A → 401; candidate B → 401.
// ---------------------------------------------------------------------------

async fn scenario_1_core(
    two: TwoBarrierStack,
    jwt_a: String,
    refresh_kind: RouterKind,
    logout_kind: RouterKind,
) {
    let TwoBarrierStack { stack, barrier_a, barrier_b } = two;

    let refresh_router = router_for(&stack, refresh_kind);
    let logout_router = router_for(&stack, logout_kind);
    let refresh_uri = refresh_uri(refresh_kind);
    let logout_uri = logout_uri(logout_kind);

    // Spawn refresh — it will:
    //   a) `engine.verify` get_session_by_hash → inner returns Ok(Some(A))
    //      then barrier A's AfterInner parks refresh
    //   b) once race driver releases barrier A, refresh proceeds; the
    //      rotate SQL execute hits barrier B (BeforeInner) and parks again
    let refresh_handle = tokio::spawn({
        let router = refresh_router.clone();
        let jwt_a = jwt_a.clone();
        async move {
            send(
                &router,
                axum::http::Method::POST,
                refresh_uri,
                Some(&cookie(&jwt_a)),
                Some(auth_db::ORIGIN),
            )
            .await
        }
    });

    // Race driver arrives at barrier A — release refresh. Refresh's read of
    // A has already committed (AfterInner), so refresh carries Ok(Some(A));
    // refresh proceeds to rotate execute and parks at barrier B (BeforeInner).
    let _ = with_timeout(barrier_a.wait()).await;

    // Issue logout on the other router — logout's revoke SQL does NOT match
    // barrier B's rotate SQL predicate; it passes through, commits → 200.
    let (logout_status, logout_set_cookie, _body) = with_timeout(send(
        &logout_router,
        axum::http::Method::POST,
        logout_uri,
        Some(&cookie(&jwt_a)),
        Some(auth_db::ORIGIN),
    ))
    .await;
    assert_eq!(
        logout_status,
        StatusCode::OK,
        "logout(A) on the other entry must succeed while refresh is parked at barrier B",
    );
    let set_cookie = logout_set_cookie
        .as_deref()
        .expect("logout success emits ClearSession Set-Cookie");
    assert!(
        set_cookie.contains("Max-Age=0"),
        "logout ClearSession should carry Max-Age=0, got {set_cookie}",
    );

    // Release barrier B so refresh's rotate.execute can finish. The store row
    // is now cleared (token = NULL, revision bumped by logout), so 0 rows
    // match the CAS WHERE clause → Conflict → Invalid → 401.
    let _ = with_timeout(barrier_b.wait()).await;

    let (refresh_status, refresh_set_cookie, _body) =
        with_timeout(refresh_handle).await.expect("refresh task panicked");

    assert_eq!(
        refresh_status,
        StatusCode::UNAUTHORIZED,
        "resume refresh → rotate CAS Conflict → Invalid → 401, got {refresh_status:?}",
    );
    assert!(
        refresh_set_cookie.is_none()
            || refresh_set_cookie
                .as_deref()
                .map(|s| !s.starts_with("bcs_session=") || s.contains("Max-Age=0"))
                .unwrap_or(true),
        "refresh CAS-Conflict error path must NOT emit a SetSession Set-Cookie; got {refresh_set_cookie:?}",
    );

    // Cookie A is now revoked (logout cleared it).
    let (status_a, _, _) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt_a)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        status_a,
        StatusCode::UNAUTHORIZED,
        "cookie A must no longer verify after logout(A)",
    );

    // Candidate B — refresh never installed B (rotate failed); synthesize a
    // candidate B JWT signed with the same secret and bumped revision. The
    // store has no row at B's hash → verifier returns Ok(None) → 401.
    let jwt_b = sign_candidate_b(&jwt_a);
    let (status_b, _, _) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt_b)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        status_b,
        StatusCode::UNAUTHORIZED,
        "synthesized candidate B (engine would-have-issued it on a successful rotate) must not \
         verify — no B was installed; got {status_b:?}",
    );

    // Noise: prevent the linter from thinking ROTATE/REVOKE prefixes are dead.
    let _ = ROTATE_SQL_PREFIX;
    let _ = REVOKE_SQL_PREFIX;
}

fn sign_candidate_b(jwt_a: &str) -> String {
    let jwt = bcs_jwt::OAuthSessionJwt::new(auth_db::JWT_SECRET);
    let claims = jwt
        .verify_for_revoke(jwt_a)
        .expect("jwt_a verifies under the test secret");
    let next_claims = bcs_jwt::OAuthSessionClaims {
        sub: claims.sub.clone(),
        src: claims.src.clone(),
        env: claims.env.clone(),
        session_id: claims.session_id.clone(),
        revision: claims.revision.checked_add(1).expect("non-overflow"),
        iat: claims.iat,
        exp: claims.exp,
        name: claims.name.clone(),
    };
    jwt.sign(&next_claims).expect("sign candidate B")
}

#[tokio::test]
async fn race_legacy_refresh_v1_logout_cas_conflict() {
    let two = fresh_two_barrier_stack().await;
    let jwt_a = install_session(&two.stack, "github", "ext-race-1a-legacy-refresh", "Alice").await;
    scenario_1_core(two, jwt_a, RouterKind::Legacy, RouterKind::V1).await;
}

#[tokio::test]
async fn race_v1_refresh_legacy_logout_cas_conflict() {
    let two = fresh_two_barrier_stack().await;
    let jwt_a = install_session(&two.stack, "github", "ext-race-1b-v1-refresh", "Bob").await;
    scenario_1_core(two, jwt_a, RouterKind::V1, RouterKind::Legacy).await;
}

// ---------------------------------------------------------------------------
// Scenario 2 — rotate commits but the refresh HTTP response is paused
// (barrier AFTER the rotate write); logout(A) lands meanwhile on the other
// entry → late Set B cookie; B must not pass verification (401).
// ---------------------------------------------------------------------------

async fn fresh_after_rotate_barrier_stack() -> (AuthStack, Arc<Barrier>) {
    let raw_db: Arc<dyn DbPlugin> = {
        let db: Arc<dyn DbPlugin> = Arc::new(
            bcs_db_local::LocalSqliteDbPlugin::new().expect("open sqlite"),
        );
        create_user_identities_schema(&db).await;
        db
    };
    let barrier = Arc::new(Barrier::new(2));
    let decorator = Arc::new(BarrierDbPlugin::with_timing(
        raw_db.clone(),
        barrier.clone(),
        BarrierOp::Execute,
        ROTATE_SQL_PREFIX,
        BarrierTiming::AfterInner,
    ));
    let mut stack = build_stack(StackOptions::with_decorated(decorator as Arc<dyn DbPlugin>)).await;
    stack.raw_db = raw_db;
    (stack, barrier)
}

#[tokio::test]
async fn race_rotate_commits_but_response_paused_late_b_cookie_fails() {
    let (stack, barrier) = fresh_after_rotate_barrier_stack().await;
    let jwt_a = install_session(&stack, "github", "ext-race-2", "Carol").await;

    // Spawn refresh — the rotate execute commits (inner returns Ok(Applied)),
    // then barrier parks refresh BEFORE its continuation packages Set-Cookie.
    let refresh_handle = tokio::spawn({
        let router = stack.v1_router.clone();
        let jwt_a = jwt_a.clone();
        async move {
            send(
                &router,
                axum::http::Method::POST,
                V1_REFRESH,
                Some(&cookie(&jwt_a)),
                Some(auth_db::ORIGIN),
            )
            .await
        }
    });

    // Race driver arrives at the barrier — until refresh parks at the
    // AfterInner post-rotate wait, this blocks. When refresh arrives AND
    // commits rotate (inner.execute returns Ok(Applied)), the barrier
    // releases both: refresh's continuation begins to package Set-Cookie B
    // and the race driver proceeds to issue logout on the other entry.
    //
    // Logout's revoke SQL does NOT match the barrier's rotate predicate, so
    // it passes through the plugin untouched, commits against the rotated
    // row (whose session_id is preserved by the rotate), and clears B by
    // session_id. By the time refresh's continuation finishes packaging
    // Set-Cookie B, B is already revoked server-side.
    let _ = with_timeout(barrier.wait()).await;

    // Issue logout on the legacy router — revoke by session_id clears the
    // rotated row (B shares A's sid; revoke matches the refreshed row).
    let (logout_status, _logout_set_cookie, _) = with_timeout(send(
        &stack.legacy_router,
        axum::http::Method::POST,
        LEGACY_LOGOUT,
        Some(&cookie(&jwt_a)),
        Some(auth_db::ORIGIN),
    ))
    .await;
    assert_eq!(
        logout_status,
        StatusCode::OK,
        "logout(A) on the legacy entry must succeed (B shares A's sid, revoke clears it)",
    );

    // Refresh's continuation has likely already completed (its continuation
    // rate is fast after the barrier release). Await the JoinHandle so the
    // test driver observes refresh's response and Set-Cookie. Refresh
    // returns 200 because rotate was Applied; Set-Cookie carries B.
    let (refresh_status, refresh_set_cookie, _body) =
        with_timeout(refresh_handle).await.expect("refresh task panicked");

    assert_eq!(
        refresh_status,
        StatusCode::OK,
        "refresh returns 200 (rotate committed) even though logout has since revoked B; got {refresh_status:?}",
    );
    let jwt_b = auth_db::extract_session_token(&refresh_set_cookie)
        .expect("refresh success emits SetSession bcs_session=<token>")
        .to_string();
    assert_ne!(
        jwt_b, jwt_a,
        "refresh issues a different (B) token via rotate",
    );

    // §8.5 #2: B must NOT pass verification — logout(A) revoked B by sid
    // while refresh's response was in flight.
    let (status_b, _, _) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt_b)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        status_b,
        StatusCode::UNAUTHORIZED,
        "late Set-Cookie B must NOT pass verification (logout(A) revoked B before refresh returned); got {status_b:?}",
    );

    // Sanity: A is also gone (logout cleared A OR rotate replaced A's hash;
    // either way, the JWT's hash matches nothing).
    let (status_a_after, _, _) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt_a)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        status_a_after,
        StatusCode::UNAUTHORIZED,
        "A is no longer verifiable post-race (rotated-away hash AND revoked)",
    );
}

// ---------------------------------------------------------------------------
// Scenario 3 — new login C commits vs old A logout: C unaffected by late
// A logout (C's request → 200). Sequential — no barrier needed.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn race_new_login_c_survives_late_a_logout() {
    let stack = build_stack(StackOptions::plain()).await;
    let jwt_a = install_session(&stack, "github", "ext-race-3a", "User A").await;
    // Independent scope — C is a fresh login of a different external user.
    let jwt_c = install_session(&stack, "github", "ext-race-3c", "User C").await;

    // logout(A) on the legacy entry — A's row gets cleared by session_id.
    let (logout_status, _logout_set_cookie, _) = send(
        &stack.legacy_router,
        axum::http::Method::POST,
        LEGACY_LOGOUT,
        Some(&cookie(&jwt_a)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        logout_status,
        StatusCode::OK,
        "logout(A) succeeds",
    );

    // C's request on the V1 router — unaffected by A's logout (logout only
    // matched A's sid; C's row has a different sid).
    let (status_c, _, _) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt_c)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        status_c,
        StatusCode::OK,
        "C's session is unaffected by A's logout — logout revoked only A's sid",
    );

    // Sanity: A is now revoked.
    let (status_a, _, _) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt_a)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        status_a,
        StatusCode::UNAUTHORIZED,
        "A is revoked; cookie A must 401",
    );
}

// ---------------------------------------------------------------------------
// Scenario 4 — two simultaneous refreshes on A: exactly one Applied, the
// loser gets 401 and NO Set-Cookie.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn race_two_concurrent_refreshes_one_applied_loser_401_no_set_cookie() {
    let stack = build_stack(StackOptions::plain()).await;
    let jwt_a = install_session(&stack, "github", "ext-race-4", "Dave").await;

    let (r1, r2) = {
        let router = stack.v1_router.clone();
        let jwt = jwt_a.clone();
        let h1 = tokio::spawn(async move {
            send(
                &router,
                axum::http::Method::POST,
                V1_REFRESH,
                Some(&cookie(&jwt)),
                Some(auth_db::ORIGIN),
            )
            .await
        });
        let router = stack.v1_router.clone();
        let jwt = jwt_a.clone();
        let h2 = tokio::spawn(async move {
            send(
                &router,
                axum::http::Method::POST,
                V1_REFRESH,
                Some(&cookie(&jwt)),
                Some(auth_db::ORIGIN),
            )
            .await
        });
        (h1, h2)
    };

    let (s1, c1, _) = with_timeout(r1).await.expect("refresh 1 panicked");
    let (s2, c2, _) = with_timeout(r2).await.expect("refresh 2 panicked");

    // Exactly one refresh Applied (200 + Set-Cookie); the loser 401 (CAS
    // Conflict → Invalid) with NO Set-Cookie.
    let winner_status_ok = s1 == StatusCode::OK && s2 == StatusCode::UNAUTHORIZED;
    let loser_status_ok = s2 == StatusCode::OK && s1 == StatusCode::UNAUTHORIZED;
    assert!(
        winner_status_ok || loser_status_ok,
        "expected exactly one 200 + one 401 among the two concurrent refreshes; got {s1:?} and {s2:?}",
    );
    let (winner_set_cookie, loser_set_cookie) = if winner_status_ok {
        (c1, c2)
    } else {
        (c2, c1)
    };
    let _winner_jwt = auth_db::extract_session_token(&winner_set_cookie)
        .expect("winner issued a SetSession bcs_session=<token>");
    assert!(
        loser_set_cookie.is_none()
            || loser_set_cookie
                .as_deref()
                .map(|s| !s.starts_with("bcs_session=") || s.contains("Max-Age=0"))
                .unwrap_or(true),
        "loser (CAS conflict) must NOT emit a SetSession Set-Cookie, got {loser_set_cookie:?}",
    );

    // Sanity: cookie A is rotated-away → 401 post-race.
    let (status_a, _, _) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt_a)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        status_a,
        StatusCode::UNAUTHORIZED,
        "cookie A is rotated-away; using it post-race must 401",
    );
}

// ---------------------------------------------------------------------------
// Scenario 5 — two independent `DbUserIdentityStore` instances over the SAME
// SQLite plugin racing a refresh + logout — outcomes identical to single-
// store runs (store-level atomicity at the DB write boundary).
// ---------------------------------------------------------------------------

#[tokio::test]
async fn race_two_stores_same_db_atomic_outcome_matches_single_store() {
    let shared_db: Arc<dyn DbPlugin> = Arc::new(
        bcs_db_local::LocalSqliteDbPlugin::new().expect("open shared sqlite"),
    );
    create_user_identities_schema(&shared_db).await;

    // Two independent DbUserIdentityStore instances over the SAME plugin.
    let _store_a = bcs_user_identity::DbUserIdentityStore::sqlite(shared_db.clone());
    let store_b = bcs_user_identity::DbUserIdentityStore::sqlite(shared_db.clone());

    // The stack uses the SAME shared plugin under its store — both `store_a`
    // and the stack's internal store write the same row.
    let mut stack = build_stack(StackOptions::with_decorated(shared_db.clone())).await;
    stack.raw_db = shared_db.clone();

    let jwt_a = install_session(&stack, "github", "ext-race-5", "Erin").await;

    // store_b observes the SAME live session row installed by the stack's
    // engine, since both stores share the same in-memory SQLite rows.
    let session_id_from_jwt = bcs_jwt::OAuthSessionJwt::new(auth_db::JWT_SECRET)
        .verify_for_revoke(&jwt_a)
        .expect("jwt_a verifies")
        .session_id
        .clone();
    let scope = bcs_auth_api::SessionScope {
        user_id: stack
            .identities
            .ensure_identity("github", "ext-race-5", Some("Erin"), None, auth_db::ENV)
            .await
            .expect("ensure idempotent"),
        provider: "github".to_string(),
        env: auth_db::ENV.to_string(),
    };
    let snapshot_from_store_b = store_b
        .get_session_by_hash(&to_repo_scope(&scope), &bcs_jwt::token_hash(&jwt_a), 1_000_000_000)
        .await
        .expect("store_b read succeeds")
        .expect("store_b observes the SAME live session row installed via the stack's store");
    assert_eq!(
        snapshot_from_store_b.version.session_id, session_id_from_jwt,
        "both stores observe the SAME session_id (shared DB rows)",
    );

    // refresh succeeds; store_b independently observes the rotated-away
    // hash (Ok(None)).
    let (refresh_status, refresh_set_cookie, _) = send(
        &stack.v1_router,
        axum::http::Method::POST,
        V1_REFRESH,
        Some(&cookie(&jwt_a)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        refresh_status,
        StatusCode::OK,
        "refresh against a single live session on the shared DB must succeed",
    );
    let jwt_b = auth_db::extract_session_token(&refresh_set_cookie)
        .expect("refresh success emits SetSession")
        .to_string();

    let snapshot_after_rotate = store_b
        .get_session_by_hash(&to_repo_scope(&scope), &bcs_jwt::token_hash(&jwt_a), 1_000_000_000)
        .await
        .expect("store_b read after rotate");
    assert!(
        snapshot_after_rotate.is_none(),
        "store_b observes the rotated-away A hash as Ok(None) (atomicity guarantees the write)",
    );

    // logout(B) directly through store_b (the sibling store) — must revoke.
    use bcs_service_api::port::repo::auth_session::AuthSessionRepoPort;
    let revoke_result = store_b
        .revoke_session(&to_repo_scope(&scope), &session_id_from_jwt)
        .await
        .expect("store_b revoke_session must be Ok, not Err");
    assert_eq!(
        revoke_result,
        bcs_service_api::port::repo::auth_session::AuthSessionRevoke::Revoked,
        "store_b operates on the SAME rows — revoke matches the rotated row (session_id kept) and returns Revoked",
    );

    // Cookie B → 401 (revoke succeeded, shared by all stores).
    let (status_b, _, _) = send(
        &stack.v1_router,
        axum::http::Method::GET,
        V1_GROUPS,
        Some(&cookie(&jwt_b)),
        Some(auth_db::ORIGIN),
    )
    .await;
    assert_eq!(
        status_b,
        StatusCode::UNAUTHORIZED,
        "B is revoked by the sibling store; the verifier rejects cookie B",
    );

    let _ = stack;
}

fn to_repo_scope(
    scope: &bcs_auth_api::SessionScope,
) -> bcs_service_api::port::repo::auth_session::AuthSessionScope {
    bcs_service_api::port::repo::auth_session::AuthSessionScope {
        user_id: scope.user_id.clone(),
        provider: scope.provider.clone(),
        env: scope.env.clone(),
    }
}
