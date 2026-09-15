use super::*;

impl MemoryBotRepo {
    pub(super) async fn repo_unregister(&self, bot_id: &str) -> bool {
        self.soft_delete(bot_id).await
    }

    pub(super) async fn repo_soft_delete(&self, bot_id: &str) -> bool {
        self.deleted_bot_ids.write().await.insert(bot_id.to_string());
        let mut bots = self.bots.write().await;
        let memory_removed = bots.remove(bot_id).is_some();
        drop(bots);

        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.retain(|_, value| value != bot_id);
        drop(token_to_bot);

        let mut binding_index = self.binding_channel_index.write().await;
        binding_index.retain(|_, value| value != bot_id);

        memory_removed || self.bot_info_path(bot_id).exists()
    }

    pub(super) async fn repo_cleanup_expired(&self) {
        let mut bots = self.bots.write().await;
        let before = bots.len();
        bots.retain(|_, b| !b.is_expired());
        let removed = before - bots.len();
        if removed > 0 {
            warn!(request_id = %bcs_observability::CurrentRequestId, removed, "Removed expired bot registrations");
        }
    }

    pub(super) async fn repo_load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return None;
        }
        self.load_capabilities_from_disk(bot_id).await
    }

    pub(super) async fn repo_save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        // Update binding channel index
        self.sync_binding_channel_index(bot_id, caps).await;

        // Persist to disk
        self.save_capabilities_to_disk(bot_id, caps).await?;
        // Update in-memory cache so that subsequent get() calls reflect the final
        // merged capabilities produced by the application layer.
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(bot_id) {
                existing.capabilities.name = caps.name.clone();
                existing.capabilities.summary = caps.summary.clone();
                existing.capabilities.domains = caps.domains.clone();
                existing.capabilities.skills = caps.skills.clone();
                existing.capabilities.scopes = caps.scopes.clone();
                existing.capabilities.binding_channels = caps.binding_channels.clone();
                if !caps.visibility.is_empty() {
                    existing.capabilities.visibility = caps.visibility.clone();
                }
                // agent_code: 从请求中更新（允许设置或清除）
                if caps.agent_code.is_some() {
                    existing.capabilities.agent_code = caps.agent_code.clone();
                }
                // agent_token: 从请求中更新（允许设置）
                if caps.agent_token.is_some() {
                    existing.capabilities.agent_token = caps.agent_token.clone();
                }
            }
        }
        Ok(())
    }

    pub(super) async fn repo_update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        let visibility_value = if visibility.is_empty() {
            "private"
        } else {
            visibility
        };

        // Update in-memory
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(bot_id) {
                existing.capabilities.visibility = visibility_value.to_string();
            }
        }

        // Update on disk (only the visibility field)
        let path = self.bot_info_path(bot_id);
        if path.exists() {
            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(mut persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    persisted.visibility = Some(visibility_value.to_string());
                    let updated = serde_json::to_string_pretty(&persisted)?;
                    fs::write(&path, updated).await?;
                    info!(bot_id = %bot_id, visibility = %visibility_value, "Updated visibility on disk");
                }
            }
        }

        Ok(())
    }

    pub(super) async fn repo_set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()> {
        warn!(
            request_id = %bcs_observability::CurrentRequestId,
            bot_id = %bot_id,
            hidden = %hidden,
            "set_hidden is DEPRECATED and is now a Noop; use update_actor_status(bot_id, ActorStatus::Hidden) instead (Task H.1)"
        );
        Ok(())
    }

    /// Update the actor-level lifecycle status (`Online` / `Hidden`) — Task P.2.
    ///
    /// In-memory implementation: only updates the in-process registry entry.
    /// File-based persistence is intentionally not extended for `status` in
    /// the local registry (MemoryBotRepo is a dev-only fallback; production runs
    /// PersistentBotRepo which persists to MySQL).
    pub(super) async fn repo_update_actor_status(
        &self,
        bot_id: &str,
        status: bcs_service_api::ActorStatus,
    ) -> ServiceResult<()> {
        let mut bots = self.bots.write().await;
        if let Some(bot) = bots.get_mut(bot_id) {
            bot.status = status;
            info!(bot_id = %bot_id, status = ?status, "update_actor_status (in-memory): updated");
        } else {
            debug!(bot_id = %bot_id, "update_actor_status (in-memory): bot not loaded, no-op");
        }
        Ok(())
    }
}
