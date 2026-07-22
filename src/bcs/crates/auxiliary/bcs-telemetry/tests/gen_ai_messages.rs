use bcs_telemetry::{capture_gen_ai_input_messages, capture_gen_ai_output_messages};
use serde_json::Value;

const LIMIT_BYTES: usize = 4096;

#[test]
fn input_messages_are_schema_compliant_json() {
    let captured = capture_gen_ai_input_messages("你好 \"BCN\"", LIMIT_BYTES);

    let Ok(messages): Result<Value, _> = serde_json::from_str(&captured.value) else {
        panic!("input messages must be valid JSON");
    };
    assert_eq!(messages[0]["role"], "user");
    assert_eq!(messages[0]["parts"][0]["type"], "text");
    assert_eq!(messages[0]["parts"][0]["content"], "你好 \"BCN\"");
    assert_eq!(messages.as_array().map(Vec::len), Some(1));
    assert!(!captured.truncated);
}

#[test]
fn output_messages_are_schema_compliant_json() {
    let captured = capture_gen_ai_output_messages("response text", "stop", LIMIT_BYTES);

    let Ok(messages): Result<Value, _> = serde_json::from_str(&captured.value) else {
        panic!("output messages must be valid JSON");
    };
    assert_eq!(messages[0]["role"], "assistant");
    assert_eq!(messages[0]["parts"][0]["type"], "text");
    assert_eq!(messages[0]["parts"][0]["content"], "response text");
    assert_eq!(messages[0]["finish_reason"], "stop");
    assert_eq!(messages.as_array().map(Vec::len), Some(1));
    assert!(!captured.truncated);
}

#[test]
fn truncation_preserves_valid_json_and_truncates_only_content() {
    let content = format!("START{}\\\"\nEND", "你".repeat(3000));

    let captured = capture_gen_ai_output_messages(&content, "error", LIMIT_BYTES);

    assert!(captured.truncated);
    assert!(captured.value.len() <= LIMIT_BYTES);
    assert_eq!(captured.original_size_bytes, content.len());
    let Ok(messages): Result<Value, _> = serde_json::from_str(&captured.value) else {
        panic!("truncated output messages must be valid JSON");
    };
    let Some(text) = messages[0]["parts"][0]["content"].as_str() else {
        panic!("output message must contain text content");
    };
    assert!(text.starts_with("START"));
    assert!(text.ends_with("\\\"\nEND"));
    assert!(text.contains("...[TRUNCATED]..."));
    assert_eq!(messages[0]["finish_reason"], "error");
    assert_eq!(captured.captured_size_bytes, text.len());
}
