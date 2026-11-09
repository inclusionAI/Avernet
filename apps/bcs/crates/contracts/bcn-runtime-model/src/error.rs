use serde::{Deserialize, Serialize};
use thiserror::Error;

pub type RuntimeResult<T> = Result<T, RuntimeError>;
pub type ConnectorResult<T> = Result<T, ConnectorError>;
pub type StorageResult<T> = Result<T, StorageError>;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Error)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeError {
    #[error("invalid input: {0}")] InvalidInput(String),
    #[error("unsupported: {0}")] Unsupported(String),
    #[error("access denied")] AccessDenied,
    #[error("not found")] NotFound,
    #[error("conflict: {0}")] Conflict(String),
    #[error("capacity exceeded")] CapacityExceeded,
    #[error("execution_unknown")] ExecutionUnknown,
    #[error("cancellation_unknown")] CancellationUnknown,
    #[error("context_blocked")] ContextBlocked,
    #[error("ownership lost")] OwnershipLost,
    #[error("incompatible schema: {0}")] IncompatibleSchema(u32),
    #[error("storage: {0}")] Storage(String),
    #[error("unavailable: {0}")] Unavailable(String),
}

/// A transport error must preserve whether an external side effect is possible.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Error)]
#[serde(rename_all = "snake_case")]
pub enum ConnectorError {
    #[error("not sent: {0}")] NotSent(String),
    #[error("possibly accepted: {0}")] MayHaveBeenAccepted(String),
    #[error("remote failure: {code}: {message}")] RemoteFailed { code: String, message: String },
    #[error("unsupported operation: {0}")] Unsupported(String),
    #[error("ownership lost")] OwnershipLost,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum StorageError {
    #[error("invalid scope or key: {0}")] InvalidKey(String),
    #[error("request exceeds limit: {0}")] LimitExceeded(String),
    #[error("condition failed for {key}")] ConditionFailed { key: String },
    #[error("invalid scan cursor")] InvalidCursor,
    #[error("duplicate mutation key: {0}")] DuplicateMutation(String),
    #[error("memory storage cannot serve production")] UnsupportedProfile,
    #[error("revision space exhausted")] RevisionExhausted,
    #[error("storage unavailable: {0}")] Unavailable(String),
}
