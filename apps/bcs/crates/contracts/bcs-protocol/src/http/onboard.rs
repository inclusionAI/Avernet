use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use crate::{BindingChannels, Skill, deserialize_skills};

/// Request to onboard (register detailed bot info after streaming connection).
#[derive(Debug, Serialize, Deserialize)]
pub struct OnboardRequest {
    /// Bot display name.
    pub name: String,
    /// Bot capability summary.
    #[serde(default)]
    pub summary: Option<String>,
    /// Domains this bot covers.
    #[serde(default)]
    pub domains: Vec<String>,
    /// Skills this bot has.
    #[serde(default, deserialize_with = "deserialize_skills")]
    pub skills: Vec<Skill>,
    /// Access scopes this bot has.
    #[serde(default)]
    pub scopes: Vec<String>,
    /// Channel bindings for message routing.
    #[serde(default)]
    pub binding_channels: Option<BindingChannels>,
}

/// Admin request to onboard a bot by bot_id.
#[derive(Debug, Serialize, Deserialize)]
pub struct AdminOnboardRequest {
    /// Bot ID to onboard.
    pub bot_id: String,
    /// Bot display name.
    #[serde(default)]
    pub name: Option<String>,
    /// Bot capability summary.
    #[serde(default)]
    pub summary: Option<String>,
    /// Domains this bot covers.
    #[serde(default)]
    pub domains: Vec<String>,
    /// Skills this bot has.
    #[serde(default, deserialize_with = "deserialize_skills")]
    pub skills: Vec<Skill>,
    /// Access scopes this bot has.
    #[serde(default)]
    pub scopes: Vec<String>,
    /// Channel bindings for message routing.
    #[serde(default)]
    pub binding_channels: Option<BindingChannels>,
    /// Deprecated hidden flag retained for old clients; ignored by handlers.
    #[serde(default)]
    pub hidden: Option<bool>,
}

/// Response from bot onboard.
#[derive(Debug, Serialize, Deserialize)]
pub struct OnboardResponse {
    pub bot_uuid: String,
    pub onboarded: bool,
    pub name: String,
    /// Binding results for each channel (success/conflict).
    #[serde(default)]
    pub binding_results: HashMap<String, serde_json::Value>,
    /// Channels that were unbound during this onboard.
    #[serde(default)]
    pub unbound: Vec<String>,
}

/// Phase 0 backfill request for `POST /admin/bots/{bot_uuid}/ensure`
/// (spec §4.2 Step 0b). Authenticated by a service credential
/// (`X-BCS-Service-Key`), NOT a user JWT.
#[derive(Debug, Serialize, Deserialize)]
pub struct EnsureBotRequest {
    /// Bot display name. Required for a newly-created bot; preserved on
    /// re-ensure when omitted.
    #[serde(default)]
    pub name: Option<String>,
    /// Bot capability summary.
    #[serde(default)]
    pub summary: Option<String>,
    /// Creator's staff number. Required to bind owner edges; an empty value
    /// skips the owner-edge binding (registration only).
    #[serde(default)]
    pub staff_no: String,
    /// Optional creator nick name persisted on the human actor.
    #[serde(default)]
    pub nick_name: Option<String>,
    /// Bot visibility (`public` / `protected`). Empty preserves existing.
    #[serde(default)]
    pub visibility: String,
    /// Human-facing visibility (`public` / `protected` / `private`) — the
    /// edge-permission attribute deciding whether humans may befriend or act
    /// on this bot. Distinct from `visibility`, which is the bot-network
    /// attribute. `None` preserves the existing attribute. Applied after the
    /// ensure upsert via the internal bot-attributes patch, so a re-ensure
    /// that only binds owner edges can stay a pure no-op.
    #[serde(default)]
    pub user_visibility: Option<String>,
}

/// Response from the Phase 0 ensure endpoint.
#[derive(Debug, Serialize, Deserialize)]
pub struct EnsureBotResponse {
    pub bot_uuid: String,
    pub ensured: bool,
    /// `true` when a new `bcs_bots` row was created; `false` when the bot
    /// already existed.
    pub created: bool,
}
