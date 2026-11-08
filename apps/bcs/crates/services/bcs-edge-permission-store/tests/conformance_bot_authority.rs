//! Rule 25 driver: runs the full
//! `BotAuthorityRepoPort` shared conformance harness
//! against the SQLite-backed [`DbBotAuthorityStore`]
//! (post-migration 034-shaped schema, the SAME fixture
//! the store's `authority_reads` suite and the
//! CI-only MySQL gate drive).

use bcs_test_support::bot_authority_sqlite::new_fixture;
use bcs_test_support::contract::bot_authority_contract::{
    BotAuthorityContractFixture, bot_authority_repo_port_contract_tests,
};

const ENV: &str = "authority-env";
const OTHER_ENV: &str = "authority-env-other";

/// The shared port contract on the SQL authority store:
/// strict reads, manager writes, transfer writes and
/// audit semantics (identical to the memory projection
/// and the CI MySQL gate).
#[tokio::test]
async fn bot_authority_repo_port_contract_sqlite() {
    let fixture = new_fixture(ENV).await;
    // The impl under test stays explicit: the same
    // `DbBotAuthorityStore` the store suites drive.
    let store = fixture.store.clone();
    let _typed: &bcs_edge_permission_store::DbBotAuthorityStore =
        store.as_ref();
    bot_authority_repo_port_contract_tests(
        &fixture,
        ENV,
        OTHER_ENV,
    )
    .await;
}

/// Entry integrity: the port object MUST be the
/// `DbBotAuthorityStore` instance the fixture builds
/// (not a test double).
#[tokio::test]
async fn bot_authority_repo_port_is_the_sql_store() {
    let fixture = new_fixture(ENV).await;
    let repo = fixture.authority_repo();
    let _ = &repo as &std::sync::Arc<
        dyn bcs_service_api::port::repo::BotAuthorityRepoPort,
    >;
    // Sanity read through the store's strict read path:
    // an unknown bot is Ok(None), never an error.
    assert_eq!(
        repo.role("human_none", "bot_none")
            .await
            .expect("unknown-bot role"),
        None
    );
}
