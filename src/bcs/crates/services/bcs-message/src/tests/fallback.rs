use super::*;

#[tokio::test]
async fn chat_session_history_interleaves_every_state_machine_panel() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;

    for (created_at, run_id) in [(2, "sm-run-1"), (4, "sm-run-2")] {
        repo.append_message(NewMessage {
            group_id: "group-1".to_string(),
            session_id: session_id.clone(),
            sender_id: bcs_domain::BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
            sender_type: SenderType::Bot,
            message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string(),
            content: serde_json::json!({
                "text": format!(
                    "<AixUI type=\"panel\" component=\"bcsPanel.StateMachineRunView\" params='{{\"runId\":\"{run_id}\"}}' />"
                ),
                "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
                "metadata": {
                    "state_machine": {
                        "event": "panel",
                        "run_id": run_id,
                        "component": "bcsPanel.StateMachineRunView",
                    }
                }
            }),
            client_msg_id: Some(format!("{run_id}:000-panel")),
            created_at,
            run_id: run_id.to_string(),
            owner_bot_id: None,
            visibility_domain: bcs_domain::MessageVisibilityDomain::StateMachine,
            audience: Some(bcs_domain::MessageAudience::FullOnly),
        })
        .await
        .expect("append state-machine panel");
    }
    repo.append_message(NewMessage {
        group_id: "group-1".to_string(),
        session_id: session_id.clone(),
        sender_id: "human-1".to_string(),
        sender_type: SenderType::Human,
        message_type: "chat".to_string(),
        content: serde_json::Value::String("ordinary message".to_string()),
        client_msg_id: None,
        created_at: 3,
        run_id: String::new(),
        owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
        audience: Some(bcs_domain::MessageAudience::Public),
    })
    .await
    .expect("append ordinary message");

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, None))
        .await
        .expect("state-machine panel history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 3);
    assert_eq!(result.messages[0].id, "sm-run-2:000-panel");
    assert_eq!(result.messages[1].content, "ordinary message");
    assert_eq!(result.messages[2].id, "sm-run-1:000-panel");
}

#[tokio::test]
async fn pre_cutoff_manager_worker_falls_back_to_legacy_history() {
    let (service, _repo, _sessions, fallback, session_id) = service_fixture(
        GroupStrategy::ManagerWorker,
        0,
        u64::MAX,
        vec![fallback_message("legacy")],
    )
    .await;

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("worker-a")))
        .await
        .expect("manager worker fallback history");

    assert_eq!(fallback.session_calls().await, 1);
    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "legacy");
}

#[tokio::test]
async fn pre_cutoff_participant_reads_only_classified_durable_history() {
    let (service, repo, sessions, fallback, session_id) = service_fixture(
        GroupStrategy::ManagerWorker,
        0,
        u64::MAX,
        vec![fallback_message("unclassified worker reply")],
    )
    .await;
    let mut human = Participant::human("human-1", ParticipantRole::Observer);
    human.message_view_scope = MessageViewScope::Participant;
    sessions
        .add_participant(&session_id, human)
        .await
        .expect("add participant Human");
    for (sender_id, content, audience, created_at) in [
        (
            "worker-a",
            "classified worker reply",
            bcs_domain::MessageAudience::FullOnly,
            2,
        ),
        (
            "mgr",
            "public manager announcement",
            bcs_domain::MessageAudience::Public,
            3,
        ),
    ] {
        repo.append_message(NewMessage {
            group_id: "group-1".to_string(),
            session_id: session_id.clone(),
            sender_id: sender_id.to_string(),
            sender_type: SenderType::Bot,
            message_type: "chat".to_string(),
            content: serde_json::Value::String(content.to_string()),
            client_msg_id: None,
            created_at,
            run_id: String::new(),
            owner_bot_id: None,
            visibility_domain: bcs_domain::MessageVisibilityDomain::ManagerWorker,
            audience: Some(audience),
        })
        .await
        .expect("append classified history");
    }

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("human-1")))
        .await
        .expect("participant history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "public manager announcement");
}

#[tokio::test]
async fn pre_cutoff_participant_without_classified_session_does_not_use_fallback() {
    let (service, _repo, sessions, fallback, session_id) = service_fixture(
        GroupStrategy::ManagerWorker,
        0,
        u64::MAX,
        vec![fallback_message("unclassified worker reply")],
    )
    .await;
    let mut human = Participant::human("human-1", ParticipantRole::Observer);
    human.message_view_scope = MessageViewScope::Participant;
    service
        .group
        .add_participant("group-1", human)
        .await
        .expect("add group participant Human");
    sessions
        .delete(&session_id)
        .await
        .expect("delete classified session");

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("human-1")))
        .await
        .expect("participant history");

    assert_eq!(fallback.session_calls().await, 0);
    assert!(result.messages.is_empty());
}

#[tokio::test]
async fn pre_cutoff_manager_worker_merges_opening_message_only_for_humans() {
    let (service, repo, _sessions, fallback, session_id) = service_fixture(
        GroupStrategy::ManagerWorker,
        0,
        u64::MAX,
        vec![fallback_message("legacy")],
    )
    .await;
    let stable_message_id = format!("{session_id}:000-opening");
    repo.append_message(NewMessage {
        group_id: "group-1".to_string(),
        session_id: session_id.clone(),
        sender_id: bcs_domain::BCS_SESSION_OPENING_MESSAGE_SENDER.to_string(),
        sender_type: SenderType::Bot,
        message_type: SESSION_OPENING_MESSAGE_TYPE.to_string(),
        content: serde_json::json!({
            "text": "任务协作群开场消息",
            "bot_name": BCS_SESSION_OPENING_MESSAGE_SENDER_NAME,
            "metadata": {
                "opening_message": {
                    "scope": "session",
                    "strategy": "manager_worker",
                }
            }
        }),
        client_msg_id: Some(stable_message_id.clone()),
        created_at: 2,
        run_id: format!("{session_id}:opening"),
        owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::ManagerWorker,
        audience: Some(bcs_domain::MessageAudience::Public),
    })
    .await
    .expect("append opening message");

    let mut human_command = session_cmd("group-1", &session_id, Some("worker-a"));
    human_command.caller = CallerContext::Human(HumanActor {
        actor_id: "human-1".to_string(),
        staff_no: "human-1".to_string(),
    });
    let human_result = service
        .get_session_history(human_command)
        .await
        .expect("human legacy history");
    assert!(
        human_result
            .messages
            .iter()
            .any(|message| message.id == stable_message_id)
    );

    let mut bot_command = session_cmd("group-1", &session_id, Some("worker-a"));
    bot_command.caller = CallerContext::Bot(BotActor {
        bot_uuid: "worker-a".to_string(),
    });
    let bot_result = service
        .get_session_history(bot_command)
        .await
        .expect("bot legacy history");
    assert!(
        bot_result
            .messages
            .iter()
            .all(|message| message.id != stable_message_id)
    );
    assert_eq!(fallback.session_calls().await, 2);
}

#[tokio::test]
async fn pre_cutoff_chat_history_merges_persisted_state_machine_panel_anchor() {
    let (service, repo, _sessions, fallback, session_id) = service_fixture(
        GroupStrategy::Chat,
        u64::MAX,
        u64::MAX,
        vec![fallback_message("legacy")],
    )
    .await;
    let run_id = "sm-old-session-run";
    repo.append_message(NewMessage {
        group_id: "group-1".to_string(),
        session_id: session_id.clone(),
        sender_id: bcs_domain::BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
        sender_type: SenderType::Bot,
        message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string(),
        content: serde_json::json!({
            "text": format!(
                "<AixUI type=\"panel\" component=\"bcsPanel.StateMachineRunView\" params='{{\"runId\":\"{run_id}\"}}' />"
            ),
            "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
            "metadata": {
                "state_machine": {
                    "event": "panel",
                    "run_id": run_id,
                    "component": "bcsPanel.StateMachineRunView",
                }
            }
        }),
        client_msg_id: Some(format!("{run_id}:000-panel")),
        created_at: 2,
        run_id: run_id.to_string(),
        owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::StateMachine,
        audience: Some(bcs_domain::MessageAudience::FullOnly),
    })
    .await
    .expect("append state-machine panel");

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, None))
        .await
        .expect("legacy history with state-machine panel");

    assert_eq!(fallback.session_calls().await, 1);
    assert_eq!(result.messages.len(), 2);
    assert_eq!(result.messages[0].id, format!("{run_id}:000-panel"));
    assert_eq!(result.messages[1].content, "legacy");
}

#[tokio::test]
async fn manager_worker_group_history_is_rejected_without_fallback() {
    let (mut service, repo, _sessions, fallback, _session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    let counting_repo = Arc::new(CountingMessageRepo::new(repo));
    service.message_repo = counting_repo.clone();

    for view_bot_id in [None, Some("worker-a")] {
        let err = service
            .get_history(group_cmd("group-1", view_bot_id))
            .await
            .expect_err("manager worker group history should be rejected");

        assert!(
            matches!(
                err,
                GroupUseCaseError::Service(ServiceError::InvalidOperation { .. })
            ),
            "expected InvalidOperation, got {err:?}"
        );
    }

    assert_eq!(counting_repo.query_calls().await, 0);
    assert_eq!(fallback.group_calls().await, 0);
    assert_eq!(fallback.session_calls().await, 0);
}

#[tokio::test]
async fn manager_worker_worker_view_filters_by_worker_owner_after_cutoff() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "human_1",
        "public-human",
        None,
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "worker-a",
        "a-only",
        Some("worker-a"),
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "worker-b",
        "b-only",
        Some("worker-b"),
    )
    .await;

    let result = service
        .get_session_history(session_cmd("group-1", &session_id, Some("worker-a")))
        .await
        .expect("manager worker db history");

    assert_eq!(fallback.session_calls().await, 0);
    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "a-only");
}

#[tokio::test]
async fn manager_worker_human_worker_view_keeps_public_opening_message_after_cutoff() {
    let (service, repo, _sessions, fallback, session_id) =
        service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
    let stable_message_id = format!("{session_id}:000-opening");
    repo.append_message(NewMessage {
        group_id: "group-1".to_string(),
        session_id: session_id.clone(),
        sender_id: bcs_domain::BCS_SESSION_OPENING_MESSAGE_SENDER.to_string(),
        sender_type: SenderType::Bot,
        message_type: SESSION_OPENING_MESSAGE_TYPE.to_string(),
        content: serde_json::json!({
            "text": "任务协作群开场消息",
            "bot_name": BCS_SESSION_OPENING_MESSAGE_SENDER_NAME,
            "metadata": {
                "opening_message": {
                    "scope": "session",
                    "strategy": "manager_worker",
                }
            }
        }),
        client_msg_id: Some(stable_message_id.clone()),
        created_at: 1,
        run_id: format!("{session_id}:opening"),
        owner_bot_id: None,
        visibility_domain: bcs_domain::MessageVisibilityDomain::ManagerWorker,
        audience: Some(bcs_domain::MessageAudience::Public),
    })
    .await
    .expect("append opening message");
    append_history(
        &repo,
        "group-1",
        &session_id,
        "worker-a",
        "a-only",
        Some("worker-a"),
    )
    .await;
    append_history(
        &repo,
        "group-1",
        &session_id,
        "worker-b",
        "b-only",
        Some("worker-b"),
    )
    .await;

    let mut command = session_cmd("group-1", &session_id, Some("worker-a"));
    command.caller = CallerContext::Human(HumanActor {
        actor_id: "human-1".to_string(),
        staff_no: "human-1".to_string(),
    });
    let result = service
        .get_session_history(command)
        .await
        .expect("manager worker human worker view history");

    assert_eq!(fallback.session_calls().await, 0);
    let message_ids = result
        .messages
        .iter()
        .map(|message| message.id.as_str())
        .collect::<Vec<_>>();
    let contents = result
        .messages
        .iter()
        .map(|message| message.content.as_str())
        .collect::<Vec<_>>();
    assert!(message_ids.contains(&stable_message_id.as_str()));
    assert!(contents.contains(&"a-only"));
    assert!(!contents.contains(&"b-only"));
}
