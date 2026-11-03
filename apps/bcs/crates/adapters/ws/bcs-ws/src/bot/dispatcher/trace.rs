//! Bot response trace attribute capture for WS-dispatched chat events.

use bcs_protocol::ChatEventState;
use serde_json::Value;
use tracing::{Span, info};
use tracing_opentelemetry::OpenTelemetrySpanExt;

pub(super) const BOT_RESPONSE_CONTENT_LIMIT_BYTES: usize = 4096;
pub(super) const TRUNCATION_MARKER: &str = "...[TRUNCATED]...";

pub(super) fn record_ws_bot_response_trace(
    bot_id: &str,
    run_id: &str,
    event_state: &ChatEventState,
    event_payload: &Value,
) {
    let span = Span::current();
    span.set_attribute("bcn.operation", "bot.response");
    span.set_attribute("bcn.bot.id", bot_id.to_string());
    span.set_attribute("bcn.run.id", run_id.to_string());
    span.set_attribute("bcn.callback.state", format!("{event_state:?}"));
    let content = bot_response_text(event_payload);
    let (attribute_name, captured, original_size_bytes, captured_size_bytes, truncated) =
        if let Some(finish_reason) = bot_response_finish_reason(event_state) {
            let captured = bcs_telemetry::capture_gen_ai_output_messages(
                &content,
                finish_reason,
                BOT_RESPONSE_CONTENT_LIMIT_BYTES,
            );
            (
                "gen_ai.output.messages",
                captured.value,
                captured.original_size_bytes,
                captured.captured_size_bytes,
                captured.truncated,
            )
        } else {
            let (captured, truncated) = truncate_bot_response_content(&content);
            let captured_size_bytes = captured.len();
            (
                "bcn.bot.response.chunk",
                captured,
                content.len(),
                captured_size_bytes,
                truncated,
            )
        };
    span.set_attribute(attribute_name, captured);
    span.set_attribute(
        "bcn.content.original_size_bytes",
        original_size_bytes as i64,
    );
    span.set_attribute(
        "bcn.content.captured_size_bytes",
        captured_size_bytes as i64,
    );
    span.set_attribute(
        "bcn.content.limit_bytes",
        BOT_RESPONSE_CONTENT_LIMIT_BYTES as i64,
    );
    span.set_attribute("bcn.content.truncated", truncated);
    span.set_attribute("bcn.content.untrusted", false);
    info!(
        bot_id = %bot_id,
        run_id = %run_id,
        response_state = ?event_state,
        content_original_size_bytes = original_size_bytes,
        content_captured_size_bytes = captured_size_bytes,
        content_truncated = truncated,
        "Bot response trace attributes recorded"
    );
}

fn bot_response_finish_reason(event_state: &ChatEventState) -> Option<&'static str> {
    match event_state {
        ChatEventState::Final => Some("stop"),
        ChatEventState::Error => Some("error"),
        ChatEventState::Aborted => Some("aborted"),
        ChatEventState::Delta | ChatEventState::ToolCallStart | ChatEventState::ToolCallEnd => None,
    }
}

fn bot_response_text(event_payload: &Value) -> String {
    if let Some(content) = event_payload
        .pointer("/message/content")
        .and_then(Value::as_str)
    {
        return content.to_string();
    }
    let content = event_payload
        .pointer("/message/content")
        .and_then(Value::as_array)
        .map(|parts| {
            parts
                .iter()
                .filter_map(|part| part.get("text").and_then(Value::as_str))
                .collect::<String>()
        });
    match content {
        Some(content) if !content.is_empty() => content,
        _ => event_payload.to_string(),
    }
}

pub(super) fn truncate_bot_response_content(content: &str) -> (String, bool) {
    if content.len() <= BOT_RESPONSE_CONTENT_LIMIT_BYTES {
        return (content.to_string(), false);
    }
    let available = BOT_RESPONSE_CONTENT_LIMIT_BYTES - TRUNCATION_MARKER.len();
    let requested_head = available.saturating_mul(3) / 4;
    let head_end = floor_char_boundary(content, requested_head);
    let requested_tail = available - head_end;
    let tail_start = ceil_char_boundary(content, content.len().saturating_sub(requested_tail));
    (
        format!(
            "{}{}{}",
            &content[..head_end],
            TRUNCATION_MARKER,
            &content[tail_start..]
        ),
        true,
    )
}

fn floor_char_boundary(value: &str, mut index: usize) -> usize {
    index = index.min(value.len());
    while index > 0 && !value.is_char_boundary(index) {
        index -= 1;
    }
    index
}

fn ceil_char_boundary(value: &str, mut index: usize) -> usize {
    index = index.min(value.len());
    while index < value.len() && !value.is_char_boundary(index) {
        index += 1;
    }
    index
}
