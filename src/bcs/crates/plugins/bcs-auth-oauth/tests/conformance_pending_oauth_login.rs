//! Pending OAuth login store conformance test driver.
//!
//! Invokes the central `pending_oauth_login_store_contract_tests` harness
//! against the in-memory `MemoryPendingOAuthLoginStore`. The harness is
//! object-safe and uses only the trait surface; this driver only wires
//! the concrete implementation and exercises the verbatim cross-browser
//! snippet the plan requires (spec §8.4 / Task 9).
//!
//! # What this driver covers
//!
//! - The full pending-login store conformance suite (see the harness docs
//!   in `bcs-test-support/.../pending_oauth_login.rs`).
//! - The verbatim Task 9 cross-browser snippet (asserts wrong-browser
//!   consume is rejected AND the subsequent correct-browser consume still
//!   succeeds — the wrong-browser attempt does NOT burn A's pending state).
//!
//! # What this driver does NOT cover
//!
//! `LoginStateError::Unavailable` is never returned by the memory store
//! (no failure modes beyond panics). Fault-injection tests for non-memory
//! (DB-backed) bridges will live in Task 11+ drivers, not here.

#![allow(clippy::unwrap_used, clippy::expect_used)]

use bcs_auth_api::PendingOAuthLoginStore;
use bcs_auth_oauth::MemoryPendingOAuthLoginStore;
use bcs_test_support::contract::plugin::pending_oauth_login_store_contract_tests;

/// Memory store passes the full pending-login contract suite.
#[tokio::test]
async fn memory_store_passes_pending_login_contract() {
    let store = MemoryPendingOAuthLoginStore::new();
    pending_oauth_login_store_contract_tests(&store).await;
}

/// Verbatim cross-browser snippet from the Task 9 brief.
///
/// A wrong-browser consume (state matches but nonce is `"browser-b"`)
/// MUST be rejected, AND the subsequent correct-browser consume (same
/// state with `batch.browser_nonce`) MUST succeed — confirming the
/// failed cross-browser attempt did NOT burn the pending state.
#[tokio::test]
async fn cross_browser_snippet() {
    let store = MemoryPendingOAuthLoginStore::new();
    let batch = store
        .issue_batch(
            &["github".into()],
            "https://bcs.example.com/openapi/v1/auth/callback",
            "v1",
            100,
        )
        .await
        .unwrap();
    let state = &batch.provider_states[0].1;
    assert!(store
        .consume(
            state,
            "browser-b",
            "github",
            "https://bcs.example.com/openapi/v1/auth/callback/github",
            "v1",
            101,
        )
        .await
        .is_err());
    assert!(store
        .consume(
            state,
            &batch.browser_nonce,
            "github",
            "https://bcs.example.com/openapi/v1/auth/callback/github",
            "v1",
            101,
        )
        .await
        .is_ok());
}
