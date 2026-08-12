use bcs_service_api::{ChatEventState, ChatResponseMode, apply_provider_event_text};

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
                "delta" | "tool_call_start" | "tool_call_end" | "final" => {
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
        "agent" => DetachDeliveryCallback::Success,
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
                    if let Some(payload) = event.payload.as_ref() {
                        apply_provider_event_text(accumulated, "chat.event", payload,
                            &ChatEventState::Delta, response_mode);
                    }
                    DrainOutcome::Continue
                }
                "final" => {
                    if let Some(payload) = event.payload.as_ref() {
                        apply_provider_event_text(accumulated, "chat.event", payload,
                            &ChatEventState::Final, response_mode);
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
                    if let Some(payload) = event.payload.as_ref() {
                        let state = if state == "tool_call_start" {
                            ChatEventState::ToolCallStart
                        } else {
                            ChatEventState::ToolCallEnd
                        };
                        apply_provider_event_text(accumulated, "chat.event", payload,
                            &state, response_mode);
                    }
                    DrainOutcome::Continue
                }
                _ => DrainOutcome::Continue,
            }
        }
        "agent" => {
            if let Some(payload) = event.payload.as_ref() {
                apply_provider_event_text(accumulated, "agent", payload,
                    &ChatEventState::ToolCallEnd, response_mode);
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
