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

pub mod caller_identity;
pub mod auth_session_identity;
pub mod oauth_session_engine;
pub mod pending_oauth_login;

/// Contract tests every strict `AuthSessionIdentityPort` bridge must pass
/// (V1 API auth plugin chain).
///
/// Drivers pass a fresh persistence repo, the matching identity repo, and
/// the factory wiring them into the port under test (e.g. bootstrap's
/// `RepoAuthSessionIdentityPort`); the harness owns the error-injection
/// decorators and the classification asserts.
pub use auth_session_identity::auth_session_identity_port_contract_tests;

/// Contract tests the strict `OAuthSessionEngine` must pass over any
/// `AuthSessionIdentityPort` (V1 API auth plugin chain, Task 8): the
/// install → same-`now` refresh → by-sid revoke lifecycle from plan §8.5.
///
/// Drivers pass the HMAC secret, the port under test (in-crate test double
/// now; Task 13's real repository bridge later), the injected environment,
/// and the idle window; the harness owns the sequence and the asserts.
pub use oauth_session_engine::oauth_session_engine_contract_tests;

/// Contract tests every `PendingOAuthLoginStore` must pass (V1 API auth
/// plugin chain, Task 9): browser-bound one-time OAuth state from plan
/// §8.4 — wrong-browser consume never burns the pending record, any
/// single consume invalidates the whole batch, TTL/expiry fail closed,
/// store failures yield no login URLs, and one barrier-released
/// concurrent consume has exactly ONE winner.
///
/// Drivers pass the store under test (the memory store now; Task 13's
/// shared persistence store later); the harness owns the sequence and
/// the asserts. `pending_o_auth_login_store_contract_tests` is the
/// R25-gate-canonical name; the human-spelled alias stays exported for
/// existing importers.
pub use pending_oauth_login::{
    pending_oauth_login_contract_tests, pending_o_auth_login_store_contract_tests,
};
