//! T1 prototype conformance only, not a production-provider certification.
#![allow(clippy::unwrap_used, reason = "contract assertion")]
#[path = "group_round_support/mod.rs"]
mod support;
use bcs_test_support::contract::distributed::group_round::group_round_repo_contract_tests;

#[tokio::test]
async fn prototype_group_round_repo_conformance() {
    let prototype = support::Prototype::new().await;
    group_round_repo_contract_tests(&prototype, support::request("a", "first", 1)).await.unwrap();
}
