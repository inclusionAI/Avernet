//! Plugin-backed bot repository implementation.
//!
//! This module provides a bot repository backed by:
//! - **Database plugin**: Persistent storage for bot capabilities and tokens
//! - **Process Memory**: streaming connection state and heartbeat tracking
//!
//! # Architecture
//!
//! ```text
//! Layer 1 (Memory):    ws_connection, last_heartbeat, token_to_bot mapping
//! Layer 2 (Database):  bot_info JSON, session_token, name
//! ```

#[cfg(test)]
use bcs_service_api::BotDynamicStatus;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tokio::sync::{RwLock, oneshot};
use tracing::{debug, info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_db_api::{
    DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue as Value, db_get_column, db_get_column_opt,
};
use bcs_service_api::{
    BindingChannels, BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotSearchCandidateQuery, BotSearchFriendshipFilter,
    BotCapabilities, BotControlPlaneDescriptor, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotTaskModesQuery, TaskModeMatch,
    BotMetricCount,
    BotMetricsSnapshotPort, ConnectStreamError, RegisteredBot, ServiceError, ServiceResult, Skill,
};

fn log_bot_cache_source(source: &'static str) {
    bcs_observability::count("bot.memory", source);
    debug!(target: "bcs_observation", request_id = %bcs_observability::current_request_id(), source, "bot.load.source");
}

pub mod memory;
pub mod provider;
pub mod provider_cache;

#[cfg(test)]
#[path = "../tests/unit/heartbeat.rs"]
mod heartbeat_tests;

pub use bcs_service_api::port::repo::BotRepoPort;
pub use memory::MemoryBotRepo;
pub use provider::{DbProviderStore, MemoryProviderStore};

/// Maximum time before a bot registration expires (5 minutes).
const BOT_EXPIRY: Duration = Duration::from_secs(300);

fn bot_info_json_set(
    updates: Vec<(&'static str, serde_json::Value)>,
) -> ServiceResult<(String, Vec<Value>)> {
    let mut expressions = Vec::with_capacity(updates.len());
    let mut params = Vec::with_capacity(updates.len());
    for (path, value) in updates {
        expressions.push(format!("'{path}', json_extract(?, '$')"));
        params.push(Value::from(serde_json::to_string(&value).map_err(|error| {
            ServiceError::InternalError(error.to_string())
        })?));
    }
    Ok((
        format!(
            "bot_info = JSON_SET(\
             CASE WHEN JSON_VALID(bot_info) AND LOWER(JSON_TYPE(bot_info)) = 'object' \
                  THEN bot_info ELSE JSON_OBJECT() END, {})",
            expressions.join(", ")
        ),
        params,
    ))
}

fn serialized_string_value<T: Serialize>(value: &T) -> ServiceResult<String> {
    serde_json::to_value(value)
        .map_err(|error| ServiceError::InternalError(error.to_string()))?
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| ServiceError::InternalError("expected a serialized string value".to_string()))
}

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
            env: self.env.clone(),
            created_by: self.created_by.clone(),
            actor_kind: self.actor_kind,
            status: self.status,
        }
    }
}

/// Persistent bot repository backed by a DB plugin.
///
/// Process memory holds WebSocket connections and heartbeat timestamps;
/// the database holds persistent capabilities and tokens. Heartbeat payloads
/// are neither retained nor written to an external cache.
pub struct PersistentBotRepo {
    identity_locks: admission::IdentityLocks,
    // Layer 1: Process Memory
    /// Bot connections and state.
    bots: RwLock<HashMap<String, RegisteredBotInner>>,
    /// Token to bot_uuid mapping (hot cache for auth).
    token_to_bot: RwLock<HashMap<String, String>>,
    /// Channel binding index: (channel, binding_key) -> bot_uuid.
    binding_channel_index: Arc<RwLock<HashMap<(String, String), String>>>,
    /// Process-local runtime info, e.g. client_kind from bot.connect.
    bot_info_overrides: RwLock<HashMap<(String, String), String>>,

    // Layer 2: Database
    /// DB plugin for persistent storage.
    db: Arc<dyn DbPlugin>,

    /// SQL dialect flavor.
    flavor: DbSqlFlavor,

    /// Pending request-response channels for send_request.
    pending_requests: RwLock<HashMap<String, oneshot::Sender<serde_json::Value>>>,
}

impl PersistentBotRepo {
    /// Create a DB-backed repository using the MySQL dialect.
    pub fn new(db: Arc<dyn DbPlugin>) -> Self {
        Self::with_sql_flavor(db, DbSqlFlavor::Mysql)
    }

    /// Create a DB-backed repository with the selected SQL flavor.
    pub fn with_sql_flavor(
        db: Arc<dyn DbPlugin>,
        flavor: DbSqlFlavor,
    ) -> Self {
        Self {
            identity_locks: admission::IdentityLocks::default(),
            bots: RwLock::new(HashMap::new()),
            token_to_bot: RwLock::new(HashMap::new()),
            binding_channel_index: Arc::new(RwLock::new(HashMap::new())),
            bot_info_overrides: RwLock::new(HashMap::new()),
            db,
            flavor,
            pending_requests: RwLock::new(HashMap::new()),
        }
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






#[cfg(test)]
mod tests;
mod control_plane;
mod registration;
mod reads;
mod persistence;
mod human;
mod tokens;
mod streaming;
mod repo;
mod metrics;
mod database;
mod discovery;

mod admission;
mod admission_sql;

#[cfg(test)]
#[path = "../tests/unit/streaming_admission.rs"]
mod streaming_admission_tests;
