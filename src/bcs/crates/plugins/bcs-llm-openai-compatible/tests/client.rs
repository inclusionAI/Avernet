use bcs_config_api::{LlmConfig, LlmProviderType, StructuredOutputMode};
use bcs_llm_api::{LlmChatCompletionPort, LlmChatCompletionRequest, LlmChatMessage};
use bcs_llm_openai_compatible::OpenAiCompatibleLlmClient;
use secrecy::Secret;
use serde_json::json;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::time::Duration;

#[test]
fn builds_openai_compatible_request_with_json_schema_response_format() {
    let client = OpenAiCompatibleLlmClient::new(test_config(
        "https://api.openai.com/v1/",
        StructuredOutputMode::JsonSchema,
        10_000,
    ))
    .expect("openai-compatible client");
    let response_format = judge_response_format();

    let request = client
        .build_http_request(test_request(response_format))
        .expect("http request");

    assert_eq!(
        request.url().as_str(),
        "https://api.openai.com/v1/chat/completions"
    );
    assert_eq!(
        request.headers()["authorization"].to_str().expect("auth header"),
        "Bearer openai-key"
    );
    let json_body = request_json_body(&request);
    assert_eq!(json_body["model"], "gpt-4.1-mini");
    assert_eq!(json_body["stream"], false);
    assert_eq!(json_body["temperature"], 0.0);
    assert_eq!(json_body["max_tokens"], 4096);
    assert_eq!(json_body["messages"][0]["content"], "Return JSON only.");
    assert_eq!(json_body["response_format"]["type"], "json_schema");
    assert_eq!(json_body["response_format"]["json_schema"]["strict"], true);
}

#[test]
fn downgrades_to_json_object_when_configured() {
    let client = OpenAiCompatibleLlmClient::new(test_config(
        "https://api.openai.com/v1",
        StructuredOutputMode::JsonObject,
        10_000,
    ))
    .expect("openai-compatible client");

    let request = client
        .build_http_request(test_request(judge_response_format()))
        .expect("http request");

    let json_body = request_json_body(&request);
    assert_eq!(json_body["response_format"], json!({"type": "json_object"}));
}

#[test]
fn builds_forced_tool_call_when_configured() {
    let client = OpenAiCompatibleLlmClient::new(test_config(
        "https://api.openai.com/v1",
        StructuredOutputMode::ToolCall,
        10_000,
    ))
    .expect("openai-compatible client");

    let request = client
        .build_http_request(test_request(judge_response_format()))
        .expect("http request");

    let json_body = request_json_body(&request);
    assert!(json_body.get("response_format").is_none());
    assert_eq!(json_body["tools"][0]["type"], "function");
    assert_eq!(
        json_body["tools"][0]["function"]["name"],
        "judge_response"
    );
    assert_eq!(json_body["tools"][0]["function"]["strict"], true);
    assert_eq!(
        json_body["tool_choice"],
        json!({"type": "function", "function": {"name": "judge_response"}})
    );
}

#[tokio::test]
async fn openai_compatible_http_error_includes_status_url_and_body_excerpt() {
    let base_url = spawn_openai_compatible_response(
        "HTTP/1.1 400 Bad Request\r\ncontent-type: text/plain\r\ncontent-length: 29\r\n\r\nupstream rejected json_object",
    )
    .await;
    let client = OpenAiCompatibleLlmClient::new(test_config(
        &base_url,
        StructuredOutputMode::JsonObject,
        10_000,
    ))
    .expect("openai-compatible client");

    let error = client
        .complete(test_request(json!({"type": "json_object"})))
        .await
        .expect_err("http error should include diagnostics")
        .to_string();

    assert!(error.contains("400 Bad Request"), "{error}");
    assert!(error.contains("/chat/completions"), "{error}");
    assert!(error.contains("upstream rejected json_object"), "{error}");
}

#[tokio::test]
async fn openai_compatible_success_response_parses_choice_content() {
    let body = r#"{"choices":[{"message":{"content":"{\"outcome\":\"complete\"}"}}],"usage":{"total_tokens":12}}"#;
    let response = format!(
        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\n\r\n{}",
        body.len(),
        body
    );
    let base_url = spawn_openai_compatible_response(response).await;
    let client = OpenAiCompatibleLlmClient::new(test_config(
        &base_url,
        StructuredOutputMode::JsonObject,
        10_000,
    ))
    .expect("openai-compatible client");

    let response = client
        .complete(test_request(json!({"type": "json_object"})))
        .await
        .expect("completion response");

    assert_eq!(response.content, "{\"outcome\":\"complete\"}");
    assert_eq!(response.raw["usage"]["total_tokens"], 12);
}

#[tokio::test]
async fn openai_compatible_passes_llm_chat_completion_contract() {
    let body = r#"{"choices":[{"message":{"content":"{\"outcome\":\"complete\"}"}}]}"#;
    let response = format!(
        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\n\r\n{}",
        body.len(),
        body
    );
    let base_url = spawn_openai_compatible_response(response).await;
    let client = OpenAiCompatibleLlmClient::new(test_config(
        &base_url,
        StructuredOutputMode::JsonSchema,
        10_000,
    ))
    .expect("openai-compatible client");

    bcs_test_support::contract::plugin::llm_chat_completion_contract_tests(&client, "gpt-4.1-mini")
        .await;
}

#[tokio::test]
async fn openai_compatible_success_response_parses_tool_call_arguments() {
    let body = r#"{"choices":[{"message":{"content":null,"tool_calls":[{"function":{"arguments":"{\"outcome\":\"complete\"}"}}]}}],"usage":{"total_tokens":12}}"#;
    let response = format!(
        "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\n\r\n{}",
        body.len(),
        body
    );
    let base_url = spawn_openai_compatible_response(response).await;
    let client = OpenAiCompatibleLlmClient::new(test_config(
        &base_url,
        StructuredOutputMode::ToolCall,
        10_000,
    ))
    .expect("openai-compatible client");

    let response = client
        .complete(test_request(judge_response_format()))
        .await
        .expect("completion response");

    assert_eq!(response.content, "{\"outcome\":\"complete\"}");
}

#[tokio::test]
async fn openai_compatible_timeout_error_includes_reqwest_diagnostics() {
    let base_url = spawn_hanging_openai_compatible_server().await;
    let client = OpenAiCompatibleLlmClient::new(test_config(
        &base_url,
        StructuredOutputMode::JsonObject,
        20,
    ))
    .expect("openai-compatible client");

    let error = client
        .complete(test_request(json!({"type": "json_object"})))
        .await
        .expect_err("hanging server should time out")
        .to_string();

    assert!(error.contains("timeout=true"), "{error}");
    assert!(error.contains("connect="), "{error}");
    assert!(error.contains("source_chain="), "{error}");
    assert!(error.contains("debug="), "{error}");
    assert!(error.contains("proxy_env="), "{error}");
    assert!(error.contains("structured_output=json_object"), "{error}");
}

fn test_config(
    base_url: &str,
    structured_output: StructuredOutputMode,
    timeout_ms: u64,
) -> LlmConfig {
    LlmConfig {
        provider_type: LlmProviderType::OpenAiCompatible,
        base_url: base_url.to_string(),
        api_key_env: None,
        api_key: Some(Secret::new("openai-key".to_string())),
        model: "gpt-4.1-mini".to_string(),
        timeout_ms,
        temperature: 0.0,
        max_tokens: 4_096,
        structured_output,
    }
}

fn test_request(response_format: serde_json::Value) -> LlmChatCompletionRequest {
    LlmChatCompletionRequest {
        model: "gpt-4.1-mini".to_string(),
        messages: vec![LlmChatMessage {
            role: "system".to_string(),
            content: json!("Return JSON only."),
        }],
        response_format: Some(response_format),
        stream: false,
    }
}

fn judge_response_format() -> serde_json::Value {
    json!({
        "type": "json_schema",
        "json_schema": {
            "name": "judge_response",
            "strict": true,
            "schema": {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": ["approved", "rejected"]}
                },
                "required": ["outcome"],
                "additionalProperties": false
            }
        }
    })
}

fn request_json_body(request: &reqwest::Request) -> serde_json::Value {
    let body = request.body().and_then(|body| body.as_bytes()).expect("json body");
    serde_json::from_slice(body).expect("body json")
}

async fn spawn_openai_compatible_response(response: impl Into<String>) -> String {
    let response = response.into();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind mock server");
    let addr = listener.local_addr().expect("mock server addr");
    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.expect("accept mock request");
        let mut request = vec![0; 4096];
        let _ = socket.read(&mut request).await.expect("read mock request");
        socket
            .write_all(response.as_bytes())
            .await
            .expect("write mock response");
    });
    format!("http://{addr}")
}

async fn spawn_hanging_openai_compatible_server() -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind hanging mock server");
    let addr = listener.local_addr().expect("hanging mock server addr");
    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.expect("accept hanging mock request");
        let mut request = vec![0; 4096];
        let _ = socket.read(&mut request).await.expect("read hanging mock request");
        tokio::time::sleep(Duration::from_millis(200)).await;
    });
    format!("http://{addr}")
}
