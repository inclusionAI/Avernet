//! Run channel manager for tracking run_id -> client sink mappings.
//!
//! Run sinks are explicit (bot-authority plan Task 7):
//! `register_internal` keeps the existing internal service String
//! consumer channel; `register_workbench` attaches the protected
//! Workbench sink (typed frames + the owning connection instance).
//! Run aliases always resolve to the SAME registered sink instance,
//! so an alias can never serve another Human's connection under the
//! same run string; the session fallback remains Internal-only for
//! public output (spec §7.2: public Workbench 输出不借 last-run 许可).
//!
//! Workbench frames are authorized at the ENQUEUE boundary against
//! the operation's frozen per-connection permit
//! ([`super::output_permits`]) — never per frame at the writer, and
//! never retroactively revoked mid-operation (spec §8.2).

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use opentelemetry::trace::SpanContext;
use tokio::sync::{mpsc, RwLock};
use tracing::{debug, info, warn};

use bcs_domain::{MessageAudience, MessageVisibilityDomain};

use super::connection_retirement::SlowConnectionRetirement;
use super::output_permits::OutputPermitStore;
use super::protected_delivery::{OutboundFrame, RunClientSink, TERMINAL_ENQUEUE_BUDGET};

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
    Internal {
        tx: mpsc::Sender<String>,
        source: Option<String>,
        event: String,
    },
    Workbench {
        tx: mpsc::Sender<OutboundFrame>,
        source: Option<String>,
        connection_instance_id: u64,
        event: String,
    },
}

#[derive(Debug)]
pub struct RunChannelManager {
    channels: RwLock<HashMap<String, ClientChannel>>,
    session_runs: RwLock<HashMap<String, Vec<String>>>,
    run_sessions: RwLock<HashMap<String, String>>,
    run_aliases: RwLock<HashMap<String, String>>,
    /// The operation permit store consulted at the Workbench enqueue
    /// boundary. Shared with the fanout registry and the output
    /// admission port; a standalone manager (unit fixtures) owns an
    /// empty store, so an unbound run simply admits no Workbench
    /// frame while Internal sinks keep flowing.
    output_permits: Arc<OutputPermitStore>,
    /// Slow-consumer retirement hook (spec §9): a terminal frame
    /// lost past the bounded backpressure budget retires the
    /// saturated connection as an observable transport failure.
    /// Standalone fixtures leave it unwired; composition installs
    /// the Workbench registry.
    connection_retirement: Option<Arc<dyn SlowConnectionRetirement>>,
    /// Terminal frames lost past the bounded budget on this lane.
    terminal_frames_lost: AtomicU64,
}

impl Default for RunChannelManager {
    fn default() -> Self {
        Self::new()
    }
}

impl RunChannelManager {
    pub fn new() -> Self {
        Self {
            channels: RwLock::new(HashMap::new()),
            session_runs: RwLock::new(HashMap::new()),
            run_sessions: RwLock::new(HashMap::new()),
            run_aliases: RwLock::new(HashMap::new()),
            output_permits: Arc::new(OutputPermitStore::new()),
            connection_retirement: None,
            terminal_frames_lost: AtomicU64::new(0),
        }
    }

    /// Share the composition-wide permit store.
    pub fn with_output_permits(self, permits: Arc<OutputPermitStore>) -> Self {
        Self {
            output_permits: permits,
            ..self
        }
    }

    /// Install the composition's slow-consumer retirement hook.
    pub fn with_connection_retirement(
        self,
        retirement: Arc<dyn SlowConnectionRetirement>,
    ) -> Self {
        Self {
            connection_retirement: Some(retirement),
            ..self
        }
    }

    /// Terminal frames lost past the bounded backpressure budget
    /// (each one retired its connection).
    pub fn terminal_frames_lost(&self) -> u64 {
        self.terminal_frames_lost.load(Ordering::Relaxed)
    }

    /// Retire one saturated connection through the composition hook
    /// (no-op for standalone fixtures without a registry behind them).
    async fn retire_connection(&self, connection_instance_id: u64) {
        if let Some(retirement) = self.connection_retirement.as_ref() {
            retirement
                .retire(connection_instance_id, "terminal frame lost past the backpressure budget")
                .await;
        }
    }

    /// The shared operation permit store (enqueue-boundary checks
    /// and test fixtures install through the same handle).
    pub fn output_permits(&self) -> Arc<OutputPermitStore> {
        self.output_permits.clone()
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
    /// and are authorized at the ENQUEUE boundary against the
    /// operation's frozen permit for THIS connection instance
    /// (bot-authority spec §8.2/§8.3 — 已准入输出不追溯). The Run is
    /// bound to the operation that admitted it in the SAME
    /// registration, so an immediate callback frame never finds an
    /// unbound Run.
    pub async fn register_workbench(
        &self,
        run_id: String,
        bcs_group_id: String,
        tx: mpsc::Sender<OutboundFrame>,
        connection_instance_id: u64,
        operation_id: &str,
        source: Option<String>,
        user_id: Option<String>,
        trace_parent: Option<SpanContext>,
    ) {
        self.output_permits
            .bind_run(&run_id, operation_id)
            .await;
        self.register_sink(
            run_id,
            bcs_group_id,
            RunClientSink::Workbench {
                tx,
                connection_instance_id,
            },
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
        self.send_visible_event(run_id, event, MessageVisibilityDomain::Chat, None, false)
            .await
    }

    /// Deliver one event to a run sink. Internal sinks keep their
    /// existing unfiltered String contract; Workbench sinks are
    /// authorized at the ENQUEUE boundary against the frozen
    /// per-connection permit of the operation this Run is bound to
    /// (spec §8.2: 已准入输出不追溯) — a mid-operation revocation
    /// changes nothing here, a frame without a permit is refused,
    /// and the socket writer just sends.
    pub async fn send_visible_event(
        &self,
        run_id: &str,
        event: String,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
        terminal: bool,
    ) -> bool {
        let resolved_run_id = self.resolve_run_id(run_id).await;
        // Lock discipline: the read lock is ONLY used to resolve the
        // sink — the channels guard is dropped BEFORE any permit or
        // enqueue await, so a slow Workbench writer can never stall
        // register/unregister on ANY run.
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
                RunClientSink::Workbench {
                    tx,
                    connection_instance_id,
                } => EnqueueJob::Workbench {
                    tx: tx.clone(),
                    source: channel.source.clone(),
                    connection_instance_id: *connection_instance_id,
                    event,
                },
            }
        };
        match job {
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
            EnqueueJob::Workbench {
                tx,
                source,
                connection_instance_id,
                event,
            } => {
                let visibility = super::protected_delivery::visibility(
                    visibility_domain,
                    audience,
                );
                let Some(operation_id) =
                    self.output_permits.operation_of_run(&resolved_run_id).await
                else {
                    debug!(
                        run_id = %run_id,
                        resolved_run_id = %resolved_run_id,
                        "Run is not bound to an admitted operation; Workbench frame refused"
                    );
                    return false;
                };
                if !self
                    .output_permits
                    .check_frame(
                        &operation_id,
                        connection_instance_id,
                        &visibility,
                        Some(&resolved_run_id),
                    )
                    .await
                {
                    debug!(
                        run_id = %run_id,
                        resolved_run_id = %resolved_run_id,
                        connection_instance_id,
                        "Workbench run frame has no permit for this connection"
                    );
                    return false;
                }
                if terminal {
                    // Run terminal ≠ operation complete: only THIS
                    // stream closes; the operation stays open for its
                    // sibling runs until every registered stream is
                    // terminal (spec §8.2).
                    self.output_permits
                        .mark_run_terminal(&operation_id, &resolved_run_id)
                        .await;
                }
                let frame = if terminal {
                    OutboundFrame::Terminal { payload: event }
                } else {
                    OutboundFrame::Data { payload: event }
                };
                // Terminal frames take the bounded backpressure
                // budget; a terminal still lost past the budget
                // retires the saturated connection as an explicit
                // transport failure (spec §9 — never a silent
                // final-less stream). Data frames keep the blocking
                // enqueue contract of this lane.
                let enqueued = if terminal {
                    match tx.try_send(frame) {
                        Ok(()) => Ok(()),
                        Err(mpsc::error::TrySendError::Closed(_)) => Err(()),
                        Err(mpsc::error::TrySendError::Full(frame)) => {
                            match tokio::time::timeout(TERMINAL_ENQUEUE_BUDGET, tx.send(frame))
                                .await
                            {
                                Ok(Ok(())) => Ok(()),
                                Ok(Err(_)) => Err(()),
                                Err(_) => {
                                    self.terminal_frames_lost.fetch_add(1, Ordering::Relaxed);
                                    warn!(
                                        request_id = %bcs_observability::CurrentRequestId,
                                        run_id = %run_id,
                                        resolved_run_id = %resolved_run_id,
                                        connection_instance_id,
                                        "TERMINAL run frame lost past the backpressure budget; \
                                         retiring the saturated connection as a transport failure"
                                    );
                                    self.retire_connection(connection_instance_id).await;
                                    Err(())
                                }
                            }
                        }
                    }
                } else {
                    bcs_observability::observe_result(
                        "ws.run_channels.enqueue",
                        tx.send(frame),
                    )
                    .await
                    .map_err(|_| ())
                };
                match enqueued {
                    Ok(()) => {
                        debug!(
                            run_id = %run_id,
                            resolved_run_id = %resolved_run_id,
                            source = ?source,
                            "Permitted frame queued for the workbench writer"
                        );
                        true
                    }
                    Err(()) => {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            run_id = %run_id,
                            resolved_run_id = %resolved_run_id,
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
            false,
        )
        .await
    }

    /// Session-targeted fallback. The "latest Run of the session"
    /// shortcut now serves ONLY Internal sinks (spec §7.2: public
    /// Workbench 输出不得借该 Session 最后一个 Run 的许可) — a
    /// Workbench sink reached through this path would deliver an
    /// event whose producer never named THIS Run under another
    /// connection's permit lineage, so it is refused with a log.
    pub async fn send_visible_event_by_session(
        &self,
        bcs_group_id: &str,
        event: String,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
        terminal: bool,
    ) -> bool {
        let session_runs = self.session_runs.read().await;

        if let Some(run_ids) = session_runs.get(bcs_group_id) {
            if let Some(run_id) = run_ids.last() {
                let run_id = run_id.clone();
                drop(session_runs);
                if matches!(
                    self.sink_of(&run_id).await,
                    Some(RunClientSink::Workbench { .. })
                ) {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        bcs_group_id = %bcs_group_id,
                        run_id = %run_id,
                        "session fallback refused for a public Workbench sink: no canonical Run binding"
                    );
                    return false;
                }
                debug!(bcs_group_id = %bcs_group_id, run_id = %run_id, "Sending event by session fallback");
                return self
                    .send_visible_event(&run_id, event, visibility_domain, audience, terminal)
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

        for run_id in &run_ids_to_remove {
            self.output_permits.unbind_run(run_id).await;
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
mod tests;
