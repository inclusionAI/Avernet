use super::*;

impl PersistentBotRepo {
    pub(super) async fn repo_unregister(&self, bot_id: &str) -> bool {
        self.soft_delete(bot_id).await
    }

    pub(super) async fn repo_soft_delete(&self, bot_id: &str) -> bool {
        // Soft-delete in the configured database
        let db_deleted = self.soft_delete_in_db(bot_id).await;

        // Remove from memory
        let mut bots = self.bots.write().await;
        let memory_removed = bots.remove(bot_id).is_some();

        // Remove token mapping
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.retain(|_, v| v != bot_id);

        db_deleted || memory_removed
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
        self.load_from_db(bot_id, false).await.map(
            |(mut caps, _env, _hidden, _created_by, _actor_kind, _status)| {
                // agent_token 是运行时敏感字段，只存内存，不从 DB 恢复
                caps.agent_token = None;
                caps
            },
        )
    }

    pub(super) async fn repo_save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
        // Sync binding channel index
        self.sync_binding_channel_index(bot_id, caps).await;

        // Get token from memory for DB insert
        let session_token: Option<String> = {
            let bots = self.bots.read().await;
            bots.get(bot_id).and_then(|b| b.session_token.clone())
        };

        // Save to the configured database
        self.save_to_db(bot_id, caps, session_token.as_deref(), None)
            .await?;

        // Update memory with the final merged capabilities produced by the
        // application layer.
        let mut bots = self.bots.write().await;
        if let Some(existing) = bots.get_mut(bot_id) {
            existing.last_heartbeat = Instant::now();
            existing.capabilities.name = caps.name.clone();
            existing.capabilities.summary = caps.summary.clone();
            existing.capabilities.domains = caps.domains.clone();
            existing.capabilities.skills = caps.skills.clone();
            existing.capabilities.scopes = caps.scopes.clone();
            existing.capabilities.binding_channels = caps.binding_channels.clone();
            if !caps.visibility.is_empty() {
                existing.capabilities.visibility = caps.visibility.clone();
            }
            if caps.agent_code.is_some() {
                existing.capabilities.agent_code = caps.agent_code.clone();
            }
            if caps.agent_token.is_some() {
                existing.capabilities.agent_token = caps.agent_token.clone();
            }
        }

        Ok(())
    }

    pub(super) async fn repo_update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        let env = resolve_env();
        let visibility_value = if visibility.is_empty() {
            "private"
        } else {
            visibility
        };

        let sql = "UPDATE bcs_bots SET visibility = ? WHERE bot_uuid = ? AND env = ?";
        self.db_execute_affected(
            sql,
            vec![
                Value::from(visibility_value),
                Value::from(bot_id),
                Value::from(env.as_str()),
            ],
        )
        .await
        .map_err(|e| {
            warn!(request_id = %bcs_observability::CurrentRequestId, bot_uuid = %bot_id, error = %e, "update_visibility: failed to update database");
            ServiceError::InternalError(e.to_string())
        })?;

        // Update in-memory
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(bot_id) {
                existing.capabilities.visibility = visibility_value.to_string();
            }
        }

        info!(bot_uuid = %bot_id, visibility = %visibility_value, "update_visibility: updated");
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

    /// Update the actor-level lifecycle status (`Online` / `Hidden`) — Task P.2 + H.2.
    ///
    /// Persists to `bcs_bots.status` and keeps in-memory `RegisteredBotInner.status`
    /// in sync. Idempotent: writing the same value is a no-op at the DB level.
    pub(super) async fn repo_update_actor_status(
        &self,
        bot_id: &str,
        status: bcs_service_api::ActorStatus,
    ) -> ServiceResult<()> {
        let env = resolve_env();
        let status_str = match status {
            bcs_service_api::ActorStatus::Online => "online",
            bcs_service_api::ActorStatus::Hidden => "hidden",
        };

        let sql = "UPDATE bcs_bots SET status = ? WHERE bot_uuid = ? AND env = ?";
        self.db_execute_affected(
            sql,
            vec![
                Value::from(status_str),
                Value::from(bot_id),
                Value::from(env.as_str()),
            ],
        )
        .await
        .map_err(|e| {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                bot_id = %bot_id,
                status = %status_str,
                error = %e,
                "update_actor_status: failed to update bcs_bots.status"
            );
            ServiceError::InternalError(e.to_string())
        })?;

        // Sync in-memory status for already-loaded entries.
        {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.status = status;
            }
        }

        info!(
            bot_id = %bot_id,
            status = %status_str,
            "update_actor_status: updated"
        );
        Ok(())
    }
}
