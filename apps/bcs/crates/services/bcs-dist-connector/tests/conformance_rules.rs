//! Conformance entry: `DistConnectorRules` against the core contract harness.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod common;

use bcs_dist_connector::rules::DistConnectorRules;
use bcs_dist_model::DispatchPhase;
use bcs_test_support::contract::distributed::services::dist_connector_core_service_contract_tests;

#[test]
fn conformance_dist_connector_rules() {
    dist_connector_core_service_contract_tests(
        &DistConnectorRules,
        &common::in_phase(DispatchPhase::Accepted),
    )
    .unwrap();
}
