//! Pre-release named-chat fixture selection. The queue carries references only.
use secrecy::{ExposeSecret, Secret};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct A2aTestUserSelection {
    pub alias: String,
    pub binding_ref: String,
    pub caller_bot_id: String,
}

#[derive(Clone)]
pub struct A2aTestUserAdmission {
    pub selection: A2aTestUserSelection,
    pub target_bot_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct A2aTestUserConfig {
    pub accept_new: bool,
    pub alias: String,
    pub binding_ref: String,
    pub caller_bot_id: String,
    pub provider_id: String,
    pub target_bot_id: String,
    pub interface_url: String,
    pub header_name: String,
    pub user_id_secret: String,
    #[serde(skip)]
    user_id: Option<Secret<String>>,
}

pub fn valid_ref(value: &str) -> bool {
    !value.is_empty() && value.len() <= 64
        && value.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
}

impl A2aTestUserConfig {
    pub fn validate(&self) -> Result<(), &'static str> {
        if !valid_ref(&self.alias) || !valid_ref(&self.binding_ref)
            || [&self.caller_bot_id, &self.provider_id, &self.target_bot_id, &self.user_id_secret]
                .iter().any(|value| value.trim().is_empty() || value.trim() != value.as_str())
            || !self.header_name.starts_with("x-") || !self.header_name.ends_with("-agent-user")
            || !self.header_name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            || !crate::queued_provider_headers::safe_name(&self.header_name)
        {
            return Err("invalid A2A test user configuration");
        }
        Ok(())
    }

    pub fn set_user_id(&mut self, value: String) -> Result<(), &'static str> {
        if value.is_empty() || value.len() > 128 || !value.bytes().all(|b| b.is_ascii_graphic()) {
            return Err("invalid A2A test user value");
        }
        self.user_id = Some(value.into());
        Ok(())
    }

    pub fn user_id(&self) -> Option<&str> {
        self.user_id.as_ref().map(|value| value.expose_secret().as_str())
    }
}
