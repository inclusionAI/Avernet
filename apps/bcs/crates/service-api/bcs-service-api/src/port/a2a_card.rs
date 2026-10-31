//! Outbound declaration validation. No probe execution or credential values.
use async_trait::async_trait;
use serde_json::Value;
use crate::ServiceResult;
use super::repo::a2a_bot::A2aInterface;

#[derive(Debug, Clone, PartialEq)]
pub struct ValidatedA2aCard {
    pub raw: Value,
    pub interface: A2aInterface,
}

#[async_trait]
pub trait A2aCardPort: Send + Sync {
    /// Fetch a public Card using the configured outbound policy. Auth readiness
    /// is local configuration validation, never proof the remote accepts it.
    async fn fetch(&self, url: &str, bearer_configured: bool) -> ServiceResult<ValidatedA2aCard>;
    /// Validate directly supplied content with the same schema/capability/route
    /// rules as fetched content. Never fetch a source URL or submit a Task.
    async fn validate(&self, _raw: Value, _bearer_configured: bool) -> ServiceResult<ValidatedA2aCard> {
        Err(crate::ServiceError::InvalidOperation {message:"inline AgentCard validation is not configured".into(),request_id:None})
    }
}
