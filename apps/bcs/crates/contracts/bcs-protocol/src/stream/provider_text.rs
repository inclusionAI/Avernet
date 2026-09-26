//! Provider chat delta/final text assembly.
//!
//! Provider finals are full snapshots on some engines and suffixes on others.
//! This module keeps that wire-format interpretation in the protocol crate so
//! service contracts and concrete services share one implementation.

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderTextEventState {
    Delta,
    Final,
    Aborted,
    Error,
    ToolCallStart,
    ToolCallEnd,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderTextResponseMode {
    Full,
    AfterLastToolCall,
}

pub fn apply_provider_event_text(
    accumulated: &mut String,
    event_type: &str,
    payload: &Value,
    state: ProviderTextEventState,
    response_mode: ProviderTextResponseMode,
) {
    if event_type == "agent" {
        if response_mode == ProviderTextResponseMode::AfterLastToolCall
            && payload.get("stream").and_then(Value::as_str) == Some("tool")
        {
            accumulated.clear();
        }
        return;
    }
    if !matches!(event_type, "chat" | "chat.event") {
        return;
    }
    match state {
        ProviderTextEventState::Delta => {
            if let Some(text) = payload
                .get("delta_text")
                .or_else(|| payload.get("deltaText"))
                .and_then(Value::as_str)
                .or_else(|| provider_message_text(payload))
            {
                accumulated.push_str(text);
            }
        }
        ProviderTextEventState::Final => {
            if let Some(text) = provider_message_text(payload) {
                merge_provider_final_text(accumulated, text, response_mode);
            }
        }
        ProviderTextEventState::ToolCallStart | ProviderTextEventState::ToolCallEnd
            if response_mode == ProviderTextResponseMode::AfterLastToolCall =>
        {
            accumulated.clear();
        }
        _ => {}
    }
}

fn provider_message_text(payload: &Value) -> Option<&str> {
    payload
        .get("message")
        .and_then(|message| message.get("content"))
        .and_then(Value::as_array)
        .and_then(|content| content.first())
        .and_then(|block| block.get("text"))
        .and_then(Value::as_str)
}

fn merge_provider_final_text(accumulated: &mut String, text: &str, mode: ProviderTextResponseMode) {
    if text.is_empty() {
        return;
    }
    if accumulated.is_empty() {
        accumulated.push_str(text);
        return;
    }
    let compacted = text.replace("\n\n", "");
    match mode {
        ProviderTextResponseMode::Full
            if text.starts_with(accumulated.as_str())
                || compacted.starts_with(accumulated.as_str()) =>
        {
            accumulated.clear();
            accumulated.push_str(text);
        }
        ProviderTextResponseMode::AfterLastToolCall
            if text == accumulated.as_str()
                || text.ends_with(accumulated.as_str())
                || compacted == accumulated.as_str()
                || compacted.ends_with(accumulated.as_str()) => {}
        ProviderTextResponseMode::AfterLastToolCall => {
            if let Some(deduped) = dedupe_provider_trailing_delta(text, accumulated) {
                accumulated.clear();
                accumulated.push_str(&deduped);
            } else if text.starts_with(accumulated.as_str())
                || compacted.starts_with(accumulated.as_str())
            {
                accumulated.clear();
                accumulated.push_str(text);
            } else {
                accumulated.push_str(text);
            }
        }
        _ => accumulated.push_str(text),
    }
}

fn dedupe_provider_trailing_delta(text: &str, accumulated: &str) -> Option<String> {
    let boundaries = accumulated
        .char_indices()
        .map(|(idx, _)| idx)
        .chain(std::iter::once(accumulated.len()))
        .collect::<Vec<_>>();
    for segment_start in boundaries.iter().copied().rev().skip(1) {
        let segment_len = accumulated.len() - segment_start;
        if segment_len == 0 || segment_len * 2 > accumulated.len() {
            continue;
        }
        let previous_start = accumulated.len() - segment_len * 2;
        if !boundaries.contains(&previous_start)
            || accumulated[previous_start..segment_start] != accumulated[segment_start..]
        {
            continue;
        }
        let deduped = &accumulated[..segment_start];
        let compacted = text.replace("\n\n", "");
        if text == deduped
            || text.ends_with(deduped)
            || compacted == deduped
            || compacted.ends_with(deduped)
        {
            return Some(deduped.to_string());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn payload(text: &str) -> Value {
        json!({"message": {"content": [{"text": text}]}})
    }

    #[test]
    fn full_mode_replaces_deltas_with_final_snapshot() {
        let mut text = "前半段".to_string();
        apply_provider_event_text(
            &mut text,
            "chat.event",
            &payload("前半段后半段"),
            ProviderTextEventState::Final,
            ProviderTextResponseMode::Full,
        );
        assert_eq!(text, "前半段后半段");
    }

    #[test]
    fn after_last_tool_mode_clears_text_at_tool_boundary() {
        let mut text = "调用前".to_string();
        apply_provider_event_text(
            &mut text,
            "agent",
            &json!({"stream": "tool"}),
            ProviderTextEventState::ToolCallEnd,
            ProviderTextResponseMode::AfterLastToolCall,
        );
        assert!(text.is_empty());
    }

    #[test]
    fn trailing_duplicate_unicode_delta_is_deduplicated() {
        let mut text = "处理完成处理完成".to_string();
        apply_provider_event_text(
            &mut text,
            "chat.event",
            &payload("处理完成"),
            ProviderTextEventState::Final,
            ProviderTextResponseMode::AfterLastToolCall,
        );
        assert_eq!(text, "处理完成");
    }
}
