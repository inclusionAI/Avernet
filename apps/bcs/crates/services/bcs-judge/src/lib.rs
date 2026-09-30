use std::sync::Arc;

use async_trait::async_trait;
use bcs_llm_api::{LlmChatCompletionPort, LlmChatCompletionRequest, LlmChatMessage};
use bcs_service_api::{
    JudgeDecision, JudgeEvaluatorPort, JudgeRequest, ServiceError,
};
use serde::Deserialize;
use serde_json::{Value, json};

pub struct LlmJudgeService {
    llm: Arc<dyn LlmChatCompletionPort>,
    model: String,
}

#[derive(Debug, Default)]
pub struct NoopJudgeEvaluator;

impl LlmJudgeService {
    pub fn new(llm: Arc<dyn LlmChatCompletionPort>, model: String) -> Self {
        Self { llm, model }
    }
}

#[async_trait]
impl JudgeEvaluatorPort for NoopJudgeEvaluator {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        Err(ServiceError::InvalidOperation {
            message: "state-machine judge requires an enabled LLM judge provider".to_string(),
            request_id: Some(request.run_id),
        })
    }
}

#[async_trait]
impl JudgeEvaluatorPort for LlmJudgeService {
    async fn judge(&self, request: JudgeRequest) -> Result<JudgeDecision, ServiceError> {
        if request.allowed_outcomes.is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: "judge allowed_outcomes must not be empty".to_string(),
                request_id: Some(request.run_id),
            });
        }
        let response_format = judge_response_format(&request.allowed_outcomes);
        let llm_response = self
            .llm
            .complete(LlmChatCompletionRequest {
                model: self.model.clone(),
                messages: build_messages(&request),
                response_format: Some(response_format),
                stream: false,
            })
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;

        let raw_value: Value = serde_json::from_str(&llm_response.content).map_err(|error| {
            ServiceError::InternalError(format!("judge response is not valid JSON: {error}"))
        })?;
        let wire: JudgeDecisionWire = serde_json::from_value(raw_value.clone()).map_err(|error| {
            ServiceError::InternalError(format!("judge response schema mismatch: {error}"))
        })?;
        if !request
            .allowed_outcomes
            .iter()
            .any(|outcome| outcome == &wire.outcome)
        {
            return Err(ServiceError::InvalidOperation {
                message: format!("judge outcome '{}' is not allowed", wire.outcome),
                request_id: Some(request.run_id),
            });
        }
        Ok(JudgeDecision {
            outcome: wire.outcome,
            reason: wire.reason,
            confidence: wire.confidence,
            checked_criteria: wire.checked_criteria,
            retry_instruction: wire.retry_instruction,
            raw_response: Some(raw_value),
        })
    }
}

fn build_messages(request: &JudgeRequest) -> Vec<LlmChatMessage> {
    vec![
        LlmChatMessage {
            role: "system".to_string(),
            content: json!("You are a strict BCS state-machine judge. Return only JSON that matches the provided schema. Choose exactly one allowed outcome. Do not invent outcomes or workflow transitions."),
        },
        LlmChatMessage {
            role: "user".to_string(),
            content: json!(format!(
                "[Run]\nrun_id: {}\nnode_id: {}\nattempt: {}\njudge_type: {}\n\n[Allowed Outcomes]\n{}\n\n[Criteria]\n{}\n\n[Input]\n{}\n\n[Upstream Outputs]\n{}\n\n[Candidate Artifact]\n{}",
                request.run_id,
                request.node_id,
                request.attempt,
                request.judge_type,
                serde_json::to_string_pretty(&request.allowed_outcomes).unwrap_or_else(|_| "[]".to_string()),
                serde_json::to_string_pretty(&request.criteria).unwrap_or_else(|_| "[]".to_string()),
                serde_json::to_string_pretty(&request.input).unwrap_or_else(|_| request.input.to_string()),
                serde_json::to_string_pretty(&request.upstream_outputs).unwrap_or_else(|_| "[]".to_string()),
                request.artifact_text
            )),
        },
    ]
}

fn judge_response_format(allowed_outcomes: &[String]) -> Value {
    json!({
        "type": "json_schema",
        "json_schema": {
            "name": "bcs_state_machine_judge_response",
            "strict": true,
            "schema": {
                "type": "object",
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": allowed_outcomes
                    },
                    "reason": {
                        "type": "string"
                    },
                    "confidence": {
                        "type": "number"
                    },
                    "checked_criteria": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "criterion": {"type": "string"},
                                "satisfied": {"type": "boolean"},
                                "evidence": {"type": "string"}
                            },
                            "required": ["criterion", "satisfied", "evidence"],
                            "additionalProperties": false
                        }
                    },
                    "retry_instruction": {
                        "type": "string"
                    }
                },
                "required": [
                    "outcome",
                    "reason",
                    "confidence",
                    "checked_criteria",
                    "retry_instruction"
                ],
                "additionalProperties": false
            }
        }
    })
}

#[derive(Debug, Deserialize)]
struct JudgeDecisionWire {
    outcome: String,
    reason: String,
    confidence: f64,
    checked_criteria: Vec<bcs_service_api::JudgeCheckedCriterion>,
    retry_instruction: String,
}
