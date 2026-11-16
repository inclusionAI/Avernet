//! Cross-entry shared-session races (Task 13 step 3, V1 API auth plan
//! §8/race contract) — deterministic interleavings, never timing: the
//! pause points are named read/write points INSIDE the DbPlugin
//! decorator (`support::auth_db`), and every scenario asserts the SAFE
//! outcome:
//!
//! ```text
//! refresh pauses after reading A
//! logout(A) commits and returns success
//! resume refresh -> CAS Conflict, no Set-Cookie
//! request with A -> 401
//! request with candidate B -> 401
//! ```
//!
//! Plus the four sibling shapes from the brief: rotate
//! committed-but-response-paused → logout(A) → the LATE rotated cookie
//! must fail server-side; a NEW login (fresh session id) vs an old
//! logout (the new login stays live); two concurrent refreshes of A
//! (exactly one commits); and two INDEPENDENT store instances over one
//! shared SQLite database (the CAS still fights the persisted row, not
//! process-local state).
//!
//! The discovery pair covers both cross-entry combinations —
//! legacy-refresh/V1-logout and V1-refresh/legacy-logout — over the ONE
//! shared session space production gives both entries.

use std::sync::atomic::Ordering;

use axum::http::StatusCode;

#[path = "support/auth_db.rs"]
mod auth_db;
#[path = "support/api_fakes.rs"]
mod api_fakes;
#[path = "support/api_chain.rs"]
mod api_chain;

use api_chain::{
    ApiChainFixture, Driven, FixtureSpec, assert_credentials_invalid_via_groups,
    assert_no_session_cookie, drive, rotated_token_of, session_headers,
};
use auth_db::{NamedOp, Phase};

const GROUPS: &str = "/openapi/v1/collaboration/groups";
const V1_REFRESH: &str = "/openapi/v1/auth/refresh";
const V1_LOGOUT: &str = "/openapi/v1/auth/logout";
const LEGACY_REFRESH: &str = "/auth/refresh";
const LEGACY_LOGOUT: &str = "/auth/logout";

#[derive(Clone, Copy)]
enum Entry {
    V1,
    Legacy,
}

impl Entry {
    fn router_of<'a>(&self, fixture: &'a ApiChainFixture) -> &'a axum::Router {
        match self {
            Entry::V1 => &fixture.v1_router,
            Entry::Legacy => &fixture.legacy_router,
        }
    }

    fn refresh_path(&self) -> &'static str {
        match self {
            Entry::V1 => V1_REFRESH,
            Entry::Legacy => LEGACY_REFRESH,
        }
    }

    fn logout_path(&self) -> &'static str {
        match self {
            Entry::V1 => V1_LOGOUT,
            Entry::Legacy => LEGACY_LOGOUT,
        }
    }

    fn name(&self) -> &'static str {
        match self {
            Entry::V1 => "v1",
            Entry::Legacy => "legacy",
        }
    }
}

async fn drive_entry(
    fixture: &ApiChainFixture,
    entry: Entry,
    method: &str,
    path: &str,
    token: &str,
) -> Driven {
    let headers = session_headers(token);
    drive(entry.router_of(fixture), method, path, &headers).await
}

fn spawn_entry_refresh(fixture: &ApiChainFixture, entry: Entry, token: &str) -> tokio::task::JoinHandle<Driven> {
    let router = entry.router_of(fixture).clone();
    let headers = session_headers(token);
    let path = entry.refresh_path();
    tokio::spawn(async move { drive(&router, "POST", path, &headers).await })
}

/// The verbatim race contract (brief step 3), parameterized over which
/// entry refreshes and which logs out: the refresh (A presented) is
/// paused INSIDE the DbPlugin decorator right after it read A and before
/// its rotate CAS; the logout commits on the other entry meanwhile; the
/// resumed refresh loses the CAS — `Conflict` → `Invalid` → 401 with NO
/// Set-Cookie — and afterwards BOTH the stale credential and the
/// candidate next version are credentials-invalid, with the business
/// service never entered.
async fn refresh_reading_a_loses_to_a_committed_logout(
    fixture: &ApiChainFixture,
    refresh_entry: Entry,
    logout_entry: Entry,
) {
    let seeded = fixture.seed_session("race-read-a").await;
    fixture.db.reset_counts();

    // refresh pauses after reading A: the gate sits on the rotate CAS's
    // named pre-write point (a barrier inside the decorator, no sleep).
    let mut hold = fixture
        .db
        .control
        .install_gate(NamedOp::Rotate, Phase::Pre);

    let refresh_task = spawn_entry_refresh(&fixture, refresh_entry, &seeded.token);

    // The refresh has read A (its ONE strict SELECT returned) and is now
    // holding at the write point.
    hold.wait_reached().await;
    assert_eq!(
        fixture.db.control.strict_selects.load(Ordering::SeqCst),
        1,
        "the paused refresh spent exactly its one strict SELECT reading A"
    );

    // logout(A) commits and returns success meanwhile.
    let logout = drive_entry(&fixture, logout_entry, "POST", logout_entry.logout_path(), &seeded.token).await;
    assert_eq!(
        logout.status,
        StatusCode::OK,
        "logout must commit and succeed: {:?}",
        logout.body
    );
    assert_eq!(fixture.db.control.revoked_rows.load(Ordering::SeqCst), 1);

    // Resume the refresh: the CAS now runs against the revoked row.
    hold.release();
    let refresh = refresh_task.await.expect("refresh task");
    assert_eq!(
        refresh.status,
        StatusCode::UNAUTHORIZED,
        "the lost refresh ({}-refresh vs {}-logout) lowers to Unauthenticated: {:?}",
        refresh_entry.name(),
        logout_entry.name(),
        refresh.body
    );
    assert_no_session_cookie(&refresh);
    assert_eq!(fixture.db.control.rotates.load(Ordering::SeqCst), 1);
    assert_eq!(fixture.db.control.applied_rotates.load(Ordering::SeqCst), 0);
    assert_eq!(fixture.db.control.conflicted_rotates.load(Ordering::SeqCst), 1);

    // request with A -> 401; request with candidate B -> 401.
    let group_calls = fixture.group.calls("list_groups");
    assert_credentials_invalid_via_groups(&fixture.v1_router, &seeded.token).await;
    let candidate_b = fixture.candidate_token(&seeded, 2);
    assert_credentials_invalid_via_groups(&fixture.v1_router, &candidate_b).await;
    assert_eq!(
        fixture.group.calls("list_groups"),
        group_calls,
        "credentials-invalid requests never enter the business service"
    );
}

async fn fresh() -> ApiChainFixture {
    ApiChainFixture::assemble(FixtureSpec::standard()).await
}

/// The verbatim sequence, both sides on the V1 entry (the base shape).
#[tokio::test]
async fn v1_refresh_pausing_after_reading_a_loses_the_cas_to_a_v1_logout() {
    let fixture = fresh().await;
    refresh_reading_a_loses_to_a_committed_logout(&fixture, Entry::V1, Entry::V1).await;
}

/// CROSS-ENTRY combination 1: a LEGACY refresh loses to a V1 logout.
#[tokio::test]
async fn legacy_refresh_pausing_after_reading_a_loses_the_cas_to_a_v1_logout() {
    let fixture = fresh().await;
    refresh_reading_a_loses_to_a_committed_logout(&fixture, Entry::Legacy, Entry::V1).await;
}

/// CROSS-ENTRY combination 2: a V1 refresh loses to a LEGACY logout.
#[tokio::test]
async fn v1_refresh_pausing_after_reading_a_loses_the_cas_to_a_legacy_logout() {
    let fixture = fresh().await;
    refresh_reading_a_loses_to_a_committed_logout(&fixture, Entry::V1, Entry::Legacy).await;
}

/// rotate committed but the refresh's RESPONSE is still paused: the
/// logout (by session id) still wins, and the LATE rotated cookie B the
/// paused refresh eventually hands out must fail server-side (the revoke
/// clears the hash the CAS had just written).
#[tokio::test]
async fn rotate_committed_and_response_paused_still_dies_to_the_late_cookie() {
    let fixture = fresh().await;
    let seeded = fixture.seed_session("race-late-cookie").await;
    fixture.db.reset_counts();

    // Pause AFTER the rotate executed: the rotation is durably committed
    // in the database, the response has not been handed out yet.
    let mut hold = fixture.db.control.install_gate(NamedOp::Rotate, Phase::Post);
    let refresh_task = spawn_entry_refresh(&fixture, Entry::V1, &seeded.token);
    hold.wait_reached().await;
    assert_eq!(
        fixture.db.control.applied_rotates.load(Ordering::SeqCst),
        1,
        "the rotation has COMMITTED before the logout runs"
    );

    // logout(A) commits and succeeds against the JUST-ROTATED row (revoke
    // is by session id; the presented token need not be current).
    let logout = drive_entry(&fixture, Entry::V1, "POST", V1_LOGOUT, &seeded.token).await;
    assert_eq!(logout.status, StatusCode::OK, "{:?}", logout.body);
    assert_eq!(fixture.db.control.revoked_rows.load(Ordering::SeqCst), 1);

    // The paused refresh now returns — carrying its already-obsolete
    // rotated credential (the race window browsers really see).
    hold.release();
    let refresh = refresh_task.await.expect("refresh task");
    assert_eq!(refresh.status, StatusCode::OK, "{:?}", refresh.body);
    let late_b = rotated_token_of(&refresh);

    // The LATE B cookie must fail server-side: both it and A are
    // credentials-invalid now.
    assert_credentials_invalid_via_groups(&fixture.v1_router, &late_b).await;
    assert_credentials_invalid_via_groups(&fixture.v1_router, &seeded.token).await;
    assert_eq!(fixture.group.calls("list_groups"), 0);
}

/// A NEW login (fresh session instance) is never affected by the old
/// logout: revoke kills only its own session id, and the new login's
/// CAS-install reads the CURRENT revision after the revoke bump.
#[tokio::test]
async fn a_new_login_survives_an_old_logout_of_the_same_identity() {
    let fixture = fresh().await;
    let seeded = fixture.seed_session("race-new-login").await;

    // The old session's logout commits first.
    let logout = drive_entry(&fixture, Entry::V1, "POST", V1_LOGOUT, &seeded.token).await;
    assert_eq!(logout.status, StatusCode::OK, "{:?}", logout.body);
    assert_credentials_invalid_via_groups(&fixture.v1_router, &seeded.token).await;

    // A completed login for the SAME identity installs its fresh session
    // through the REAL engine — exactly the post-exchange install the
    // callback bridge performs (read the current revision, sign a NEW
    // random session id, CAS-install).
    let login = fixture.login_new_session(&seeded).await;
    assert_ne!(login.sid, seeded.sid, "a new login is a fresh session instance");

    // The new login is live and reaches the business exactly once; the
    // old logout stays revoked.
    let before = fixture.group.calls("list_groups");
    let driven = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &[("cookie", format!("bcs_session={}", login.token))],
    )
    .await;
    assert_eq!(driven.status, StatusCode::OK, "{:?}", driven.body);
    assert_eq!(fixture.group.calls("list_groups"), before + 1);
    assert_credentials_invalid_via_groups(&fixture.v1_router, &seeded.token).await;
    assert_eq!(
        fixture.group.calls("list_groups"),
        before + 1,
        "the revoked old session never re-enters the business service"
    );
}

/// Two concurrent refreshes of the SAME session A: both read A at the
/// named read point (one shared barrier holds them together), then both
/// race the rotate CAS — exactly ONE commits, the loser never hands out
/// a credential, and the winner's rotated credential is genuinely live.
#[tokio::test]
async fn two_concurrent_refreshes_of_a_commit_exactly_one_rotation() {
    let fixture = fresh().await;
    let seeded = fixture.seed_session("race-two-refreshes").await;
    fixture.db.reset_counts();

    // The named READ point: both refreshes' strict SELECTs park on one
    // shared barrier before either may proceed to its CAS.
    let barrier = fixture
        .db
        .control
        .install_barrier(NamedOp::StrictSelect, Phase::Post, 3);

    let first = spawn_entry_refresh(&fixture, Entry::V1, &seeded.token);
    let second = spawn_entry_refresh(&fixture, Entry::V1, &seeded.token);

    // The test is the third barrier party, so both reads are guaranteed
    // complete before ANY rotate executes.
    barrier.wait().await;
    let (first, second) = (
        first.await.expect("first refresh"),
        second.await.expect("second refresh"),
    );

    assert_eq!(
        fixture.db.control.rotates.load(Ordering::SeqCst),
        2,
        "both refreshes reached their rotate CAS after their reads"
    );
    assert_eq!(
        fixture.db.control.applied_rotates.load(Ordering::SeqCst),
        1,
        "exactly one rotation commits"
    );
    assert_eq!(
        fixture.db.control.conflicted_rotates.load(Ordering::SeqCst),
        1,
        "the other rotation loses the CAS"
    );

    let statuses = [first.status, second.status];
    assert_eq!(
        statuses.iter().filter(|status| **status == StatusCode::OK).count(),
        1,
        "exactly one refresh wins: {statuses:?}"
    );
    assert_eq!(
        statuses.iter().filter(|status| **status == StatusCode::UNAUTHORIZED).count(),
        1,
        "the losing refresh lowers to Unauthenticated: {statuses:?}"
    );

    let (winner, loser) = if first.status == StatusCode::OK {
        (first, second)
    } else {
        (second, first)
    };
    assert_no_session_cookie(&loser);
    let winner_token = rotated_token_of(&winner);

    // The winner's rotated credential is genuinely live and the shared
    // row advanced by exactly one revision.
    let before = fixture.group.calls("list_groups");
    let driven = drive(
        &fixture.v1_router,
        "GET",
        GROUPS,
        &[("cookie", format!("bcs_session={winner_token}"))],
    )
    .await;
    assert_eq!(driven.status, StatusCode::OK, "{:?}", driven.body);
    assert_eq!(fixture.group.calls("list_groups"), before + 1);
    let revision = fixture
        .identity
        .read_session_revision(&bcs_auth_api::session::SessionScope {
            user_id: seeded.user_id.clone(),
            provider: "github".to_string(),
            env: api_chain::ENV.to_string(),
        })
        .await
        .expect("read the shared row's revision");
    assert_eq!(revision, seeded.revision + 1);
}

/// Two INDEPENDENT store instances (separate `DbUserIdentityStore`s, each
/// with its own DbPlugin wrapper over its own connection) on ONE shared
/// SQLite database: the refresh on the V1 entry reads A and pauses; the
/// logout through the LEGACY entry's independent store commits against
/// the same rows; the resumed refresh still loses its CAS — the
/// atomicity lives in the persisted row, not in any process-local state.
#[tokio::test]
async fn independent_store_instances_over_one_database_still_serialize_the_cas() {
    let dir = tempfile::TempDir::new().expect("temp dir for the shared sqlite db");
    let mut spec = FixtureSpec::standard();
    spec.independent_legacy_store = true;
    spec.sqlite_path = Some(dir.path().join("task13-shared-race.sqlite"));

    let fixture = ApiChainFixture::assemble(spec).await;
    refresh_reading_a_loses_to_a_committed_logout(&fixture, Entry::V1, Entry::Legacy).await;
}
