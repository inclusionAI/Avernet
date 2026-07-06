//! BCS bot service: registry storage and application orchestration.

pub mod application;
pub mod core;

pub use application::{
    ActorDirectory, Bot, BotOnboarding, HumanActor, ProviderBotEvents, ProviderManagement,
};
#[allow(deprecated)]
pub use core::BotRegistry;
pub use core::{BotCore, BotInfo, LayottoRegistry, MemoryBotRepo, ProviderCore};
