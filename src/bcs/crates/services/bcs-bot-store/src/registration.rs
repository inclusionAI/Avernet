use super::*;

impl PersistentBotRepo {
    pub(super) async fn repo_register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        info!(bot_id = %bot_id, name = ?capabilities.name, "register: received registration request");

        // Sync binding channel index
        self.sync_binding_channel_index(&bot_id, &capabilities)
            .await;

        // Get token from memory for DB insert
        let session_token: Option<String> = {
            let bots = self.bots.read().await;
            bots.get(&bot_id).and_then(|b| b.session_token.clone())
        };

        // Save to the configured database (include token if available)
        self.save_to_db(&bot_id, &capabilities, session_token.as_deref(), None)
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, error = %e, "Failed to save bot to database during register");
                e
            })?;

        // Update memory
        let mut bots = self.bots.write().await;

        if let Some(existing) = bots.get_mut(&bot_id) {
            existing.last_heartbeat = Instant::now();
            if capabilities.name.is_some() {
                existing.capabilities.name = capabilities.name;
            }
            if capabilities.summary.is_some() {
                existing.capabilities.summary = capabilities.summary;
            }
            if !capabilities.domains.is_empty() {
                existing.capabilities.domains = capabilities.domains;
            }
            if !capabilities.skills.is_empty() {
                existing.capabilities.skills = capabilities.skills;
            }
            if !capabilities.scopes.is_empty() {
                existing.capabilities.scopes = capabilities.scopes;
            }
            if !capabilities.visibility.is_empty() {
                existing.capabilities.visibility = capabilities.visibility;
            }
            if capabilities.agent_code.is_some() {
                existing.capabilities.agent_code = capabilities.agent_code;
            }
            if capabilities.agent_token.is_some() {
                existing.capabilities.agent_token = capabilities.agent_token;
            }
            info!(bot_id = %bot_id, "register: updated existing bot");
        } else {
            // Normalize: empty visibility defaults to "private" for new bots
            let mut caps = capabilities;
            if caps.visibility.is_empty() {
                caps.visibility = "private".to_string();
            }
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_uuid: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities: caps,
                    ws_connection: None,
                    session_token: None,
                    env: Some(resolve_env()),
                    hidden: false,
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: None,
                },
            );
            info!(bot_id = %bot_id, "register: inserted new bot");
        }

        Ok(())
    }

    pub(super) async fn repo_update_capabilities(
        &self,
        bot_id: &str,
        capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        info!(bot_id = %bot_id, name = ?capabilities.name, "update_capabilities: replacing capabilities",);
        let session_token: Option<String> = {
            let bots = self.bots.read().await;
            bots.get(bot_id).and_then(|b| b.session_token.clone())
        };
        let exists_in_db = self.exists_in_db(bot_id).await;
        if exists_in_db {
            self.save_to_db(bot_id, &capabilities, session_token.as_deref(), None)
                .await?;
        }

        // Wholesale in-memory replacement (no `is_empty` skip, unlike
        // `register`) so a PATCH that clears a field also takes effect in the
        // live registry and runtime discovery, not only in the database.
        // Session token / created_by / runtime state are on `RegisteredBotInner`
        // and are left untouched here.
        let updated_in_memory = {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(bot_id) {
                existing.last_heartbeat = Instant::now();
                existing.capabilities = capabilities;
                info!(bot_id = %bot_id, "update_capabilities: replaced capabilities in memory");
                true
            } else {
                info!(
                    bot_id = %bot_id,
                    "update_capabilities: skipped in-memory update because bot is not loaded"
                );
                false
            }
        };

        if updated_in_memory || exists_in_db {
            Ok(())
        } else {
            Err(ServiceError::BotNotFound(bot_id.to_string()))
        }
    }

    pub(super) async fn repo_register_with_owner_and_token(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        info!(bot_id = %bot_id, name = ?capabilities.name, "register_with_owner_and_token: received registration request");

        self.save_to_db(&bot_id, &capabilities, Some(token), Some(created_by))
            .await
            .map_err(|e| {
                warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, error = %e, "Failed to save bot to database during register_with_owner_and_token");
                e
            })?;

        self.sync_binding_channel_index(&bot_id, &capabilities)
            .await;

        let previous_token = {
            let mut bots = self.bots.write().await;

            if let Some(existing) = bots.get_mut(&bot_id) {
                existing.last_heartbeat = Instant::now();
                if capabilities.name.is_some() {
                    existing.capabilities.name = capabilities.name.clone();
                }
                if capabilities.summary.is_some() {
                    existing.capabilities.summary = capabilities.summary.clone();
                }
                if !capabilities.domains.is_empty() {
                    existing.capabilities.domains = capabilities.domains.clone();
                }
                if !capabilities.skills.is_empty() {
                    existing.capabilities.skills = capabilities.skills.clone();
                }
                if !capabilities.scopes.is_empty() {
                    existing.capabilities.scopes = capabilities.scopes.clone();
                }
                if !capabilities.visibility.is_empty() {
                    existing.capabilities.visibility = capabilities.visibility.clone();
                }
                if capabilities.agent_code.is_some() {
                    existing.capabilities.agent_code = capabilities.agent_code.clone();
                }
                if capabilities.agent_token.is_some() {
                    existing.capabilities.agent_token = capabilities.agent_token.clone();
                }
                if existing.created_by.is_none() {
                    existing.created_by = Some(created_by.to_string());
                }
                existing.session_token.replace(token.to_string())
            } else {
                let mut caps = capabilities;
                if caps.visibility.is_empty() {
                    caps.visibility = "private".to_string();
                }
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_uuid: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities: caps,
                        ws_connection: None,
                        session_token: Some(token.to_string()),
                        env: Some(resolve_env()),
                        hidden: false,
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: Some(created_by.to_string()),
                    },
                );
                None
            }
        };

        let mut token_to_bot = self.token_to_bot.write().await;
        if let Some(previous_token) = previous_token.filter(|previous| previous != token) {
            token_to_bot.remove(&previous_token);
        }
        token_to_bot.insert(token.to_string(), bot_id.clone());

        info!(bot_id = %bot_id, "register_with_owner_and_token: completed");
        Ok(())
    }

    pub(super) async fn repo_update_status(&self, bot_id: &str) -> bool {
        // Update memory
        {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.last_heartbeat = Instant::now();
            } else {
                debug!(bot_id = %bot_id, "Bot not found for status update");
                return false;
            }
        }

        debug!(bot_id = %bot_id, "Bot heartbeat renewed");
        true
    }
}
