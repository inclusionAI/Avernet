//! In-memory bot repository implementation.
//!
//! Provides local bot persistence, discovery, and streaming connection state.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tokio::fs;
use tokio::sync::{RwLock, oneshot};
use tracing::{debug, info, warn};

use bcs_config::resolve_env_str as resolve_env;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{
    BindingChannels, BotCandidateReadQuery, BotCandidateReadRecord, BotCandidateVisibility,
    BotCapabilities, BotControlPlaneDescriptor, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRecord, BotControlPlaneRepoPort, BotDynamicStatus, BotMetricCount,
    BotMetricsSnapshotPort, RegisteredBot, ServiceError, ServiceResult, Skill,
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
    bots: RwLock<BTreeMap<String, RegisteredBotInner>>,
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
    /// Dynamic status updated periodically.
    dynamic_status: BotDynamicStatus,
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
            dynamic_status: self.dynamic_status.clone(),
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
            bots: RwLock::new(BTreeMap::new()),
            control_plane_audit: RwLock::new(HashMap::new()),
            token_to_bot: RwLock::new(HashMap::new()),
            deleted_bot_ids: RwLock::new(HashSet::new()),
            binding_channel_index: RwLock::new(HashMap::new()),
            bot_info_overrides: RwLock::new(HashMap::new()),
            bots_base_dir,
            pending_requests: RwLock::new(HashMap::new()),
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
                    warn!(bot_id = %bot_id, error = %e, "Failed to parse capabilities file");
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
            bots: RwLock::new(BTreeMap::new()),
            control_plane_audit: RwLock::new(HashMap::new()),
            token_to_bot: RwLock::new(HashMap::new()),
            deleted_bot_ids: RwLock::new(HashSet::new()),
            binding_channel_index: RwLock::new(HashMap::new()),
            bot_info_overrides: RwLock::new(HashMap::new()),
            bots_base_dir: PathBuf::from("."),
            pending_requests: RwLock::new(HashMap::new()),
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

#[async_trait]
impl BotRepoPort for MemoryBotRepo {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.deleted_bot_ids.write().await.remove(&bot_id);

        // Update binding channel index
        self.sync_binding_channel_index(&bot_id, &capabilities)
            .await;

        // Pre-read created_by from disk before acquiring lock
        let persisted_created_by = {
            let path = self.bot_info_path(&bot_id);
            if let Ok(content) = fs::read_to_string(&path).await {
                serde_json::from_str::<PersistedCapabilities>(&content)
                    .ok()
                    .and_then(|p| p.created_by)
            } else {
                None
            }
        };

        let mut bots = self.bots.write().await;

        if let Some(existing) = bots.get_mut(&bot_id) {
            // Update existing registration
            existing.last_heartbeat = Instant::now();
            // Merge capabilities, keeping non-empty values
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
            if capabilities.binding_channels.is_some() {
                existing.capabilities.binding_channels = capabilities.binding_channels;
            }
            if !capabilities.visibility.is_empty() {
                existing.capabilities.visibility = capabilities.visibility.clone();
            }
            // agent_code: 从请求中更新（允许设置或清除）
            if capabilities.agent_code.is_some() {
                existing.capabilities.agent_code = capabilities.agent_code;
            }
            // agent_token: 从请求中更新（允许设置）
            if capabilities.agent_token.is_some() {
                existing.capabilities.agent_token = capabilities.agent_token;
            }
            // Load created_by from disk if not already set in memory
            if existing.created_by.is_none() && persisted_created_by.is_some() {
                existing.created_by = persisted_created_by;
            }
            debug!(bot_id = %bot_id, "Bot registration updated");
        } else {
            // New registration
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_id: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities,
                    dynamic_status: BotDynamicStatus::default(),
                    ws_connection: None,
                    session_token: None,
                    env: Some(resolve_env()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: persisted_created_by,
                    protocol_version: 1,
                },
            );
            info!(bot_id = %bot_id, "Bot registered");
        }

        let now = unix_millis();
        let mut audit = self.control_plane_audit.write().await;
        let entry = audit.entry(bot_id).or_insert((now, now));
        entry.1 = now;

        Ok(())
    }

    async fn register_with_owner_and_token(
        &self,
        bot_id: String,
        capabilities: BotCapabilities,
        created_by: &str,
        token: &str,
    ) -> ServiceResult<()> {
        self.deleted_bot_ids.write().await.remove(&bot_id);

        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        let persisted_created_by = {
            let bots = self.bots.read().await;
            bots.get(&bot_id)
                .and_then(|bot| bot.created_by.clone())
                .unwrap_or_else(|| created_by.to_string())
        };

        let persisted = PersistedCapabilities {
            bot_id: bot_id.clone(),
            name: capabilities.name.clone(),
            summary: capabilities.summary.clone(),
            domains: capabilities.domains.clone(),
            skills: capabilities.skills.clone(),
            scopes: capabilities.scopes.clone(),
            binding_channels: capabilities.binding_channels.clone(),
            token: Some(token.to_string()),
            registered_at: now,
            hidden: false,
            created_by: Some(persisted_created_by),
            visibility: if capabilities.visibility.is_empty() {
                None
            } else {
                Some(capabilities.visibility.clone())
            },
            agent_code: capabilities.agent_code.clone(),
            agent_token: capabilities.agent_token.clone(),
        };

        let path = self.bot_info_path(&bot_id);
        let dir = path.parent().ok_or_else(|| {
            ServiceError::InternalError(format!("Invalid path for bot: {}", bot_id))
        })?;
        fs::create_dir_all(dir).await?;
        let content = serde_json::to_string_pretty(&persisted)?;
        fs::write(&path, content).await?;

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
                if capabilities.binding_channels.is_some() {
                    existing.capabilities.binding_channels = capabilities.binding_channels.clone();
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
                bots.insert(
                    bot_id.clone(),
                    RegisteredBotInner {
                        bot_id: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities,
                        dynamic_status: BotDynamicStatus::default(),
                        ws_connection: None,
                        session_token: Some(token.to_string()),
                        env: Some(resolve_env()),
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: Some(created_by.to_string()),
                        protocol_version: 1,
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

        let now = unix_millis();
        let mut audit = self.control_plane_audit.write().await;
        let entry = audit.entry(bot_id.clone()).or_insert((now, now));
        entry.1 = now;

        info!(bot_id = %bot_id, "Bot registered with owner and token");
        Ok(())
    }

    async fn update_status(&self, bot_id: &str, status: BotDynamicStatus) -> bool {
        let mut bots = self.bots.write().await;

        if let Some(bot) = bots.get_mut(bot_id) {
            bot.dynamic_status = status;
            bot.last_heartbeat = Instant::now();
            debug!(bot_id = %bot_id, "Bot dynamic status updated");
            true
        } else {
            debug!(bot_id = %bot_id, "Bot not found for status update");
            false
        }
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
    }

    async fn get_agent_credentials(
        &self,
        bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        let bots = self.bots.read().await;
        bots.get(bot_id)
            .filter(|b| !b.is_expired())
            .map(|b| bcs_service_api::AgentCredentials {
                agent_code: b.capabilities.agent_code.clone(),
                agent_token: b.capabilities.agent_token.clone(),
            })
    }

    async fn add_bot_info(&self, bot_id: &str, key: &str, value: String) {
        let bots = self.bots.read().await;
        if !bots.contains_key(bot_id) {
            return;
        }
        drop(bots);

        if key == "agent_token" {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.capabilities.agent_token = Some(value);
            }
            return;
        }

        if key == "client_kind" {
            self.bot_info_overrides
                .write()
                .await
                .insert((bot_id.to_string(), key.to_string()), value);
        }
    }

    async fn get_bot_info(&self, bot_id: &str, key: &str) -> Option<String> {
        if key == "agent_token" {
            let bots = self.bots.read().await;
            return bots
                .get(bot_id)
                .and_then(|bot| bot.capabilities.agent_token.clone());
        }

        self.bot_info_overrides
            .read()
            .await
            .get(&(bot_id.to_string(), key.to_string()))
            .cloned()
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn list_all_bots(&self) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        bots.values().map(|b| b.to_registered_bot()).collect()
    }

    async fn list_bots_by_name_and_cooperatable_with(
        &self,
        name: &str,
        bot_uuid: &str,
        cooperatable_only: bool,
        friend_uuids: &HashSet<String>,
        offset: usize,
        limit: usize,
    ) -> (Vec<(RegisteredBot, bool)>, usize) {
        let bots = self.bots.read().await;
        let name_lower = name.to_lowercase();

        let filtered: Vec<(RegisteredBot, bool)> = bots
            .values()
            .map(|b| b.to_registered_bot())
            .filter(|b| b.bot_uuid != bot_uuid)
            .filter(|b| b.actor_kind != bcs_service_api::ActorKind::Human)
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

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        let current_env = resolve_env();
        let bots = self.bots.read().await;
        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| {
                b.created_by.as_deref() == Some(created_by)
                    && b.env.as_deref() == Some(current_env.as_str())
            })
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn get_by_ids(&self, bot_ids: &[String]) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        let mut seen = HashSet::new();
        bot_ids
            .iter()
            .filter(|id| seen.insert(id.as_str()))
            .filter_map(|id| bots.get(id.as_str()))
            .filter(|b| !b.is_expired())
            .map(|b| b.to_registered_bot())
            .collect()
    }

    async fn discover(&self, query: &str) -> Vec<RegisteredBot> {
        let bots = self.bots.read().await;
        let query_lower = query.to_lowercase();

        bots.values()
            .filter(|b| !b.is_expired())
            .filter(|b| {
                // Check name
                b.capabilities.name.as_ref()
                    .map(|n| n.to_lowercase().contains(&query_lower))
                    .unwrap_or(false)
                // Check summary
                || b.capabilities.summary.as_ref()
                    .map(|s| s.to_lowercase().contains(&query_lower))
                    .unwrap_or(false)
                // Check dynamic summary (highest priority - real-time info)
                || b.dynamic_status.dynamic_summary.as_ref()
                    .map(|s| s.to_lowercase().contains(&query_lower))
                    .unwrap_or(false)
                // Check domains
                || b.capabilities.domains.iter()
                    .any(|d| d.to_lowercase().contains(&query_lower))
                // Check skills
                || b.capabilities.skills.iter()
                    .any(|s| s.name.to_lowercase().contains(&query_lower))
                // Check scopes
                || b.capabilities.scopes.iter()
                    .any(|s| s.to_lowercase().contains(&query_lower))
                // Check bot_id
                || b.bot_id.to_lowercase().contains(&query_lower)
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

    async fn unregister(&self, bot_id: &str) -> bool {
        self.soft_delete(bot_id).await
    }

    async fn soft_delete(&self, bot_id: &str) -> bool {
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

    async fn cleanup_expired(&self) {
        let mut bots = self.bots.write().await;
        let before = bots.len();
        bots.retain(|_, b| !b.is_expired());
        let removed = before - bots.len();
        if removed > 0 {
            warn!(removed, "Removed expired bot registrations");
        }
    }

    async fn load_from_storage(&self, bot_id: &str) -> Option<BotCapabilities> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return None;
        }
        self.load_capabilities_from_disk(bot_id).await
    }

    async fn save_to_storage(&self, bot_id: &str, caps: &BotCapabilities) -> ServiceResult<()> {
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

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
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

    /// DEPRECATED (Rev-4 / Human Actor V1): Noop + WARN. Replaced by
    /// [`update_actor_status`](Self::update_actor_status). (Task H.1)
    #[allow(deprecated)]
    async fn set_hidden(&self, bot_id: &str, hidden: bool) -> ServiceResult<()> {
        warn!(
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
    async fn update_actor_status(
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

    /// Ensure a Human Actor entry exists for the given staff_no — Task O.3.
    ///
    /// In-memory implementation: idempotent insert into the in-process registry.
    /// `name` is preserved on subsequent calls (Requirement 3.1#4).
    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<bcs_service_api::EnsureHumanResult> {
        let bot_uuid = format!("human_{}", staff_no);

        let default_summary = "写点什么介绍自己";

        let mut bots = self.bots.write().await;
        if let Some(existing) = bots.get_mut(&bot_uuid) {
            // Backfill summary if it is missing or empty.
            let needs_summary = existing
                .capabilities
                .summary
                .as_deref()
                .map_or(true, |s| s.is_empty());
            if needs_summary {
                existing.capabilities.summary = Some(default_summary.to_string());
                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor (in-memory): backfilled empty summary"
                );
            } else {
                debug!(
                    bot_uuid = %bot_uuid,
                    "ensure_human_actor (in-memory): already exists, preserving existing fields"
                );
            }
            return Ok(bcs_service_api::EnsureHumanResult { created: false });
        }

        let session_token = uuid::Uuid::new_v4().to_string();
        let caps = BotCapabilities {
            name: Some(nick_name.to_string()),
            summary: Some(default_summary.to_string()),
            visibility: "protected".to_string(),
            ..Default::default()
        };

        bots.insert(
            bot_uuid.clone(),
            RegisteredBotInner {
                bot_id: bot_uuid.clone(),
                last_heartbeat: Instant::now(),
                capabilities: caps,
                dynamic_status: BotDynamicStatus::default(),
                ws_connection: None,
                session_token: Some(session_token),
                env: Some(resolve_env()),
                status: bcs_service_api::ActorStatus::Online,
                actor_kind: bcs_service_api::ActorKind::Human,
                created_by: Some(staff_no.to_string()),
                protocol_version: 1,
            },
        );

        let now = unix_millis();
        self.control_plane_audit
            .write()
            .await
            .insert(bot_uuid.clone(), (now, now));

        info!(
            bot_uuid = %bot_uuid,
            staff_no = %staff_no,
            nick_name = %nick_name,
            "ensure_human_actor (in-memory): row inserted"
        );
        Ok(bcs_service_api::EnsureHumanResult { created: true })
    }

    async fn list_legacy_bots_for_owner(
        &self,
        staff_no: &str,
        env: &str,
    ) -> ServiceResult<Vec<RegisteredBot>> {
        let bots = self.bots.read().await;
        let results: Vec<RegisteredBot> = bots
            .values()
            .filter(|b| {
                // Must be a Bot (not Human) and match env
                if b.actor_kind != bcs_service_api::ActorKind::Bot {
                    return false;
                }
                if b.env.as_deref() != Some(env) {
                    return false;
                }
                // Rule (a): created_by matches
                if b.created_by.as_deref() == Some(staff_no) {
                    return true;
                }
                // Rule (b): created_by is None and namespace is whitelisted
                if b.created_by.is_none() && is_legacy_namespace(&b.bot_id, staff_no) {
                    return true;
                }
                false
            })
            .map(|b| {
                // 清除敏感字段，防止通过常规接口泄露
                let mut capabilities = b.capabilities.clone();
                capabilities.agent_code = None;
                capabilities.agent_token = None;
                RegisteredBot {
                    bot_uuid: b.bot_id.clone(),
                    capabilities,
                    dynamic_status: b.dynamic_status.clone(),
                    env: b.env.clone(),
                    created_by: b.created_by.clone(),
                    actor_kind: b.actor_kind.clone(),
                    status: b.status.clone(),
                }
            })
            .collect();

        Ok(results)
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return false;
        }
        self.bot_info_path(bot_id).exists()
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        // Update in-memory (respects overwrite flag; early return if not overwriting and already claimed)
        let already_claimed = {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                if overwrite || bot.created_by.is_none() {
                    bot.created_by = Some(created_by.to_string());
                    false
                } else {
                    true
                }
            } else {
                false
            }
        };

        if already_claimed {
            return Ok(());
        }

        // Update on disk (respects overwrite flag)
        let path = self.bot_info_path(bot_id);
        if path.exists() {
            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(mut persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    if overwrite || persisted.created_by.is_none() {
                        persisted.created_by = Some(created_by.to_string());
                        let updated = serde_json::to_string_pretty(&persisted)?;
                        fs::write(&path, updated).await?;
                        info!(bot_id = %bot_id, created_by = %created_by, overwrite = overwrite, "Updated created_by on disk");
                    }
                }
            }
        }

        Ok(())
    }

    async fn save_token(&self, bot_id: &str, token: &str) -> ServiceResult<()> {
        // We need to save token separately since it's not in BotCapabilities
        // Read the persisted file directly, update token, and save back
        let path = self.bot_info_path(bot_id);

        // Try to load existing persisted data
        let mut persisted = if path.exists() {
            match fs::read_to_string(&path).await {
                Ok(content) => serde_json::from_str::<PersistedCapabilities>(&content)
                    .unwrap_or_else(|_| PersistedCapabilities {
                        bot_id: bot_id.to_string(),
                        name: None,
                        summary: None,
                        domains: vec![],
                        skills: vec![],
                        scopes: vec![],
                        binding_channels: None,
                        token: Some(token.to_string()),
                        registered_at: 0,
                        hidden: false,
                        created_by: None,
                        visibility: None,
                        agent_code: None,
                        agent_token: None,
                    }),
                Err(_) => PersistedCapabilities {
                    bot_id: bot_id.to_string(),
                    name: None,
                    summary: None,
                    domains: vec![],
                    skills: vec![],
                    scopes: vec![],
                    binding_channels: None,
                    token: Some(token.to_string()),
                    registered_at: 0,
                    hidden: false,
                    created_by: None,
                    visibility: None,
                    agent_code: None,
                    agent_token: None,
                },
            }
        } else {
            PersistedCapabilities {
                bot_id: bot_id.to_string(),
                name: None,
                summary: None,
                domains: vec![],
                skills: vec![],
                scopes: vec![],
                binding_channels: None,
                token: Some(token.to_string()),
                registered_at: 0,
                hidden: false,
                created_by: None,
                visibility: None,
                agent_code: None,
                agent_token: None,
            }
        };

        let previous_token = persisted.token.clone();

        // Update token
        persisted.token = Some(token.to_string());

        // Ensure directory exists
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir).await?;
        }

        // Save
        let content = serde_json::to_string_pretty(&persisted)?;
        fs::write(&path, content).await?;

        let previous_token = {
            let mut bots = self.bots.write().await;
            if let Some(bot) = bots.get_mut(bot_id) {
                bot.session_token.replace(token.to_string()).or(previous_token)
            } else {
                previous_token
            }
        };
        let mut token_to_bot = self.token_to_bot.write().await;
        if let Some(previous_token) = previous_token.filter(|previous| previous != token) {
            token_to_bot.remove(&previous_token);
        }
        token_to_bot.insert(token.to_string(), bot_id.to_string());

        info!(bot_id = %bot_id, "Token saved to storage");
        Ok(())
    }

    async fn load_token(&self, bot_id: &str) -> Option<String> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return None;
        }
        {
            let bots = self.bots.read().await;
            if let Some(token) = bots.get(bot_id).and_then(|bot| bot.session_token.clone()) {
                return Some(token);
            }
        }

        let path = self.bot_info_path(bot_id);
        if !path.exists() {
            return None;
        }

        match fs::read_to_string(&path).await {
            Ok(content) => match serde_json::from_str::<PersistedCapabilities>(&content) {
                Ok(persisted) => persisted.token,
                Err(e) => {
                    warn!(bot_id = %bot_id, error = %e, "Failed to parse capabilities file for token");
                    None
                }
            },
            Err(e) => {
                debug!(bot_id = %bot_id, error = %e, "Failed to read capabilities file for token");
                None
            }
        }
    }

    async fn find_bot_by_token(&self, token: &str) -> Option<String> {
        // First check in-memory token mapping (fast path)
        {
            let token_to_bot = self.token_to_bot.read().await;
            if let Some(bot_id) = token_to_bot.get(token) {
                if self.deleted_bot_ids.read().await.contains(bot_id) {
                    return None;
                }
                return Some(bot_id.clone());
            }
        }

        // Fall back to scanning disk (slow path, for BCS server restarts)
        let mut entries = match fs::read_dir(&self.bots_base_dir).await {
            Ok(entries) => entries,
            Err(_) => return None,
        };

        while let Ok(Some(entry)) = entries.next_entry().await {
            if !entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false) {
                continue;
            }

            let bot_id = entry.file_name().to_string_lossy().to_string();
            if self.deleted_bot_ids.read().await.contains(&bot_id) {
                continue;
            }
            let path = self.bot_info_path(&bot_id);

            if !path.exists() {
                continue;
            }

            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    if persisted.token.as_deref() == Some(token) {
                        debug!(bot_id = %bot_id, "Found bot by token on disk");
                        return Some(bot_id);
                    }
                }
            }
        }

        None
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

    async fn find_bot_by_agent_code(&self, agent_code: &str) -> Option<String> {
        if agent_code.is_empty() {
            return None;
        }

        // Fast path: scan in-memory bots for a matching agent_code.
        {
            let bots = self.bots.read().await;
            for (bot_id, bot) in bots.iter() {
                if self.deleted_bot_ids.read().await.contains(bot_id) {
                    continue;
                }
                if bot.capabilities.agent_code.as_deref() == Some(agent_code) {
                    return Some(bot_id.clone());
                }
            }
        }

        // Fall back to scanning disk (slow path, for BCS server restarts).
        let mut entries = match fs::read_dir(&self.bots_base_dir).await {
            Ok(entries) => entries,
            Err(_) => return None,
        };

        while let Ok(Some(entry)) = entries.next_entry().await {
            if !entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false) {
                continue;
            }

            let bot_id = entry.file_name().to_string_lossy().to_string();
            if self.deleted_bot_ids.read().await.contains(&bot_id) {
                continue;
            }
            let path = self.bot_info_path(&bot_id);
            if !path.exists() {
                continue;
            }

            if let Ok(content) = fs::read_to_string(&path).await {
                if let Ok(persisted) = serde_json::from_str::<PersistedCapabilities>(&content) {
                    if persisted.agent_code.as_deref() == Some(agent_code) {
                        debug!(bot_id = %bot_id, "Found bot by agent_code on disk");
                        return Some(bot_id);
                    }
                }
            }
        }

        None
    }

    // ===== Streaming Connection Management =====

    async fn register_streaming_connection(&self, bot_id: String) -> Result<String, ()> {
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

        // Either create new bot entry or update existing one
        if let Some(bot) = bots.get_mut(&bot_id) {
            bot.ws_connection = Some(BotConnection {
                session_token: session_token.clone(),
                connected_at: Instant::now(),
            });
            bot.session_token = Some(session_token.clone());
            bot.last_heartbeat = Instant::now();
        } else {
            // Create new bot entry (will be updated with capabilities on onboard)
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_id: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities: BotCapabilities::default(),
                    dynamic_status: BotDynamicStatus::default(),
                    ws_connection: Some(BotConnection {
                        session_token: session_token.clone(),
                        connected_at: Instant::now(),
                    }),
                    session_token: Some(session_token.clone()),
                    env: Some(resolve_env()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: None,
                    protocol_version: 1,
                },
            );
        }

        // Store token mapping
        let mut token_to_bot = self.token_to_bot.write().await;
        token_to_bot.insert(session_token.clone(), bot_id.clone());

        info!(bot_id = %bot_id, token = %session_token, "Bot streaming connection registered");

        Ok(session_token)
    }

    async fn reconnect_streaming(&self, existing_token: String) -> Result<(String, String), ()> {
        // Check if token is valid and get bot_id
        let bot_id = {
            let token_to_bot = self.token_to_bot.read().await;
            match token_to_bot.get(&existing_token) {
                Some(id) => id.clone(),
                None => {
                    warn!(token = %existing_token, "Unknown token for reconnection");
                    return Err(());
                }
            }
        };

        let mut bots = self.bots.write().await;

        // Check if bot is already connected
        if let Some(bot) = bots.get(&bot_id) {
            if bot.ws_connection.is_some() {
                warn!(bot_id = %bot_id, "Bot already has an active connection");
                return Err(());
            }
        }

        // Update or create bot entry with connection
        if let Some(bot) = bots.get_mut(&bot_id) {
            bot.ws_connection = Some(BotConnection {
                session_token: existing_token.clone(),
                connected_at: Instant::now(),
            });
            bot.last_heartbeat = Instant::now();
        } else {
            // Bot not in memory, create entry (capabilities will be loaded from disk)
            bots.insert(
                bot_id.clone(),
                RegisteredBotInner {
                    bot_id: bot_id.clone(),
                    last_heartbeat: Instant::now(),
                    capabilities: BotCapabilities::default(),
                    dynamic_status: BotDynamicStatus::default(),
                    ws_connection: Some(BotConnection {
                        session_token: existing_token.clone(),
                        connected_at: Instant::now(),
                    }),
                    session_token: Some(existing_token.clone()),
                    env: Some(resolve_env()),
                    status: bcs_service_api::ActorStatus::Online,
                    actor_kind: bcs_service_api::ActorKind::Bot,
                    created_by: None,
                    protocol_version: 1,
                },
            );
        }

        info!(bot_id = %bot_id, token = %existing_token, "Bot streaming connection re-established");

        Ok((bot_id, existing_token))
    }

    async fn disconnect_streaming(&self, bot_id: &str) {
        let mut bots = self.bots.write().await;

        if let Some(bot) = bots.get_mut(bot_id) {
            if let Some(conn) = bot.ws_connection.take() {
                // DO NOT remove token_to_bot mapping - token should persist for reconnection
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

    async fn get_protocol_version(&self, bot_id: &str) -> u32 {
        let bots = self.bots.read().await;
        bots.get(bot_id).map(|b| b.protocol_version).unwrap_or(1)
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
                        bot_id: bot_id.clone(),
                        last_heartbeat: Instant::now(),
                        capabilities: BotCapabilities::default(),
                        dynamic_status: BotDynamicStatus::default(),
                        ws_connection: None,
                        session_token: Some(token.clone()),
                        env: Some(resolve_env()),
                        status: bcs_service_api::ActorStatus::Online,
                        actor_kind: bcs_service_api::ActorKind::Bot,
                        created_by: None,
                        protocol_version: 1,
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

#[async_trait]
impl BotControlPlaneRepoPort for MemoryBotRepo {
    async fn get_control_plane(
        &self,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return Ok(None);
        }
        let audit = self
            .control_plane_audit
            .read()
            .await
            .get(bot_id)
            .copied()
            .unwrap_or((0, 0));
        let bots = self.bots.read().await;
        let Some(bot) = bots.get(bot_id) else {
            return Ok(None);
        };
        let record_env = bot.env.clone().unwrap_or_else(resolve_env);
        if record_env != env {
            return Ok(None);
        }
        let Some(name) = bot
            .capabilities
            .name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
        else {
            return Ok(None);
        };
        Ok(Some(BotControlPlaneRecord {
            bot_id: bot.bot_id.clone(),
            kind: bot.actor_kind,
            name: name.to_string(),
            visibility: if bot.capabilities.visibility.is_empty() {
                "protected".to_string()
            } else {
                bot.capabilities.visibility.clone()
            },
            status: bot.status,
            env: record_env,
            created_by: bot.created_by.clone(),
            descriptor: BotControlPlaneDescriptor {
                summary: bot.capabilities.summary.clone().unwrap_or_default(),
                domains: bot.capabilities.domains.clone(),
                skills: bot.capabilities.skills.clone(),
                scopes: bot.capabilities.scopes.clone(),
            },
            agent_code: bot.capabilities.agent_code.clone(),
            created_at: audit.0,
            updated_at: audit.1,
        }))
    }

    async fn list_control_plane_candidates(
        &self,
        query: BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<BotCandidateReadRecord>, u64)> {
        let ids = self.bots.read().await.keys().cloned().collect::<Vec<_>>();
        let name = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_lowercase);
        let mut records = Vec::new();
        for bot_id in ids {
            let Some(bot) = self.get_control_plane(&bot_id, &query.env).await? else {
                continue;
            };
            if bot.bot_id == query.acting_bot_id || bot.kind != bcs_service_api::ActorKind::Bot {
                continue;
            }
            if name
                .as_ref()
                .is_some_and(|name| !bot.name.to_lowercase().contains(name))
            {
                continue;
            }
            let is_friend = query.friend_ids.contains(&bot.bot_id);
            let visible = match query.visibility {
                BotCandidateVisibility::Discovery => {
                    matches!(bot.visibility.as_str(), "public" | "protected")
                }
                BotCandidateVisibility::Collaboration => bot.visibility == "public" || is_friend,
            };
            if visible {
                records.push(BotCandidateReadRecord { bot, is_friend });
            }
        }
        records.sort_by(|left, right| {
            right
                .bot
                .created_at
                .cmp(&left.bot.created_at)
                .then_with(|| left.bot.bot_id.cmp(&right.bot.bot_id))
        });
        let total = records.len() as u64;
        let page = records
            .into_iter()
            .skip(query.offset as usize)
            .take(query.limit as usize)
            .collect();
        Ok((page, total))
    }

    async fn list_control_plane_by_creator(
        &self,
        query: BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<BotControlPlaneRecord>> {
        let ids = self.bots.read().await.keys().cloned().collect::<Vec<_>>();
        let name = query
            .name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_lowercase);
        let mut records = Vec::new();
        for bot_id in ids {
            let Some(bot) = self.get_control_plane(&bot_id, &query.env).await? else {
                continue;
            };
            if bot.created_by.as_deref() != Some(query.created_by.as_str())
                || query.kind.is_some_and(|kind| bot.kind != kind)
                || query.status.is_some_and(|status| bot.status != status)
                || name
                    .as_ref()
                    .is_some_and(|name| !bot.name.to_lowercase().contains(name))
            {
                continue;
            }
            records.push(bot);
        }
        records.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| left.bot_id.cmp(&right.bot_id))
        });
        Ok(records)
    }

    async fn patch_control_plane(
        &self,
        bot_id: &str,
        env: &str,
        patch: BotControlPlanePatch,
    ) -> ServiceResult<Option<BotControlPlaneRecord>> {
        if self.deleted_bot_ids.read().await.contains(bot_id) {
            return Ok(None);
        }
        let (mut capabilities, token, created_by, record_env) = {
            let bots = self.bots.read().await;
            let Some(bot) = bots.get(bot_id) else {
                return Ok(None);
            };
            let record_env = bot.env.clone().unwrap_or_else(resolve_env);
            if record_env != env {
                return Ok(None);
            }
            (
                bot.capabilities.clone(),
                bot.session_token.clone(),
                bot.created_by.clone(),
                record_env,
            )
        };

        if let Some(name) = patch.name.as_ref() {
            capabilities.name = Some(name.clone());
        }
        if let Some(visibility) = patch.visibility.as_ref() {
            capabilities.visibility = visibility.clone();
        }
        if let Some(descriptor) = patch.descriptor.as_ref() {
            if let Some(summary) = descriptor.summary.as_ref() {
                capabilities.summary = Some(summary.clone());
            }
            if let Some(domains) = descriptor.domains.as_ref() {
                capabilities.domains = domains.clone();
            }
            if let Some(skills) = descriptor.skills.as_ref() {
                capabilities.skills = skills.clone();
            }
            if let Some(scopes) = descriptor.scopes.as_ref() {
                capabilities.scopes = scopes.clone();
            }
        }

        let now = unix_millis();
        let created_at = self
            .control_plane_audit
            .read()
            .await
            .get(bot_id)
            .map(|audit| audit.0)
            .unwrap_or(now);
        let persisted = PersistedCapabilities {
            bot_id: bot_id.to_string(),
            name: capabilities.name.clone(),
            summary: capabilities.summary.clone(),
            domains: capabilities.domains.clone(),
            skills: capabilities.skills.clone(),
            scopes: capabilities.scopes.clone(),
            binding_channels: capabilities.binding_channels.clone(),
            token,
            registered_at: created_at,
            hidden: false,
            created_by: created_by.clone(),
            visibility: Some(capabilities.visibility.clone()),
            agent_code: capabilities.agent_code.clone(),
            agent_token: capabilities.agent_token.clone(),
        };
        let path = self.bot_info_path(bot_id);
        let directory = path.parent().ok_or_else(|| {
            ServiceError::InternalError(format!("Invalid path for bot: {bot_id}"))
        })?;
        fs::create_dir_all(directory).await?;
        fs::write(&path, serde_json::to_string_pretty(&persisted)?).await?;

        {
            let mut bots = self.bots.write().await;
            let Some(bot) = bots.get_mut(bot_id) else {
                return Ok(None);
            };
            bot.capabilities = capabilities;
            bot.created_by = created_by;
            bot.env = Some(record_env);
            if let Some(status) = patch.status {
                bot.status = status;
            }
        }
        self.control_plane_audit
            .write()
            .await
            .insert(bot_id.to_string(), (created_at, now));
        self.get_control_plane(bot_id, env).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn register_and_get_bot() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("Test Bot".to_string()),
            ..Default::default()
        };
        registry.register("test".to_string(), caps).await.unwrap();

        let bot = registry.get("test").await;
        assert!(bot.is_some());
        assert_eq!(bot.unwrap().capabilities.name, Some("Test Bot".to_string()));
    }

    #[tokio::test]
    async fn unregistered_bot_returns_none() {
        let registry = MemoryBotRepo::new();

        let bot = registry.get("unknown").await;
        assert!(bot.is_none());
    }

    #[tokio::test]
    async fn update_existing_registration() {
        let registry = MemoryBotRepo::new();

        let caps1 = BotCapabilities::default();
        registry.register("test".to_string(), caps1).await.unwrap();

        let caps2 = BotCapabilities {
            name: Some("Updated Name".to_string()),
            ..Default::default()
        };
        registry.register("test".to_string(), caps2).await.unwrap();

        let bot = registry.get("test").await.unwrap();
        assert_eq!(bot.capabilities.name, Some("Updated Name".to_string()));
    }

    #[tokio::test]
    async fn save_token_updates_memory_token_index() {
        let temp_dir = tempfile::tempdir().expect("temp dir");
        let registry = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());
        registry
            .register("bot-1".to_string(), BotCapabilities::default())
            .await
            .unwrap();

        registry.save_token("bot-1", "token-1").await.unwrap();
        assert_eq!(
            registry.find_bot_by_token("token-1").await.as_deref(),
            Some("bot-1")
        );

        registry.save_token("bot-1", "token-2").await.unwrap();
        let token_to_bot = registry.token_to_bot.read().await;
        assert!(token_to_bot.get("token-1").is_none());
        assert_eq!(token_to_bot.get("token-2").map(String::as_str), Some("bot-1"));
    }

    #[tokio::test]
    async fn register_with_owner_and_token_persists_owner_token_and_index() {
        let temp_dir = tempfile::tempdir().expect("temp dir");
        let registry = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());
        let caps = BotCapabilities {
            name: Some("Provider Bot".to_string()),
            ..Default::default()
        };

        registry
            .register_with_owner_and_token(
                "bot-1".to_string(),
                caps,
                "11111111",
                "token-1",
            )
            .await
            .unwrap();

        assert_eq!(
            registry
                .get("bot-1")
                .await
                .expect("registered bot")
                .created_by
                .as_deref(),
            Some("11111111")
        );
        assert_eq!(registry.load_token("bot-1").await.as_deref(), Some("token-1"));
        assert_eq!(
            registry.find_bot_by_token("token-1").await.as_deref(),
            Some("bot-1")
        );
    }

    #[tokio::test]
    async fn update_dynamic_status() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("DBA Expert".to_string()),
            skills: vec![Skill::new("sql_analysis")],
            ..Default::default()
        };
        registry.register("dba".to_string(), caps).await.unwrap();

        let status = BotDynamicStatus {
            status: "busy".to_string(),
            dynamic_summary: Some("Processing deadlock request".to_string()),
            load: Some(0.7),
            updated_at: Some(1234567890),
        };
        let updated = registry.update_status("dba", status.clone()).await;
        assert!(updated);

        let bot = registry.get("dba").await.unwrap();
        assert_eq!(bot.dynamic_status.status, "busy");
        assert_eq!(
            bot.dynamic_status.dynamic_summary,
            Some("Processing deadlock request".to_string())
        );
        assert_eq!(bot.dynamic_status.load, Some(0.7));

        let not_found = registry
            .update_status("unknown", BotDynamicStatus::default())
            .await;
        assert!(!not_found);
    }

    #[tokio::test]
    async fn discover_by_capability() {
        let registry = MemoryBotRepo::new();

        let dba_caps = BotCapabilities {
            name: Some("DBA Expert".to_string()),
            domains: vec!["database".to_string(), "mysql".to_string()],
            skills: vec![Skill::new("sql_analysis"), Skill::new("deadlock_debugging")],
            ..Default::default()
        };
        registry
            .register("dba".to_string(), dba_caps)
            .await
            .unwrap();

        let sec_caps = BotCapabilities {
            name: Some("Security Expert".to_string()),
            domains: vec!["security".to_string()],
            skills: vec![Skill::new("vulnerability_scan")],
            ..Default::default()
        };
        registry
            .register("security".to_string(), sec_caps)
            .await
            .unwrap();

        let results = registry.discover("database").await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "dba");

        let results = registry.find_by_skills(&["deadlock"]).await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "dba");
    }

    #[tokio::test]
    async fn unregister_bot() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities::default();
        registry.register("test".to_string(), caps).await.unwrap();
        assert!(registry.get("test").await.is_some());

        let removed = registry.unregister("test").await;
        assert!(removed);
        assert!(registry.get("test").await.is_none());

        // Unregistering non-existent bot returns false
        let removed_again = registry.unregister("test").await;
        assert!(!removed_again);
    }

    #[tokio::test]
    async fn find_by_multiple_skills() {
        let registry = MemoryBotRepo::new();

        let dba_caps = BotCapabilities {
            skills: vec![
                Skill::new("sql_analysis"),
                Skill::new("deadlock_debugging"),
                Skill::new("performance_tuning"),
            ],
            ..Default::default()
        };
        registry
            .register("dba".to_string(), dba_caps)
            .await
            .unwrap();

        let sec_caps = BotCapabilities {
            skills: vec![Skill::new("sql_analysis"), Skill::new("security_audit")],
            ..Default::default()
        };
        registry
            .register("security".to_string(), sec_caps)
            .await
            .unwrap();

        // DBA has both skills
        let results = registry.find_by_skills(&["deadlock", "performance"]).await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "dba");

        // Both have sql_analysis
        let results = registry.find_by_skills(&["sql_analysis"]).await;
        assert_eq!(results.len(), 2);

        // No one has all these
        let results = registry.find_by_skills(&["deadlock", "security"]).await;
        assert_eq!(results.len(), 0);
    }

    #[tokio::test]
    async fn find_by_domains() {
        let registry = MemoryBotRepo::new();

        let dba_caps = BotCapabilities {
            domains: vec!["database".to_string(), "mysql".to_string()],
            ..Default::default()
        };
        registry
            .register("dba".to_string(), dba_caps)
            .await
            .unwrap();

        let results = registry.find_by_domains(&["database"]).await;
        assert_eq!(results.len(), 1);

        let results = registry.find_by_domains(&["database", "mysql"]).await;
        assert_eq!(results.len(), 1);

        let results = registry.find_by_domains(&["security"]).await;
        assert_eq!(results.len(), 0);
    }

    #[tokio::test]
    async fn find_by_scopes() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            scopes: vec![
                "database:read".to_string(),
                "database:write".to_string(),
                "logs:read".to_string(),
            ],
            ..Default::default()
        };
        registry.register("dba".to_string(), caps).await.unwrap();

        let results = registry.find_by_scopes(&["database:read"]).await;
        assert_eq!(results.len(), 1);

        let results = registry
            .find_by_scopes(&["database:read", "logs:read"])
            .await;
        assert_eq!(results.len(), 1);

        let results = registry.find_by_scopes(&["admin:all"]).await;
        assert_eq!(results.len(), 0);
    }

    #[tokio::test]
    async fn capability_merge_keeps_non_empty_values() {
        let registry = MemoryBotRepo::new();

        // Initial registration with empty capabilities
        let caps1 = BotCapabilities {
            name: None,
            summary: None,
            domains: vec![],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            ..Default::default()
        };
        registry.register("test".to_string(), caps1).await.unwrap();

        // Update with some values
        let caps2 = BotCapabilities {
            name: Some("Test Bot".to_string()),
            summary: Some("A test bot".to_string()),
            domains: vec!["testing".to_string()],
            skills: vec![],
            scopes: vec![],
            binding_channels: None,
            ..Default::default()
        };
        registry.register("test".to_string(), caps2).await.unwrap();

        let bot = registry.get("test").await.unwrap();
        assert_eq!(bot.capabilities.name, Some("Test Bot".to_string()));
        assert_eq!(bot.capabilities.summary, Some("A test bot".to_string()));
        assert_eq!(bot.capabilities.domains, vec!["testing".to_string()]);
    }

    #[tokio::test]
    async fn save_to_storage_replaces_memory_with_final_capabilities() {
        let temp_dir = tempfile::TempDir::new().unwrap();
        let registry = MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf());
        let mut bindings = BindingChannels::new();
        bindings.insert(
            "antding".to_string(),
            bcs_service_api::BindingChannel {
                binding_key: "old-key".to_string(),
            },
        );

        registry
            .register(
                "test".to_string(),
                BotCapabilities {
                    name: Some("Old".to_string()),
                    summary: Some("Old summary".to_string()),
                    domains: vec!["old-domain".to_string()],
                    skills: vec![Skill::new("old-skill")],
                    scopes: vec!["old-scope".to_string()],
                    binding_channels: Some(bindings),
                    visibility: "public".to_string(),
                    agent_code: Some("agent-code".to_string()),
                    agent_token: Some("agent-token".to_string()),
                    ..Default::default()
                },
            )
            .await
            .unwrap();

        registry
            .save_to_storage(
                "test",
                &BotCapabilities {
                    name: None,
                    summary: None,
                    domains: Vec::new(),
                    skills: Vec::new(),
                    scopes: Vec::new(),
                    binding_channels: None,
                    visibility: String::new(),
                    agent_code: None,
                    agent_token: None,
                    ..Default::default()
                },
            )
            .await
            .unwrap();

        let bot = registry.get("test").await.unwrap();
        assert_eq!(bot.capabilities.name, None);
        assert_eq!(bot.capabilities.summary, None);
        assert!(bot.capabilities.domains.is_empty());
        assert!(bot.capabilities.skills.is_empty());
        assert!(bot.capabilities.scopes.is_empty());
        assert!(bot.capabilities.binding_channels.is_none());
        assert_eq!(bot.capabilities.visibility, "public");

        let credentials = registry.get_agent_credentials("test").await.unwrap();
        assert_eq!(credentials.agent_code.as_deref(), Some("agent-code"));
        assert_eq!(credentials.agent_token.as_deref(), Some("agent-token"));
    }

    #[tokio::test]
    async fn discover_by_bot_id() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("Zhang San".to_string()),
            ..Default::default()
        };
        registry
            .register("zhangsan".to_string(), caps)
            .await
            .unwrap();

        let results = registry.discover("zhang").await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "zhangsan");
    }

    #[tokio::test]
    async fn discover_by_dynamic_summary() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("DBA Bot".to_string()),
            ..Default::default()
        };
        registry.register("dba".to_string(), caps).await.unwrap();

        // Update with dynamic summary
        let status = BotDynamicStatus {
            status: "busy".to_string(),
            dynamic_summary: Some("Currently handling deadlock analysis".to_string()),
            load: None,
            updated_at: None,
        };
        registry.update_status("dba", status).await;

        // Should find by dynamic summary content
        let results = registry.discover("deadlock analysis").await;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].bot_uuid, "dba");
    }

    #[tokio::test]
    async fn list_active_excludes_expired() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities::default();
        registry
            .register("bot1".to_string(), caps.clone())
            .await
            .unwrap();
        registry.register("bot2".to_string(), caps).await.unwrap();

        // Both are active
        let active = registry.list_active().await;
        assert_eq!(active.len(), 2);

        // Unregister one
        registry.unregister("bot1").await;

        let active = registry.list_active().await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].bot_uuid, "bot2");
    }

    #[tokio::test]
    async fn cleanup_expired_removes_old_bots() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities::default();
        registry
            .register("bot1".to_string(), caps.clone())
            .await
            .unwrap();
        registry.register("bot2".to_string(), caps).await.unwrap();

        // Unregister bot1 to simulate expiry scenario
        registry.unregister("bot1").await;

        // Run cleanup (won't remove bot2 since it's not expired)
        registry.cleanup_expired().await;

        let active = registry.list_active().await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].bot_uuid, "bot2");
    }
}
