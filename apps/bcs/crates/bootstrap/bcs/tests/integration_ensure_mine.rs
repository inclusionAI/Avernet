//! T-7 — Integration tests (HTTP end-to-end) for `POST /me/ensure-human`.
//!
//! Uses `start_test_server` with `BCS_AUTH_MOCK=1` to exercise the full HTTP
//! stack, covering:
//! - 200 success with correct response shape
//! - 401 when no login identity (missing headers AND empty staff_no)
//! - 400 when `staff_no` contains special characters
//! - matched_bots populated after pre-onboarding a legacy bot
//!
//! **Note on "missing edges" coverage**: The primary use case of this endpoint
//! — repairing edges for legacy bots that have `created_by IS NULL` and a
//! whitelisted namespace `bot_uuid` — cannot be tested end-to-end at the HTTP
//! layer because (a) WebSocket `bot.connect` assigns random UUIDs that don't
//! match `default:{staff_no}` format, and (b) the onboard flow always creates
//! edges via `ensure_human_actor_and_owner_edges`. This scenario is fully
//! covered by the unit tests in `ensure_mine_unit.rs` (see
//! `full_flow_existing_user_with_legacy_bots` and the `list_legacy_*` tests).

mod helpers;

use helpers::*;
use serde_json::Value;

// ============================================================================
// HTTP helpers
// ============================================================================

/// POST /me/ensure-human with mock staff identity.
async fn ensure_human(
    addr: std::net::SocketAddr,
    staff_no: &str,
) -> reqwest::Response {
    reqwest::Client::new()
        .post(format!("http://{}/me/ensure-human", addr))
        .header("X-Mock-User-Id", staff_no)
        .header("X-Mock-Nick-Name", format!("User-{}", staff_no))
        .send()
        .await
        .expect("HTTP request to /me/ensure-human failed")
}

/// POST /me/ensure-human without any identity headers.
async fn ensure_human_no_auth(addr: std::net::SocketAddr) -> reqwest::Response {
    reqwest::Client::new()
        .post(format!("http://{}/me/ensure-human", addr))
        .send()
        .await
        .expect("HTTP request to /me/ensure-human failed")
}

// ============================================================================
// Tests
// ============================================================================

/// New user, no bots → 200 with `human_created=true`, empty `matched_bots`.
#[tokio::test]
async fn ensure_human_new_user_returns_200() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    let resp = ensure_human(addr, "newstaff001").await;
    assert_eq!(resp.status(), 200, "expected 200 for new user");

    let body: Value = resp.json().await.expect("parse JSON body");
    assert_eq!(body["actor_uuid"], "human_newstaff001");
    assert_eq!(body["human_created"], true);
    assert!(
        body["matched_bots"].as_array().unwrap().is_empty(),
        "no bots should match for a fresh server"
    );
    assert_eq!(body["edges_created"], 0);
    assert_eq!(body["edges_upgraded"], 0);
    assert!(body["failed_bots"].as_array().unwrap().is_empty());
}

/// Idempotent: second call → 200 with `human_created=false`.
#[tokio::test]
async fn ensure_human_idempotent_second_call() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    // First call
    let resp1 = ensure_human(addr, "idempotent01").await;
    assert_eq!(resp1.status(), 200);
    let body1: Value = resp1.json().await.unwrap();
    assert_eq!(body1["human_created"], true);

    // Second call
    let resp2 = ensure_human(addr, "idempotent01").await;
    assert_eq!(resp2.status(), 200);
    let body2: Value = resp2.json().await.unwrap();
    assert_eq!(body2["human_created"], false);
}

/// No login identity → 401.
#[tokio::test]
async fn ensure_human_no_auth_returns_401() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    let resp = ensure_human_no_auth(addr).await;
    assert_eq!(resp.status(), 401, "missing identity must return 401");

    let body: Value = resp.json().await.unwrap();
    assert!(
        body["error"].as_str().unwrap_or("").contains("登录态"),
        "error should mention login requirement, got: {}",
        body["error"]
    );
}

/// Empty staff_no → 401 (not 400).
///
/// Regression: requirements specify "认证上下文缺失或 staff_no 为空" → 401.
/// An empty string from the auth SDK must be treated the same as missing.
#[tokio::test]
async fn ensure_human_empty_staff_no_returns_401() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    // Send empty string as staff_no — should be treated as "missing"
    let resp = ensure_human(addr, "").await;
    assert_eq!(
        resp.status(),
        401,
        "empty staff_no must return 401, not 400"
    );

    let body: Value = resp.json().await.unwrap();
    assert!(
        body["error"].as_str().unwrap_or("").contains("登录态"),
        "error should mention login requirement, got: {}",
        body["error"]
    );
}

/// staff_no with special characters → 400.
#[tokio::test]
async fn ensure_human_invalid_staff_no_returns_400() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    // staff_no with SQL injection characters
    let resp = ensure_human(addr, "staff%_no").await;
    assert_eq!(resp.status(), 400, "special chars must return 400");

    let body: Value = resp.json().await.unwrap();
    assert!(
        body["error"].as_str().unwrap_or("").contains("staff_no"),
        "error should mention invalid staff_no, got: {}",
        body["error"]
    );
}

/// Pre-onboard a bot as a user → ensure-human picks it up in `matched_bots`.
#[tokio::test]
async fn ensure_human_with_preexisting_bot_matches() {
    let _ = tracing_subscriber::fmt::try_init();
    let tmp = create_temp_bots_dir();
    let (addr, _srv) = start_test_server(&tmp.path().to_path_buf()).await;
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    let staff_no = "botowner42";

    // Step 1: Connect a bot via WebSocket and onboard it with the owner identity
    let mut bot = MockBot::connect(addr).await;
    bot.register("LegacyBot", &[], addr).await;
    let bot_uuid = bot.bot_id.clone();

    // Onboard as user — this sets `created_by = staff_no` on the bot
    onboard_bot_as_user(addr, &bot.token, "LegacyBot", staff_no).await;

    // Step 2: Call ensure-human → the bot should appear in matched_bots
    let resp = ensure_human(addr, staff_no).await;
    assert_eq!(resp.status(), 200);

    let body: Value = resp.json().await.unwrap();
    assert_eq!(body["actor_uuid"], format!("human_{}", staff_no));
    // `onboard_bot_as_user` already calls `ensure_human_actor_and_owner_edges`
    // internally, so the Human row already exists → `human_created=false`.
    assert_eq!(body["human_created"], false);

    let matched: Vec<&str> = body["matched_bots"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|v| v.as_str())
        .collect();
    assert!(
        matched.contains(&bot_uuid.as_str()),
        "matched_bots should contain the pre-onboarded bot {}, got: {:?}",
        bot_uuid,
        matched,
    );

    // Owner edges were already created during onboard, so `ensure_owner_edges_counted`
    // finds them already present → edges_created=0, edges_upgraded=0 (idempotent).
    assert_eq!(body["edges_created"], 0);
    assert_eq!(body["edges_upgraded"], 0);
}
