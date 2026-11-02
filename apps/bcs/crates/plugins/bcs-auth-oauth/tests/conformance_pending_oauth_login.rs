//! Pending login batch conformance (V1 API auth plugin chain, Task 9).
//!
//! The centralized harness in `bcs-test-support` pins the browser-bound
//! state semantics from plan §8.4 (verbatim cross-browser sequence,
//! batch-wide invalidation, TTL, constant-time nonce hashing, and the
//! one-winner concurrent consume). This driver runs it against the
//! production memory store; Task 13 repeats the harness for any shared
//! persistence-backed store. The canonical R25 harness name is the
//! trait's `to_snake` (`pending_o_auth_login_store_contract_tests`).

use std::sync::Arc;

use bcs_auth_oauth::MemoryPendingOAuthLoginStore;
use bcs_test_support::contract::plugin::pending_o_auth_login_store_contract_tests;

#[tokio::test]
async fn pending_login_batch_contract_holds_over_the_memory_store() {
    pending_o_auth_login_store_contract_tests(Arc::new(MemoryPendingOAuthLoginStore::new())).await;
}
