//! A2A registration and Card management use cases. The remote Agent protocol is unchanged.
use super::ApplicationError;
pub use crate::application::a2a_bot::{ManagedA2aCard, NamedA2aRegistration};
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
    pub catalog_labels: Option<crate::types::catalog_labels::CatalogLabelsInput>,
    pub token: String,
    pub provider_bot_ref: String,
    pub owners: Option<Vec<String>>,
    pub summary: Option<String>,
    pub visibility: A2aRegistrationVisibility,
    pub agent_id: crate::types::a2a_bot::A2aAgentIdentity,
    pub source: A2aCardInput,
}

pub struct RegisterNamedA2aAgent {
    pub catalog_labels: Option<crate::types::catalog_labels::CatalogLabelsInput>,
    pub token: String,
    pub provider_name: String,
    pub provider_bot_ref: String,
    pub owners: Vec<String>,
    pub summary: Option<String>,
    pub source: A2aCardInput,
}

pub struct UpdateA2aAgent {
    pub catalog_labels: Option<crate::types::catalog_labels::CatalogLabelsInput>,
    pub token: String,
    pub bot_id: String,
    pub source: A2aCardInput,
    pub visibility: Option<A2aRegistrationVisibility>,
    pub agent_id: crate::types::a2a_bot::A2aAgentIdentity,
}

pub struct UpdateA2aAgentMetadata {
    pub token: String,
    pub bot_id: String,
    pub catalog_labels: Option<crate::types::catalog_labels::CatalogLabelsInput>,
    pub status: Option<crate::ActorStatus>,
    pub visibility: Option<A2aRegistrationVisibility>,
    pub user_visibility: Option<crate::application::v1::UserVisibility>,
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
    async fn update_metadata(&self, command: UpdateA2aAgentMetadata) -> Result<ManagedA2aCard, ApplicationError>;
    async fn refresh(&self, token: &str, bot_id: &str) -> Result<ManagedA2aCard, ApplicationError>;
    async fn register_named(
        &self,
        command: RegisterNamedA2aAgent,
    ) -> Result<NamedA2aRegistration, ApplicationError>;
}
