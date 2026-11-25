//! Startup capability checks: a provider refuses to start against a store that
//! lacks a capability it relies on, instead of failing on the first request.

use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::{StoreCapability, StorePlugin};

/// Checks every `(plugin, capability)` pair; the first missing one fails with
/// `NotApplied { code: "missing_store_capability:<Capability>" }`.
pub fn require(checks: &[(&dyn StorePlugin, StoreCapability)]) -> Result<(), StoreError> {
    for (plugin, capability) in checks {
        if !plugin.supports(*capability) {
            return Err(StoreError::NotApplied {
                code: format!("missing_store_capability:{capability:?}"),
            });
        }
    }
    Ok(())
}
