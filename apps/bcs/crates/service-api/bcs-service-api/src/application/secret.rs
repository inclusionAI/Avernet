//! Application-layer secret service.
//!
//! Thin orchestration over [`SecretAccessPort`] so HTTP/CLI/WS adapters never
//! hold the port directly (per CLAUDE.md: "HTTP state exposed to route
//! handlers must expose application services, not core services or ports.").
//! Today it is a 1:1 pass-through; future audit/rate-limit/redaction logic
//! goes here.

use async_trait::async_trait;
use thiserror::Error;

use crate::port::secret::SecretAccessError;

#[derive(Debug, Clone)]
pub struct SecretView {
    pub name: String,
    pub user: String,
    pub value: String,
}

#[derive(Debug, Error)]
pub enum SecretServiceError {
    #[error("secret not found: {0}")]
    NotFound(String),
    #[error("secret backend unavailable: {0}")]
    Unavailable(String),
    #[error("invalid secret input: {0}")]
    InvalidInput(String),
}

impl From<SecretAccessError> for SecretServiceError {
    fn from(err: SecretAccessError) -> Self {
        match err {
            SecretAccessError::NotFound(name) => Self::NotFound(name),
            SecretAccessError::Unavailable(msg) => Self::Unavailable(msg),
            SecretAccessError::InvalidInput(msg) => Self::InvalidInput(msg),
        }
    }
}

#[async_trait]
pub trait SecretService: Send + Sync + 'static {
    async fn get_secret(&self, name: &str) -> Result<SecretView, SecretServiceError>;
}
