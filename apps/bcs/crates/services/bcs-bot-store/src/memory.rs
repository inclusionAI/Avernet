//! In-memory bot repository implementation.
//!
//! Provides local bot persistence, discovery, and streaming connection state.

#[path = "memory_registration_create.rs"]
mod registration_create;
#[path = "memory_agent_bots.rs"]
mod agent_bots;
#[path = "memory_bot_repo_port.rs"]
mod bot_repo_port;
#[path = "memory_control_plane.rs"]
mod control_plane;
/// Mine-union candidate read over the SAME shared
/// state (bot-authority plan Task 4.5, spec §8.0
/// point 1); mirrors the SQL store's
/// `PersistentBotRepo::list_controllable`.
#[path = "memory_controllable.rs"]
mod controllable;
// Bot-authority projection over the SAME MemoryBotRepo state
// (Bot-authority plan Task 1; owner/manager edges + transfer
// rows + ownership versions live in the shared `authority`
// facet below). Holds the harness conformance tests alongside
// the SQL Store's suite.
#[path = "memory_authority.rs"]
pub mod authority;
/// Memory ownership sync command (2026-10-29 design §5.1).
#[path = "memory_ownership_sync.rs"]
pub mod ownership_sync;
/// Shared runtime permission-profile state + the
/// `PermissionProfileRepoPort` (same Arc, Task 4.3a).
#[path = "memory_permission_profile.rs"]
pub mod permission_profile;
/// Trusted memory registration composites (Task 4).
#[path = "memory_registration_ownership.rs"]
pub(super) mod registration_ownership;
/// Memory projection of the atomic retirement
/// (Task 4, spec §7.1) on the SAME shared state.
#[path = "memory_retirement.rs"]
mod retirement;

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
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
    BindingChannels,
    BotCapabilities, BotMetricCount,
    BotMetricsSnapshotPort, FriendCheckInStrategy, RegisteredBot, ServiceError,
    ServiceResult, Skill, UserVisibility,
};
#[cfg(test)]
use bcs_service_api::BotDynamicStatus;

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
    /// Process-local runtime info, including the active negotiated client kind
    /// and server-owned coordination profile.
    bot_info_overrides: RwLock<HashMap<(String, String), String>>,
    /// Base directory for bot files (from BCS_DATA_DIR).
    bots_base_dir: PathBuf,
    /// Pending one-shot request-response channels: request_id -> oneshot sender.
    pending_requests: RwLock<HashMap<String, oneshot::Sender<serde_json::Value>>>,
    /// Control-plane task-mode toggles: (`task_claim_mode`, `task_dream_mode`).
    task_modes: RwLock<HashMap<String, (bool, bool)>>,
    /// Bot-authority state (owner/manager edges, ownership
    /// versions, transfer rows) on the same shared state
    /// boundary (spec §7: Memory 共享一个原子状态边界).
    authority: RwLock<authority::MemoryAuthorityState>,
    /// Shared runtime permission-profile state (same
    /// MemoryBotRepo Arc: profile records, active-default
    /// index, u64 allocator starting at 1).
    profiles: RwLock<permission_profile::MemoryProfileState>,
    /// Legacy `created_by` fallback role reads for the ownership
    /// migration window (bootstrap config
    /// `bot_authority.legacy_created_by_fallback`); only the
    /// authority role/roles_for facade consults it.
    legacy_created_by_fallback: AtomicBool,
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
            authority: RwLock::new(authority::MemoryAuthorityState::default()),
            profiles: RwLock::new(permission_profile::MemoryProfileState::default()),
            legacy_created_by_fallback: AtomicBool::new(false),
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

impl MemoryBotRepo {
    /// Bootstrap wiring flips the legacy `created_by` fallback
    /// posture of the authority role reads (ownership migration
    /// window); default OFF so fixtures stay strict unless a
    /// scenario opts in.
    pub fn set_legacy_created_by_fallback(&self, enabled: bool) {
        self.legacy_created_by_fallback
            .store(enabled, Ordering::SeqCst);
    }
}

impl Default for MemoryBotRepo {
    fn default() -> Self {
        Self {
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
            authority: RwLock::new(authority::MemoryAuthorityState::default()),
            profiles: RwLock::new(permission_profile::MemoryProfileState::default()),
            legacy_created_by_fallback: AtomicBool::new(false),
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
    async fn heartbeat_renews_registration_without_retaining_dynamic_status() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("DBA Expert".to_string()),
            skills: vec![Skill::new("sql_analysis")],
            ..Default::default()
        };
        registry.register("dba".to_string(), caps).await.unwrap();

        let _status = BotDynamicStatus {
            status: "busy".to_string(),
            dynamic_summary: Some("Processing deadlock request".to_string()),
            load: Some(0.7),
            updated_at: Some(1234567890),
        };
        registry.bots.write().await.get_mut("dba").unwrap().last_heartbeat =
            Instant::now() - BOT_EXPIRY - Duration::from_secs(1);
        let updated = registry.update_status("dba").await;
        assert!(updated);

        let bot = registry.get("dba").await.unwrap();
        assert!(serde_json::to_value(bot).unwrap().get("dynamic_status").is_none());
        assert!(!registry.bots.read().await["dba"].is_expired());

        let not_found = registry.update_status("unknown").await;
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
    async fn discovery_ignores_dynamic_summary() {
        let registry = MemoryBotRepo::new();

        let caps = BotCapabilities {
            name: Some("DBA Bot".to_string()),
            ..Default::default()
        };
        registry.register("dba".to_string(), caps).await.unwrap();

        // Update with dynamic summary
        let _status = BotDynamicStatus {
            status: "busy".to_string(),
            dynamic_summary: Some("Currently handling deadlock analysis".to_string()),
            load: None,
            updated_at: None,
        };
        registry.update_status("dba").await;

        // Heartbeat descriptions are not part of the discovery contract.
        assert!(registry.discover("deadlock analysis").await.is_empty());
        assert_eq!(registry.discover("DBA").await.len(), 1);
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
