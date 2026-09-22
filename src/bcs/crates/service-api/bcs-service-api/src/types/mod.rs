//! Shared service contract types.
//!
//! `types` is the low-level module that may be used by `application`, `core`,
//! and `port` contracts without creating reverse dependencies between those
//! layers.

pub mod bot_control_plane;
pub mod agent_registration;
pub mod error;
pub mod event;
pub mod provider_registration;

pub use bcs_domain::*;
pub use bot_control_plane::*;
pub use agent_registration::*;
pub use error::{ServiceError, ServiceResult};
pub use event::*;

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
