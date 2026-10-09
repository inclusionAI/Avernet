use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use async_trait::async_trait;
use bcs_domain::{ActorStatus, HumanMessageView, MessageAudience, MessageVisibilityDomain};
use bcs_service_api::application::v1::delivery_authorization::DeliveryAuthorizationService;
use bcs_service_api::port::{ParticipantViewBindingPort, ParticipantViewScopeChangeLease};
use bcs_service_api::{BotDetailCommand, BotQueryService, ServiceError, ServiceResult};
use serde_json::Value;
use tokio::sync::{Mutex, RwLock, mpsc};
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};

use crate::web::protected_delivery::{
    ProtectedDeliveryBinding, ProtectedDeliveryGate, WorkbenchOutbound,
};

#[derive(Debug)]
struct FrontendConnection {
    tx: mpsc::Sender<WorkbenchOutbound>,
    /// The SELECTED VIEW actor id bound to this connection at subscribe time —
    /// a legacy targeting key for actor-directed broadcasts. This field is
    /// NEVER the real operator: a Bot view puts a Bot actor id here while the
    /// real User lives only in `protected` (Task 16, spec §14.5).
    user_id: Option<String>,
    /// The continuous-authorization binding of a verified Human connection.
    /// `None` keeps the connection on the legacy PublicControl lane with the
    /// pre-existing inline visibility behavior.
    protected: Option<ProtectedSlot>,
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
}

#[derive(Debug)]
struct ProtectedSlot {
    binding: Arc<ProtectedDeliveryBinding>,
    /// Binding generation id: globally unique, never reused. A replaced
    /// connection can never inherit a previous generation's queued frames.
    binding_id: u64,
}

/// One bounded enqueue-target snapshot of the same event (spec §14.5):
/// event-related, taken under the registry lock, authorized AFTER the lock is
/// released, and re-verified against conn id / binding generation / closed
/// state before anything is queued.
struct BoundTarget {
    conn_id: u64,
    binding_id: u64,
    binding: Arc<ProtectedDeliveryBinding>,
    silent: bool,
}

static NEXT_CONN_ID: AtomicU64 = AtomicU64::new(1);
static NEXT_SCOPE_CHANGE_LEASE_ID: AtomicU64 = AtomicU64::new(1);

pub struct WorkbenchConnectionRegistry {
    pub connection_epoch: crate::shared::connection_epoch::ConnectionEpoch,
    sessions: RwLock<HashMap<String, Vec<FrontendConnection>>>,
    bot_query: RwLock<Option<Arc<dyn BotQueryService>>>,
    scope_change_barriers: Mutex<HashSet<(String, String, u64)>>,
    scope_changes_disabled: bool,
    /// Shared continuous-authorization gate (Task 16): authorization hook +
    /// binding generations, used at BOTH the pre-enqueue and pre-send
    /// positions. Defaults to fail-closed (no hook attached).
    protected_delivery: Arc<ProtectedDeliveryGate>,
    /// The trusted deployment env boundary for protected bindings — an
    /// assembly fact, never client input, never inferred per frame.
    trusted_env: String,
}

impl std::fmt::Debug for WorkbenchConnectionRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkbenchConnectionRegistry")
            .field("cluster_scope_changes_enabled", &!self.scope_changes_disabled)
            .field("protected_hook_configured", &self.protected_delivery.hook_configured())
            .finish_non_exhaustive()
    }
}

impl Default for WorkbenchConnectionRegistry {
    fn default() -> Self {
        Self {
            connection_epoch: Default::default(),
            sessions: RwLock::new(HashMap::new()),
            bot_query: RwLock::new(None),
            scope_change_barriers: Mutex::new(HashSet::new()),
            scope_changes_disabled: false,
            protected_delivery: Arc::new(ProtectedDeliveryGate::new()),
            trusted_env: resolve_trusted_env(),
        }
    }
}

impl WorkbenchConnectionRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_bot_query(bot_query: Arc<dyn BotQueryService>) -> Self {
        Self {
            connection_epoch: Default::default(),
            sessions: RwLock::new(HashMap::new()),
            bot_query: RwLock::new(Some(bot_query)),
            scope_change_barriers: Mutex::new(HashSet::new()),
            scope_changes_disabled: false,
            protected_delivery: Arc::new(ProtectedDeliveryGate::new()),
            trusted_env: resolve_trusted_env(),
        }
    }

    pub fn with_scope_changes_enabled(mut self, enabled: bool) -> Self {
        self.scope_changes_disabled = !enabled;
        self
    }

    /// The shared pre-enqueue/pre-send authorization gate.
    pub fn protected_delivery(&self) -> Arc<ProtectedDeliveryGate> {
        self.protected_delivery.clone()
    }

    /// Assembly wiring (bootstrap; Task 18) and tests attach the real
    /// `DeliveryAuthorizationService` here. Unwired stays fail-closed.
    pub fn set_delivery_authorization(&self, service: Arc<dyn DeliveryAuthorizationService>) {
        self.protected_delivery.set_authorization(service);
    }

    /// The trusted deployment env boundary used for protected binding
    /// contexts.
    pub fn trusted_env(&self) -> &str {
        &self.trusted_env
    }

    pub async fn set_bot_query(&self, bot_query: Arc<dyn BotQueryService>) {
        *self.bot_query.write().await = Some(bot_query);
    }

    pub async fn subscribe(
        &self,
        session_id: String,
        tx: mpsc::Sender<WorkbenchOutbound>,
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

    pub async fn subscribe_with_shutdown(
        &self,
        session_id: String,
        tx: mpsc::Sender<WorkbenchOutbound>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
        shutdown: CancellationToken,
    ) -> ServiceResult<u64> {
        self.subscribe_internal(session_id, tx, user_id, human_view, shutdown, None)
            .await
    }

    /// Subscribe with a verified protected-delivery binding. The REAL User /
    /// selected view / binding generation are captured here; every protected
    /// frame enqueued to this connection then flows through the two
    /// authorization positions. Returns `(conn_id, binding_id)`.
    pub async fn subscribe_bound(
        &self,
        session_id: String,
        tx: mpsc::Sender<WorkbenchOutbound>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
        protected: Option<ProtectedDeliveryBinding>,
        shutdown: CancellationToken,
    ) -> ServiceResult<(u64, u64)> {
        let conn_id = self
            .subscribe_internal(session_id.clone(), tx, user_id, human_view, shutdown, protected)
            .await?;
        let binding_id = self
            .sessions
            .read()
            .await
            .get(&session_id)
            .and_then(|conns| conns.iter().find(|conn| conn.conn_id == conn_id))
            .and_then(|conn| conn.protected.as_ref().map(|slot| slot.binding_id))
            .unwrap_or_default();
        Ok((conn_id, binding_id))
    }

    async fn subscribe_internal(
        &self,
        session_id: String,
        tx: mpsc::Sender<WorkbenchOutbound>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
        shutdown: CancellationToken,
        protected: Option<ProtectedDeliveryBinding>,
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
        let protected = protected.map(|binding| {
            let binding_id = self.protected_delivery.register_binding(conn_id);
            ProtectedSlot {
                binding: Arc::new(binding),
                binding_id,
            }
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

    pub async fn unsubscribe(&self, session_id: &str, conn_id: u64) {
        let retired = {
            let mut sessions = self.sessions.write().await;
            sessions
                .get_mut(session_id)
                .and_then(|connections| {
                    connections
                        .iter()
                        .position(|conn| conn.conn_id == conn_id)
                        .map(|idx| connections.remove(idx))
                })
                .and_then(|conn| conn.protected.as_ref().map(|slot| slot.binding_id))
        };
        if let Some(binding_id) = retired {
            // The old binding generation retires: queued protected frames of
            // this binding are dead backlog (drop, no authority read, no
            // fallback), and a replacement connection gets a fresh generation.
            self.protected_delivery.retire_binding(binding_id);
        }
    }

    /// The protected channel anchor for a live connection's CURRENT binding:
    /// used by the dispatcher when registering run channels so run fallback /
    /// re-dispatches re-authorize with the SAME real identity context.
    pub async fn channel_binding_of(
        &self,
        session_id: &str,
        conn_id: u64,
    ) -> Option<crate::web::protected_delivery::ProtectedChannelRef> {
        let sessions = self.sessions.read().await;
        let conn = sessions
            .get(session_id)?
            .iter()
            .find(|conn| conn.conn_id == conn_id)?;
        let slot = conn.protected.as_ref()?;
        let binding = &slot.binding;
        Some(crate::web::protected_delivery::ProtectedChannelRef {
            user_id: binding.user_id.clone(),
            view_actor_id: binding.view_actor_id.clone(),
            tenant: binding.tenant.clone(),
            env: binding.env.clone(),
            resource_kind: binding.resource_kind,
            resource_id: binding.resource_id.clone(),
            binding_id: slot.binding_id,
            gate: self.protected_delivery.clone(),
        })
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
        )
        .await
    }

    pub async fn broadcast_visible_excluding(
        &self,
        session_id: &str,
        event_json: &str,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
        exclude_conn_id: Option<u64>,
    ) -> usize {
        self.broadcast_selected(session_id, event_json, visibility_domain, audience, exclude_conn_id, None).await
    }

    pub async fn broadcast_to_actors(&self, session_id: &str, event_json: &str, actor_ids: &[String], exclude_conn_id: Option<u64>) -> usize {
        self.broadcast_selected(session_id, event_json, MessageVisibilityDomain::Chat, None, exclude_conn_id, Some(actor_ids)).await
    }

    pub async fn broadcast_visible_to_actors(&self, session_id: &str, event_json: &str, actor_ids: &[String], exclude_conn_id: Option<u64>, visibility_domain: MessageVisibilityDomain, audience: Option<&MessageAudience>) -> usize {
        self.broadcast_selected(session_id, event_json, visibility_domain, audience, exclude_conn_id, Some(actor_ids)).await
    }

    /// Position 1 (pre-enqueue, spec §14.5): take a BOUNDED snapshot of this
    /// event's targets under the registry lock, RELEASE the lock before the
    /// application authorize_batch (ceil(K/128) chunks), then re-verify
    /// conn id / binding generation / closed state and enqueue ONLY the
    /// `Deliver` items:
    /// - `SkipMessage`: skip just this frame — no unsubscribe, no close,
    ///   no fallback re-send;
    /// - `InvalidateBinding` / batch `Err`: invalidate the binding (its
    ///   protected backlog is dropped on drain with no further reads) —
    ///   PublicControl traffic of the connection is unaffected.
    async fn broadcast_selected(&self, session_id: &str, event_json: &str, visibility_domain: MessageVisibilityDomain, audience: Option<&MessageAudience>, exclude_conn_id: Option<u64>, actor_ids: Option<&[String]>) -> usize {
        let mut delivered = 0usize;
        // ---- bounded snapshot under the registry lock ----
        let bound_targets: Vec<BoundTarget> = {
            let mut sessions = self.sessions.write().await;
            let Some(connections) = sessions.get_mut(session_id) else {
                return 0;
            };

            let mut disconnected = Vec::new();
            let mut bounded = Vec::new();
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
                if let Some(slot) = conn.protected.as_ref() {
                    // Bound lane: no inline visibility filtering here — the
                    // application hook owns the SkipMessage judgment from the
                    // same history matrix (eligibility first, spec §14.5).
                    bounded.push(BoundTarget {
                        conn_id: conn.conn_id,
                        binding_id: slot.binding_id,
                        binding: Arc::clone(&slot.binding),
                        silent: conn.silent,
                    });
                    continue;
                }
                if conn
                    .human_view
                    .as_ref()
                    .is_some_and(|view| !view.allows_artifact(visibility_domain, audience))
                {
                    continue;
                }

                // Read the flag resolved once at subscribe time — no remote lookup
                // on the broadcast hot path, so a slow/failing BotQuery can never
                // re-stall SSE delivery.
                let payload = if conn.silent {
                    stamp_silent_true(event_json)
                } else {
                    event_json.to_string()
                };

                match conn.tx.try_send(WorkbenchOutbound::PublicControl(payload)) {
                    Ok(()) => {
                        delivered += 1;
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
            bounded
            // ---- registry lock released; authorized below ----
        };
        delivered
            + self
                .deliver_authorized_bound(
                    session_id,
                    bound_targets,
                    event_json,
                    visibility_domain,
                    audience,
                )
                .await
    }

    /// Run the position-1 authorization over the released-lock snapshot and
    /// enqueue only the `Deliver` items. Every registered (still live) target
    /// of the event is verified or invalidated — none is defaulted to
    /// authorized, and a failing batch fails the whole event closed.
    async fn deliver_authorized_bound(
        &self,
        session_id: &str,
        targets: Vec<BoundTarget>,
        event_json: &str,
        visibility_domain: MessageVisibilityDomain,
        audience: Option<&MessageAudience>,
    ) -> usize {
        if targets.is_empty() {
            return 0;
        }
        let gate = self.protected_delivery.clone();
        let contexts: Vec<_> = targets
            .iter()
            .map(|target| {
                target.binding.context(
                    bcs_service_api::application::v1::delivery_authorization::DeliveryAction::DeliverFrame,
                    visibility_domain,
                    audience,
                )
            })
            .collect();
        let decisions = gate.authorize_targets(contexts.clone()).await;
        let mut delivered = 0usize;
        let mut disconnected = Vec::new();
        match decisions {
            Ok(decisions) => {
                for ((target, context), decision) in targets.iter().zip(contexts).zip(decisions) {
                    match decision {
                        bcs_service_api::application::v1::delivery_authorization::DeliveryAuthorizationDecision::Deliver => {
                            // Post-read recheck (spec §17.2): the connection
                            // must still hold the SAME conn id and binding
                            // generation and stay open — an authorization
                            // result must never reach a replaced connection.
                            let Some(tx) = self
                                .confirm_bound_target(session_id, target.conn_id, target.binding_id)
                                .await
                            else {
                                continue;
                            };
                            let payload = if target.silent {
                                stamp_silent_true(event_json)
                            } else {
                                event_json.to_string()
                            };
                            match tx.try_send(WorkbenchOutbound::Protected {
                                payload,
                                context,
                                binding_id: target.binding_id,
                            }) {
                                Ok(()) => {
                                    delivered += 1;
                                    debug!(
                                        session_id = %session_id,
                                        conn_id = target.conn_id,
                                        binding_id = target.binding_id,
                                        "frontend protected event enqueued for delivery"
                                    );
                                }
                                Err(mpsc::error::TrySendError::Closed(_)) => {
                                    disconnected.push((target.conn_id, target.binding_id))
                                }
                                Err(mpsc::error::TrySendError::Full(_)) => {
                                    warn!(request_id = %bcs_observability::CurrentRequestId, session_id = %session_id, conn_id = target.conn_id, "frontend channel full; protected frame dropped");
                                }
                            }
                        }
                        bcs_service_api::application::v1::delivery_authorization::DeliveryAuthorizationDecision::SkipMessage => {
                            // Only this frame is skipped; connection,
                            // subscription and binding all stay.
                        }
                        bcs_service_api::application::v1::delivery_authorization::DeliveryAuthorizationDecision::InvalidateBinding => {
                            gate.invalidate_binding(target.binding_id);
                        }
                    }
                }
            }
            Err(()) => {
                // Fail the whole event's batch closed: every not-yet-verified
                // target is invalidated, nothing is queued (spec §17.2).
                for target in &targets {
                    gate.invalidate_binding(target.binding_id);
                }
            }
        }
        if !disconnected.is_empty() {
            let mut sessions = self.sessions.write().await;
            if let Some(connections) = sessions.get_mut(session_id) {
                let mut retired = Vec::new();
                connections.retain(|conn| {
                    if disconnected
                        .iter()
                        .any(|(conn_id, _)| *conn_id == conn.conn_id)
                    {
                        if let Some(slot) = conn.protected.as_ref() {
                            retired.push(slot.binding_id);
                        }
                        false
                    } else {
                        true
                    }
                });
                drop(sessions);
                for binding_id in retired {
                    gate.retire_binding(binding_id);
                }
            }
        }
        delivered
    }

    /// Re-verify one authorized target against CURRENT registry facts:
    /// the same conn id, the SAME binding generation, still open, and the
    /// generation still live in the gate (no raced invalidation).
    async fn confirm_bound_target(
        &self,
        session_id: &str,
        conn_id: u64,
        binding_id: u64,
    ) -> Option<mpsc::Sender<WorkbenchOutbound>> {
        if !self.protected_delivery.binding_active(binding_id) {
            return None;
        }
        let sessions = self.sessions.read().await;
        let conn = sessions
            .get(session_id)?
            .iter()
            .find(|conn| conn.conn_id == conn_id)?;
        if conn.shutdown.is_cancelled() {
            return None;
        }
        let slot = conn.protected.as_ref()?;
        if slot.binding_id != binding_id {
            return None;
        }
        Some(conn.tx.clone())
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

/// Trusted deployment env boundary resolved once per registry construction
/// (assembly fact; a placeholder until Task 18 threads the config-loaded env
/// through bootstrap wiring — the resolved value is the same resolver the
/// office-network identity stack uses).
fn resolve_trusted_env() -> String {
    bcs_config::resolve_env_str()
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

        let mut retired_bindings = Vec::new();
        let mut sessions = self.sessions.write().await;
        if let Some(connections) = sessions.get_mut(scope_id) {
            let close_event = serde_json::json!({
                "type": "event",
                "event": "close",
                "payload": {
                    "reason": "view_scope_changed",
                    "message": "Participant message view scope changed; reconnect to refresh the view"
                }
            })
            .to_string();
            for connection in connections.iter() {
                let matches_actor = connection
                    .human_view
                    .as_ref()
                    .is_some_and(|view| view.actor_id == human_actor_id);
                if matches_actor {
                    if let Some(slot) = connection.protected.as_ref() {
                        retired_bindings.push(slot.binding_id);
                    }
                    let _ = connection
                        .tx
                        .try_send(WorkbenchOutbound::PublicControl(close_event.clone()));
                    connection.shutdown.cancel();
                }
            }
        }
        drop(sessions);
        drop(barriers);

        // Scope change replaces the connection authorization facts: the old
        // protected binding generations retire so queued sensitive frames are
        // discarded (never flushed) and cannot leak into a re-subscribed view.
        for binding_id in retired_bindings {
            self.protected_delivery.retire_binding(binding_id);
        }

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
            MessageVisibilityDomain::ManagerWorker, Some(&MessageAudience::FullOnly)).await, 0);
        assert!(rx.try_recv().is_err());
        assert_eq!(registry.broadcast_visible_to_actors("session", "{}", &actors, None,
            MessageVisibilityDomain::ManagerWorker, Some(&MessageAudience::Public)).await, 1);
        match rx.try_recv().unwrap() {
            WorkbenchOutbound::PublicControl(payload) => assert_eq!(payload, "{}"),
            other => panic!("legacy lane payloads stay PublicControl, got {other:?}"),
        }
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
        match allowed_rx.try_recv().unwrap() {
            WorkbenchOutbound::PublicControl(payload) => assert_eq!(payload, "{}"),
            other => panic!("expected PublicControl, got {other:?}"),
        }
        assert!(denied_rx.try_recv().is_err());
        assert!(anonymous_rx.try_recv().is_err());
        assert_eq!(registry.broadcast_to_actors("session", "{}", &[], None).await, 0);
    }
}