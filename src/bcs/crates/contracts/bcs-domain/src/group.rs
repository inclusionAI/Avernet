//! Group / participant / routing-policy pure domain types.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::actor::ActorKind;
use crate::message::{AuditEntry, GroupMessage, Task};

/// Role of a participant in a session.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ParticipantRole {
    /// Handles non-@mentioned messages, drives the session.
    #[default]
    Driver,
    /// Provides context/expertise when invoked.
    Consultant,
    /// Manager in manager_worker groups: dispatches `bcs_assign_task` to workers.
    Manager,
    /// Worker in manager_worker groups: receives task dispatch, replies with
    /// `bcs_task_complete`.
    Worker,
    /// Can see messages but not actively involved.
    Observer,
}

/// Type of participant in a group session.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ParticipantKind {
    /// Bot participant.
    #[default]
    Bot,
}

/// Kind of group.
///
/// `Normal` is the existing multi-actor group; `Dm` is an Actor-level 1:1
/// direct message group that is automatically deduplicated by `dm_pair_key`.
/// V1 supports Bot↔Bot and Human↔Bot pairs. Human↔Human DMs are rejected.
///
/// See `docs/specs/bcs-human-actor/requirements.md` Requirement 3.19.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum GroupKind {
    /// Normal multi-participant group (default).
    #[default]
    Normal,
    /// Actor-level 1:1 direct message group.
    Dm,
}

/// Per-group, per-participant collaboration mode.
///
/// V1 ships **only** these four variants. Future variants (e.g. `supervised`,
/// `standby`) are intentionally NOT added in V1; the wire layer rejects any
/// unknown value at deserialization (per requirements 3.4#3 and 3.18#5).
///
/// Valid (`ActorKind`, `ParticipantMode`) combinations:
/// - `(Bot, Auto)`, `(Bot, Muted)`
/// - `(Human, Present)`, `(Human, Absent)`
///
/// All other combinations are illegal; HTTP handlers SHALL return 400.
/// DB load paths SHALL normalize illegal combinations in-memory (see M.6).
///
/// See `docs/specs/bcs-human-actor/requirements.md` Requirement 3.3 / 3.4 / 3.18.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ParticipantMode {
    /// Bot autonomously decides whether to respond (default for Bot).
    Auto,
    /// Bot is muted: forced to `Inject` even when @mentioned or driver.
    Muted,
    /// Human is present in the group and can speak.
    Present,
    /// Human is absent (default for Human): cannot speak, may shadow-observe.
    Absent,
}

impl Default for ParticipantMode {
    fn default() -> Self {
        // Default mirrors the legacy implicit assumption (bot-driven groups).
        // For explicit kind-aware default, prefer `ParticipantMode::default_for(kind)`.
        Self::Auto
    }
}

impl ParticipantMode {
    /// Return the default mode for the given actor kind.
    ///
    /// - `ActorKind::Bot`   → `ParticipantMode::Auto`
    /// - `ActorKind::Human` → `ParticipantMode::Absent`
    pub fn default_for(kind: ActorKind) -> Self {
        match kind {
            ActorKind::Bot => Self::Auto,
            ActorKind::Human => Self::Absent,
        }
    }

    /// Return whether this mode is a legal value for the given actor kind.
    ///
    /// Only the 4 documented combinations are legal:
    /// - `Bot`   ↔ `Auto` | `Muted`
    /// - `Human` ↔ `Present` | `Absent`
    pub fn is_valid_for(self, kind: ActorKind) -> bool {
        matches!(
            (kind, self),
            (ActorKind::Bot, Self::Auto)
                | (ActorKind::Bot, Self::Muted)
                | (ActorKind::Human, Self::Present)
                | (ActorKind::Human, Self::Absent)
        )
    }
}

/// A participant in a group session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Participant {
    /// Bot unique identifier (UUID assigned by BCS).
    /// For User participants, this holds the user_id.
    pub bot_uuid: String,
    /// Display name of the bot (for @mention matching by name).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bot_name: Option<String>,
    /// Type of participant (Bot or User). Defaults to Bot for backward compatibility.
    /// When None, treated as Bot (for backward compatibility with existing struct literals).
    #[serde(rename = "type", default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<ParticipantKind>,
    /// Role in this session.
    #[serde(default)]
    pub role: ParticipantRole,
    /// Actor kind for this participant (`Bot` or `Human`).
    /// Defaults to `Bot` for backward compatibility (Requirement 3.3).
    #[serde(default)]
    pub actor_kind: ActorKind,
    /// Per-group collaboration mode.
    ///
    /// Stored as `Option` to keep backward compatibility with legacy struct
    /// literals and serialized rows where the column was absent. When `None`,
    /// callers should use `effective_mode()` which falls back to
    /// `ParticipantMode::default_for(actor_kind)`.
    ///
    /// Application-layer INSERTs MUST always populate this field (see
    /// Requirement 3.10#2 / 3.18#6).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<ParticipantMode>,
}

impl Participant {
    /// Create a bot participant.
    pub fn bot(bot_uuid: impl Into<String>, role: ParticipantRole) -> Self {
        Self {
            bot_uuid: bot_uuid.into(),
            bot_name: None,
            kind: Some(ParticipantKind::Bot),
            role,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
        }
    }

    /// Create a human participant.
    ///
    /// NOTE: `kind` is intentionally left as `None` here so that legacy
    /// callers reading `effective_kind()` still get the default
    /// `ParticipantKind::Bot` (the legacy enum has only one variant in V1
    /// and is being deprecated in favor of `actor_kind`). The authoritative
    /// "is this a Bot or a Human?" answer comes from `actor_kind` via
    /// [`Self::is_bot`] / [`Self::is_human`].
    pub fn human(actor_id: impl Into<String>, role: ParticipantRole) -> Self {
        Self {
            bot_uuid: actor_id.into(),
            bot_name: None,
            kind: None,
            role,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::default_for(ActorKind::Human)),
        }
    }

    /// Get the effective participant kind (defaults to Bot).
    ///
    /// Prefer [`Self::is_bot`] / [`Self::is_human`] which dispatch on
    /// `actor_kind` — the V1 source of truth for actor classification.
    pub fn effective_kind(&self) -> ParticipantKind {
        self.kind.unwrap_or(ParticipantKind::Bot)
    }

    /// Check if this participant is a bot.
    ///
    /// V1: dispatches on `actor_kind`, NOT on the legacy `kind` enum.
    /// Bot-only delivery paths (`bcs-routing`, `bcs-message-flow`,
    /// initial context injection, ...) all rely on
    /// this method to exclude Human participants from Bot-targeted frames.
    pub fn is_bot(&self) -> bool {
        self.actor_kind == ActorKind::Bot
    }

    /// Check if this participant is a Human actor.
    pub fn is_human(&self) -> bool {
        self.actor_kind == ActorKind::Human
    }

    /// Get the effective collaboration mode, falling back to
    /// `ParticipantMode::default_for(actor_kind)` when `mode` is `None`.
    pub fn effective_mode(&self) -> ParticipantMode {
        self.mode
            .unwrap_or_else(|| ParticipantMode::default_for(self.actor_kind))
    }
}

/// Shared workspace for a group session.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Workspace {
    /// Key decisions made.
    #[serde(default)]
    pub decisions: Vec<String>,
    /// Tasks and assignments.
    #[serde(default)]
    pub tasks: Vec<Task>,
    /// Notes and context.
    #[serde(default)]
    pub notes: Vec<String>,
    /// Audit log entries.
    #[serde(default)]
    pub audit_log: Vec<AuditEntry>,
}

/// Status of a group session.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum GroupStatus {
    /// Group is active and accepting messages.
    #[default]
    Active,
    /// Group has completed its task normally.
    Completed,
    /// Group ended with an error (service group instances only).
    Error,
    /// Group was closed by user or system.
    Closed,
    /// Group is inactive (no activity for too long).
    Inactive,
}

/// Routing mode for a group session.
///
/// Controls how BCS processes routing for messages in this group.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum RoutingMode {
    /// Only use structured routing metadata; ignore @mentions in text.
    Structured,
    /// Only use legacy @mention parsing; ignore routing metadata.
    Mention,
    /// Prefer structured metadata; fallback to @mention if none present.
    Hybrid,
}

impl Default for RoutingMode {
    fn default() -> Self {
        Self::Hybrid
    }
}

/// Default delivery strategy when a bot's final reply has no explicit routing.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DefaultDelivery {
    /// Legacy behavior: driver gets chat.send, others get chat.inject.
    SendToDriver,
    /// New behavior: all others get chat.inject, no automatic chat.send.
    InjectObservers,
}

impl Default for DefaultDelivery {
    fn default() -> Self {
        Self::SendToDriver
    }
}

/// Error type for sender_routes validation.
#[derive(Debug, Clone, thiserror::Error)]
pub enum SenderRoutesValidationError {
    #[error("self-referencing: sender '{0}' cannot target itself")]
    SelfReference(String),
    #[error("cycle detected in sender_routes: {0}")]
    CycleDetected(String),
    #[error("bot '{0}' is not a group participant")]
    NotAParticipant(String),
    #[error("sender '{0}' has {1} targets, exceeding limit of 10")]
    TooManyTargets(String, usize),
    #[error("total sender_routes entries ({0}) exceed participant count ({1})")]
    TooManyEntries(usize, usize),
}

/// Group-level routing policy configuration.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RoutingPolicy {
    /// Routing mode (structured/mention/hybrid).
    #[serde(default)]
    pub mode: RoutingMode,
    /// Default delivery when bot final has no explicit routing.
    #[serde(default)]
    pub default_bot_final_delivery: DefaultDelivery,
    /// Static sender-based forwarding table.
    /// Maps sender_bot_id to a list of target_bot_ids that should receive `Send` delivery.
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub sender_routes: HashMap<String, Vec<String>>,
}

/// A group.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Group {
    /// Group ID.
    pub id: String,
    /// Group label (user-friendly name).
    #[serde(default)]
    pub label: Option<String>,
    /// Group status.
    #[serde(default)]
    pub status: GroupStatus,
    /// Driver bot ID.
    pub driver_bot: String,
    /// Originator bot ID (who created this group).
    /// Defaults to driver_bot if not specified.
    #[serde(default)]
    pub originator: Option<String>,
    /// Group-level routing policy.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub routing_policy: Option<RoutingPolicy>,
    /// User-provided group context (optional description of collaboration goal/background).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<String>,
    /// All participants.
    pub participants: Vec<Participant>,
    /// Message history (only stored if store_messages is enabled in config).
    #[serde(default)]
    pub messages: Vec<GroupMessage>,
    /// Shared workspace.
    #[serde(default)]
    pub workspace: Workspace,
    /// Service group UUID (immutable, set at instance creation; None for regular groups).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub service_group_uuid: Option<String>,
    /// Service mode (immutable, set at instance creation; None for regular groups).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub service_mode: Option<String>,
    /// Creation timestamp.
    pub created_at: u64,
    /// Last update timestamp.
    pub updated_at: u64,
    /// Group kind (`Normal` or `Dm`). Defaults to `Normal` for backward
    /// compatibility (Requirement 3.19).
    #[serde(default)]
    pub group_kind: GroupKind,
    /// Canonical pair key for `Dm` groups (`min(a,b)|max(a,b)`).
    /// Always `None` for `Normal` groups; required for `Dm` groups via the
    /// `(env, dm_pair_key)` unique index (see migration 005).
    /// The key is computed from actor ids, not Bot-only ids.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dm_pair_key: Option<String>,
    /// Group strategy: `Chat` (default) or `ManagerWorker`.
    #[serde(default)]
    pub group_strategy: GroupStrategy,
    /// Service-as-a-Group 配置；None = 普通协作群。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub service_spec: Option<crate::session::ServiceSpec>,
    /// 版本号（本期恒为 1，仅 schema 保留）。
    #[serde(default = "default_group_version")]
    pub version: i32,
    /// 记录状态（本期恒为 "active"）。
    #[serde(default = "default_record_status")]
    pub record_status: String,
    /// Group visibility: "private" (default, friends-only) or "public" (open access).
    #[serde(default = "default_visibility_private")]
    pub visibility: String,
}

fn default_group_version() -> i32 {
    1
}

fn default_record_status() -> String {
    "active".to_string()
}

fn default_visibility_private() -> String {
    "private".to_string()
}

impl Group {
    /// Compute the canonical DM pair key for two actor IDs (Task G.2).
    ///
    /// The key is `min(a, b) + "|" + max(a, b)` so that the pair `(A, B)` and
    /// `(B, A)` always produce the same key. Used together with the
    /// `(env, dm_pair_key)` unique index on `bcs_groups` (migration 005) to
    /// guarantee a single DM group per pair per environment.
    ///
    /// Both inputs are compared as `&str`; trimming / case-folding is the
    /// caller's responsibility (we keep this verbatim because actor IDs are
    /// already normalized — Bot UUIDs and `human_{staff_no}` literals).
    pub fn compute_dm_pair_key(a: &str, b: &str) -> String {
        if a <= b {
            format!("{}|{}", a, b)
        } else {
            format!("{}|{}", b, a)
        }
    }

    /// Compare groups for list views: most recently updated first, then
    /// `group_id` ascending for deterministic pagination when timestamps tie.
    pub fn cmp_by_updated_at_desc(a: &Self, b: &Self) -> std::cmp::Ordering {
        b.updated_at
            .cmp(&a.updated_at)
            .then_with(|| a.id.cmp(&b.id))
    }

    /// Sort groups for list views: `updated_at` descending, `group_id`
    /// ascending as a stable tie-breaker.
    pub fn sort_by_updated_at_desc(groups: &mut [Self]) {
        groups.sort_by(Self::cmp_by_updated_at_desc);
    }

    /// Create a new group.
    pub fn new(
        id: impl Into<String>,
        driver_bot: impl Into<String>,
        participants: Vec<Participant>,
    ) -> Self {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);

        Self {
            id: id.into(),
            label: None,
            status: GroupStatus::Active,
            driver_bot: driver_bot.into(),
            originator: None, // Will be set to driver_bot by default
            routing_policy: None,
            context: None,
            participants,
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: now,
            updated_at: now,
            group_kind: GroupKind::Normal,
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        }
    }

    /// Get participant bot IDs (all participants including users).
    pub fn participant_ids(&self) -> Vec<&str> {
        self.participants
            .iter()
            .map(|p| p.bot_uuid.as_str())
            .collect()
    }

    /// Get bot participant IDs only (excludes user participants).
    pub fn bot_participant_ids(&self) -> Vec<&str> {
        self.participants
            .iter()
            .filter(|p| p.is_bot())
            .map(|p| p.bot_uuid.as_str())
            .collect()
    }

    /// Get a participant by ID (bot_uuid or user_id) or by bot_name.
    pub fn get_participant(&self, id: &str) -> Option<&Participant> {
        self.participants
            .iter()
            .find(|p| p.bot_uuid == id || p.bot_name.as_deref().map_or(false, |name| name == id))
    }

    /// Get the driver participant.
    pub fn get_driver(&self) -> Option<&Participant> {
        self.participants
            .iter()
            .find(|p| p.bot_uuid == self.driver_bot)
    }

    /// Get the originator (defaults to driver_bot if not specified).
    pub fn originator(&self) -> &str {
        self.originator.as_deref().unwrap_or(&self.driver_bot)
    }
}

/// Group collaboration strategy; determines the participant role hierarchy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GroupStrategy {
    /// Single-driver chat group. Lead = Driver; participants can be Consultant/Observer.
    #[serde(rename = "chat")]
    Chat,
    /// Manager-Worker service group. Lead = Manager; participants split into Manager/Worker.
    #[serde(rename = "manager_worker")]
    ManagerWorker,
    /// State-machine managed group. Lead remains Driver for chat fallback only.
    #[serde(rename = "state_machine")]
    StateMachine,
}

impl Default for GroupStrategy {
    fn default() -> Self {
        Self::Chat
    }
}

impl GroupStrategy {
    /// The lead role for this strategy — the participant who receives
    /// `chat.send` when no @-mention targets anyone.
    pub fn lead_role(self) -> ParticipantRole {
        match self {
            Self::Chat => ParticipantRole::Driver,
            Self::ManagerWorker => ParticipantRole::Manager,
            Self::StateMachine => ParticipantRole::Driver,
        }
    }

    /// Whether `role` is allowed for participants in a group with this strategy.
    ///
    /// `Observer` is allowed in both. Other roles are strategy-specific.
    /// Ported from legacy `bcs_services::GroupStrategy::allows_role` (commit
    /// 0c775f5b §820).
    pub fn allows_role(self, role: ParticipantRole) -> bool {
        match self {
            Self::Chat | Self::StateMachine => matches!(
                role,
                ParticipantRole::Driver
                    | ParticipantRole::Consultant
                    | ParticipantRole::Observer
            ),
            Self::ManagerWorker => matches!(
                role,
                ParticipantRole::Manager
                    | ParticipantRole::Worker
                    | ParticipantRole::Observer
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sorts_groups_by_updated_at_desc_then_id_asc() {
        let mut groups = vec![
            group_with_updated_at("group-c", 20),
            group_with_updated_at("group-b", 30),
            group_with_updated_at("group-a", 30),
        ];

        Group::sort_by_updated_at_desc(&mut groups);

        let ids = groups
            .iter()
            .map(|group| group.id.as_str())
            .collect::<Vec<_>>();
        assert_eq!(ids, vec!["group-a", "group-b", "group-c"]);
    }

    #[test]
    fn state_machine_strategy_uses_driver_lead_and_chat_roles() {
        let strategy = GroupStrategy::StateMachine;

        assert_eq!(strategy.lead_role(), ParticipantRole::Driver);
        assert!(strategy.allows_role(ParticipantRole::Driver));
        assert!(strategy.allows_role(ParticipantRole::Consultant));
        assert!(strategy.allows_role(ParticipantRole::Observer));
        assert!(!strategy.allows_role(ParticipantRole::Manager));
        assert!(!strategy.allows_role(ParticipantRole::Worker));
    }

    fn group_with_updated_at(id: &str, updated_at: u64) -> Group {
        let mut group = Group::new(
            id,
            "driver",
            vec![Participant::bot("driver", ParticipantRole::Driver)],
        );
        group.updated_at = updated_at;
        group
    }
}
