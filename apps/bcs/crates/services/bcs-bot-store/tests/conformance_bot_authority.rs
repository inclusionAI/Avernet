//! Rule 25 driver: runs the shared conformance
//! harnesses against the REAL [`MemoryBotRepo`]:
//! - the FULL `BotAuthorityRepoPort` contract on the
//!   memory projection (the exact same fixture body
//!   `memory_authority.rs` drives),
//! - the `PermissionProfileRepoPort` contract on the
//!   same shared memory state (the registration profile
//!   projection the runtime consumers share).

#[path = "memory_authority_fixture.rs"]
mod memory_authority_fixture;

use std::sync::Arc;

use bcs_bot_store::MemoryBotRepo;
use bcs_test_support::contract::bot_authority_contract::bot_authority_repo_port_contract_tests;
use bcs_test_support::contract::repo::permission_profile_repo_port_contract_tests;

use memory_authority_fixture::{MemoryAuthorityFixture, target_env};

/// The shared `BotAuthorityRepoPort` contract on the
/// memory projection: strict reads, manager writes,
/// transfer writes and audit semantics — byte-equal to
/// the SQL store's outcomes.
#[tokio::test]
async fn bot_authority_repo_port_contract_memory() {
    let env = target_env().await;
    let other = format!("{env}-other");
    let fixture = MemoryAuthorityFixture {
        repo: Arc::new(MemoryBotRepo::new()),
    };
    // The impl under test stays explicit: the shared
    // atomic memory state of `MemoryBotRepo`.
    let _typed: &MemoryBotRepo = fixture.repo.as_ref();
    bot_authority_repo_port_contract_tests(&fixture, &env, &other)
        .await;
}

/// The shared `PermissionProfileRepoPort` contract on
/// the same memory state (registration profile /
/// runtime consumers share one projection).
#[tokio::test]
async fn permission_profile_repo_port_contract_memory() {
    let env = target_env().await;
    let repo = Arc::new(MemoryBotRepo::new());
    let _typed: &MemoryBotRepo = repo.as_ref();
    permission_profile_repo_port_contract_tests(repo.as_ref(), &env)
        .await;
}
