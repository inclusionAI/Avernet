use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller};
pub use crate::types::provider_registration::ProviderRegistrationMode;

/// Command for issuing a short-lived bot-registration token.
#[derive(Debug, Clone)]
pub struct IssueRegisterToken {
    pub caller: AuthenticatedCaller,
    pub provider_id: Option<String>,
}

/// A minted register token and its absolute expiry in milliseconds.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegisterTokenView {
    pub token: String,
    pub expires_at: u64,
    pub note: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub registration: Option<RegisterTokenScope>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegisterTokenScope {
    pub token_version: u8,
    pub provider_id: String,
    pub allowed_modes: Vec<ProviderRegistrationMode>,
}

/// Command for registering a bot with a register token.
#[derive(Debug, Clone)]
pub struct RegisterBot {
    pub token: String,
    pub bot_name: String,
    pub mode: Option<ProviderRegistrationMode>,
    pub provider_bot_ref: Option<String>,
    pub webhook_url: Option<String>,
}

/// Credentials returned by a successful bot registration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotRegistration {
    pub bot_name: String,
    pub bot_uuid: String,
    pub bot_token: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub registration: Option<BotRegistrationScope>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotRegistrationScope {
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub mode: ProviderRegistrationMode,
    pub webhook_url: Option<String>,
    pub effective_webhook_url: Option<String>,
}

/// V1 bot registration facade: token issuance (Human principal required) and
/// anonymous token-to-bot registration.
#[async_trait]
pub trait RegisterService: Send + Sync {
    async fn issue_register_token(
        &self,
        command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError>;

    async fn register_bot(
        &self,
        command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError>;
}
