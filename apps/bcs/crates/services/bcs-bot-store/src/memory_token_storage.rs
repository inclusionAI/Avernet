//! Token persistence for the in-memory registry, split out of the
//! former over-limit `memory.rs` (plan Task 3 memory split).
//!
//! Tokens are NOT part of `BotCapabilities`: they are persisted into each
//! bot's `bot.json` directly (disk read - update - write), surviving
//! streaming disconnects so reconnects keep working across restarts.
//! Helper doc comments from the original methods move with them.

use super::*;

impl MemoryBotRepo {
    /// Persist a bot token into `bot.json` (delegated from
    /// `BotRepoPort::save_token`).
    pub(super) async fn persist_bot_token(
        &self,
        bot_id: &str,
        token: &str,
    ) -> ServiceResult<()> {
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

    /// Read a bot token from memory or the persisted file
    /// (delegated from `BotRepoPort::load_token`).
    pub(super) async fn read_persisted_bot_token(&self, bot_id: &str) -> Option<String> {
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

    /// Resolve a bot by its token (delegated from
    /// `BotRepoPort::find_bot_by_token`).
    pub(super) async fn find_bot_id_by_token(&self, token: &str) -> Option<String> {
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

    /// Resolve a bot by its security-gateway agent_code
    /// (delegated from `BotRepoPort::find_bot_by_agent_code`).
    pub(super) async fn find_bot_id_by_agent_code(&self, agent_code: &str) -> Option<String> {
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
