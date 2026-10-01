//! Shared ID and token generators used by both BCN onboarding (bot_core) and
//! provider-driven registration (provider_core).
//!
//! Keeping these in one place ensures bots onboarded via either path get the
//! same `bot_<8 hex>` UUID and the same plain-UUID runtime token, so callers
//! and persistence layers do not have to special-case provider bots.

/// Generates a bot UUID in the canonical `bot_<8 hex>` form (e.g. `bot_fc47bf4b`).
pub(crate) fn new_bot_uuid() -> String {
    format!("bot_{}", &uuid::Uuid::new_v4().to_string()[..8])
}

/// Generates a bot session/runtime token using BCN's format: a plain UUID v4 string.
pub(crate) fn new_session_token() -> String {
    uuid::Uuid::new_v4().to_string()
}

/// Generates a provider ID in the canonical `prv_<8 hex>` form, matching the
/// shape of bot UUIDs.
pub(crate) fn new_provider_id() -> String {
    format!("prv_{}", &uuid::Uuid::new_v4().to_string()[..8])
}
