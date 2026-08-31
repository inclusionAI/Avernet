//! Callback dispatcher for service-invocation sessions.
//!
//! Two paths live in this crate:
//!
//! 1. [`legacy::execute_callback`] / [`legacy::recover_pending_callbacks`] —
//!    drives `ServiceGroupInstance.callback_status` for the older
//!    `/service-groups/*` API. Re-exported for backward compatibility
//!    (stubbed — TODO(phase-2): integrate with new types).
//!
//! 2. [`dispatch::dispatch_callback`] — the new flow that fires after a
//!    service `Session` completes. It iterates every channel in the
//!    group's `service_spec.callback_config.channels` concurrently and
//!    updates `Session.callback_status` to one of `succeeded` /
//!    `partial_failed` / `failed` based on the aggregate result.
//!
//! Concurrency: normal dispatch and recovery both claim an activation-aware
//! Session lease before sending. Duplicate invocations are safe; only the
//! current fencing token may write the terminal `callback_status`.

pub mod antding;
pub mod baas;
pub mod dispatch;
pub mod legacy;

pub use dispatch::{
    aggregate_results, dispatch_callback, dispatch_callback_with_url_guard,
    dispatch_for_session_with_url_guard, maybe_dispatch_for_session,
    maybe_dispatch_for_session_with_url_guard, AggregateStatus, SessionCallbackDispatcher,
};
pub use legacy::{execute_callback, recover_pending_callbacks};
