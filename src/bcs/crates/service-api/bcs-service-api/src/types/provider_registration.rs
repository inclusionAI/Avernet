pub use bcs_domain::provider_registration_token::ProviderRegistrationMode;

/// One-time registration result, NOT a persisted reservation or replay record.
/// Runtime credentials must only enter the explicit registration response DTO.
#[derive(Clone, PartialEq, Eq)]
pub struct ProviderRegistrationRecord {
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub owner: String,
    pub mode: ProviderRegistrationMode,
    pub bot_name: String,
    pub bot_uuid: String,
    pub bot_token: String,
    pub webhook_url: Option<String>,
}
