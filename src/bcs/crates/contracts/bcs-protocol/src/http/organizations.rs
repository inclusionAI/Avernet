use serde::{Deserialize, Serialize};

use crate::{BotCapabilities, Skill};

#[derive(Debug, Clone, Deserialize)]
pub struct CreateOrganizationRequest {
    pub organization_code: String,
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PatchOrganizationRequest {
    pub name: Option<String>,
    #[serde(default)]
    pub description: Option<Option<String>>,
    pub disabled: Option<bool>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PutOrganizationMemberRequest {
    #[serde(default)]
    pub role: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct PatchOrganizationMemberProfileRequest {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub summary: Option<String>,
    #[serde(default)]
    pub domains: Option<Vec<String>>,
    #[serde(default)]
    pub skills: Option<Vec<Skill>>,
    #[serde(default)]
    pub scopes: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationResponse {
    pub organization_code: String,
    pub name: String,
    pub description: Option<String>,
    pub managing_provider_id: String,
    pub disabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationMemberResponse {
    pub organization_code: String,
    pub bot_uuid: String,
    pub role: Option<String>,
    pub disabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationMemberBotResponse {
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub agent_code: Option<String>,
    pub name: Option<String>,
    pub summary: Option<String>,
    pub domains: Vec<String>,
    pub skills: Vec<Skill>,
    pub scopes: Vec<String>,
    pub visibility: String,
    pub created_by: Option<String>,
    pub actor_kind: String,
    pub env: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationMemberDetailResponse {
    pub organization_code: String,
    pub bot_uuid: String,
    pub role: Option<String>,
    pub disabled: bool,
    pub bot: Option<OrganizationMemberBotResponse>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationMemberProfileResponse {
    pub organization_code: String,
    pub bot_uuid: String,
    pub provider_id: String,
    pub profile: BotCapabilities,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationListResponse {
    pub organizations: Vec<OrganizationResponse>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationMemberListResponse {
    pub members: Vec<OrganizationMemberResponse>,
    pub offset: u64,
    pub limit: u64,
    pub total: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationCandidateBotResponse {
    pub bot_uuid: String,
    pub provider_id: String,
    pub capabilities: BotCapabilities,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationCandidateBotListResponse {
    pub bots: Vec<OrganizationCandidateBotResponse>,
    pub offset: u64,
    pub limit: u64,
    pub total: u64,
}
