//! Log-only operation observations and transport-neutral correlation data.
//!
//! Logs use the subscriber installed by bootstrap. This crate does not create
//! spans, read tracing SDK context, record metrics, or inspect operation payloads.
//! Correlation uses request IDs and parent/child operation IDs, independently of tracing.

mod operation;
pub use operation::{CurrentRequestId, Operation, observe_result, observe_value, with_request_context, with_request_id, in_current_context, count, current_operation_id, current_request_id};

/// Identifies this process instance across restarts, independently of tracing.
/// Numeric client/pool IDs are only meaningful together with this value.
pub fn process_instance_id() -> &'static str {
    static ID: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    ID.get_or_init(|| uuid::Uuid::new_v4().to_string())
}
