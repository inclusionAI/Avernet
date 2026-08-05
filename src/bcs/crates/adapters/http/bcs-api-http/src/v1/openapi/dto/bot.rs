use bcs_service_api::application::v1::{
    BotCandidatePurpose, BotDescriptorPatch, BotKind, BotPatch, BotReachability, BotSkill,
    BotStatus, BotVisibility,
};
use serde::Deserialize;

fn default_limit() -> u64 {
    20
}

#[derive(Debug, Clone, Copy, Default, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CandidatePurposeQuery {
    #[default]
    Discovery,
    Collaboration,
}

impl From<CandidatePurposeQuery> for BotCandidatePurpose {
    fn from(value: CandidatePurposeQuery) -> Self {
        match value {
            CandidatePurposeQuery::Discovery => Self::Discovery,
            CandidatePurposeQuery::Collaboration => Self::Collaboration,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListBotCandidatesQuery {
    #[serde(default)]
    pub purpose: CandidatePurposeQuery,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QueryBotsRequest {
    pub bot_ids: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateBotDescriptorRequest {
    #[serde(default)]
    pub summary: Option<String>,
    #[serde(default)]
    pub domains: Option<Vec<String>>,
    #[serde(default)]
    pub skills: Option<Vec<BotSkill>>,
    #[serde(default)]
    pub scopes: Option<Vec<String>>,
}

impl From<UpdateBotDescriptorRequest> for BotDescriptorPatch {
    fn from(value: UpdateBotDescriptorRequest) -> Self {
        Self {
            summary: value.summary,
            domains: value.domains,
            skills: value.skills,
            scopes: value.scopes,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateBotRequest {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub visibility: Option<BotVisibility>,
    #[serde(default)]
    pub status: Option<BotStatus>,
    #[serde(default)]
    pub descriptor: Option<UpdateBotDescriptorRequest>,
}

impl From<UpdateBotRequest> for BotPatch {
    fn from(value: UpdateBotRequest) -> Self {
        Self {
            name: value.name,
            visibility: value.visibility,
            status: value.status,
            descriptor: value.descriptor.map(BotDescriptorPatch::from),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListMyBotsQuery {
    #[serde(default)]
    pub kind: Option<BotKind>,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub status: Option<BotStatus>,
    #[serde(default)]
    pub reachability: Option<BotReachability>,
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
}
