use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Organization {
    pub env: String,
    pub code: String,
    pub name: String,
    pub description: Option<String>,
    pub managing_provider_id: String,
    pub disabled: bool,
    pub created_at: u64,
    pub updated_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrganizationMember {
    pub env: String,
    pub organization_code: String,
    pub bot_uuid: String,
    pub role: Option<String>,
    pub disabled: bool,
    pub created_at: u64,
    pub updated_at: u64,
}
