//! Log-only operation observations and transport-neutral correlation data.
//!
//! Logs use the subscriber installed by bootstrap. This crate does not create
//! spans, read tracing SDK context, record metrics, or inspect operation payloads.
//! Correlation uses request IDs and parent/child operation IDs, independently of tracing.

mod operation;
pub use operation::{Operation, observe_result, observe_value, with_request_context, in_current_context, count, current_operation_id, current_request_id};
