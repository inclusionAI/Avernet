use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use async_trait::async_trait;
use bcs_domain::{ActorStatus, HumanMessageView, MessageAudience, MessageVisibilityDomain};
use bcs_service_api::port::{ParticipantViewBindingPort, ParticipantViewScopeChangeLease};
use bcs_service_api::{BotDetailCommand, BotQueryService, ServiceError, ServiceResult};
use serde_json::Value;
use tokio::sync::{Mutex, RwLock, mpsc};
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};

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
}

static NEXT_CONN_ID: AtomicU64 = AtomicU64::new(1);
static NEXT_SCOPE_CHANGE_LEASE_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Default)]
pub struct WorkbenchConnectionRegistry {
    sessions: RwLock<HashMap<String, Vec<FrontendConnection>>>,
    bot_query: RwLock<Option<Arc<dyn BotQueryService>>>,
    scope_change_barriers: Mutex<HashSet<(String, String, u64)>>,
    scope_changes_disabled: bool,
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
            sessions: RwLock::new(HashMap::new()),
            bot_query: RwLock::new(Some(bot_query)),
            scope_change_barriers: Mutex::new(HashSet::new()),
            scope_changes_disabled: false,
        }
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

    pub async fn subscribe_with_shutdown(
        &self,
        session_id: String,
        tx: mpsc::Sender<String>,
        user_id: Option<String>,
        human_view: Option<HumanMessageView>,
        shutdown: CancellationToken,
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
        let conn = FrontendConnection {
            tx,
            user_id,
            silent,
            connected_at: Instant::now(),
            conn_id,
            human_view,
            shutdown,
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
        if let Some(connections) = self.sessions.write().await.get_mut(session_id) {
            connections.retain(|conn| conn.conn_id != conn_id);
        }
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

    async fn broadcast_selected(&self, session_id: &str, event_json: &str, visibility_domain: MessageVisibilityDomain, audience: Option<&MessageAudience>, exclude_conn_id: Option<u64>, actor_ids: Option<&[String]>) -> usize {
        let mut sessions = self.sessions.write().await;
        let Some(connections) = sessions.get_mut(session_id) else {
            return 0;
        };

        let mut delivered = 0usize;
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

            match conn.tx.try_send(payload) {
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
        delivered
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
                    let _ = connection.tx.try_send(close_event.clone());
                    connection.shutdown.cancel();
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
