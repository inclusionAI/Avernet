use super::*;

impl MemoryBotRepo {
    pub(super) async fn repo_register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.deleted_bot_ids.write().await.remove(&bot_id);

        // Update binding channel index
        self.sync_binding_channel_index(&bot_id, &capabilities)
            .await;

        // Pre-read created_by from disk before acquiring lock
        let persisted = {
            let path = self.bot_info_path(&bot_id);
            if let Ok(content) = fs::read_to_string(&path).await {
                serde_json::from_str::<PersistedCapabilities>(&content).ok()
            } else {
                None
            }
        };
        let persisted_created_by = persisted.as_ref().and_then(|value| value.created_by.clone());
        let persisted_user_visibility = persisted
            .as_ref()
            .map(|value| value.user_visibility)
            .unwrap_or_default();
        let persisted_friend_ext = persisted
            .as_ref()
            .map(|value| value.friend_ext.clone())
            .unwrap_or_default();
        let persisted_friend_check_in_strategy = persisted
            .as_ref()
            .map(|value| value.friend_check_in_strategy)
            .unwrap_or_default();

        let mut bots = self.bots.write().await;

        if let Some(existing) = bots.get_mut(&bot_id) {
            // Update existing registration
            existing.last_heartbeat = Instant::now();
            // Merge capabilities, keeping non-empty values
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
            if capabilities.binding_channels.is_some() {
                existing.capabilities.binding_channels = capabilities.binding_channels;
            }
            if !capabilities.visibility.is_empty() {
                existing.capabilities.visibility = capabilities.visibility.clone();
            }
            // agent_code: 从请求中更新（允许设置或清除）
            if capabilities.agent_code.is_some() {
                existing.capabilities.agent_code = capabilities.agent_code;
            }
            // agent_token: 从请求中更新（允许设置）
            if capabilities.agent_token.is_some() {
                existing.capabilities.agent_token = capabilities.agent_token;
            }
            // Load created_by from disk if not already set in memory
            if existing.created_by.is_none() && persisted_created_by.is_some() {
                existing.created_by = persisted_created_by;
            }
            debug!(bot_id = %bot_id, "Bot registration updated");
        } else {
            // New registration
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_id: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities,
                    ws_connection: None,
                    session_token: None,
                    env: Some(resolve_env()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: persisted_created_by,
                    protocol_version: 1,
                    user_visibility: persisted_user_visibility,
                    friend_ext: persisted_friend_ext,
                    friend_check_in_strategy: persisted_friend_check_in_strategy,
                },
            );
            info!(bot_id = %bot_id, "Bot registered");
        }

        let now = unix_millis();
        let mut audit = self.control_plane_audit.write().await;
        let entry = audit.entry(bot_id).or_insert((now, now));
        entry.1 = now;

        Ok(())
    }

    pub(super) async fn repo_update_capabilities(
        &self,
        bot_id: &str,
        capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        self.deleted_bot_ids.write().await.remove(bot_id);
        self.sync_binding_channel_index(bot_id, &capabilities)
            .await;
        // Persist to disk verbatim (no empty-array skip), matching the
        // wholesale replacement semantics of the core update path.
        self.save_capabilities_to_disk(bot_id, &capabilities).await?;
        let mut bots = self.bots.write().await;
        if let Some(existing) = bots.get_mut(bot_id) {
            existing.last_heartbeat = Instant::now();
            existing.capabilities = capabilities;
            let mut audit = self.control_plane_audit.write().await;
            let now = unix_millis();
            let entry = audit.entry(bot_id.to_string()).or_insert((now, now));
            entry.1 = now;
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
        self.deleted_bot_ids.write().await.remove(&bot_id);

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        let persisted_created_by = {
            let bots = self.bots.read().await;
            bots.get(&bot_id)
                .and_then(|bot| bot.created_by.clone())
                .unwrap_or_else(|| created_by.to_string())
        };
        let persisted_attributes = {
            let path = self.bot_info_path(&bot_id);
            if let Ok(content) = fs::read_to_string(&path).await {
                serde_json::from_str::<PersistedCapabilities>(&content)
                    .ok()
                    .map(|value| {
                        (
                            value.user_visibility,
                            value.friend_ext,
                            value.friend_check_in_strategy,
                        )
                    })
                    .unwrap_or_default()
            } else {
                Default::default()
            }
        };

        let persisted = PersistedCapabilities {
            bot_id: bot_id.clone(),
            name: capabilities.name.clone(),
            summary: capabilities.summary.clone(),
            domains: capabilities.domains.clone(),
            skills: capabilities.skills.clone(),
            scopes: capabilities.scopes.clone(),
            binding_channels: capabilities.binding_channels.clone(),
            token: Some(token.to_string()),
            registered_at: now,
            hidden: false,
            created_by: Some(persisted_created_by),
            visibility: if capabilities.visibility.is_empty() {
                None
            } else {
                Some(capabilities.visibility.clone())
            },
            agent_code: capabilities.agent_code.clone(),
            agent_token: capabilities.agent_token.clone(),
            user_visibility: persisted_attributes.0,
            friend_ext: persisted_attributes.1.clone(),
            friend_check_in_strategy: persisted_attributes.2,
        };

        let path = self.bot_info_path(&bot_id);
        let dir = path.parent().ok_or_else(|| {
            ServiceError::InternalError(format!("Invalid path for bot: {}", bot_id))
        })?;
        fs::create_dir_all(dir).await?;
        let content = serde_json::to_string_pretty(&persisted)?;
        fs::write(&path, content).await?;

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
                if capabilities.binding_channels.is_some() {
                    existing.capabilities.binding_channels = capabilities.binding_channels.clone();
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
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_id: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities,
                        ws_connection: None,
                        session_token: Some(token.to_string()),
                        env: Some(resolve_env()),
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: Some(created_by.to_string()),
                        protocol_version: 1,
                        user_visibility: persisted_attributes.0,
                        friend_ext: persisted_attributes.1,
                        friend_check_in_strategy: persisted_attributes.2,
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

        let now = unix_millis();
        let mut audit = self.control_plane_audit.write().await;
        let entry = audit.entry(bot_id.clone()).or_insert((now, now));
        entry.1 = now;

        info!(bot_id = %bot_id, "Bot registered with owner and token");
        Ok(())
    }

    pub(super) async fn repo_update_status(&self, bot_id: &str) -> bool {
        let mut bots = self.bots.write().await;

        if let Some(bot) = bots.get_mut(bot_id) {
            bot.last_heartbeat = Instant::now();
            debug!(bot_id = %bot_id, "Bot heartbeat renewed");
            true
        } else {
            debug!(bot_id = %bot_id, "Bot not found for status update");
            false
        }
    }
}
