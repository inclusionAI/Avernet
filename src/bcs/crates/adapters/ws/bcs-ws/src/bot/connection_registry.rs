use std::collections::HashMap;

use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::{
    BotConnectionControlPort, BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult, KickReason,
    ServiceError, ServiceResult,
};
use tokio::sync::{mpsc, oneshot, RwLock};
use tracing::{debug, warn};

#[derive(Debug)]
struct BotConnection {
    tx: mpsc::Sender<String>,
    token_expires_at: Option<u64>,
}

#[derive(Debug, Default)]
pub struct BotConnectionRegistry {
    connections: RwLock<HashMap<String, BotConnection>>,
    pending_requests: RwLock<HashMap<String, oneshot::Sender<serde_json::Value>>>,
}

impl BotConnectionRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub async fn connect(&self, bot_id: String, tx: mpsc::Sender<String>) {
        self.connections.write().await.insert(bot_id, BotConnection {
            tx,
            token_expires_at: None,
        });
    }

    pub async fn disconnect(&self, bot_id: &str) {
        self.connections.write().await.remove(bot_id);
    }

    pub async fn is_connected(&self, bot_id: &str) -> bool {
        self.connections.read().await.contains_key(bot_id)
    }

    pub async fn set_token_expires_at(&self, bot_id: &str, expires_at: u64) {
        let mut conns = self.connections.write().await;
        if let Some(conn) = conns.get_mut(bot_id) {
            conn.token_expires_at = Some(expires_at);
        }
    }

    /// Collect bot_ids whose token will expire within `early_secs` from now.
    /// i.e. disconnect bots where: now + early_secs >= exp
    pub async fn collect_expiring(&self, now_secs: u64, early_secs: u64) -> Vec<String> {
        let conns = self.connections.read().await;
        conns
            .iter()
            .filter_map(|(bot_id, conn)| {
                let exp = conn.token_expires_at?;
                if now_secs + early_secs >= exp {
                    Some(bot_id.clone())
                } else {
                    None
                }
            })
            .collect()
    }

    pub async fn send_frame_json(&self, bot_id: &str, frame_json: String) -> Result<(), ()> {
        let maybe_tx = self.connections.read().await.get(bot_id).map(|c| c.tx.clone());
        let Some(tx) = maybe_tx else {
            debug!(bot_id = %bot_id, "bot delivery skipped: not connected");
            return Err(());
        };

        tx.send(frame_json).await.map_err(|err| {
            warn!(bot_id = %bot_id, error = %err, "bot delivery failed");
        })
    }

    pub async fn send_request(
        &self,
        bot_id: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let request_id = uuid::Uuid::new_v4().to_string();
        let frame = BcsFrame::Request(RequestFrame::new(
            request_id.clone(),
            method.to_string(),
            Some(params),
        ));
        let frame_str = serde_json::to_string(&frame).map_err(|e| e.to_string())?;

        let (tx, rx) = oneshot::channel::<serde_json::Value>();
        {
            let mut pending = self.pending_requests.write().await;
            pending.insert(request_id.clone(), tx);
        }

        if self.send_frame_json(bot_id, frame_str).await.is_err() {
            let mut pending = self.pending_requests.write().await;
            pending.remove(&request_id);
            return Err(format!("Bot '{}' not connected", bot_id));
        }

        match tokio::time::timeout(
            std::time::Duration::from_millis(timeout_ms),
            rx,
        ).await {
            Ok(Ok(payload)) => Ok(payload),
            Ok(Err(_)) => Err("Request channel closed".to_string()),
            Err(_) => {
                let mut pending = self.pending_requests.write().await;
                pending.remove(&request_id);
                Err(format!("Request to bot '{}' timed out after {}ms", bot_id, timeout_ms))
            }
        }
    }

    pub async fn resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        let mut pending = self.pending_requests.write().await;
        if let Some(tx) = pending.remove(request_id) {
            let _ = tx.send(response);
        }
    }
}

#[async_trait]
impl BotDeliveryPort for BotConnectionRegistry {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        match target {
            BotDeliveryTarget::WebSocket { bot_id } => {
                self.connections.read().await.contains_key(bot_id)
            }
            BotDeliveryTarget::HttpProvider { .. } => false,
        }
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let BotDeliveryTarget::WebSocket { bot_id } = &cmd.target else {
            return Ok(BotDeliveryResult {
                target_bot_id: cmd.target_bot_id().to_string(),
                delivered: false,
                error: Some(ServiceError::InvalidOperation {
                    message: "websocket registry cannot deliver http provider target".to_string(),
                    request_id: Some(cmd.run_id),
                }),
            });
        };
        let frame_json = serde_json::to_string(&cmd.frame)
            .map_err(|err| ServiceError::InternalError(format!("serialize bot frame: {err}")))?;

        match self.send_frame_json(bot_id, frame_json).await {
            Ok(()) => Ok(BotDeliveryResult {
                target_bot_id: bot_id.clone(),
                delivered: true,
                error: None,
            }),
            Err(()) => Ok(BotDeliveryResult {
                target_bot_id: bot_id.clone(),
                delivered: false,
                error: Some(ServiceError::BotNotConnected(bot_id.clone())),
            }),
        }
    }
}

#[async_trait]
impl BotConnectionControlPort for BotConnectionRegistry {
    async fn kick(&self, bot_id: &str, reason: KickReason) -> bool {
        let maybe_conn = self.connections.write().await.remove(bot_id);
        let Some(conn) = maybe_conn else {
            debug!(bot_id = %bot_id, "kick skipped: bot not connected");
            return false;
        };
        let frame = serde_json::json!({
            "type": "event",
            "event": "bot.kicked",
            "payload": { "reason": reason.as_str() },
        });
        let frame_str = match serde_json::to_string(&frame) {
            Ok(s) => s,
            Err(err) => {
                warn!(bot_id = %bot_id, error = %err, "kick: failed to serialize event frame");
                return true;
            }
        };
        let _ = conn.tx.send(frame_str).await;
        drop(conn);
        true
    }
}
