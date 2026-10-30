//! Workbench WS lease-command contract (spec §6.3, P3.3): the four lease
//! methods ride the authenticated connection — the holder identity is
//! ALWAYS the bound business actor, never a frame value. An unmounted
//! store answers with an explicit error instead of a fabricated lease.

#[path = "support/ws_lease_harness.rs"]
mod lease_harness;

use lease_harness::{lease_state, lease_state_without_store, send_frame};
use bcs_ws::web::{WebClientConnectionState, WorkbenchConnectionAuth};
use tokio::sync::mpsc;

fn user_bound(actor_id: &str) -> WorkbenchConnectionAuth {
    WorkbenchConnectionAuth::UserBound {
        actor_id: Some(actor_id.to_string()),
        caller_ingress: None,
    }
}

#[tokio::test]
async fn lease_acquire_preempt_release_current_round_trip() {
    let state = lease_state().await;
    let (tx, _rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();

    // Acquire as Alice.
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_alice"),
        "req-1", "lease.acquire", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert!(response.ok, "acquire ok: {response:?}");
    assert_eq!(response.payload["status"], "granted");
    let alice_lease_id = response.payload["leaseId"].as_str().unwrap().to_string();
    let alice_epoch = response.payload["epoch"].as_u64().unwrap();

    // Self acquire again: idempotent, same lease, never a steal.
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_alice"),
        "req-2", "lease.acquire", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert_eq!(response.payload["status"], "self_held");
    assert_eq!(response.payload["leaseId"].as_str().unwrap(), alice_lease_id);
    assert_eq!(response.payload["epoch"].as_u64().unwrap(), alice_epoch);

    // Bob acquires: occupied conflict, never a steal.
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_bob"),
        "req-3", "lease.acquire", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert_eq!(response.payload["status"], "occupied");
    assert_eq!(response.payload["holderActorId"], "human_alice");

    // Bob preempts: epoch strictly increases.
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_bob"),
        "req-4", "lease.preempt", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert_eq!(response.payload["status"], "granted");
    let bob_epoch = response.payload["epoch"].as_u64().unwrap();
    assert!(bob_epoch > alice_epoch);
    let bob_lease_id = response.payload["leaseId"].as_str().unwrap().to_string();

    // Current: held.
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_alice"),
        "req-5", "lease.current", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert_eq!(response.payload["status"], "held");
    assert_eq!(response.payload["epoch"].as_u64().unwrap(), bob_epoch);
    assert_eq!(response.payload["holderActorId"], "human_bob");
    // Bob releases with the matching coordinates.
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_bob"),
        "req-6", "lease.release", serde_json::json!({
            "groupId": "group-1",
            "leaseId": bob_lease_id,
            "leaseEpoch": bob_epoch,
        }),
    ).await;
    assert_eq!(response.payload["status"], "released");

    // Current again: no live lease.
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_alice"),
        "req-7", "lease.current", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert_eq!(response.payload["status"], "none");
}

#[tokio::test]
async fn lease_commands_fail_closed_without_a_store_or_an_actor() {
    let state_without_store = lease_state_without_store().await;
    let (tx, _rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();

    // Unmounted store: explicit error, never a fabricated lease.
    let response = send_frame(
        &state_without_store, &tx, &mut connection, &user_bound("human_alice"),
        "req-a", "lease.acquire", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert!(!response.ok);
    assert_eq!(response.error_code, "lease_unavailable");

    // Anonymous connection: an authenticated Human is required.
    let state = lease_state().await;
    let response = send_frame(
        &state, &tx, &mut connection,
        &WorkbenchConnectionAuth::UserBound { actor_id: None, caller_ingress: None },
        "req-b", "lease.acquire", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert!(!response.ok);
    assert_eq!(response.error_code, "unauthorized");

    // Release without lease coordinates: invalid request.
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_alice"),
        "req-c", "lease.release", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert!(!response.ok);
    assert_eq!(response.error_code, "invalid_request");
}

#[tokio::test]
async fn outsider_cannot_read_or_mutate_a_groups_lease() {
    let state = lease_state().await;
    let (tx, _rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();
    let granted = send_frame(&state, &tx, &mut connection, &user_bound("human_alice"),
        "owner", "lease.acquire", serde_json::json!({"groupId": "group-1"})).await;
    for method in ["lease.acquire", "lease.preempt", "lease.current", "lease.release"] {
        let response = send_frame(&state, &tx, &mut connection, &user_bound("mock_outsider"),
            "outsider", method, serde_json::json!({"groupId": "group-1",
                "leaseId": granted.payload["leaseId"], "leaseEpoch": granted.payload["epoch"]})).await;
        assert!(!response.ok, "{method} must check current membership");
        assert_eq!(response.error_code, "sender_not_in_group");
    }
    let current = send_frame(&state, &tx, &mut connection, &user_bound("human_alice"),
        "check", "lease.current", serde_json::json!({"groupId": "group-1"})).await;
    assert_eq!(current.payload["leaseId"], granted.payload["leaseId"]);
    assert_eq!(current.payload["epoch"], granted.payload["epoch"]);
}

#[tokio::test]
async fn session_bound_lease_cannot_escape_group_scope() {
    let state = lease_state().await;
    let (tx, _rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();
    connection.phase = bcs_ws::web::WebConnectionPhase::Connected;
    let auth = WorkbenchConnectionAuth::SessionBound {
        tenant: None, actor_id: "human_alice".into(), group_id: "other-group".into(),
        session_id: "other-session".into(), caller_ingress: None,
    };
    for method in ["lease.acquire", "lease.preempt", "lease.current", "lease.release"] {
        let response = send_frame(&state, &tx, &mut connection, &auth,
            "scope", method, serde_json::json!({"groupId": "group-1"})).await;
        assert!(!response.ok);
        assert_eq!(response.error_code, "token_scope_mismatch");
    }
    assert!(state.speaking_leases.as_ref().unwrap().current("group-1").await.unwrap().is_none());
}

#[tokio::test]
async fn lease_rechecks_authorization_after_membership_revocation() {
    use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
    let access = Arc::new(AtomicBool::new(true));
    let state = lease_harness::lease_state_with_access(access.clone());
    let (tx, _rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();
    let before = send_frame(&state, &tx, &mut connection, &user_bound("human_alice"),
        "before", "lease.acquire", serde_json::json!({"groupId": "group-1"})).await;
    assert!(before.ok);
    access.store(false, Ordering::SeqCst);
    let after = send_frame(&state, &tx, &mut connection, &user_bound("human_alice"),
        "after", "lease.preempt", serde_json::json!({"groupId": "group-1"})).await;
    assert_eq!(after.error_code, "sender_not_in_group");
    let lease = state.speaking_leases.as_ref().unwrap().current("group-1").await.unwrap().unwrap();
    assert_eq!(lease.epoch, before.payload["epoch"].as_u64().unwrap());
}

#[tokio::test]
async fn lease_window_follows_the_composition_configured_timeout() {
    // §15.99: acquire/preempt grant the CONFIGURED idle window (from
    // `[speaking_lease] idle_timeout_ms`), not the frozen 60s legacy
    // constant. The workbench frontend reads `expiresAt_ms` per lease, so a
    // deployment that widens the window needs no frontend change.
    use lease_harness::{lease_state_with_idle_timeout_ms, send_frame};

    let before_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64;
    let state = lease_state_with_idle_timeout_ms(90_000).await;
    let (tx, _rx) = mpsc::channel(8);
    let mut connection = WebClientConnectionState::default();
    let response = send_frame(
        &state, &tx, &mut connection, &user_bound("human_alice"),
        "req-window", "lease.acquire", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert!(response.ok, "acquire ok: {response:?}");
    let expires_at = response.payload["expiresAt_ms"].as_i64()
        .expect("granted lease carries an absolute expiry");
    let after_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64;
    let span = expires_at - after_ms;
    assert!(
        span > 0 && span <= 90_000 && expires_at - before_ms >= 89_000,
        "the granted window must span the configured 90s, got {span}ms"
    );

    // Frontends that honor the lease still speak within the WIDER window:
    // a submission 70s later (~11s beyond the legacy 60s) must still admit.
    // (The workbench submission check lives in message-flow and shares the
    // same configured value via the composition; here we pin the store-side
    // lease validity: `lease.current` still reports held at 70s.)
    tokio::time::pause();
    tokio::time::advance(std::time::Duration::from_millis(70_000)).await;
    let current = send_frame(
        &state, &tx, &mut connection, &user_bound("human_alice"),
        "req-hold", "lease.current", serde_json::json!({"groupId": "group-1"}),
    ).await;
    assert!(current.ok, "current ok: {current:?}");
    assert_eq!(current.payload["status"], "held",
        "the lease must survive 70s under the configured 90s window");
}
