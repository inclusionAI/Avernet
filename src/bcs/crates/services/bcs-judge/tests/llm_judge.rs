use std::sync::Arc;

use async_trait::async_trait;
use bcs_judge::LlmJudgeService;
use bcs_llm_api::{
    LlmChatCompletionPort, LlmChatCompletionRequest, LlmChatCompletionResponse, LlmError,
};
use bcs_service_api::{JudgeArtifact, JudgeEvaluatorPort, JudgeRequest};
use serde_json::json;
use tokio::sync::Mutex;

#[tokio::test]
async fn llm_judge_uses_json_schema_and_validates_allowed_outcome() {
    let llm = Arc::new(RecordingLlm {
        response: r#"{"outcome":"approved","reason":"covers criteria","confidence":0.91,"checked_criteria":[{"criterion":"has final answer","satisfied":true,"evidence":"final answer present"}],"retry_instruction":""}"#.to_string(),
        requests: Mutex::new(Vec::new()),
    });
    let judge = LlmJudgeService::new(llm.clone(), "DeepSeek-V3".to_string());

    let decision = judge
        .judge(JudgeRequest {
            run_id: "sm-run-1".to_string(),
            node_id: "synthesize".to_string(),
            attempt: 1,
            judge_type: "llm".to_string(),
            criteria: vec!["has final answer".to_string()],
            allowed_outcomes: vec!["approved".to_string(), "rejected".to_string()],
            input: json!({"question": "risk review"}),
            upstream_outputs: vec![JudgeArtifact {
                node_id: "review".to_string(),
                text: "expert review".to_string(),
            }],
            artifact_text: "candidate final answer".to_string(),
        })
        .await
        .expect("judge decision");

    assert_eq!(decision.outcome, "approved");
    assert_eq!(decision.reason, "covers criteria");
    assert_eq!(decision.checked_criteria.len(), 1);

    let request = llm.requests.lock().await[0].clone();
    assert_eq!(request.model, "DeepSeek-V3");
    let response_format = request.response_format.expect("response format");
    assert_eq!(response_format["type"], "json_schema");
    assert_eq!(
        response_format["json_schema"]["schema"]["properties"]["outcome"]["enum"],
        json!(["approved", "rejected"])
    );
    assert!(request.messages.iter().any(|message| {
        message.role == "user" && message.content.to_string().contains("candidate final answer")
    }));
}

#[tokio::test]
async fn llm_judge_rejects_outcome_outside_allowed_list() {
    let llm = Arc::new(RecordingLlm {
        response: r#"{"outcome":"invented","reason":"bad","confidence":0.4,"checked_criteria":[],"retry_instruction":""}"#.to_string(),
        requests: Mutex::new(Vec::new()),
    });
    let judge = LlmJudgeService::new(llm, "DeepSeek-V3".to_string());

    let error = judge
        .judge(JudgeRequest {
            run_id: "sm-run-1".to_string(),
            node_id: "synthesize".to_string(),
            attempt: 1,
            judge_type: "llm".to_string(),
            criteria: vec!["has final answer".to_string()],
            allowed_outcomes: vec!["approved".to_string(), "rejected".to_string()],
            input: json!({}),
            upstream_outputs: Vec::new(),
            artifact_text: "candidate".to_string(),
        })
        .await
        .expect_err("invalid outcome should be rejected");

    assert!(error.to_string().contains("not allowed"));
}

struct RecordingLlm {
    response: String,
    requests: Mutex<Vec<LlmChatCompletionRequest>>,
}

#[async_trait]
impl LlmChatCompletionPort for RecordingLlm {
    async fn complete(
        &self,
        request: LlmChatCompletionRequest,
    ) -> Result<LlmChatCompletionResponse, LlmError> {
        self.requests.lock().await.push(request);
        Ok(LlmChatCompletionResponse {
            content: self.response.clone(),
            raw: json!({}),
        })
    }
}
