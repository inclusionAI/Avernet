use super::*;

impl PersistentBotRepo {


    pub(super) async fn repo_register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
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

    pub(super) async fn repo_reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        let token_preview = format!("{}...", &existing_token[..existing_token.len().min(4)]);
        info!(token_preview = %token_preview, "reconnect_streaming: attempting reconnect");

        // Find bot by token (memory -> database)
        let bot_id = self.find_bot_by_token(&existing_token).await.ok_or(())?;

        let token_preview = format!("{}...", &existing_token[..existing_token.len().min(4)]);
        info!(bot_id = %bot_id, token_preview = %token_preview, "reconnect_streaming: found bot by token");

        let _identity = self.identity_locks.lock(&bot_id).await;
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

    pub(super) async fn repo_disconnect_streaming(&self, bot_id: &str) {
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

    pub(super) async fn repo_register_http_connection(&self, bot_id: String, token: String) -> String {
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
