//! Versioned display data and the frozen phase-one output safety profile.
use crate::{ArtifactId, Deadline, EventId, NodeId, OutputClass, Payload};
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const DISPLAY_FORMAT_VERSION: u32 = 1;

/// Ref payloads encode exactly the same logical value as Inline. Projection
/// consumers resolve and verify them before calling the pure projection service.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum ArtifactUpdate {
    Upsert {
        content: Payload,
    },
    /// The resolved value must be a JSON string. Arbitrary JSON is never merged.
    AppendText {
        delta: Payload,
    },
}

/// Stable identity is computed from task/workflow data, never random IDs or
/// retry counts. Node, artifact ID and class together identify the display unit.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ArtifactChange {
    pub node: NodeId,
    pub artifact_id: ArtifactId,
    pub class: OutputClass,
    pub update: ArtifactUpdate,
}

/// Resolved content only. A complete DisplaySnapshot may itself be externalized
/// through TaskContent.snapshot; references never change this inner encoding.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DisplayUnit {
    pub node: NodeId,
    pub artifact_id: ArtifactId,
    pub class: OutputClass,
    pub content: Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DisplaySnapshot {
    pub format_version: u32,
    /// First appearance order; replacing a unit never changes its position.
    pub units: Vec<DisplayUnit>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotPolicy {
    pub max_pending_events: u32,
    pub max_pending_bytes: u64,
    pub max_delay_millis: u64,
}

/// Frozen at task creation, copied unchanged into checkpoint task progress and
/// every published snapshot. A takeover/configuration change cannot replace it.
/// Output content has no per-unit, cumulative or full-snapshot byte quota.
/// The host must also prove its physical checkpoint wrapper and bounded terminal
/// candidates fit these budgets before enabling the runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct OutputLimits {
    pub display_format: u32,
    pub inline_threshold_bytes: u32,
    pub max_checkpoint_bytes: u32,
    pub max_outbox_bytes: u32,
    pub checkpoint_finalization_reserve: u32,
    pub outbox_finalization_reserve: u32,
    pub max_actions: u32,
    pub max_artifacts: u32,
    pub max_queued_tasks: u32,
    pub max_dispatches: u32,
    pub max_identity_bytes: u32,
    pub max_reference_metadata_bytes: u32,
    pub max_runner_state_bytes: u32,
    pub snapshots: SnapshotPolicy,
}

/// Initial safe profile, not a measured provider throughput/capacity claim.
pub const PHASE_ONE_OUTPUT_LIMITS: OutputLimits = OutputLimits {
    display_format: DISPLAY_FORMAT_VERSION,
    inline_threshold_bytes: 65_536,
    max_checkpoint_bytes: 1_048_576,
    max_outbox_bytes: 262_144,
    checkpoint_finalization_reserve: 65_536,
    outbox_finalization_reserve: 65_536,
    max_actions: 32,
    max_artifacts: 128,
    max_queued_tasks: 32,
    max_dispatches: 64,
    max_identity_bytes: 256,
    max_reference_metadata_bytes: 1_024,
    max_runner_state_bytes: 65_536,
    snapshots: SnapshotPolicy {
        max_pending_events: 128,
        max_pending_bytes: 65_536,
        max_delay_millis: 1_000,
    },
};

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct OutputUsage {
    /// Committed Upsert complete values plus AppendText deltas, in encoded JSON
    /// UTF-8 bytes. Diagnostic only, saturating at u64::MAX. Replacement gives no
    /// refund; effect/read replay never counts the same committed change again.
    pub cumulative_bytes: u64,
}

#[derive(Debug, Default, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotProgress {
    pub published_through: Option<EventId>,
    pub pending_events: u32,
    pub pending_bytes: u64,
    /// Persisted stable occurrence identity wakes the last unflushed increment.
    pub flush_deadline: Option<Deadline>,
}
