use super::*;
use bcs_domain::{SenderType, STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE, STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE};

#[tokio::test]
async fn durable_audience_and_body_override_mutable_human_snapshot() {
    let groups = Arc::new(GroupStore::new());
    groups.upsert(state_machine_test_group()).await.unwrap();
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let messages = Arc::new(MemoryMessageRepo::new());
    let runtime = test_runtime!(store.clone(), store.clone(), store.clone(), store,
        groups, sessions, Arc::new(RecordingDelivery::default()), noop_judge())
        .with_message_repo(messages.clone());
    let started = runtime.start_state_machine_run(StartStateMachineRunCommand {
        group_id: "group-1".into(), session_id: None, definition_yaml: Some(human_input_yaml()),
        definition: None, definition_ref: None, participant_bindings: None, opening_message_override: None,
        input: json!({}), caller_id: Some("human_1001".into()),
        authenticated_human: Some(AuthenticatedHumanCaller { actor_id: "human_1001".into(), display_name: Some("Reviewer".into()) }),
    }).await.unwrap();
    let run = &started.view.run;
    runtime.respond_human_node(RespondHumanNodeCommand {
        run_id: run.run_id.clone(), node_id: "review".into(), caller_actor_id: "human_1001".into(),
        content: "snapshot response".into(), source: HumanResponseSource::Http,
    }).await.unwrap();
    for (kind, event, suffix, text, sender) in [
        (STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE, "human_input_prompt", "human-input-prompt", "frozen prompt", "bcs_state_machine"),
        (STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE, "human_input_response", "1-output", "frozen response", "human_1001"),
    ] {
        messages.append_message(NewMessage {
            group_id: run.group_id.clone(), session_id: run.session_id.clone(), sender_id: sender.into(),
            sender_type: if sender == "human_1001" { SenderType::Human } else { SenderType::Bot },
            message_type: kind.into(), content: json!({"text": text, "metadata": {"state_machine": {
                "run_id": run.run_id, "node_id": "review", "attempt": 0, "event": event
            }}}),
            client_msg_id: Some(format!("{}:review:0:{suffix}", run.run_id)), owner_bot_id: None,
            visibility_domain: MessageVisibilityDomain::StateMachine, audience: Some(MessageAudience::FullOnly),
            created_at: run.created_at + 1, run_id: run.run_id.clone(),
        }).await.unwrap();
    }
    let participant = runtime.get_state_machine_session_history_for_view(&run.session_id, 20, None,
        HumanMessageView { actor_id: "human_1001".into(), scope: MessageViewScope::Participant, allow_legacy_unclassified_chat: false })
        .await.unwrap().unwrap();
    assert_eq!(participant.messages.len(), 1, "hidden durable prompt/response must never fall back to visible snapshots");
    assert!(participant.messages[0].id.ends_with(":000-panel"));
    let full = runtime.get_state_machine_session_history(&run.session_id, 20, None).await.unwrap().unwrap();
    assert_eq!(full.messages.len(), 2);
    assert!(full.messages.iter().all(|m| m.content != "frozen prompt"));
    for text in ["frozen response"] {
        let message = full.messages.iter().find(|message| message.content == text).unwrap();
        assert!(message.run_id.is_empty());
        assert!(message.history_meta.is_none());
    }
    assert!(full.messages.iter().all(|message| message.content != "snapshot response"));
}
