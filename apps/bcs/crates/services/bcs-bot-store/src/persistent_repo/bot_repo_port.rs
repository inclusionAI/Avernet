//! `BotRepoPort` implementation for `PersistentBotRepo`.
//!
//! Moved verbatim from `lib.rs` during the crate split.

use std::time::{Duration, Instant};

use async_trait::async_trait;
use tokio::sync::oneshot;
use tracing::{debug, info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{
    DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_service_api::{
    BotCapabilities, ConnectStreamError, RegisteredBot, ServiceError, ServiceResult,
    is_mock_token,
};

use bcs_service_api::port::repo::BotRepoPort;
use super::PersistentBotRepo;
use crate::{BotConnection, BotInfo, RegisteredBotInner, is_legacy_namespace, log_bot_cache_source};

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
        self.initialize_owned_bot(bot_id, owner_user_id, &mutation)
            .await
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
                    protocol_version: 1,
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
                        protocol_version: 1,
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
            if !bot.is_expired() {
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
                    Some(bot) if !bot.is_expired() => {
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
            if !bot.is_expired() {
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

    /// Ensure a Human Actor row exists in `bcs_bots` — Task O.3.
    ///
    /// Idempotent INSERT IGNORE: if `bot_uuid = "human_{staff_no}"` already
    /// exists, nothing is done (in particular, `name` is preserved per
    /// Requirement 3.1#4).
    ///
    /// On first INSERT, writes:
    /// - `bot_uuid = "human_{staff_no}"`
    /// - `actor_kind = 'human'`
    /// - `status = 'online'`
    /// - `visibility = 'protected'`
    /// - `name = nick_name`
    /// - `session_token = UUID v4`
    /// - `created_by = staff_no`
    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<bcs_service_api::EnsureHumanResult> {
        let env = resolve_env();
        let bot_uuid = format!("human_{}", staff_no);
        let default_summary = "写点什么介绍自己";

        // Fast path: already present → backfill missing fields, then return.
        if self.exists_in_db(&bot_uuid).await {
            // Deserialize current bot_info into BotInfo struct (missing fields get defaults).
            let existing_json = self
                .read_bot_info_json(&bot_uuid)
                .await
                .unwrap_or_else(|| serde_json::json!({}));
            let mut bot_info: BotInfo =
                serde_json::from_value(existing_json.clone()).unwrap_or_default();

            // Check if summary needs backfill.
            let needs_summary = bot_info.summary.as_deref().map_or(true, |s| s.is_empty());
            if needs_summary {
                bot_info.summary = Some(default_summary.to_string());
            }

            // Re-serialize and compare to detect if anything actually changed.
            let merged_str = serde_json::to_string(&bot_info).unwrap_or_default();
            let needs_backfill = merged_str != existing_json.to_string();

            if needs_backfill {
                let sql = "UPDATE bcs_bots SET bot_info = ? WHERE bot_uuid = ? AND env = ?";
                self.db_execute_affected(
                    sql,
                    vec![
                        Value::from(merged_str.as_str()),
                        Value::from(bot_uuid.as_str()),
                        Value::from(env.as_str()),
                    ],
                )
                .await
                .map_err(|e| {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        bot_uuid = %bot_uuid,
                        error = %e,
                        "ensure_human_actor: failed to backfill bot_info"
                    );
                    ServiceError::InternalError(format!(
                        "ensure_human_actor: failed to backfill bot_info for {}: {}",
                        bot_uuid, e
                    ))
                })?;

                // Sync in-memory cache so subsequent reads (e.g. /bots/query)
                // reflect the updated fields without waiting for a full reload.
                {
                    let mut bots = self.bots.write().await;
                    if let Some(bot) = bots.get_mut(&bot_uuid) {
                        if needs_summary {
                            bot.capabilities.summary = Some(default_summary.to_string());
                        }
                    }
                }

                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor: backfilled missing bot_info fields"
                );
            } else {
                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor: row already exists, preserving existing fields"
                );
            }
            return Ok(bcs_service_api::EnsureHumanResult { created: false });
        }

        let session_token = uuid::Uuid::new_v4().to_string();
        let initial_bot_info = BotInfo {
            summary: Some(default_summary.to_string()),
            ..Default::default()
        };
        let bot_info_str = serde_json::to_string(&initial_bot_info).unwrap_or_default();
        let visibility = "protected";
        let actor_kind = "human";
        let status = "online";

        let sql = format!(
            "{} INTO bcs_bots \
             (bot_uuid, actor_kind, name, bot_info, session_token, created_by, visibility, status, env, registered_at, updated_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            self.flavor.insert_or_ignore()
        );
        let affected = self
            .db_execute_affected(
                &sql,
                vec![
                    Value::from(bot_uuid.as_str()),
                    Value::from(actor_kind),
                    Value::from(nick_name),
                    Value::from(bot_info_str.as_str()),
                    Value::from(session_token.as_str()),
                    Value::from(staff_no),
                    Value::from(visibility),
                    Value::from(status),
                    Value::from(env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_uuid = %bot_uuid,
                    error = %e,
                    "ensure_human_actor: INSERT IGNORE failed"
                );
                ServiceError::InternalError(e.to_string())
            })?;

        let created = affected > 0;
        info!(
            bot_uuid = %bot_uuid,
            staff_no = %staff_no,
            nick_name = %nick_name,
            env = %env,
            created = %created,
            "ensure_human_actor: INSERT IGNORE completed"
        );
        Ok(bcs_service_api::EnsureHumanResult { created })
    }

    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        // Rule (a): bots with `created_by = staff_no`
        // Rule (b): bots with `created_by IS NULL` whose `bot_uuid` ends with `:{staff_no}`
        //           (filtered in Rust by `is_legacy_namespace` whitelist)
        let like_pattern = format!("%:{}", staff_no);
        let sql = "SELECT bot_uuid, actor_kind, name, bot_info, session_token, \
                   visibility, status, created_by \
                   FROM bcs_bots \
                   WHERE env = ? AND actor_kind = 'bot' AND ( \
                       created_by = ? \
                       OR (created_by IS NULL AND bot_uuid LIKE ?) \
                   ) AND COALESCE(is_deleted, 0) = 0 \
                   ORDER BY gmt_create DESC";

        let rows = self
            .db_query(
                sql,
                vec![
                    Value::from(env),
                    Value::from(staff_no),
                    Value::from(like_pattern.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    staff_no = %staff_no,
                    env = %env,
                    error = %e,
                    "list_legacy_bots_for_owner: query failed"
                );
                ServiceError::InternalError(e.to_string())
            })?;

        let results: Vec<RegisteredBot> = rows
            .iter()
            .filter_map(|row| {
                let bot_uuid: String = db_get_column_opt(row, "bot_uuid").ok().flatten()?;
                let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "bot".to_string());
                if actor_kind_str != "bot" {
                    return None;
                }
                let created_by: Option<String> =
                    db_get_column_opt(row, "created_by").ok().flatten();

                // For rule (b): apply whitelist filter
                if created_by.is_none() && !is_legacy_namespace(&bot_uuid, staff_no) {
                    return None;
                }

                let actor_kind = bcs_service_api::ActorKind::Bot;
                let status_str: String = db_get_column_opt(row, "status")
                    .ok()
                    .flatten()
                    .filter(|v: &String| !v.is_empty())
                    .unwrap_or_else(|| "online".to_string());
                let hidden = status_str == "hidden";
                let status = match status_str.as_str() {
                    "hidden" => bcs_service_api::ActorStatus::Hidden,
                    _ => bcs_service_api::ActorStatus::Online,
                };
                let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                    .ok()
                    .flatten()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default();
                let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "private".to_string());

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
                        // SECURITY: 敏感字段置空，防止通过常规接口泄露
                        agent_code: None,
                        agent_token: None,
                    },
                    env: Some(env.to_string()),
                    created_by,
                    actor_kind,
                    status,
                })
            })
            .collect();

        info!(
            staff_no = %staff_no,
            env = %env,
            count = results.len(),
            "list_legacy_bots_for_owner: query completed"
        );
        Ok(results)
    }

    /// Repair a Human actor's `name` column — used by the `/debug/whoami`
    /// debug endpoint to backfill the real `nick_name` after onboard fell
    /// back to writing `staff_no` (because the auth SDK didn't return
    /// `nick_name` at the time).
    async fn update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
        let env = resolve_env();
        let bot_uuid = format!("human_{}", staff_no);

        let sql = "UPDATE bcs_bots SET name = ? WHERE bot_uuid = ? AND env = ?";
        self.db_execute_affected(
            sql,
            vec![
                Value::from(new_name),
                Value::from(bot_uuid.as_str()),
                Value::from(env.as_str()),
            ],
        )
        .await
        .map_err(|e| {
            warn!(
                request_id = %bcs_observability::CurrentRequestId,
                bot_uuid = %bot_uuid,
                error = %e,
                "update_human_name: failed to update database"
            );
            ServiceError::InternalError(e.to_string())
        })?;

        // Update in-memory mirror if the row is cached.
        {
            let mut bots = self.bots.write().await;
            if let Some(existing) = bots.get_mut(&bot_uuid) {
                existing.capabilities.name = Some(new_name.to_string());
            }
        }

        info!(
            bot_uuid = %bot_uuid,
            staff_no = %staff_no,
            new_name = %new_name,
            env = %env,
            "update_human_name: human actor name updated"
        );
        Ok(())
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

    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        self.save_token_to_db(bot_id, token).await
    }

    async fn load_token(&self, bot_id: &str) -> Option<String> {
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

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
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

    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        let index = self.binding_channel_index.read().await;
        index
            .get(&(channel.to_string(), binding_key.to_string()))
            .cloned()
    }

    // ===== Streaming Connection Management =====

    async fn connect_or_promote_streaming(
        &self,
        bot_id: String,
    ) -> Result<String, ConnectStreamError> {
        // Decide the branch from the registry's authoritative current token
        // (memory → DB fallback via load_token) and existence (memory or DB),
        // before taking the write lock, so a DB-only bot after a BCS restart is
        // still located and a previously-registered bot (even with no token) is
        // refused rather than silently overwritten.
        let existing_token = self.load_token(&bot_id).await;
        let is_mock = existing_token.as_deref().map(is_mock_token).unwrap_or(false);
        let bot_exists = {
            let in_mem = self.bots.read().await.get(&bot_id).is_some();
            in_mem || existing_token.is_some() || self.exists_in_db(&bot_id).await
        };
        // ws_connection presence and connected state are only meaningful in
        // memory; a DB-only entry has no live connection.
        let in_memory_connected = {
            let bots = self.bots.read().await;
            bots.get(&bot_id)
                .map(|bot| bot.ws_connection.is_some())
                .unwrap_or(false)
        };
        let token_preview = |t: &str| format!("{}...", &t[..t.len().min(4)]);

        match (bot_exists, is_mock, in_memory_connected) {
            // truly new (no record anywhere) → create with a real token
            (false, _, _) => {
                let session_token = uuid::Uuid::new_v4().to_string();
                let mut bots = self.bots.write().await;
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
                        protocol_version: 1,
                        session_token: Some(session_token.clone()),
                        env: Some(resolve_env()),
                        hidden: false,
                        created_by: None,
                    },
                );
                self.token_to_bot
                    .write()
                    .await
                    .insert(session_token.clone(), bot_id.clone());
                info!(
                    bot_id = %bot_id,
                    branch = "create",
                    token_preview = %token_preview(&session_token),
                    "register_streaming_connection: create"
                );
                Ok(session_token)
            }
            // pre-registered plugin bot, not yet attached → promote MOCK → real
            (true, true, _) => {
                let previous_mock = existing_token.clone();
                let session_token = uuid::Uuid::new_v4().to_string();
                // Persist FIRST: if this fails, surface the error before any
                // in-memory mutation, so memory and DB never split into a
                // "real-token in memory, MOCK in DB" half-state that only
                // surfaces as a stale-MOCK reconnect after a BCS restart.
                if let Err(err) = self.save_token_to_db(&bot_id, &session_token).await {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        bot_id = %bot_id,
                        error = %err,
                        "connect_or_promote_streaming: promote_mock DB persist failed; refusing ws"
                    );
                    return Err(ConnectStreamError::InternalError(format!(
                        "promote_mock: failed to persist promoted token: {err}"
                    )));
                }
                let mut bots = self.bots.write().await;
                if let Some(bot) = bots.get_mut(&bot_id) {
                    bot.ws_connection = Some(BotConnection {
                        session_token: session_token.clone(),
                        connected_at: Instant::now(),
                    });
                    bot.session_token = Some(session_token.clone());
                    bot.last_heartbeat = Instant::now();
                } else {
                    // DB-only after restart: hydrate the entry with the new real
                    // token so subsequent connects see a real-token bot.
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
                            protocol_version: 1,
                            session_token: Some(session_token.clone()),
                            env: Some(resolve_env()),
                            hidden: false,
                            created_by: None,
                        },
                    );
                }
                let mut token_to_bot = self.token_to_bot.write().await;
                if let Some(prev) = previous_mock {
                    token_to_bot.remove(&prev);
                }
                token_to_bot.insert(session_token.clone(), bot_id.clone());
                info!(
                    bot_id = %bot_id,
                    branch = "promote_mock",
                    previous_token_kind = "mock",
                    token_preview = %token_preview(&session_token),
                    "register_streaming_connection: promote_mock"
                );
                Ok(session_token)
            }
            // real token, already connected → reject
            (true, false, true) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id = %bot_id,
                    branch = "already_connected",
                    "connect_or_promote_streaming: real-token bot already connected"
                );
                Err(ConnectStreamError::AlreadyConnected(bot_id))
            }
            // real token, not connected → anti-hijack: refuse an empty/stale-token
            // claim of a real-token bot. (Reconnect by the real token still works
            // via the existing reconnect_streaming(token) path.)
            (true, false, false) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    bot_id = %bot_id,
                    branch = "already_registered",
                    "connect_or_promote_streaming: refusing empty/stale-token claim of real-token bot"
                );
                Err(ConnectStreamError::AlreadyRegistered(bot_id))
            }
        }
    }

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
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
                    protocol_version: 1,
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

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        let token_preview = format!("{}...", &existing_token[..existing_token.len().min(4)]);
        info!(token_preview = %token_preview, "reconnect_streaming: attempting reconnect");

        // Find bot by token (memory -> database)
        let bot_id = self.find_bot_by_token(&existing_token).await.ok_or(())?;

        let token_preview = format!("{}...", &existing_token[..existing_token.len().min(4)]);
        info!(bot_id = %bot_id, token_preview = %token_preview, "reconnect_streaming: found bot by token");

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
                    protocol_version: 1,
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

    async fn disconnect_streaming(&self, bot_id: &str) {
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

    async fn is_connected(&self, bot_id: &str) -> bool {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .map(|b| b.ws_connection.is_some())
            .unwrap_or(false)
    }

    async fn send_frame(&self, bot_id: &str, _frame: String) -> Result<(), ()> {
        warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, "Bot frame delivery is owned by the ws adapter");
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        let bots = self.bots.read().await;
        bots.iter()
            .filter(|(_, b)| b.ws_connection.is_some())
            .map(|(id, _)| id.clone())
            .collect()
    }

    async fn store_token_mapping(&self, token: String, bot_id: String) {
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.insert(token.clone(), bot_id.clone());
        debug!(bot_id = %bot_id, token = %token, "Token mapping stored");
    }

    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .map(|bot| bot.protocol_version)
            .unwrap_or(1)
    }

    async fn set_protocol_version(&self, bot_id: &str, version: u32) {
        let mut bots = self.bots.write().await;
        if let Some(bot) = bots.get_mut(bot_id) {
            bot.protocol_version = version;
        }
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String {
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
                        protocol_version: 1,
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
        self.store_token_mapping(token.clone(), bot_id.clone())
            .await;
        token
    }

    async fn send_request(
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

    async fn resolve_pending_request(&self, request_id: &str, response: serde_json::Value) {
        let mut pending = self.pending_requests.write().await;
        if let Some(tx) = pending.remove(request_id) {
            let _ = tx.send(response);
        }
    }
}
