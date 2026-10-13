use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InviteConfig {
    #[serde(default)]
    pub token_secret: Option<String>,

    #[serde(default)]
    pub token_secret_secret: Option<String>,

    /// Whether the invite-code access gate is enabled.
    /// When false or unset, protected routes do not enforce invite-code binding.
    #[serde(default)]
    pub invite_code_gate_enabled: bool,

    /// Whether anonymous OpenAPI callers may claim a newly generated invite code.
    #[serde(default)]
    pub public_claim_enabled: bool,

    /// Maximum number of invite codes that anonymous OpenAPI callers may claim.
    #[serde(default = "default_public_claim_max_count")]
    pub public_claim_max_count: u64,

    #[serde(default = "default_invite_ttl_seconds")]
    pub default_ttl_seconds: u64,

    #[serde(default)]
    pub base_url: Option<String>,

    #[serde(default)]
    pub group_link_url: Option<String>,

    #[serde(default)]
    pub session_link_url: Option<String>,
}

impl Default for InviteConfig {
    fn default() -> Self {
        Self {
            token_secret: None,
            token_secret_secret: None,
            invite_code_gate_enabled: false,
            public_claim_enabled: false,
            public_claim_max_count: default_public_claim_max_count(),
            default_ttl_seconds: default_invite_ttl_seconds(),
            base_url: None,
            group_link_url: None,
            session_link_url: None,
        }
    }
}

fn default_public_claim_max_count() -> u64 {
    1_000
}

fn default_invite_ttl_seconds() -> u64 {
    86400
}
