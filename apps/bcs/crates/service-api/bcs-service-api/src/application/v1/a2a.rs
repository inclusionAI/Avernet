//! A2A control-plane use cases. The remote Agent protocol is unchanged.
use super::ApplicationError;
pub use crate::application::a2a_bot::{ManagedA2aCard, NamedA2aRegistration};
pub use crate::application::a2a_chat::ChatResponseMode;
pub use crate::types::a2a_bot::A2aRegistrationVisibility;
pub use crate::types::a2a_bot::a2a_management::parse_card_json;
use async_trait::async_trait;
use serde_json::Value;

#[derive(Clone, Default)]
pub struct A2aCardInput {
    pub agent_card_url: Option<String>,
    pub agent_card: Option<Value>,
}

pub struct RegisterA2aAgent {
    pub token: String,
    pub provider_bot_ref: String,
    pub owners: Option<Vec<String>>,
    pub summary: Option<String>,
    pub visibility: A2aRegistrationVisibility,
    pub source: A2aCardInput,
}

pub struct RegisterNamedA2aAgent {
    pub token: String,
    pub provider_name: String,
    pub provider_bot_ref: String,
    pub owners: Vec<String>,
    pub summary: Option<String>,
    pub source: A2aCardInput,
}

pub struct UpdateA2aAgent {
    pub token: String,
    pub bot_id: String,
    pub source: A2aCardInput,
    pub visibility: Option<A2aRegistrationVisibility>,
}

pub struct StartNamedA2aChat {
    pub token: String,
    pub actor: Option<String>,
    pub provider_name: String,
    pub provider_bot_ref: String,
    pub message: String,
    pub from: Option<String>,
    pub session_id: Option<String>,
    pub timeout_ms: Option<u64>,
    pub client: Option<String>,
    pub tags: Vec<String>,
    pub response_mode: ChatResponseMode,
    pub caller_wait_mode: Option<String>,
    pub organization_code: Option<String>,
    pub provider_bypass_headers: Vec<(String, String)>,
}

pub struct QueryA2aRun {
    pub token: String,
    pub actor: Option<String>,
    pub run_id: String,
    pub wait_ms: u64,
    pub since_version: u64,
}

#[async_trait]
pub trait A2aService: Send + Sync {
    async fn register(&self, command: RegisterA2aAgent)
    -> Result<ManagedA2aCard, ApplicationError>;
    async fn find(
        &self,
        token: &str,
        provider_bot_ref: &str,
    ) -> Result<Vec<ManagedA2aCard>, ApplicationError>;
    async fn get(&self, token: &str, bot_id: &str) -> Result<ManagedA2aCard, ApplicationError>;
    async fn update(&self, command: UpdateA2aAgent) -> Result<ManagedA2aCard, ApplicationError>;
    async fn refresh(&self, token: &str, bot_id: &str) -> Result<ManagedA2aCard, ApplicationError>;
    async fn register_named(
        &self,
        command: RegisterNamedA2aAgent,
    ) -> Result<NamedA2aRegistration, ApplicationError>;
    async fn chat(&self, command: StartNamedA2aChat) -> Result<Value, ApplicationError>;
    async fn get_run(&self, command: QueryA2aRun) -> Result<Value, ApplicationError>;
    async fn cancel_run(
        &self,
        token: &str,
        actor: Option<&str>,
        run_id: &str,
    ) -> Result<Value, ApplicationError>;
}
