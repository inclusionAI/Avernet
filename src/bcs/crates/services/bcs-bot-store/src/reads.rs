use super::*;

impl PersistentBotRepo {
    pub(super) async fn repo_get_by_ids(&self, bot_ids: &[String]) -> Vec<RegisteredBot> {
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

    pub(super) async fn repo_get(&self, bot_id: &str) -> Option<RegisteredBot> {
        match bcs_observability::observe_result("bot.load", self.try_get(bot_id)).await {
            Ok(value) => value,
            Err(_) => {
                warn!(target: "bcs_observation", request_id = %bcs_observability::current_request_id(), outcome = "load_error", fallback = "omitted", "bot.load.fallback");
                None
            }
        }
    }

    pub(super) async fn repo_try_get(&self, bot_id: &str) -> ServiceResult<Option<RegisteredBot>> {
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
    pub(super) async fn repo_get_including_deleted(&self, bot_id: &str) -> Option<RegisteredBot> {
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

    pub(super) async fn repo_get_agent_credentials(
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

    pub(super) async fn repo_add_bot_info(&self, bot_id: &str, key: &str, value: String) {
        // 目前仅支持 "agent_token"（复用 capabilities.agent_token 存储，仅内存）。
        // 后期需要其他字段时，应在 RegisteredBotInner 上新增一个 HashMap 内存对象
        // 来承载任意 key/value，而不是继续往 capabilities 上加字段。
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

    pub(super) async fn repo_get_bot_info(&self, bot_id: &str, key: &str) -> Option<String> {
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

    pub(super) async fn repo_list_active(&self) -> Vec<RegisteredBot> {
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

    pub(super) async fn repo_list_all_bots(&self) -> Vec<RegisteredBot> {
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

    pub(super) async fn repo_list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
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

    pub(super) async fn repo_try_list_bots_by_creator(
        &self,
        created_by: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let current_env = resolve_env();
        self.list_bots_by_creator_from_db(created_by, &current_env)
            .await
    }

    pub(super) async fn repo_discover(&self, query: &str) -> Vec<RegisteredBot> {
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

    pub(super) async fn repo_find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| skills.iter().all(|s| b.has_skill(s)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| domains.iter().all(|d| b.has_domain(d)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| scopes.iter().all(|s| b.has_scope(s)))
            .map(|b| b.to_registered_bot())
            .collect()
    }

    pub(super) async fn repo_list_bots_by_name_and_cooperatable_with(
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
}
