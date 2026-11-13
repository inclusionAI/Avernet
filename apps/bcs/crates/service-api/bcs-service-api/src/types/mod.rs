//! Shared service contract types.
//!
//! `types` is the low-level module that may be used by `application`, `core`,
//! and `port` contracts without creating reverse dependencies between those
//! layers.

pub mod collaboration_draft;
pub mod bot_authority;
pub mod bot_control_plane;
pub mod bot_profile;
pub mod agent_registration;
pub mod authorization_snapshot;
pub mod error;
pub mod event;
pub mod provider_registration;

pub use bcs_domain::*;
pub use authorization_snapshot::*;
pub use bot_authority::*;
pub use bot_control_plane::*;
pub use agent_registration::*;
pub use error::{ServiceError, ServiceResult};
pub use event::*;

/// One current Group row and its optional stored Card template, read together.
/// A missing template is valid for Chat/DM and historical unbackfilled Groups.
/// Stores project current label/context into the template's display fields while
/// preserving interfaces, skills and extensions; reads never persist a backfill.
#[derive(Debug, Clone)]
pub struct GroupCardSnapshot {
    pub group_id: String,
    pub group_kind: GroupKind,
    pub group_strategy: GroupStrategy,
    pub visibility: String,
    pub record_status: String,
    pub card_template: Option<serde_json::Value>,
}

/// Mutable fields exposed by the BCN OpenAPI v1 Group PATCH operation.
///
/// Each `None` means "leave the stored value unchanged". Keeping this patch
/// typed and field-scoped prevents a read-modify-upsert cycle from replacing
/// participants, routing extensions, or other state changed concurrently.
#[derive(Debug, Clone, Default)]
pub struct GroupMutableFieldsPatch {
    pub label: Option<String>,
    pub context: Option<String>,
    /// Outer `None` leaves the field unchanged; `Some(None)` restores the default.
    pub opening_message: Option<Option<OpeningMessage>>,
    pub visibility: Option<String>,
    pub default_bot_final_delivery: Option<DefaultDelivery>,
    /// Per-group human-mention notify mode. `None` means "leave the stored
    /// value unchanged"; `Some(mode)` overwrites the stored Group value.
    /// No `Option<Option<_>>` is needed because `null` is rejected at the
    /// HTTP boundary and there is no "clear to null" state.
    pub human_mention_notify_mode: Option<HumanMentionNotifyMode>,
}

/// Read-only snapshot of the Group policy that controls whether
/// human-mention messages notify external channels. Returned by the
/// fail-closed `GroupRepoPort::read_human_notify_policy` /
/// `GroupCoreService::read_human_notify_policy` defaults; production
/// Group Core/Store implementations override them in Task 2.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupHumanNotifyPolicy {
    pub mode: HumanMentionNotifyMode,
    pub driver_bot_id: String,
}

pub mod a2a_bot;

pub mod run_budget;
