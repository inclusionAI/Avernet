//! Compatibility hooks for the legacy `/service-groups/*` API.

pub mod legacy;
pub use legacy::{execute_callback, recover_pending_callbacks};
