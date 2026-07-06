//! BCSFuse HTTP client wrapper.
//!
//! This crate is intentionally transport-only. Business implementations of BCS
//! service traits live in `services/bcs-fusion`.

pub mod client;
pub mod types;

pub use bcs_config_api::BcsFuseConfig;
pub use client::FuseClient;
pub use types::*;

/// Errors from the bcsfuse client.
#[derive(Debug, thiserror::Error)]
pub enum FuseClientError {
    /// HTTP request failed.
    #[error("HTTP request failed: {0}")]
    HttpRequest(#[from] reqwest::Error),

    /// HTTP error with details.
    #[error("HTTP error: {0}")]
    HttpError(String),

    /// HTTP client build error.
    #[error("HTTP client build error: {0}")]
    HttpClient(reqwest::Error),

    /// Worker not found.
    #[error("Worker not found: {0}")]
    WorkerNotFound(String),

    /// Request timeout.
    #[error("Request timeout")]
    Timeout,

    /// bcsfuse service unavailable.
    #[error("bcsfuse service unavailable")]
    ServiceUnavailable,

    /// Invalid response.
    #[error("Invalid response: {0}")]
    InvalidResponse(String),
}
