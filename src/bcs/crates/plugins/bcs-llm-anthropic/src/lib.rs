use std::time::Instant;

use async_trait::async_trait;
use bcs_config_api::{LlmConfig, StructuredOutputMode};
use bcs_llm_api::{
    LlmChatCompletionPort, LlmChatCompletionRequest, LlmChatCompletionResponse, LlmError,
};
use reqwest::header::{CONTENT_TYPE, HeaderMap, HeaderName, HeaderValue};
use secrecy::ExposeSecret;
use serde::Deserialize;
use serde_json::{Value, json};
use tracing::warn;

const ANTHROPIC_VERSION: &str = "2023-06-01";
const X_API_KEY: HeaderName = HeaderName::from_static("x-api-key");
const ANTHROPIC_VERSION_HEADER: HeaderName = HeaderName::from_static("anthropic-version");

#[derive(Clone)]
pub struct AnthropicLlmClient {
    config: LlmConfig,
    client: reqwest::Client,
}

impl AnthropicLlmClient {
    pub fn new(config: LlmConfig) -> Result<Self, LlmError> {
        if config.base_url.trim().is_empty() {
            return Err(LlmError::Config(
                "anthropic base_url must not be empty".to_string(),
            ));
        }
        let has_api_key = config
            .api_key
            .as_ref()
            .is_some_and(|api_key| !api_key.expose_secret().trim().is_empty());
        if !has_api_key {
            return Err(LlmError::Config(
                "anthropic api_key is required".to_string(),
            ));
        }
        if config.structured_output == StructuredOutputMode::JsonObject {
            return Err(LlmError::Config(
                "anthropic does not support structured_output = \"json_object\"; use \"json_schema\" or \"tool_call\""
                    .to_string(),
            ));
        }
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_millis(config.timeout_ms))
            .build()
            .map_err(|error| LlmError::Config(error.to_string()))?;
        Ok(Self { config, client })
    }

    pub fn build_http_request(
        &self,
        request: LlmChatCompletionRequest,
    ) -> Result<reqwest::Request, LlmError> {
        let LlmChatCompletionRequest {
            model,
            messages,
            response_format,
            stream,
        } = request;
        if stream {
            return Err(LlmError::Config(
                "anthropic streaming is not supported by this completion port".to_string(),
            ));
        }

        let url = anthropic_messages_url(&self.config.base_url);
        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(
            ANTHROPIC_VERSION_HEADER,
            HeaderValue::from_static(ANTHROPIC_VERSION),
        );
        let api_key = self
            .config
            .api_key
            .as_ref()
            .ok_or_else(|| LlmError::Config("anthropic api_key is required".to_string()))?;
        let api_key_header = HeaderValue::from_str(api_key.expose_secret())
            .map_err(|error| LlmError::Config(format!("invalid anthropic api_key header: {error}")))?;
        headers.insert(X_API_KEY, api_key_header);

        let (system, messages) = translate_messages(messages)?;
        let mut body = json!({
            "model": model,
            "messages": messages,
            "stream": false,
            "max_tokens": self.config.max_tokens,
        });
        if let Some(system) = system {
            body["system"] = Value::String(system);
        }
        apply_structured_output(&mut body, response_format, self.config.structured_output)?;

        self.client
            .post(url)
            .headers(headers)
            .json(&body)
            .build()
            .map_err(|error| LlmError::Request(error.to_string()))
    }
}

#[async_trait]
impl LlmChatCompletionPort for AnthropicLlmClient {
    async fn complete(
        &self,
        request: LlmChatCompletionRequest,
    ) -> Result<LlmChatCompletionResponse, LlmError> {
        let model = request.model.clone();
        let structured_output_requested = request.response_format.is_some();
        let structured_output = structured_output_mode_name(self.config.structured_output);
        let http_request = self.build_http_request(request)?;
        let url = http_request.url().to_string();
        let started_at = Instant::now();
        let response = self.client.execute(http_request).await.map_err(|error| {
            let elapsed_ms = elapsed_ms(started_at);
            warn!(
                provider = "anthropic",
                model = %model,
                url = %url,
                elapsed_ms,
                timeout = error.is_timeout(),
                connect = error.is_connect(),
                error = %error,
                structured_output,
                "anthropic: message request failed"
            );
            LlmError::Request(format!(
                "anthropic request to {url} failed after {elapsed_ms}ms: {error}; timeout={}; connect={}; structured_output={structured_output}",
                error.is_timeout(),
                error.is_connect(),
            ))
        })?;
        let status = response.status();
        let request_id = response
            .headers()
            .get("request-id")
            .and_then(|value| value.to_str().ok())
            .unwrap_or("none")
            .to_string();
        let body = response.text().await.map_err(|error| {
            let elapsed_ms = elapsed_ms(started_at);
            warn!(
                provider = "anthropic",
                model = %model,
                url = %url,
                status = %status,
                request_id = %request_id,
                elapsed_ms,
                error = %error,
                "anthropic: failed to read message response body"
            );
            LlmError::Response(format!(
                "anthropic failed to read response body from {url} with status {status} after {elapsed_ms}ms: {error}; request_id={request_id}"
            ))
        })?;
        let elapsed_ms = elapsed_ms(started_at);
        let body_len = body.len();
        if !status.is_success() {
            warn!(
                provider = "anthropic",
                model = %model,
                url = %url,
                status = %status,
                request_id = %request_id,
                elapsed_ms,
                response_body_len = body_len,
                "anthropic: message request returned non-success status"
            );
            return Err(LlmError::Request(format!(
                "anthropic returned {status} from {url} after {elapsed_ms}ms; body_len={body_len}; request_id={request_id}"
            )));
        }

        let raw: Value = serde_json::from_str(&body).map_err(|error| {
            warn!(
                provider = "anthropic",
                model = %model,
                url = %url,
                status = %status,
                request_id = %request_id,
                elapsed_ms,
                response_body_len = body_len,
                error = %error,
                "anthropic: message response is not valid JSON"
            );
            LlmError::Response(format!(
                "anthropic response from {url} with status {status} is not valid JSON after {elapsed_ms}ms: {error}; body_len={body_len}; request_id={request_id}"
            ))
        })?;
        let parsed: AnthropicMessageResponse =
            serde_json::from_value(raw.clone()).map_err(|error| {
                warn!(
                    provider = "anthropic",
                    model = %model,
                    url = %url,
                    status = %status,
                    request_id = %request_id,
                    elapsed_ms,
                    response_body_len = body_len,
                    error = %error,
                    "anthropic: message response schema mismatch"
                );
                LlmError::Response(format!(
                    "anthropic response schema mismatch from {url} with status {status} after {elapsed_ms}ms: {error}; body_len={body_len}; request_id={request_id}"
                ))
            })?;

        reject_incomplete_response(&parsed, &url, &request_id)?;
        let content = match (structured_output_requested, self.config.structured_output) {
            (false, _) | (true, StructuredOutputMode::JsonSchema) => {
                text_content(parsed.content)
            }
            (true, StructuredOutputMode::ToolCall) => tool_use_content(parsed.content),
            (true, StructuredOutputMode::JsonObject) => {
                unreachable!("rejected during client construction")
            }
        }
        .ok_or_else(|| {
            LlmError::Response(format!(
                "anthropic response from {url} is missing the expected {structured_output} content; request_id={request_id}"
            ))
        })?;

        Ok(LlmChatCompletionResponse { content, raw })
    }
}

fn anthropic_messages_url(base_url: &str) -> String {
    let base_url = base_url.trim_end_matches('/');
    if base_url.ends_with("/v1") {
        format!("{base_url}/messages")
    } else {
        format!("{base_url}/v1/messages")
    }
}

fn translate_messages(
    messages: Vec<bcs_llm_api::LlmChatMessage>,
) -> Result<(Option<String>, Vec<Value>), LlmError> {
    let mut system_parts = Vec::new();
    let mut translated = Vec::new();
    for message in messages {
        match message.role.as_str() {
            "system" => system_parts.push(text_from_message_content(&message.content)?),
            "user" | "assistant" => translated.push(json!({
                "role": message.role,
                "content": message.content,
            })),
            other => {
                return Err(LlmError::Config(format!(
                    "anthropic does not support LLM message role '{other}'"
                )));
            }
        }
    }
    if translated.is_empty() {
        return Err(LlmError::Config(
            "anthropic requires at least one user or assistant message".to_string(),
        ));
    }
    let system = if system_parts.is_empty() {
        None
    } else {
        Some(system_parts.join("\n\n"))
    };
    Ok((system, translated))
}

fn text_from_message_content(content: &Value) -> Result<String, LlmError> {
    if let Some(text) = content.as_str() {
        return Ok(text.to_string());
    }
    let Some(blocks) = content.as_array() else {
        return Err(LlmError::Config(
            "anthropic system message content must be a string or text-block array".to_string(),
        ));
    };
    let mut texts = Vec::new();
    for block in blocks {
        let is_text = block
            .get("type")
            .and_then(Value::as_str)
            .is_some_and(|value| value == "text");
        let Some(text) = block.get("text").and_then(Value::as_str).filter(|_| is_text) else {
            return Err(LlmError::Config(
                "anthropic system message arrays may contain only text blocks".to_string(),
            ));
        };
        texts.push(text);
    }
    Ok(texts.join("\n"))
}

fn apply_structured_output(
    body: &mut Value,
    response_format: Option<Value>,
    mode: StructuredOutputMode,
) -> Result<(), LlmError> {
    let Some(response_format) = response_format else {
        return Ok(());
    };
    let schema = parse_json_schema_envelope(response_format)?;
    match mode {
        StructuredOutputMode::JsonSchema => {
            body["output_config"] = json!({
                "format": {
                    "type": "json_schema",
                    "schema": schema.schema,
                }
            });
        }
        StructuredOutputMode::ToolCall => {
            body["tools"] = json!([
                {
                    "name": schema.name,
                    "description": "Return the structured judge response.",
                    "input_schema": schema.schema,
                    "strict": schema.strict,
                }
            ]);
            body["tool_choice"] = json!({
                "type": "tool",
                "name": schema.name,
            });
        }
        StructuredOutputMode::JsonObject => {
            return Err(LlmError::Config(
                "anthropic does not support structured_output = \"json_object\"".to_string(),
            ));
        }
    }
    Ok(())
}

struct JsonSchemaEnvelope {
    name: String,
    strict: bool,
    schema: Value,
}

fn parse_json_schema_envelope(response_format: Value) -> Result<JsonSchemaEnvelope, LlmError> {
    if response_format.get("type").and_then(Value::as_str) != Some("json_schema") {
        return Err(LlmError::Config(
            "anthropic requires response_format.type = \"json_schema\"".to_string(),
        ));
    }
    let json_schema = response_format
        .get("json_schema")
        .ok_or_else(|| LlmError::Config("json_schema response_format is missing json_schema".to_string()))?;
    let name = json_schema
        .get("name")
        .and_then(Value::as_str)
        .filter(|name| !name.trim().is_empty())
        .ok_or_else(|| LlmError::Config("json_schema response_format requires a non-empty name".to_string()))?
        .to_string();
    let schema = json_schema
        .get("schema")
        .cloned()
        .ok_or_else(|| LlmError::Config("json_schema response_format is missing schema".to_string()))?;
    let strict = json_schema
        .get("strict")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    Ok(JsonSchemaEnvelope {
        name,
        strict,
        schema,
    })
}

fn reject_incomplete_response(
    response: &AnthropicMessageResponse,
    url: &str,
    request_id: &str,
) -> Result<(), LlmError> {
    match response.stop_reason.as_deref() {
        Some("refusal") => Err(LlmError::Response(format!(
            "anthropic refused the request to {url}; request_id={request_id}"
        ))),
        Some("max_tokens") => Err(LlmError::Response(format!(
            "anthropic response from {url} reached max_tokens; request_id={request_id}"
        ))),
        Some("model_context_window_exceeded") => Err(LlmError::Response(format!(
            "anthropic response from {url} exceeded the model context window; request_id={request_id}"
        ))),
        Some("pause_turn") => Err(LlmError::Response(format!(
            "anthropic response from {url} paused before completing; request_id={request_id}"
        ))),
        None => Err(LlmError::Response(format!(
            "anthropic response from {url} is missing stop_reason; request_id={request_id}"
        ))),
        Some(_) => Ok(()),
    }
}

fn text_content(content: Vec<AnthropicContentBlock>) -> Option<String> {
    let text = content
        .into_iter()
        .filter_map(|block| match block {
            AnthropicContentBlock::Text { text } => Some(text),
            _ => None,
        })
        .collect::<String>();
    (!text.trim().is_empty()).then_some(text)
}

fn tool_use_content(content: Vec<AnthropicContentBlock>) -> Option<String> {
    content.into_iter().find_map(|block| match block {
        AnthropicContentBlock::ToolUse { input, .. } => serde_json::to_string(&input).ok(),
        _ => None,
    })
}

fn structured_output_mode_name(mode: StructuredOutputMode) -> &'static str {
    match mode {
        StructuredOutputMode::JsonSchema => "json_schema",
        StructuredOutputMode::JsonObject => "json_object",
        StructuredOutputMode::ToolCall => "tool_call",
    }
}

fn elapsed_ms(started_at: Instant) -> u64 {
    started_at
        .elapsed()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[derive(Debug, Deserialize)]
struct AnthropicMessageResponse {
    content: Vec<AnthropicContentBlock>,
    stop_reason: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type")]
enum AnthropicContentBlock {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "tool_use")]
    ToolUse {
        #[allow(dead_code)]
        name: String,
        input: Value,
    },
    #[serde(other)]
    Other,
}
