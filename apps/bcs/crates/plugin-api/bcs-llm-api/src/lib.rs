//! Provider-neutral LLM completion contract.
//!
//! Every implementation must run
//! `bcs_test_support::contract::plugin::llm_chat_completion_contract_tests`.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LlmChatMessage {
    /// Canonical role. Providers must support `system`, `user`, and `assistant`.
    pub role: String,
    /// Provider-neutral message content. Judge requests currently use JSON strings.
    pub content: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LlmChatCompletionRequest {
    pub model: String,
    pub messages: Vec<LlmChatMessage>,
    /// Canonical BCS structured-output envelope.
    ///
    /// When present, the value uses
    /// `{ "type": "json_schema", "json_schema": { "name", "strict", "schema" } }`.
    /// Provider implementations translate this envelope to their native wire format.
    #[serde(default)]
    pub response_format: Option<Value>,
    #[serde(default)]
    pub stream: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LlmChatCompletionResponse {
    pub content: String,
    #[serde(default)]
    pub raw: Value,
}

#[derive(Debug, thiserror::Error)]
pub enum LlmError {
    #[error("LLM configuration error: {0}")]
    Config(String),
    #[error("LLM request failed: {0}")]
    Request(String),
    #[error("LLM response parse failed: {0}")]
    Response(String),
}

#[async_trait]
pub trait LlmChatCompletionPort: Send + Sync {
    async fn complete(
        &self,
        request: LlmChatCompletionRequest,
    ) -> Result<LlmChatCompletionResponse, LlmError>;
}
