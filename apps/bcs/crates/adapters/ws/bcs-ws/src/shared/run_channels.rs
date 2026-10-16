//! Run channel manager for tracking run_id -> client sink mappings.
//!
//! Run sinks are explicit (bot-authority plan Task 7):
//! `register_internal` keeps the existing internal service String
//! consumer channel; `register_workbench` attaches the protected
//! Workbench sink (typed frames + the connection's protected
//! delivery binding). Run aliases and the session fallback always
//! resolve to the SAME registered sink instance, so a fallback can
//! never serve another Human's connection under the same
//! run/session string.

use std::collections::HashMap;
use std::time::Instant;

use opentelemetry::trace::SpanContext;
use tokio::sync::{mpsc, RwLock};
use tracing::{debug, info, warn};

use bcs_domain::{MessageAudience, MessageVisibilityDomain};
use bcs_service_api::application::v1::ProtectedDeliveryBinding;

use super::protected_delivery::{OutboundFrame, RunClientSink};

#[derive(Debug, Clone)]
pub struct ClientChannel {
    pub tx: RunClientSink,
    pub registered_at: Instant,
    pub source: Option<String>,
    pub user_id: Option<String>,
    pub trace_parent: Option<SpanContext>,
}

#[derive(Debug)]
enum EnqueueJob {
    /// No channel any more.
    Missing,
    Internal {
        tx: mpsc::Sender<String>,
        source: Option<String>,
        event: String,
    },
    Workbench {
        tx: mpsc::Sender<OutboundFrame>,
        source: Option<String>,
        frame: OutboundFrame,
    },
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

    /// Explicit Internal sink registration: the internal service
    /// String port (existing `ChatRunEventPort` consumers). Never
    /// used by a public writer.
    pub async fn register_internal(
        &self,
        run_id: String,
        bcs_group_id: String,
        tx: mpsc::Sender<String>,
        source: Option<String>,
        user_id: Option<String>,
    ) {
        self.register_sink(
            run_id,
            bcs_group_id,
            RunClientSink::Internal { tx },
            source,
            user_id,
            None,
        )
        .await;
    }

    /// Explicit Internal sink registration with run trace context.
    pub async fn register_internal_with_trace_parent(
        &self,
        run_id: String,
        bcs_group_id: String,
        tx: mpsc::Sender<String>,
        source: Option<String>,
        user_id: Option<String>,
        trace_parent: Option<SpanContext>,
    ) {
        self.register_sink(
            run_id,
            bcs_group_id,
            RunClientSink::Internal { tx },
            source,
            user_id,
            trace_parent,
        )
        .await;
    }

    /// Explicit protected Workbench sink registration: typed
    /// `OutboundFrame`s flow through a bounded per-connection queue
    /// and are re-authorized per frame at the final writer
    /// (bot-authority spec §8.2/§8.3). The binding is stamped onto
    /// every frame enqueued through this sink.
    pub async fn register_workbench(
        &self,
        run_id: String,
        bcs_group_id: String,
        tx: mpsc::Sender<OutboundFrame>,
        binding: ProtectedDeliveryBinding,
        source: Option<String>,
        user_id: Option<String>,
        trace_parent: Option<SpanContext>,
    ) {
        self.register_sink(
            run_id,
            bcs_group_id,
            RunClientSink::Workbench { tx, binding },
            source,
            user_id,
            trace_parent,
        )
        .await;
    }

    async fn register_sink(
        &self,
        run_id: String,
        bcs_group_id: String,
        tx: RunClientSink,
        source: Option<String>,
        user_id: Option<String>,
        trace_parent: Option<SpanContext>,
    ) {
        let trace_parent = trace_parent.filter(SpanContext::is_valid);
        let channel = ClientChannel {
            tx,
            registered_at: Instant::now(),
            source,
            user_id,
            trace_parent,
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

    /// Deliver one event to a run sink. Internal sinks keep their
    /// existing unfiltered String contract; Workbench sinks enqueue
    /// a protected frame that the final socket writer authorizes
    /// against the current authorization facts — there is NO
    /// enqueue-time authorization and no cached connection scope
    /// here.
    pub async fn send_visible_event(
        &self,
        run_id: &str,
        event: String,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
    ) -> bool {
        let resolved_run_id = self.resolve_run_id(run_id).await;
        // Lock discipline (bot-authority spec §8.2): the read lock is
        // ONLY used to resolve the sink — the channels guard is
        // dropped BEFORE any enqueue await, so a slow Workbench
        // writer can never stall register/unregister on ANY run.
        let job = {
            let channels = bcs_observability::observe_value("ws.run_channels.read_lock", self.channels.read()).await;
            let Some(channel) = channels.get(&resolved_run_id) else {
                debug!(
                    run_id = %run_id,
                    resolved_run_id = %resolved_run_id,
                    "No client channel found for run_id"
                );
                // The channels guard drops on leaving the block.
                return false;
            };
            match &channel.tx {
                RunClientSink::Internal { tx } => EnqueueJob::Internal {
                    tx: tx.clone(),
                    source: channel.source.clone(),
                    event,
                },
                RunClientSink::Workbench { tx, binding } => EnqueueJob::Workbench {
                    tx: tx.clone(),
                    source: channel.source.clone(),
                    frame: OutboundFrame::Protected {
                        visibility: super::protected_delivery::visibility(
                            visibility_domain,
                            audience,
                        ),
                        payload: event,
                        binding: binding.clone(),
                    },
                },
            }
        };
        match job {
            EnqueueJob::Missing => false,
            EnqueueJob::Internal { tx, source, event } => {
                match bcs_observability::observe_result(
                    "ws.run_channels.enqueue",
                    tx.send(event),
                )
                .await
                {
                    Ok(()) => {
                        debug!(
                            run_id = %run_id,
                            resolved_run_id = %resolved_run_id,
                            source = ?source,
                            "Event forwarded to client"
                        );
                        true
                    }
                    Err(err) => {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            run_id = %run_id,
                            resolved_run_id = %resolved_run_id,
                            error = %err,
                            "Failed to send event to client channel"
                        );
                        false
                    }
                }
            }
            EnqueueJob::Workbench { tx, source, frame } => {
                match bcs_observability::observe_result(
                    "ws.run_channels.enqueue",
                    tx.send(frame),
                )
                .await
                {
                    Ok(()) => {
                        debug!(
                            run_id = %run_id,
                            resolved_run_id = %resolved_run_id,
                            source = ?source,
                            "Protected frame queued for final-writer authorization"
                        );
                        true
                    }
                    Err(err) => {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            run_id = %run_id,
                            resolved_run_id = %resolved_run_id,
                            error = %err,
                            "Failed to enqueue protected frame"
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

    /// The sink registered under `run_id` (alias-resolved).
    pub async fn sink_of(&self, run_id: &str) -> Option<RunClientSink> {
        let resolved_run_id = self.resolve_run_id(run_id).await;
        self.channels
            .read()
            .await
            .get(&resolved_run_id)
            .map(|channel| channel.tx.clone())
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

    fn human_self_binding() -> ProtectedDeliveryBinding {
        ProtectedDeliveryBinding {
            tenant: Some("tenant-a".into()),
            user_id: "user-a".into(),
            group_id: "group-a".into(),
            session_id: Some("group-a:s1".into()),
            view_actor_id: "human_user-a".into(),
            connection_instance_id: 7,
        }
    }

    #[tokio::test]
    async fn register_internal_and_send_event() {
        let manager = RunChannelManager::new();
        let (tx, mut rx) = mpsc::channel(16);

        manager
            .register_internal(
                "run-1".to_string(),
                "grp-001".to_string(),
                tx,
                Some("webui".to_string()),
                Some("user-123".to_string()),
            )
            .await;

        assert!(manager.is_registered("run-1").await);

        let sent = manager.send_event("run-1", "test-event".to_string()).await;
        assert!(sent);

        let event = rx.recv().await;
        assert_eq!(event, Some("test-event".to_string()));

        let runs = manager.get_session_runs("grp-001").await;
        assert_eq!(runs, vec!["run-1"]);

        manager.unregister("run-1").await;
        assert!(!manager.is_registered("run-1").await);
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
            .register_internal(
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
            .register_internal_with_trace_parent(
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
            .register_internal_with_trace_parent(
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

    #[tokio::test]
    async fn workbench_sink_enqueues_protected_frames_and_keeps_binding() {
        let manager = RunChannelManager::new();
        let (frame_tx, mut frame_rx) = mpsc::channel(4);
        manager
            .register_workbench(
                "run-wb".to_string(),
                "group-a:s1".to_string(),
                frame_tx,
                human_self_binding(),
                Some("workbench-ws".to_string()),
                Some("human_user-a".to_string()),
                None,
            )
            .await;

        assert!(manager
            .send_visible_event(
                "run-wb",
                "payload".to_string(),
                MessageVisibilityDomain::StateMachine,
                Some(&MessageAudience::FullOnly),
            )
            .await);

        match frame_rx.recv().await {
            Some(OutboundFrame::Protected {
                binding,
                visibility,
                payload,
            }) => {
                assert_eq!(binding, human_self_binding());
                assert_eq!(visibility.domain, MessageVisibilityDomain::StateMachine);
                assert_eq!(visibility.audience, Some(MessageAudience::FullOnly));
                assert_eq!(payload, "payload");
            }
            other => panic!("expected protected frame, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn control_frames_flow_through_the_workbench_sink_unchanged() {
        let manager = RunChannelManager::new();
        let (frame_tx, mut frame_rx) = mpsc::channel(4);
        manager
            .register_workbench(
                "run-wb".to_string(),
                "group-a:s1".to_string(),
                frame_tx,
                human_self_binding(),
                None,
                None,
                None,
            )
            .await;

        // The manager never fabricates control frames itself; the
        // sink accepts them from the final writer side.
        let RunClientSink::Workbench { tx, .. } = manager.sink_of("run-wb").await.unwrap()
        else {
            panic!("workbench sink expected");
        };
        tx.send(OutboundFrame::ConnectionControl {
            payload: "{}".to_string(),
        })
        .await
        .unwrap();
        assert!(matches!(
            frame_rx.recv().await,
            Some(OutboundFrame::ConnectionControl { .. })
        ));
    }

    #[tokio::test]
    async fn alias_preserves_the_bound_sink_of_its_source() {
        let manager = RunChannelManager::new();
        let (frame_tx, mut frame_rx) = mpsc::channel(4);
        manager
            .register_workbench(
                "outer-run".to_string(),
                "group-a:s1".to_string(),
                frame_tx,
                human_self_binding(),
                Some("workbench-ws".to_string()),
                None,
                None,
            )
            .await;
        assert!(
            manager
                .register_alias("inner-run".to_string(), "outer-run".to_string())
                .await
        );

        // The alias resolves to the SAME sink instance and binding.
        assert!(
            manager
                .send_visible_event(
                    "inner-run",
                    "via-alias".to_string(),
                    MessageVisibilityDomain::Chat,
                    None,
                )
                .await
        );
        match frame_rx.recv().await {
            Some(OutboundFrame::Protected { binding, payload, .. }) => {
                assert_eq!(payload, "via-alias");
                assert_eq!(binding, human_self_binding());
            }
            other => panic!("expected protected frame, got {other:?}"),
        }
    }
}
