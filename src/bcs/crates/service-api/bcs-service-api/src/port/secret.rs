//! Outbound infrastructure port for reading secrets.
//!
//! Lives next to `port::repo` because it is "infra abstraction the core
//! consumes": a core service that needs a credential calls a
//! [`SecretAccessPort`] trait without caring whether the value comes from
//! mist, an env-var snapshot, or a JSON file on disk.

use async_trait::async_trait;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SecretAccessError {
    #[error("secret not found: {0}")]
    NotFound(String),

    /// Backend is wired but configured as disabled, unreachable, or rejected
    /// the request. Distinct from `NotFound` so callers can decide whether to
    /// fall back to a default secret.
    #[error("secret backend unavailable: {0}")]
    Unavailable(String),

    #[error("invalid secret input: {0}")]
    InvalidInput(String),
}

#[derive(Debug, Clone)]
pub struct SecretRecord {
    pub name: String,
    pub user: String,
    pub value: String,
}

#[async_trait]
pub trait SecretAccessPort: Send + Sync + 'static {
    async fn get_secret(&self, name: &str) -> Result<SecretRecord, SecretAccessError>;
}
