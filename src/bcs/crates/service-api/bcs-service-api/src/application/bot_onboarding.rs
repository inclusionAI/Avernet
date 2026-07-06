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

#[async_trait]
pub trait BotOnboardingService: Send + Sync {
    async fn onboard_bot(&self, command: BotOnboardCommand) -> ServiceResult<BotOnboardResult>;

    async fn admin_onboard_bot(
        &self,
        command: AdminBotOnboardCommand,
    ) -> ServiceResult<BotOnboardResult>;
}
