pub mod bot_core;
mod ids;
pub mod provider_core;

pub use bcs_bot_store::{BotInfo, LayottoRegistry, MemoryBotRepo};
pub use bot_core::BotCore;
pub use provider_core::ProviderCore;

#[deprecated(note = "Use BotCore; BotRegistry is a temporary compatibility alias.")]
pub type BotRegistry = BotCore;
