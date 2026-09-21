//! Persisted Bot delivery mode and the migration-time delivery read source.

use serde::{Deserialize, Serialize};

// Provider administration, registration tokens and Bot records share one type.
pub use crate::provider::ProviderBotConnectionMode as BotConnectionMode;

/// Selects how the delivery resolver determines a Bot's transport. This does
/// not select a registration writer or grant Provider management privileges.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DownlinkDetectionSource {
    #[default]
    Binding,
    BotConnectionMode,
}

/// Provider metadata stored on the Bot row. Deletion is the Bot's tombstone,
/// not a second independently managed binding lifecycle. Contains no secrets.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotProviderRecord {
    pub bot_uuid: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub connection_mode: BotConnectionMode,
    pub webhook_url: Option<String>,
    pub is_deleted: bool,
    pub registered_at: u64,
    pub updated_at: u64,
}
