use super::{
    Arc,
    AuthenticatedHumanCaller,
    BlockingJudge,
    CollaborationRuntime,
    CollaborationRuntimeError,
    CollaborationRuntimeService,
    GroupCoreService,
    GroupMessageType,
    GroupStore,
    HandleSessionHumanInputCommand,
    HandleSessionHumanInputOutcome,
    HumanMessageView,
    HumanResponseSource,
    HumanRunAccessCommand,
    JudgeDecision,
    ListPendingHumanNodesCommand,
    MemoryCollaborationStore,
    MemoryGroupRepo,
    MemoryMessageRepo,
    MemorySessionRepo,
    MessageAudience,
    MessageRole,
    MessageViewScope,
    MessageVisibilityDomain,
    Participant,
    ParticipantRole,
    RecordingDelivery,
    RecordingFrontendDelivery,
    RecordingJudge,
    RecordingSessionChannelOutbound,
    RespondHumanNodeCommand,
    RespondHumanNodeOutcome,
    SequencedJudge,
    SessionManagementService,
    SessionManagementServiceImpl,
    StartStateMachineRunCommand,
    StateMachineNodeStatus,
    StateMachineNodeSubStatus,
    StateMachineRunAccessCommand,
    StateMachineRunRepoPort,
    StateMachineRunStatus,
    human_input_yaml,
    json,
    judged_human_input_yaml,
    noop_judge,
    single_node_yaml,
    state_machine_test_group,
    test_group,
    test_sessions
};

#[tokio::test]
async fn human_input_waits_without_bot_delivery_and_completes_from_natural_language() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let session_repo = Arc::new(MemorySessionRepo::new());
    let sessions = Arc::new(SessionManagementServiceImpl::new(session_repo.clone(), Arc::new(MemoryGroupRepo::new())));
    let store = Arc::new(MemoryCollaborationStore::new().with_session_repo(session_repo));
    let delivery = Arc::new(RecordingDelivery::default());
    let channel_outbound = Arc::new(RecordingSessionChannelOutbound::default());
    let frontend_delivery = Arc::new(RecordingFrontendDelivery::default());
    let message_repo = Arc::new(MemoryMessageRepo::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions.clone(),
        delivery.clone(),
        noop_judge(),
    )
    .with_session_channel_outbound(channel_outbound.clone())
    .with_frontend_delivery(frontend_delivery.clone())
    .with_message_repo(message_repo.clone());

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
                                               caller_association: None,
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start human run");

    sessions
        .add_participant(
            &started.view.run.session_id,
            Participant::human("human_2002", ParticipantRole::Observer),
        )
        .await
        .expect("add a Human after prompt activation");
    sessions
        .update_participant_message_view_scope(
            &started.view.run.session_id,
            "human_1001",
            MessageViewScope::Participant,
        )
        .await
        .expect("participant scope is a message projection preference");

    let unauthenticated_read = runtime
        .get_state_machine_run_with_access(StateMachineRunAccessCommand {
            run_id: started.view.run.run_id.clone(),
            authenticated_human: None,
        })
        .await
        .expect_err("HumanInput run must reject unauthenticated reads");
    assert!(matches!(
        unauthenticated_read,
        CollaborationRuntimeError::Unauthenticated
    ));

    assert!(delivery.commands.lock().await.is_empty());
    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );
    assert!(started.view.nodes[0].assignee_bot_id.is_none());
    assert!(started.view.nodes[0].delivery_request_id.is_none());
    let frontend_commands = frontend_delivery.commands.lock().await;
    assert!(
        frontend_commands
            .iter()
            .all(|command| !matches!(command.audience, Some(MessageAudience::Directed { .. }))),
        "HumanInput must remain available through StateMachine APIs without adding a new main-stream event for full viewers",
    );
    assert!(frontend_commands.iter().any(|command| {
        command.visibility_domain == MessageVisibilityDomain::StateMachine
            && command.audience == Some(MessageAudience::Public)
            && command.event_json.contains("bcsPanel.StateMachineRunView")
    }));
    drop(frontend_commands);
    let channel_events = channel_outbound.events.lock().await;
    assert_eq!(channel_events.len(), 1);
    assert_eq!(channel_events[0].run_id, started.view.run.run_id);
    assert_eq!(channel_events[0].node_id, "review");
    assert_eq!(
        channel_events[0].response_ref,
        format!("{}/review", started.view.run.run_id)
    );
    assert_eq!(channel_events[0].display_name, "Review");
    assert_eq!(channel_events[0].instruction, "请用自然语言给出你的意见。");
    assert!(channel_events[0].upstream_artifacts.is_empty());
    drop(channel_events);

    let human_access = HumanRunAccessCommand {
        run_id: started.view.run.run_id.clone(),
        caller_actor_id: "human_1001".to_string(),
    };
    assert!(
        runtime
            .get_state_machine_run_for_human(human_access.clone())
            .await
            .expect("Human reads run")
            .is_some()
    );
    assert!(
        runtime
            .get_state_machine_node_run_for_human(human_access.clone(), "review")
            .await
            .expect("Human reads node")
            .is_some()
    );
    assert!(
        runtime
            .get_state_machine_run_graph_for_human(human_access.clone())
            .await
            .expect("Human reads graph")
            .is_some()
    );
    let authenticated_access = StateMachineRunAccessCommand {
        run_id: started.view.run.run_id.clone(),
        authenticated_human: Some(AuthenticatedHumanCaller {
            actor_id: "human_1001".to_string(),
            display_name: Some("Reviewer".to_string()),
        }),
    };
    assert!(
        runtime
            .get_state_machine_node_run_with_access(authenticated_access.clone(), "review")
            .await
            .expect("authenticated Human reads node")
            .is_some()
    );
    assert!(
        runtime
            .get_state_machine_run_graph_with_access(authenticated_access)
            .await
            .expect("authenticated Human reads graph")
            .is_some()
    );
    let pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(),
            caller_actor_id: "human_1001".to_string(),
        })
        .await
        .expect("list pending human nodes");
    assert_eq!(pending.len(), 1);
    assert_eq!(
        pending[0].response_ref,
        format!("{}/review", started.view.run.run_id)
    );
    let forbidden = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: started.view.run.run_id.clone(),
            caller_actor_id: "human_2002".to_string(),
        })
        .await
        .expect_err("non-participant must not inspect pending Human nodes");
    assert!(matches!(forbidden, CollaborationRuntimeError::Forbidden(_)));

    for content in ["   ".to_string(), "x".repeat(64 * 1024 + 1)] {
        let invalid = runtime
            .respond_human_node(RespondHumanNodeCommand {
                run_id: started.view.run.run_id.clone(),
                node_id: "review".to_string(),
                caller_actor_id: "human_1001".to_string(),
                content,
                source: HumanResponseSource::Http,
            })
            .await
            .expect_err("invalid Human response must be rejected");
        assert!(matches!(
            invalid,
            CollaborationRuntimeError::InvalidRequest(_)
        ));
    }

    let completed = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: "group-1".to_string(),
            session_id: Some(started.view.run.session_id.clone()),
            caller_actor_id: "human_1001".to_string(),
            content: "这个方案可以发布".to_string(),
            source: HumanResponseSource::Http,
            caller_ingress: None,
        })
        .await
        .expect("route session message to HumanInput");
    let HandleSessionHumanInputOutcome::Consumed {
        response: completed,
    } = completed
    else {
        panic!("state-machine session message should be consumed")
    };
    let RespondHumanNodeOutcome { node, run } = completed;
    assert_eq!(node.status, StateMachineNodeStatus::Completed);
    assert_eq!(node.outcome.as_deref(), Some("complete"));
    assert_eq!(node.responded_by.as_deref(), Some("human_1001"));
    assert_eq!(node.artifact_text.as_deref(), Some("这个方案可以发布"));
    assert_eq!(run.status, StateMachineRunStatus::Completed);

    let history = runtime
        .get_state_machine_session_history(&run.session_id, 20, None)
        .await
        .expect("human history")
        .expect("history result");
    assert!(history.messages.iter().all(|message| {
        message.sender != "bcs_state_machine" || message.content != "请用自然语言给出你的意见。"
    }));
    let human_message = history
        .messages
        .iter()
        .find(|message| message.sender == "human_1001")
        .expect("human history message");
    assert_eq!(human_message.message_type, GroupMessageType::Bot);
    let participant_history = runtime
        .get_state_machine_session_history_for_view(
            &run.session_id,
            20,
            None,
            HumanMessageView {
                actor_id: "human_1001".to_string(),
                scope: MessageViewScope::Participant,
                allow_legacy_unclassified_chat: false,
            },
        )
        .await
        .expect("assigned participant history")
        .expect("assigned participant history result");
    assert!(
        participant_history
            .messages
            .iter()
            .any(|message| message.id.ends_with(":000-panel")),
        "participant history keeps the public panel control message",
    );
    assert!(participant_history.messages.iter().any(|message| {
        message.sender == "bcs_state_machine" && message.content == "请用自然语言给出你的意见。"
    }));
    assert!(
        participant_history
            .messages
            .iter()
            .any(|message| message.sender == "human_1001")
    );
    let other_participant_history = runtime
        .get_state_machine_session_history_for_view(
            &run.session_id,
            20,
            None,
            HumanMessageView {
                actor_id: "human_2002".to_string(),
                scope: MessageViewScope::Participant,
                allow_legacy_unclassified_chat: false,
            },
        )
        .await
        .expect("other participant history")
        .expect("other participant history result");
    assert!(other_participant_history.messages.iter().all(|message| {
        message.sender != "human_1001" && message.content != "请用自然语言给出你的意见。"
    }));
    assert_eq!(human_message.role, MessageRole::User);
    assert_eq!(human_message.bot_name.as_deref(), Some("Reviewer"));
    let no_longer_pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: run.run_id,
            caller_actor_id: "human_1001".to_string(),
        })
        .await
        .expect("completed Human run has no pending nodes");
    assert!(no_longer_pending.is_empty());
}

#[tokio::test]
async fn state_machine_session_rejects_message_without_pending_human_input() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let delivery = Arc::new(RecordingDelivery::default());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        delivery.clone(),
        noop_judge(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(single_node_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"question": "wait for the bot"}),
                                                        caller_association: None,
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start bot run");

    let error = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: "group-1".to_string(),
            session_id: Some(started.view.run.session_id),
            caller_actor_id: "human_1001".to_string(),
            content: "这条消息不能发送给 bot".to_string(),
            source: HumanResponseSource::Http,
            caller_ingress: None,
        })
        .await
        .expect_err("state-machine chat must reject input while no HumanInput is pending");
    assert!(matches!(error, CollaborationRuntimeError::Conflict(_)));
    let non_human_node = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "answer".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "不能直接响应 bot 节点".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("Human response endpoint must reject bot_task nodes");
    assert!(matches!(
        non_human_node,
        CollaborationRuntimeError::InvalidRequest(_)
    ));
    assert_eq!(delivery.commands.lock().await.len(), 1);
}

#[tokio::test]
async fn human_runtime_rejects_missing_context_and_invalid_response_targets() {
    let group = Arc::new(GroupStore::new());
    group
        .upsert(state_machine_test_group())
        .await
        .expect("seed state-machine group");
    let mut second_state_machine_group = state_machine_test_group();
    second_state_machine_group.id = "group-2".to_string();
    group
        .upsert(second_state_machine_group)
        .await
        .expect("seed second state-machine group");
    let mut regular_group = test_group();
    regular_group.id = "group-regular".to_string();
    group
        .upsert(regular_group)
        .await
        .expect("seed regular group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );

    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "validate errors"}),
                                                       caller_association: None,
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start Human run");

    for (group_id, session_id, expected_fragment) in [
        ("missing-group", None, "group not found"),
        (
            "group-1",
            Some("missing-session".to_string()),
            "session not found",
        ),
    ] {
        let error = runtime
            .start_state_machine_run(StartStateMachineRunCommand {
                group_id: group_id.to_string(),
                session_id,
                definition_yaml: Some(human_input_yaml()),
                definition: None,
                definition_ref: None,
                participant_bindings: None,
                opening_message_override: None,
                input: json!({"proposal": "invalid start"}),
                caller_id: Some("human_1001".to_string()),
                authenticated_human: Some(AuthenticatedHumanCaller {
                    actor_id: "human_1001".to_string(),
                    display_name: Some("Reviewer".to_string()),
                }),
                caller_association: None,
            })
            .await
            .expect_err("invalid run context must reject start");
        assert!(error.to_string().contains(expected_fragment));
    }

    let not_state_machine = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: "group-regular".to_string(),
            session_id: None,
            caller_actor_id: "human_1001".to_string(),
            content: "regular chat".to_string(),
            source: HumanResponseSource::Http,
            caller_ingress: None,
        })
        .await
        .expect("regular group remains outside state-machine input routing");
    assert!(matches!(
        not_state_machine,
        HandleSessionHumanInputOutcome::NotStateMachine
    ));

    for (session_id, expected_fragment) in [
        (None, "require a session id"),
        (Some("missing-session".to_string()), "has no active run"),
        (
            Some(started.view.run.session_id.clone()),
            "does not belong to the target group",
        ),
    ] {
        let group_id = if expected_fragment.contains("target group") {
            "group-2"
        } else {
            "group-1"
        };
        let error = runtime
            .handle_session_human_input(HandleSessionHumanInputCommand {
                group_id: group_id.to_string(),
                session_id,
                caller_actor_id: "human_1001".to_string(),
                content: "invalid context".to_string(),
                source: HumanResponseSource::Http,
                caller_ingress: None,
            })
            .await
            .expect_err("invalid state-machine input context must be rejected");
        assert!(error.to_string().contains(expected_fragment));
    }

    let missing_run = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: "missing-run".to_string(),
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "approve".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("missing run must be rejected");
    assert!(matches!(
        missing_run,
        CollaborationRuntimeError::RunNotFound(_)
    ));

    let missing_node = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id.clone(),
            node_id: "missing-node".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "approve".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("missing Human node must be rejected");
    assert!(matches!(
        missing_node,
        CollaborationRuntimeError::NodeNotFound { .. }
    ));

    let missing_access = HumanRunAccessCommand {
        run_id: "missing-run".to_string(),
        caller_actor_id: "human_1001".to_string(),
    };
    assert!(
        runtime
            .get_state_machine_run_for_human(missing_access.clone())
            .await
            .expect("missing run lookup")
            .is_none()
    );
    assert!(
        runtime
            .get_state_machine_node_run_for_human(missing_access.clone(), "review")
            .await
            .expect("missing node run lookup")
            .is_none()
    );
    assert!(
        runtime
            .get_state_machine_run_graph_for_human(missing_access)
            .await
            .expect("missing graph lookup")
            .is_none()
    );

    runtime
        .cancel_state_machine_run_for_human(
            HumanRunAccessCommand {
                run_id: started.view.run.run_id.clone(),
                caller_actor_id: "human_1001".to_string(),
            },
            None,
        )
        .await
        .expect("cancel Human run");
    let late_response = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id,
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "too late".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("aborted run must reject Human responses");
    assert!(matches!(
        late_response,
        CollaborationRuntimeError::Conflict(_)
    ));
}

#[tokio::test]
async fn human_input_uses_the_same_judge_contract_as_bot_output() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let judge = Arc::new(SequencedJudge::new(vec![JudgeDecision {
        outcome: "approved".to_string(),
        reason: "approval".to_string(),
        confidence: 0.4,
        checked_criteria: Vec::new(),
        retry_instruction: "unused by the regular judge contract".to_string(),
        raw_response: None,
    }]));
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        judge.clone(),
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judged_human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
                                               caller_association: None,
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start judged Human run");

    let completed = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id.clone(),
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "看起来还行".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect("Judge outcome should complete the Human node");
    let RespondHumanNodeOutcome { node, run } = completed;
    assert_eq!(node.outcome.as_deref(), Some("approved"));
    assert_eq!(node.artifact_text.as_deref(), Some("看起来还行"));
    assert_eq!(run.status, StateMachineRunStatus::Completed);
    assert_eq!(judge.requests.lock().await.len(), 1);
}

#[tokio::test]
async fn human_input_is_persisted_and_no_longer_pending_while_judging() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let judge = Arc::new(BlockingJudge::new("approved"));
    let runtime = Arc::new(test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        judge.clone(),
    ));
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judged_human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
                                               caller_association: None,
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }),
        })
        .await
        .expect("start judged Human run");
    let run_id = started.view.run.run_id.clone();

    let response_task = tokio::spawn({
        let runtime = runtime.clone();
        let run_id = run_id.clone();
        async move {
            runtime
                .respond_human_node(RespondHumanNodeCommand {
                    run_id,
                    node_id: "review".to_string(),
                    caller_actor_id: "human_1001".to_string(),
                    content: "看起来还行".to_string(),
                    source: HumanResponseSource::Http,
                })
                .await
        }
    });
    judge.started.notified().await;

    let judging = runtime
        .get_state_machine_node_run(&run_id, "review")
        .await
        .expect("load judging Human node")
        .expect("Human node exists");
    assert_eq!(judging.node.status, StateMachineNodeStatus::Running);
    assert_eq!(judging.node.artifact_text.as_deref(), Some("看起来还行"));
    assert_eq!(judging.node.responded_by.as_deref(), Some("human_1001"));
    assert_eq!(judging.sub_status, Some(StateMachineNodeSubStatus::Judging));
    let pending = runtime
        .list_pending_human_nodes(ListPendingHumanNodesCommand {
            run_id: run_id.clone(),
            caller_actor_id: "human_1001".to_string(),
        })
        .await
        .expect("list pending Human nodes while judging");
    assert!(pending.is_empty());

    let duplicate = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: run_id.clone(),
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "第二次提交".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("Judging Human node must reject duplicate responses");
    assert!(matches!(duplicate, CollaborationRuntimeError::Conflict(_)));

    judge.release.notify_one();
    let completed = response_task
        .await
        .expect("Human response task should join")
        .expect("Judge should complete the Human node");
    assert_eq!(completed.node.status, StateMachineNodeStatus::Completed);
    assert_eq!(completed.node.artifact_text.as_deref(), Some("看起来还行"));
}

#[tokio::test]
async fn human_input_remains_persisted_when_judge_fails() {
    let group = Arc::new(GroupStore::new());
    group.upsert(test_group()).await.expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let judge = Arc::new(RecordingJudge::with_error("judge provider unavailable"));
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group,
        sessions,
        Arc::new(RecordingDelivery::default()),
        judge,
    );
    let started = runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: "group-1".to_string(),
            session_id: None,
            definition_yaml: Some(judged_human_input_yaml()),
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: json!({"proposal": "ship it"}),
            caller_id: Some("human_1001".to_string()),
            authenticated_human: Some(AuthenticatedHumanCaller {
                actor_id: "human_1001".to_string(),
                display_name: Some("Reviewer".to_string()),
            }), caller_association: None,
        })
        .await
        .expect("start judged Human run");

    let error = runtime
        .respond_human_node(RespondHumanNodeCommand {
            run_id: started.view.run.run_id.clone(),
            node_id: "review".to_string(),
            caller_actor_id: "human_1001".to_string(),
            content: "请补充风险说明".to_string(),
            source: HumanResponseSource::Http,
        })
        .await
        .expect_err("Judge failure should fail the Human response request");
    assert!(matches!(
        error,
        CollaborationRuntimeError::JudgeUnavailable(_)
    ));

    let failed = store
        .get_node_run(&started.view.run.run_id, "review")
        .await
        .expect("load failed Human node")
        .expect("Human node exists");
    assert_eq!(failed.status, StateMachineNodeStatus::Failed);
    assert_eq!(failed.artifact_text.as_deref(), Some("请补充风险说明"));
    assert_eq!(failed.responded_by.as_deref(), Some("human_1001"));
}
