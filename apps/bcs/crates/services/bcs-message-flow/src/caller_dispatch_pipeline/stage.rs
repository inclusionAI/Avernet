//! Per-stage liveness bounds for the protected dispatch pipeline
//! (spec 2026-11-03-caller-dispatch-stage-timeouts): there is no outer
//! window — every stage is individually bounded and its timeout converges
//! with stage-accurate evidence.
use std::future::Future;
use std::time::Duration;

/// Composition default, mirroring `DeliveryRuntimeConfig.io_timeout`
/// (30s in production wiring). Only tests shrink it.
pub(super) const DEFAULT_STAGE_TIMEOUT: Duration = Duration::from_secs(30);

/// Await `future` under the stage's liveness `budget`. `None` means the
/// stage exceeded its bound: nothing about success or failure is known,
/// the caller OWES the conservative convergence decision, and the WARN
/// names the stage for the on-call. `pub(crate)` because the root
/// (`dispatch_once`) and delegate (`dispatch_delegate_once`) bodies live
/// in sibling submodules that share the parent's scope.
pub(crate) async fn stage_bound<T>(
    budget: Duration,
    stage: &'static str,
    future: impl Future<Output = T>,
) -> Option<T> {
    match tokio::time::timeout(budget, future).await {
        Ok(value) => Some(value),
        Err(_) => {
            tracing::warn!(stage, "caller protected dispatch stage timed out");
            None
        }
    }
}
