//! Optional external coordination payload resolution. Identity is supplied by
//! the authenticated run, never by the tool's arguments.
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use crate::ServiceResult;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CoordinationContext {
    pub bot_id: String,
    pub group_id: String,
    pub session_id: Option<String>,
    pub run_id: String,
    pub tool_call_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CoordinationStatus { Applied, Failed, Unknown }

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CoordinationResult {
    pub status: CoordinationStatus,
    pub task_id: Option<String>,
    pub error_code: Option<String>,
}

pub struct CoordinationLease {
    pub arguments: Map<String, Value>,
    pub claim_token: String,
}

pub enum CoordinationClaim {
    Acquired(CoordinationLease),
    /// None means an earlier claimant may have executed, without a receipt.
    Duplicate(Option<CoordinationResult>),
}

#[async_trait]
pub trait CoordinationIntentPort: Send + Sync {
    /// Only an explicitly acknowledged claim permits execution. A timeout must
    /// not be retried as an execution grant. Reads are bounded by run deadline.
    async fn resolve_and_claim(
        &self, intent_id: &str, tool: &str, context: &CoordinationContext,
        deadline_ms: u64,
    ) -> ServiceResult<CoordinationClaim>;

    /// Append an immutable receipt. Retries must only repeat this operation.
    async fn finish(
        &self, intent_id: &str, context: &CoordinationContext,
        claim_token: &str, result: &CoordinationResult,
    ) -> ServiceResult<()>;
}
