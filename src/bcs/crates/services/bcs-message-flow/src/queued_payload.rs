//! Recover Bot-bound content exclusively from canonical messages. This is not
//! the public history projection: signed URLs must never be broadcast as part
//! of a delivery status or logged with a prepared payload.
use bcs_domain::message_delivery::{DeliveryContextSelection, SelectedDeliveryContext, MessageDeliveryStatus, PersistedMessageDelivery};
use bcs_domain::{Attachment, AttachmentType, DeliveryType};
use bcs_service_api::port::repo::MessageRepoPort;
use std::collections::BTreeSet;

pub struct QueuedPayload {
    pub text: String,
    pub attachments: Vec<Attachment>,
    pub state_version: u64,
    pub selection: DeliveryContextSelection,
}

const HISTORY_HEADER: &str = "历史上下文（以下消息不要求单独回复）：\n";
const OMITTED: &str = "部分较早历史因上下文限制已省略。\n";
const TRUNCATED: &str = "[该条历史前部已省略]\n";

pub async fn read_queued_payload(
    repo: &dyn MessageRepoPort,
    carrier: &PersistedMessageDelivery,
    contexts: &[PersistedMessageDelivery],
) -> Result<QueuedPayload, String> {
    read_queued_payload_with_projection(repo, carrier, contexts, |_, text| Ok(text.to_string()))
        .await
}

/// Apply the routing intent captured at admission, without resolving mentions
/// again against a group whose membership may have changed while waiting.
pub async fn read_queued_payload_with_projection(
    repo: &dyn MessageRepoPort,
    carrier: &PersistedMessageDelivery,
    contexts: &[PersistedMessageDelivery],
    project: impl Fn(&PersistedMessageDelivery, &str) -> Result<String, String> + Send + Sync,
) -> Result<QueuedPayload, String> {
    read_bounded_queued_payload(repo, carrier, contexts, contexts.len() as u64, 24, 131072, project).await
}

/// Only the newest bounded metadata page is supplied. Canonical bodies are
/// loaded one at a time until the byte/count budget is exhausted. One original
/// body can itself exceed the budget: this is not a database row memory cap.
pub async fn read_bounded_queued_payload(
    repo: &dyn MessageRepoPort,
    carrier: &PersistedMessageDelivery,
    contexts: &[PersistedMessageDelivery],
    total: u64,
    max_messages: u32,
    max_bytes: u64,
    project: impl Fn(&PersistedMessageDelivery, &str) -> Result<String, String> + Send + Sync,
) -> Result<QueuedPayload, String> {
    if carrier.state.kind != DeliveryType::Send
        || carrier.state.status != MessageDeliveryStatus::Queued
    {
        return Err("queued carrier required".into());
    }
    let mut contexts: Vec<_> = contexts.iter().collect();
    contexts.sort_by_key(|d| std::cmp::Reverse(d.source_session_seq));
    let mut seen = BTreeSet::new();
    for context in &contexts {
        if !seen.insert(&context.delivery_id)
            || context.state.kind != DeliveryType::Inject
            || context.state.status != MessageDeliveryStatus::Bound
            || context.bound_to_delivery_id.as_deref() != Some(carrier.delivery_id.as_str())
            || context.env != carrier.env
            || context.session_id != carrier.session_id
            || context.target_bot_id != carrier.target_bot_id
            || context.source_session_seq >= carrier.source_session_seq
        {
            return Err("invalid bound context".into());
        }
    }
    let saved: Option<DeliveryContextSelection> = carrier.context_selection_json.clone().map(serde_json::from_value).transpose().map_err(|_| "invalid context selection")?;
    let (max_messages, max_bytes) = saved.as_ref().map_or((max_messages, max_bytes), |s| (s.max_messages, s.max_bytes));
    let budget = usize::try_from(max_bytes).map_err(|_| "invalid context budget")?;
    if max_messages == 0 || budget < 512 || total < contexts.len() as u64 { return Err("invalid context limits or count".into()); }
    if let Some(s) = &saved {
        if s.version != 1 || s.bound_count != total || s.selected.len() > max_messages as usize { return Err("stale context selection".into()); }
    }
    let mut selection = DeliveryContextSelection { version: 1, max_messages, max_bytes, bound_count: total, history_bytes: 0, selected: Vec::new() };
    let mut blocks = Vec::new();
    let mut history_size = HISTORY_HEADER.len();
    for row in contexts.into_iter().take(max_messages as usize) {
        let previous = saved.as_ref().and_then(|s| s.selected.iter().find(|s| s.delivery_id == row.delivery_id));
        if saved.is_some() && previous.is_none() { break; }
        if previous.is_some_and(|s| s.state_version != row.state.state_version) { return Err("stale context version".into()); }
        let message = repo.get_message_by_id(&carrier.session_id, &row.source_message_id).await.map_err(|_| "canonical message read failed")?.ok_or("canonical message missing")?;
        if message.group_id != row.group_id || message.session_seq != row.source_session_seq {
            return Err("canonical message scope or sequence mismatch".into());
        }
        let body = message.content.get("text").and_then(serde_json::Value::as_str).ok_or("canonical message text is invalid")?;
        let body = project(row, body)?;
        let prefix = format!("\n[from:{}; seq:{}]\n", message.sender_id, message.session_seq);
        let omitted = total > selection.selected.len() as u64 + 1;
        let mut start = previous.map_or(0, |s| s.body_start);
        if !body.is_char_boundary(start) { return Err("invalid context UTF-8 offset".into()); }
        // First try complete history without an omission marker: reserving a
        // marker prematurely can truncate history that would fit in full.
        let overhead = history_size + prefix.len() + 1;
        if saved.is_none() && overhead.saturating_add(body.len()) > budget {
            if !selection.selected.is_empty() { break; }
            let truncated_overhead = overhead + TRUNCATED.len() + if omitted { OMITTED.len() } else { 0 };
            let room = budget.saturating_sub(truncated_overhead);
            if truncated_overhead > budget { break; }
            start = body.char_indices().rev().take_while(|(i,_)| body.len() - i <= room).last().map_or(body.len(), |(i,_)| i);
        }
        let block = format!("{}{}{}\n", prefix, if start > 0 { TRUNCATED } else { "" }, &body[start..]);
        if history_size + block.len() > budget { return Err("context selection exceeds budget".into()); }
        let original_attachments: Vec<Attachment> = message.content.get("attachments").map(|v| serde_json::from_value(v.clone())).transpose().map_err(|_| "canonical attachments are invalid")?.unwrap_or_default();
        let attachments: Vec<_> = original_attachments.into_iter().filter(|a| a.attachment_type != AttachmentType::File).collect();
        history_size += block.len();
        selection.selected.push(SelectedDeliveryContext { delivery_id: row.delivery_id.clone(), state_version: row.state.state_version, body_start: start });
        blocks.push((block, attachments, prefix.len()));
        if start > 0 { break; }
    }
    if saved.is_none() && total > selection.selected.len() as u64 {
        // Once omission is known, its marker is also part of the budget. Drop
        // oldest selected whole messages first, never fill a gap with older ones.
        while blocks.len() > 1 && history_size + OMITTED.len() > budget {
            history_size -= blocks.pop().expect("nonempty blocks").0.len();
            selection.selected.pop();
        }
        if history_size + OMITTED.len() > budget && !blocks.is_empty() {
            let (block, _, prefix_len) = &mut blocks[0];
            let overhead = HISTORY_HEADER.len() + OMITTED.len() + *prefix_len + TRUNCATED.len() + 1;
            if overhead > budget {
                blocks.clear(); selection.selected.clear();
            } else {
                let body = &block[*prefix_len..block.len()-1];
                let room = budget - overhead;
                let start = body.char_indices().rev().take_while(|(i,_)| body.len()-i <= room).last().map_or(body.len(), |(i,_)| i);
                let replacement = format!("{}{}{}\n", &block[..*prefix_len], TRUNCATED, &body[start..]);
                selection.selected[0].body_start = start;
                *block = replacement;
            }
        }
    }
    let mut text = String::new();
    let mut attachments = Vec::new();
    if total > 0 {
        text.push_str(HISTORY_HEADER);
        if total > selection.selected.len() as u64 { text.push_str(OMITTED); }
        for (block, files, _) in blocks.into_iter().rev() { text.push_str(&block); attachments.extend(files); }
    }
    selection.history_bytes = text.len() as u64;
    if selection.history_bytes > max_bytes { return Err("context wrapper exceeds budget".into()); }
    if saved.as_ref().is_some_and(|s| s != &selection) { return Err("context selection changed".into()); }
    let source = repo.get_message_by_id(&carrier.session_id, &carrier.source_message_id).await.map_err(|_| "canonical source read failed")?.ok_or("canonical source missing")?;
    if source.group_id != carrier.group_id || source.session_seq != carrier.source_session_seq { return Err("canonical source scope mismatch".into()); }
    let body = project(carrier, source.content.get("text").and_then(serde_json::Value::as_str).ok_or("canonical source text invalid")?)?;
    if !text.is_empty() { text.push_str("\n当前请求：\n"); }
    text.push_str(&body);
    let files: Vec<Attachment> = source.content.get("attachments").map(|v| serde_json::from_value(v.clone())).transpose().map_err(|_| "canonical attachments are invalid")?.unwrap_or_default();
    attachments.extend(files);
    Ok(QueuedPayload {
        text,
        attachments,
        state_version: carrier.state.state_version,
        selection,
    })
}
