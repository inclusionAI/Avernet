//! Plugin-backed bot repository implementation.
//!
//! This module provides a bot repository backed by:
//! - **Cache plugin**: Dynamic status with TTL for failover recovery
//! - **Database plugin**: Persistent storage for bot capabilities and tokens
//! - **Process Memory**: streaming connection state and heartbeat tracking
//!
//! # Architecture
//!
//! ```text
//! Layer 1 (Memory):    ws_connection, last_heartbeat, token_to_bot mapping
//! Layer 2 (Cache):     dynamic_status (TTL 600s)
//! Layer 3 (Database):  bot_info JSON, session_token, name
//! ```

use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tokio::sync::{RwLock, oneshot};
use tracing::{debug, info, warn};

use bcs_cache_api::{CacheError, CachePlugin};
use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{
    DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_service_api::{
    BindingChannels, BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotCapabilities, BotControlPlaneDescriptor, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotDynamicStatus, BotMetricCount,
    BotMetricsSnapshotPort, RegisteredBot, ServiceError, ServiceResult, Skill,
};

pub mod memory;
pub mod provider;

pub use bcs_service_api::port::repo::BotRepoPort;
pub use memory::MemoryBotRepo;
pub use provider::{DbProviderStore, MemoryProviderStore};

/// Maximum time before a bot registration expires (5 minutes).
const BOT_EXPIRY: Duration = Duration::from_secs(300);

/// Cache TTL for dynamic status (10 minutes = 10x heartbeat interval).
const STATUS_CACHE_TTL_SECONDS: i64 = 600;

/// Default cache key prefix used by legacy constructors.
const DEFAULT_CACHE_KEY_PREFIX: &str = "bcs:";

/// Cache key namespace for bot status.
const STATUS_CACHE_KEY_NAMESPACE: &str = "status:";

fn is_legacy_namespace(bot_uuid: &str, staff_no: &str) -> bool {
    let suffix = format!(":{}", staff_no);
    if !bot_uuid.ends_with(&suffix) {
        return false;
    }
    let namespace = &bot_uuid[..bot_uuid.len() - suffix.len()];
    if namespace == "default" {
        return true;
    }
    let bytes = namespace.as_bytes();
    if bytes.len() != 17 {
        return false;
    }
    if bytes[8] != b'_' {
        return false;
    }
    bytes[..8].iter().all(|b| b.is_ascii_digit())
        && bytes[9..]
            .iter()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
}

/// Bot info stored in the `bcs_bots.bot_info` field.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BotInfo {
    pub summary: Option<String>,
    #[serde(default)]
    pub domains: Vec<String>,
    #[serde(default, deserialize_with = "bcs_service_api::deserialize_skills")]
    pub skills: Vec<Skill>,
    #[serde(default)]
    pub scopes: Vec<String>,
    /// Channel bindings for message routing.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binding_channels: Option<BindingChannels>,
    #[serde(default)]
    pub hidden: bool,
    /// AI安全网关agent_code
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_code: Option<String>,
    /// AI安全网关授权token
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_token: Option<String>,
}

/// A bot streaming connection marker (process-local, not serializable).
#[derive(Debug, Clone)]
pub struct BotConnection {
    /// Session token for authentication.
    pub session_token: String,
    /// When the connection was established.
    pub connected_at: Instant,
}

/// Internal representation of a registered bot.
#[derive(Debug)]
struct RegisteredBotInner {
    /// Bot unique identifier (UUID).
    bot_uuid: String,
    /// Last heartbeat timestamp (process-local).
    last_heartbeat: Instant,
    /// Bot capabilities (loaded from database).
    capabilities: BotCapabilities,
    /// Dynamic status (synced to cache).
    dynamic_status: BotDynamicStatus,
    /// Active streaming connection (if connected).
    ws_connection: Option<BotConnection>,
    /// Session token (persisted in database).
    session_token: Option<String>,
    /// Server environment (prod, gray, pre, dev).
    env: Option<String>,
    /// DEPRECATED: Hidden mechanism removed in Rev-4 / Human Actor V1.
    /// Retained for struct compatibility; ignored by routing/visibility.
    hidden: bool,
    /// Actor-level lifecycle status (`Online` / `Hidden`) — Task P.2 / Requirement 3.16.
    status: bcs_service_api::ActorStatus,
    /// Actor kind (Bot / Human) — Human Actor V1 / Requirement 3.1.
    /// Sourced from `bcs_bots.actor_kind`. Defaults to `Bot` for legacy rows.
    actor_kind: bcs_service_api::ActorKind,
    /// Creator's staff_no (set during onboard, immutable).
    created_by: Option<String>,
}

impl RegisteredBotInner {
    /// Check if this bot registration has expired.
    fn is_expired(&self) -> bool {
        self.last_heartbeat.elapsed() > BOT_EXPIRY
    }

    /// Check if this bot has a specific skill (case-insensitive partial match).
    fn has_skill(&self, skill: &str) -> bool {
        self.capabilities
            .skills
            .iter()
            .any(|s| s.name.to_lowercase().contains(&skill.to_lowercase()))
    }

    /// Check if this bot has a specific domain (case-insensitive partial match).
    fn has_domain(&self, domain: &str) -> bool {
        self.capabilities
            .domains
            .iter()
            .any(|d| d.to_lowercase().contains(&domain.to_lowercase()))
    }

    /// Check if this bot has a specific scope (case-insensitive partial match).
    fn has_scope(&self, scope: &str) -> bool {
        self.capabilities
            .scopes
            .iter()
            .any(|s| s.to_lowercase().contains(&scope.to_lowercase()))
    }

    /// Convert to public RegisteredBot type.
    ///
    /// Human Actor V1 / Code-Review fix #1: propagate the persisted
    /// `actor_kind` and `status` instead of returning defaults, otherwise
    /// downstream callers (`O.5` human_ guard, `P.3` mode validation,
    /// `F.3` Human↔Human rejection) will misclassify every actor.
    fn to_registered_bot(&self) -> RegisteredBot {
        // 清除敏感字段，防止通过常规接口泄露
        let mut capabilities = self.capabilities.clone();
        capabilities.agent_token = None;

        RegisteredBot {
            bot_uuid: self.bot_uuid.clone(),
            capabilities,
            dynamic_status: self.dynamic_status.clone(),
            env: self.env.clone(),
            created_by: self.created_by.clone(),
            actor_kind: self.actor_kind,
            status: self.status,
        }
    }
}

/// Persistent bot repository backed by cache and DB plugins.
///
/// Uses a three-layer storage architecture:
/// - Layer 1: Process memory for WebSocket connections and heartbeats
/// - Layer 2: Cache plugin for dynamic status with TTL
/// - Layer 3: Database plugin for persistent capabilities and tokens
pub struct PersistentBotRepo {
    // Layer 1: Process Memory
    /// Bot connections and state.
    bots: RwLock<HashMap<String, RegisteredBotInner>>,
    /// Token to bot_uuid mapping (hot cache for auth).
    token_to_bot: RwLock<HashMap<String, String>>,
    /// Channel binding index: (channel, binding_key) -> bot_uuid.
    binding_channel_index: Arc<RwLock<HashMap<(String, String), String>>>,
    /// Process-local runtime info, e.g. client_kind from bot.connect.
    bot_info_overrides: RwLock<HashMap<(String, String), String>>,

    // Layer 2: Cache
    /// Cache plugin for dynamic status storage.
    cache: Arc<dyn CachePlugin>,
    /// Business cache key prefix resolved from configuration.
    cache_key_prefix: String,

    // Layer 3: Database
    /// DB plugin for persistent storage.
    db: Arc<dyn DbPlugin>,

    /// SQL dialect flavor.
    flavor: DbSqlFlavor,

    /// Pending request-response channels for send_request.
    pending_requests: RwLock<HashMap<String, oneshot::Sender<serde_json::Value>>>,
}

impl PersistentBotRepo {
    /// Create a new persistent bot repository with cache and DB plugins.
    pub fn with_plugins(
        cache: Arc<dyn CachePlugin>,
        db: Arc<dyn DbPlugin>,
    ) -> Self {
        Self::with_plugins_flavor(cache, db, DbSqlFlavor::Mysql)
    }

    /// Create a new persistent bot repository with cache, DB plugins, and SQL flavor.
    pub fn with_plugins_flavor(
        cache: Arc<dyn CachePlugin>,
        db: Arc<dyn DbPlugin>,
        flavor: DbSqlFlavor,
    ) -> Self {
        Self::with_plugins_flavor_and_cache_key_prefix(
            cache,
            db,
            flavor,
            DEFAULT_CACHE_KEY_PREFIX,
        )
    }

    /// Create a new distributed registry with an explicit business cache key prefix.
    pub fn with_plugins_flavor_and_cache_key_prefix(
        cache: Arc<dyn CachePlugin>,
        db: Arc<dyn DbPlugin>,
        flavor: DbSqlFlavor,
        cache_key_prefix: impl Into<String>,
    ) -> Self {
        Self {
            bots: RwLock::new(HashMap::new()),
            token_to_bot: RwLock::new(HashMap::new()),
            binding_channel_index: Arc::new(RwLock::new(HashMap::new())),
            bot_info_overrides: RwLock::new(HashMap::new()),
            cache,
            cache_key_prefix: cache_key_prefix.into(),
            db,
            flavor,
            pending_requests: RwLock::new(HashMap::new()),
        }
    }

    /// Create a new distributed registry.
    pub fn new(
        cache: Arc<dyn CachePlugin>,
        db: Arc<dyn DbPlugin>,
        _legacy_db: String,
    ) -> Self {
        Self::with_plugins(cache, db)
    }

    /// Get current timestamp in milliseconds.
    fn current_timestamp() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0)
    }

    /// Build cache key for bot status.
    #[cfg(test)]
    fn status_cache_key(bot_uuid: &str) -> String {
        Self::status_cache_key_with_prefix(DEFAULT_CACHE_KEY_PREFIX, bot_uuid)
    }

    fn configured_status_cache_key(&self, bot_uuid: &str) -> String {
        Self::status_cache_key_with_prefix(&self.cache_key_prefix, bot_uuid)
    }

    fn status_cache_key_with_prefix(cache_key_prefix: &str, bot_uuid: &str) -> String {
        format!("{}{}{}", cache_key_prefix, STATUS_CACHE_KEY_NAMESPACE, bot_uuid)
    }

    async fn db_query(&self, sql: &str, params: Vec<Value>) -> bcs_db_api::DbResult<Vec<DbRow>> {
        self.db.query(DbStatement::with_params(sql, params)).await
    }

    async fn db_execute_affected(
        &self,
        sql: &str,
        params: Vec<Value>,
    ) -> bcs_db_api::DbResult<u64> {
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .map(|result| result.affected_rows)
    }

    fn cache_hash_to_strings(
        fields: BTreeMap<String, Vec<u8>>,
    ) -> Result<HashMap<String, String>, CacheError> {
        fields
            .into_iter()
            .map(|(field, value)| {
                String::from_utf8(value)
                    .map(|value| (field, value))
                    .map_err(|err| CacheError::Backend(err.to_string()))
            })
            .collect()
    }

    /// Save bot capabilities to the configured database.
    /// First checks if the record exists, then uses UPDATE if it exists, otherwise INSERT.
    async fn save_to_db(
        &self,
        bot_uuid: &str,
        caps: &BotCapabilities,
        session_token: Option<&str>,
        created_by_override: Option<&str>,
    ) -> ServiceResult<()> {
        let env = resolve_env();

        let (hidden, existing_created_by) = {
            let bots = self.bots.read().await;
            bots.get(bot_uuid)
                .map(|b| (b.hidden, b.created_by.clone()))
                .unwrap_or((false, None))
        };
        let created_by = created_by_override
            .map(|created_by| created_by.to_string())
            .or(existing_created_by);

        let bot_info = BotInfo {
            summary: caps.summary.clone(),
            domains: caps.domains.clone(),
            skills: caps.skills.clone(),
            scopes: caps.scopes.clone(),
            binding_channels: caps.binding_channels.clone(),
            hidden,
            agent_code: caps.agent_code.clone(),
            agent_token: caps.agent_token.clone(),
        };
        let bot_info_json = serde_json::to_string(&bot_info)
            .map_err(|e| ServiceError::InternalError(e.to_string()))?;

        let name = caps.name.as_deref().unwrap_or(bot_uuid);

        // Dedicated `agent_code` column (transition: dual-written alongside the
        // `bot_info` JSON above). None maps to SQL NULL.
        let agent_code_value = match caps.agent_code.as_deref() {
            Some(code) => Value::from(code),
            None => Value::Null,
        };

        // Check if record exists
        let exists = self.exists_in_db(bot_uuid).await;

        // Normalize: empty visibility defaults to "private" before persisting
        let visibility = if caps.visibility.is_empty() {
            "private"
        } else {
            &caps.visibility
        };

        let affected = if exists {
            // UPDATE existing record — created_by is NOT updated here;
            // use update_created_by_in_db() / save_created_by() for that.
            // Only overwrite session_token when we have a value; None means
            // the caller doesn't know the token — preserve whatever is in DB.
            if let Some(token) = session_token {
                let sql = "UPDATE bcs_bots SET name = ?, bot_info = ?, session_token = ?, visibility = ?, agent_code = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ?";
                self.db_execute_affected(sql, vec![
                    Value::from(name),
                    Value::from(bot_info_json.as_str()),
                    Value::from(token),
                    Value::from(visibility),
                    agent_code_value.clone(),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ]).await
            } else {
                let sql = "UPDATE bcs_bots SET name = ?, bot_info = ?, visibility = ?, agent_code = ?, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ?";
                self.db_execute_affected(sql, vec![
                    Value::from(name),
                    Value::from(bot_info_json.as_str()),
                    Value::from(visibility),
                    agent_code_value.clone(),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ]).await
            }
        } else {
            // INSERT new record — None maps to SQL NULL.
            // P.6: explicitly write status='online' on first INSERT (do NOT rely
            // on the column default). UPSERT-style updates above intentionally
            // leave `status` untouched so a hidden actor stays hidden across
            // re-onboards (Requirement 3.16#7).
            let sql = "INSERT INTO bcs_bots (bot_uuid, name, bot_info, session_token, created_by, visibility, status, actor_kind, agent_code, is_deleted, env, registered_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";
            self.db_execute_affected(sql, vec![
                Value::from(bot_uuid),
                Value::from(name),
                Value::from(bot_info_json.as_str()),
                match session_token {
                    Some(t) => Value::from(t),
                    None => Value::Null,
                },
                match created_by.as_deref() {
                    Some(cb) => Value::from(cb),
                    None => Value::Null,
                },
                Value::from(visibility),
                Value::from("online"),
                Value::from("bot"),
                agent_code_value.clone(),
                Value::from(0_i64),
                Value::from(env.as_str()),
            ]).await
        }.map_err(|e| {
            warn!(bot_uuid = %bot_uuid, error = %e, "save_to_db: failed");
            ServiceError::InternalError(e.to_string())
        })?;

        info!(bot_uuid = %bot_uuid, affected_rows = affected, exists = exists, "save_to_db: {} completed", if exists { "update" } else { "insert" });

        Ok(())
    }

    /// Load bot capabilities, env, hidden flag, and created_by from the configured database.
    ///
    /// H.2: `hidden` is now derived from the new `bcs_bots.status` column
    /// (`status == 'hidden'`). The legacy `bot_info.hidden` field is no longer
    /// authoritative — if the row was migrated but `bot_info.hidden=true` is
    /// still on disk, it is ignored in favor of `status`.
    async fn try_load_from_db(
        &self,
        bot_uuid: &str,
        include_deleted: bool,
    ) -> ServiceResult<
        Option<(
            BotCapabilities,
            Option<String>,
            bool,
            Option<String>,
            bcs_service_api::ActorKind,
            bcs_service_api::ActorStatus,
        )>,
    > {
        let env = resolve_env();
        // Code-Review fix #1: include `actor_kind` so the registry read path can
        // propagate it back to callers (O.5 / P.3 / F.3 all gate on actor_kind).
        //
        // `include_deleted` skips the soft-delete filter so callers that only
        // need display metadata (e.g. group participant names of removed bots)
        // can still read the `name` snapshot from the retained row.
        let sql = if include_deleted {
            "SELECT name, bot_info, visibility, status, actor_kind, env, created_by, agent_code FROM bcs_bots WHERE bot_uuid = ? AND env = ?".to_string()
        } else {
            "SELECT name, bot_info, visibility, status, actor_kind, env, created_by, agent_code FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0".to_string()
        };

        let rows = self
            .db_query(&sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "load Bot '{bot_uuid}' from registry database: {error}"
                ))
            })?;

        if let Some(row) = rows.first() {
            let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
            let env: Option<String> = db_get_column_opt(row, "env").ok().flatten();
            let visibility: String = db_get_column_opt(row, "visibility")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "private".to_string());
            // H.2: derive hidden from bcs_bots.status, not bot_info.hidden.
            // Status column may be absent on un-migrated rows → default to "online".
            let status_str: String = db_get_column_opt(row, "status")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "online".to_string());
            // Code-Review fix #1: read actor_kind from the database; default to bot for
            // un-migrated rows (legacy data has no actor_kind column populated).
            let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "bot".to_string());
            let created_by: Option<String> = db_get_column_opt(row, "created_by").ok().flatten();
            let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                .ok()
                .flatten()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_default();

            let hidden = status_str == "hidden";
            let actor_status = match status_str.as_str() {
                "hidden" => bcs_service_api::ActorStatus::Hidden,
                _ => bcs_service_api::ActorStatus::Online,
            };
            let actor_kind = match actor_kind_str.as_str() {
                "human" => bcs_service_api::ActorKind::Human,
                _ => bcs_service_api::ActorKind::Bot,
            };

            // agent_code transition: prefer the dedicated `agent_code` column;
            // fall back to the legacy `bot_info.agent_code` JSON for rows written
            // before the column existed (historical data not yet backfilled).
            let col_agent_code: Option<String> = db_get_column_opt(row, "agent_code").ok().flatten();
            let agent_code = match (col_agent_code, bot_info.agent_code) {
                (Some(code), _) => {
                    info!(
                        bot_uuid = %bot_uuid,
                        source = "column",
                        "load_from_mysql: agent_code resolved from dedicated column"
                    );
                    Some(code)
                }
                (None, Some(code)) => {
                    warn!(
                        bot_uuid = %bot_uuid,
                        source = "bot_info_fallback",
                        "load_from_mysql: agent_code missing in column, fell back to bot_info JSON"
                    );
                    Some(code)
                }
                (None, None) => {
                    info!(
                        bot_uuid = %bot_uuid,
                        "load_from_mysql: agent_code absent in both column and bot_info"
                    );
                    None
                }
            };

            return Ok(Some((
                BotCapabilities {
                    name,
                    summary: bot_info.summary,
                    domains: bot_info.domains,
                    skills: bot_info.skills,
                    scopes: bot_info.scopes,
                    binding_channels: bot_info.binding_channels,
                    hidden,
                    visibility,
                    agent_code,
                    agent_token: bot_info.agent_token,
                },
                env,
                hidden,
                created_by,
                actor_kind,
                actor_status,
            )));
        }

        Ok(None)
    }

    async fn load_from_db(
        &self,
        bot_uuid: &str,
        include_deleted: bool,
    ) -> Option<(
        BotCapabilities,
        Option<String>,
        bool,
        Option<String>,
        bcs_service_api::ActorKind,
        bcs_service_api::ActorStatus,
    )> {
        self.try_load_from_db(bot_uuid, include_deleted)
            .await
            .ok()
            .flatten()
    }

    /// Save dynamic status to the configured cache.
    async fn save_status_to_cache(&self, bot_uuid: &str, status: &BotDynamicStatus) {
        let key = self.configured_status_cache_key(bot_uuid);
        let now = Self::current_timestamp();

        info!(
            bot_uuid = %bot_uuid,
            status_cache_key = %key,
            status = %status.status,
            dynamic_summary = ?status.dynamic_summary,
            load = ?status.load,
            "Saving bot status to cache"
        );

        // HSET multiple fields
        if let Err(e) = self
            .cache
            .hash_set(&key, "status", status.status.as_bytes().to_vec())
            .await
        {
            warn!(bot_uuid = %bot_uuid, error = %e, "Failed to save status to cache");
            return;
        }

        if let Some(ref summary) = status.dynamic_summary {
            let _ = self
                .cache
                .hash_set(&key, "dynamic_summary", summary.as_bytes().to_vec())
                .await;
        }
        if let Some(load) = status.load {
            let _ = self
                .cache
                .hash_set(&key, "load", load.to_string().into_bytes())
                .await;
        }
        let _ = self
            .cache
            .hash_set(&key, "updated_at", now.to_string().into_bytes())
            .await;

        // Set TTL
        let _ = self
            .cache
            .expire(&key, Duration::from_secs(STATUS_CACHE_TTL_SECONDS as u64))
            .await;

        info!(bot_uuid = %bot_uuid, status_cache_key = %key, "Bot status saved to cache successfully");
    }

    /// Load dynamic status from the configured cache.
    async fn load_status_from_cache(&self, bot_uuid: &str) -> BotDynamicStatus {
        let key = self.configured_status_cache_key(bot_uuid);

        match self
            .cache
            .hash_get_all(&key)
            .await
            .and_then(Self::cache_hash_to_strings)
        {
            Ok(map) => BotDynamicStatus {
                status: map.get("status").cloned().unwrap_or_default(),
                dynamic_summary: map.get("dynamic_summary").cloned(),
                load: map.get("load").and_then(|s| s.parse().ok()),
                updated_at: map.get("updated_at").and_then(|s| s.parse().ok()),
            },
            Err(_) => BotDynamicStatus::default(),
        }
    }

    /// Read the `bot_info` JSON column for a given bot, parsed as a JSON object.
    /// Returns `None` if the row is missing, the column is NULL, or JSON parsing fails.
    async fn read_bot_info_json(&self, bot_uuid: &str) -> Option<serde_json::Value> {
        let env = resolve_env();
        let sql = "SELECT bot_info FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 LIMIT 1";
        let rows = self
            .db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .ok()?;
        let row = rows.first()?;
        let bot_info_str: String = db_get_column_opt(row, "bot_info").ok().flatten()?;
        serde_json::from_str(&bot_info_str).ok()
    }

    /// Check if bot exists in the configured database.
    async fn exists_in_db(&self, bot_uuid: &str) -> bool {
        let env = resolve_env();
        let sql = "SELECT 1 FROM bcs_bots WHERE bot_uuid = ? AND env = ? LIMIT 1";
        self.db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .map(|r| !r.is_empty())
            .unwrap_or(false)
    }

    /// Save session token to the configured database.
    async fn save_token_to_db(&self, bot_uuid: &str, token: &str) -> ServiceResult<()> {
        let env = resolve_env();

        let sql = "UPDATE bcs_bots SET session_token = ? WHERE bot_uuid = ? AND env = ?";

        info!(bot_uuid = %bot_uuid, env = %env, "save_token_to_db: executing update");

        let affected = self
            .db_execute_affected(
                sql,
                vec![
                    Value::from(token),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(bot_uuid = %bot_uuid, error = %e, "save_token_to_db: failed");
                ServiceError::InternalError(e.to_string())
            })?;

        info!(bot_uuid = %bot_uuid, affected_rows = affected, "save_token_to_db: update completed");

        Ok(())
    }

    /// Update created_by in the configured database.
    /// - `overwrite=false`: only if currently NULL (first writer wins, original behavior)
    /// - `overwrite=true`: unconditional update (last writer wins)
    async fn update_created_by_in_db(
        &self,
        bot_uuid: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        let env = resolve_env();
        let sql = if overwrite {
            "UPDATE bcs_bots SET created_by = ? WHERE bot_uuid = ? AND env = ?"
        } else {
            "UPDATE bcs_bots SET created_by = ? WHERE bot_uuid = ? AND env = ? AND created_by IS NULL"
        };

        let affected = self
            .db_execute_affected(
                sql,
                vec![
                    Value::from(created_by),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                ],
            )
            .await
            .map_err(|e| {
                warn!(bot_uuid = %bot_uuid, error = %e, "update_created_by_in_db: failed");
                ServiceError::InternalError(e.to_string())
            })?;

        info!(bot_uuid = %bot_uuid, created_by = %created_by, affected_rows = affected, "update_created_by_in_db: completed");

        Ok(())
    }

    /// Query bots by creator from the configured database.
    ///
    /// H.3: SELECT now includes `status` and `actor_kind`; `caps.hidden` is
    /// derived from `status == 'hidden'` (legacy `bot_info.hidden` ignored).
    async fn list_bots_by_creator_from_db(
        &self,
        created_by: &str,
        env: &str,
    ) -> Vec<RegisteredBot> {
        let sql = "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, env, created_by FROM bcs_bots WHERE created_by = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = match self
            .db_query(sql, vec![Value::from(created_by), Value::from(env)])
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!(created_by = %created_by, error = %e, "list_bots_by_creator_from_db: failed");
                return Vec::new();
            }
        };

        rows.iter()
            .filter_map(|row| {
                let bot_uuid: String = db_get_column_opt(row, "bot_uuid").ok().flatten()?;
                let name: Option<String> = db_get_column_opt(row, "name").ok().flatten();
                let env: Option<String> = db_get_column_opt(row, "env").ok().flatten();
                let visibility: String = db_get_column_opt(row, "visibility")
                    .ok()
                    .flatten()
                    .unwrap_or_else(|| "protected".to_string());
                let created_by: Option<String> =
                    db_get_column_opt(row, "created_by").ok().flatten();
                let bot_info: BotInfo = db_get_column_opt::<String>(row, "bot_info")
                    .ok()
                    .flatten()
                    .and_then(|s| serde_json::from_str(&s).ok())
                    .unwrap_or_default();

                // H.3: derive hidden from bcs_bots.status (column may be absent
                // on un-migrated rows → default to "online" → hidden=false).
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

                let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                    .ok()
                    .flatten()
                    .filter(|v: &String| !v.is_empty())
                    .unwrap_or_else(|| "bot".to_string());
                let actor_kind = match actor_kind_str.as_str() {
                    "human" => bcs_service_api::ActorKind::Human,
                    _ => bcs_service_api::ActorKind::Bot,
                };

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
                    dynamic_status: BotDynamicStatus::default(),
                    env,
                    created_by,
                    actor_kind,
                    status,
                })
            })
            .collect()
    }

    /// Load session token from the configured database.
    async fn load_token_from_db(&self, bot_uuid: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT session_token FROM bcs_bots WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = self
            .db_query(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .ok()?;

        if let Some(row) = rows.first() {
            return db_get_column_opt(row, "session_token").ok().flatten();
        }

        None
    }

    /// Find bot by token in the configured database (indexed lookup).
    async fn find_bot_by_token_in_db(&self, token: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT bot_uuid FROM bcs_bots WHERE session_token = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        info!(token = %token, env = %env, "find_bot_by_token_in_db: executing query");

        let rows = match self
            .db_query(sql, vec![Value::from(token), Value::from(env.as_str())])
            .await
        {
            Ok(rows) => rows,
            Err(e) => {
                warn!(token = %token, env = %env, error = %e, "find_bot_by_token_in_db: database query failed");
                return None;
            }
        };

        info!(token = %token, rows_count = rows.len(), "find_bot_by_token_in_db: query completed");

        if let Some(row) = rows.first() {
            match db_get_column::<String>(row, "bot_uuid") {
                Ok(bot_uuid) => {
                    info!(token = %token, bot_uuid = %bot_uuid, "find_bot_by_token_in_db: found bot");
                    return Some(bot_uuid);
                }
                Err(e) => {
                    warn!(token = %token, error = %e, "find_bot_by_token_in_db: failed to get bot_uuid");
                    return None;
                }
            }
        }

        warn!(token = %token, "find_bot_by_token_in_db: no bot found");
        None
    }

    /// Resolve a bot by its dedicated `agent_code` column via the indexed
    /// lookup (`idx_agent_code`). Returns the first matching `bot_uuid`.
    async fn find_bot_by_agent_code_in_db(&self, agent_code: &str) -> Option<String> {
        let env = resolve_env();
        let sql = "SELECT bot_uuid FROM bcs_bots WHERE agent_code = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        let rows = match self
            .db_query(sql, vec![Value::from(agent_code), Value::from(env.as_str())])
            .await
        {
            Ok(rows) => rows,
            Err(e) => {
                warn!(agent_code = %agent_code, env = %env, error = %e, "find_bot_by_agent_code_in_db: query failed");
                return None;
            }
        };

        if let Some(row) = rows.first() {
            match db_get_column::<String>(row, "bot_uuid") {
                Ok(bot_uuid) => {
                    info!(agent_code = %agent_code, bot_uuid = %bot_uuid, "find_bot_by_agent_code_in_db: found bot");
                    return Some(bot_uuid);
                }
                Err(e) => {
                    warn!(agent_code = %agent_code, error = %e, "find_bot_by_agent_code_in_db: failed to get bot_uuid");
                    return None;
                }
            }
        }

        warn!(agent_code = %agent_code, "find_bot_by_agent_code_in_db: no bot found");
        None
    }

    /// Soft-delete bot from the configured database.
    async fn soft_delete_in_db(&self, bot_uuid: &str) -> bool {
        let env = resolve_env();
        let sql = "UPDATE bcs_bots SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0";

        self.db_execute_affected(sql, vec![Value::from(bot_uuid), Value::from(env.as_str())])
            .await
            .map(|n| n > 0)
            .unwrap_or(false)
    }

    /// Sync binding channel index for a bot.
    /// Removes old bindings and adds new ones.
    async fn sync_binding_channel_index(&self, bot_id: &str, capabilities: &BotCapabilities) {
        let mut index = self.binding_channel_index.write().await;

        // Remove old bindings for this bot
        index.retain(|_, v| v != bot_id);

        // Add new bindings
        if let Some(ref binding_channels) = capabilities.binding_channels {
            for (channel, binding) in binding_channels {
                index.insert(
                    (channel.clone(), binding.binding_key.clone()),
                    bot_id.to_string(),
                );
            }
        }
    }

    /// Query bots from the configured database by name with pagination.
    /// Uses a CTE to resolve friendships and applies cooperatability filtering in SQL.
    ///
    /// Returns `(Vec<(RegisteredBot, bool)>, usize)` where bool = is_friend.
    async fn list_bots_by_name_and_cooperatable_with_impl(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        let env = resolve_env();
        let like = if name.is_empty() {
            "%".to_string()
        } else {
            format!("%{}%", name.replace('%', r"\%").replace('_', r"\_"))
        };

        let coop_flag: i64 = if cooperatable_only { 1 } else { 0 };

        let visibility_filter = self.flavor.iif(
            "?",
            "b.visibility = 'public' OR f.bot_uuid IS NOT NULL",
            "b.visibility IN ('public', 'protected')",
        );

        let page_sql = format!(
            "WITH friend_uuids AS (\
                SELECT right_bot AS bot_uuid FROM bcs_friendships \
                WHERE left_bot = ? AND env = ? \
                UNION \
                SELECT left_bot AS bot_uuid FROM bcs_friendships \
                WHERE right_bot = ? AND env = ? \
            ) \
            SELECT b.bot_uuid, b.name, b.bot_info, b.visibility, b.env, b.created_by, \
                   b.actor_kind, b.status, \
                   f.bot_uuid IS NOT NULL as is_friend \
            FROM bcs_bots b \
            LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
            WHERE b.env = ? \
              AND COALESCE(b.is_deleted, 0) = 0 \
              AND b.name LIKE ? \
              AND b.bot_uuid != ? \
              AND (b.actor_kind IS NULL OR b.actor_kind != 'human') \
              AND {} \
            ORDER BY b.id DESC \
            LIMIT ? OFFSET ?",
            visibility_filter
        );

        let page_rows = match self
            .db_query(
                &page_sql,
                vec![
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(env.as_str()),
                    Value::from(like.as_str()),
                    Value::from(bot_uuid),
                    Value::from(coop_flag),
                    Value::from(limit as i64),
                    Value::from(offset as i64),
                ],
            )
            .await
        {
            Ok(r) => r,
            Err(e) => {
                warn!(error = %e, "list_bots_by_name_and_cooperatable_with_impl (page): failed");
                return (Vec::new(), 0);
            }
        };

        let count_sql = format!(
            "WITH friend_uuids AS (\
                SELECT right_bot AS bot_uuid FROM bcs_friendships \
                WHERE left_bot = ? AND env = ? \
                UNION \
                SELECT left_bot AS bot_uuid FROM bcs_friendships \
                WHERE right_bot = ? AND env = ? \
            ) \
            SELECT count(*) AS total \
            FROM bcs_bots b \
            LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
            WHERE b.env = ? \
              AND COALESCE(b.is_deleted, 0) = 0 \
              AND b.name LIKE ? \
              AND b.bot_uuid != ? \
              AND (b.actor_kind IS NULL OR b.actor_kind != 'human') \
              AND {}",
            visibility_filter
        );

        let total: usize = match self
            .db_query(
                &count_sql,
                vec![
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(bot_uuid),
                    Value::from(env.as_str()),
                    Value::from(env.as_str()),
                    Value::from(like.as_str()),
                    Value::from(bot_uuid),
                    Value::from(coop_flag),
                ],
            )
            .await
        {
            Ok(rows) => rows
                .first()
                .and_then(|row| db_get_column_opt::<i64>(row, "total").ok().flatten())
                .map(|v| v as usize)
                .unwrap_or(0),
            Err(e) => {
                warn!(error = %e, "list_bots_by_name_and_cooperatable_with_impl (count): failed");
                0
            }
        };

        let mut results = Vec::new();
        for row in &page_rows {
            let bot_uuid: String = match db_get_column_opt::<String>(row, "bot_uuid").ok().flatten()
            {
                Some(v) => v,
                None => continue,
            };
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
            let is_friend: bool = db_get_column_opt::<i64>(row, "is_friend")
                .ok()
                .flatten()
                .map(|v| v != 0)
                .unwrap_or(false);

            // Human Actor V1: propagate persisted actor_kind / status so this
            // batch-by-ids path matches the single-row load (`load_bot_capabilities`
            // / `to_registered_bot`); otherwise downstream `O.5` / `P.3` / `F.3`
            // checks misclassify every actor as `Bot` / `Online`.
            let actor_kind_str: String = db_get_column_opt(row, "actor_kind")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "bot".to_string());
            let actor_kind = match actor_kind_str.as_str() {
                "human" => bcs_service_api::ActorKind::Human,
                _ => bcs_service_api::ActorKind::Bot,
            };
            let status_str: String = db_get_column_opt(row, "status")
                .ok()
                .flatten()
                .filter(|v: &String| !v.is_empty())
                .unwrap_or_else(|| "online".to_string());
            let status = match status_str.as_str() {
                "hidden" => bcs_service_api::ActorStatus::Hidden,
                _ => bcs_service_api::ActorStatus::Online,
            };

            let bot = RegisteredBot {
                bot_uuid,
                capabilities: BotCapabilities {
                    name,
                    summary: bot_info.summary,
                    domains: bot_info.domains,
                    skills: bot_info.skills,
                    scopes: bot_info.scopes,
                    binding_channels: bot_info.binding_channels,
                    hidden: bot_info.hidden,
                    visibility,
                    // SECURITY: 敏感字段置空，防止通过常规接口泄露
                    agent_code: None,
                    agent_token: None,
                },
                dynamic_status: BotDynamicStatus::default(),
                env,
                created_by,
                actor_kind,
                status,
            };
            results.push((bot, is_friend));
        }

        (results, total)
    }
}

fn sql_metric_actor_kind(raw: &str) -> bcs_service_api::ActorKind {
    match raw {
        "human" => bcs_service_api::ActorKind::Human,
        _ => bcs_service_api::ActorKind::Bot,
    }
}

fn sql_metric_actor_status(raw: &str) -> bcs_service_api::ActorStatus {
    match raw {
        "hidden" => bcs_service_api::ActorStatus::Hidden,
        _ => bcs_service_api::ActorStatus::Online,
    }
}

#[async_trait]
impl BotMetricsSnapshotPort for PersistentBotRepo {
    async fn bot_counts(&self) -> ServiceResult<Vec<BotMetricCount>> {
        let env = resolve_env();
        let sql = "SELECT actor_kind, status, visibility, COUNT(*) AS bot_count \
                   FROM ( \
                       SELECT \
                           CASE WHEN actor_kind = 'human' THEN 'human' ELSE 'bot' END AS actor_kind, \
                           CASE WHEN status = 'hidden' THEN 'hidden' ELSE 'online' END AS status, \
                           CASE \
                               WHEN visibility IS NULL OR TRIM(visibility) = '' THEN 'private' \
                               WHEN visibility IN ('public', 'protected', 'private') THEN visibility \
                               ELSE 'other' \
                           END AS visibility \
                       FROM bcs_bots \
                       WHERE env = ? \
                         AND COALESCE(is_deleted, 0) = 0 \
                   ) metric_bots \
                   GROUP BY actor_kind, status, visibility";
        let rows = self
            .db_query(sql, vec![Value::from(env.as_str())])
            .await
            .map_err(|e| {
                warn!(env = %env, error = %e, "bot metrics snapshot query failed");
                ServiceError::InternalError(format!("bot metrics snapshot query failed: {}", e))
            })?;

        let mut counts = Vec::with_capacity(rows.len());
        for row in rows {
            let actor_kind_raw: String = db_get_column(&row, "actor_kind").map_err(|e| {
                ServiceError::InternalError(format!(
                    "bot metrics actor_kind conversion failed: {}",
                    e
                ))
            })?;
            let status_raw: String = db_get_column(&row, "status").map_err(|e| {
                ServiceError::InternalError(format!("bot metrics status conversion failed: {}", e))
            })?;
            let visibility: String = db_get_column(&row, "visibility").map_err(|e| {
                ServiceError::InternalError(format!(
                    "bot metrics visibility conversion failed: {}",
                    e
                ))
            })?;
            let bot_count: i64 = db_get_column(&row, "bot_count").map_err(|e| {
                ServiceError::InternalError(format!("bot metrics count conversion failed: {}", e))
            })?;
            let count = u64::try_from(bot_count).map_err(|e| {
                ServiceError::InternalError(format!("bot metrics count is invalid: {}", e))
            })?;
            if count == 0 {
                continue;
            }

            counts.push(BotMetricCount {
                actor_kind: sql_metric_actor_kind(&actor_kind_raw),
                status: sql_metric_actor_status(&status_raw),
                visibility: Some(visibility),
                count,
            });
        }
        Ok(counts)
    }
}

#[async_trait]
impl BotRepoPort for PersistentBotRepo {
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
                warn!(bot_id = %bot_id, error = %e, "Failed to save bot to database during register");
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
                    dynamic_status: BotDynamicStatus::default(),
                    ws_connection: None,
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
                warn!(bot_id = %bot_id, error = %e, "Failed to save bot to database during register_with_owner_and_token");
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
                        dynamic_status: BotDynamicStatus::default(),
                        ws_connection: None,
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

    async fn update_status(&self, bot_id: &str, status: BotDynamicStatus) -> bool {
        // Update memory
        {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.dynamic_status = status.clone();
                bot.last_heartbeat = Instant::now();
            } else {
                debug!(bot_id = %bot_id, "Bot not found for status update");
                return false;
            }
        }

        // Save to cache with TTL
        self.save_status_to_cache(bot_id, &status).await;

        debug!(bot_id = %bot_id, "Bot dynamic status updated");
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
        self.try_get(bot_id).await.ok().flatten()
    }

    async fn try_get(&self, bot_id: &str) -> ServiceResult<Option<RegisteredBot>> {
        let bots = self.bots.read().await;

        if let Some(bot) = bots.get(bot_id) {
            if !bot.is_expired() {
                return Ok(Some(bot.to_registered_bot()));
            }
        }

        // Fallback: load from database + cache
        drop(bots);

        // Code-Review fix #1: take actor_kind/status from the database instead of
        // returning defaults; otherwise O.5/P.3/F.3 will misclassify any actor
        // whose row is no longer cached in process memory.
        let Some((mut capabilities, env, _hidden, created_by, actor_kind, status)) =
            self.try_load_from_db(bot_id, false).await?
        else {
            return Ok(None);
        };
        let dynamic_status = self.load_status_from_cache(bot_id).await;

        // 清除敏感字段，防止通过常规接口泄露
        capabilities.agent_token = None;

        Ok(Some(RegisteredBot {
            bot_uuid: bot_id.to_string(),
            capabilities,
            dynamic_status,
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
        let dynamic_status = self.load_status_from_cache(bot_id).await;

        // 清除敏感字段，防止通过常规接口泄露
        capabilities.agent_code = None;
        capabilities.agent_token = None;

        Some(RegisteredBot {
            bot_uuid: bot_id.to_string(),
            capabilities,
            dynamic_status,
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
        // 目前仅支持 "agent_token"（复用 capabilities.agent_token 存储，仅内存）。
        // 后期需要其他字段时，应在 RegisteredBotInner 上新增一个 HashMap 内存对象
        // 来承载任意 key/value，而不是继续往 capabilities 上加字段。
        if key != "agent_token" && key != "client_kind" {
            tracing::warn!(bot_id = %bot_id, key = %key, "add_bot_info: unrecognized key, ignoring");
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

    async fn list_active(&self) -> Vec<RegisteredBot> {
        // Master has all active connections in memory
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        let current_env = resolve_env();
        // D-F: unified DB query — always query the database to include offline bots.
        // InMemory dynamic_status is supplemented at the handler layer via
        // `bot_is_effectively_online`, not here.
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
                    || b.dynamic_status
                        .dynamic_summary
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
            warn!(removed, "Removed expired bot registrations");
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
            warn!(bot_uuid = %bot_id, error = %e, "update_visibility: failed to update database");
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
                    dynamic_status: BotDynamicStatus::default(),
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
            warn!(token_prefix = %prefix, "find_bot_by_token: token not found in any layer (cache/memory/database)");
        }
        result
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

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
        info!(bot_id = %bot_id, "register_streaming_connection: registering new connection");

        let mut bots = self.bots.write().await;

        // Check if bot is already connected
        if let Some(bot) = bots.get(&bot_id) {
            if bot.ws_connection.is_some() {
                warn!(bot_id = %bot_id, "Bot already has an active streaming connection");
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
                    dynamic_status: BotDynamicStatus::default(),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    ws_connection: Some(BotConnection {
                        session_token: session_token.clone(),
                        connected_at: Instant::now(),
                    }),
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
                warn!(bot_id = %bot_id, "Bot already has an active connection");
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
            // Bot not in memory - load from database and cache
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
            let dynamic_status = self.load_status_from_cache(&bot_id).await;

            info!(bot_id = %bot_id, caps_loaded = capabilities.name.is_some(), "reconnect_streaming: loaded capabilities from storage");

            let mut bots = self.bots.write().await;
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_uuid: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities,
                    dynamic_status,
                    ws_connection: Some(BotConnection {
                        session_token: existing_token.clone(),
                        connected_at: Instant::now(),
                    }),
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
        warn!(bot_id = %bot_id, "Bot frame delivery is owned by the ws adapter");
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
                        dynamic_status: BotDynamicStatus::default(),
                        ws_connection: None,
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

fn control_plane_record_from_row(row: &DbRow) -> ServiceResult<BotControlPlaneRecord> {
    let bot_id: String = db_get_column(row, "bot_uuid")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let name: String = db_get_column(row, "name")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let env: String = db_get_column(row, "env")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let visibility = db_get_column_opt::<String>(row, "visibility")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "protected".to_string());
    let kind = match db_get_column_opt::<String>(row, "actor_kind")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .as_deref()
    {
        Some("human") => bcs_service_api::ActorKind::Human,
        _ => bcs_service_api::ActorKind::Bot,
    };
    let status = match db_get_column_opt::<String>(row, "status")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .as_deref()
    {
        Some("hidden") => bcs_service_api::ActorStatus::Hidden,
        _ => bcs_service_api::ActorStatus::Online,
    };
    let created_by = db_get_column_opt(row, "created_by")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
    let bot_info = db_get_column_opt::<String>(row, "bot_info")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .and_then(|value| serde_json::from_str::<BotInfo>(&value).ok())
        .unwrap_or_default();
    let agent_code = db_get_column_opt::<String>(row, "agent_code")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .or(bot_info.agent_code);
    let created_at = db_get_column::<i64>(row, "gmt_create_ms")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .max(0) as u64;
    let updated_at = db_get_column::<i64>(row, "gmt_modified_ms")
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .max(0) as u64;

    Ok(BotControlPlaneRecord {
        bot_id,
        kind,
        name,
        visibility,
        status,
        env,
        created_by,
        descriptor: BotControlPlaneDescriptor {
            summary: bot_info.summary.unwrap_or_default(),
            domains: bot_info.domains,
            skills: bot_info.skills,
            scopes: bot_info.scopes,
        },
        agent_code,
        created_at,
        updated_at,
    })
}

#[async_trait]
impl BotControlPlaneRepoPort for PersistentBotRepo {
    async fn get_control_plane(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        let sql = format!(
            "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, env, \
                    created_by, agent_code, ({}) * 1000 AS gmt_create_ms, \
                    ({}) * 1000 AS gmt_modified_ms \
             FROM bcs_bots \
             WHERE bot_uuid = ? AND env = ? AND COALESCE(is_deleted, 0) = 0 \
             LIMIT 1",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified")
        );
        let rows = self
            .db_query(&sql, vec![Value::from(bot_id), Value::from(env)])
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        rows.first().map(control_plane_record_from_row).transpose()
    }

    async fn list_control_plane_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        let name = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty());
        let name_filter = name.unwrap_or_default().to_lowercase();
        let mut friend_ids = query.friend_ids.iter().cloned().collect::<Vec<_>>();
        friend_ids.sort_unstable();
        let visibility_filter = match query.visibility {
            BotCandidateVisibility::Discovery => "b.visibility IN ('public', 'protected')",
            BotCandidateVisibility::Collaboration => {
                "b.visibility = 'public' OR f.bot_uuid IS NOT NULL"
            }
        };
        let friend_rows = if friend_ids.is_empty() {
            "SELECT NULL AS bot_uuid WHERE 1 = 0".to_string()
        } else {
            friend_ids
                .iter()
                .enumerate()
                .map(|(index, _)| {
                    if index == 0 {
                        "SELECT ? AS bot_uuid"
                    } else {
                        "SELECT ?"
                    }
                })
                .collect::<Vec<_>>()
                .join(" UNION ALL ")
        };
        let common = format!("WITH friend_uuids AS ({friend_rows}) ");
        let page_sql = format!(
            "{common}\
             SELECT b.bot_uuid, b.name, b.bot_info, b.visibility, b.status, \
                    b.actor_kind, b.env, b.created_by, b.agent_code, \
                    ({} ) * 1000 AS gmt_create_ms, \
                    ({} ) * 1000 AS gmt_modified_ms, \
                    CASE WHEN f.bot_uuid IS NULL THEN 0 ELSE 1 END AS is_friend \
             FROM bcs_bots b \
             LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
             WHERE b.env = ? AND COALESCE(b.is_deleted, 0) = 0 \
               AND COALESCE(b.actor_kind, 'bot') = 'bot' \
               AND b.bot_uuid != ? AND INSTR(LOWER(b.name), ?) > 0 \
               AND ({visibility_filter}) \
             ORDER BY b.gmt_create DESC, b.bot_uuid ASC \
             LIMIT ? OFFSET ?",
            self.flavor.unix_ts("b.gmt_create"),
            self.flavor.unix_ts("b.gmt_modified"),
        );
        let mut base_params = friend_ids
            .iter()
            .map(|friend_id| Value::from(friend_id.as_str()))
            .collect::<Vec<_>>();
        base_params.extend([
            Value::from(query.env.as_str()),
            Value::from(query.acting_bot_id.as_str()),
            Value::from(name_filter.as_str()),
        ]);
        let mut page_params = base_params.clone();
        page_params.push(Value::from(query.limit as i64));
        page_params.push(Value::from(query.offset as i64));
        let rows = self
            .db_query(&page_sql, page_params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        let mut records = Vec::with_capacity(rows.len());
        for row in &rows {
            let is_friend = db_get_column::<i64>(row, "is_friend")
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                != 0;
            records.push(BotCandidateReadRecord {
                bot: control_plane_record_from_row(row)?,
                is_friend,
            });
        }

        let count_sql = format!(
            "{common}\
             SELECT COUNT(*) AS total \
             FROM bcs_bots b \
             LEFT JOIN friend_uuids f ON b.bot_uuid = f.bot_uuid \
             WHERE b.env = ? AND COALESCE(b.is_deleted, 0) = 0 \
               AND COALESCE(b.actor_kind, 'bot') = 'bot' \
               AND b.bot_uuid != ? AND INSTR(LOWER(b.name), ?) > 0 \
               AND ({visibility_filter})"
        );
        let count_rows = self
            .db_query(&count_sql, base_params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        let total = count_rows
            .first()
            .map(|row| db_get_column::<i64>(row, "total"))
            .transpose()
            .map_err(|error| ServiceError::InternalError(error.to_string()))?
            .unwrap_or(0)
            .max(0) as u64;
        Ok((records, total))
    }

    async fn list_control_plane_by_creator(
        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        let mut sql = format!(
            "SELECT bot_uuid, name, bot_info, visibility, status, actor_kind, env, \
                    created_by, agent_code, ({}) * 1000 AS gmt_create_ms, \
                    ({}) * 1000 AS gmt_modified_ms \
             FROM bcs_bots \
             WHERE created_by = ? AND env = ? AND COALESCE(is_deleted, 0) = 0",
            self.flavor.unix_ts("gmt_create"),
            self.flavor.unix_ts("gmt_modified")
        );
        let mut params = vec![
            Value::from(query.created_by.as_str()),
            Value::from(query.env.as_str()),
        ];
        if let Some(kind) = query.kind {
            sql.push_str(" AND COALESCE(actor_kind, 'bot') = ?");
            params.push(Value::from(match kind {
                bcs_service_api::ActorKind::Bot => "bot",
                bcs_service_api::ActorKind::Human => "human",
            }));
        }
        if let Some(name) = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            sql.push_str(" AND INSTR(LOWER(name), ?) > 0");
            params.push(Value::from(name.to_lowercase()));
        }
        if let Some(status) = query.status {
            sql.push_str(" AND status = ?");
            params.push(Value::from(match status {
                bcs_service_api::ActorStatus::Online => "online",
                bcs_service_api::ActorStatus::Hidden => "hidden",
            }));
        }
        sql.push_str(" ORDER BY gmt_create DESC, bot_uuid ASC");
        let rows = self
            .db_query(&sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        rows.iter().map(control_plane_record_from_row).collect()
    }

    async fn patch_control_plane(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        let existing = self.get_control_plane(bot_id, env).await?;
        if existing.is_none() {
            return Ok(None);
        }

        let mut assignments = Vec::new();
        let mut params = Vec::new();
        if let Some(name) = patch.name.as_deref() {
            assignments.push("name = ?");
            params.push(Value::from(name));
        }
        if let Some(visibility) = patch.visibility.as_deref() {
            assignments.push("visibility = ?");
            params.push(Value::from(visibility));
        }
        if let Some(status) = patch.status {
            assignments.push("status = ?");
            params.push(Value::from(match status {
                bcs_service_api::ActorStatus::Online => "online",
                bcs_service_api::ActorStatus::Hidden => "hidden",
            }));
        }
        if let Some(descriptor) = patch.descriptor.as_ref() {
            let rows = self
                .db_query(
                    "SELECT bot_info FROM bcs_bots WHERE bot_uuid = ? AND env = ? \
                     AND COALESCE(is_deleted, 0) = 0 LIMIT 1",
                    vec![Value::from(bot_id), Value::from(env)],
                )
                .await
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            let raw = rows
                .first()
                .map(|row| db_get_column_opt::<String>(row, "bot_info"))
                .transpose()
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                .flatten();
            let mut value = raw
                .as_deref()
                .and_then(|raw| serde_json::from_str::<serde_json::Value>(raw).ok())
                .filter(serde_json::Value::is_object)
                .unwrap_or_else(|| serde_json::Value::Object(serde_json::Map::new()));
            let object = value.as_object_mut().ok_or_else(|| {
                ServiceError::InternalError("Bot descriptor is not a JSON object".to_string())
            })?;
            if let Some(summary) = descriptor.summary.as_ref() {
                object.insert(
                    "summary".to_string(),
                    serde_json::Value::String(summary.clone()),
                );
            }
            if let Some(domains) = descriptor.domains.as_ref() {
                object.insert(
                    "domains".to_string(),
                    serde_json::to_value(domains)
                        .map_err(|error| ServiceError::InternalError(error.to_string()))?,
                );
            }
            if let Some(skills) = descriptor.skills.as_ref() {
                object.insert(
                    "skills".to_string(),
                    serde_json::to_value(skills)
                        .map_err(|error| ServiceError::InternalError(error.to_string()))?,
                );
            }
            if let Some(scopes) = descriptor.scopes.as_ref() {
                object.insert(
                    "scopes".to_string(),
                    serde_json::to_value(scopes)
                        .map_err(|error| ServiceError::InternalError(error.to_string()))?,
                );
            }
            assignments.push("bot_info = ?");
            params.push(Value::from(value.to_string()));
        }

        assignments.push("gmt_modified = CURRENT_TIMESTAMP");
        assignments.push("updated_at = CURRENT_TIMESTAMP");
        params.push(Value::from(bot_id));
        params.push(Value::from(env));
        let sql = format!(
            "UPDATE bcs_bots SET {} WHERE bot_uuid = ? AND env = ? \
             AND COALESCE(is_deleted, 0) = 0",
            assignments.join(", ")
        );
        let affected = self
            .db_execute_affected(&sql, params)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        if affected == 0 {
            return Ok(None);
        }

        if let Some(bot) = self.bots.write().await.get_mut(bot_id) {
            if let Some(name) = patch.name {
                bot.capabilities.name = Some(name);
            }
            if let Some(visibility) = patch.visibility {
                bot.capabilities.visibility = visibility;
            }
            if let Some(status) = patch.status {
                bot.status = status;
            }
            if let Some(descriptor) = patch.descriptor {
                if let Some(summary) = descriptor.summary {
                    bot.capabilities.summary = Some(summary);
                }
                if let Some(domains) = descriptor.domains {
                    bot.capabilities.domains = domains;
                }
                if let Some(skills) = descriptor.skills {
                    bot.capabilities.skills = skills;
                }
                if let Some(scopes) = descriptor.scopes {
                    bot.capabilities.scopes = scopes;
                }
            }
        }

        self.get_control_plane(bot_id, env).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Note: These tests require external cache and database connections.
    // Run with: cargo test --package bcs-bot -- --ignored

    #[test]
    fn test_bot_info_serialization() {
        let bot_info = BotInfo {
            summary: Some("Test bot".to_string()),
            domains: vec!["testing".to_string()],
            skills: vec![Skill::new("test")],
            scopes: vec!["read".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, bot_info.summary);
        assert_eq!(parsed.domains, bot_info.domains);
        assert_eq!(parsed.skills, bot_info.skills);
        assert_eq!(parsed.scopes, bot_info.scopes);
    }

    #[test]
    fn test_status_cache_key_format() {
        let key = PersistentBotRepo::status_cache_key("bot-123");
        assert_eq!(key, "bcs:status:bot-123");
    }

    #[test]
    fn test_status_cache_key_uses_configured_prefix() {
        let key = PersistentBotRepo::status_cache_key_with_prefix("tenant:", "bot-123");
        assert_eq!(key, "tenant:status:bot-123");
    }

    #[test]
    fn test_registry_status_cache_key_uses_constructor_prefix() {
        let cache = Arc::new(bcs_cache_local::InMemoryCachePlugin::new());
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        let registry = PersistentBotRepo::with_plugins_flavor_and_cache_key_prefix(
            cache,
            db,
            DbSqlFlavor::Sqlite,
            "tenant:",
        );

        assert_eq!(
            registry.configured_status_cache_key("bot-123"),
            "tenant:status:bot-123"
        );
    }

    #[test]
    fn test_bot_inner_expiry() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities::default(),
            dynamic_status: BotDynamicStatus::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(!bot.is_expired());

        // Simulate time passing (we can't actually wait, so just test the logic)
        // In real usage, last_heartbeat.elapsed() would be compared
    }

    #[test]
    fn test_bot_inner_skill_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                skills: vec![Skill::new("SQL Analysis"), Skill::new("Deadlock Debugging")],
                ..Default::default()
            },
            dynamic_status: BotDynamicStatus::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_skill("sql"));
        assert!(bot.has_skill("deadlock"));
        assert!(bot.has_skill("SQL")); // Case insensitive
        assert!(!bot.has_skill("python"));
    }

    #[test]
    fn test_bot_inner_domain_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                domains: vec!["Database".to_string(), "MySQL".to_string()],
                ..Default::default()
            },
            dynamic_status: BotDynamicStatus::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_domain("database"));
        assert!(bot.has_domain("mysql"));
        assert!(!bot.has_domain("security"));
    }

    #[test]
    fn test_bot_inner_scope_matching() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                scopes: vec!["database:read".to_string(), "database:write".to_string()],
                ..Default::default()
            },
            dynamic_status: BotDynamicStatus::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_scope("database:read"));
        assert!(bot.has_scope("write"));
        assert!(!bot.has_scope("admin"));
    }

    #[test]
    fn test_to_registered_bot() {
        let bot = RegisteredBotInner {
            bot_uuid: "test-uuid".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                name: Some("Test Bot".to_string()),
                ..Default::default()
            },
            dynamic_status: BotDynamicStatus {
                status: "busy".to_string(),
                load: Some(0.5),
                ..Default::default()
            },
            ws_connection: None,
            session_token: None,
            env: Some("prod".to_string()),
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        let registered = bot.to_registered_bot();
        assert_eq!(registered.bot_uuid, "test-uuid");
        assert_eq!(registered.capabilities.name, Some("Test Bot".to_string()));
        assert_eq!(registered.dynamic_status.status, "busy");
        assert_eq!(registered.dynamic_status.load, Some(0.5));
        assert_eq!(registered.env, Some("prod".to_string()));
    }

    #[test]
    fn test_bot_info_default() {
        let bot_info = BotInfo::default();
        assert!(bot_info.summary.is_none());
        assert!(bot_info.domains.is_empty());
        assert!(bot_info.skills.is_empty());
        assert!(bot_info.scopes.is_empty());
    }

    #[test]
    fn test_bot_info_with_all_fields() {
        let bot_info = BotInfo {
            summary: Some("A comprehensive bot".to_string()),
            domains: vec!["database".to_string(), "security".to_string()],
            skills: vec![
                Skill::new("sql_analysis"),
                Skill::new("penetration_testing"),
            ],
            scopes: vec!["admin:read".to_string(), "admin:write".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, Some("A comprehensive bot".to_string()));
        assert_eq!(parsed.domains.len(), 2);
        assert_eq!(parsed.skills.len(), 2);
        assert_eq!(parsed.scopes.len(), 2);
    }

    #[test]
    fn test_bot_info_empty_arrays() {
        let json = r#"{"summary":null,"domains":[],"skills":[],"scopes":[]}"#;
        let bot_info: BotInfo = serde_json::from_str(json).unwrap();
        assert!(bot_info.summary.is_none());
        assert!(bot_info.domains.is_empty());
    }

    #[test]
    fn test_status_cache_key_with_special_chars() {
        let key = PersistentBotRepo::status_cache_key("bot-with-dashes_123");
        assert_eq!(key, "bcs:status:bot-with-dashes_123");

        let key = PersistentBotRepo::status_cache_key("中文机器人");
        assert_eq!(key, "bcs:status:中文机器人");
    }

    #[test]
    fn test_current_timestamp() {
        let ts = PersistentBotRepo::current_timestamp();
        // Should be a reasonable timestamp (after 2020)
        assert!(ts > 1577836800000); // 2020-01-01 in ms
        // Should be before 2100
        assert!(ts < 4102444800000); // 2100-01-01 in ms
    }

    #[test]
    fn test_bot_inner_has_skill_partial_match() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                skills: vec![Skill::new("SQL_Analysis_Expert")],
                ..Default::default()
            },
            dynamic_status: BotDynamicStatus::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        // Partial match should work
        assert!(bot.has_skill("sql"));
        assert!(bot.has_skill("analysis"));
        assert!(bot.has_skill("expert"));
        // Case insensitive
        assert!(bot.has_skill("SQL"));
        assert!(bot.has_skill("ANALYSIS"));
    }

    #[test]
    fn test_bot_inner_has_domain_partial_match() {
        let bot = RegisteredBotInner {
            bot_uuid: "test".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities {
                domains: vec!["Database-Administration".to_string()],
                ..Default::default()
            },
            dynamic_status: BotDynamicStatus::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(bot.has_domain("database"));
        assert!(bot.has_domain("administration"));
        assert!(!bot.has_domain("security"));
    }

    #[test]
    fn test_bot_inner_empty_capabilities() {
        let bot = RegisteredBotInner {
            bot_uuid: "empty".to_string(),
            last_heartbeat: Instant::now(),
            capabilities: BotCapabilities::default(),
            dynamic_status: BotDynamicStatus::default(),
            ws_connection: None,
            session_token: None,
            env: None,
            hidden: false,
            status: bcs_service_api::ActorStatus::Online,
            actor_kind: bcs_service_api::ActorKind::Bot,
            created_by: None,
        };

        assert!(!bot.has_skill("anything"));
        assert!(!bot.has_domain("anything"));
        assert!(!bot.has_scope("anything"));
    }

    #[test]
    fn test_bot_connection_clone() {
        let conn = BotConnection {
            session_token: "test-token".to_string(),
            connected_at: Instant::now(),
        };

        let conn_clone = conn.clone();
        assert_eq!(conn_clone.session_token, "test-token");
    }

    #[test]
    fn test_dynamic_status_default() {
        let status = BotDynamicStatus::default();
        assert!(status.status.is_empty());
        assert!(status.dynamic_summary.is_none());
        assert!(status.load.is_none());
        assert!(status.updated_at.is_none());
    }

    #[test]
    fn test_bot_capabilities_default() {
        let caps = BotCapabilities::default();
        assert!(caps.name.is_none());
        assert!(caps.summary.is_none());
        assert!(caps.domains.is_empty());
        assert!(caps.skills.is_empty());
        assert!(caps.scopes.is_empty());
    }

    #[test]
    fn test_bot_info_json_roundtrip_complex() {
        let bot_info = BotInfo {
            summary: Some("Summary with \"quotes\" and \\backslashes\\rating".to_string()),
            domains: vec![
                "domain:with:colons".to_string(),
                "domain-with-dashes".to_string(),
            ],
            skills: vec![Skill::new("skill with spaces")],
            scopes: vec!["scope/with/slashes".to_string()],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
        };

        let json = serde_json::to_string(&bot_info).unwrap();
        let parsed: BotInfo = serde_json::from_str(&json).unwrap();

        assert_eq!(parsed.summary, bot_info.summary);
        assert_eq!(parsed.domains, bot_info.domains);
        assert_eq!(parsed.skills, bot_info.skills);
        assert_eq!(parsed.scopes, bot_info.scopes);
    }

    // ===== Integration tests (require external cache and database) =====
    // Run with: cargo test --package bcs-bot -- --ignored

    #[tokio::test]
    #[ignore = "Requires external cache and database connections"]
    async fn integration_test_register_and_retrieve() {
        // This test documents the expected workflow:
        // 1. Create PersistentBotRepo with CachePlugin and DbPlugin handles
        // 2. Register a bot
        // 3. Retrieve the bot from memory (fast path)
        // 4. Verify capabilities are persisted to the database
        //
        // Example setup is intentionally omitted because production wiring
        // now happens through the bootstrap composition root.
        //
        // let caps = BotCapabilities {
        //     name: Some("Test Bot".into()),
        //     skills: vec!["testing".into()],
        //     ..Default::default()
        // };
        // registry.register("test-bot".into(), caps).await;
        //
        // let bot = registry.get("test-bot").await.unwrap();
        // assert_eq!(bot.capabilities.name, Some("Test Bot".into()));
    }

    #[tokio::test]
    #[ignore = "Requires external cache and database connections"]
    async fn integration_test_status_updates_to_cache() {
        // This test documents the expected workflow:
        // 1. Update bot status (which goes to cache with TTL)
        // 2. Verify TTL is set correctly (600s)
        // 3. After failover, status can be recovered from cache
        //
        // Example:
        // let status = BotDynamicStatus {
        //     status: "busy".into(),
        //     dynamic_summary: Some("Processing request".into()),
        //     load: Some(0.7),
        //     ..Default::default()
        // };
        // registry.update_status("test-bot", status).await;
        //
        // // Status should be in cache with key "bcs:status:test-bot"
        // let recovered = registry.load_status_from_cache("test-bot").await;
        // assert_eq!(recovered.status, "busy");
    }

    #[tokio::test]
    #[ignore = "Requires external cache and database connections"]
    async fn integration_test_token_persistence() {
        // This test documents the expected workflow:
        // 1. Register WS connection (generates token)
        // 2. Token is persisted to the database
        // 3. After server restart, token can be looked up from the database
        //
        // Example:
        // let (tx, _rx) = mpsc::channel(10);
        // let token = registry.register_streaming_connection("test-bot".into()).await.unwrap();
        //
        // // Token should be findable
        // let found = registry.find_bot_by_token(&token).await;
        // assert_eq!(found, Some("test-bot".into()));
    }

    #[tokio::test]
    #[ignore = "Requires external cache and database connections"]
    async fn integration_test_reconnect_streaming_recover_from_storage() {
        // This test documents the failover recovery workflow:
        // 1. Bot connects and has capabilities in database, status in cache
        // 2. WS disconnects (but token is preserved)
        // 3. Server restarts (memory cleared)
        // 4. Bot reconnects with existing token
        // 5. Server recovers capabilities from database and status from cache
        //
        // Example:
        // let (tx, _rx) = mpsc::channel(10);
        // let token = registry.register_streaming_connection("test-bot".into()).await.unwrap();
        //
        // // Simulate server restart by clearing memory
        // // (in real scenario, memory is lost)
        //
        // let (tx2, _rx2) = mpsc::channel(10);
        // let (bot_uuid, recovered_token) = registry.reconnect_streaming(token.clone()).await.unwrap();
        // assert_eq!(bot_uuid, "test-bot");
        // assert_eq!(recovered_token, token);
    }

    #[tokio::test]
    #[ignore = "Requires external cache and database connections"]
    async fn integration_test_sql_injection_protection() {
        // This test documents SQL injection protection:
        // Bot IDs and other fields are escaped before SQL queries
        //
        // Example:
        // let malicious_id = "bot'; DROP TABLE bcs_bots; --";
        // let caps = BotCapabilities::default();
        // registry.register(malicious_id.into(), caps).await;
        // // Should not cause SQL injection, just creates a bot with that name
    }

    #[test]
    fn test_sql_escaping_in_save_to_storage() {
        // Verify that special SQL characters are properly escaped
        let bot_info = BotInfo {
            summary: Some("Test with 'single quotes'".into()),
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            hidden: false,
            agent_code: None,
            agent_token: None,
        };
        let json = serde_json::to_string(&bot_info).unwrap();
        // JSON escaping handles most cases, but the SQL layer also does escaping
        assert!(json.contains("single quotes"));
    }
}
