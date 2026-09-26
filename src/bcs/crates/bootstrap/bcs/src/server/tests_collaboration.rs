//! Tests mod split out from server.rs: judge_provider_tests.

    use super::*;

use super::*;
    use crate::plugins::LlmProviderFactory;
    use bcs_llm_api::{LlmChatCompletionRequest, LlmChatCompletionResponse, LlmError};
    use bcs_service_api::{JudgeArtifact, JudgeRequest};
    use serde_json::json;

    struct RecordingLlm {
        requests: Mutex<Vec<LlmChatCompletionRequest>>,
    }

    #[async_trait::async_trait]
    impl LlmChatCompletionPort for RecordingLlm {
        async fn complete(
            &self,
            request: LlmChatCompletionRequest,
        ) -> std::result::Result<LlmChatCompletionResponse, LlmError> {
            self.requests.lock().await.push(request);
            Ok(LlmChatCompletionResponse {
                content: json!({
                    "outcome": "approved",
                    "reason": "ok",
                    "confidence": 0.9,
                    "checked_criteria": [],
                    "retry_instruction": "",
                })
                .to_string(),
                raw: json!({}),
            })
        }
    }

    fn test_llm_factory(_config: BcsConfig) -> crate::Result<Arc<dyn LlmChatCompletionPort>> {
        Ok(Arc::new(RecordingLlm {
            requests: Mutex::new(Vec::new()),
        }))
    }

    inventory::submit! {
        LlmProviderFactory {
            name: "test-internal-llm",
            build: test_llm_factory,
        }
    }

    fn judge_request() -> JudgeRequest {
        JudgeRequest {
            run_id: "run-1".to_string(),
            node_id: "judge".to_string(),
            attempt: 1,
            judge_type: "llm".to_string(),
            criteria: vec!["must pass".to_string()],
            allowed_outcomes: vec!["approved".to_string(), "rejected".to_string()],
            input: json!({"question": "ready?"}),
            upstream_outputs: vec![JudgeArtifact {
                node_id: "work".to_string(),
                text: "candidate output".to_string(),
            }],
            artifact_text: "candidate output".to_string(),
        }
    }

    #[test]
    fn judge_llm_provider_selection_uses_public_provider_types() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::OpenAiCompatible;

        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::OpenAiCompatible
        );

        config.llm.provider_type = LlmProviderType::Anthropic;
        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::Anthropic
        );
    }

    #[test]
    fn anthropic_llm_provider_requires_api_key() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Anthropic;
        config.llm.base_url = "https://api.anthropic.com/v1".to_string();
        config.llm.api_key_env = None;
        config.llm.api_key = None;

        let error = match create_judge_evaluator(&config, &BcsServerExtensions::default()) {
            Ok(_) => panic!("anthropic provider without an API key should fail"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("anthropic api_key is required"));
    }

    #[test]
    fn anthropic_llm_provider_builds_judge_evaluator() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Anthropic;
        config.llm.base_url = "https://api.anthropic.com/v1".to_string();
        config.llm.api_key_env = None;
        config.llm.api_key = Some(Secret::new("anthropic-key".to_string()));

        create_judge_evaluator(&config, &BcsServerExtensions::default())
            .expect("valid anthropic provider should build a judge evaluator");
    }

    #[tokio::test]
    async fn none_llm_without_injection_uses_noop_judge() {
        let config = BcsConfig::default();
        let evaluator =
            create_judge_evaluator(&config, &BcsServerExtensions::default()).expect("evaluator");

        let error = match evaluator.judge(judge_request()).await {
            Ok(_) => panic!("noop judge should reject LLM judge requests"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("requires an enabled LLM"));
    }

    #[tokio::test]
    async fn injected_llm_provider_is_used_when_present() {
        let llm = Arc::new(RecordingLlm {
            requests: Mutex::new(Vec::new()),
        });
        let llm_provider: Arc<dyn LlmChatCompletionPort> = llm.clone();
        let mut config = BcsConfig::default();
        config.llm.model = "custom-judge-model".to_string();
        let extensions = BcsServerExtensions {
            llm_provider: Some(llm_provider),
            ..BcsServerExtensions::default()
        };

        let evaluator = create_judge_evaluator(&config, &extensions).expect("evaluator");
        let decision = evaluator
            .judge(judge_request())
            .await
            .expect("judge decision");

        assert_eq!(decision.outcome, "approved");
        let requests = llm.requests.lock().await;
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].model, "custom-judge-model");
    }

    #[tokio::test]
    async fn registered_llm_provider_is_selected_by_type() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Other("test-internal-llm".to_string());
        config.llm.model = "registered-model".to_string();

        let evaluator =
            create_judge_evaluator(&config, &BcsServerExtensions::default()).expect("evaluator");
        let decision = evaluator
            .judge(judge_request())
            .await
            .expect("judge decision");

        assert_eq!(decision.outcome, "approved");
    }
