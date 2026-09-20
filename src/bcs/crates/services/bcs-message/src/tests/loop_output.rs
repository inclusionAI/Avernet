use super::*;

#[tokio::test]
async fn chat_loop_outputs_preserve_content_role_identity_and_execution_metadata() {
    let (service, repo, sessions, _, session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
    sessions.add_participant(&session_id, Participant::human("human-1", ParticipantRole::Observer))
        .await.expect("add Human author");
    let metadata = serde_json::json!({"state_machine": {
        "event": "output", "run_id": "sm-loop", "node_id": "cycle__2__review",
        "execution": {"definition_node_id": "review", "loop_id": "cycle", "iteration": 2, "max_iterations": 3}
    }});
    for (sender, sender_type, text, audience) in [
        ("worker-a", SenderType::Bot, "editor result", bcs_domain::MessageAudience::FullOnly),
        ("human-1", SenderType::Human, "author input", bcs_domain::MessageAudience::Directed { actor_ids: vec!["human-1".into()] }),
    ] {
        repo.append_message_with_id(format!("stored-{sender}"), NewMessage {
            group_id: "group-1".into(), session_id: session_id.clone(), sender_id: sender.into(), sender_type,
            message_type: bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE.into(),
            content: serde_json::json!({"text": text, "metadata": metadata}),
            client_msg_id: Some(format!("legacy-client-{sender}")), created_at: 2, run_id: "sm-loop".into(),
            owner_bot_id: None, visibility_domain: bcs_domain::MessageVisibilityDomain::StateMachine,
            audience: Some(audience),
        }).await.expect("persist Loop output");
    }
    let history = service.get_session_history(session_cmd("group-1", &session_id, None))
        .await.expect("reload ordinary Chat history");
    assert_eq!(history.messages.len(), 2);
    for message in history.messages {
        assert_eq!(message.id, format!("stored-{}", message.sender));
        assert_eq!(message.run_id, "sm-loop");
        assert_eq!(message.metadata, Some(metadata.clone()));
        assert_eq!(message.bot_name, None);
        if message.sender == "human-1" {
            assert_eq!(message.content, "author input");
            assert_eq!(message.role, MessageRole::User);
        } else {
            assert_eq!(message.content, "editor result");
            assert_eq!(message.role, MessageRole::Assistant);
        }
    }
}
