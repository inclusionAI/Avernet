//! Application-layer error re-exports.

pub use super::{
    bot_management::BotUseCaseError, friends::FriendUseCaseError,
    group_management::GroupUseCaseError, human_actor::EnsureCurrentHumanActorError,
};
