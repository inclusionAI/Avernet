use super::*;

impl MemoryBotRepo {
    pub(super) async fn repo_has_been_onboarded(&self, bot_id: &str) -> bool {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return false;
        }
        self.bot_info_path(bot_id).exists()
    }

    pub(super) async fn repo_save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        // Update in-memory (respects overwrite flag; early return if not overwriting and already claimed)
        let already_claimed = {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                if overwrite || bot.created_by.is_none() {
                    bot.created_by = Some(created_by.to_string());
                    false
                } else {
                    true
                }
            } else {
                false
            }
        };

        if already_claimed {
            return Ok(());
        }

        // Update on disk (respects overwrite flag)
        let path = self.bot_info_path(bot_id);
        if path.exists() {
            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(mut persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    if overwrite || persisted.created_by.is_none() {
                        persisted.created_by = Some(created_by.to_string());
                        let updated = serde_json::to_string_pretty(&persisted)?;
                        fs::write(&path, updated).await?;
                        info!(bot_id = %bot_id, created_by = %created_by, overwrite = overwrite, "Updated created_by on disk");
                    }
                }
            }
        }

        Ok(())
    }

    pub(super) async fn repo_save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        // We need to save token separately since it's not in BotCapabilities
        // Read the persisted file directly, update token, and save back
        let path = self.bot_info_path(bot_id);

        // Try to load existing persisted data
        let mut persisted = if path.exists() {
            match fs::read_to_string(&path).await {
                Ok(content) => serde_json::from_str::<PersistedCapabilities>(&content)
                    .unwrap_or_else(|_| PersistedCapabilities {
                        bot_id: bot_id.to_string(),
                        name: None,
                        summary: None,
                        domains: vec![],
                        skills: vec![],
                        scopes: vec![],
                        binding_channels: None,
                        token: Some(token.to_string()),
                        registered_at: 0,
                        hidden: false,
                        created_by: None,
                        visibility: None,
                        agent_code: None,
                        agent_token: None,
                        user_visibility: UserVisibility::default(),
                        friend_ext: serde_json::Map::new(),
                        friend_check_in_strategy: FriendCheckInStrategy::default(),
                    }),
                Err(_) => PersistedCapabilities {
                    bot_id: bot_id.to_string(),
                    name: None,
                    summary: None,
                    domains: vec![],
                    skills: vec![],
                    scopes: vec![],
                    binding_channels: None,
                    token: Some(token.to_string()),
                    registered_at: 0,
                    hidden: false,
                    created_by: None,
                    visibility: None,
                    agent_code: None,
                    agent_token: None,
                    user_visibility: UserVisibility::default(),
                    friend_ext: serde_json::Map::new(),
                    friend_check_in_strategy: FriendCheckInStrategy::default(),
                },
            }
        } else {
            PersistedCapabilities {
                bot_id: bot_id.to_string(),
                name: None,
                summary: None,
                domains: vec![],
                skills: vec![],
                scopes: vec![],
                binding_channels: None,
                token: Some(token.to_string()),
                registered_at: 0,
                hidden: false,
                created_by: None,
                visibility: None,
                agent_code: None,
                agent_token: None,
                user_visibility: UserVisibility::default(),
                friend_ext: serde_json::Map::new(),
                friend_check_in_strategy: FriendCheckInStrategy::default(),
            }
        };

        let previous_token = persisted.token.clone();

        // Update token
        persisted.token = Some(token.to_string());

        // Ensure directory exists
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir).await?;
        }

        // Save
        let content = serde_json::to_string_pretty(&persisted)?;
        fs::write(&path, content).await?;

        let previous_token = {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.session_token.replace(token.to_string()).or(previous_token)
            } else {
                previous_token
            }
        };
        let mut token_to_bot = self.token_to_bot.write().await;
        if let Some(previous_token) = previous_token.filter(|previous| previous != token) {
            token_to_bot.remove(&previous_token);
        }
        token_to_bot.insert(token.to_string(), bot_id.to_string());

        info!(bot_id = %bot_id, "Token saved to storage");
        Ok(())
    }

    pub(super) async fn repo_load_token(&self, bot_id: &str) -> Option<String> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return None;
        }
        {
            let bots = self.bots.read().await;
            if let Some(token) = bots.get(bot_id).and_then(|bot| bot.session_token.clone()) {
                return Some(token);
            }
        }

        let path = self.bot_info_path(bot_id);
        if !path.exists() {
            return None;
        }

        match fs::read_to_string(&path).await {
            Ok(content) => match serde_json::from_str::<PersistedCapabilities>(&content) {
                Ok(persisted) => persisted.token,
                Err(e) => {
                    warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, error = %e, "Failed to parse capabilities file for token");
                    None
                }
            },
            Err(e) => {
                debug!(bot_id = %bot_id, error = %e, "Failed to read capabilities file for token");
                None
            }
        }
    }

    pub(super) async fn repo_find_bot_by_token(&self, token: &str) -> Option<String> {
        // First check in-memory token mapping (fast path)
        {
            let token_to_bot = self.token_to_bot.read().await;
            if let Some(bot_id) = token_to_bot.get(token) {
                if self.deleted_bot_ids.read().await.contains(bot_id) {
                    return None;
                }
                return Some(bot_id.clone());
            }
        }

        // Fall back to scanning disk (slow path, for BCS server restarts)
        let mut entries = match fs::read_dir(&self.bots_base_dir).await {
            Ok(entries) => entries,
            Err(_) => return None,
        };

        while let Ok(Some(entry)) = entries.next_entry().await {
            if !entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false) {
                continue;
            }

            let bot_id = entry.file_name().to_string_lossy().to_string();
            if self.deleted_bot_ids.read().await.contains(&bot_id) {
                continue;
            }
            let path = self.bot_info_path(&bot_id);

            if !path.exists() {
                continue;
            }

            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    if persisted.token.as_deref() == Some(token) {
                        debug!(bot_id = %bot_id, "Found bot by token on disk");
                        return Some(bot_id);
                    }
                }
            }
        }

        None
    }

    pub(super) async fn repo_find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        let index = self.binding_channel_index.read().await;
        index
            .get(&(channel.to_string(), binding_key.to_string()))
            .cloned()
    }

    pub(super) async fn repo_find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        if agent_code.is_empty() {
            return None;
        }

        // Fast path: scan in-memory bots for a matching agent_code.
        {
            let bots = self.bots.read().await;
            for (bot_id, bot) in bots.iter() {
                if self.deleted_bot_ids.read().await.contains(bot_id) {
                    continue;
                }
                if bot.capabilities.agent_code.as_deref() == Some(agent_code) {
                    return Some(bot_id.clone());
                }
            }
        }

        // Fall back to scanning disk (slow path, for BCS server restarts).
        let mut entries = match fs::read_dir(&self.bots_base_dir).await {
            Ok(entries) => entries,
            Err(_) => return None,
        };

        while let Ok(Some(entry)) = entries.next_entry().await {
            if !entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false) {
                continue;
            }

            let bot_id = entry.file_name().to_string_lossy().to_string();
            if self.deleted_bot_ids.read().await.contains(&bot_id) {
                continue;
            }
            let path = self.bot_info_path(&bot_id);
            if !path.exists() {
                continue;
            }

            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    if persisted.agent_code.as_deref() == Some(agent_code) {
                        debug!(bot_id = %bot_id, "Found bot by agent_code on disk");
                        return Some(bot_id);
                    }
                }
            }
        }

        None
    }
}
