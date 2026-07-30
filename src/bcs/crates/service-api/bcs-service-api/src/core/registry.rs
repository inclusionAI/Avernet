use async_trait::async_trait;

use super::{ActorStatus, EnsureHumanResult, ServiceError, ServiceResult};
use bcs_domain::Group;

pub use bcs_domain::registry::deserialize_skills;
pub use bcs_domain::{
    AgentCredentials, BindingChannel, BindingChannels, BotCapabilities, BotConnectParams,
    BotConnectResult, BotDeliveryTarget, BotDynamicStatus, ConnectionKind, CoordinationSurface,
    DynamicStatusResponse, RegisteredBot, Skill,
};

// ---------------------------------------------------------------------------
// Error type kept in the service-api layer; downstream adapters/services depend
// on this enum directly.
// ---------------------------------------------------------------------------

/// Error type for bot connection operations.
#[derive(Debug, Clone, thiserror::Error)]
pub enum ConnectError {
    /// Bot ID is already connected.
    #[error("Bot '{0}' is already connected")]
    AlreadyConnected(String),
    /// Bot ID is already registered.
    #[error("Bot '{0}' is already registered")]
    AlreadyRegistered(String),
    /// Invalid bot ID.
    #[error("Bot ID cannot be empty")]
    InvalidBotId,
    /// Invalid token.
    #[error("Invalid token")]
    InvalidToken,
    /// Internal error.
    #[error("Internal error: {0}")]
    InternalError(String),
}

// ============================================================================
// Service Traits
// ============================================================================

/// Service for bot registration and discovery.
#[async_trait]
pub trait BotRegistryCoreService: Send + Sync {
    /// Register or update a bot.
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()>;

    /// Register a bot with immutable owner and runtime token in one core operation.
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

    /// Update a bot's dynamic status.
    async fn update_status(&self, bot_id: &str, status: BotDynamicStatus) -> bool;

    /// Get a bot's registration info.
    /// This method excludes `agent_code` so delivery contracts must opt in to
    /// exposing that routing identifier, and excludes the sensitive `agent_token` credential.
    async fn get(&self, bot_id: &str) -> Option<RegisteredBot>;

    /// Get registration info without hiding persistence failures.
    ///
    /// Existing implementations retain their compatibility behavior through
    /// this default. Fallible core/store implementations should override it.
    async fn try_get(&self, bot_id: &str) -> ServiceResult<Option<RegisteredBot>> {
        Ok(self.get(bot_id).await)
    }

    /// Like [`get`](Self::get) but also returns soft-deleted bots.
    ///
    /// Used for display-only enrichment (e.g. filling in a removed bot's name
    /// in group participant listings). Default implementation delegates to
    /// `get`, so deleted bots resolve to `None` unless the implementation
    /// overrides this to read the retained row.
    async fn get_including_deleted(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.get(bot_id).await
    }

    /// Get a bot's routing identifier (`agent_code`) and sensitive credential (`agent_token`).
    /// This is a separate method so callers explicitly select the fields they need and do not
    /// leak `agent_token` through regular bot responses.
    /// Used by AI Security Gateway for message validation.
    async fn get_agent_credentials(&self, bot_id: &str) -> Option<AgentCredentials>;

    /// Set an in-memory extension field on a bot record by key.
    /// Process-local, non-persisted.
    ///
    /// 目前仅支持 `"agent_token"` 这一个 key。后期若需要支持其他字段，
    /// 应在 bot 记录上新增一个内存 HashMap 对象来承载任意 key/value。
    async fn add_bot_info(&self, _bot_id: &str, _key: &str, _value: String) {}

    /// Read an in-memory extension field set via [`add_bot_info`](Self::add_bot_info).
    async fn get_bot_info(&self, _bot_id: &str, _key: &str) -> Option<String> {
        None
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        let bot = self
            .get(bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(bot_id.to_string()))?;
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot.bot_uuid,
        })
    }

    async fn resolve_coordination_surface(
        &self,
        bot_id: &str,
    ) -> ServiceResult<CoordinationSurface> {
        let _ = self
            .get(bot_id)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(bot_id.to_string()))?;
        let client_kind = self
            .get_bot_info(bot_id, "client_kind")
            .await
            .map(|value| value.trim().to_ascii_lowercase());
        if client_kind.as_deref() == Some("plugin") {
            return Ok(CoordinationSurface::native_tool());
        }
        Ok(CoordinationSurface::legacy_upstream())
    }

    /// Batch query bots by their UUIDs.
    /// Returns only bots that exist in the registry.
    /// Results are deduplicated by bot_uuid.
    /// Does not filter by visibility — returns all matching bots regardless of visibility.
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

    /// List all active bots.
    async fn list_active(&self) -> Vec<RegisteredBot>;

    /// List active bots created by a specific user (staff_no).
    /// Filters by both `created_by` and current `env`.
    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot>;

    /// Discover bots by capability keywords.
    async fn discover(&self, query: &str) -> Vec<RegisteredBot>;

    /// Find bots by skills.
    async fn find_by_skills(&self, skills: &[&str]) -> Vec<RegisteredBot>;

    /// Find bots by domains.
    async fn find_by_domains(&self, domains: &[&str]) -> Vec<RegisteredBot>;

    /// Find bots by scopes.
    async fn find_by_scopes(&self, scopes: &[&str]) -> Vec<RegisteredBot>;

    /// Find bots by name (case-insensitive contains match).
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

    /// List all registered bots, including those that may have expired (offline).
    /// Default implementation falls back to `list_active()` for backward compatibility.
    /// In-memory registries should override this to return the full bot map.
    async fn list_all_bots(&self) -> Vec<RegisteredBot> {
        self.list_active().await
    }

    /// Query bots by name with pagination, filtered by cooperatability rules.
    ///
    /// - `name`: case-insensitive LIKE pattern. Empty string means "match all".
    /// - `bot_uuid`: the requesting bot's UUID, used in the DB CTE for friendship JOIN.
    ///   Ignored by the default in-memory impl (use `friend_uuids` instead).
    /// - `cooperatable_only`:
    ///   - `true`: return `public` bots + bots that are friends with `bot_uuid`.
    ///     Friends with `private` visibility ARE included.
    ///   - `false`: return `public` + `protected` bots only. `private` bots are excluded.
    /// - `friend_uuids`: pre-fetched friend UUID set from FriendCoreService. DB impl ignores this
    ///   (uses CTE), in-memory impl uses it to compute `is_friend` and filter.
    /// - Returns `(Vec<(RegisteredBot, bool)>, usize)` where `bool` = `is_friend`,
    ///   and `usize` = total count (before pagination, after visibility filtering).
    ///
    /// **Contract**: the `is_friend` field in the return value is authoritative. The caller
    /// (server handler) MUST NOT re-compute or override it for the data returned by this method.
    /// Only fuse results (which bypass this method) need friend lookup in the handler.
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
                } else {
                    if vis == "public" || vis == "protected" {
                        Some((b, is_friend))
                    } else {
                        None
                    }
                }
            })
            .collect();

        let total = filtered.len();
        let page: Vec<(RegisteredBot, bool)> =
            filtered.into_iter().skip(offset).take(limit).collect();

        (page, total)
    }

    /// Unregister a bot.
    async fn unregister(&self, bot_id: &str) -> bool;

    /// Soft-delete a bot from default registry/query/token lookup paths.
    async fn soft_delete(&self, bot_id: &str) -> bool {
        self.unregister(bot_id).await
    }

    /// Clean up expired registrations.
    async fn cleanup_expired(&self);

    /// Load capabilities from storage for a bot.
    /// Returns None if the bot has not been onboarded or file doesn't exist.
    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities>;

    /// Save capabilities to storage for a bot.
    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()>;

    /// Update only the visibility field for a bot, without touching other fields.
    /// This avoids the risk of overwriting name/summary with stale or None values
    /// when only a visibility change is needed.
    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()>;

    /// DEPRECATED: Hidden mechanism is deprecated. Use `visibility` field instead,
    /// and use [`update_actor_status`](Self::update_actor_status) for the
    /// Online/Hidden actor-level status (Requirement 3.16).
    ///
    /// V1 implementations SHOULD turn this into a Noop and emit a WARN log
    /// (see Task H.1).
    #[deprecated(
        since = "0.2.0",
        note = "Hidden mechanism removed in Rev-4 / Human Actor V1. \
                Use `update_actor_status(bot_id, ActorStatus::Hidden)` instead."
    )]
    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()>;

    /// Update the actor-level lifecycle status (`Online` / `Hidden`) for a bot
    /// or human actor (Requirement 3.16).
    ///
    /// Replaces the legacy [`set_hidden`](Self::set_hidden) mechanism. The new
    /// status is persisted to the `bcs_bots.status` column; in-memory state
    /// SHOULD be kept in sync.
    ///
    /// Default impl is a Noop so legacy implementations compile without
    /// changes; production implementations MUST override this.
    async fn update_actor_status(&self, bot_id: &str, status: ActorStatus) -> ServiceResult<()> {
        let _ = (bot_id, status);
        Ok(())
    }

    /// Ensure a Human actor row exists in `bcs_bots` for the given staff_no.
    ///
    /// Idempotent: if the row already exists it MUST NOT be overwritten
    /// (in particular, `name` MUST be preserved per Requirement 3.1#4).
    /// On first INSERT, sets:
    ///   - `bot_uuid = "human_{staff_no}"`
    ///   - `actor_kind = 'human'`
    ///   - `status = 'online'`
    ///   - `visibility = 'protected'`
    ///   - `name = nick_name` (passed in by caller)
    ///   - `created_by = staff_no`
    ///
    /// Default impl is a Noop so legacy implementations compile; production
    /// implementations MUST override this.
    ///
    /// See Requirement 3.1#2, 3.1#2a, 3.1#4.
    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        let _ = (staff_no, nick_name);
        Ok(EnsureHumanResult { created: false })
    }

    /// List legacy bots owned by `staff_no` for the `/me/ensure-human` endpoint.
    ///
    /// Returns bots where:
    /// - (a) `created_by = staff_no` AND `actor_kind = 'bot'`, OR
    /// - (b) `created_by IS NULL` AND `bot_uuid` matches `{namespace}:{staff_no}`
    ///       with a whitelisted namespace (`default` or `{yyyymmdd}_{8chars}`).
    ///
    /// Default impl returns an empty vec (no legacy bots).
    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let _ = (staff_no, env);
        Ok(vec![])
    }

    /// Repair the `name` column for a Human actor row in `bcs_bots`.
    ///
    /// Used by the `/debug/whoami` debug endpoint to backfill the real
    /// `nick_name` after the original onboard fell back to writing
    /// `staff_no` (because the auth SDK didn't return `nick_name` at the time).
    ///
    /// Contract:
    /// - Target row: `bcs_bots WHERE bot_uuid = "human_{staff_no}" AND env = ?`
    /// - Only updates the `name` column; does NOT touch any other field
    /// - If the row does not exist, this is a no-op (no error)
    /// - `new_name` MUST be non-empty (caller is responsible for trimming)
    ///
    /// Default impl is a Noop so legacy implementations compile; production
    /// implementations MUST override this.
    async fn update_human_name(&self, staff_no: &str, new_name: &str) -> ServiceResult<()> {
        let _ = (staff_no, new_name);
        Ok(())
    }

    /// Check if a bot has been onboarded (has stored capabilities).
    async fn has_been_onboarded(&self, bot_id: &str) -> bool;

    /// Save the created_by field for a bot.
    /// - `overwrite=false`: only set if currently empty (first-writer-wins, default)
    /// - `overwrite=true`: unconditionally overwrite existing value
    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()>;

    /// Save the session token for a bot (persists across restarts).
    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()>;

    /// Load the session token for a bot.
    async fn load_token(&self, bot_id: &str) -> Option<String>;

    /// Find a bot by its token. Returns bot_id if found.
    async fn find_bot_by_token(&self, token: &str) -> Option<String>;

    /// Find a bot by its dedicated `agent_code`. Returns bot_id if found.
    /// Auth plugins can use this to resolve provider-registered bots whose
    /// `agent_code` was set to their `provider_bot_ref`.
    /// Default impl returns `None` so noop/mock registries need not implement it.
    async fn find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        let _ = agent_code;
        None
    }

    /// Find a bot by its channel binding.
    /// Returns bot_id if found.
    async fn find_bot_by_binding_channel(
        &self,
        channel: &str,
        binding_key: &str,
    ) -> Option<String> {
        let _ = (channel, binding_key);
        None
    }

    // ===== Streaming Connection Management =====

    /// Register a new streaming connection for a bot.
    /// Returns Ok(token) on success, or Err if bot is already connected.
    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()>;

    /// Reconnect a bot with an existing token.
    /// Returns Ok((bot_id, token)) on success, or Err if token is invalid or bot is already connected.
    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()>;

    /// Remove a streaming connection (on disconnect).
    /// Token mapping is preserved for reconnection.
    async fn disconnect_streaming(&self, bot_id: &str);

    /// Check if a bot has an active streaming connection.
    async fn is_connected(&self, bot_id: &str) -> bool;

    /// Batch check runtime-active actors for owner-facing lists.
    ///
    /// Runtime active intentionally ignores actor lifecycle visibility (`Hidden`)
    /// and means either an active WS connection or a routable HTTP provider target.
    async fn list_runtime_active_bot_ids(&self, bot_ids: &[String]) -> Vec<String> {
        let mut active = Vec::new();
        for bot_id in bot_ids {
            if self.is_connected(bot_id).await {
                active.push(bot_id.clone());
                continue;
            }
            if matches!(
                self.resolve_delivery_target(bot_id).await,
                Ok(BotDeliveryTarget::HttpProvider { .. })
            ) {
                active.push(bot_id.clone());
            }
        }
        active
    }

    /// Check whether an actor should be shown as online in user-facing lists.
    ///
    /// Effective online means the actor has an active transport connection and
    /// its actor lifecycle status is `Online`. Remote-backed implementations
    /// may override this default to resolve connection state and actor status
    /// in one backend read instead of the default two async calls.
    async fn is_effectively_online(&self, bot_id: &str) -> bool {
        if !self.is_connected(bot_id).await {
            return false;
        }
        match self.get(bot_id).await {
            Some(bot) => bot.status == ActorStatus::Online,
            None => false,
        }
    }

    /// Send a frame to a connected bot through the active transport.
    async fn send_frame(&self, bot_id: &str, frame: String) -> Result<(), ()>;

    /// Send a request to a bot and wait for the response (one-shot request-response).
    /// Returns the response payload on success, or an error string on failure.
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

    /// Register a pending one-shot request (called by dispatcher when res frame arrives).
    async fn resolve_pending_request(&self, _request_id: &str, _response: serde_json::Value) {}

    /// List all bots with active streaming connections.
    async fn list_connected(&self) -> Vec<String>;

    /// Store a token-to-bot mapping (for restoring from disk).
    async fn store_token_mapping(&self, token: String, bot_id: String);

    /// Get the negotiated protocol version for a connected bot (defaults to 1).
    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        let _ = bot_id;
        1
    }

    /// Set the negotiated protocol version for a connected bot.
    async fn set_protocol_version(&self, bot_id: &str, version: u32) {
        let _ = (bot_id, version);
    }

    // ===== Unified Bot Connection =====

    /// Register an HTTP connection for a bot (no streaming channel).
    /// Creates a minimal bot entry if needed and stores token mapping.
    /// Returns the session token.
    async fn register_http_connection(&self, bot_id: String, token: String) -> String;

    /// Connect a bot (handles both streaming and HTTP).
    ///
    /// This method provides unified connection logic for both:
    /// - Streaming connections
    /// - HTTP connections (no persistent channel)
    ///
    /// Implementations own token validation, bot id generation, reconnect
    /// semantics, and any storage hydration. The service-api crate is a pure
    /// contract crate and intentionally does not provide business defaults.
    async fn connect_bot(
        &self,
        _params: BotConnectParams,
        _kind: ConnectionKind,
    ) -> Result<BotConnectResult, ConnectError> {
        Err(ConnectError::InternalError(
            "connect_bot is not implemented by this registry".to_string(),
        ))
    }
}

/// Fill in missing or placeholder `bot_name` fields on a Group's participants
/// from the registry.
///
/// A participant name is considered missing when it is absent, empty, or equal
/// to the bot UUID. This is the canonical implementation — callers should use
/// this instead of duplicating the loop.
pub async fn backfill_bot_names(registry: &dyn BotRegistryCoreService, group: &mut Group) {
    for participant in &mut group.participants {
        if !needs_bot_name_backfill(participant) {
            continue;
        }

        // Use get_including_deleted so removed (soft-deleted) bots still resolve
        // their name snapshot for display; the group participant row is retained
        // after a bot is deleted, and without this the frontend would only see
        // the bot_uuid with no bot_name.
        if let Some(bot) = registry.get_including_deleted(&participant.bot_uuid).await {
            if let Some(name) = bot
                .capabilities
                .name
                .as_deref()
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                participant.bot_name = Some(name.to_string());
            }
        }
    }
}

/// Same as [`backfill_bot_names`] but operates on a bare participant slice,
/// useful for Session participants that are not wrapped in a Group.
pub async fn backfill_participant_names(
    registry: &dyn BotRegistryCoreService,
    participants: &mut [bcs_domain::Participant],
) {
    for participant in participants.iter_mut() {
        if !needs_bot_name_backfill(participant) {
            continue;
        }

        // See backfill_bot_names: include soft-deleted bots for display.
        if let Some(bot) = registry.get_including_deleted(&participant.bot_uuid).await {
            if let Some(name) = bot
                .capabilities
                .name
                .as_deref()
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                participant.bot_name = Some(name.to_string());
            }
        }
    }
}

fn needs_bot_name_backfill(participant: &bcs_domain::Participant) -> bool {
    match participant.bot_name.as_deref() {
        None => true,
        Some(name) => {
            let name = name.trim();
            name.is_empty() || name == participant.bot_uuid
        }
    }
}
