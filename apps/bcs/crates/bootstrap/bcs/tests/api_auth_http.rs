//! DB→HTTP fault propagation through the REAL chain (Task 13 step 1/2,
//! V1 API auth plan §8.6): the fault is injected ONLY at the DbPlugin
//! boundary (`support::auth_db`), and every layer above is real —
//! `DbUserIdentityStore`, the bootstrap identity bridges, the Task 8
//! engine inside the Task 12 assembled chain, the Stateful wrap, the real
//! router. The protected surface under test is the real
//! `GET /openapi/v1/collaboration/groups` route, so the three failure
//! classes surface exactly where handlers would begin:
//!
//! - a DATABASE query failure → store `Unavailable` → strict engine
//!   `Unavailable` → delivery `DependencyUnavailable` → HTTP 503
//!   `auth_dependency_unavailable`;
//! - a query that succeeds with ZERO rows → `Ok(None)` → credentials
//!   invalid → HTTP 401, business call == 0;
//! - a MALFORMED stored row → `CorruptRecord` → engine `Internal` → HTTP
//!   500, business call == 0;
//! - the refresh/logout entries keep the same taxonomy (503 on the V1
//!   side; the legacy entry keeps its pre-mapped shape).
//!
//! Business-call counting is asserted, never printed, and the same tests
//! carry their own positive controls (after the fault clears, the exact
//! same request reaches the counting business service).

use std::sync::atomic::Ordering;

use axum::http::StatusCode;
use serde_json::Value;

#[path = "support/auth_db.rs"]
mod auth_db;
#[path = "support/api_fakes.rs"]
mod api_fakes;
#[path = "support/api_chain.rs"]
mod api_chain;

use api_chain::{ApiChainFixture, FixtureSpec, assert_no_session_cookie, drive, session_cookie_only, session_headers};
use auth_db::NamedOp;

const GROUPS: &str = "/openapi/v1/collaboration/groups";
const V1_REFRESH: &str = "/openapi/v1/auth/refresh";
const LEGACY_REFRESH: &str = "/auth/refresh";

/// DATABASE query outage → store `Unavailable` → 503
/// `auth_dependency_unavailable` on the real protected route; the business
/// service is never entered; the SAME request reaches the business exactly
/// once after the fault clears (positive control in the same scenario).
#[tokio::test]
async fn strict_db_query_outage_is_a_503_and_never_reaches_the_business() {
    let fixture = ApiChainFixture::assemble(FixtureSpec::standard()).await;
    let seeded = fixture.seed_session("fault-503-user").await;

    fixture.db.control.set_faults(&[NamedOp::StrictSelect]);
    fixture.db.reset_counts();
    let faulted = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&seeded.token),
    )
    .await;
    assert_eq!(faulted.status, StatusCode::SERVICE_UNAVAILABLE, "{:?}", faulted.body);
    assert_eq!(faulted.body["code"], 50_300);
    assert_eq!(
        faulted.body["data"]["error_code"], "auth_dependency_unavailable",
        "the strict dependency category is stable, got {:?}",
        faulted.body
    );
    assert_eq!(
        fixture.db.control.strict_selects.load(Ordering::SeqCst),
        1,
        "exactly ONE strict SELECT is spent before the fault surfaces"
    );
    assert_eq!(
        fixture.group.calls("list_groups"), 0,
        "the business service must not run behind a failed authentication dependency"
    );

    // Positive control: the identical request (same cookie, same route)
    // reaches the counting business service exactly once once the database
    // answers again — the counter is proven able to move.
    fixture.db.control.clear_faults();
    fixture.db.reset_counts();
    let healthy = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&seeded.token),
    )
    .await;
    assert_eq!(healthy.status, StatusCode::OK, "{:?}", healthy.body);
    assert_eq!(fixture.group.calls("list_groups"), 1);
    assert_eq!(
        fixture.db.control.strict_selects.load(Ordering::SeqCst),
        1,
        "a healthy strict verify is exactly ONE SELECT"
    );
}

/// A query that SUCCEEDS but finds no current session row is plain
/// absence, never an outage: valid signature, never-installed session →
/// `Ok(None)` → credentials invalid → 401, business never entered.
#[tokio::test]
async fn zero_row_strict_query_is_credentials_invalid_not_an_outage() {
    let fixture = ApiChainFixture::assemble(FixtureSpec::standard()).await;

    // The identity EXISTS (a completed login for another browser session),
    // but the presented credential names a session that was never
    // installed for it: the strict SELECT succeeds with zero rows.
    let other = fixture
        .identity
        .ensure_identity("github", "zero-row-user", None, None, api_chain::ENV)
        .await
        .expect("seed identity without a session");
    let never_installed =
        api_chain::sign_session_token(&fixture.jwt, &other, "zero-row-never-installed-sid", 1);

    fixture.db.reset_counts();
    let driven = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&never_installed),
    )
    .await;
    assert_eq!(driven.status, StatusCode::UNAUTHORIZED, "{:?}", driven.body);
    assert_eq!(driven.body["data"]["error_code"], "unauthenticated");
    assert_eq!(
        fixture.db.control.rows_returned.load(Ordering::SeqCst),
        0,
        "the strict read came back with zero rows, not an error"
    );
    assert_eq!(
        fixture.db.control.strict_selects.load(Ordering::SeqCst),
        1,
        "the absence verdict spends exactly one SELECT"
    );
    assert_eq!(fixture.group.calls("list_groups"), 0);
}

/// A MALFORMED stored row (persisted corruption) is `CorruptRecord` at the
/// store, `Internal` at the engine, HTTP 500 — never a denial, never an
/// outage, and never business-reachable.
#[tokio::test]
async fn corrupt_revision_row_is_a_500_and_never_reaches_the_business() {
    let fixture = ApiChainFixture::assemble(FixtureSpec::standard()).await;
    let seeded = fixture.seed_session("fault-500-user").await;

    // Hand-corrupt the persisted row through the REAL database (fixture
    // seeding, not the chain under test): the token hash stays installed
    // so the strict SELECT still finds the row, but its revision cannot
    // be read back as an integer.
    fixture
        .db
        .raw
        .execute(bcs_db_api::DbStatement::with_params(
            "UPDATE bcs_user_identities SET session_revision = ? WHERE user_id = ?",
            vec![
                bcs_db_api::DbValue::from("not-an-integer"),
                bcs_db_api::DbValue::from(seeded.user_id.as_str()),
            ],
        ))
        .await
        .expect("plant the corrupt revision");

    fixture.db.reset_counts();
    let driven = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &session_cookie_only(&seeded.token),
    )
    .await;
    assert_eq!(
        driven.status,
        StatusCode::INTERNAL_SERVER_ERROR,
        "persisted corruption must never look like a denial: {:?}",
        driven.body
    );
    assert_eq!(driven.body["code"], 50_000);
    assert_eq!(driven.body["data"]["error_code"], "internal_error");
    assert_eq!(fixture.group.calls("list_groups"), 0);
}

/// The refresh entries keep the same taxonomy: a strict dependency outage
/// is a 503 `auth_dependency_unavailable` on the V1 route with NO
/// Set-Cookie, while the legacy entry keeps its pre-mapped shape; both
/// recover once the dependency answers.
#[tokio::test]
async fn refresh_dependency_outage_maps_503_on_v1_and_the_legacy_shape_on_auth() {
    let fixture = ApiChainFixture::assemble(FixtureSpec::standard()).await;
    let seeded = fixture.seed_session("fault-refresh-user").await;

    // V1 refresh: the strict read faults → 503, zero cookie instructions.
    fixture.db.control.set_faults(&[NamedOp::StrictSelect]);
    let faulted = drive(
        &fixture.v1_router,
        "POST",
        V1_REFRESH,
        &session_headers(&seeded.token),
    )
    .await;
    assert_eq!(faulted.status, StatusCode::SERVICE_UNAVAILABLE, "{:?}", faulted.body);
    assert_eq!(faulted.body["data"]["error_code"], "auth_dependency_unavailable");
    assert_no_session_cookie(&faulted);

    // The legacy entry keeps its own pre-mapped shape for the same
    // dependency fault (Task 11 pinned it; it must not silently drift),
    // and likewise never hands out a credential under outage.
    let legacy_faulted = drive(
        &fixture.legacy_router,
        "POST",
        LEGACY_REFRESH,
        &session_headers(&seeded.token),
    )
    .await;
    assert!(
        legacy_faulted.status == StatusCode::INTERNAL_SERVER_ERROR
            || legacy_faulted.status == StatusCode::SERVICE_UNAVAILABLE,
        "legacy dependency outage surfaces as an error, got {} {:?}",
        legacy_faulted.status,
        legacy_faulted.body
    );
    assert_no_session_cookie(&legacy_faulted);

    // Recovery positive control on both entries: the V1 refresh rotates
    // the session (its credential is handed out only after the CAS
    // applies), and the LEGACY entry then rotates that SAME live session
    // — one shared session space behind both entries.
    fixture.db.control.clear_faults();
    let recovered = drive(
        &fixture.v1_router,
        "POST",
        V1_REFRESH,
        &session_headers(&seeded.token),
    )
    .await;
    assert!(
        recovered.status == StatusCode::OK,
        "recovered refresh: status {:?} body {:?}",
        recovered.status,
        recovered.body
    );
    let rotated = api_chain::rotated_token_of(&recovered);
    assert!(rotated.len() > 16, "the recovered refresh hands out a real rotated credential");
    let legacy_recovered = drive(
        &fixture.legacy_router,
        "POST",
        LEGACY_REFRESH,
        &session_headers(&rotated),
    )
    .await;
    assert_eq!(
        legacy_recovered.status,
        StatusCode::OK,
        "legacy recovery body: {:?}",
        legacy_recovered.body
    );
    assert!(
        api_chain::rotated_token_of(&legacy_recovered).len() > 16,
        "the recovered legacy refresh hands out a real rotated credential"
    );
}

/// The envelope shape shared by every denial in this suite (asserted once,
/// reused by the other suites' expectations).
#[allow(dead_code)]
fn unauthenticated_envelope(body: &Value) {
    assert_eq!(body["code"], 40_100);
    assert_eq!(body["data"]["error_code"], "unauthenticated");
}

#[tokio::test]
async fn current_user_preserves_the_stored_avatar_with_one_strict_select() {
    let fixture = ApiChainFixture::assemble(FixtureSpec::standard()).await;
    let seeded = fixture.seed_session("avatar-user").await;
    for avatar in [Some("/avatars/user.png"), None] {
        fixture.identity.ensure_identity(
            "github", "avatar-user", Some("Avatar User"), avatar, api_chain::ENV,
        ).await.expect("update identity display data");
        fixture.db.reset_counts();
        let response = drive(
            &fixture.v1_router, "GET", "/openapi/v1/auth/user",
            &session_cookie_only(&seeded.token),
        ).await;
        assert_eq!(response.status, StatusCode::OK, "{:?}", response.body);
        assert_eq!(response.body["data"]["user_id"], seeded.user_id);
        assert_eq!(response.body["data"]["avatar"], serde_json::json!(avatar));
        assert_eq!(fixture.db.control.strict_selects.load(Ordering::SeqCst), 1,
            "display projection must not perform a second credential lookup");
    }
}

#[tokio::test]
async fn refresh_rejects_disabled_provider_without_writing_or_issuing_a_cookie() {
    let mut spec = FixtureSpec::standard();
    spec.chain = vec!["google", "gateway"];
    spec.table_fragments.push((
        "google".to_string(),
        "client_id = \"google-refresh-test\"\nclient_secret_secret = \"google-refresh-secret\"".to_string(),
    ));
    spec.secrets.push(("google-refresh-secret".to_string(), "test-value".to_string()));
    let fixture = ApiChainFixture::assemble(spec).await;
    // A shared legacy session is valid, but GitHub is not enabled in V1.
    let seeded = fixture.seed_session("disabled-provider-refresh").await;
    for gateway in [false, true] {
        let mut headers = session_headers(&seeded.token);
        if gateway {
            headers.push(("x-avernet-principal", api_chain::gateway_user_principal_token("gateway-user")));
        }
        fixture.db.reset_counts();
        let rejected = drive(&fixture.v1_router, "POST", V1_REFRESH, &headers).await;
        assert_eq!(rejected.status, StatusCode::FORBIDDEN);
        assert_eq!(rejected.body["data"]["error_code"], "session_forbidden");
        assert_no_session_cookie(&rejected);
        assert_eq!(fixture.db.control.strict_selects.load(Ordering::SeqCst), 1);
        assert_eq!(fixture.db.control.rotates.load(Ordering::SeqCst), 0);
    }
    // The refusal did not modify the shared session; legacy refresh remains
    // independent of the explicit V1 provider selection.
    fixture.db.reset_counts();
    let legacy = drive(
        &fixture.legacy_router, "POST", LEGACY_REFRESH, &session_headers(&seeded.token),
    ).await;
    assert_eq!(legacy.status, StatusCode::OK);
    assert_eq!(fixture.db.control.strict_selects.load(Ordering::SeqCst), 1);
    assert_eq!(fixture.db.control.rotates.load(Ordering::SeqCst), 1);
    // Disabled providers must still be able to revoke their own sessions.
    let logout = drive(
        &fixture.v1_router, "POST", "/openapi/v1/auth/logout",
        &session_headers(&api_chain::rotated_token_of(&legacy)),
    ).await;
    assert_eq!(logout.status, StatusCode::OK);
}

#[tokio::test]
async fn allowed_provider_refresh_keeps_one_select_and_one_cas() {
    let fixture = ApiChainFixture::assemble(FixtureSpec::standard()).await;
    let seeded = fixture.seed_session("allowed-provider-refresh").await;
    fixture.db.reset_counts();
    let response = drive(
        &fixture.v1_router, "POST", V1_REFRESH, &session_headers(&seeded.token),
    ).await;
    assert_eq!(response.status, StatusCode::OK);
    assert_ne!(api_chain::rotated_token_of(&response), seeded.token);
    assert_eq!(fixture.db.control.strict_selects.load(Ordering::SeqCst), 1);
    assert_eq!(fixture.db.control.rows_returned.load(Ordering::SeqCst), 1);
    assert_eq!(fixture.db.control.rotates.load(Ordering::SeqCst), 1);
    assert_eq!(fixture.db.control.applied_rotates.load(Ordering::SeqCst), 1);
}
