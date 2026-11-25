//! Request-scoped user ID for a pre-release named-chat test.
use secrecy::{ExposeSecret, Secret};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone)]
pub struct A2aTestUserSelection {
    pub caller_bot_id: String,
    user_id: Secret<String>,
}

impl A2aTestUserSelection {
    pub fn new(caller_bot_id: String, user_id: String) -> Result<Self, &'static str> {
        if user_id.is_empty() || user_id.len() > 32 || !user_id.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err("invalid A2A test user ID");
        }
        Ok(Self { caller_bot_id, user_id: user_id.into() })
    }

    pub fn user_id(&self) -> &str {
        self.user_id.expose_secret().as_str()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct A2aTestUserConfig {
    pub caller_bot_id: String,
    pub provider_id: String,
    pub target_bot_id: String,
    pub interface_url: String,
    pub header_name: String,
}

impl A2aTestUserConfig {
    pub fn validate(&self) -> Result<(), &'static str> {
        if [&self.caller_bot_id, &self.provider_id, &self.target_bot_id]
                .iter().any(|value| value.trim().is_empty() || value.trim() != value.as_str())
            || !self.header_name.starts_with("x-") || !self.header_name.ends_with("-agent-user")
            || !self.header_name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            || !crate::queued_provider_headers::safe_name(&self.header_name)
        {
            return Err("invalid A2A test user configuration");
        }
        Ok(())
    }
}
