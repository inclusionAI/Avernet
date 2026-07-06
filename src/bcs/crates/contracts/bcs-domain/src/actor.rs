//! Actor / relation pure domain types.

use serde::{Deserialize, Serialize};

/// Kind of network actor.
///
/// V1 introduces `Human` alongside the existing `Bot`, enabling Human Actors
/// (identified as `human_{staff_no}`) to participate in BCS as first-class
/// network members.
///
/// See `docs/specs/bcs-human-actor/requirements.md` Requirement 3.2 / 3.16.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ActorKind {
    /// Bot actor (default; preserves backward compatibility).
    #[default]
    Bot,
    /// Human actor, backed by an Ant staff_no.
    Human,
}

/// Actor-level lifecycle / presence status.
///
/// Orthogonal to `visibility` (privacy) and per-group `mode` (collaboration
/// posture). V1 only defines `Online` (default) and `Hidden`; `Hidden` means
/// "do not push popup notifications, but messages still flow into transcripts".
///
/// See `docs/specs/bcs-human-actor/requirements.md` Requirement 3.16.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ActorStatus {
    /// Online (default). Receivers may show ring / popup / desktop notifications.
    #[default]
    Online,
    /// Hidden. Messages are still delivered and persisted, but receivers should
    /// not raise interruptive notifications. Sender side is unrestricted.
    Hidden,
}

/// Result of `ensure_human_actor` — tells the caller whether the row was
/// newly created or already existed.
#[derive(Debug, Clone)]
pub struct EnsureHumanResult {
    /// `true` if a new `bcs_bots` row was inserted; `false` if it already existed.
    pub created: bool,
}

/// Result of `ensure_owner_edges_counted` — per-bot edge creation summary.
#[derive(Debug, Clone, Default)]
pub struct EnsureOwnerEdgesResult {
    /// Number of edges newly inserted (0, 1, or 2).
    pub created: u32,
    /// Number of edges where `is_creator` was upgraded from `false` to `true` (0 or 1).
    pub upgraded: u32,
}

/// A directed edge in the BCS relation graph (`bcs_actor_relations`).
///
/// V1 only consumes `is_creator`; `kinds` / `allow` / `deny` are bitmap
/// columns reserved for V2 fine-grained permissions and are always written as
/// `0` in V1.
///
/// See `docs/specs/bcs-human-actor/design.md` §4.1 / §4.3.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RelationEdge {
    /// Source actor id.
    pub from_id: String,
    /// Target actor id.
    pub to_id: String,
    /// Server environment (prod / gray / pre / dev).
    pub env: String,
    /// Reserved 64-bit bitmap for relation kinds (V1 always 0).
    #[serde(default)]
    pub kinds: u64,
    /// Reserved 64-bit bitmap for permission allow flags (V1 always 0).
    #[serde(default)]
    pub allow: u64,
    /// Reserved 64-bit bitmap for permission deny flags (V1 always 0).
    #[serde(default)]
    pub deny: u64,
    /// Whether `from_id` is the creator (owner) of `to_id`.
    /// In V1 this is the only field business logic reads.
    #[serde(default)]
    pub is_creator: bool,
}
