use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use bcs_domain::ActorStatus;
use bcs_service_api::{BotDetailCommand, BotQueryService};
use serde_json::Value;
use tokio::sync::{RwLock, mpsc};
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
}

static NEXT_CONN_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Default)]
pub struct WorkbenchConnectionRegistry {
    sessions: RwLock<HashMap<String, Vec<FrontendConnection>>>,
    bot_query: RwLock<Option<Arc<dyn BotQueryService>>>,
}

impl std::fmt::Debug for WorkbenchConnectionRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkbenchConnectionRegistry")
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
        }
    }

    pub async fn set_bot_query(&self, bot_query: Arc<dyn BotQueryService>) {
        *self.bot_query.write().await = Some(bot_query);
    }

    pub async fn subscribe(
        &self,
        session_id: String,
        tx: mpsc::Sender<String>,
        user_id: Option<String>,
    ) -> u64 {
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
        };

        self.sessions
            .write()
            .await
            .entry(session_id)
            .or_default()
            .push(conn);
        conn_id
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
            .map(Vec::len)
            .unwrap_or_default()
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
        let mut sessions = self.sessions.write().await;
        let Some(connections) = sessions.get_mut(session_id) else {
            return 0;
        };

        let mut delivered = 0usize;
        let mut disconnected = Vec::new();
        for conn in connections.iter() {
            if exclude_conn_id.is_some_and(|id| conn.conn_id == id) {
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
                    warn!(session_id = %session_id, conn_id = conn.conn_id, "frontend channel full");
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
                    user_id = %user_id,
                    %error,
                    "silent-status get_bot failed at subscribe; defaulting to not-silent"
                );
                false
            }
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
