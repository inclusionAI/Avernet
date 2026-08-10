use std::{env, error::Error, time::Instant};

use async_trait::async_trait;
use bcs_config_api::{LlmConfig, StructuredOutputMode};
use bcs_llm_api::{
    LlmChatCompletionPort, LlmChatCompletionRequest, LlmChatCompletionResponse, LlmError,
};
use reqwest::header::{AUTHORIZATION, CONTENT_TYPE, HeaderMap, HeaderValue};
use secrecy::ExposeSecret;
use serde::Deserialize;
use serde_json::{Value, json};
use tracing::warn;

#[derive(Clone)]
pub struct OpenAiCompatibleLlmClient {
    config: LlmConfig,
    client: reqwest::Client,
}

impl OpenAiCompatibleLlmClient {
    pub fn new(config: LlmConfig) -> Result<Self, LlmError> {
        if config.base_url.trim().is_empty() {
            return Err(LlmError::Config(
                "openai-compatible base_url must not be empty".to_string(),
            ));
        }
        let has_api_key = config
            .api_key
            .as_ref()
            .is_some_and(|api_key| !api_key.expose_secret().trim().is_empty());
        if !has_api_key {
            return Err(LlmError::Config(
                "openai-compatible api_key is required".to_string(),
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
        let url = format!(
            "{}/chat/completions",
            self.config.base_url.trim_end_matches('/')
        );
        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        let api_key = self
            .config
            .api_key
            .as_ref()
            .ok_or_else(|| LlmError::Config("openai-compatible api_key is required".to_string()))?;
        let auth = HeaderValue::from_str(&format!("Bearer {}", api_key.expose_secret()))
            .map_err(|error| LlmError::Config(format!("invalid openai-compatible api_key header: {error}")))?;
        headers.insert(AUTHORIZATION, auth);

        let mut body = json!({
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        });
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
impl LlmChatCompletionPort for OpenAiCompatibleLlmClient {
    async fn complete(
        &self,
        request: LlmChatCompletionRequest,
    ) -> Result<LlmChatCompletionResponse, LlmError> {
        let model = request.model.clone();
        let requested_response_format_type = response_format_type(request.response_format.as_ref());
        let structured_output = structured_output_mode_name(self.config.structured_output);
        let http_request = self.build_http_request(request)?;
        let url = http_request.url().to_string();
        let started_at = Instant::now();
        let response = self
            .client
            .execute(http_request)
            .await
            .map_err(|error| {
                let elapsed_ms = elapsed_ms(started_at);
                let diagnostics = reqwest_error_diagnostics(&error);
                warn!(
                    provider = "openai_compatible",
                    model = %model,
                    url = %url,
                    elapsed_ms = elapsed_ms,
                    error = %error,
                    timeout = diagnostics.timeout,
                    connect = diagnostics.connect,
                    request = diagnostics.request,
                    body = diagnostics.body,
                    decode = diagnostics.decode,
                    status = %diagnostics.status,
                    source_chain = %diagnostics.source_chain,
                    error_debug = %diagnostics.debug,
                    proxy_env = %diagnostics.proxy_env,
                    requested_response_format_type = %requested_response_format_type,
                    structured_output = %structured_output,
                    "openai-compatible: chat completion request failed"
                );
                LlmError::Request(format!(
                    "openai-compatible request to {url} failed after {elapsed_ms}ms: {error}; \
                     timeout={}; connect={}; request={}; body={}; decode={}; status={}; \
                     source_chain={}; debug={}; proxy_env={}; \
                     requested_response_format_type={}; structured_output={}",
                    diagnostics.timeout,
                    diagnostics.connect,
                    diagnostics.request,
                    diagnostics.body,
                    diagnostics.decode,
                    diagnostics.status,
                    diagnostics.source_chain,
                    diagnostics.debug,
                    diagnostics.proxy_env,
                    requested_response_format_type,
                    structured_output
                ))
            })?;
        let status = response.status();
        let body = response
            .text()
            .await
            .map_err(|error| {
                let elapsed_ms = elapsed_ms(started_at);
                let diagnostics = reqwest_error_diagnostics(&error);
                warn!(
                    provider = "openai_compatible",
                    model = %model,
                    url = %url,
                    status = %status,
                    elapsed_ms = elapsed_ms,
                    error = %error,
                    timeout = diagnostics.timeout,
                    connect = diagnostics.connect,
                    request = diagnostics.request,
                    body = diagnostics.body,
                    decode = diagnostics.decode,
                    error_status = %diagnostics.status,
                    source_chain = %diagnostics.source_chain,
                    error_debug = %diagnostics.debug,
                    proxy_env = %diagnostics.proxy_env,
                    "openai-compatible: failed to read chat completion response body"
                );
                LlmError::Response(format!(
                    "openai-compatible failed to read response body from {url} with status {status} after {elapsed_ms}ms: {error}; \
                     timeout={}; connect={}; request={}; body={}; decode={}; status={}; \
                     source_chain={}; debug={}; proxy_env={}",
                    diagnostics.timeout,
                    diagnostics.connect,
                    diagnostics.request,
                    diagnostics.body,
                    diagnostics.decode,
                    diagnostics.status,
                    diagnostics.source_chain,
                    diagnostics.debug,
                    diagnostics.proxy_env
                ))
            })?;
        let elapsed_ms = elapsed_ms(started_at);
        let body_len = body.len();
        if !status.is_success() {
            warn!(
                provider = "openai_compatible",
                model = %model,
                url = %url,
                status = %status,
                elapsed_ms = elapsed_ms,
                response_body_len = body_len,
                "openai-compatible: chat completion returned non-success status"
            );
            return Err(LlmError::Request(format!(
                "openai-compatible returned {status} from {url} after {elapsed_ms}ms; body_len={body_len}"
            )));
        }
        let raw: Value = serde_json::from_str(&body).map_err(|error| {
            warn!(
                provider = "openai_compatible",
                model = %model,
                url = %url,
                status = %status,
                elapsed_ms = elapsed_ms,
                response_body_len = body_len,
                error = %error,
                "openai-compatible: chat completion response is not valid JSON"
            );
            LlmError::Response(format!(
                "openai-compatible response from {url} with status {status} is not valid JSON after {elapsed_ms}ms: {error}; body_len={body_len}"
            ))
        })?;
        let parsed: OpenAiCompatibleChatResponse = serde_json::from_value(raw.clone())
            .map_err(|error| {
                warn!(
                    provider = "openai_compatible",
                    model = %model,
                    url = %url,
                    status = %status,
                    elapsed_ms = elapsed_ms,
                    response_body_len = body_len,
                    error = %error,
                    "openai-compatible: chat completion response schema mismatch"
                );
                LlmError::Response(format!(
                    "openai-compatible response schema mismatch from {url} with status {status} after {elapsed_ms}ms: {error}; body_len={body_len}"
                ))
            })?;
        let content = parsed
            .choices
            .into_iter()
            .next()
            .and_then(choice_message_content)
            .ok_or_else(|| {
                warn!(
                    provider = "openai_compatible",
                    model = %model,
                    url = %url,
                    status = %status,
                    elapsed_ms = elapsed_ms,
                    response_body_len = body_len,
                    "openai-compatible: chat completion response missing content"
                );
                LlmError::Response(format!(
                    "openai-compatible response from {url} with status {status} missing choices[0].message.content or tool_calls[0].function.arguments after {elapsed_ms}ms; body_len={body_len}"
                ))
            })?;
        Ok(LlmChatCompletionResponse { content, raw })
    }
}

fn apply_structured_output(
    body: &mut Value,
    response_format: Option<Value>,
    mode: StructuredOutputMode,
) -> Result<(), LlmError> {
    let Some(response_format) = response_format else {
        return Ok(());
    };
    match mode {
        StructuredOutputMode::JsonSchema => {
            body["response_format"] = response_format;
        }
        StructuredOutputMode::JsonObject => {
            body["response_format"] = json!({"type": "json_object"});
        }
        StructuredOutputMode::ToolCall => {
            apply_tool_call_schema(body, response_format)?;
        }
    }
    Ok(())
}

fn apply_tool_call_schema(body: &mut Value, response_format: Value) -> Result<(), LlmError> {
    if !response_format
        .get("type")
        .and_then(Value::as_str)
        .is_some_and(|format_type| format_type == "json_schema")
    {
        body["response_format"] = response_format;
        return Ok(());
    }
    let json_schema = response_format
        .get("json_schema")
        .ok_or_else(|| LlmError::Config("json_schema response_format is missing json_schema".to_string()))?;
    let name = json_schema
        .get("name")
        .and_then(Value::as_str)
        .filter(|name| !name.trim().is_empty())
        .ok_or_else(|| LlmError::Config("json_schema response_format requires a non-empty name".to_string()))?;
    let schema = json_schema
        .get("schema")
        .cloned()
        .ok_or_else(|| LlmError::Config("json_schema response_format is missing schema".to_string()))?;
    let strict = json_schema
        .get("strict")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    body["tools"] = json!([
        {
            "type": "function",
            "function": {
                "name": name,
                "description": "Return the structured judge response.",
                "parameters": schema,
                "strict": strict
            }
        }
    ]);
    body["tool_choice"] = json!({
        "type": "function",
        "function": {
            "name": name
        }
    });
    Ok(())
}

fn response_format_type(response_format: Option<&Value>) -> String {
    response_format
        .and_then(|format| format.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("none")
        .to_string()
}

fn structured_output_mode_name(mode: StructuredOutputMode) -> &'static str {
    match mode {
        StructuredOutputMode::JsonSchema => "json_schema",
        StructuredOutputMode::JsonObject => "json_object",
        StructuredOutputMode::ToolCall => "tool_call",
    }
}

struct ReqwestErrorDiagnostics {
    timeout: bool,
    connect: bool,
    request: bool,
    body: bool,
    decode: bool,
    status: String,
    source_chain: String,
    debug: String,
    proxy_env: String,
}

fn reqwest_error_diagnostics(error: &reqwest::Error) -> ReqwestErrorDiagnostics {
    ReqwestErrorDiagnostics {
        timeout: error.is_timeout(),
        connect: error.is_connect(),
        request: error.is_request(),
        body: error.is_body(),
        decode: error.is_decode(),
        status: error
            .status()
            .map(|status| status.to_string())
            .unwrap_or_else(|| "none".to_string()),
        source_chain: error_source_chain(error),
        debug: format!("{error:?}"),
        proxy_env: proxy_env_summary(),
    }
}

fn error_source_chain(error: &dyn Error) -> String {
    let mut parts = vec![error.to_string()];
    let mut current = error.source();
    while let Some(source) = current {
        parts.push(source.to_string());
        current = source.source();
    }
    parts.join(" | caused by: ")
}

fn proxy_env_summary() -> String {
    [
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ]
    .into_iter()
    .filter_map(|name| {
        env::var(name)
            .ok()
            .map(|value| format!("{name}={}", redact_proxy(&value)))
    })
    .collect::<Vec<_>>()
    .join(",")
}

fn redact_proxy(value: &str) -> String {
    if let Some(at_index) = value.rfind('@') {
        let prefix = &value[..at_index];
        if let Some(scheme_index) = prefix.find("://") {
            return format!("{}://***{}", &prefix[..scheme_index], &value[at_index..]);
        }
    }
    value.to_string()
}

fn elapsed_ms(started_at: Instant) -> u64 {
    started_at
        .elapsed()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn choice_message_content(choice: OpenAiCompatibleChoice) -> Option<String> {
    if let Some(content) = choice
        .message
        .content
        .filter(|content| !content.trim().is_empty())
    {
        return Some(content);
    }
    choice
        .message
        .tool_calls
        .into_iter()
        .map(|tool_call| tool_call.function.arguments)
        .find(|arguments| !arguments.trim().is_empty())
}

#[derive(Debug, Deserialize)]
struct OpenAiCompatibleChatResponse {
    choices: Vec<OpenAiCompatibleChoice>,
}

#[derive(Debug, Deserialize)]
struct OpenAiCompatibleChoice {
    message: OpenAiCompatibleMessage,
}

#[derive(Debug, Deserialize)]
struct OpenAiCompatibleMessage {
    content: Option<String>,
    #[serde(default)]
    tool_calls: Vec<OpenAiCompatibleToolCall>,
}

#[derive(Debug, Deserialize)]
struct OpenAiCompatibleToolCall {
    function: OpenAiCompatibleToolFunction,
}

#[derive(Debug, Deserialize)]
struct OpenAiCompatibleToolFunction {
    arguments: String,
}
