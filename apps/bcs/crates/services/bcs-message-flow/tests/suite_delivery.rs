#[path = "../../../test-support/message_flow_contract_support.rs"]
mod flow_support;
#[path = "support/session.rs"]
mod session_support;

#[path = "cases/conformance_delivery_scheduling.rs"]
mod conformance_delivery_scheduling;
#[path = "cases/conformance_managed_delivery.rs"]
mod conformance_managed_delivery;
#[path = "cases/conformance_message_delivery.rs"]
mod conformance_message_delivery;
#[path = "cases/conformance_queued_group.rs"]
mod conformance_queued_group;
#[path = "cases/conformance_queued_system.rs"]
mod conformance_queued_system;
#[path = "cases/conformance_queued_task.rs"]
mod conformance_queued_task;
#[path = "cases/delivery_failure_notifications.rs"]
mod delivery_failure_notifications;
#[path = "cases/delivery_history_query.rs"]
mod delivery_history_query;
#[path = "cases/delivery_policy.rs"]
mod delivery_policy;
#[path = "cases/delivery_query_visibility.rs"]
mod delivery_query_visibility;
#[path = "cases/delivery_runtime.rs"]
mod delivery_runtime;
#[path = "cases/managed_delivery.rs"]
mod managed_delivery;
#[path = "cases/operation_output_admission.rs"]
mod operation_output_admission;
#[path = "cases/operation_output_lifecycle.rs"]
mod operation_output_lifecycle;
#[path = "cases/operation_output_uncertainty.rs"]
mod operation_output_uncertainty;
#[path = "cases/queued_provider_headers.rs"]
mod queued_provider_headers;
