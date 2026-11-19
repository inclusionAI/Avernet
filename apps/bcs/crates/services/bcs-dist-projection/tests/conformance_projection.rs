use bcs_dist_projection::OutputProjectionService;
use bcs_test_support::contract::distributed::runtime_safety::output_projection_core_service_contract_tests;
#[test]
fn conformance_output_projection() {
    output_projection_core_service_contract_tests(&OutputProjectionService);
}
