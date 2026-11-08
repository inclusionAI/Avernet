//! Streaming connection and request-response bodies for `PersistentBotRepo`.
//!
//! Moved verbatim from the `BotRepoPort` trait impl during the bot-avatar
//! plan Task 1 file-size split; the trait impl keeps explicit delegations
//! to these `*_impl` inherent methods.

use std::time::{Duration, Instant};

use tokio::sync::oneshot;
use tracing::{debug, info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_service_api::{ConnectStreamError, is_mock_token};
use bcs_service_api::port::repo::BotRepoPort;

use super::PersistentBotRepo;
use crate::{BotCapabilities, BotConnection, RegisteredBotInner};

impl PersistentBotRepo {

    pub(super) async fn connect_or_promote_streaming_impl(
        &self,
        bot_id: String,
    ) -> Result<String, ConnectStreamError> {
        // Decide the branch from the registry's authoritative current token
        // (memory → DB fallback via load_token) and existence (memory or DB),
        // before taking the write lock, so a DB-only bot after a BCS restart is
        // still located and a previously-registered bot (even with no token) is
        // refused rather than silently overwritten.
        let existing_token = self.load_token(&bot_id).await;
        let is_mock = existing_token.as_deref().map(is_mock_token).unwrap_or(false);
        let bot_exists = {
            let in_mem = self.bots.read().await.get(&bot_id).is_some();
            in_mem || existing_token.is_some() || self.exists_in_db(&bot_id).await
        };
        // ws_connection presence and connected state are only meaningful in
        // memory; a DB-only entry has no live connection.
        let in_memory_connected = {
            let bots = self.bots.read().await;
            bots.get(&bot_id)
                .map(|bot| bot.ws_connection.is_some())
                .unwrap_or(false)
        };
        let token_preview = |t: &str| format!("{}...", &t[..t.len().min(4)]);

        match (bot_exists, is_mock, in_memory_connected) {
            // truly new (no record anywhere) → create with a real token
            (false, _, _) => {
                let session_token = uuid::Uuid::new_v4().to_string();
                let mut bots = self.bots.write().await;
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_uuid: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities: BotCapabilities::default(),
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        ws_connection: Some(BotConnection {
                            session_token: session_token.clone(),
                            connected_at: Instant::now(),
                        }),
                        protocol_version: 1,
                        session_token: Some(session_token.clone()),
                        env: Some(resolve_env()),
                        hidden: false,
                        created_by: None,
                    },
                );
                self.token_to_bot
                    .write()
                    .await
                    .insert(session_token.clone(), bot_id.clone());
                info!(
                    bot_id = %bot_id,
                    branch = "create",
                    token_preview = %token_preview(&session_token),
                    "register_streaming_connection: create"
                );
                Ok(session_token)
            }
            // pre-registered plugin bot, not yet attached → promote MOCK → real
            (true, true, _) => {
                let previous_mock = existing_token.clone();
                let session_token = uuid::Uuid::new_v4().to_string();
                // Persist FIRST: if this fails, surface the error before any
                // in-memory mutation, so memory and DB never split into a
                // "real-token in memory, MOCK in DB" half-state that only
                // surfaces as a stale-MOCK reconnect after a BCS restart.
                if let Err(err) = self.save_token_to_db(&bot_id, &session_token).await {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        bot_id = %bot_id,
                        error = %err,
                        "connect_or_promote_streaming: promote_mock DB persist failed; refusing ws"
                    );
                    return Err(ConnectStreamError::InternalError(format!(
                        "promote_mock: failed to persist promoted token: {err}"
                    )));
                }
                let mut bots = self.bots.write().await;
                if let Some(bot) = bots.get_mut(&bot_id) {
                    bot.ws_connection = Some(BotConnection {
                        session_token: session_token.clone(),
                        connected_at: Instant::now(),
                    });
                    bot.session_token = Some(session_token.clone());
                    bot.last_heartbeat = Instant::now();
                } else {
                    // DB-only after restart: hydrate the entry with the new real
                    // token so subsequent connects see a real-token bot.
                    bots.insert(
                        bot_id.clone(),
                        RegisteredBotInner {
                            bot_uuid: bot_id.clone(),
                            last_heartbeat: Instant::now(),
                            capabilities: BotCapabilities::default(),
                            status: bcs_service_api::ActorStatus::Online,
                            actor_kind: bcs_service_api::ActorKind::Bot,
                            ws_connection: Some(BotConnection {
                                session_token: session_token.clone(),
                                connected_at: Instant::now(),
                            }),
                            protocol_version: 1,
                            session_token: Some(session_token.clone()),
                            env: Some(resolve_env()),
                            hidden: false,
                            created_by: None,
                        },
                    );
                }
                let mut token_to_bot = self.token_to_bot.write().await;
                if let Some(prev) = previous_mock {
                    token_to_bot.remove(&prev);
                }
                token_to_bot.insert(session_token.clone(), bot_id.clone());
                info!(
                    bot_id = %bot_id,
                    branch = "promote_mock",
                    previous_token_kind = "mock",
                    token_preview = %token_preview(&session_token),
                    "register_streaming_connection: promote_mock"
                );
                Ok(session_token)
            }
            // real token, already connected → reject
            (true, false, true) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id = %bot_id,
                    branch = "already_connected",
                    "connect_or_promote_streaming: real-token bot already connected"
                );
                Err(ConnectStreamError::AlreadyConnected(bot_id))
            }
            // real token, not connected → anti-hijack: refuse an empty/stale-token
            // claim of a real-token bot. (Reconnect by the real token still works
            // via the existing reconnect_streaming(token) path.)
            (true, false, false) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id = %bot_id,
                    branch = "already_registered",
                    "connect_or_promote_streaming: refusing empty/stale-token claim of real-token bot"
                );
                Err(ConnectStreamError::AlreadyRegistered(bot_id))
            }
        }
    }

    pub(super) async fn register_streaming_connection_impl(&self, bot_id: String) -> Result<String, ()> {
        info!(bot_id = %bot_id, "register_streaming_connection: registering new connection");

        let mut bots = self.bots.write().await;

        // Check if bot is already connected
        if let Some(bot) = bots.get(&bot_id) {
            if bot.ws_connection.is_some() {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, "Bot already has an active streaming connection");
                return Err(());
            }
        }

        // Generate session token
        let session_token = uuid::Uuid::new_v4().to_string();

        // Update or create bot entry
        if let Some(bot) = bots.get_mut(&bot_id) {
            bot.ws_connection = Some(BotConnection {
                session_token: session_token.clone(),
                connected_at: Instant::now(),
            });
            bot.session_token = Some(session_token.clone());
            bot.last_heartbeat = Instant::now();
        } else {
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_uuid: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities: BotCapabilities::default(),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    ws_connection: Some(BotConnection {
                        session_token: session_token.clone(),
                        connected_at: Instant::now(),
                    }),
                    protocol_version: 1,
                    session_token: Some(session_token.clone()),
                    env: Some(resolve_env()),
                    hidden: false,
                    created_by: None,
                },
            );
        }

        // Store token mapping in memory
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.insert(session_token.clone(), bot_id.clone());

        // Note: Token is NOT persisted to DB here. It will be saved during onboard
        // when save_to_db is called with the session_token.

        let token_preview = format!("{}...", &session_token[..4]);
        info!(bot_id = %bot_id, token_preview = %token_preview, "register_streaming_connection: connection registered (token in memory only, will persist on onboard)");

        Ok(session_token)
    }

    pub(super) async fn reconnect_streaming_impl(&self, existing_token: String) -> Result<(String, String), ()> {
        let token_preview = format!("{}...", &existing_token[..existing_token.len().min(4)]);
        info!(token_preview = %token_preview, "reconnect_streaming: attempting reconnect");

        // Find bot by token (memory -> database)
        let bot_id = self.find_bot_by_token(&existing_token).await.ok_or(())?;

        let token_preview = format!("{}...", &existing_token[..existing_token.len().min(4)]);
        info!(bot_id = %bot_id, token_preview = %token_preview, "reconnect_streaming: found bot by token");

        let mut bots = self.bots.write().await;

        // Check if bot is already connected
        if let Some(bot) = bots.get(&bot_id) {
            if bot.ws_connection.is_some() {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, "Bot already has an active connection");
                return Err(());
            }
        }

        // Update or create bot entry (reuse the existing write lock)
        if let Some(bot) = bots.get_mut(&bot_id) {
            bot.ws_connection = Some(BotConnection {
                session_token: existing_token.clone(),
                connected_at: Instant::now(),
            });
            bot.last_heartbeat = Instant::now();
            info!(bot_id = %bot_id, "reconnect_streaming: updated existing bot in memory");
        } else {
            // Bot not in memory - load persistent registration details from the database
            drop(bots);

            // Code-Review fix #1: capture actor_kind/status from the database so the
            // reconnected entry reflects the true actor type and lifecycle
            // status (Human reconnects must not silently downgrade to Bot/Online).
            let (capabilities, env, _hidden, created_by, actor_kind, actor_status) =
                self.load_from_db(&bot_id, false).await.unwrap_or((
                    BotCapabilities::default(),
                    Some(resolve_env()),
                    false,
                    None,
                    bcs_service_api::ActorKind::Bot,
                    bcs_service_api::ActorStatus::Online,
                ));

            info!(bot_id = %bot_id, caps_loaded = capabilities.name.is_some(), "reconnect_streaming: loaded capabilities from storage");

            let mut bots = self.bots.write().await;
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_uuid: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities,
                    ws_connection: Some(BotConnection {
                        session_token: existing_token.clone(),
                        connected_at: Instant::now(),
                    }),
                    protocol_version: 1,
                    session_token: Some(existing_token.clone()),
                    env,
                    hidden: false,
                    status: actor_status,
                    actor_kind,
                    created_by,
                },
            );
        }

        // Store token mapping
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.insert(existing_token.clone(), bot_id.clone());

        let token_preview = format!("{}...", &existing_token[..existing_token.len().min(4)]);
        info!(bot_id = %bot_id, token_preview = %token_preview, "reconnect_streaming: connection re-established");

        Ok((bot_id, existing_token))
    }

    pub(super) async fn disconnect_streaming_impl(&self, bot_id: &str) {
        let mut bots = self.bots.write().await;

        if let Some(bot) = bots.get_mut(bot_id) {
            if let Some(conn) = bot.ws_connection.take() {
                // DO NOT remove token_to_bot mapping - token persists for reconnection
                info!(
                    bot_id = %bot_id,
                    token = %conn.session_token,
                    duration_ms = conn.connected_at.elapsed().as_millis() as u64,
                    "Bot streaming connection removed (token preserved for reconnection)"
                );
            }
        } else {
            debug!(bot_id = %bot_id, "Bot not found for disconnect");
        }
    }

    pub(super) async fn is_connected_impl(&self, bot_id: &str) -> bool {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .map(|b| b.ws_connection.is_some())
            .unwrap_or(false)
    }

    pub(super) async fn send_frame_impl(&self, bot_id: &str, _frame: String) -> Result<(), ()> {
        warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, "Bot frame delivery is owned by the ws adapter");
        Err(())
    }

    pub(super) async fn list_connected_impl(&self) -> Vec<String> {
        let bots = self.bots.read().await;
        bots.iter()
            .filter(|(_, b)| b.ws_connection.is_some())
            .map(|(id, _)| id.clone())
            .collect()
    }

    pub(super) async fn store_token_mapping_impl(&self, token: String, bot_id: String) {
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.insert(token.clone(), bot_id.clone());
        debug!(bot_id = %bot_id, token = %token, "Token mapping stored");
    }

    pub(super) async fn get_protocol_version_impl(&self, bot_id: &str) -> u32 {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .map(|bot| bot.protocol_version)
            .unwrap_or(1)
    }

    pub(super) async fn set_protocol_version_impl(&self, bot_id: &str, version: u32) {
        let mut bots = self.bots.write().await;
        if let Some(bot) = bots.get_mut(bot_id) {
            bot.protocol_version = version;
        }
    }

    pub(super) async fn register_http_connection_impl(&self, bot_id: String, token: String) -> String {
        // Create a minimal bot entry if it doesn't exist
        {
            let mut bots = self.bots.write().await;
            if !bots.contains_key(&bot_id) {
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_uuid: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities: BotCapabilities::default(),
                        ws_connection: None,
                        protocol_version: 1,
                        session_token: Some(token.clone()),
                        env: Some(resolve_env()),
                        hidden: false,
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: None,
                    },
                );
                info!(bot_id = %bot_id, "Created minimal bot entry for HTTP connection");
            }
        }
        // Store token mapping
        self.store_token_mapping(token.clone(), bot_id.clone())
            .await;
        token
    }

    pub(super) async fn send_request_impl(
        &self,
        bot_id: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let request_id = uuid::Uuid::new_v4().to_string();
        let frame = serde_json::json!({
            "type": "req",
            "id": request_id,
            "method": method,
            "params": params,
        });
        let frame_str = serde_json::to_string(&frame).map_err(|e| e.to_string())?;

        let (tx, rx) = oneshot::channel::<serde_json::Value>();
        {
            let mut pending = self.pending_requests.write().await;
            pending.insert(request_id.clone(), tx);
        }

        if self.send_frame(bot_id, frame_str).await.is_err() {
            let mut pending = self.pending_requests.write().await;
            pending.remove(&request_id);
            return Err(format!("Bot '{}' not connected", bot_id));
        }

        match tokio::time::timeout(Duration::from_millis(timeout_ms), rx).await {
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

    pub(super) async fn resolve_pending_request_impl(&self, request_id: &str, response: serde_json::Value) {
        let mut pending = self.pending_requests.write().await;
        if let Some(tx) = pending.remove(request_id) {
            let _ = tx.send(response);
        }
    }
}
