use bcs_service_api::ChatResponseMode;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DrainOutcome {
    Continue,
    Final,
    Error(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DetachDeliveryCallback {
    Success,
    Error(String),
    Ignored,
}

pub fn drain_chat_event(event_str: &str, accumulated: &mut String) -> DrainOutcome {
    drain_chat_event_with_mode(event_str, accumulated, ChatResponseMode::Full)
}

pub fn classify_detach_delivery_callback(event_str: &str) -> DetachDeliveryCallback {
    let frame = match serde_json::from_str::<bcs_protocol::BcsFrame>(event_str) {
        Ok(frame) => frame,
        Err(_) => return DetachDeliveryCallback::Ignored,
    };

    let event = match frame {
        bcs_protocol::BcsFrame::Event(event) => event,
        _ => return DetachDeliveryCallback::Ignored,
    };

    match event.event.as_str() {
        "chat.event" => {
            let state = event
                .payload
                .as_ref()
                .and_then(|payload| payload.get("state"))
                .and_then(|state| state.as_str())
                .unwrap_or("");
            match state {
                "delivered" | "accepted" | "submitted" | "running" | "final" => {
                    DetachDeliveryCallback::Success
                }
                "error" | "aborted" => DetachDeliveryCallback::Error(
                    chat_event_text(event.payload.as_ref())
                        .unwrap_or("Unknown error")
                        .to_string(),
                ),
                _ => DetachDeliveryCallback::Ignored,
            }
        }
        "error" => {
            let error = event
                .payload
                .as_ref()
                .and_then(|payload| payload.get("message"))
                .and_then(|message| message.as_str())
                .unwrap_or("Unknown error")
                .to_string();
            DetachDeliveryCallback::Error(error)
        }
        _ => DetachDeliveryCallback::Ignored,
    }
}

pub fn drain_chat_event_with_mode(
    event_str: &str,
    accumulated: &mut String,
    response_mode: ChatResponseMode,
) -> DrainOutcome {
    let frame = match serde_json::from_str::<bcs_protocol::BcsFrame>(event_str) {
        Ok(frame) => frame,
        Err(_) => return DrainOutcome::Continue,
    };

    let event = match frame {
        bcs_protocol::BcsFrame::Event(event) => event,
        _ => return DrainOutcome::Continue,
    };

    match event.event.as_str() {
        "chat.event" => {
            let state = event
                .payload
                .as_ref()
                .and_then(|payload| payload.get("state"))
                .and_then(|state| state.as_str())
                .unwrap_or("");

            match state {
                "delta" => {
                    if let Some(text) = chat_event_text(event.payload.as_ref()) {
                        accumulated.push_str(text);
                    }
                    DrainOutcome::Continue
                }
                "final" => {
                    if let Some(text) = chat_event_text(event.payload.as_ref()) {
                        merge_final_text(accumulated, text, response_mode);
                    }
                    DrainOutcome::Final
                }
                "error" | "aborted" => {
                    let error = chat_event_text(event.payload.as_ref())
                        .unwrap_or("Unknown error")
                        .to_string();
                    DrainOutcome::Error(error)
                }
                "tool_call_start" | "tool_call_end" => {
                    if response_mode == ChatResponseMode::AfterLastToolCall {
                        accumulated.clear();
                    }
                    DrainOutcome::Continue
                }
                _ => DrainOutcome::Continue,
            }
        }
        "agent" => {
            let stream = event
                .payload
                .as_ref()
                .and_then(|payload| payload.get("stream"))
                .and_then(|stream| stream.as_str())
                .unwrap_or("");
            if response_mode == ChatResponseMode::AfterLastToolCall && stream == "tool" {
                accumulated.clear();
            }
            DrainOutcome::Continue
        }
        "chat.response" | "response" => {
            if let Some(text) = event
                .payload
                .as_ref()
                .and_then(|payload| payload.get("text"))
                .and_then(|text| text.as_str())
            {
                accumulated.push_str(text);
            } else if let Some(content) = event
                .payload
                .as_ref()
                .and_then(|payload| payload.get("content"))
                .and_then(|content| content.as_str())
            {
                accumulated.push_str(content);
            } else if let Some(payload) = event.payload {
                accumulated.push_str(&payload.to_string());
            }
            DrainOutcome::Continue
        }
        "chat.complete" | "complete" => DrainOutcome::Final,
        "error" => {
            let error = event
                .payload
                .as_ref()
                .and_then(|payload| payload.get("message"))
                .and_then(|message| message.as_str())
                .unwrap_or("Unknown error")
                .to_string();
            DrainOutcome::Error(error)
        }
        _ => {
            if let Some(text) = event
                .payload
                .as_ref()
                .and_then(|payload| payload.get("text"))
                .and_then(|text| text.as_str())
            {
                accumulated.push_str(text);
            }
            DrainOutcome::Continue
        }
    }
}

fn chat_event_text(payload: Option<&serde_json::Value>) -> Option<&str> {
    payload
        .and_then(|payload| payload.get("message"))
        .and_then(|message| message.get("content"))
        .and_then(|content| content.as_array())
        .and_then(|content| content.first())
        .and_then(|block| block.get("text"))
        .and_then(|text| text.as_str())
}

fn merge_final_text(accumulated: &mut String, text: &str, response_mode: ChatResponseMode) {
    if text.is_empty() {
        return;
    }
    if accumulated.is_empty() {
        accumulated.push_str(text);
        return;
    }

    match response_mode {
        ChatResponseMode::Full => {
            if final_snapshot_starts_with(text, accumulated.as_str()) {
                accumulated.clear();
                accumulated.push_str(text);
            } else {
                accumulated.push_str(text);
            }
        }
        ChatResponseMode::AfterLastToolCall => {
            if final_snapshot_ends_with(text, accumulated.as_str()) {
                return;
            }
            if let Some(deduped) =
                dedupe_repeated_trailing_delta(text, accumulated.as_str())
            {
                accumulated.clear();
                accumulated.push_str(&deduped);
                return;
            }
            if final_snapshot_starts_with(text, accumulated.as_str()) {
                accumulated.clear();
                accumulated.push_str(text);
            } else {
                accumulated.push_str(text);
            }
        }
    }
}

fn final_snapshot_starts_with(text: &str, accumulated: &str) -> bool {
    if text.starts_with(accumulated) {
        return true;
    }
    text.replace("\n\n", "").starts_with(accumulated)
}

fn final_snapshot_ends_with(text: &str, accumulated: &str) -> bool {
    if text == accumulated || text.ends_with(accumulated) {
        return true;
    }
    let compacted = text.replace("\n\n", "");
    compacted == accumulated || compacted.ends_with(accumulated)
}

fn dedupe_repeated_trailing_delta(text: &str, accumulated: &str) -> Option<String> {
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
        if !boundaries.contains(&previous_start) {
            continue;
        }
        let repeated = &accumulated[previous_start..segment_start];
        let trailing = &accumulated[segment_start..];
        if repeated != trailing {
            continue;
        }
        let deduped = &accumulated[..segment_start];
        if final_snapshot_ends_with(text, deduped) {
            return Some(deduped.to_string());
        }
    }
    None
}
