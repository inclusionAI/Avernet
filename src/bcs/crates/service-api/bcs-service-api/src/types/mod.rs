//! Shared service contract types.
//!
//! `types` is the low-level module that may be used by `application`, `core`,
//! and `port` contracts without creating reverse dependencies between those
//! layers.

pub mod bot_control_plane;
pub mod error;

pub use bot_control_plane::*;
pub use bcs_domain::*;
pub use error::{ServiceError, ServiceResult};

/// Mutable fields exposed by the BCN OpenAPI v1 Group PATCH operation.
///
/// Each `None` means "leave the stored value unchanged". Keeping this patch
/// typed and field-scoped prevents a read-modify-upsert cycle from replacing
/// participants, routing extensions, or other state changed concurrently.
#[derive(Debug, Clone, Default)]
pub struct GroupMutableFieldsPatch {
    pub label: Option<String>,
    pub context: Option<String>,
    pub visibility: Option<String>,
    pub default_bot_final_delivery: Option<DefaultDelivery>,
}
