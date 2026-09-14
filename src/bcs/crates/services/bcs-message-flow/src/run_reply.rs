//! Heuristic terminal normalization. Explicit deltas concatenate verbatim;
//! completed visible segments are separated by one newline. No substring dedup.
use crate::BcsMessageFlow;
use bcs_service_api::{BotEventCommand, ServiceError, ServiceResult};

#[derive(Debug, Clone)]
pub(crate) struct RunReply {
    pub text: String,
    pub display: String,
    pub method: &'static str,
    pub raw_final: String,
    pub source_ids: Vec<String>,
}

pub(crate) fn chat_key(cmd: &BotEventCommand) -> String {
    serde_json::json!([cmd.group_id, cmd.bcs_session_id, cmd.bot_id, cmd.run_id]).to_string()
}

fn join(history: &str, current: &str) -> String {
    match (history.is_empty(), current.is_empty()) {
        (true, _) => current.into(), (_, true) => history.into(),
        _ => format!("{history}\n{current}"),
    }
}

fn normalize(history: &str, current: &str, final_text: &str) -> RunReply {
    let accumulated = join(history, current);
    let (text, display, method) = if final_text.is_empty() {
        (accumulated, current.to_owned(), "empty")
    } else if !accumulated.is_empty() && final_text.starts_with(&accumulated) {
        let remainder = final_text.strip_prefix(history).unwrap_or(final_text);
        let display = if history.is_empty() { final_text } else { remainder.strip_prefix('\n').unwrap_or(remainder) };
        (final_text.to_owned(), display.to_owned(), "full_snapshot")
    } else if !current.is_empty() && final_text.starts_with(current) {
        (join(history, final_text), final_text.to_owned(), "segment_snapshot")
    } else {
        let display = format!("{current}{final_text}");
        (join(history, &display), display, "delta")
    };
    RunReply { text, display, method, raw_final: final_text.into(), source_ids: Vec::new() }
}

pub(crate) async fn prepare(flow: &BcsMessageFlow, cmd: &BotEventCommand, final_text: &str, managed_terminal: bool) -> ServiceResult<RunReply> {
    let mut segments = Vec::new();
    let mut source_ids = Vec::new();
    if let (Some(repo), Some(session)) = (&flow.message_repo, cmd.bcs_session_id.as_deref()) {
        let rows = {
            let _timing = crate::reply_timing::Timer::new("reply.history_read");
            let read = || repo.run_chat_segments(session, &cmd.bot_id, &cmd.run_id);
            let result = if managed_terminal {
                crate::storage_retry::retry(crate::storage_retry::shutdown(flow), "terminal_history",
                    |e| matches!(e, bcs_service_api::port::repo::MessageRepoError::StorageError(_)), read).await
            } else { read().await };
            result
                .map_err(|_| ServiceError::InternalError("run text reconstruction failed".into()))?
        };
        let _timing = crate::reply_timing::Timer::new("reply.history_extract");
        for row in rows {
            // Old queue final objects have a display-only projection. Do not
            // append their cumulative routing body to already stored segments.
            let text = row.content.get("queue_display_text").and_then(|v| v.as_str())
                .or_else(|| row.content.as_str())
                .or_else(|| row.content.get("text").and_then(|v| v.as_str()));
            if let Some(text) = text.filter(|s| !s.is_empty()) { segments.push(text.to_owned()); source_ids.push(row.message_id); }
        }
    }
    let current = {
        let _timing = crate::reply_timing::Timer::new("reply.buffer_read");
        flow.message_tracker.peek_chat_buf(&chat_key(cmd)).await.unwrap_or_default()
    };
    let _timing = crate::reply_timing::Timer::new("reply.normalize");
    let mut reply = normalize(&segments.join("\n"), &current, final_text);
    reply.source_ids = source_ids;
    Ok(reply)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn three_modes_empty_and_short_repeats() {
        for (final_text, expected, mode) in [
            ("A\nBC", "A\nBC", "full_snapshot"),
            ("BC", "A\nBC", "segment_snapshot"),
            ("C", "A\nBC", "delta"),
            ("", "A\nB", "empty"),
        ] {
            let r = normalize("A", "B", final_text);
            assert_eq!(r.text, expected); assert_eq!(r.method, mode);
        }
        assert_eq!(normalize("", "好的", "好的").text, "好的");
        assert_eq!(normalize("工具前", "", "").text, "工具前");
        assert_eq!(normalize("工具前", "", "工具前").display, "");
        assert_eq!(normalize("A\nB", "C", "D").text, "A\nB\nCD");
        // Rewritten snapshots are deliberately retained, not guessed away.
        assert_eq!(normalize("A", "B", "rewrite").text, "A\nBrewrite");
    }
}
