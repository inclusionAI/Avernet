//! Environment-scoped Session identity; independent of individual run retention.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionType { Group, DirectA2a }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionRegistration {
    pub session_id: String,
    pub session_type: SessionType,
    /// Group sequences remain in the Group Session table.
    pub current_msg_seq: Option<i64>,
}

pub fn validate_direct_session_id(id: &str) -> crate::ServiceResult<()> {
    if id.is_empty() || id.chars().count() > 128 || id.chars().any(char::is_control) {
        return Err(crate::ServiceError::SessionInvalidParams("invalid direct session id".into()));
    }
    Ok(())
}
