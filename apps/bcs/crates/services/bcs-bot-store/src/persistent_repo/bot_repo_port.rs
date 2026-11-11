//! `BotRepoPort` implementation for `PersistentBotRepo`.
//!
//! Moved verbatim from `lib.rs` during the crate split.

use std::time::Instant;

use async_trait::async_trait;
use tracing::{debug, info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{
    DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_service_api::{
    BotCapabilities, ConnectStreamError, RegisteredBot, ServiceError, ServiceResult,
};

use bcs_service_api::port::repo::BotRepoPort;
use super::PersistentBotRepo;
use crate::{BotInfo, log_bot_cache_source};

#[async_trait]
impl BotRepoPort for PersistentBotRepo {
    async fn find_agent_registration(
        &self, agent_code: &str,
    ) -> ServiceResult<Option<bcs_service_api::types::AgentBotRegistration>> {
        self.load_agent_registration(agent_code).await
    }

    async fn try_load_token(&self, bot_id: &str) -> ServiceResult<Option<String>> {
        self.load_registration_token(bot_id).await
    }

    async fn create_registration_if_absent(
        &self, bot_id: String, capabilities: BotCapabilities, created_by: &str, token: &str,
    ) -> ServiceResult<bool> {
        self.create_registration_once(bot_id, capabilities, created_by, token).await
    }

    async fn create_registration_if_absent_with_ownership(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
        initialization: bcs_service_api::types::bot_authority::OwnershipInitialization,
    ) -> ServiceResult<bool> {
        self.create_registration_owned(
            bot_id, capabilities, created_by, token, &initialization,
        )
        .await
    }

    async fn initialize_ownership(
        &self,
        bot_id: &str,
        owner_user_id: &str,
        mutation: bcs_service_api::types::bot_authority::AuthorityMutation,
    ) -> ServiceResult<bcs_service_api::types::bot_authority::BotOwnershipView> {
        self.initialize_owned_bot(bot_id, owner_user_id, &mutation, &[])
            .await
    }

    async fn initialize_provider_authority(
        &self, bot_id: &str,
        initialization: bcs_service_api::types::bot_authority::OwnershipInitialization,
    ) -> ServiceResult<bcs_service_api::types::bot_authority::BotOwnershipView> {
        crate::ownership_managers::validate_provider_initialization(&initialization)?;
        self.initialize_owned_bot(bot_id, &initialization.owner_user_id,
            &initialization.mutation, &initialization.initial_manager_user_ids).await
    }


    async fn ownership_governance_state(
        &self,
        bot_id: &str,
    ) -> ServiceResult<
        Option<bcs_service_api::types::bot_authority::BotOwnershipGovernance>,
    > {
        self.read_ownership_governance(bot_id).await
    }

    async fn retire_with_authority(
        &self,
        bot_id: &str,
        mutation: bcs_service_api::types::bot_authority::AuthorityMutation,
    ) -> ServiceResult<bool> {
        self.retire_bot_with_authority(bot_id, &mutation)
            .await
    }

    // ===== Registration & Discovery =====

    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.register_impl(bot_id, capabilities).await
    }

    async fn update_capabilities(
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

    async fn register_with_owner_and_token(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        self.register_with_owner_and_token_impl(bot_id, capabilities, created_by, token).await
    }

    async fn update_status(&self, bot_id: &str) -> bool {
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

    async fn get_by_ids(&self, bot_ids: &[String]) -> Vec<RegisteredBot> {
        let mut seen = std::collections::HashSet::new();
        let mut results = Vec::new();
        for bot_id in bot_ids {
            if seen.insert(bot_id.as_str()) {
                if let Some(bot) = self.get(bot_id).await {
                    results.push(bot);
                }
            }
        }
        results
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        match bcs_observability::observe_result("bot.load", self.try_get(bot_id)).await {
            Ok(value) => value,
            Err(_) => {
                warn!(target: "bcs_observation", request_id = %bcs_observability::current_request_id(), outcome = "load_error", fallback = "omitted", "bot.load.fallback");
                None
            }
        }
    }

    async fn try_get(&self, bot_id: &str) -> ServiceResult<Option<RegisteredBot>> {
        let bots = bcs_observability::observe_value("bot.memory_lock.wait", self.bots.read()).await;

        if let Some(bot) = bots.get(bot_id) {
            if !bot.is_expired() && self.use_cached_metadata(bot_id).await {
                log_bot_cache_source("memory_hit");
                return Ok(Some(bot.to_registered_bot()));
            }
        }

        // Fallback: load persistent registration details from the database
        drop(bots);
        log_bot_cache_source("memory_miss");

        // Code-Review fix #1: take actor_kind/status from the database instead of
        // returning defaults; otherwise O.5/P.3/F.3 will misclassify any actor
        // whose row is no longer cached in process memory.
        let Some((mut capabilities, env, _hidden, created_by, actor_kind, status)) =
            bcs_observability::observe_result("bot.db_load", self.try_load_from_db(bot_id, false)).await?
        else {
            return Ok(None);
        };

        // 清除敏感字段，防止通过常规接口泄露
        capabilities.agent_token = None;

        Ok(Some(RegisteredBot {
            bot_uuid: bot_id.to_string(),
            capabilities,
            env,
            created_by,
            actor_kind,
            status,
        }))
    }

    /// Batched strict registry read (legacy-mine ruling): the
    /// warm-cache answers come from the process map; every cold
    /// id resolves through ONE chunked SQL load whose failures
    /// propagate — never a silently omitted candidate.
    async fn try_get_many(&self, bot_ids: &[String]) -> ServiceResult<Vec<RegisteredBot>> {
        let mut found: std::collections::HashMap<String, RegisteredBot> =
            std::collections::HashMap::new();
        let mut missing: Vec<String> = Vec::new();
        {
            let bots = self.bots.read().await;
            for bot_id in bot_ids {
                match bots.get(bot_id) {
                    Some(bot) if !bot.is_expired() && self.use_cached_metadata(bot_id).await => {
                        found.insert(bot_id.clone(), bot.to_registered_bot());
                    }
                    _ => missing.push(bot_id.clone()),
                }
            }
        }

        for (bot_uuid, (mut capabilities, env, _hidden, created_by, actor_kind, status)) in
            self.try_load_many_from_db(&missing, false).await?
        {
            capabilities.agent_token = None;
            found.insert(
                bot_uuid.clone(),
                RegisteredBot {
                    bot_uuid,
                    capabilities,
                    env,
                    created_by,
                    actor_kind,
                    status,
                },
            );
        }

        let mut seen = std::collections::HashSet::new();
        let mut out = Vec::new();
        for bot_id in bot_ids {
            if !seen.insert(bot_id.as_str()) {
                continue;
            }
            if let Some(bot) = found.get(bot_id).cloned() {
                out.push(bot);
            }
        }
        Ok(out)
    }

    /// Like [`get`](Self::get) but also returns soft-deleted bots (rows with
    /// `is_deleted = 1`). Used for display-only enrichment where the bot's
    /// `name` snapshot is still needed after removal (e.g. group participant
    /// names in `/bots/{id}/groups`). Sensitive fields are stripped the same
    /// way as `get`.
    ///
    /// Checks the in-memory cache first (the common case during backfill, where
    /// most participants are active, cached bots) and only falls back to a
    /// database read — with the soft-delete filter dropped — on a cache miss,
    /// so removed bots can still be resolved from the retained row without
    /// hitting the database for every participant.
    async fn get_including_deleted(&self, bot_id: &str) -> Option<RegisteredBot> {
        let bots = self.bots.read().await;
        if let Some(bot) = bots.get(bot_id) {
            if !bot.is_expired() && self.use_cached_metadata(bot_id).await {
                return Some(bot.to_registered_bot());
            }
        }
        drop(bots);

        let (mut capabilities, env, _hidden, created_by, actor_kind, status) =
            self.load_from_db(bot_id, true).await?;

        // 清除敏感字段，防止通过常规接口泄露
        capabilities.agent_code = None;
        capabilities.agent_token = None;

        Some(RegisteredBot {
            bot_uuid: bot_id.to_string(),
            capabilities,
            env,
            created_by,
            actor_kind,
            status,
        })
    }

    async fn get_agent_credentials(
        &self,
        bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        // Check in-memory cache first
        let bots = self.bots.read().await;
        if let Some(bot) = bots.get(bot_id) {
            if !bot.is_expired() {
                return Some(bcs_service_api::AgentCredentials {
                    agent_code: bot.capabilities.agent_code.clone(),
                    agent_token: bot.capabilities.agent_token.clone(),
                });
            }
        }
        drop(bots);

        // Fallback: load from database
        let (capabilities, _env, _hidden, _created_by, _actor_kind, _status) =
            self.load_from_db(bot_id, false).await?;

        Some(bcs_service_api::AgentCredentials {
            agent_code: capabilities.agent_code,
            agent_token: capabilities.agent_token,
        })
    }

    async fn add_bot_info(&self, bot_id: &str, key: &str, value: String) {
        // Agent credentials reuse capabilities; negotiated runtime metadata stays
        // in the dedicated process-local override map.
        if key != "agent_token" && key != "client_kind" {
            tracing::warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, key = %key, "add_bot_info: unrecognized key, ignoring");
            return;
        }
        if key == "client_kind" {
            let bots = self.bots.read().await;
            if !bots.contains_key(bot_id) {
                return;
            }
            drop(bots);
            self.bot_info_overrides
                .write()
                .await
                .insert((bot_id.to_string(), key.to_string()), value);
            return;
        }
        let mut bots = self.bots.write().await;
        if let Some(bot) = bots.get_mut(bot_id) {
            bot.capabilities.agent_token = Some(value);
        }
    }

    async fn get_bot_info(&self, bot_id: &str, key: &str) -> Option<String> {
        if key == "client_kind" {
            return self
                .bot_info_overrides
                .read()
                .await
                .get(&(bot_id.to_string(), key.to_string()))
                .cloned();
        }
        if key == "agent_token" {
            let bots = self.bots.read().await;
            return bots
                .get(bot_id)
                .and_then(|bot| bot.capabilities.agent_token.clone());
        }
        None
    }

    async fn set_bot_info(&self, bot_id: &str, key: &str, value: Option<String>) {
        if let Some(value) = value {
            self.add_bot_info(bot_id, key, value).await;
            return;
        }
        if key == "client_kind" {
            self.bot_info_overrides
                .write()
                .await
                .remove(&(bot_id.to_string(), key.to_string()));
        }
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        // Master has active connections in memory, while soft-delete state is
        // authoritative in the DB. Re-registering a retained soft-deleted row
        // intentionally does not clear `is_deleted`, so default active reads
        // must cross-check the DB before exposing memory entries.
        let env = resolve_env();
        let active_bot_ids = match self
            .db_query(
                "SELECT bot_uuid FROM bcs_bots WHERE env = ? AND COALESCE(is_deleted, 0) = 0",
                vec![Value::from(env.as_str())],
            )
            .await
        {
            Ok(rows) => rows
                .into_iter()
                .filter_map(|row| db_get_column::<String>(&row, "bot_uuid").ok())
                .collect::<std::collections::HashSet<_>>(),
            Err(error) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, env = %env, error = %error, "list_active: failed to load active bot ids from DB; returning empty result to preserve DB tombstone authority");
                return Vec::new();
            }
        };

        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| active_bot_ids.contains(&b.bot_uuid))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn list_all_bots(&self) -> Vec<RegisteredBot> {
        let env = resolve_env();
        let sql = "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, env, created_by FROM bcs_bots WHERE env = ? AND COALESCE(is_deleted, 0) = 0 AND COALESCE(actor_kind, 'bot') = 'bot' ORDER BY gmt_create DESC, bot_uuid ASC";
        let rows = match self
            .db_query(sql, vec![Value::from(env.as_str())])
            .await
        {
            Ok(rows) => rows,
            Err(error) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, env = %env, error = %error, "list_all_bots: failed to load bot rows from DB; returning empty result");
                return Vec::new();
            }
        };

        rows
            .iter()
            .filter_map(|row| {
                let bot_uuid: String = db_get_column_opt(row, "bot_uuid").ok().flatten()?;
                let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
                let env: Option<String> = db_get_column_opt(row, "env").ok().flatten();
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "protected".to_string());
                let created_by: Option<String> = db_get_column_opt(row, "created_by").ok().flatten();
                let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                    .ok()
                    .flatten()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default();

                let status_str: String = db_get_column_opt(row, "status")
                    .ok()
                    .flatten()
                    .filter(|v: &String| !v.is_empty())
                    .unwrap_or_else(|| "online".to_string());
                let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                    .ok()
                    .flatten()
                    .filter(|v: &String| !v.is_empty())
                    .unwrap_or_else(|| "bot".to_string());

                let actor_kind = match actor_kind_str.as_str() {
                    "human" => bcs_service_api::ActorKind::Human,
                    _ => bcs_service_api::ActorKind::Bot,
                };
                let status = match status_str.as_str() {
                    "hidden" => bcs_service_api::ActorStatus::Hidden,
                    _ => bcs_service_api::ActorStatus::Online,
                };
                let hidden = status_str == "hidden";

                Some(RegisteredBot {
                    bot_uuid,
                    capabilities: BotCapabilities {
                        name,
                        summary: bot_info.summary,
                        domains: bot_info.domains,
                        skills: bot_info.skills,
                        scopes: bot_info.scopes,
                        binding_channels: bot_info.binding_channels,
                        hidden,
                        visibility,
                        agent_code: None,
                        agent_token: None,
                    },
                    env,
                    created_by,
                    actor_kind,
                    status,
                })
            })
            .collect()
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        let current_env = resolve_env();
        // D-F: unified DB query — always query the database to include offline bots.
        // User-facing online status is computed from runtime connectivity at
        // the application layer, not persisted heartbeat payloads.
        match self
            .list_bots_by_creator_from_db(created_by, &current_env)
            .await
        {
            Ok(bots) => bots,
            Err(error) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, created_by = %created_by, error = %error, "list_bots_by_creator_from_db: failed");
                Vec::new()
            }
        }
    }

    async fn list_uninitialized_bots_by_creator(
        &self,
        created_by: &str,
        limit: usize,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let current_env = resolve_env();
        // Propagate (never swallow): the mine sweep must
        // distinguish "no candidates" from "store read failed".
        self.list_uninitialized_bots_by_creator_from_db(created_by, &current_env, limit)
            .await
    }

    async fn try_list_bots_by_creator(
        &self,
        created_by: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let current_env = resolve_env();
        self.list_bots_by_creator_from_db(created_by, &current_env)
            .await
    }

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        let query_lower = query.to_lowercase();

        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| {
                b.capabilities
                    .name
                    .as_ref()
                    .map(|n| n.to_lowercase().contains(&query_lower))
                    .unwrap_or(false)
                    || b.capabilities
                        .summary
                        .as_ref()
                        .map(|s| s.to_lowercase().contains(&query_lower))
                        .unwrap_or(false)
                    || b.capabilities
                        .domains
                        .iter()
                        .any(|d| d.to_lowercase().contains(&query_lower))
                    || b.capabilities
                        .skills
                        .iter()
                        .any(|s| s.name.to_lowercase().contains(&query_lower))
                    || b.capabilities
                        .scopes
                        .iter()
                        .any(|s| s.to_lowercase().contains(&query_lower))
                    || b.bot_uuid.to_lowercase().contains(&query_lower)
            })
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| skills.iter().all(|s| b.has_skill(s)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| domains.iter().all(|d| b.has_domain(d)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| scopes.iter().all(|s| b.has_scope(s)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn list_bots_by_name_and_cooperatable_with(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        _friend_uuids: &std::collections::HashSet<String>,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        self.list_bots_by_name_and_cooperatable_with_impl(
            name,
            bot_uuid,
            cooperatable_only,
            offset,
            limit,
        )
        .await
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.soft_delete(bot_id).await
    }

    async fn soft_delete(&self, bot_id: &str) -> bool {
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

    async fn cleanup_expired(&self) {
        let mut bots = self.bots.write().await;
        let before = bots.len();
        bots.retain(|_, b| !b.is_expired());
        let removed = before - bots.len();
        if removed > 0 {
            warn!(request_id = %bcs_observability::CurrentRequestId, removed, "Removed expired bot registrations");
        }
    }

    // ===== Persistence =====

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        self.load_from_db(bot_id, false).await.map(
            |(mut caps, _env, _hidden, _created_by, _actor_kind, _status)| {
                // agent_token 是运行时敏感字段，只存内存，不从 DB 恢复
                caps.agent_token = None;
                caps
            },
        )
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
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

    async fn update_profile_existing(
        &self,
        bot_id: &str,
        update: bcs_service_api::types::bot_profile::ExistingProfileUpdate,
    ) -> ServiceResult<bcs_service_api::types::bot_profile::ProfileWrite> {
        self.update_profile_existing_impl(bot_id, update).await
    }

    async fn update_avatar_existing(
        &self,
        bot_id: &str,
        avatar: bcs_service_api::types::bot_profile::AvatarChange,
    ) -> ServiceResult<bcs_service_api::types::bot_profile::ProfileWrite> {
        self.update_avatar_existing_impl(bot_id, avatar).await
    }

    async fn read_avatar_batch(
        &self,
        bot_ids: &[String],
    ) -> ServiceResult<bcs_service_api::types::bot_profile::BotAvatarMap> {
        self.read_avatar_batch_impl(bot_ids).await
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
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

    /// DEPRECATED (Rev-4 / Human Actor V1): the hidden mechanism is replaced by
    /// `bcs_bots.status` (`online` / `hidden`). This impl is now a Noop with a
    /// WARN log so legacy callers keep compiling but no longer touch any storage.
    /// Use [`update_actor_status`](Self::update_actor_status) instead. (Task H.1)
    #[allow(deprecated)]
    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()> {
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
    async fn update_actor_status(
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

    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<bcs_service_api::EnsureHumanResult> {
        self.ensure_human_actor_impl(staff_no, nick_name).await
    }

    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        self.list_legacy_bots_for_owner_impl(staff_no, env).await
    }

    async fn update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
        self.update_human_name_impl(staff_no, new_name).await
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.exists_in_db(bot_id).await
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        self.save_created_by_impl(bot_id, created_by, overwrite).await
    }

    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        self.save_token_impl(bot_id, token).await
    }

    async fn load_token(&self, bot_id: &str) -> Option<String> {
        self.load_token_impl(bot_id).await
    }

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
        self.find_bot_by_token_impl(token).await
    }

    async fn query_agent_bots(
        &self,
        query: &bcs_service_api::types::agent_registration::AgentBotQuery,
    ) -> ServiceResult<Vec<bcs_service_api::types::agent_registration::AgentBotRecord>> {
        self.load_agent_bots(query).await
    }

    async fn lookup_bot_token(
        &self,
        token: &str,
        actor: Option<&str>,
    ) -> ServiceResult<Option<bcs_service_api::types::agent_registration::AgentBotRecord>> {
        self.load_token_bot(token, actor).await
    }

    async fn find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        self.find_bot_by_agent_code_impl(agent_code).await
    }

    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        self.find_bot_by_binding_channel_impl(channel, binding_key).await
    }

    // ===== Streaming Connection Management =====

    async fn connect_or_promote_streaming(
        &self,
        bot_id: String,
    ) -> Result<String, ConnectStreamError> {
        self.connect_or_promote_streaming_impl(bot_id).await
    }

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
        self.register_streaming_connection_impl(bot_id).await
    }

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        self.reconnect_streaming_impl(existing_token).await
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        self.disconnect_streaming_impl(bot_id).await
    }

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.is_connected_impl(bot_id).await
    }

    async fn send_frame(&self, bot_id: &str, _frame: String) -> Result<(), ()> {
        self.send_frame_impl(bot_id, _frame).await
    }

    async fn list_connected(&self) -> Vec<String> {
        self.list_connected_impl().await
    }

    async fn store_token_mapping(&self, token: String, bot_id: String) {
        self.store_token_mapping_impl(token, bot_id).await
    }

    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        self.get_protocol_version_impl(bot_id).await
    }

    async fn set_protocol_version(&self, bot_id: &str, version: u32) {
        self.set_protocol_version_impl(bot_id, version).await
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String {
        self.register_http_connection_impl(bot_id, token).await
    }

    async fn send_request(
        &self,
        bot_id: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        self.send_request_impl(bot_id, method, params, timeout_ms).await
    }

    async fn resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        self.resolve_pending_request_impl(request_id, response).await
    }
}
