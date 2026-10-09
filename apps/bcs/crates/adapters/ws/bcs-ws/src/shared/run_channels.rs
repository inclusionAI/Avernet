//! Run channel manager for tracking run_id -> client channel mappings.
//!
//! Task 16 (spec §14.5): a run channel is either a protected Workbench lane
//! carrying the connection's real-identity binding, or a legacy raw-text lane
//! (the HTTP async-chat run event port). Protected lanes re-authorize every
//! dispatch at enqueue (position 1) with the binding's complete context —
//! fallback/re-dispatch is a NEW dispatch and never reuses an earlier
//! decision; the WS writer still re-checks before the actual send
//! (position 2, `ProtectedDeliveryGate::authorize_dequeued`).

use std::collections::HashMap;
use std::fmt;
use std::time::Instant;

use opentelemetry::trace::SpanContext;
use bcs_domain::{HumanMessageView, MessageAudience, MessageVisibilityDomain};
use tokio::sync::{mpsc, RwLock};
use tracing::{debug, info, warn};

use crate::web::protected_delivery::{
    ProtectedChannelRef, ProtectedDeliveryGate, WorkbenchOutbound,
};
use bcs_service_api::application::v1::delivery_authorization::{
    DeliveryAction, DeliveryAuthorizationDecision,
};

/// The producer side of one run channel: either the typed Workbench outbound
/// queue (drained by the WS writer with the pre-send re-authorization) or the
/// legacy raw-text lane (HTTP async-chat consumers reading plain strings).
#[derive(Debug, Clone)]
pub(crate) enum ChannelSender {
    RawText(mpsc::Sender<String>),
    Workbench(mpsc::Sender<WorkbenchOutbound>),
}

#[derive(Clone)]
pub struct ClientChannel {
    pub(crate) sender: ChannelSender,
    pub registered_at: Instant,
    pub source: Option<String>,
    pub user_id: Option<String>,
    pub trace_parent: Option<SpanContext>,
    pub human_view: Option<HumanMessageView>,
    /// Protected anchor for Workbench lanes: real User / selected view /
    /// binding generation. `None` = legacy lane; then `human_view` keeps the
    /// pre-existing inline visibility behavior.
    pub(crate) protected: Option<ProtectedChannelRef>,
}

impl fmt::Debug for ClientChannel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ClientChannel")
            .field("registered_at", &self.registered_at)
            .field("source", &self.source)
            .field("user_id", &self.user_id)
            .field("trace_parent", &self.trace_parent)
            .field("human_view", &self.human_view)
            .field("protected", &self.protected)
            .finish()
    }
}

#[derive(Debug, Default)]
pub struct RunChannelManager {
    channels: RwLock<HashMap<String, ClientChannel>>,
    session_runs: RwLock<HashMap<String, Vec<String>>>,
    run_sessions: RwLock<HashMap<String, String>>,
    run_aliases: RwLock<HashMap<String, String>>,
}

impl RunChannelManager {
    pub fn new() -> Self {
        Self::default()
    }

    /// Legacy raw-text lane registration (HTTP async-chat run event port).
    pub async fn register(
        &self,
        run_id: String,
        bcs_group_id: String,
        tx: mpsc::Sender<String>,
        source: Option<String>,
        user_id: Option<String>,
    ) {
        self.register_lane(
            run_id,
            bcs_group_id,
            ChannelSender::RawText(tx),
            source,
            user_id,
            None,
            None,
            None,
        )
        .await;
    }

    pub async fn register_with_trace_parent(
        &self,
        run_id: String,
        bcs_group_id: String,
        tx: mpsc::Sender<String>,
        source: Option<String>,
        user_id: Option<String>,
        trace_parent: Option<SpanContext>,
    ) {
        self.register_lane(
            run_id,
            bcs_group_id,
            ChannelSender::RawText(tx),
            source,
            user_id,
            trace_parent,
            None,
            None,
        )
        .await;
    }

    /// Workbench WS lane registration: the queue is the connection's typed
    /// outbound channel; protected channels carry the connection's REAL
    /// identity binding (Task 16). Run fallback and re-dispatches made
    /// through this channel re-authorize that exact context.
    #[allow(clippy::too_many_arguments)]
    pub async fn register_workbench_with_view(
        &self,
        run_id: String,
        bcs_group_id: String,
        tx: mpsc::Sender<WorkbenchOutbound>,
        source: Option<String>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
        protected: Option<ProtectedChannelRef>,
    ) {
        self.register_lane(
            run_id,
            bcs_group_id,
            ChannelSender::Workbench(tx),
            source,
            user_id,
            None,
            human_view,
            protected,
        )
        .await;
    }

    #[allow(clippy::too_many_arguments)]
    async fn register_lane(
        &self,
        run_id: String,
        bcs_group_id: String,
        sender: ChannelSender,
        source: Option<String>,
        user_id: Option<String>,
        trace_parent: Option<SpanContext>,
        human_view: Option<HumanMessageView>,
        protected: Option<ProtectedChannelRef>,
    ) {
        let trace_parent = trace_parent.filter(SpanContext::is_valid);
        let channel = ClientChannel {
            sender,
            registered_at: Instant::now(),
            source,
            user_id,
            trace_parent,
            human_view,
            protected,
        };

        self.channels.write().await.insert(run_id.clone(), channel);
        self.session_runs
            .write()
            .await
            .entry(bcs_group_id.clone())
            .or_insert_with(Vec::new)
            .push(run_id.clone());
        self.run_sessions
            .write()
            .await
            .insert(run_id.clone(), bcs_group_id.clone());

        info!(run_id = %run_id, bcs_group_id = %bcs_group_id, "Run channel registered");
    }

    pub async fn send_event(&self, run_id: &str, event: String) -> bool {
        self.send_visible_event(run_id, event, MessageVisibilityDomain::Chat, None)
            .await
    }

    pub async fn send_visible_event(
        &self,
        run_id: &str,
        event: String,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
    ) -> bool {
        let resolved_run_id = self.resolve_run_id(run_id).await;
        let channels = bcs_observability::observe_value("ws.run_channels.read_lock", self.channels.read()).await;

        let Some(channel) = channels.get(&resolved_run_id) else {
            debug!(
                run_id = %run_id,
                resolved_run_id = %resolved_run_id,
                "No client channel found for run_id"
            );
            return false;
        };
        // Bounded snapshot of the single related target (spec §14.5: the
        // batch is event-related and bounded — a run lane resolves to at most
        // one channel), cloned before the lock is released for the authority
        // round-trip.
        let sender = channel.sender.clone();
        let protected = channel.protected.clone();
        let legacy_hidden = channel
            .human_view
            .as_ref()
            .is_some_and(|view| !view.allows_artifact(visibility_domain, audience));
        drop(channels);

        // Protected lane: position 1 (pre-enqueue). No inline visibility
        // filtering happens here — the application hook owns both the
        // binding-eligibility and the SkipMessage judgment (spec §14.5:
        // filtering must never mask a revocation).
        if let Some(protected) = protected.as_ref() {
            return self
                .send_protected(&resolved_run_id, sender, protected, event, visibility_domain, audience)
                .await;
        }

        if legacy_hidden {
            return false;
        }
        self.send_legacy(&resolved_run_id, sender, event).await
    }

    async fn send_protected(
        &self,
        resolved_run_id: &str,
        sender: ChannelSender,
        protected: &ProtectedChannelRef,
        event: String,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
    ) -> bool {
        let gate = &protected.gate;
        let binding = protected.binding();
        let context = binding.context(DeliveryAction::DeliverFrame, visibility_domain, audience);
        match gate.authorize_targets(vec![context.clone()]).await {
            Ok(decisions) => match decisions.as_slice() {
                [DeliveryAuthorizationDecision::Deliver] => {}
                // SkipMessage / InvalidateBinding: this dispatch alone stops;
                // no fallback re-send for the same target (spec §14.6).
                [DeliveryAuthorizationDecision::SkipMessage] => return false,
                _ => {
                    gate.invalidate_binding(protected.binding_id);
                    return false;
                }
            },
            Err(()) => {
                // Fail closed: stop this binding's protected drain.
                gate.invalidate_binding(protected.binding_id);
                return false;
            }
        }
        // Re-check that the same run still resolves to this exact
        // registration and binding generation before enqueue (spec §17.2:
        // the result must not reach a replaced channel).
        let channels = self.channels.read().await;
        let Some(current) = channels.get(resolved_run_id) else {
            return false;
        };
        if current.protected.as_ref().map(|p| p.binding_id) != Some(protected.binding_id) {
            return false;
        }
        if !gate.binding_active(protected.binding_id) {
            return false;
        }
        drop(channels);

        let ChannelSender::Workbench(tx) = &sender else {
            return false;
        };
        match bcs_observability::observe_result(
            "ws.run_channels.enqueue",
            tx.send(WorkbenchOutbound::Protected {
                payload: event.clone(),
                context,
                binding_id: protected.binding_id,
            }),
        )
        .await
        {
            Ok(()) => {
                debug!(
                    run_id = %resolved_run_id,
                    binding_id = protected.binding_id,
                    "Protected event forwarded to workbench client"
                );
                true
            }
            Err(err) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    run_id = %resolved_run_id,
                    error = %err,
                    "Failed to send protected event to client channel"
                );
                false
            }
        }
    }

    async fn send_legacy(
        &self,
        resolved_run_id: &str,
        sender: ChannelSender,
        event: String,
    ) -> bool {
        match &sender {
            ChannelSender::Workbench(tx) => {
                match bcs_observability::observe_result(
                    "ws.run_channels.enqueue",
                    tx.send(WorkbenchOutbound::PublicControl(event.clone())),
                )
                .await
                {
                    Ok(()) => {
                        debug!(run_id = %resolved_run_id, "Event forwarded to client");
                        true
                    }
                    Err(err) => {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            run_id = %resolved_run_id,
                            error = %err,
                            "Failed to send event to client channel"
                        );
                        false
                    }
                }
            }
            ChannelSender::RawText(tx) => {
                match bcs_observability::observe_result(
                    "ws.run_channels.enqueue",
                    tx.send(event.clone()),
                )
                .await
                {
                    Ok(()) => {
                        debug!(run_id = %resolved_run_id, "Event forwarded to client");
                        true
                    }
                    Err(err) => {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            run_id = %resolved_run_id,
                            error = %err,
                            "Failed to send event to client channel"
                        );
                        false
                    }
                }
            }
        }
    }

    pub async fn send_event_by_session(&self, bcs_group_id: &str, event: String) -> bool {
        self.send_visible_event_by_session(
            bcs_group_id,
            event,
            MessageVisibilityDomain::Chat,
            None,
        )
        .await
    }

    pub async fn send_visible_event_by_session(
        &self,
        bcs_group_id: &str,
        event: String,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
    ) -> bool {
        let session_runs = self.session_runs.read().await;

        if let Some(run_ids) = session_runs.get(bcs_group_id) {
            if let Some(run_id) = run_ids.last() {
                let run_id = run_id.clone();
                drop(session_runs);
                debug!(bcs_group_id = %bcs_group_id, run_id = %run_id, "Sending event by session fallback");
                return self
                    .send_visible_event(&run_id, event, visibility_domain, audience)
                    .await;
            }
        }

        debug!(bcs_group_id = %bcs_group_id, "No runs found for session");
        false
    }

    pub async fn unregister(&self, run_id: &str) {
        let resolved_run_id = self.resolve_run_id(run_id).await;
        let removed = self.channels.write().await.remove(&resolved_run_id);

        let aliases_to_remove = {
            let aliases = self.run_aliases.read().await;
            aliases
                .iter()
                .filter_map(|(alias, target)| {
                    if alias == run_id || target == &resolved_run_id {
                        Some(alias.clone())
                    } else {
                        None
                    }
                })
                .collect::<Vec<_>>()
        };

        if !aliases_to_remove.is_empty() {
            let mut aliases = self.run_aliases.write().await;
            for alias in &aliases_to_remove {
                aliases.remove(alias);
            }
        }

        let mut run_ids_to_remove = aliases_to_remove;
        run_ids_to_remove.push(resolved_run_id.clone());
        if run_id != resolved_run_id {
            run_ids_to_remove.push(run_id.to_string());
        }
        run_ids_to_remove.sort();
        run_ids_to_remove.dedup();

        {
            let mut run_sessions = self.run_sessions.write().await;
            for run_id in &run_ids_to_remove {
                run_sessions.remove(run_id);
            }
        }

        if let Some(channel) = removed {
            let mut session_runs = self.session_runs.write().await;
            for runs in session_runs.values_mut() {
                runs.retain(|r| !run_ids_to_remove.contains(r));
            }
            session_runs.retain(|_, runs| !runs.is_empty());
            info!(
                run_id = %run_id,
                resolved_run_id = %resolved_run_id,
                duration_ms = channel.registered_at.elapsed().as_millis() as u64,
                removed_run_ids = ?run_ids_to_remove,
                "Run channel unregistered"
            );
        } else {
            debug!(
                run_id = %run_id,
                resolved_run_id = %resolved_run_id,
                removed_run_ids = ?run_ids_to_remove,
                "No run channel found during unregister"
            );
        }
    }

    pub async fn unregister_session(&self, bcs_group_id: &str) {
        let run_ids = self.session_runs.read().await.get(bcs_group_id).cloned();

        if let Some(run_ids) = run_ids {
            let mut channels = self.channels.write().await;
            for run_id in &run_ids {
                channels.remove(run_id);
            }
            drop(channels);

            {
                let mut aliases = self.run_aliases.write().await;
                aliases.retain(|alias, target| {
                    !run_ids.iter().any(|run_id| run_id == alias || run_id == target)
                });
            }
            {
                let mut run_sessions = self.run_sessions.write().await;
                for run_id in &run_ids {
                    run_sessions.remove(run_id);
                }
            }
            self.session_runs.write().await.remove(bcs_group_id);
            info!(
                bcs_group_id = %bcs_group_id,
                run_count = run_ids.len(),
                "All run channels for session unregistered"
            );
        }
    }

    pub async fn is_registered(&self, run_id: &str) -> bool {
        let resolved_run_id = self.resolve_run_id(run_id).await;
        self.channels.read().await.contains_key(&resolved_run_id)
    }

    pub async fn trace_parent(&self, run_id: &str) -> Option<SpanContext> {
        let resolved_run_id = self.resolve_run_id(run_id).await;
        let trace_parent = self.channels
            .read()
            .await
            .get(&resolved_run_id)
            .and_then(|channel| channel.trace_parent.clone());
        debug!(
            run_id = %run_id,
            resolved_run_id = %resolved_run_id,
            trace_parent_found = trace_parent.is_some(),
            "Run channel trace context lookup"
        );
        trace_parent
    }

    pub async fn register_alias(&self, alias_run_id: String, source_run_id: String) -> bool {
        if alias_run_id == source_run_id {
            return self.is_registered(&source_run_id).await;
        }

        let resolved_source_run_id = self.resolve_run_id(&source_run_id).await;
        let session_id = match self.session_for_run(&resolved_source_run_id).await {
            Some(session_id) => session_id,
            None => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    alias_run_id = %alias_run_id,
                    source_run_id = %source_run_id,
                    resolved_source_run_id = %resolved_source_run_id,
                    "Run channel alias skipped: source session not found"
                );
                return false;
            }
        };

        if !self.channels.read().await.contains_key(&resolved_source_run_id) {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                alias_run_id = %alias_run_id,
                source_run_id = %source_run_id,
                resolved_source_run_id = %resolved_source_run_id,
                session_id = %session_id,
                "Run channel alias skipped: source channel not found"
            );
            return false;
        }

        self.run_aliases
            .write()
            .await
            .insert(alias_run_id.clone(), resolved_source_run_id.clone());
        self.run_sessions
            .write()
            .await
            .insert(alias_run_id.clone(), session_id.clone());
        {
            let mut session_runs = self.session_runs.write().await;
            let runs = session_runs.entry(session_id.clone()).or_insert_with(Vec::new);
            if !runs.contains(&alias_run_id) {
                runs.push(alias_run_id.clone());
            }
        }

        info!(
            alias_run_id = %alias_run_id,
            source_run_id = %source_run_id,
            resolved_source_run_id = %resolved_source_run_id,
            session_id = %session_id,
            "Run channel alias registered"
        );
        true
    }

    pub async fn session_for_run(&self, run_id: &str) -> Option<String> {
        if let Some(session_id) = self.run_sessions.read().await.get(run_id).cloned() {
            return Some(session_id);
        }

        let resolved_run_id = self.resolve_run_id(run_id).await;
        if resolved_run_id == run_id {
            return None;
        }
        self.run_sessions
            .read()
            .await
            .get(&resolved_run_id)
            .cloned()
    }

    pub async fn get_session_runs(&self, bcs_group_id: &str) -> Vec<String> {
        self.session_runs
            .read()
            .await
            .get(bcs_group_id)
            .cloned()
            .unwrap_or_default()
    }

    pub async fn run_count(&self) -> usize {
        self.channels.read().await.len()
    }

    async fn resolve_run_id(&self, run_id: &str) -> String {
        self.run_aliases
            .read()
            .await
            .get(run_id)
            .cloned()
            .unwrap_or_else(|| run_id.to_string())
    }

    pub async fn cleanup_expired(&self, max_age_secs: u64) {
        let mut channels = self.channels.write().await;
        let before = channels.len();

        channels.retain(|_, channel| channel.registered_at.elapsed().as_secs() < max_age_secs);

        let removed = before - channels.len();
        if removed > 0 {
            warn!(request_id = %bcs_observability::CurrentRequestId, removed, "Removed expired run channels");
        }
    }
}

impl fmt::Debug for ProtectedDeliveryGate {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ProtectedDeliveryGate").finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use opentelemetry::trace::{SpanContext, SpanId, TraceFlags, TraceId, TraceState};

    fn test_span_context() -> SpanContext {
        SpanContext::new(
            TraceId::from_hex("0af7651916cd43dd8448eb211c80319c").unwrap(),
            SpanId::from_hex("b7ad6b7169203331").unwrap(),
            TraceFlags::SAMPLED,
            false,
            TraceState::default(),
        )
    }

    #[tokio::test]
    async fn register_and_send_event() {
        let manager = RunChannelManager::new();
        let (tx, mut rx) = mpsc::channel(16);
        let (workbench_tx, mut workbench_rx) = mpsc::channel::<WorkbenchOutbound>(16);

        manager
            .register(
                "run-1".to_string(),
                "grp-001".to_string(),
                tx,
                Some("webui".to_string()),
                Some("user-123".to_string()),
            )
            .await;
        manager
            .register_workbench_with_view(
                "run-wb".to_string(),
                "grp-001".to_string(),
                workbench_tx,
                Some("workbench-ws".to_string()),
                None,
                None,
                None,
            )
            .await;

        assert!(manager.is_registered("run-1").await);
        assert!(manager.is_registered("run-wb").await);

        let sent = manager.send_event("run-1", "test-event".to_string()).await;
        assert!(sent);

        let event = rx.recv().await;
        assert_eq!(event, Some("test-event".to_string()));

        let sent = manager
            .send_event("run-wb", "workbench-event".to_string())
            .await;
        assert!(sent);
        match workbench_rx.recv().await {
            Some(WorkbenchOutbound::PublicControl(payload)) => {
                assert_eq!(payload, "workbench-event")
            }
            other => panic!("unbound workbench lane stays PublicControl, got {other:?}"),
        }

        let mut runs = manager.get_session_runs("grp-001").await;
        runs.sort();
        assert_eq!(runs, vec!["run-1", "run-wb"]);

        manager.unregister("run-1").await;
        assert!(!manager.is_registered("run-1").await);
        manager.unregister("run-wb").await;
        assert!(!manager.is_registered("run-wb").await);
    }

    #[tokio::test]
    async fn send_to_unregistered_run_fails() {
        let manager = RunChannelManager::new();

        let sent = manager.send_event("unknown-run", "test".to_string()).await;
        assert!(!sent);
    }

    #[tokio::test]
    async fn alias_forwards_event_and_preserves_session_key() {
        let manager = RunChannelManager::new();
        let (tx, mut rx) = mpsc::channel(16);

        manager
            .register(
                "outer-run".to_string(),
                "grp-001:abcdef12".to_string(),
                tx,
                Some("webui".to_string()),
                Some("user-123".to_string()),
            )
            .await;

        assert!(
            manager
                .register_alias("sub-run".to_string(), "outer-run".to_string())
                .await
        );
        assert!(manager.is_registered("sub-run").await);
        assert_eq!(
            manager.session_for_run("sub-run").await.as_deref(),
            Some("grp-001:abcdef12")
        );

        let sent = manager.send_event("sub-run", "test-event".to_string()).await;
        assert!(sent);
        assert_eq!(rx.recv().await, Some("test-event".to_string()));

        let runs = manager.get_session_runs("grp-001:abcdef12").await;
        assert_eq!(runs, vec!["outer-run", "sub-run"]);

        manager.unregister("sub-run").await;
        assert!(!manager.is_registered("outer-run").await);
        assert!(!manager.is_registered("sub-run").await);
        assert!(manager.get_session_runs("grp-001:abcdef12").await.is_empty());
    }

    #[tokio::test]
    async fn trace_parent_lookup_resolves_alias_and_cleans_up_with_channel() {
        let manager = RunChannelManager::new();
        let (tx, _rx) = mpsc::channel(16);
        let trace_parent = test_span_context();

        manager
            .register_with_trace_parent(
                "outer-run".to_string(),
                "grp-001".to_string(),
                tx,
                Some("http-chat-async".to_string()),
                None,
                Some(trace_parent.clone()),
            )
            .await;
        assert!(
            manager
                .register_alias("inner-run".to_string(), "outer-run".to_string())
                .await
        );

        assert_eq!(manager.trace_parent("inner-run").await, Some(trace_parent));
        manager.unregister("inner-run").await;
        assert_eq!(manager.trace_parent("outer-run").await, None);
        assert_eq!(manager.trace_parent("inner-run").await, None);
    }

    #[tokio::test]
    async fn invalid_trace_parent_is_not_retained() {
        let manager = RunChannelManager::new();
        let (tx, _rx) = mpsc::channel(1);

        manager
            .register_with_trace_parent(
                "run-invalid".to_string(),
                "group-1".to_string(),
                tx,
                Some("http-chat-async".to_string()),
                None,
                Some(SpanContext::empty_context()),
            )
            .await;

        assert_eq!(manager.trace_parent("run-invalid").await, None);
    }
}