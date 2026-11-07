#[test]
fn execution_wait_total_budget_policy_conformance() {
    bcs_test_support::contract::core::run_budget::run_budget_core_service_contract_tests(&bcs_message_flow::run_budget::RunBudgetCore);
}
