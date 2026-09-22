use super::*;
use crate::{MessageAudience, MessageVisibilityDomain, PersistedMessageStatus};
use serde_json::json;

fn saved(kind: &str, key: &str, event: &str, node: &str, attempt: i32) -> PersistedMessage {
    PersistedMessage {
        message_id: physical_message_id(key), client_msg_id: Some(key.into()),
        group_id: "group".into(), session_id: "session".into(), session_seq: 1,
        sender_id: "human".into(), sender_type: SenderType::Human,
        message_type: kind.into(), content: json!({"text": "same text", "metadata": {
            "state_machine": {"run_id": "workflow", "node_id": node, "attempt": attempt, "event": event}
        }}),
        owner_bot_id: None, visibility_domain: Some(MessageVisibilityDomain::StateMachine),
        audience: Some(MessageAudience::Directed { actor_ids: vec!["human".into()] }),
        status: PersistedMessageStatus::Normal, created_at: 100, run_id: "workflow".into(),
    }
}

#[test]
fn producer_ids_keep_short_keys_and_hash_utf8_by_bytes() {
    assert_eq!(physical_message_id("run:node:1:1-output"), "run:node:1:1-output");
    assert_eq!(physical_message_id(&"a".repeat(64)), "a".repeat(64));
    assert_eq!(physical_message_id(&"a".repeat(65)),
        "635361c48bb9eab14198e76ea8ab7f1a41685d6ad62aa9146d301d4f17eb0ae0");
    assert_eq!(physical_message_id(&"中".repeat(22)).len(), 64);
}

#[test]
fn projection_retains_public_ids_roles_and_workflow_metadata_without_round_grouping() {
    for (kind, event) in [
        (STATE_MACHINE_PANEL_MESSAGE_TYPE, "panel"),
        (STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE, "human_input_prompt"),
        (STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE, "human_input_response"),
        (STATE_MACHINE_OUTPUT_MESSAGE_TYPE, "output"),
    ] {
        let key = "x".repeat(90);
        let row = saved(kind, &key, event, "node", 1);
        let projected = project_state_machine_message(&row).unwrap();
        assert_eq!(projected.id, if kind == STATE_MACHINE_OUTPUT_MESSAGE_TYPE { row.message_id } else { key });
        assert_eq!(projected.role, if matches!(kind, STATE_MACHINE_PANEL_MESSAGE_TYPE | STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE) {
            MessageRole::Assistant
        } else { MessageRole::User });
        assert!(projected.run_id.is_empty());
        assert!(projected.history_meta.is_none());
        assert_eq!(projected.metadata.unwrap()["state_machine"]["run_id"], "workflow");
    }
    let published = saved("chat", "state-machine-result:workflow", "published_result", "node", 1);
    assert!(project_state_machine_message(&published).is_none());
}

#[test]
fn legacy_response_and_hashed_output_merge_by_identity_with_durable_winning() {
    let node = "long-node-".repeat(10);
    let key = output_message_key("workflow", &node, 1);
    let durable = project_state_machine_message(&saved(
        STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE, "legacy-response", "human_input_response", &node, 1,
    )).unwrap();
    let mut snapshot = project_state_machine_message(&saved(
        STATE_MACHINE_OUTPUT_MESSAGE_TYPE, &key, "output", &node, 1,
    )).unwrap();
    snapshot.id = key;
    snapshot.content = "regenerated text".into();
    let result = merge_state_machine_history(vec![durable], vec![snapshot], 10);
    assert_eq!(result.len(), 1);
    assert_eq!(result[0].id, "legacy-response");
    assert_eq!(result[0].content, "same text");
}

#[test]
fn same_text_across_nodes_attempts_and_published_results_remains_distinct() {
    let mut messages = Vec::new();
    for (node, attempt) in [("human-a", 1), ("human-b", 1), ("human-a", 2)] {
        let key = output_message_key("workflow", node, attempt);
        messages.push(project_state_machine_message(&saved(
            STATE_MACHINE_OUTPUT_MESSAGE_TYPE, &key, "output", node, attempt,
        )).unwrap());
    }
    let mut published = messages[0].clone();
    published.id = "state-machine-result:workflow".into();
    published.run_id = "chat-round".into();
    published.metadata.as_mut().unwrap()["state_machine"]["event"] = json!("published_result");
    messages.push(published);
    let result = merge_state_machine_history(messages, vec![], 10);
    assert_eq!(result.len(), 4);
    assert_eq!(result.iter().find(|m| m.id == "state-machine-result:workflow").unwrap().run_id, "chat-round");
}

#[test]
fn latest_page_is_descending_and_missing_identity_never_merges_by_text() {
    let mut messages = Vec::new();
    for timestamp in 1..=3 {
        let mut message = project_state_machine_message(&saved(
            STATE_MACHINE_OUTPUT_MESSAGE_TYPE, &format!("id-{timestamp}"), "output", "node", 1,
        )).unwrap();
        message.timestamp = timestamp;
        message.metadata = None;
        messages.push(message);
    }
    let result = merge_state_machine_history(vec![], messages, 2);
    assert_eq!(result.iter().map(|m| m.timestamp).collect::<Vec<_>>(), vec![3, 2]);
}
