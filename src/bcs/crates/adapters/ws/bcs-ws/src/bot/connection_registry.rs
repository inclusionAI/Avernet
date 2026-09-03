use std::collections::HashMap;

use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::{BcsFrame, ChatAbortParams, ChatAbortResult, RequestFrame, ResponseFrame};
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotConnectionControlPort, BotDeliveryCommand,
    BotDeliveryPort, BotDeliveryResult, KickReason, ServiceError, ServiceResult,
};
use tokio::sync::{RwLock, mpsc, oneshot};
use tracing::{debug, warn};

fn is_unknown_method_code(code: &str) -> bool {
    [
        "not_found",
        "unknown_method",
        "method_not_found",
        "not_implemented",
    ]
    .iter()
    .any(|candidate| code.eq_ignore_ascii_case(candidate))
}

#[derive(Debug)]
struct BotConnection {
    tx: mpsc::Sender<String>,
    token_expires_at: Option<u64>,
}

#[derive(Debug, Default)]
pub struct BotConnectionRegistry {
    connections: RwLock<HashMap<String, BotConnection>>,
    pending_requests: RwLock<HashMap<String, oneshot::Sender<serde_json::Value>>>,
    pending_abort_requests: RwLock<HashMap<String, oneshot::Sender<ResponseFrame>>>,
}

impl BotConnectionRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub async fn connect(&self, bot_id: String, tx: mpsc::Sender<String>) {
        self.connections.write().await.insert(
            bot_id,
            BotConnection {
                tx,
                token_expires_at: None,
            },
        );
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
        let maybe_tx = self
            .connections
            .read()
            .await
            .get(bot_id)
            .map(|c| c.tx.clone());
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

        match tokio::time::timeout(std::time::Duration::from_millis(timeout_ms), rx).await {
            Ok(Ok(payload)) => Ok(payload),
            Ok(Err(_)) => Err("Request channel closed".to_string()),
            Err(_) => {
                let mut pending = self.pending_requests.write().await;
                pending.remove(&request_id);
                Err(format!(
                    "Request to bot '{}' timed out after {}ms",
                    bot_id, timeout_ms
                ))
            }
        }
    }

    pub async fn resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        let mut pending = self.pending_requests.write().await;
        if let Some(tx) = pending.remove(request_id) {
            let _ = tx.send(response);
        }
    }

    pub async fn resolve_pending_abort_request(
        &self,
        request_id: &str,
        response: ResponseFrame,
    ) -> bool {
        let mut pending = self.pending_abort_requests.write().await;
        let Some(tx) = pending.remove(request_id) else {
            return false;
        };
        let _ = tx.send(response);
        true
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

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        let BotDeliveryTarget::WebSocket { bot_id } = &cmd.target else {
            return Err(ServiceError::InvalidOperation {
                message: "websocket registry cannot abort an HTTP Provider target".to_string(),
                request_id: Some(cmd.command_id),
            });
        };
        let run_id = cmd
            .run_id
            .as_deref()
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: "Bot WebSocket chat.abort requires an exact run_id".to_string(),
                request_id: Some(cmd.command_id.clone()),
            })?;
        let params = serde_json::to_value(ChatAbortParams {
            session_key: cmd.session_id.clone(),
            run_id: Some(run_id.to_string()),
        })
        .map_err(|error| ServiceError::InternalError(format!("serialize chat.abort: {error}")))?;
        let frame = BcsFrame::Request(RequestFrame::new(
            cmd.command_id.clone(),
            "chat.abort",
            Some(params),
        ));
        let frame_json = serde_json::to_string(&frame).map_err(|error| {
            ServiceError::InternalError(format!("serialize chat.abort frame: {error}"))
        })?;
        let (tx, rx) = oneshot::channel();
        self.pending_abort_requests
            .write()
            .await
            .insert(cmd.command_id.clone(), tx);
        if self.send_frame_json(bot_id, frame_json).await.is_err() {
            self.pending_abort_requests
                .write()
                .await
                .remove(&cmd.command_id);
            return Err(ServiceError::BotNotConnected(bot_id.clone()));
        }
        let response = match tokio::time::timeout(
            std::time::Duration::from_millis(cmd.timeout_ms),
            rx,
        )
        .await
        {
            Ok(Ok(response)) => response,
            Ok(Err(_)) => {
                return Err(ServiceError::InternalError(
                    "chat.abort response channel closed".to_string(),
                ));
            }
            Err(_) => {
                self.pending_abort_requests
                    .write()
                    .await
                    .remove(&cmd.command_id);
                return Err(ServiceError::InternalError(format!(
                    "chat.abort request timed out after {}ms",
                    cmd.timeout_ms
                )));
            }
        };
        if !response.ok {
            if response
                .error
                .as_ref()
                .is_some_and(|error| is_unknown_method_code(&error.code))
            {
                return Err(ServiceError::BotMethodUnsupported {
                    bot_id: bot_id.clone(),
                    method: "chat.abort".to_string(),
                });
            }
            let error = response.error.map_or_else(
                || "Bot rejected chat.abort".to_string(),
                |error| format!("{}: {}", error.code, error.message),
            );
            return Err(ServiceError::InvalidOperation {
                message: error,
                request_id: Some(cmd.command_id),
            });
        }
        let result: ChatAbortResult =
            serde_json::from_value(response.payload.unwrap_or(serde_json::Value::Null)).map_err(
                |error| ServiceError::InternalError(format!("decode chat.abort response: {error}")),
            )?;
        if result.aborted_run_ids.len() > 1
            || result
                .aborted_run_ids
                .first()
                .is_some_and(|aborted| aborted != run_id)
        {
            return Err(ServiceError::InvalidOperation {
                message: "Bot chat.abort returned run ids outside the exact request scope"
                    .to_string(),
                request_id: Some(cmd.command_id),
            });
        }
        Ok(BotAbortDeliveryResult {
            target_bot_id: bot_id.clone(),
            aborted_run_ids: result.aborted_run_ids,
        })
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
