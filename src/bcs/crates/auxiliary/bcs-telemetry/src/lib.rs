//! Pure OpenTelemetry semantic attribute encoders shared by delivery adapters.
//!
//! This crate owns no tracing pipeline or BCS business behavior. It only converts
//! adapter-provided text into bounded, schema-compliant GenAI message JSON.

use serde_json::{Value, json};

const TRUNCATION_MARKER: &str = "...[TRUNCATED]...";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapturedGenAiMessages {
    /// JSON string stored in the GenAI span attribute.
    pub value: String,
    /// Original unencoded text size.
    pub original_size_bytes: usize,
    /// Captured unencoded text size, including the truncation marker when present.
    pub captured_size_bytes: usize,
    /// Whether the text content was truncated before JSON serialization.
    pub truncated: bool,
}

/// Encode one user text message using the `gen_ai.input.messages` schema.
pub fn capture_gen_ai_input_messages(
    content: &str,
    limit_bytes: usize,
) -> CapturedGenAiMessages {
    capture_gen_ai_messages(content, None, limit_bytes)
}

/// Encode one assistant text message using the `gen_ai.output.messages` schema.
pub fn capture_gen_ai_output_messages(
    content: &str,
    finish_reason: &str,
    limit_bytes: usize,
) -> CapturedGenAiMessages {
    capture_gen_ai_messages(content, Some(finish_reason), limit_bytes)
}

fn capture_gen_ai_messages(
    content: &str,
    finish_reason: Option<&str>,
    limit_bytes: usize,
) -> CapturedGenAiMessages {
    let original = serialize_messages(content, finish_reason);
    if original.len() <= limit_bytes {
        return CapturedGenAiMessages {
            value: original,
            original_size_bytes: content.len(),
            captured_size_bytes: content.len(),
            truncated: false,
        };
    }

    let empty_size = serialize_messages("", finish_reason).len();
    let content_budget = limit_bytes
        .saturating_sub(empty_size)
        .saturating_sub(TRUNCATION_MARKER.len());
    let head_budget = content_budget.saturating_mul(3) / 4;
    let head_end = take_head_within_json_budget(content, head_budget);
    let head_size = escaped_json_len(&content[..head_end]);
    let tail_budget = content_budget.saturating_sub(head_size);
    let tail_start = take_tail_within_json_budget(content, head_end, tail_budget);
    let captured_content = format!(
        "{}{}{}",
        &content[..head_end],
        TRUNCATION_MARKER,
        &content[tail_start..]
    );
    let value = serialize_messages(&captured_content, finish_reason);

    CapturedGenAiMessages {
        value,
        original_size_bytes: content.len(),
        captured_size_bytes: captured_content.len(),
        truncated: true,
    }
}

fn serialize_messages(content: &str, finish_reason: Option<&str>) -> String {
    let mut message = json!({
        "role": if finish_reason.is_some() { "assistant" } else { "user" },
        "parts": [{
            "type": "text",
            "content": content,
        }],
    });
    if let Some(finish_reason) = finish_reason {
        message["finish_reason"] = Value::String(finish_reason.to_string());
    }
    Value::Array(vec![message]).to_string()
}

fn take_head_within_json_budget(content: &str, budget: usize) -> usize {
    let mut size: usize = 0;
    let mut end = 0;
    for (index, character) in content.char_indices() {
        let character_size = escaped_json_char_len(character);
        if size.saturating_add(character_size) > budget {
            break;
        }
        size += character_size;
        end = index + character.len_utf8();
    }
    end
}

fn take_tail_within_json_budget(content: &str, head_end: usize, budget: usize) -> usize {
    let mut size: usize = 0;
    let mut start = content.len();
    for (index, character) in content.char_indices().rev() {
        if index < head_end {
            break;
        }
        let character_size = escaped_json_char_len(character);
        if size.saturating_add(character_size) > budget {
            break;
        }
        size += character_size;
        start = index;
    }
    start
}

fn escaped_json_len(content: &str) -> usize {
    content.chars().map(escaped_json_char_len).sum()
}

fn escaped_json_char_len(character: char) -> usize {
    match character {
        '\"' | '\\' | '\u{0008}' | '\u{000c}' | '\n' | '\r' | '\t' => 2,
        '\u{0000}'..='\u{001f}' => 6,
        _ => character.len_utf8(),
    }
}
