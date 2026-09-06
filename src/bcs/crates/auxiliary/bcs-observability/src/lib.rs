//! Log-only operation observations and transport-neutral correlation data.
//!
//! Logs use the subscriber installed by bootstrap. This crate does not create
//! spans, read tracing SDK context, record metrics, or inspect operation payloads.
//! Adapters may supply an existing trace ID as a string through `with_trace_id`.

mod operation;
pub use operation::{Operation, observe_result, observe_value, with_request_context, with_trace_id, in_current_context, count, current_operation_id, current_request_id, current_trace_id};
