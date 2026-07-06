//! Legacy `bcs-service-group` callback path (stubbed).
//!
//! Drives `ServiceGroupInstance.callback_status` for the older
//! `/service-groups/*` API. The new Session-based dispatcher in
//! [`super::dispatch::dispatch_callback`] lives alongside this — this
//! crate does not delete the legacy path (per spec §16 follow-up).
//!
//! Ported from legacy `bcs/src/callback/legacy.rs`.
//!
//! TODO(phase-2): The old `ServiceGroupInstance` / `ServiceGroupTemplate`
//! types and their store traits (`ServiceGroupInstanceStore`,
//! `ServiceGroupTemplateStore`) do not yet have replacements in the new
//! architecture. These functions are stubs that log a warning and return;
//! they will be re-wired when the legacy types are ported.

use tracing::warn;

/// Legacy callback executor for the `/service-groups/*` API.
///
/// TODO(phase-2): integrate with new ServiceGroup* replacement types when
/// they are available. The current stub logs a warning and returns
/// immediately.
pub async fn execute_callback(group_id: &str) {
    warn!(
        target: "callback",
        event = "callback.legacy_execute_skipped",
        group_id = %group_id,
        reason = "legacy ServiceGroupInstance path not yet migrated to new architecture (TODO phase-2)",
    );
}

/// Recover pending callbacks for the legacy `/service-groups/*` API.
///
/// TODO(phase-2): integrate with new ServiceGroup* replacement types when
/// they are available. The current stub logs a warning and returns
/// immediately.
pub async fn recover_pending_callbacks() {
    warn!(
        target: "callback",
        event = "callback.legacy_recover_skipped",
        reason = "legacy ServiceGroupInstance path not yet migrated to new architecture (TODO phase-2)",
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_execute_callback_stub() {
        // Stub always returns without error.
        execute_callback("grp-stub").await;
    }

    #[tokio::test]
    async fn test_recover_pending_callbacks_stub() {
        // Stub always returns without error.
        recover_pending_callbacks().await;
    }
}
