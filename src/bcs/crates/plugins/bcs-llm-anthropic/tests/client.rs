use bcs_config_api::{LlmConfig, LlmProviderType, StructuredOutputMode};
use bcs_llm_anthropic::AnthropicLlmClient;
use bcs_llm_api::{LlmChatCompletionPort, LlmChatCompletionRequest, LlmChatMessage};
use secrecy::Secret;
use serde_json::json;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[test]
fn builds_anthropic_json_schema_request() {
    let client = AnthropicLlmClient::new(test_config(
        "https://api.anthropic.com/",
        StructuredOutputMode::JsonSchema,
    ))
    .expect("anthropic client");

    let request = client
        .build_http_request(test_request(judge_response_format()))
        .expect("http request");

    assert_eq!(
        request.url().as_str(),
        "https://api.anthropic.com/v1/messages"
    );
    assert_eq!(
        request.headers()["x-api-key"]
            .to_str()
            .expect("api key header"),
        "anthropic-key"
    );
    assert_eq!(
        request.headers()["anthropic-version"]
            .to_str()
            .expect("version header"),
        "2023-06-01"
    );
    assert!(request.headers().get("authorization").is_none());

    let body = request_json_body(&request);
    assert_eq!(body["model"], "claude-sonnet-4-6");
    assert_eq!(body["max_tokens"], 4096);
    assert_eq!(body["stream"], false);
    assert!(body.get("temperature").is_none());
    assert_eq!(body["system"], "Return JSON only.");
    assert_eq!(body["messages"].as_array().map(Vec::len), Some(3));
    assert_eq!(body["messages"][0]["role"], "user");
    assert_eq!(body["messages"][0]["content"], "Judge the candidate.");
    assert_eq!(body["messages"][1]["role"], "assistant");
    assert_eq!(
        body["messages"][1]["content"],
        "I will evaluate the candidate against the criteria."
    );
    assert_eq!(body["messages"][2]["role"], "user");
    assert_eq!(body["messages"][2]["content"], "Return the final outcome now.");
    assert_eq!(body["output_config"]["format"]["type"], "json_schema");
    assert_eq!(
        body["output_config"]["format"]["schema"]["properties"]["outcome"]["enum"],
        json!(["approved", "rejected"])
    );
}

#[test]
fn accepts_base_url_that_already_contains_v1() {
    let client = AnthropicLlmClient::new(test_config(
        "https://api.anthropic.com/v1/",
        StructuredOutputMode::JsonSchema,
    ))
    .expect("anthropic client");

    let request = client
        .build_http_request(test_request(judge_response_format()))
        .expect("http request");

    assert_eq!(
        request.url().as_str(),
        "https://api.anthropic.com/v1/messages"
    );
}

#[test]
fn builds_kimi_anthropic_compatible_messages_url() {
    let client = AnthropicLlmClient::new(test_config(
        "https://api.kimi.com/coding/",
        StructuredOutputMode::JsonSchema,
    ))
    .expect("anthropic-compatible client");

    let request = client
        .build_http_request(test_request(judge_response_format()))
        .expect("http request");

    assert_eq!(
        request.url().as_str(),
        "https://api.kimi.com/coding/v1/messages"
    );
}

#[test]
fn builds_anthropic_forced_tool_call_request() {
    let client = AnthropicLlmClient::new(test_config(
        "https://api.anthropic.com/v1",
        StructuredOutputMode::ToolCall,
    ))
    .expect("anthropic client");

    let request = client
        .build_http_request(test_request(judge_response_format()))
        .expect("http request");
    let body = request_json_body(&request);

    assert!(body.get("output_config").is_none());
    assert_eq!(body["tools"][0]["name"], "judge_response");
    assert_eq!(body["tools"][0]["strict"], true);
    assert_eq!(
        body["tools"][0]["input_schema"]["properties"]["outcome"]["enum"],
        json!(["approved", "rejected"])
    );
    assert_eq!(
        body["tool_choice"],
        json!({"type": "tool", "name": "judge_response"})
    );
}

#[test]
fn rejects_json_object_mode() {
    let error = match AnthropicLlmClient::new(test_config(
        "https://api.anthropic.com/v1",
        StructuredOutputMode::JsonObject,
    )) {
        Ok(_) => panic!("json_object must be rejected"),
        Err(error) => error,
    };

    assert!(error.to_string().contains("does not support"));
}

#[test]
fn rejects_empty_base_url_and_streaming_requests() {
    let error = match AnthropicLlmClient::new(test_config(" \t", StructuredOutputMode::JsonSchema))
    {
        Ok(_) => panic!("empty base URL must be rejected"),
        Err(error) => error,
    };
    assert!(error.to_string().contains("base_url must not be empty"));

    let client = AnthropicLlmClient::new(test_config(
        "https://api.anthropic.com/v1",
        StructuredOutputMode::JsonSchema,
    ))
    .expect("anthropic client");
    let mut request = test_request(judge_response_format());
    request.stream = true;

    let error = client
        .build_http_request(request)
        .expect_err("streaming must be rejected")
        .to_string();
    assert!(error.contains("streaming is not supported"), "{error}");
}

#[test]
fn rejects_invalid_message_shapes() {
    let client = AnthropicLlmClient::new(test_config(
        "https://api.anthropic.com/v1",
        StructuredOutputMode::JsonSchema,
    ))
    .expect("anthropic client");

    let cases = [
        (
            vec![LlmChatMessage {
                role: "tool".to_string(),
                content: json!("result"),
            }],
            "does not support LLM message role 'tool'",
        ),
        (
            vec![LlmChatMessage {
                role: "system".to_string(),
                content: json!("instructions only"),
            }],
            "requires at least one user or assistant message",
        ),
        (
            vec![
                LlmChatMessage {
                    role: "system".to_string(),
                    content: json!({"type": "text", "text": "instructions"}),
                },
                LlmChatMessage {
                    role: "user".to_string(),
                    content: json!("question"),
                },
            ],
            "system message content must be a string or text-block array",
        ),
        (
            vec![
                LlmChatMessage {
                    role: "system".to_string(),
                    content: json!([{"type": "image", "text": "not text"}]),
                },
                LlmChatMessage {
                    role: "user".to_string(),
                    content: json!("question"),
                },
            ],
            "system message arrays may contain only text blocks",
        ),
    ];

    for (messages, expected) in cases {
        let mut request = test_request(judge_response_format());
        request.messages = messages;
        let error = client
            .build_http_request(request)
            .expect_err("invalid message shape must be rejected")
            .to_string();
        assert!(error.contains(expected), "{error}");
    }
}

#[tokio::test]
async fn parses_anthropic_text_response() {
    let body = r#"{"content":[{"type":"text","text":"{\"outcome\":\"approved\"}"}],"stop_reason":"end_turn","usage":{"input_tokens":10,"output_tokens":5}}"#;
    let base_url = spawn_anthropic_response(200, body, "req-text").await;
    let client = AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::JsonSchema))
        .expect("anthropic client");

    let response = client
        .complete(test_request(judge_response_format()))
        .await
        .expect("completion response");

    assert_eq!(response.content, "{\"outcome\":\"approved\"}");
    assert_eq!(response.raw["usage"]["input_tokens"], 10);
}

#[tokio::test]
async fn joins_all_anthropic_text_blocks_in_order() {
    let body = r#"{"content":[{"type":"text","text":"{\"outcome\":"},{"type":"thinking","thinking":"hidden","signature":"sig"},{"type":"text","text":"\"approved\"}"}],"stop_reason":"end_turn"}"#;
    let base_url = spawn_anthropic_response(200, body, "req-multi-text").await;
    let client = AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::JsonSchema))
        .expect("anthropic client");

    let response = client
        .complete(test_request(judge_response_format()))
        .await
        .expect("completion response");

    assert_eq!(response.content, "{\"outcome\":\"approved\"}");
}

#[tokio::test]
async fn anthropic_passes_llm_chat_completion_contract() {
    let body = r#"{"content":[{"type":"text","text":"{\"outcome\":\"complete\"}"}],"stop_reason":"end_turn"}"#;
    let base_url = spawn_anthropic_response(200, body, "req-contract").await;
    let client = AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::JsonSchema))
        .expect("anthropic client");

    bcs_test_support::contract::plugin::llm_chat_completion_contract_tests(
        &client,
        "claude-sonnet-4-6",
    )
    .await;
}

#[tokio::test]
async fn parses_anthropic_tool_use_response() {
    let body = r#"{"content":[{"type":"tool_use","id":"toolu_1","name":"judge_response","input":{"outcome":"approved"}}],"stop_reason":"tool_use"}"#;
    let base_url = spawn_anthropic_response(200, body, "req-tool").await;
    let client = AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::ToolCall))
        .expect("anthropic client");

    let response = client
        .complete(test_request(judge_response_format()))
        .await
        .expect("completion response");

    assert_eq!(response.content, "{\"outcome\":\"approved\"}");
}

#[tokio::test]
async fn tool_call_mode_parses_plain_text_without_response_format() {
    let body = r#"{"content":[{"type":"text","text":"plain response"}],"stop_reason":"end_turn"}"#;
    let base_url = spawn_anthropic_response(200, body, "req-plain").await;
    let client = AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::ToolCall))
        .expect("anthropic client");
    let mut request = test_request(judge_response_format());
    request.response_format = None;

    let response = client
        .complete(request)
        .await
        .expect("plain completion response");

    assert_eq!(response.content, "plain response");
}

#[tokio::test]
async fn request_transport_error_includes_diagnostics() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind mock server");
    let addr = listener.local_addr().expect("mock server addr");
    tokio::spawn(async move {
        let (socket, _) = listener.accept().await.expect("accept mock request");
        drop(socket);
    });
    let client = AnthropicLlmClient::new(test_config(
        &format!("http://{addr}/v1"),
        StructuredOutputMode::JsonSchema,
    ))
    .expect("anthropic client");

    let error = client
        .complete(test_request(judge_response_format()))
        .await
        .expect_err("closed connection must fail")
        .to_string();

    assert!(error.contains("anthropic request to"), "{error}");
    assert!(error.contains("structured_output=json_schema"), "{error}");
    assert!(error.contains("connect="), "{error}");
}

#[tokio::test]
async fn response_body_read_error_includes_request_context() {
    let raw_response = "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\nrequest-id: req-short\r\ncontent-length: 50\r\n\r\n{";
    let base_url = spawn_raw_response(raw_response).await;
    let client = AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::JsonSchema))
        .expect("anthropic client");

    let error = client
        .complete(test_request(judge_response_format()))
        .await
        .expect_err("truncated response body must fail")
        .to_string();

    assert!(error.contains("failed to read response body"), "{error}");
    assert!(error.contains("req-short"), "{error}");
}

#[tokio::test]
async fn rejects_invalid_json_and_schema_mismatch() {
    let invalid_json_url = spawn_anthropic_response(200, "not-json", "req-json").await;
    let invalid_json_client = AnthropicLlmClient::new(test_config(
        &invalid_json_url,
        StructuredOutputMode::JsonSchema,
    ))
    .expect("anthropic client");
    let error = invalid_json_client
        .complete(test_request(judge_response_format()))
        .await
        .expect_err("invalid JSON must fail")
        .to_string();
    assert!(error.contains("not valid JSON"), "{error}");
    assert!(error.contains("req-json"), "{error}");

    let invalid_schema_url = spawn_anthropic_response(
        200,
        r#"{"content":"wrong","stop_reason":"end_turn"}"#,
        "req-schema",
    )
    .await;
    let invalid_schema_client = AnthropicLlmClient::new(test_config(
        &invalid_schema_url,
        StructuredOutputMode::JsonSchema,
    ))
    .expect("anthropic client");
    let error = invalid_schema_client
        .complete(test_request(judge_response_format()))
        .await
        .expect_err("schema mismatch must fail")
        .to_string();
    assert!(error.contains("schema mismatch"), "{error}");
    assert!(error.contains("req-schema"), "{error}");
}

#[tokio::test]
async fn rejects_missing_expected_content_and_incomplete_stop_reasons() {
    let missing_url = spawn_anthropic_response(
        200,
        r#"{"content":[{"type":"thinking","thinking":"hidden"}],"stop_reason":"end_turn"}"#,
        "req-missing",
    )
    .await;
    let missing_client =
        AnthropicLlmClient::new(test_config(&missing_url, StructuredOutputMode::JsonSchema))
            .expect("anthropic client");
    let error = missing_client
        .complete(test_request(judge_response_format()))
        .await
        .expect_err("missing text content must fail")
        .to_string();
    assert!(
        error.contains("missing the expected json_schema content"),
        "{error}"
    );
    assert!(error.contains("req-missing"), "{error}");

    for (body, request_id) in [
        (
            r#"{"content":[{"type":"text","text":"{\"outcome\":\"approved\"}"}]}"#,
            "req-stop-missing",
        ),
        (
            r#"{"content":[{"type":"text","text":"{\"outcome\":\"approved\"}"}],"stop_reason":null}"#,
            "req-stop-null",
        ),
    ] {
        let base_url = spawn_anthropic_response(200, body, request_id).await;
        let client =
            AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::JsonSchema))
                .expect("anthropic client");
        let error = client
            .complete(test_request(judge_response_format()))
            .await
            .expect_err("missing stop reason must fail")
            .to_string();
        assert!(error.contains("missing stop_reason"), "{error}");
        assert!(error.contains(request_id), "{error}");
    }

    for (stop_reason, expected) in [
        ("max_tokens", "reached max_tokens"),
        (
            "model_context_window_exceeded",
            "exceeded the model context window",
        ),
        ("pause_turn", "paused before completing"),
    ] {
        let body = format!(r#"{{"content":[],"stop_reason":"{stop_reason}"}}"#);
        let base_url = spawn_anthropic_response(200, &body, "req-incomplete").await;
        let client =
            AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::JsonSchema))
                .expect("anthropic client");
        let error = client
            .complete(test_request(judge_response_format()))
            .await
            .expect_err("incomplete response must fail")
            .to_string();
        assert!(error.contains(expected), "{error}");
    }
}

#[tokio::test]
async fn rejects_refusal_stop_reason() {
    let body = r#"{"content":[],"stop_reason":"refusal","stop_details":{"type":"refusal"}}"#;
    let base_url = spawn_anthropic_response(200, body, "req-refusal").await;
    let client = AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::JsonSchema))
        .expect("anthropic client");

    let error = client
        .complete(test_request(judge_response_format()))
        .await
        .expect_err("refusal should fail")
        .to_string();

    assert!(error.contains("refused"), "{error}");
    assert!(error.contains("req-refusal"), "{error}");
}

#[tokio::test]
async fn http_error_includes_status_body_and_request_id() {
    let base_url =
        spawn_anthropic_response(429, r#"{"error":{"message":"rate limited"}}"#, "req-rate").await;
    let client = AnthropicLlmClient::new(test_config(&base_url, StructuredOutputMode::JsonSchema))
        .expect("anthropic client");

    let error = client
        .complete(test_request(judge_response_format()))
        .await
        .expect_err("rate limit should fail")
        .to_string();

    assert!(error.contains("429 Too Many Requests"), "{error}");
    assert!(error.contains("rate limited"), "{error}");
    assert!(error.contains("req-rate"), "{error}");
}

fn test_config(base_url: &str, structured_output: StructuredOutputMode) -> LlmConfig {
    LlmConfig {
        provider_type: LlmProviderType::Anthropic,
        base_url: base_url.to_string(),
        api_key_env: None,
        api_key: Some(Secret::new("anthropic-key".to_string())),
        model: "claude-sonnet-4-6".to_string(),
        timeout_ms: 10_000,
        temperature: 0.0,
        max_tokens: 4_096,
        structured_output,
    }
}

fn test_request(response_format: serde_json::Value) -> LlmChatCompletionRequest {
    LlmChatCompletionRequest {
        model: "claude-sonnet-4-6".to_string(),
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
    let body = request
        .body()
        .and_then(|body| body.as_bytes())
        .expect("json body");
    serde_json::from_slice(body).expect("body json")
}

async fn spawn_anthropic_response(status: u16, body: &str, request_id: &str) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind mock server");
    let addr = listener.local_addr().expect("mock server addr");
    let reason = match status {
        200 => "OK",
        429 => "Too Many Requests",
        _ => "Error",
    };
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\ncontent-type: application/json\r\nrequest-id: {request_id}\r\ncontent-length: {}\r\n\r\n{body}",
        body.len()
    );
    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.expect("accept mock request");
        let mut request = vec![0; 8_192];
        let _ = socket.read(&mut request).await.expect("read mock request");
        socket
            .write_all(response.as_bytes())
            .await
            .expect("write mock response");
    });
    format!("http://{addr}/v1")
}

async fn spawn_raw_response(response: &str) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind mock server");
    let addr = listener.local_addr().expect("mock server addr");
    let response = response.to_string();
    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.expect("accept mock request");
        let mut request = vec![0; 8_192];
        let _ = socket.read(&mut request).await.expect("read mock request");
        socket
            .write_all(response.as_bytes())
            .await
            .expect("write mock response");
    });
    format!("http://{addr}/v1")
}
