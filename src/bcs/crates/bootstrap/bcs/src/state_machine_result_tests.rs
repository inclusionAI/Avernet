use super::*;
use bcs_message_store::MemoryMessageRepo;
use bcs_test_support::NoopMessageFlowService;

#[tokio::test]
async fn chat_publication_history_reuses_stable_identity_and_rejects_conflicts() {
    let messages = Arc::new(MemoryMessageRepo::new());
    let publisher = MessageFlowStateMachineResultPublisher::new(Arc::new(NoopMessageFlowService), messages.clone());
    let cmd = StateMachineResultPublishCommand { run_id: "run".into(), group_id: "group".into(), session_id: "session".into(),
        sender_bot_id: "bot".into(), content: "原始结果".into(), created_at_ms: 100 };
    // Failure after history insertion is still a failure; history presence must
    // never be treated as evidence that routing accepted the publication.
    for _ in 0..2 {
        let error = publisher.publish_state_machine_result(cmd.clone()).await.unwrap_err();
        assert!(error.to_string().contains("message flow service"));
    }
    assert_eq!(messages.get_current_seq("session").await.unwrap(), 1);
    let saved = messages.get_message_by_id("session", "state-machine-result:run").await.unwrap().unwrap();
    assert_eq!(saved.content, serde_json::json!("原始结果"));
    assert_eq!(saved.created_at, 100); assert_eq!(saved.run_id, "run");
    assert_eq!(saved.audience, Some(MessageAudience::Public));
    let mut changed = cmd; changed.content = "changed".into();
    assert!(matches!(publisher.publish_state_machine_result(changed).await.unwrap_err(), bcs_service_api::ServiceError::Conflict(_)));
    assert_eq!(messages.get_current_seq("session").await.unwrap(), 1);
}
