use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use async_trait::async_trait;
use bcs_domain::{ActorStatus, HumanMessageView, MessageAudience, MessageVisibilityDomain};
use bcs_service_api::application::v1::{
    AdmittedRecipient, DeliveryAdmissionService, DeliveryScope, DeliveryVisibility,
    ProtectedDeliveryBinding,
};
use bcs_service_api::port::{ParticipantViewBindingPort, ParticipantViewScopeChangeLease};
use bcs_service_api::{BotDetailCommand, BotQueryService, ServiceError, ServiceResult};
use serde_json::Value;
use tokio::sync::{Mutex, RwLock, mpsc};
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};

use crate::shared::{OutboundFrame, SlowConnectionRetirement, TERMINAL_ENQUEUE_BUDGET};
use crate::shared::output_permits::OutputPermitStore;

#[derive(Debug)]
struct FrontendConnection {
    tx: mpsc::Sender<String>,
    /// The actor id bound to this connection, retained for diagnostics.
    #[allow(dead_code)]
    user_id: Option<String>,
    /// Whether messages to this connection are stamped `silent: true`. Resolved
    /// ONCE at subscribe time from the user's hidden status, so the broadcast
    /// hot path never issues a remote `get_bot` (a per-frame remote lookup here
    /// previously throttled streaming to a few frames/sec). Tradeoff: a mid-
    /// session Online<->Hidden switch is not reflected until the client
    /// reconnects.
    silent: bool,
    connected_at: Instant,
    conn_id: u64,
    human_view: Option<HumanMessageView>,
    shutdown: CancellationToken,
    /// Protected-delivery binding of THIS connection (real
    /// User/token tenant/resource/authorized view; the
    /// instance id is stamped to `conn_id` at insert). The binding
    /// is the admission CANDIDATE presented per operation/event;
    /// frames flow through the connection's bounded queue already
    /// authorized (bot-authority spec §8.2).
    protected: Option<ProtectedConnectionDelivery>,
}

/// The protected delivery channels of one registry
/// connection: the bounded out-of-band frame queue plus THIS
/// connection's admission-candidate binding.
#[derive(Debug, Clone)]
pub struct ProtectedConnectionDelivery {
    pub queue: mpsc::Sender<OutboundFrame>,
    pub binding: ProtectedDeliveryBinding,
}

static NEXT_CONN_ID: AtomicU64 = AtomicU64::new(1);
static NEXT_SCOPE_CHANGE_LEASE_ID: AtomicU64 = AtomicU64::new(1);

pub struct WorkbenchConnectionRegistry {
    pub connection_epoch: crate::shared::connection_epoch::ConnectionEpoch,
    sessions: RwLock<HashMap<String, Vec<FrontendConnection>>>,
    bot_query: RwLock<Option<Arc<dyn BotQueryService>>>,
    scope_change_barriers: Mutex<HashSet<(String, String, u64)>>,
    scope_changes_disabled: bool,
    /// Operation permit store shared with the run manager and the
    /// output-admission port.
    output_permits: Arc<OutputPermitStore>,
    /// Application-level admission resolver consulted per accepted
    /// operation and per independent resource event — never per
    /// frame (bot-authority spec §8.2). Held in a sync lock: the
    /// composition installs it once and the event path only borrows
    /// the Arc briefly (no await under the lock).
    delivery_admission: std::sync::RwLock<Option<Arc<dyn DeliveryAdmissionService>>>,
    events_skipped_auth_failure: AtomicU64,
}

impl Default for WorkbenchConnectionRegistry {
    fn default() -> Self {
        Self {
            connection_epoch: Default::default(),
            sessions: RwLock::new(HashMap::new()),
            bot_query: RwLock::new(None),
            scope_change_barriers: Mutex::new(HashSet::new()),
            scope_changes_disabled: false,
            output_permits: Arc::new(OutputPermitStore::new()),
            delivery_admission: std::sync::RwLock::new(None),
            events_skipped_auth_failure: AtomicU64::new(0),
        }
    }
}

impl std::fmt::Debug for WorkbenchConnectionRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkbenchConnectionRegistry")
            .field("cluster_scope_changes_enabled", &!self.scope_changes_disabled)
            .finish_non_exhaustive()
    }
}

impl WorkbenchConnectionRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_bot_query(bot_query: Arc<dyn BotQueryService>) -> Self {
        Self {
            bot_query: RwLock::new(Some(bot_query)),
            ..Self::default()
        }
    }

    /// Share the composition-wide operation permit store.
    pub fn with_output_permits(mut self, permits: Arc<OutputPermitStore>) -> Self {
        self.output_permits = permits;
        self
    }

    pub fn output_permits(&self) -> Arc<OutputPermitStore> {
        self.output_permits.clone()
    }

    /// Wire the application admission resolver (composition installs
    /// it exactly once; an unwired registry fails every event
    /// closed and installs no operation permits).
    pub fn set_delivery_admission(&self, admission: Arc<dyn DeliveryAdmissionService>) {
        *self
            .delivery_admission
            .write()
            .expect("delivery admission lock poisoned") = Some(admission);
    }

    pub fn delivery_admission(&self) -> Option<Arc<dyn DeliveryAdmissionService>> {
        self.delivery_admission
            .read()
            .expect("delivery admission lock poisoned")
            .clone()
    }

    pub fn events_skipped_auth_failure(&self) -> u64 {
        self.events_skipped_auth_failure.load(Ordering::Relaxed)
    }

    pub fn with_scope_changes_enabled(mut self, enabled: bool) -> Self {
        self.scope_changes_disabled = !enabled;
        self
    }

    pub async fn set_bot_query(&self, bot_query: Arc<dyn BotQueryService>) {
        *self.bot_query.write().await = Some(bot_query);
    }

    pub async fn subscribe(
        &self,
        session_id: String,
        tx: mpsc::Sender<String>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
    ) -> ServiceResult<u64> {
        self.subscribe_with_shutdown(
            session_id,
            tx,
            user_id,
            human_view,
            CancellationToken::new(),
        )
        .await
    }

    /// Subscribing WITHOUT protection keeps the legacy registry
    /// behavior (per-connection `human_view` filter over the raw
    /// String channel). Production Workbench connections subscribe
    /// through [`Self::subscribe_protected_with_shutdown`].
    pub async fn subscribe_with_shutdown(
        &self,
        session_id: String,
        tx: mpsc::Sender<String>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
        shutdown: CancellationToken,
    ) -> ServiceResult<u64> {
        self.do_subscribe(
            session_id,
            tx,
            user_id,
            human_view,
            shutdown,
            None,
        )
        .await
    }

    /// Subscribe with a protected-delivery binding: fanout frames to
    /// this connection flow as `OutboundFrame::Protected` into the
    /// connection's bounded queue and are re-authorized per frame at
    /// the final writer. The binding's connection-instance id is
    /// stamped to the allocated `conn_id`.
    pub async fn subscribe_protected_with_shutdown(
        &self,
        session_id: String,
        tx: mpsc::Sender<String>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
        shutdown: CancellationToken,
        mut binding: ProtectedDeliveryBinding,
        queue: mpsc::Sender<OutboundFrame>,
    ) -> ServiceResult<u64> {
        self.do_subscribe(
            session_id,
            tx,
            user_id,
            human_view,
            shutdown,
            Some(ProtectedConnectionDelivery {
                queue,
                binding: {
                    // The instance id is stamped below; a placeholder
                    // keeps the binding whole until insert.
                    binding.connection_instance_id = 0;
                    binding
                },
            }),
        )
        .await
    }

    async fn do_subscribe(
        &self,
        session_id: String,
        tx: mpsc::Sender<String>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
        shutdown: CancellationToken,
        protected: Option<ProtectedConnectionDelivery>,
    ) -> ServiceResult<u64> {
        // Hold the barrier mutex until the connection is inserted. This makes
        // subscribe linearizable with begin_scope_change: either the new
        // connection is removed by begin, or it observes the barrier and fails.
        let barriers = self.scope_change_barriers.lock().await;
        if let Some(view) = human_view.as_ref()
            && barriers
                .iter()
                .any(|(blocked_scope_id, blocked_actor_id, _)| {
                    blocked_scope_id == &session_id && blocked_actor_id == &view.actor_id
                })
        {
            return Err(ServiceError::Conflict(
                "view_scope_change_in_progress".to_string(),
            ));
        }
        let conn_id = NEXT_CONN_ID.fetch_add(1, Ordering::Relaxed);
        // Resolve the silent (hidden-user) flag ONCE here — before taking the
        // sessions lock — so the broadcast hot path never awaits a remote lookup
        // (and never awaits one while holding the sessions write lock).
        let silent = self.resolve_silent_for_user(user_id.as_deref()).await;
        let protected = protected.map(|mut delivery| {
            delivery.binding.connection_instance_id = conn_id;
            delivery
        });
        let conn = FrontendConnection {
            tx,
            user_id,
            silent,
            connected_at: Instant::now(),
            conn_id,
            human_view,
            shutdown,
            protected,
        };

        self.sessions
            .write()
            .await
            .entry(session_id)
            .or_default()
            .push(conn);
        drop(barriers);
        Ok(conn_id)
    }

    /// The protected bindings of ALL connections currently
    /// subscribed to one scope — the candidate set of an operation
    /// admission. The short read lock is never held across an
    /// admission await (the caller resolves AFTER this returns).
    pub async fn protected_candidates(
        &self,
        session_id: &str,
    ) -> Vec<ProtectedDeliveryBinding> {
        self.sessions
            .read()
            .await
            .get(session_id)
            .map(|connections| {
                connections
                    .iter()
                    .filter(|connection| !connection.shutdown.is_cancelled())
                    .filter_map(|connection| {
                        connection
                            .protected
                            .as_ref()
                            .map(|protected| protected.binding.clone())
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// The protected binding of ONE connection instance (fanout
    /// replay paths re-use the connection's own binding). `None` on
    /// legacy/unprotected subscriptions.
    pub async fn protected_binding(
        &self,
        session_id: &str,
        conn_id: u64,
    ) -> Option<ProtectedDeliveryBinding> {
        self.sessions
            .read()
            .await
            .get(session_id)
            .and_then(|connections| {
                connections
                    .iter()
                    .find(|conn| conn.conn_id == conn_id)
            })
            .and_then(|conn| conn.protected.as_ref().map(|p| p.binding.clone()))
    }

    pub async fn unsubscribe(&self, session_id: &str, conn_id: u64) {
        if let Some(connections) = self.sessions.write().await.get_mut(session_id) {
            connections.retain(|conn| conn.conn_id != conn_id);
        }
        // The connection instance is gone: every operation permit
        // keyed to it is released (permits are never inherited by a
        // replacement instance — spec §6.2).
        self.output_permits.remove_connection(conn_id).await;
    }

    pub async fn connection_count(&self, session_id: &str) -> usize {
        self.sessions
            .read()
            .await
            .get(session_id)
            .map(|connections| {
                connections
                    .iter()
                    .filter(|connection| !connection.shutdown.is_cancelled())
                    .count()
            })
            .unwrap_or_default()
    }

    pub(crate) async fn binding_slot_count(&self, session_id: &str) -> usize {
        self.sessions
            .read()
            .await
            .get(session_id)
            .map(Vec::len)
            .unwrap_or_default()
    }

    pub async fn scope_change_in_progress(&self, session_id: &str, actor_id: &str) -> bool {
        self.scope_change_barriers
            .lock()
            .await
            .iter()
            .any(|(blocked_scope_id, blocked_actor_id, _)| {
                blocked_scope_id == session_id && blocked_actor_id == actor_id
            })
    }

    pub async fn broadcast(&self, session_id: &str, event_json: &str) -> usize {
        self.broadcast_excluding(session_id, event_json, None).await
    }

    pub async fn broadcast_excluding(
        &self,
        session_id: &str,
        event_json: &str,
        exclude_conn_id: Option<u64>,
    ) -> usize {
        self.broadcast_visible_excluding(
            session_id,
            event_json,
            MessageVisibilityDomain::Chat,
            None,
            exclude_conn_id,
            None,
            None,
            false,
        )
        .await
    }

    /// Workbench fanout entry: `operation_id = Some` marks output of
    /// ONE admitted operation — protected connections are checked
    /// against the per-connection permit FROZEN at that operation's
    /// admission (no fresh authorization, no retroactive
    /// revocation). `None` marks an independent finite resource
    /// event — THIS event is authorized once per candidate against
    /// current facts; a read failure skips it without touching any
    /// existing stream (bot-authority spec §8.2/§7.3).
    ///
    /// `run_id` carries the frame's canonical stream identity: a
    /// TERMINAL closes THAT stream only — sibling streams of the
    /// same operation keep flowing, and the operation's output
    /// closes when every stream registered at admission is terminal
    /// (Run terminal ≠ operation complete).
    pub async fn broadcast_visible_excluding(
        &self,
        session_id: &str,
        event_json: &str,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
        exclude_conn_id: Option<u64>,
        operation_id: Option<&str>,
        run_id: Option<&str>,
        terminal: bool,
    ) -> usize {
        self.broadcast_selected(session_id, event_json, visibility_domain, audience, exclude_conn_id, None, operation_id, run_id, terminal).await
    }

    pub async fn broadcast_to_actors(&self, session_id: &str, event_json: &str, actor_ids: &[String], exclude_conn_id: Option<u64>) -> usize {
        self.broadcast_selected(session_id, event_json, MessageVisibilityDomain::Chat, None, exclude_conn_id, Some(actor_ids), None, None, false).await
    }

    pub async fn broadcast_visible_to_actors(&self, session_id: &str, event_json: &str, actor_ids: &[String], exclude_conn_id: Option<u64>, visibility_domain: MessageVisibilityDomain, audience: Option<&MessageAudience>, operation_id: Option<&str>, run_id: Option<&str>, terminal: bool) -> usize {
        self.broadcast_selected(session_id, event_json, visibility_domain, audience, exclude_conn_id, Some(actor_ids), operation_id, run_id, terminal).await
    }

    async fn broadcast_selected(&self, session_id: &str, event_json: &str, visibility_domain: MessageVisibilityDomain, audience: Option<&MessageAudience>, exclude_conn_id: Option<u64>, actor_ids: Option<&[String]>, operation_id: Option<&str>, run_id: Option<&str>, terminal: bool) -> usize {
        // Unprotected (legacy) connections keep the existing
        // human_view filter; protected connections are authorized at
        // THIS enqueue boundary — per operation permit (frozen facts)
        // or per independent event (current facts) — never per frame
        // at the writer (bot-authority spec §8.2).
        //
        // Registry lock discipline: the write lock only COLLECTS the
        // eligible (connection, binding, payload) jobs — it is
        // dropped BEFORE any admission or enqueue await.
        let (protected_jobs, mut delivered) = {
            let mut sessions = self.sessions.write().await;
            let Some(connections) = sessions.get_mut(session_id) else {
                return 0;
            };

            let mut protected_jobs = Vec::new();
            let mut legacy_delivered = 0usize;
            let mut disconnected = Vec::new();
            for conn in connections.iter() {
                if actor_ids.is_some_and(|ids| conn.user_id.as_ref().is_none_or(|actor| !ids.contains(actor))) {
                    continue;
                }
                if conn.shutdown.is_cancelled() {
                    continue;
                }
                if exclude_conn_id.is_some_and(|id| conn.conn_id == id) {
                    continue;
                }
                // Read the flag resolved once at subscribe time — no remote
                // lookup on the broadcast hot path, so a slow/failing BotQuery
                // can never re-stall SSE delivery.
                let payload = if conn.silent {
                    stamp_silent_true(event_json)
                } else {
                    event_json.to_string()
                };
                if let Some(protected) = &conn.protected {
                    protected_jobs.push((protected.clone(), payload));
                    continue;
                }
                if conn
                    .human_view
                    .as_ref()
                    .is_some_and(|view| !view.allows_artifact(visibility_domain, audience))
                {
                    continue;
                }

                match conn.tx.try_send(payload) {
                    Ok(()) => {
                        legacy_delivered += 1;
                        debug!(
                            session_id = %session_id,
                            conn_id = conn.conn_id,
                            connected_ms = conn.connected_at.elapsed().as_millis() as u64,
                            "frontend event delivered"
                        );
                    }
                    Err(mpsc::error::TrySendError::Closed(_)) => disconnected.push(conn.conn_id),
                    Err(mpsc::error::TrySendError::Full(_)) => {
                        warn!(request_id = %bcs_observability::CurrentRequestId, session_id = %session_id, conn_id = conn.conn_id, "frontend channel full");
                    }
                }
            }
            connections.retain(|conn| !disconnected.contains(&conn.conn_id));
            (protected_jobs, legacy_delivered)
        };

        // Outside the registry lock: authorize, THEN enqueue — with
        // no subscriber queue awaited on the hot path. Data frames
        // of a saturated slow subscriber are dropped for THAT
        // connection only (same policy as the legacy lane); terminal
        // frames instead take one SHARED bounded backpressure window
        // (never a silent drop, and N saturated connections never
        // serialize the fanout into N waits). A terminal still lost
        // past the budget retires its connection as an explicit
        // transport failure — the client's socket closes and it
        // recovers through its normal reconnect protocol (spec §9),
        // instead of holding a live stream without its final. A
        // queue whose writer is gone retires that stale
        // subscription.
        let visibility = DeliveryVisibility {
            domain: visibility_domain,
            audience: audience.cloned(),
        };
        let jobs = match operation_id {
            Some(operation_id) => {
                let mut jobs = Vec::with_capacity(protected_jobs.len());
                for (protected, payload) in protected_jobs {
                    let conn_id = protected.binding.connection_instance_id;
                    if self
                        .output_permits
                        .check_frame(operation_id, conn_id, &visibility, run_id)
                        .await
                    {
                        jobs.push((protected.queue, conn_id, payload));
                    } else {
                        debug!(
                            session_id = %session_id,
                            conn_id,
                            "operation frame dropped: no output permit for this connection"
                        );
                    }
                }
                jobs
            }
            None => {
                self.authorize_event_jobs(session_id, protected_jobs, &visibility)
                    .await
            }
        };
        let mut stale_queues = Vec::new();
        let mut terminal_waits: Vec<(mpsc::Sender<OutboundFrame>, u64, OutboundFrame)> = Vec::new();
        for (queue, conn_id, payload) in jobs {
            let frame = if terminal {
                OutboundFrame::Terminal { payload }
            } else {
                OutboundFrame::Data { payload }
            };
            match queue.try_send(frame) {
                Ok(()) => {
                    delivered += 1;
                }
                Err(mpsc::error::TrySendError::Closed(_)) => stale_queues.push(conn_id),
                Err(mpsc::error::TrySendError::Full(frame)) => {
                    if terminal {
                        terminal_waits.push((queue, conn_id, frame));
                    } else {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            session_id = %session_id,
                            conn_id = conn_id,
                            "frontend protected fanout queue full; data frame dropped for slow subscriber"
                        );
                    }
                }
            }
        }
        if !terminal_waits.is_empty() {
            let outcomes = futures::future::join_all(terminal_waits.into_iter().map(
                |(queue, conn_id, frame)| async move {
                    let outcome = match tokio::time::timeout(
                        TERMINAL_ENQUEUE_BUDGET,
                        queue.send(frame),
                    )
                    .await
                    {
                        Ok(Ok(())) => ProtectedEnqueue::Sent,
                        Ok(Err(_)) => ProtectedEnqueue::Closed,
                        Err(_) => ProtectedEnqueue::TerminalLost,
                    };
                    (conn_id, outcome)
                },
            ))
            .await;
            for (conn_id, outcome) in outcomes {
                match outcome {
                    ProtectedEnqueue::Sent => {
                        delivered += 1;
                    }
                    ProtectedEnqueue::Closed => stale_queues.push(conn_id),
                    ProtectedEnqueue::TerminalLost => {
                        warn!(
                            request_id = %bcs_observability::CurrentRequestId,
                            session_id = %session_id,
                            conn_id = conn_id,
                            "TERMINAL frame lost past the backpressure budget; \
                             retiring the saturated connection as a transport failure"
                        );
                        self.events_skipped_auth_failure
                            .fetch_add(1, Ordering::Relaxed);
                        self.retire_connection(conn_id).await;
                    }
                }
            }
        }
        // Run terminal ≠ operation complete (spec §8.2): the frame
        // closes its OWN stream; the operation turns Draining only
        // when every registered stream is terminal. A terminal
        // without a canonical stream identity closes its operation
        // directly (no producer tags one in v1).
        if terminal {
            if let Some(operation_id) = operation_id {
                match run_id {
                    Some(run_id) => {
                        self.output_permits
                            .mark_run_terminal(operation_id, run_id)
                            .await
                    }
                    None => self.output_permits.mark_terminal(operation_id).await,
                }
            }
        }
        for conn_id in stale_queues {
            self.unsubscribe(session_id, conn_id).await;
        }
        delivered
    }

    /// Retire ONE saturated connection instance as an explicit
    /// transport failure (spec §9 slow-consumer policy): cancel its
    /// socket shutdown so the client observes a closed connection,
    /// and remove every state keyed to the instance (subscription
    /// and operation permits — never inherited by a replacement).
    /// Idempotent.
    pub async fn retire_connection(&self, connection_instance_id: u64) {
        let retired = {
            let mut sessions = self.sessions.write().await;
            sessions.iter_mut().find_map(|(session_id, connections)| {
                connections
                    .iter()
                    .position(|conn| conn.conn_id == connection_instance_id)
                    .map(|index| (session_id.clone(), connections.remove(index)))
            })
        };
        let Some((session_id, conn)) = retired else {
            return;
        };
        warn!(
            request_id = %bcs_observability::CurrentRequestId,
            session_id = %session_id,
            conn_id = connection_instance_id,
            "saturated connection retired as an explicit transport failure; the client reconnects"
        );
        self.output_permits
            .remove_connection(connection_instance_id)
            .await;
        conn.shutdown.cancel();
    }

    /// Independent finite resource event (spec §7.3): authorize THIS
    /// event once per candidate against CURRENT facts — one
    /// application batch per distinct canonical scope, never per
    /// frame — and keep only the admitted jobs. A read failure is
    /// fail-closed for the affected candidates (event skipped,
    /// counted) and never cancels any existing stream or permit.
    async fn authorize_event_jobs(
        &self,
        session_id: &str,
        protected_jobs: Vec<(ProtectedConnectionDelivery, String)>,
        visibility: &DeliveryVisibility,
    ) -> Vec<(mpsc::Sender<OutboundFrame>, u64, String)> {
        let Some(admission) = self.delivery_admission() else {
            self.events_skipped_auth_failure
                .fetch_add(1, Ordering::Relaxed);
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                session_id = %session_id,
                "delivery admission is not wired; protected event skipped (fail closed)"
            );
            return Vec::new();
        };
        let mut by_scope: HashMap<(String, Option<String>), Vec<ProtectedDeliveryBinding>> =
            HashMap::new();
        for (protected, _) in &protected_jobs {
            by_scope
                .entry((
                    protected.binding.group_id.clone(),
                    protected.binding.session_id.clone(),
                ))
                .or_default()
                .push(protected.binding.clone());
        }
        let mut admitted: HashMap<u64, AdmittedRecipient> = HashMap::new();
        for ((group_id, event_session_id), bindings) in by_scope {
            let scope = DeliveryScope {
                group_id,
                session_id: event_session_id,
            };
            match admission.resolve_recipient_views(&scope, &bindings).await {
                Ok(recipients) => {
                    for recipient in recipients {
                        admitted.insert(recipient.connection_instance_id, recipient);
                    }
                }
                Err(error) => {
                    self.events_skipped_auth_failure
                        .fetch_add(1, Ordering::Relaxed);
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        session_id = %session_id,
                        %error,
                        "event authorization read failed; event skipped for those candidates"
                    );
                }
            }
        }
        protected_jobs
            .into_iter()
            .filter_map(|(protected, payload)| {
                let permit = admitted.get(&protected.binding.connection_instance_id)?;
                permit
                    .allows(visibility)
                    .then(|| (protected.queue, protected.binding.connection_instance_id, payload))
            })
            .collect()
    }

    /// Resolve whether `user_id` is Hidden (→ stamp `silent: true`). Called ONCE
    /// per connection at subscribe time (never on the broadcast hot path), so a
    /// single remote `get_bot` per connection is fine. On lookup failure it
    /// defaults to not-silent and WARNs — a hidden user could then miss the
    /// silent flag for this connection's lifetime, so the failure is logged.
    async fn resolve_silent_for_user(&self, user_id: Option<&str>) -> bool {
        let Some(user_id) = user_id else {
            return false;
        };
        let bot_query = self.bot_query.read().await.clone();
        let Some(bot_query) = bot_query else {
            return false;
        };
        match bot_query
            .get_bot(BotDetailCommand {
                caller_actor_id: Some(user_id.to_string()),
                bot_id: user_id.to_string(),
            })
            .await
        {
            Ok(actor) => actor.status == ActorStatus::Hidden,
            Err(error) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    user_id = %user_id,
                    %error,
                    "silent-status get_bot failed at subscribe; defaulting to not-silent"
                );
                false
            }
        }
    }
}

#[async_trait]
impl ParticipantViewBindingPort for WorkbenchConnectionRegistry {
    async fn begin_scope_change(
        &self,
        scope_id: &str,
        human_actor_id: &str,
    ) -> ServiceResult<ParticipantViewScopeChangeLease> {
        let lease_id = NEXT_SCOPE_CHANGE_LEASE_ID.fetch_add(1, Ordering::Relaxed);
        let mut barriers = self.scope_change_barriers.lock().await;
        if barriers
            .iter()
            .any(|(blocked_scope_id, blocked_actor_id, _)| {
                blocked_scope_id == scope_id && blocked_actor_id == human_actor_id
            })
        {
            return Err(ServiceError::Conflict(
                "view_scope_change_in_progress".to_string(),
            ));
        }
        barriers.insert((scope_id.to_string(), human_actor_id.to_string(), lease_id));

        // §8.1 of the admission contract: a scope change blocks NEW
        // admissions (the barrier above) but does NOT cancel the
        // connection, because cancellation would retroactively
        // interrupt already-admitted operations on it. The client
        // gets a soft notice (NOT a close protocol frame): existing
        // streams keep flowing under their frozen permits; every NEW
        // operation/event is admitted against the new committed
        // scope the moment it lands.
        let sessions = self.sessions.read().await;
        if let Some(connections) = sessions.get(scope_id) {
            let notice = serde_json::json!({
                "type": "event",
                "event": "view_scope_changed",
                "payload": {
                    "actor_id": human_actor_id,
                    "reason": "participant_message_view_scope_changed",
                    "message": "Participant message view scope changed; on-screen data now follows the new scope for subsequent operations"
                }
            })
            .to_string();
            for connection in connections.iter() {
                let human_match = connection
                    .human_view
                    .as_ref()
                    .is_some_and(|view| view.actor_id == human_actor_id);
                let protected_match = connection
                    .protected
                    .as_ref()
                    .is_some_and(|protected| protected.binding.view_actor_id == human_actor_id);
                if human_match || protected_match {
                    let _ = connection.tx.try_send(notice.clone());
                }
            }
        }
        drop(sessions);
        drop(barriers);

        Ok(ParticipantViewScopeChangeLease {
            scope_id: scope_id.to_string(),
            human_actor_id: human_actor_id.to_string(),
            lease_id,
        })
    }

    async fn finish_scope_change(
        &self,
        lease: ParticipantViewScopeChangeLease,
    ) -> ServiceResult<()> {
        let removed = self.scope_change_barriers.lock().await.remove(&(
            lease.scope_id,
            lease.human_actor_id,
            lease.lease_id,
        ));
        if removed {
            Ok(())
        } else {
            Err(ServiceError::InternalError(
                "participant view-scope change lease was not active".to_string(),
            ))
        }
    }
}

/// Enqueue result of one authorized protected TERMINAL frame after
/// its bounded backpressure wait.
enum ProtectedEnqueue {
    Sent,
    /// TERMINAL frame lost past the bounded backpressure budget:
    /// a transport failure — the saturated connection is retired,
    /// never a silent live stream without its final (spec §9).
    TerminalLost,
    /// The writer is gone: the subscription is stale and retired.
    Closed,
}

#[async_trait]
impl SlowConnectionRetirement for WorkbenchConnectionRegistry {
    async fn retire(&self, connection_instance_id: u64, _reason: &'static str) {
        self.retire_connection(connection_instance_id).await;
    }
}

pub fn stamp_silent_true(event_json: &str) -> String {
    match serde_json::from_str::<Value>(event_json) {
        Ok(Value::Object(mut map)) => {
            map.insert("silent".to_string(), Value::Bool(true));
            serde_json::to_string(&Value::Object(map)).unwrap_or_else(|_| event_json.to_string())
        }
        _ => event_json.to_string(),
    }
}

#[cfg(test)]
mod delivery_recipient_tests {
    use super::*;

    #[tokio::test]
    async fn targeted_status_obeys_human_visibility() {
        let registry = WorkbenchConnectionRegistry::new();
        let (tx, mut rx) = mpsc::channel(2);
        registry.subscribe("session".into(), tx, Some("member".into()), Some(HumanMessageView {
            actor_id: "member".into(), scope: bcs_domain::MessageViewScope::Participant,
            allow_legacy_unclassified_chat: false,
        })).await.unwrap();
        let actors = vec!["member".into()];
        assert_eq!(registry.broadcast_visible_to_actors("session", "{}", &actors, None,
            MessageVisibilityDomain::ManagerWorker, Some(&MessageAudience::FullOnly), None, None, false).await, 0);
        assert!(rx.try_recv().is_err());
        assert_eq!(registry.broadcast_visible_to_actors("session", "{}", &actors, None,
            MessageVisibilityDomain::ManagerWorker, Some(&MessageAudience::Public), None, None, false).await, 1);
        assert_eq!(rx.try_recv().unwrap(), "{}");
    }

    #[tokio::test]
    async fn targeted_status_never_reaches_unselected_or_anonymous_subscribers() {
        let registry = WorkbenchConnectionRegistry::new();
        let (allowed, mut allowed_rx) = mpsc::channel(2);
        let (denied, mut denied_rx) = mpsc::channel(2);
        let (anonymous, mut anonymous_rx) = mpsc::channel(2);
        registry.subscribe("session".into(), allowed, Some("member".into()), None).await.unwrap();
        registry.subscribe("session".into(), denied, Some("other".into()), None).await.unwrap();
        registry.subscribe("session".into(), anonymous, None, None).await.unwrap();
        assert_eq!(registry.broadcast_to_actors("session", "{}", &["member".into()], None).await, 1);
        assert_eq!(allowed_rx.try_recv().unwrap(), "{}");
        assert!(denied_rx.try_recv().is_err());
        assert!(anonymous_rx.try_recv().is_err());
        assert_eq!(registry.broadcast_to_actors("session", "{}", &[], None).await, 0);
    }
}
