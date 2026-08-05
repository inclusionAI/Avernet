pub mod bot_core;
pub mod bot_control_plane_core;
mod ids;
pub mod provider_core;

pub use bcs_bot_store::{BotInfo, PersistentBotRepo, MemoryBotRepo};
pub use bot_core::BotCore;
pub use bot_control_plane_core::BotControlPlaneCore;
pub use provider_core::ProviderCore;

#[deprecated(note = "Use BotCore; BotRegistry is a temporary compatibility alias.")]
pub type BotRegistry = BotCore;
