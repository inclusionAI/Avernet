//! Misc bootstrap helpers used by server construction.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

/// Check if debug mode is enabled via BCS_DEBUG env var
pub(super) fn is_debug_enabled() -> bool {
    std::env::var("BCS_DEBUG").is_ok_and(|v| v == "true")
}

pub(super) type ChannelSlot = Arc<OnceLock<Arc<dyn ChannelService>>>;

pub(super) type SessionChannelOutboundSlot = Arc<OnceLock<Arc<dyn SessionChannelOutboundPort>>>;

pub(super) type ChannelRepos = (
    Arc<dyn ChannelBindingRepoPort>,
    Arc<dyn ConversationSessionRepoPort>,
    Arc<dyn ImParticipantRepoPort>,
    Arc<dyn HumanInputRequestRepoPort>,
);

impl HttpRequestMetricsGuard {
    pub(super) fn new(
        metrics: Arc<crate::metrics::MetricsRuntime>,
        route: String,
        method: String,
    ) -> Self {
        Self {
            metrics,
            route,
            method,
            start: Instant::now(),
            completed: false,
        }
    }

    pub(super) fn complete(mut self, status: StatusCode) {
        self.completed = true;
        self.metrics.record_http_request(
            &self.route,
            &self.method,
            status,
            self.start.elapsed(),
        );
    }
}

impl Drop for HttpRequestMetricsGuard {
    fn drop(&mut self) {
        if !self.completed {
            // Axum drops the request future when the upstream connection is
            // abandoned. This is the application-side equivalent of a proxy
            // reporting 499; there is no HTTP response status to inspect.
            self.metrics.record_http_request_cancelled(
                &self.route,
                &self.method,
                self.start.elapsed(),
            );
        }
    }
}

impl DeferredStateMachineTerminalObserver {
    pub(super) fn new(next: Arc<dyn BotTerminalObserverPort>) -> Self {
        Self {
            runtime: std::sync::RwLock::new(None),
            next,
        }
    }

    pub(super) fn bind(&self, runtime: Arc<dyn CollaborationRuntimeService>) {
        *self
            .runtime
            .write()
            .expect("terminal observer lock poisoned") = Some(runtime);
    }
}
