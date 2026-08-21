//! `BotActorConfigRepoPort` — narrow read of `bcs_bots` config for
//! connect/admission decisions.
//!
//! Deliberately separate from `BotRepoPort`: the legacy bot port returns the
//! heavyweight `RegisteredBot` (capabilities + credentials + runtime
//! connection state) and is marked transitional. ConnectService (T13) and
//! AdmissionService (T14) need only the decision columns already present on
//! `bcs_bots` (`visibility`, `status`, `created_by`) plus the bot internal
//! attributes used for friend gating, so a narrow read avoids pulling that
//! struct and its store into the edge-permission decision path.
use async_trait::async_trait;
use bcs_domain::edge_permission::BotActorConfig;

#[async_trait]
pub trait BotActorConfigRepoPort: Send + Sync {
    /// Read the bot's actor config. `None` if the bot does not exist in `env`
    /// (or the read is unavailable — log + None, non-fallible).
    async fn get(&self, bot_id: &str, env: &str) -> Option<BotActorConfig>;
}