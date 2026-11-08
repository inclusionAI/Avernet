#[path = "../../../test-support/message_flow_contract_support.rs"]
mod flow_support;
#[path = "support/session.rs"]
mod session_support;

#[path = "cases/caller_admission_contract.rs"]
mod caller_admission_contract;
#[path = "cases/caller_dispatch_access_revalidation.rs"]
mod caller_dispatch_access_revalidation;
#[path = "cases/caller_dispatch_commit_contract.rs"]
mod caller_dispatch_commit_contract;
#[path = "cases/caller_dispatch_pipeline_contract.rs"]
mod caller_dispatch_pipeline_contract;
#[path = "cases/caller_required_routes_gate.rs"]
mod caller_required_routes_gate;
