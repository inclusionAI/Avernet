use async_trait::async_trait;

use crate::types::{
    ActorStatus, AgentCredentials, BotCapabilities, BotDynamicStatus, EnsureHumanResult,
    RegisteredBot, ServiceResult,
};

/// Transitional repository contract for bot registry state implementations.
///
/// This is an outbound port for `BotCore`. It currently includes both
/// persistence primitives and process-local runtime connection primitives
/// because the legacy registry stored both concerns behind one boundary.
/// Implementations live in store crates and own DB/cache/file/memory details.
/// Follow-up refactors should split runtime connection/session state into a
/// separate port before treating this as a narrow persistence-only repository.
#[async_trait]
pub trait BotRepoPort: Send + Sync {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()>;

    async fn register_with_owner_and_token(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        let bot_id_for_followup = bot_id.clone();
        self.register(bot_id, capabilities).await?;
        self.save_created_by(&bot_id_for_followup, created_by, false)
            .await?;
        self.save_token(&bot_id_for_followup, token).await
    }

    async fn update_status(&self, bot_id: &str, status: BotDynamicStatus) -> bool;
    async fn get(&self, bot_id: &str) -> Option<RegisteredBot>;

    /// Like [`get`](Self::get) but also returns soft-deleted bots.
    ///
    /// Default implementation delegates to `get` (which excludes deleted bots),
    /// returning `None` for deleted bots; persistent stores override this to
    /// read the retained (soft-deleted) row so display-only callers can still
    /// resolve a removed bot's name snapshot.
    async fn get_including_deleted(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.get(bot_id).await
    }
    async fn get_agent_credentials(&self, bot_id: &str) -> Option<AgentCredentials>;

    /// Set an in-memory extension field on a bot record by key.
    ///
    /// This is a process-local, non-persisted side channel for runtime
    /// attributes that do not belong in the persisted capabilities.
    ///
    /// 目前仅支持 `"agent_token"` 这一个 key（复用 `capabilities.agent_token`
    /// 存储）。后期若需要支持其他字段，应在 bot 记录上新增一个内存 HashMap
    /// 对象来承载任意 key/value，而不是继续往 capabilities 上加字段。
    async fn add_bot_info(&self, _bot_id: &str, _key: &str, _value: String) {}

    /// Read an in-memory extension field set via [`add_bot_info`](Self::add_bot_info).
    async fn get_bot_info(&self, _bot_id: &str, _key: &str) -> Option<String> {
        None
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

    async fn list_active(&self) -> Vec<RegisteredBot>;
    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot>;
    async fn discover(&self, query: &str) -> Vec<RegisteredBot>;
    async fn find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot>;
    async fn find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot>;
    async fn find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot>;

    async fn find_by_name(&self, name: &str) -> Vec<RegisteredBot> {
        let lower = name.to_lowercase();
        self.list_active()
            .await
            .into_iter()
            .filter(|b| {
                b.capabilities
                    .name
                    .as_ref()
                    .is_some_and(|n| n.to_lowercase().contains(&lower))
            })
            .collect()
    }

    async fn list_all_bots(&self) -> Vec<RegisteredBot> {
        self.list_active().await
    }

    async fn list_bots_by_name_and_cooperatable_with(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        friend_uuids: &std::collections::HashSet<String>,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        let bots = self.list_all_bots().await;
        let name_lower = name.to_lowercase();

        let filtered: Vec<(RegisteredBot, bool)> = bots
            .into_iter()
            .filter(|b| b.bot_uuid != bot_uuid)
            .filter(|b| {
                if !name.is_empty() {
                    b.capabilities
                        .name
                        .as_ref()
                        .map(|n| n.to_lowercase().contains(&name_lower))
                        .unwrap_or(false)
                } else {
                    true
                }
            })
            .filter_map(|b| {
                let is_friend = friend_uuids.contains(&b.bot_uuid);
                let vis = b.capabilities.visibility.as_str();
                if cooperatable_only {
                    if vis == "public" || is_friend {
                        Some((b, is_friend))
                    } else {
                        None
                    }
                } else if vis == "public" || vis == "protected" {
                    Some((b, is_friend))
                } else {
                    None
                }
            })
            .collect();

        let total = filtered.len();
        let page = filtered.into_iter().skip(offset).take(limit).collect();

        (page, total)
    }

    async fn unregister(&self, bot_id: &str) -> bool;

    /// Soft-delete a bot from default registry/query/token lookup paths.
    ///
    /// Implementations that do not have a separate soft-delete marker may
    /// remove process-local state, but persistent DB-backed implementations
    /// should mark `bcs_bots.is_deleted = 1` instead of deleting the row.
    async fn soft_delete(&self, bot_id: &str) -> bool {
        self.unregister(bot_id).await
    }

    async fn cleanup_expired(&self);
    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities>;
    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()>;
    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()>;
    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()>;

    async fn update_actor_status(&self, bot_id: &str, status: ActorStatus) -> ServiceResult<()> {
        let _ = (bot_id, status);
        Ok(())
    }

    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        let _ = (staff_no, nick_name);
        Ok(EnsureHumanResult { created: false })
    }

    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let _ = (staff_no, env);
        Ok(vec![])
    }

    async fn update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
        let _ = (staff_no, new_name);
        Ok(())
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool;
    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()>;
    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()>;
    async fn load_token(&self, bot_id: &str) -> Option<String>;
    async fn find_bot_by_token(&self, token: &str) -> Option<String>;

    /// Find a bot by its dedicated `agent_code` column. Returns `bot_uuid` if
    /// found. Auth plugins can use this to resolve provider-registered bots
    /// whose `agent_code` was set to their `provider_bot_ref`.
    ///
    /// Default impl returns `None` so test/noop repos need not implement it.
    async fn find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        let _ = agent_code;
        None
    }

    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        let _ = (channel, binding_key);
        None
    }

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()>;
    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()>;
    async fn disconnect_streaming(&self, bot_id: &str);
    async fn is_connected(&self, bot_id: &str) -> bool;

    async fn is_effectively_online(&self, bot_id: &str) -> bool {
        if !self.is_connected(bot_id).await {
            return false;
        }
        match self.get(bot_id).await {
            Some(bot) => bot.status == ActorStatus::Online,
            None => false,
        }
    }

    async fn send_frame(&self, bot_id: &str, frame: String) -> Result<(), ()>;

    async fn send_request(
        &self,
        bot_id: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let _ = (bot_id, method, params, timeout_ms);
        Err("send_request not implemented".to_string())
    }

    async fn resolve_pending_request(&self, _request_id: &str, _response: serde_json::Value) {}

    async fn list_connected(&self) -> Vec<String>;
    async fn store_token_mapping(&self, token: String, bot_id: String);

    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        let _ = bot_id;
        1
    }

    async fn set_protocol_version(&self, bot_id: &str, version: u32) {
        let _ = (bot_id, version);
    }

    async fn register_http_connection(&self, bot_id: String, token: String) -> String;
}
