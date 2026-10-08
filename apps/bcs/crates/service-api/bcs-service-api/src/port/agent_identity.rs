//! Trusted Agent identity verification, independent of Bot registration.

use async_trait::async_trait;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedAgentIdentity {
    pub agent_code: String,
}

/// Closed, credential-free failures. Backend failures must never mean missing
/// identity or missing registration.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentIdentityError {
    InvalidToken,
    NotAgent,
    Unavailable,
}

#[async_trait]
pub trait AgentIdentityPort: Send + Sync {
    /// Verify signature, expiry and identity with the trusted authentication
    /// service. Decoding an unverified JWT is insufficient. Successful results
    /// contain a nonblank, stable agent_code, even before Bot registration.
    /// Implementations must not retain/log tokens or return SDK error details.
    async fn verify(&self, token: &str) -> Result<VerifiedAgentIdentity, AgentIdentityError>;
}
