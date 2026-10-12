//! Streaming/HTTP session registration for the in-memory registry, split
//! out of the former over-limit `memory.rs` (plan Task 3 memory split).
//!
//! These are the bodies behind `BotRepoPort`'s streaming methods (the
//! one-line trait delegations live in `memory_bot_repo.rs`): session
//! token promotion, registration, reconnection, HTTP connection binding
//! and the one-shot request/response dispatch. Moved verbatim.

use super::*;

impl MemoryBotRepo {
    pub(super) async fn promote_streaming_connection(
        &self,
        bot_id: String,
    ) -> Result<String, ConnectStreamError> {
        let existing_token = self.load_token(&bot_id).await;
        let is_mock = existing_token.as_deref().map(is_mock_token).unwrap_or(false);
        let bot_exists = {
            let in_mem = self.bots.read().await.get(&bot_id).is_some();
            in_mem || existing_token.is_some() || self.bot_info_path(&bot_id).exists()
        };
        let in_memory_connected = {
            let bots = self.bots.read().await;
            bots.get(&bot_id)
                .map(|bot| bot.ws_connection.is_some())
                .unwrap_or(false)
        };
        let token_preview = |t: &str| format!("{}...", &t[..t.len().min(4)]);

        match (bot_exists, is_mock, in_memory_connected) {
            (false, _, _) => {
                let session_token = uuid::Uuid::new_v4().to_string();
                let mut bots = self.bots.write().await;
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_id: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities: BotCapabilities::default(),
                        ws_connection: Some(BotConnection {
                            session_token: session_token.clone(),
                            connected_at: Instant::now(),
                        }),
                        session_token: Some(session_token.clone()),
                        env: Some(resolve_env()),
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: None,
                        protocol_version: 1,
                        user_visibility: UserVisibility::default(),
                        friend_ext: serde_json::Map::new(),
                        friend_check_in_strategy: FriendCheckInStrategy::default(),
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
            (true, true, _) => {
                let previous_mock = existing_token.clone();
                let session_token = uuid::Uuid::new_v4().to_string();
                // Persist FIRST (disk file): if this fails, surface the error
                // before any in-memory mutation, so memory and the persisted
                // file never split into a half-state that only surfaces as a
                // stale-MOCK reconnect after a restart. Persisting before the
                // `bots.write()` lock also sidesteps the prior deadlock:
                // `save_token` re-acquires `bots.write()`.
                if let Err(err) = self.save_token(&bot_id, &session_token).await {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        bot_id = %bot_id,
                        error = %err,
                        "connect_or_promote_streaming: promote_mock disk persist failed; refusing ws"
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
                    bots.insert(
                        bot_id.clone(),
                        RegisteredBotInner {
                            bot_id: bot_id.clone(),
                            last_heartbeat: Instant::now(),
                            capabilities: BotCapabilities::default(),
                            status: bcs_service_api::ActorStatus::Online,
                            actor_kind: bcs_service_api::ActorKind::Bot,
                            created_by: None,
                            protocol_version: 1,
                            user_visibility: UserVisibility::default(),
                            friend_ext: serde_json::Map::new(),
                            friend_check_in_strategy: FriendCheckInStrategy::default(),
                            env: Some(resolve_env()),
                            ws_connection: Some(BotConnection {
                                session_token: session_token.clone(),
                                connected_at: Instant::now(),
                            }),
                            session_token: Some(session_token.clone()),
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
            (true, false, true) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id = %bot_id,
                    branch = "already_connected",
                    "connect_or_promote_streaming: real-token bot already connected"
                );
                Err(ConnectStreamError::AlreadyConnected(bot_id))
            }
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

    pub(super) async fn register_streaming_session(&self, bot_id: String) -> Result<String, ()> {
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

        // Either create new bot entry or update existing one
        if let Some(bot) = bots.get_mut(&bot_id) {
            bot.ws_connection = Some(BotConnection {
                session_token: session_token.clone(),
                connected_at: Instant::now(),
            });
            bot.session_token = Some(session_token.clone());
            bot.last_heartbeat = Instant::now();
        } else {
            // Create new bot entry (will be updated with capabilities on onboard)
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_id: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities: BotCapabilities::default(),
                    ws_connection: Some(BotConnection {
                        session_token: session_token.clone(),
                        connected_at: Instant::now(),
                    }),
                    session_token: Some(session_token.clone()),
                    env: Some(resolve_env()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: None,
                    protocol_version: 1,
                    user_visibility: UserVisibility::default(),
                    friend_ext: serde_json::Map::new(),
                    friend_check_in_strategy: FriendCheckInStrategy::default(),
                },
            );
        }

        // Store token mapping
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.insert(session_token.clone(), bot_id.clone());

        info!(bot_id = %bot_id, token = %session_token, "Bot streaming connection registered");

        Ok(session_token)
    }

    pub(super) async fn reconnect_streaming_session(
        &self,
        existing_token: String,
    ) -> Result<(String, String), ()> {
        // Check if token is valid and get bot_id
        let bot_id = {
            let token_to_bot = self.token_to_bot.read().await;
            match token_to_bot.get(&existing_token) {
                Some(id) => id.clone(),
                None => {
                    warn!(request_id = %bcs_observability::CurrentRequestId, token = %existing_token, "Unknown token for reconnection");
                    return Err(());
                }
            }
        };

        let mut bots = self.bots.write().await;

        // Check if bot is already connected
        if let Some(bot) = bots.get(&bot_id) {
            if bot.ws_connection.is_some() {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, "Bot already has an active connection");
                return Err(());
            }
        }

        // Update or create bot entry with connection
        if let Some(bot) = bots.get_mut(&bot_id) {
            bot.ws_connection = Some(BotConnection {
                session_token: existing_token.clone(),
                connected_at: Instant::now(),
            });
            bot.last_heartbeat = Instant::now();
        } else {
            // Bot not in memory, create entry (capabilities will be loaded from disk)
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_id: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities: BotCapabilities::default(),
                    ws_connection: Some(BotConnection {
                        session_token: existing_token.clone(),
                        connected_at: Instant::now(),
                    }),
                    session_token: Some(existing_token.clone()),
                    env: Some(resolve_env()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: None,
                    protocol_version: 1,
                    user_visibility: UserVisibility::default(),
                    friend_ext: serde_json::Map::new(),
                    friend_check_in_strategy: FriendCheckInStrategy::default(),
                },
            );
        }

        info!(bot_id = %bot_id, token = %existing_token, "Bot streaming connection re-established");

        Ok((bot_id, existing_token))
    }

    pub(super) async fn register_http_session(&self, bot_id: String, token: String) -> String {
        // Create a minimal bot entry if it doesn't exist
        {
            let mut bots = self.bots.write().await;
            if !bots.contains_key(&bot_id) {
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_id: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities: BotCapabilities::default(),
                        ws_connection: None,
                        session_token: Some(token.clone()),
                        env: Some(resolve_env()),
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: None,
                        protocol_version: 1,
                        user_visibility: UserVisibility::default(),
                        friend_ext: serde_json::Map::new(),
                        friend_check_in_strategy: FriendCheckInStrategy::default(),
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

    pub(super) async fn dispatch_streaming_request(
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
}
