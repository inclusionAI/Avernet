//! Composition-wide producer registration over the existing Run-context port.
//!
//! Every producer already calls `put_context` before transport I/O (including
//! direct A2A, system messages and queued preparation). Share this decorator and
//! its tracker with MessageFlow rather than teaching ingress to trust unknown
//! Runs. The inner Memory/Redis store keeps its existing persistence semantics.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    ActiveBotRunContext, BotRunContext, BotRunContextPort, BotRunScope,
    ProviderRunTransport, ServiceResult,
};

use crate::message_tracker::MessageTracker;

pub struct TrackedBotRunContext {
    inner: Arc<dyn BotRunContextPort>,
    tracker: Arc<MessageTracker>,
}

impl TrackedBotRunContext {
    pub fn new(inner: Arc<dyn BotRunContextPort>) -> Self {
        Self { inner, tracker: Arc::new(MessageTracker::new()) }
    }

    pub(crate) fn tracker(&self) -> Arc<MessageTracker> {
        self.tracker.clone()
    }
}

#[async_trait]
impl BotRunContextPort for TrackedBotRunContext {
    async fn put_context(&self, context: BotRunContext) {
        let run_id = context.run_id.clone();
        let deadline_ms = context.deadline_ms;
        let terminal = context.terminal;
        self.inner.put_context(context).await;
        if !terminal {
            // Insert-only with respect to admitted operations and aliases:
            // registration cannot replace frozen permits or mint an alias Run.
            self.tracker.register_independent_output_run(&run_id, deadline_ms).await;
        }
    }

    async fn put_alias_context(&self, context: BotRunContext) {
        // An ACK can finish projecting after a concurrent canonical final has
        // removed its tracker alias. Never mint an independent producer here.
        self.inner.put_alias_context(context).await;
    }

    async fn reschedule_deadline(&self, scope: &BotRunScope, run: &str, expected: u64, deadline: u64) -> ServiceResult<bool> {
        if self.tracker.output_binding(run).await.is_none() { return Ok(false); }
        if !self.inner.reschedule_deadline(scope, run, expected, deadline).await? { return Ok(false); }
        Ok(self.tracker.reschedule_output_deadline(run, expected, deadline).await)
    }

    async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
        self.inner.get_context(run_id).await
    }

    async fn try_begin_terminal(&self, run_id: &str) -> bool {
        self.inner.try_begin_terminal(run_id).await
    }

    async fn mark_terminal(&self, run_id: &str) -> bool {
        // MessageFlow may mark the context before enqueueing the final. Its
        // successful terminal pipeline, not this flag, retires output lineage.
        self.inner.mark_terminal(run_id).await
    }

    async fn release_terminal(&self, run_id: &str) {
        self.inner.release_terminal(run_id).await;
    }

    async fn register_active_run(&self, context: ActiveBotRunContext) -> ServiceResult<()> {
        self.inner.register_active_run(context).await
    }

    async fn list_active_runs(&self, scope: &BotRunScope) -> ServiceResult<Vec<ActiveBotRunContext>> {
        self.inner.list_active_runs(scope).await
    }

    async fn find_active_run(&self, run_id: &str) -> ServiceResult<Option<ActiveBotRunContext>> {
        self.inner.find_active_run(run_id).await
    }

    async fn bind_downstream_run_id(&self, run: &str, downstream: &str) -> ServiceResult<bool> {
        self.inner.bind_downstream_run_id(run, downstream).await
    }

    async fn bind_request_alias(&self, run: &str, request: &str) -> ServiceResult<bool> {
        self.inner.bind_request_alias(run, request).await
    }

    async fn remove_active_run(&self, scope: &BotRunScope, run: &str) -> ServiceResult<bool> {
        self.inner.remove_active_run(scope, run).await
    }

    async fn begin_provider_transport(&self, run: &str, deadline_ms: u64) -> bool {
        self.inner.begin_provider_transport(run, deadline_ms).await
    }

    async fn bind_provider_transport(&self, run: &str, transport: ProviderRunTransport) -> bool {
        self.inner.bind_provider_transport(run, transport).await
    }

    async fn get_provider_transport(&self, run: &str) -> Option<ProviderRunTransport> {
        self.inner.get_provider_transport(run).await
    }

    async fn mark_provider_transport_terminal(&self, run: &str) {
        self.inner.mark_provider_transport_terminal(run).await;
    }

    async fn clear_provider_transport(&self, run: &str) {
        self.inner.clear_provider_transport(run).await;
    }

    async fn cleanup_expired(&self, now_ms: u64, retention_ms: u64) -> usize {
        // Independent producers can end without a Bot final (e.g. A2A timeout).
        // Reuse the context retention grace so timeout terminals can still
        // publish. NEVER expire admitted operations.
        self.tracker.cleanup_expired_independent_outputs(now_ms, retention_ms).await;
        self.inner.cleanup_expired(now_ms, retention_ms).await
    }
}

#[cfg(test)]
mod tests;
