//! Rule 25 driver: runs the `BotAuthorityCoreService`
//! shared conformance harness against the REAL
//! [`BotAuthorityCoreServiceImpl`] composed over the REAL
//! `DbBotAuthorityStore` (SQLite, the same shared
//! fixture the store suites drive).

use bcs_edge_permission::BotAuthorityCoreServiceImpl;
use bcs_test_support::bot_authority_sqlite::new_fixture;
use bcs_test_support::contract::bot_authority_contract::BotAuthorityContractFixture;
use bcs_test_support::contract::bot_authority_core::bot_authority_core_service_contract_tests;

const ENV: &str = "authority-env";

/// The core read/write semantics over the SQL authority
/// store: strict role mapping, deny helpers, ownership
/// and manager projections, uninitialized/corrupt/deleted
/// state, and the manager-write + transfer round trips.
#[tokio::test]
async fn bot_authority_core_service_contract_sqlite() {
    let fixture = new_fixture(ENV).await;
    let core = BotAuthorityCoreServiceImpl::new(
        fixture.authority_repo(),
        ENV,
    );
    bot_authority_core_service_contract_tests(&core, &fixture, ENV)
        .await;
}
