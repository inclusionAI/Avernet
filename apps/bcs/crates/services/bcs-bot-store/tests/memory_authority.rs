//! The memory projection's bot-authority contract
//! (`BotAuthorityRepoPort` on `MemoryBotRepo`,
//! bot-authority plan Task 1 reads + Task 2 manager
//! writes + Task 3 transfer writes), driven through the
//! SAME shared harness scenarios as the SQL authority
//! Store so the two stores cannot drift.
//!
//! The fixture drives the SAME shared atomic state
//! boundary (`MemoryBotState`'s authority facet) the
//! production facets read and write, per spec §7, via
//! `MemoryBotRepo::authority_state` — not through any
//! test-specific write path. The fixture body lives in
//! `memory_authority_fixture.rs` (shared with the Rule 25
//! conformance entry).

#![allow(clippy::expect_used, clippy::unwrap_used)]

#[path = "memory_authority_fixture.rs"]
mod memory_authority_fixture;

use std::sync::Arc;

use bcs_bot_store::MemoryBotRepo;
use bcs_test_support::contract::bot_authority_contract::run_bot_authority_contract;
use bcs_test_support::contract::bot_transfer_contract::run_bot_transfer_contract;

use memory_authority_fixture::{MemoryAuthorityFixture, target_env};

/// The FULL shared harness (Task-1 strict reads + Task-2
/// manager writes + Task-3 transfer writes) must produce
/// the same legal results and errors as the SQL authority
/// Store.
#[tokio::test]
async fn memory_bot_repo_bot_authority_contract() {
    let env = target_env().await;
    let other = format!("{env}-other");
    let fixture = MemoryAuthorityFixture {
        repo: Arc::new(MemoryBotRepo::new()),
    };
    run_bot_authority_contract(&fixture, &env, &other).await;
}

/// The FULL shared transfer harness (Task 3) on the memory
/// projection: identical outcomes to the SQL store.
#[tokio::test]
async fn memory_bot_repo_transfer_contract() {
    let env = target_env().await;
    let fixture = MemoryAuthorityFixture {
        repo: Arc::new(MemoryBotRepo::new()),
    };
    run_bot_transfer_contract(&fixture, &env).await;
}
