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
