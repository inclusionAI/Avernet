//! Plugin contract harnesses.

use bcs_cache_api::CachePlugin;
use bcs_db_api::DbPlugin;
use bcs_llm_api::{LlmChatCompletionPort, LlmChatCompletionRequest, LlmChatMessage};
use bcs_service_api::port::secret::{SecretAccessPort, SecretRecord};
use serde_json::json;

pub async fn cache_plugin_contract_tests<P: CachePlugin>(plugin: &P) {
    crate::cache_plugin_contract_tests(plugin).await;
}

pub async fn db_plugin_contract_tests<P: DbPlugin>(plugin: &P) {
    crate::db_plugin_contract_tests(plugin).await;
}

pub async fn secret_access_contract_tests<P, F>(plugin: &P, seed: F)
where
    P: SecretAccessPort,
    F: FnOnce() -> SecretRecord,
{
    crate::secret_access_contract_tests(plugin, seed).await;
}

/// Contract tests every LLM chat completion plugin must pass.
///
/// The provider fixture must return `{"outcome":"complete"}` for the canonical
/// request constructed by this harness.
#[allow(
    clippy::expect_used,
    reason = "test harness — panic on failure is the contract"
)]
pub async fn llm_chat_completion_contract_tests<P>(plugin: &P, model: &str)
where
    P: LlmChatCompletionPort,
{
    let response = plugin
        .complete(LlmChatCompletionRequest {
            model: model.to_string(),
            messages: vec![
                LlmChatMessage {
                    role: "system".to_string(),
                    content: json!("Return JSON only."),
                },
                LlmChatMessage {
                    role: "user".to_string(),
                    content: json!("Judge the candidate."),
                },
                LlmChatMessage {
                    role: "assistant".to_string(),
                    content: json!("I will evaluate the candidate against the criteria."),
                },
                LlmChatMessage {
                    role: "user".to_string(),
                    content: json!("Return the final outcome now."),
                },
            ],
            response_format: Some(json!({
                "type": "json_schema",
                "json_schema": {
                    "name": "judge_response",
                    "strict": true,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "outcome": {
                                "type": "string",
                                "enum": ["complete", "retry"]
                            }
                        },
                        "required": ["outcome"],
                        "additionalProperties": false
                    }
                }
            })),
            stream: false,
        })
        .await
        .expect("LLM provider must complete the canonical request");

    assert_eq!(response.content, r#"{"outcome":"complete"}"#);
    assert!(
        response.raw.is_object(),
        "LLM provider must preserve the raw provider response"
    );
}
