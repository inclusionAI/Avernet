#[path = "../../../test-support/message_flow_contract_support.rs"]
mod flow_support;
#[path = "support/session.rs"]
mod session_support;

#[path = "cases/caller_dispatch_cancel_contract.rs"]
mod caller_dispatch_cancel_contract;
#[path = "cases/caller_dispatch_stage_timeout_contract.rs"]
mod caller_dispatch_stage_timeout_contract;
#[path = "cases/caller_execution.rs"]
mod caller_execution;
#[path = "cases/caller_execution_control_contract.rs"]
mod caller_execution_control_contract;
#[path = "cases/caller_existing_events_contract.rs"]
mod caller_existing_events_contract;
#[path = "cases/caller_handoff_lifecycle.rs"]
mod caller_handoff_lifecycle;
#[path = "cases/caller_history_barrier_contract.rs"]
mod caller_history_barrier_contract;
#[path = "cases/caller_observe_fanout_wiring_contract.rs"]
mod caller_observe_fanout_wiring_contract;
#[path = "cases/caller_owner_relay_contract.rs"]
mod caller_owner_relay_contract;
#[path = "cases/caller_protected_dispatch_runtime_contract.rs"]
mod caller_protected_dispatch_runtime_contract;
#[path = "cases/caller_terminal_receipt_contract.rs"]
mod caller_terminal_receipt_contract;
