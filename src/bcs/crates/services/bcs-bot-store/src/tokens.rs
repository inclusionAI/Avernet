use super::*;

impl PersistentBotRepo {
    pub(super) async fn repo_has_been_onboarded(&self, bot_id: &str) -> bool {
        self.exists_in_db(bot_id).await
    }

    pub(super) async fn repo_save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        // Update in-memory (respects overwrite flag)
        {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                if overwrite || bot.created_by.is_none() {
                    bot.created_by = Some(created_by.to_string());
                }
            }
        }

        // Update in database (conditional based on overwrite)
        self.update_created_by_in_db(bot_id, created_by, overwrite)
            .await
    }

    pub(super) async fn repo_save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        self.save_token_to_db(bot_id, token).await
    }

    pub(super) async fn repo_load_token(&self, bot_id: &str) -> Option<String> {
        // Check memory first
        {
            let bots = self.bots.read().await;
            if let Some(bot) = bots.get(bot_id) {
                if let Some(ref token) = bot.session_token {
                    return Some(token.clone());
                }
            }
        }

        // Load from database
        self.load_token_from_db(bot_id).await
    }

    pub(super) async fn repo_find_bot_by_token(&self, token: &str) -> Option<String> {
        // Check memory cache first (fast path)
        {
            let token_to_bot = self.token_to_bot.read().await;
            if let Some(bot_id) = token_to_bot.get(token) {
                let bot_id = bot_id.clone();
                drop(token_to_bot);
                if self.get(&bot_id).await.is_some() {
                    return Some(bot_id);
                }
                return None;
            }
        }

        // Check memory by iterating bots
        let memory_candidate = {
            let bots = self.bots.read().await;
            bots.iter()
                .find(|(_, bot)| bot.session_token.as_deref() == Some(token))
                .map(|(bot_id, _)| bot_id.clone())
        };
        if let Some(bot_id) = memory_candidate {
            if self.get(&bot_id).await.is_some() {
                return Some(bot_id);
            }
            return None;
        }

        // Fall back to database indexed lookup
        let result = self.find_bot_by_token_in_db(token).await;
        if result.is_none() {
            let prefix = &token[..8.min(token.len())];
            warn!(request_id = %bcs_observability::CurrentRequestId, token_prefix = %prefix, "find_bot_by_token: token not found in memory or database");
        }
        result
    }

    pub(super) async fn repo_find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        if agent_code.is_empty() {
            return None;
        }

        // Fast path: scan in-memory bots for a matching agent_code.
        let memory_candidate = {
            let bots = self.bots.read().await;
            bots.iter()
                .find(|(_, bot)| {
                    bot.capabilities.agent_code.as_deref() == Some(agent_code)
                })
                .map(|(bot_id, _)| bot_id.clone())
        };
        if let Some(bot_id) = memory_candidate {
            if self.get(&bot_id).await.is_some() {
                return Some(bot_id);
            }
            return None;
        }

        // Fall back to the database indexed lookup (idx_agent_code).
        self.find_bot_by_agent_code_in_db(agent_code).await
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
}
