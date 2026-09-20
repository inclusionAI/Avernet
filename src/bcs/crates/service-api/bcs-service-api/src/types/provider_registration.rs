pub use bcs_domain::provider_registration_token::ProviderRegistrationMode;
use serde::{Deserialize, Serialize};

/// Immutable membership reservation. Runtime credentials are internal; never
/// serialize this record into an API response or log it.
#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderRegistrationRecord {
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub owner: String,
    pub mode: ProviderRegistrationMode,
    pub bot_name: String,
    pub bot_uuid: String,
    pub bot_token: String,
    pub webhook_url: Option<String>,
    pub completed: bool,
}
