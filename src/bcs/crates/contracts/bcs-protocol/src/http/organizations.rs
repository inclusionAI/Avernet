use serde::{Deserialize, Serialize};

use crate::BotCapabilities;

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
pub struct OrganizationListResponse {
    pub organizations: Vec<OrganizationResponse>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrganizationMemberListResponse {
    pub members: Vec<OrganizationMemberResponse>,
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
}
