//! `PendingOAuthLoginStore` conformance harness.
//!
//! Concrete implementations (the memory-backed `MemoryPendingOAuthLoginStore`
//! in `bcs-auth-oauth`, future DB-backed plugin-API bridges) call
//! [`pending_oauth_login_store_contract_tests`] from a
//! `tests/conformance_pending_oauth_login.rs` driver, passing a fresh
//! `&dyn PendingOAuthLoginStore`. The harness is object-safe and uses only
//! the trait surface, so the same suite covers every implementation that
//! targets the pending-login store contract.
//!
//! # Step-4 matrix covered (spec §8.4 / plan Task 9)
//!
//! - Cross-browser rejection (pending state NOT consumed by wrong browser;
//!   verified by re-consuming with the correct nonce after a failed
//!   attempt — see [`cross_browser_rejection_contract`]).
//! - Same-batch second consume fails (atomic batch consumption).
//! - Expiry (state rejected once TTL passed; in-TTL state still consumable
//!   after an expired attempt).
//! - Provider mismatch (state-for-GITHUB rejected at GOOGLE's callback;
//!   non-consuming; correct-provider consume still succeeds).
//! - Callback mismatch (same provider but wrong `exact_callback` URL;
//!   non-consuming; correct-callback consume still succeeds).
//! - Flow namespace mismatch (`v1` state at `legacy` callback;
//!   non-consuming; correct-flow consume still succeeds).
//! - Nonce ≠ state (issued nonce and state are independent CSPRNG draws).
//!
//! # What this harness does NOT cover
//!
//! DB-fault classification (`LoginStateError::Unavailable` under storage
//! failure) is specific to the concrete store/bridge combination, so it
//! lives in the consumer `conformance_pending_oauth_login.rs` test, NOT
//! here. The memory store itself never returns `Unavailable` — that branch
//! is exercised only by future DB-backed bridges via fault injection.

use bcs_auth_api::{LoginStateError, PendingOAuthLoginStore};

const PROVIDER_GITHUB: &str = "github";
const PROVIDER_GOOGLE: &str = "google";
const CALLBACK_BASE: &str = "https://bcs.example.com/openapi/v1/auth/callback";
const FLOW_V1: &str = "v1";
const FLOW_LEGACY: &str = "legacy";

fn exact_callback_for(provider: &str) -> String {
    format!("{CALLBACK_BASE}/{provider}")
}

/// Cross-browser rejection contract: browser B's wrong-nonce consume of
/// browser A's legal pending state fails AND does NOT burn A's state. The
/// subsequent correct-nonce consume succeeds. This is the spec §8.4
/// cross-browser defense — a failed cross-browser consume must not give
/// the attacker a side channel to deny the legitimate browser its login.
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn cross_browser_rejection_contract(store: &dyn PendingOAuthLoginStore) {
    let batch = store
        .issue_batch(&[PROVIDER_GITHUB.to_string()], CALLBACK_BASE, FLOW_V1, 100)
        .await
        .expect("issue_batch must succeed");
    let (provider, state) = &batch.provider_states[0];
    assert_eq!(provider, PROVIDER_GITHUB, "provider_states[0] must be github");

    let exact_callback = exact_callback_for(PROVIDER_GITHUB);

    // Browser B presents A's state with the wrong nonce — must reject.
    let wrong = store
        .consume(state, "browser-b", provider, &exact_callback, FLOW_V1, 101)
        .await
        .expect_err("cross-browser consume MUST be rejected");
    assert_eq!(
        wrong,
        LoginStateError::Invalid,
        "wrong-nonce consume returns Invalid"
    );

    // Browser A then presents the correct nonce — must succeed. The
    // wrong-nonce attempt did NOT consume A's state.
    store
        .consume(
            state,
            &batch.browser_nonce,
            provider,
            &exact_callback,
            FLOW_V1,
            101,
        )
        .await
        .expect("correct-nonce consume after a wrong-nonce attempt MUST succeed");
}

/// Conformance suite for [`PendingOAuthLoginStore`]. Covers the full
/// step-4 matrix described in the module docs.
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn pending_oauth_login_store_contract_tests(store: &dyn PendingOAuthLoginStore) {
    cross_browser_rejection_contract(store).await;
    same_batch_second_consume_fails_contract(store).await;
    expiry_rejects_contract(store).await;
    provider_mismatch_rejects_contract(store).await;
    callback_mismatch_rejects_contract(store).await;
    flow_mismatch_rejects_contract(store).await;
    nonce_not_equal_to_state_contract(store).await;
}

#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
async fn same_batch_second_consume_fails_contract(store: &dyn PendingOAuthLoginStore) {
    let batch = store
        .issue_batch(
            &[PROVIDER_GITHUB.to_string(), PROVIDER_GOOGLE.to_string()],
            CALLBACK_BASE,
            FLOW_V1,
            100,
        )
        .await
        .expect("issue_batch must succeed");
    let (gh_provider, gh_state) = &batch.provider_states[0];
    let (go_provider, go_state) = &batch.provider_states[1];
    let gh_callback = exact_callback_for(gh_provider);
    let go_callback = exact_callback_for(go_provider);

    // First consume (github) fully matches — succeeds and atomically
    // consumes the WHOLE batch, including the google sibling state.
    store
        .consume(
            gh_state,
            &batch.browser_nonce,
            gh_provider,
            &gh_callback,
            FLOW_V1,
            101,
        )
        .await
        .expect("first fully-matching consume must succeed");

    // Second consume against any other state in the same batch fails
    // (those records were removed atomically with the first consume).
    let second = store
        .consume(
            go_state,
            &batch.browser_nonce,
            go_provider,
            &go_callback,
            FLOW_V1,
            101,
        )
        .await
        .expect_err("second consume in the same batch must fail");
    assert_eq!(
        second,
        LoginStateError::Invalid,
        "second consume after atomic batch consumption returns Invalid"
    );
}

#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
async fn expiry_rejects_contract(store: &dyn PendingOAuthLoginStore) {
    // Issued at now=100, expires at 100+300=400.
    let batch = store
        .issue_batch(&[PROVIDER_GITHUB.to_string()], CALLBACK_BASE, FLOW_V1, 100)
        .await
        .expect("issue_batch must succeed");
    let (provider, state) = &batch.provider_states[0];
    let cb = exact_callback_for(provider);

    // Consume AFTER TTL → Invalid. An expired state cannot burn its
    // sibling states in the batch — a single state expiring only fails
    // its own consume.
    let expired = store
        .consume(
            state,
            &batch.browser_nonce,
            provider,
            &cb,
            FLOW_V1,
            100 + 301,
        )
        .await
        .expect_err("expired state MUST reject");
    assert_eq!(expired, LoginStateError::Invalid, "expired consume is Invalid");

    // Before-TTL consume still succeeds — the expired attempt did NOT
    // consume the record.
    store
        .consume(
            state,
            &batch.browser_nonce,
            provider,
            &cb,
            FLOW_V1,
            100 + 299,
        )
        .await
        .expect("in-TTL consume after an expired attempt MUST succeed");
}

#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
async fn provider_mismatch_rejects_contract(store: &dyn PendingOAuthLoginStore) {
    // Issue a single state bound to github.
    let batch = store
        .issue_batch(&[PROVIDER_GITHUB.to_string()], CALLBACK_BASE, FLOW_V1, 100)
        .await
        .expect("issue_batch must succeed");
    let (_, state) = &batch.provider_states[0];

    // Present the github state with google's provider/callback — must
    // reject, batch NOT consumed.
    let google_cb = exact_callback_for(PROVIDER_GOOGLE);
    let mismatch = store
        .consume(state, &batch.browser_nonce, PROVIDER_GOOGLE, &google_cb, FLOW_V1, 101)
        .await
        .expect_err("provider mismatch MUST reject");
    assert_eq!(mismatch, LoginStateError::Invalid, "provider mismatch is Invalid");

    // Correct-provider consume still succeeds.
    let github_cb = exact_callback_for(PROVIDER_GITHUB);
    store
        .consume(
            state,
            &batch.browser_nonce,
            PROVIDER_GITHUB,
            &github_cb,
            FLOW_V1,
            101,
        )
        .await
        .expect("correct-provider consume MUST succeed after a mismatch attempt");
}

#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
async fn callback_mismatch_rejects_contract(store: &dyn PendingOAuthLoginStore) {
    let batch = store
        .issue_batch(&[PROVIDER_GITHUB.to_string()], CALLBACK_BASE, FLOW_V1, 100)
        .await
        .expect("issue_batch must succeed");
    let (provider, state) = &batch.provider_states[0];

    // Same provider but a different exact_callback URL (attacker domain)
    // — must reject, batch NOT consumed.
    let wrong_cb = "https://attacker.example.com/openapi/v1/auth/callback/github";
    let mismatch = store
        .consume(state, &batch.browser_nonce, provider, wrong_cb, FLOW_V1, 101)
        .await
        .expect_err("callback mismatch MUST reject");
    assert_eq!(mismatch, LoginStateError::Invalid, "callback mismatch is Invalid");

    let cb = exact_callback_for(provider);
    store
        .consume(state, &batch.browser_nonce, provider, &cb, FLOW_V1, 101)
        .await
        .expect("correct-callback consume MUST succeed after a mismatch attempt");
}

#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
async fn flow_mismatch_rejects_contract(store: &dyn PendingOAuthLoginStore) {
    let batch = store
        .issue_batch(&[PROVIDER_GITHUB.to_string()], CALLBACK_BASE, FLOW_V1, 100)
        .await
        .expect("issue_batch must succeed");
    let (provider, state) = &batch.provider_states[0];
    let cb = exact_callback_for(provider);

    // Right provider/callback but wrong flow namespace (`legacy` vs `v1`)
    // — must reject, batch NOT consumed.
    let mismatch = store
        .consume(state, &batch.browser_nonce, provider, &cb, FLOW_LEGACY, 101)
        .await
        .expect_err("flow mismatch MUST reject");
    assert_eq!(mismatch, LoginStateError::Invalid, "flow mismatch is Invalid");

    store
        .consume(state, &batch.browser_nonce, provider, &cb, FLOW_V1, 101)
        .await
        .expect("correct-flow consume MUST succeed after a mismatch attempt");
}

#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test harness — panic on failure is the contract"
)]
async fn nonce_not_equal_to_state_contract(store: &dyn PendingOAuthLoginStore) {
    let batch = store
        .issue_batch(&[PROVIDER_GITHUB.to_string()], CALLBACK_BASE, FLOW_V1, 100)
        .await
        .expect("issue_batch must succeed");
    let (_, state) = &batch.provider_states[0];
    assert_ne!(
        &batch.browser_nonce, state,
        "nonce MUST NOT equal the state value (CSPRNG independence)"
    );
    // Multiple states are mutually distinct as well.
    let batch2 = store
        .issue_batch(
            &[PROVIDER_GITHUB.to_string(), PROVIDER_GOOGLE.to_string()],
            CALLBACK_BASE,
            FLOW_V1,
            100,
        )
        .await
        .expect("issue_batch must succeed");
    let (p0, s0) = &batch2.provider_states[0];
    let (p1, s1) = &batch2.provider_states[1];
    assert_ne!(s0, s1, "per-provider states of one batch are distinct");
    assert_ne!(
        &batch2.browser_nonce, s0,
        "nonce MUST NOT equal any provider state"
    );
    assert_ne!(
        &batch2.browser_nonce, s1,
        "nonce MUST NOT equal any provider state"
    );
    assert_ne!(p0, p1, "providers are preserved in order");
}
