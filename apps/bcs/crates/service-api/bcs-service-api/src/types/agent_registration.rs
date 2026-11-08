//! Credential-free, persisted Bot registration projection.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentBotRegistration {
    pub bot_id: String,
    pub name: Option<String>,
    pub summary: Option<String>,
    /// Both Provider fields are absent for legacy Bots without affiliation.
    pub provider_id: Option<String>,
    pub provider_bot_ref: Option<String>,
}

#[derive(Debug, Clone)]
pub struct AgentBotQuery {
    pub agent_code: String,
    pub bot_uuid: Option<String>,
    pub provider_id: Option<String>,
    pub cursor: Option<String>,
    pub limit: usize,
    pub implicit_only: bool,
}

/// Credential-free authoritative identity snapshot. Offline Bots remain registered.
#[derive(Debug, Clone)]
pub struct AgentBotRecord {
    pub bot: AgentBotRegistration,
    pub agent_code: String,
    pub owner_id: Option<String>,
    pub provider_auth_mode: Option<crate::ProviderAuthMode>,
    pub provider_disabled: bool,
    pub binding_disabled: bool,
    /// Persisted actor status (online / hidden); separate from connection liveness.
    pub status: String,
    pub provider_metadata_loaded: bool,
}
