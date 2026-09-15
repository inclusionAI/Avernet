//! In-memory bot repository implementation.
//!
//! Provides local bot persistence, discovery, and streaming connection state.

#[cfg(test)]
use bcs_service_api::BotDynamicStatus;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tokio::fs;
use tokio::sync::{Mutex, RwLock, oneshot};
use tracing::{debug, info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{
    BindingChannels, BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotSearchCandidateQuery, BotSearchFriendshipFilter,
    BotCapabilities, BotControlPlaneDescriptor, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotTaskModesQuery, TaskModeMatch,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotMetricCount,
    BotMetricsSnapshotPort, ConnectStreamError, FriendCheckInStrategy, RegisteredBot, ServiceError,
    ServiceResult, Skill, UserVisibility,
};

fn unix_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

/// Check whether a `bot_uuid` has the form `{namespace}:{staff_no}` where
/// `namespace` is one of the whitelisted legacy patterns:
///
/// - `"default"`
/// - `"{yyyymmdd}_{8 lowercase-alphanumeric chars}"` (exactly 17 chars with `_` at position 8)
///
/// Hand-written byte-level check — no `regex` / `once_cell` dependency needed.
pub(crate) fn is_legacy_namespace(bot_uuid: &str, staff_no: &str) -> bool {
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

/// Maximum time before a bot registration expires (5 minutes).
const BOT_EXPIRY: Duration = Duration::from_secs(300);

/// Bot info file name.
const BOT_INFO_FILE: &str = "bot.json";

/// A bot streaming connection marker.
#[derive(Debug, Clone)]
pub struct BotConnection {
    /// Session token for authentication.
    pub session_token: String,
    /// When the connection was established.
    pub connected_at: Instant,
}

/// In-memory implementation of [`BotRepoPort`].
#[derive(Debug)]
pub struct MemoryBotRepo {
    identity_locks: crate::admission::IdentityLocks,
    bots: RwLock<BTreeMap<String, RegisteredBotInner>>,
    /// Serializes control-plane snapshot merges through persistence and memory.
    control_plane_patch_lock: Mutex<()>,
    /// Audit timestamps for the local control-plane projection.
    control_plane_audit: RwLock<HashMap<String, (u64, u64)>>,
    /// Token to bot_uuid mapping for authentication.
    /// Tokens persist across streaming disconnects for reconnection.
    token_to_bot: RwLock<HashMap<String, String>>,
    /// Soft-deleted bot IDs hidden from default read/token paths.
    deleted_bot_ids: RwLock<HashSet<String>>,
    /// Channel binding index: (channel, binding_key) -> bot_uuid
    /// Derived from bot capabilities for fast lookup.
    binding_channel_index: RwLock<HashMap<(String, String), String>>,
    /// Process-local runtime info, e.g. client_kind from bot.connect.
    bot_info_overrides: RwLock<HashMap<(String, String), String>>,
    /// Base directory for bot files (from BCS_DATA_DIR).
    bots_base_dir: PathBuf,
    /// Pending one-shot request-response channels: request_id -> oneshot sender.
    pending_requests: RwLock<HashMap<String, oneshot::Sender<serde_json::Value>>>,
    /// Control-plane task-mode toggles: (`task_claim_mode`, `task_dream_mode`).
    task_modes: RwLock<HashMap<String, (bool, bool)>>,
}

/// Persisted capabilities format.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct PersistedCapabilities {
    bot_id: String,
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    summary: Option<String>,
    #[serde(default)]
    domains: Vec<String>,
    #[serde(default, deserialize_with = "bcs_service_api::deserialize_skills")]
    skills: Vec<Skill>,
    #[serde(default)]
    scopes: Vec<String>,
    /// Channel bindings for message routing.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    binding_channels: Option<BindingChannels>,
    #[serde(default)]
    token: Option<String>,
    registered_at: u64,
    /// DEPRECATED: Hidden mechanism removed in Rev-4. Retained for deserialization compatibility.
    #[serde(default)]
    hidden: bool,
    /// Creator's staff_no (set during onboard, immutable).
    #[serde(default)]
    created_by: Option<String>,
    /// Bot visibility (e.g. "private", "protected", "public").
    #[serde(default)]
    visibility: Option<String>,
    /// AI安全网关agent_code
    #[serde(default, skip_serializing_if = "Option::is_none")]
    agent_code: Option<String>,
    /// AI安全网关授权token
    #[serde(default, skip_serializing_if = "Option::is_none")]
    agent_token: Option<String>,
    #[serde(default)]
    user_visibility: UserVisibility,
    #[serde(default)]
    friend_ext: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    friend_check_in_strategy: FriendCheckInStrategy,
}

impl From<&PersistedCapabilities> for BotCapabilities {
    fn from(p: &PersistedCapabilities) -> Self {
        Self {
            name: p.name.clone(),
            summary: p.summary.clone(),
            domains: p.domains.clone(),
            skills: p.skills.clone(),
            scopes: p.scopes.clone(),
            binding_channels: p.binding_channels.clone(),
            hidden: false,
            visibility: p
                .visibility
                .clone()
                .unwrap_or_else(|| String::from("protected")),
            agent_code: p.agent_code.clone(),
            agent_token: p.agent_token.clone(),
        }
    }
}

/// Internal representation with last heartbeat and optional streaming connection.
#[derive(Debug)]
struct RegisteredBotInner {
    /// Bot unique identifier (UUID).
    bot_id: String,
    /// Last heartbeat timestamp.
    last_heartbeat: Instant,
    /// Bot capabilities for discovery.
    capabilities: BotCapabilities,
    /// Active streaming connection (if connected).
    ws_connection: Option<BotConnection>,
    /// Session token (persists across connections for reconnection).
    session_token: Option<String>,
    /// Server environment (prod, gray, pre, dev).
    env: Option<String>,
    /// Actor-level lifecycle status (`Online` / `Hidden`) — Task P.2 / Requirement 3.16.
    status: bcs_service_api::ActorStatus,
    /// Actor kind (Bot / Human) — Human Actor V1 / Requirement 3.1.
    /// Code-Review fix #1: persist actor kind in the in-memory registry so
    /// `to_registered_bot()` can propagate it to callers (O.5 / P.3 / F.3).
    actor_kind: bcs_service_api::ActorKind,
    /// Creator's staff_no (set during onboard, immutable).
    created_by: Option<String>,
    /// Protocol version negotiated during bot.connect.
    protocol_version: u32,
    user_visibility: UserVisibility,
    friend_ext: serde_json::Map<String, serde_json::Value>,
    friend_check_in_strategy: FriendCheckInStrategy,
}

impl RegisteredBotInner {
    /// Check if this bot registration has expired.
    fn is_expired(&self) -> bool {
        if self.ws_connection.is_none() && self.session_token.is_some() {
            return false;
        }
        self.last_heartbeat.elapsed() > BOT_EXPIRY
    }

    /// Check if this bot has a specific skill (case-insensitive partial match).
    fn has_skill(&self, skill: &str) -> bool {
        self.capabilities
            .skills
            .iter()
            .any(|s| s.name.to_lowercase().contains(&skill.to_lowercase()))
    }

    /// Check if this bot has a specific domain.
    fn has_domain(&self, domain: &str) -> bool {
        self.capabilities
            .domains
            .iter()
            .any(|d| d.to_lowercase().contains(&domain.to_lowercase()))
    }

    /// Check if this bot has a specific scope.
    fn has_scope(&self, scope: &str) -> bool {
        self.capabilities
            .scopes
            .iter()
            .any(|s| s.to_lowercase().contains(&scope.to_lowercase()))
    }

    /// Convert to public RegisteredBot type.
    ///
    /// Human Actor V1 / Code-Review fix #1: propagate the in-memory
    /// `actor_kind` and `status` instead of returning defaults; otherwise the
    /// in-memory registry will report every actor as `Bot` / `Online` even
    /// when `ensure_human_actor` / `update_actor_status` flipped them.
    fn to_registered_bot(&self) -> RegisteredBot {
        // 清除敏感字段，防止通过常规接口泄露
        let mut capabilities = self.capabilities.clone();
        capabilities.agent_token = None;

        RegisteredBot {
            bot_uuid: self.bot_id.clone(),
            capabilities,
            env: self.env.clone().or_else(|| Some(resolve_env())),
            created_by: self.created_by.clone(),
            actor_kind: self.actor_kind,
            status: self.status,
        }
    }
}

impl MemoryBotRepo {
    /// Create a new bot registry.
    pub fn new() -> Self {
        Self::default()
    }

    /// Create a new bot registry with a base directory for persistence.
    pub fn with_base_dir(bots_base_dir: PathBuf) -> Self {
        Self {
            identity_locks: crate::admission::IdentityLocks::default(),
            bots: RwLock::new(BTreeMap::new()),
            control_plane_patch_lock: Mutex::new(()),
            control_plane_audit: RwLock::new(HashMap::new()),
            token_to_bot: RwLock::new(HashMap::new()),
            deleted_bot_ids: RwLock::new(HashSet::new()),
            binding_channel_index: RwLock::new(HashMap::new()),
            bot_info_overrides: RwLock::new(HashMap::new()),
            bots_base_dir,
            pending_requests: RwLock::new(HashMap::new()),
            task_modes: RwLock::new(HashMap::new()),
        }
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
                debug!(
                    bot_id = %bot_id,
                    channel = %channel,
                    binding_key = %binding.binding_key,
                    "Binding channel index updated"
                );
            }
        }
    }

    /// Get the path to the bot info file for a bot.
    fn bot_info_path(&self, bot_id: &str) -> PathBuf {
        self.bots_base_dir.join(bot_id).join(BOT_INFO_FILE)
    }

    /// Load capabilities from disk for a bot.
    async fn load_capabilities_from_disk(&self, bot_id: &str) -> Option<BotCapabilities> {
        let path = self.bot_info_path(bot_id);
        match fs::read_to_string(&path).await {
            Ok(content) => match serde_json::from_str::<PersistedCapabilities>(&content) {
                Ok(persisted) => {
                    debug!(bot_id = %bot_id, "Loaded capabilities from disk");
                    Some(BotCapabilities::from(&persisted))
                }
                Err(e) => {
                    warn!(request_id = %bcs_observability::CurrentRequestId, bot_id = %bot_id, error = %e, "Failed to parse capabilities file");
                    None
                }
            },
            Err(e) => {
                debug!(bot_id = %bot_id, error = %e, "No capabilities file found");
                None
            }
        }
    }

    /// Save capabilities to disk for a bot.
    async fn save_capabilities_to_disk(
        &self,
        bot_id: &str,
        caps: &BotCapabilities,
    ) -> ServiceResult<()> {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        // Preserve existing token if any
        let existing_token = self.load_token(bot_id).await;

        // Preserve created_by from memory
        let created_by = {
            let bots = self.bots.read().await;
            bots.get(bot_id).and_then(|b| b.created_by.clone())
        };
        let internal_attributes = {
            let bots = self.bots.read().await;
            bots.get(bot_id)
                .map(|bot| {
                    (
                        bot.user_visibility,
                        bot.friend_ext.clone(),
                        bot.friend_check_in_strategy,
                    )
                })
                .unwrap_or_default()
        };

        let persisted = PersistedCapabilities {
            bot_id: bot_id.to_string(),
            name: caps.name.clone(),
            summary: caps.summary.clone(),
            domains: caps.domains.clone(),
            skills: caps.skills.clone(),
            scopes: caps.scopes.clone(),
            binding_channels: caps.binding_channels.clone(),
            token: existing_token,
            registered_at: now,
            hidden: false,
            created_by,
            visibility: if caps.visibility.is_empty() {
                None
            } else {
                Some(caps.visibility.clone())
            },
            agent_code: caps.agent_code.clone(),
            agent_token: caps.agent_token.clone(),
            user_visibility: internal_attributes.0,
            friend_ext: internal_attributes.1,
            friend_check_in_strategy: internal_attributes.2,
        };

        let path = self.bot_info_path(bot_id);
        let dir = path.parent().ok_or_else(|| {
            ServiceError::InternalError(format!("Invalid path for bot: {}", bot_id))
        })?;

        // Create directory if it doesn't exist
        fs::create_dir_all(dir).await?;

        let content = serde_json::to_string_pretty(&persisted)?;
        fs::write(&path, content).await?;

        info!(bot_id = %bot_id, path = ?path, "Saved capabilities to disk");
        Ok(())
    }
}

impl Default for MemoryBotRepo {
    fn default() -> Self {
        Self {
            identity_locks: crate::admission::IdentityLocks::default(),
            bots: RwLock::new(BTreeMap::new()),
            control_plane_patch_lock: Mutex::new(()),
            control_plane_audit: RwLock::new(HashMap::new()),
            token_to_bot: RwLock::new(HashMap::new()),
            deleted_bot_ids: RwLock::new(HashSet::new()),
            binding_channel_index: RwLock::new(HashMap::new()),
            bot_info_overrides: RwLock::new(HashMap::new()),
            bots_base_dir: PathBuf::from("."),
            pending_requests: RwLock::new(HashMap::new()),
            task_modes: RwLock::new(HashMap::new()),
        }
    }
}

fn metrics_visibility_label(visibility: &str) -> String {
    match visibility {
        "public" | "protected" | "private" => visibility.to_string(),
        "" => "private".to_string(),
        _ => "other".to_string(),
    }
}

#[async_trait]
impl BotMetricsSnapshotPort for MemoryBotRepo {
    async fn bot_counts(&self) -> ServiceResult<Vec<BotMetricCount>> {
        let bots = self.bots.read().await;
        let mut counts: Vec<BotMetricCount> = Vec::new();
        for bot in bots.values() {
            let visibility = metrics_visibility_label(&bot.capabilities.visibility);
            if let Some(existing) = counts.iter_mut().find(|count| {
                count.actor_kind == bot.actor_kind
                    && count.status == bot.status
                    && count.visibility.as_deref() == Some(visibility.as_str())
            }) {
                existing.count += 1;
            } else {
                counts.push(BotMetricCount {
                    actor_kind: bot.actor_kind,
                    status: bot.status,
                    visibility: Some(visibility),
                    count: 1,
                });
            }
        }
        Ok(counts)
    }
}






#[cfg(test)]
#[path = "local_memory/tests.rs"]
mod tests;
#[path = "local_memory/control_plane.rs"]
mod control_plane;
#[path = "local_memory/registration.rs"]
mod registration;
#[path = "local_memory/reads.rs"]
mod reads;
#[path = "local_memory/persistence.rs"]
mod persistence;
#[path = "local_memory/human.rs"]
mod human;
#[path = "local_memory/tokens.rs"]
mod tokens;
#[path = "local_memory/streaming.rs"]
mod streaming;
#[path = "local_memory/repo.rs"]
mod repo;

#[path = "local_memory/admission.rs"]
mod admission;

#[cfg(test)]
#[path = "local_memory/admission_tests.rs"]
mod admission_tests;
