use serde::{Deserialize, Serialize};
use crate::{StorageError, StorageResult};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Scope {
    pub environment: String,
    pub tenant: String,
    pub namespace: String,
}
impl Scope {
    pub fn new(environment: impl Into<String>, tenant: impl Into<String>, namespace: impl Into<String>) -> Self {
        Self { environment: environment.into(), tenant: tenant.into(), namespace: namespace.into() }
    }
    /// Validate on every infrastructure boundary, including deserialized input.
    pub fn validate(&self) -> StorageResult<()> {
        for part in [&self.environment, &self.tenant, &self.namespace] {
            validate_component(part)?;
        }
        Ok(())
    }
}
pub fn validate_component(value: &str) -> StorageResult<()> {
    if value.is_empty() || value.len() > 64 || !value.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)) {
        return Err(StorageError::InvalidKey("scope/partition must contain 1..64 ASCII letters, digits, _ or -".into()));
    }
    Ok(())
}

macro_rules! scoped_id {
    ($($name:ident),+ $(,)?) => {$ (
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
        pub struct $name { pub scope: Scope, pub value: String }
        impl $name {
            pub fn new(scope: Scope, value: impl Into<String>) -> Self { Self { scope, value: value.into() } }
        }
    )+};
}
scoped_id!(AgentId, GroupId, SessionId, TaskId, DispatchId, OperationId, InteractionId,
    EventId, TimerId, ResultId, MessageId, AdmissionId, CommandId);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CallerContext {
    pub scope: Scope,
    /// Authenticated principal, supplied by the delivery boundary, not message data.
    pub principal: String,
    pub trace_id: Option<String>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionTarget { Agent { agent_id: AgentId }, Group { group_id: GroupId } }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeProfile { Test, Local, Production }
