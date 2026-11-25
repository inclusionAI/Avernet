use crate::StoreCapability;
use thiserror::Error;

pub type StoreResult<T> = Result<T, StoreError>;

/// Except OutcomeUnknown, an error from a mutating operation guarantees that
/// THIS attempt made no changes. It says nothing about earlier attempts. Do not
/// automatically retry unknown writes, particularly automatic-ID appends.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum StoreError {
    #[error("invalid store input: {0}")]
    InvalidInput(String),
    #[error("unsupported store capability: {0:?}")]
    Unsupported(StoreCapability),
    #[error("incompatible stored value type")]
    WrongType,
    #[error("configured store limit exceeded: {0}")]
    LimitExceeded(String),
    #[error("storage version exhausted")]
    VersionExhausted,
    /// No matching-content or AlreadyApplied guarantee is implied.
    #[error("explicit stream ID does not advance its retained head")]
    IdNotIncreasing,
    #[error("consumer group does not exist")]
    GroupMissing,
    #[error("operation definitely not applied: {0}")]
    NotApplied(String),
    /// Includes a timeout/disconnect after possible delivery or acknowledgement
    /// uncertainty. Resolve by durable identity/content at the service-store layer.
    #[error("write outcome unknown: {0}")]
    OutcomeUnknown(String),
}
