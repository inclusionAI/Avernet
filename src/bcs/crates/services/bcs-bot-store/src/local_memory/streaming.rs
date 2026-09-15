use super::*;

impl MemoryBotRepo {


    pub(super) async fn repo_register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
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

    pub(super) async fn repo_reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
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

        let _identity = self.identity_locks.lock(&bot_id).await;
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

    pub(super) async fn repo_disconnect_streaming(&self, bot_id: &str) {
        let mut bots = self.bots.write().await;

        if let Some(bot) = bots.get_mut(bot_id) {
            if let Some(conn) = bot.ws_connection.take() {
                // DO NOT remove token_to_bot mapping - token should persist for reconnection
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

    pub(super) async fn repo_is_connected(&self, bot_id: &str) -> bool {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .map(|b| b.ws_connection.is_some())
            .unwrap_or(false)
    }

    pub(super) async fn repo_send_frame(&self, bot_id: &str, _frame: String) -> Result<(), ()> {
        warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, "Bot frame delivery is owned by the ws adapter");
        Err(())
    }

    pub(super) async fn repo_list_connected(&self) -> Vec<String> {
        let bots = self.bots.read().await;
        bots.iter()
            .filter(|(_, b)| b.ws_connection.is_some())
            .map(|(id, _)| id.clone())
            .collect()
    }

    pub(super) async fn repo_store_token_mapping(&self, token: String, bot_id: String) {
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.insert(token.clone(), bot_id.clone());
        debug!(bot_id = %bot_id, token = %token, "Token mapping stored");
    }

    pub(super) async fn repo_get_protocol_version(&self, bot_id: &str) -> u32 {
        let bots = self.bots.read().await;
        bots.get(bot_id).map(|b| b.protocol_version).unwrap_or(1)
    }

    pub(super) async fn repo_set_protocol_version(&self, bot_id: &str, version: u32) {
        let mut bots = self.bots.write().await;
        if let Some(bot) = bots.get_mut(bot_id) {
            bot.protocol_version = version;
        }
    }

    pub(super) async fn repo_register_http_connection(&self, bot_id: String, token: String) -> String {
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
        self.repo_store_token_mapping(token.clone(), bot_id.clone())
            .await;
        token
    }

    pub(super) async fn repo_send_request(
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

    pub(super) async fn repo_resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        let mut pending = self.pending_requests.write().await;
        if let Some(tx) = pending.remove(request_id) {
            let _ = tx.send(response);
        }
    }
}
