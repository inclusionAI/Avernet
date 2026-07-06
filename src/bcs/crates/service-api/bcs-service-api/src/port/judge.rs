use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::ServiceError;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct JudgeArtifact {
    pub node_id: String,
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JudgeRequest {
    pub run_id: String,
    pub node_id: String,
    pub attempt: i32,
    pub judge_type: String,
    pub criteria: Vec<String>,
    pub allowed_outcomes: Vec<String>,
    pub input: Value,
    pub upstream_outputs: Vec<JudgeArtifact>,
    pub artifact_text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct JudgeCheckedCriterion {
    pub criterion: String,
    pub satisfied: bool,
    pub evidence: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct JudgeDecision {
    pub outcome: String,
    pub reason: String,
    pub confidence: f64,
    pub checked_criteria: Vec<JudgeCheckedCriterion>,
    pub retry_instruction: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub raw_response: Option<Value>,
}

#[async_trait]
pub trait JudgeEvaluatorPort: Send + Sync {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError>;
}
