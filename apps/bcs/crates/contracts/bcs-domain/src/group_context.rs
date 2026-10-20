//! Group Context pure domain model.
//!
//! A Group Context entry is a versioned, permission-tagged string value owned by
//! a `(name, scope)` chain. Storage keeps superseded historical rows while at
//! most one row per `(name, scope)` remains active (`valid_to = None`).

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Date/time type used by persisted Group Context entries.
pub type GroupContextTime = DateTime<Utc>;

/// Context scope level. The raw scope value is the corresponding id supplied by
/// the request context: `groupId`, `sessionId`, or `roundId`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ScopeLevel {
    Group,
    Session,
    Round,
}

impl ScopeLevel {
    /// The default lookup/list scope order exposed by the V1 contract.
    pub const DEFAULT_LEVELS: [Self; 3] = [Self::Group, Self::Session, Self::Round];

    /// Specific-to-broad ordering used by `get` responses.
    pub const SPECIFIC_TO_BROAD: [Self; 3] = [Self::Round, Self::Session, Self::Group];
}

/// Extensible JSON-object policy used by `visible_to` and `collect_from`.
///
/// V1 only interprets `userIds`; unknown object fields are retained so the
/// shape can evolve without changing the storage model.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroupContextPolicy {
    #[serde(rename = "userIds", default)]
    pub user_ids: Vec<String>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, serde_json::Value>,
}

impl GroupContextPolicy {
    pub fn users(user_ids: impl IntoIterator<Item = impl Into<String>>) -> Self {
        Self {
            user_ids: user_ids.into_iter().map(Into::into).collect(),
            extra: serde_json::Map::new(),
        }
    }

    /// Empty `visible_to.userIds` means visible to everyone.
    pub fn is_visible_to(&self, actor_id: &str) -> bool {
        self.user_ids.is_empty() || self.user_ids.iter().any(|id| id == actor_id)
    }

    /// `collect_from.userIds` is a membership list. Empty means there are no
    /// additional policy writers; it never means everyone.
    pub fn contains_user(&self, actor_id: &str) -> bool {
        self.user_ids.iter().any(|id| id == actor_id)
    }
}

/// Multi-value Group Context ownership.
///
/// Newly created and API-supplied owner lists must be non-empty. Rows that
/// predate this field can have no owners: SQL stores that legacy state as
/// `NULL`, and the store maps it to an empty list here. Empty lists never
/// grant owner permissions. `userIds` is the stable wire field name and is
/// not reused from the generic policy type.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroupContextOwners {
    #[serde(rename = "userIds")]
    pub user_ids: Vec<String>,
}

impl GroupContextOwners {
    pub fn new(user_ids: impl IntoIterator<Item = impl Into<String>>) -> Self {
        Self {
            user_ids: user_ids.into_iter().map(Into::into).collect(),
        }
    }

    pub fn contains(&self, actor_id: &str) -> bool {
        self.user_ids.iter().any(|id| id == actor_id)
    }

    pub fn is_empty(&self) -> bool {
        self.user_ids.is_empty()
    }
}

/// Read/write permission label returned by list operations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum GroupContextPermission {
    R,
    W,
    WR,
    WRO,
}

impl GroupContextPermission {
    pub fn from_access(readable: bool, writable: bool) -> Option<Self> {
        match (readable, writable) {
            (true, true) => Some(Self::WR),
            (true, false) => Some(Self::R),
            (false, true) => Some(Self::W),
            (false, false) => None,
        }
    }
}

/// Lightweight projection used by list operations. This intentionally omits
/// the payload and validity fields because list responses only need metadata
/// plus the policy columns used by permission filtering.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroupContextEntryMeta {
    pub name: String,
    pub scope_level: ScopeLevel,
    pub scope: String,
    pub visible_to: GroupContextPolicy,
    pub collect_from: GroupContextPolicy,
    pub owners: GroupContextOwners,
    pub gmt_create: GroupContextTime,
    pub gmt_modified: GroupContextTime,
}

/// Persisted version row. `id` is the physical row id; `context_id` is stable
/// across versions in the same supersede chain.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroupContextEntry {
    pub id: i64,
    pub context_id: String,
    pub name: String,
    pub scope_level: ScopeLevel,
    /// Raw scope id selected by `scope_level`; no group/session/round
    /// concatenation is performed.
    pub scope: String,
    pub content: String,
    /// The actor that wrote this version row. Business updates create a new
    /// row, so this records the write-time creator rather than being mutated.
    pub creator: String,
    pub visible_to: GroupContextPolicy,
    pub collect_from: GroupContextPolicy,
    pub owners: GroupContextOwners,
    /// Physical `id` of the old version superseded by this row.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub supersedes: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub change_reason: Option<String>,
    pub valid_from: GroupContextTime,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_to: Option<GroupContextTime>,
    /// System version marker. `false` means this is the current version in
    /// its `(name, scope)` chain; business `valid_to` does not affect this.
    #[serde(default)]
    pub is_superseded: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gmt_superseded: Option<GroupContextTime>,
    pub tx_time: GroupContextTime,
    pub gmt_create: GroupContextTime,
    pub gmt_modified: GroupContextTime,
}

impl GroupContextEntry {
    pub fn is_active(&self) -> bool {
        !self.is_superseded
    }
}
