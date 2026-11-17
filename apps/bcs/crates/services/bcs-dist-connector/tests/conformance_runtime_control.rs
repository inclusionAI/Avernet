//! Conformance entry: `ConnectorEngine` against the runtime control harness.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

mod rig;

use bcs_test_support::contract::distributed::services::dist_runtime_control_port_contract_tests;

#[tokio::test]
async fn conformance_connector_engine_runtime_control() {
    let rig = rig::Rig::new();
    dist_runtime_control_port_contract_tests(&rig.engine()).await.unwrap();
}
