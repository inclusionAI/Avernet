//! Stable identities and wire projection for StateMachine chat history.
//!
//! Callers must scope both sources to the same authorized environment/session.
//! Selecting an authoritative durable artifact and checking its audience happens
//! before this presentation-only merge; a hidden artifact must never fall back.
use std::collections::HashSet;

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::{
    BCS_STATE_MACHINE_MESSAGE_SENDER_NAME, GroupMessage, GroupMessageType, MessageRole,
    PersistedMessage, SenderType, STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE,
    STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE, STATE_MACHINE_OUTPUT_MESSAGE_TYPE,
    STATE_MACHINE_PANEL_MESSAGE_TYPE,
};

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum StateMachineHistoryEvent {
    Panel,
    HumanInputPrompt,
    Output,
}

/// Identity within one environment/session. Display text and status are not keys.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct StateMachineHistoryKey {
    pub run_id: String,
    pub node: Option<(String, u64)>,
    pub event: StateMachineHistoryEvent,
}

impl StateMachineHistoryKey {
    pub fn from_metadata(metadata: &Value) -> Option<Self> {
        let state = metadata.get("state_machine")?;
        let run_id = state.get("run_id")?.as_str().filter(|id| !id.is_empty())?;
        let event = match state.get("event")?.as_str()? {
            "panel" => StateMachineHistoryEvent::Panel,
            "human_input_prompt" => StateMachineHistoryEvent::HumanInputPrompt,
            "output" | "human_input_response" => StateMachineHistoryEvent::Output,
            _ => return None,
        };
        let node = if event == StateMachineHistoryEvent::Panel {
            None
        } else {
            Some((state.get("node_id")?.as_str().filter(|id| !id.is_empty())?.into(),
                state.get("attempt")?.as_u64()?))
        };
        Some(Self { run_id: run_id.into(), node, event })
    }
}

/// Existing producer keys remain unchanged; MySQL stores at most 64 bytes.
pub fn physical_message_id(producer_key: &str) -> String {
    if producer_key.len() <= 64 {
        producer_key.to_owned()
    } else {
        format!("{:x}", Sha256::digest(producer_key.as_bytes()))
    }
}

pub fn output_message_key(run_id: &str, node_id: &str, attempt: i32) -> String {
    format!("{run_id}:{node_id}:{attempt}:1-output")
}

/// Published `chat` results deliberately do not use the StateMachine projection.
pub fn is_state_machine_history_type(kind: &str) -> bool {
    matches!(kind, STATE_MACHINE_PANEL_MESSAGE_TYPE | STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE
        | STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE | STATE_MACHINE_OUTPUT_MESSAGE_TYPE)
}

pub fn project_state_machine_message(message: &PersistedMessage) -> Option<GroupMessage> {
    let kind = message.message_type.as_str();
    if !is_state_machine_history_type(kind) {
        return None;
    }
    let panel_or_prompt = matches!(kind, STATE_MACHINE_PANEL_MESSAGE_TYPE | STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE);
    let id = if kind == STATE_MACHINE_OUTPUT_MESSAGE_TYPE {
        message.message_id.clone()
    } else {
        message.client_msg_id.clone().unwrap_or_else(|| message.message_id.clone())
    };
    let content = message.content.get("text").and_then(Value::as_str)
        .or_else(|| message.content.as_str()).map(str::to_owned)
        .unwrap_or_else(|| message.content.to_string());
    Some(GroupMessage {
        id, timestamp: message.created_at, sender: message.sender_id.clone(), content,
        message_type: GroupMessageType::Bot,
        bot_name: message.content.get("bot_name").and_then(Value::as_str).map(str::to_owned)
            .or_else(|| panel_or_prompt.then(|| BCS_STATE_MACHINE_MESSAGE_SENDER_NAME.into())),
        role: if panel_or_prompt { MessageRole::Assistant
        } else if kind == STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE || message.sender_type == SenderType::Human {
            MessageRole::User
        } else if message.sender_type == SenderType::Bot {
            MessageRole::Assistant
        } else { MessageRole::System },
        metadata: message.content.get("metadata").cloned(),
        // Workbench treats a top-level run_id as a conversation round and merges
        // its messages. Workflow identity belongs only in state_machine metadata.
        run_id: String::new(), history_meta: None, attachments: None,
    })
}

/// Durable messages win over snapshots even when their physical IDs differ.
/// Neither a same-text message nor a separately published chat result is a duplicate.
pub fn merge_state_machine_history(
    persisted: Vec<GroupMessage>,
    snapshot: Vec<GroupMessage>,
    limit: u64,
) -> Vec<GroupMessage> {
    let mut ids = HashSet::new();
    let mut identities = HashSet::new();
    let mut messages = Vec::new();
    for mut message in persisted.into_iter().chain(snapshot) {
        let identity = message.metadata.as_ref().and_then(StateMachineHistoryKey::from_metadata);
        if ids.contains(&message.id) || identity.as_ref().is_some_and(|key| identities.contains(key)) {
            continue;
        }
        ids.insert(message.id.clone());
        if let Some(identity) = identity {
            identities.insert(identity);
            message.run_id.clear();
            message.history_meta = None;
        }
        messages.push(message);
    }
    messages.sort_by(|a, b| b.timestamp.cmp(&a.timestamp).then_with(|| b.id.cmp(&a.id)));
    messages.truncate(usize::try_from(limit).unwrap_or(usize::MAX));
    messages
}

#[cfg(test)]
#[path = "state_machine_history_tests.rs"]
mod tests;

/// Only messages accepted by the durable history writer are supplemental.
/// Legacy V2 rows without this marker retain their original window position.
pub fn is_supplemental_history(message: &PersistedMessage) -> bool {
    message.visibility_domain == Some(crate::MessageVisibilityDomain::StateMachine)
        && is_state_machine_history_type(&message.message_type)
        && message.content["metadata"]["state_machine"]["history_schema_version"].as_u64() == Some(1)
}
