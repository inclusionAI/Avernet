//! Bot onboarding use-case contracts.

use std::collections::HashMap;

use async_trait::async_trait;
use serde_json::Value;

use crate::core::{ActorKind, BindingChannels, BotCapabilities, ServiceResult, Skill};

#[derive(Debug, Clone)]
pub struct OnboardActorIdentity {
    pub staff_no: String,
    pub nick_name: Option<String>,
}

#[derive(Debug, Clone)]
pub struct BotOnboardCommand {
    pub bot_uuid: String,
    pub name: String,
    pub summary: Option<String>,
    pub domains: Vec<String>,
    pub skills: Vec<Skill>,
    pub scopes: Vec<String>,
    pub binding_channels: Option<BindingChannels>,
    pub agent_code: Option<String>,
    pub agent_token: Option<String>,
    pub actor_identity: Option<OnboardActorIdentity>,
}

#[derive(Debug, Clone)]
pub struct AdminBotOnboardCommand {
    pub bot_uuid: String,
    pub name: Option<String>,
    pub summary: Option<String>,
    pub domains: Vec<String>,
    pub skills: Vec<Skill>,
    pub scopes: Vec<String>,
    pub binding_channels: Option<BindingChannels>,
    pub actor_identity: Option<OnboardActorIdentity>,
}

#[derive(Debug, Clone)]
pub struct BotOnboardResult {
    pub bot_uuid: String,
    pub onboarded: bool,
    pub name: Option<String>,
    pub message: Option<String>,
    pub binding_results: HashMap<String, Value>,
    pub unbound: Vec<String>,
    pub capabilities: Option<BotCapabilities>,
    pub actor_kind: ActorKind,
}

/// Command for the Phase 0 `ensure_bot` backfill (spec §8.2 Phase 0, §4.2 Step 0b).
///
/// Unlike [`BotOnboardCommand`] this is a service-credential call (no user JWT):
/// it registers the bot if absent and binds the owner identity in one
/// idempotent call. Empty `"staff_no"` skips the owner-edge binding (registration
/// only).
#[derive(Debug, Clone)]
pub struct EnsureBotCommand {
    pub bot_uuid: String,
    pub name: String,
    pub summary: Option<String>,
    pub visibility: String,
    pub actor_identity: Option<OnboardActorIdentity>,
}

/// Outcome of [`BotOnboardingService::ensure_bot`].
#[derive(Debug, Clone)]
pub struct EnsureBotResult {
    pub bot_uuid: String,
    pub ensured: bool,
    /// `true` when a new `bcs_bots` row was created; `false` when it already
    /// existed and was only updated.
    pub created: bool,
}

#[async_trait]
pub trait BotOnboardingService: Send + Sync {
    async fn onboard_bot(&self, command: BotOnboardCommand) -> ServiceResult<BotOnboardResult>;

    async fn admin_onboard_bot(
        &self,
        command: AdminBotOnboardCommand,
    ) -> ServiceResult<BotOnboardResult>;

    /// Phase 0 idempotent backfill: ensure the bot is registered in `bcs_bots`,
    /// bind the creator's human actor + owner edges, and seed the default
    /// permission profile. Returns `created=true` when the bot row was newly
    /// inserted, `false` when it already existed (spec §4.2 Step 0b).
    async fn ensure_bot(&self, command: EnsureBotCommand) -> ServiceResult<EnsureBotResult>;
}
