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
