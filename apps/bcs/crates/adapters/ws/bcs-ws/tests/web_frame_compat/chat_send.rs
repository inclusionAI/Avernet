use super::*;

#[tokio::test]
async fn web_chat_send_frame_is_forwarded_to_message_flow_and_tracks_run() {
    let state = new_state();
    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);

    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let mut protected_rx = connection_state.open_protected_queue();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();

    let sent = recv_response(&mut rx).await;
    assert!(sent.ok, "response: {:?}", sent);
    assert_eq!(sent.payload.unwrap()["runId"], "run-web-1");
    assert!(rx.try_recv().is_err());
    assert_eq!(connection_state.active_run_ids, vec!["run-web-1"]);
    // Send-before-connect with NO subscribed view instance: the run
    // channel stays unregistered (spec §8.2 — 没有候选人即没有许可,
    // 无许可的输出不得旁路到无绑定队列). The business send itself
    // succeeded and tracks the run; only the output sink is refused.
    assert!(
        !state
            .dispatch_state
            .run_channels
            .is_registered("run-web-1")
            .await,
        "an instance-less sender binds no protected run sink"
    );
    assert!(
        state
            .dispatch_state
            .run_channels
            .get_session_runs("group-web-1:abcdef12")
            .await
            .is_empty(),
        "no session mapping is created for an unbound run"
    );
    assert!(
        !state
            .dispatch_state
            .run_channels
            .send_visible_event(
                "run-web-1",
                "hidden".to_string(),
                MessageVisibilityDomain::StateMachine,
                Some(&MessageAudience::FullOnly),
                false,
            )
            .await,
        "unbound run output is refused at the boundary"
    );
    assert!(
        timeout(Duration::from_millis(100), protected_rx.recv())
            .await
            .is_err(),
        "nothing reaches the protected queue"
    );

    let _ = &mut protected_rx;
    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].group_id, "group-web-1");
    assert_eq!(calls[0].from_actor_id, "human_100001");
    assert_eq!(calls[0].session_id.as_deref(), Some("group-web-1:abcdef12"));
    assert_eq!(calls[0].message, "hello");
    let authorizations = state.workbench_sessions.authorizations.lock().await;
    assert_eq!(authorizations.len(), 1);
    assert_eq!(authorizations[0].group_id, "group-web-1");
    assert_eq!(authorizations[0].from_actor_id, "human_100001");
    assert_eq!(
        authorizations[0].session_id.as_deref(),
        Some("group-web-1:abcdef12")
    );
    assert_eq!(
        authorizations[0].bound_actor_id.as_deref(),
        Some("human_100001")
    );
    let connects = state.workbench_sessions.connects.lock().await;
    assert_eq!(connects.len(), 1);
    assert_eq!(
        connects[0].session_id.as_deref(),
        Some("group-web-1:abcdef12")
    );
}

#[tokio::test]
async fn session_chat_send_does_not_inherit_group_full_scope() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let mut protected_rx = connection_state.open_protected_queue();
    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human_100001".to_string()),
        caller_ingress: None,
    };

    // Connect with the SESSION key: a session-anchored operation is
    // admitted against the Session subscribers only — a broader
    // Group-bound subscription can never inherit the send's output
    // (F4 strict target, now structural at the admission boundary).
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-group",
        "connect",
        Some(serde_json::json!({"group_id": "group-web-1", "session_id": "group-web-1:abcdef12"})),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);
    let send = BcsFrame::Request(RequestFrame::new(
        "send-session",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "session_id": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &auth,
    )
    .await
    .unwrap();
    assert!(recv_response(&mut rx).await.ok);

    assert!(
        state
            .dispatch_state
            .run_channels
            .send_visible_event(
                "run-web-1",
                "hidden".to_string(),
                MessageVisibilityDomain::StateMachine,
                Some(&MessageAudience::FullOnly),
                false,
            )
            .await,
        "the session run's operation permit covers its enqueue"
    );
    match timeout(Duration::from_secs(1), protected_rx.recv())
        .await
        .expect("permitted frame enqueued")
    {
        Some(bcs_ws::shared::OutboundFrame::Data { payload }) => {
            assert_eq!(payload, "hidden");
        }
        other => panic!("expected a data frame, got {other:?}"),
    }
    assert!(rx.try_recv().is_err());
}

#[tokio::test]
async fn web_chat_send_is_consumed_by_the_single_pending_human_input_and_emits_empty_final() {
    let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::new(
        SessionHumanInputBehavior::Consumed,
    ));
    let state = new_state_with_collaboration_runtime(collaboration_runtime.clone());
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-human-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "untrusted-payload-actor",
            "sessionKey": "group-web-1:abcdef12",
            "message": "批准发布"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();

    let sent = recv_response(&mut rx).await;
    assert!(sent.ok, "response: {sent:?}");
    assert_eq!(sent.payload.unwrap()["runId"], "state-run-1");

    let final_frame: serde_json::Value = serde_json::from_str(
        &rx.recv()
            .await
            .expect("HumanInput completion should emit a final chat event"),
    )
    .unwrap();
    assert_eq!(final_frame["type"], "event");
    assert_eq!(final_frame["event"], "chat");
    assert_eq!(final_frame["group_id"], "group-web-1");
    assert_eq!(final_frame["bot_uuid"], "bcs_state_machine");
    assert_eq!(final_frame["payload"]["run_id"], "state-run-1");
    assert_eq!(
        final_frame["payload"]["bcs_session_id"],
        "group-web-1:abcdef12"
    );
    assert_eq!(final_frame["payload"]["state"], "final");
    assert_eq!(final_frame["payload"]["message"]["role"], "assistant");
    assert_eq!(
        final_frame["payload"]["message"]["content"],
        serde_json::json!([])
    );
    assert!(rx.try_recv().is_err());
    assert!(state.message_flow.web_sends.lock().await.is_empty());
    let commands = collaboration_runtime.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id, "human_100001");
    assert_eq!(commands[0].content, "批准发布");
    assert_eq!(
        commands[0].session_id.as_deref(),
        Some("group-web-1:abcdef12")
    );
}

#[tokio::test]
async fn web_chat_send_is_rejected_when_state_machine_has_no_pending_human_input() {
    let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::new(
        SessionHumanInputBehavior::Conflict,
    ));
    let state = new_state_with_collaboration_runtime(collaboration_runtime);
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let send = BcsFrame::Request(RequestFrame::new(
        "send-human-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sessionKey": "group-web-1:abcdef12",
            "message": "不应进入 Bot 群聊"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();

    let rejected = recv_response(&mut rx).await;
    assert!(!rejected.ok);
    assert_eq!(
        rejected.error.as_ref().map(|error| error.code.as_str()),
        Some("conflict")
    );
    let response_error = rejected.error.as_ref().expect("conflict error");

    let error_frame: serde_json::Value = serde_json::from_str(
        &rx.recv()
            .await
            .expect("HumanInput rejection should emit a chat error event"),
    )
    .unwrap();
    assert_eq!(error_frame["type"], "event");
    assert_eq!(error_frame["event"], "chat");
    assert_eq!(error_frame["group_id"], "group-web-1");
    assert_eq!(error_frame["bot_uuid"], "bcs_state_machine");
    assert_eq!(
        error_frame["payload"]["bcs_session_id"],
        "group-web-1:abcdef12"
    );
    assert_eq!(error_frame["payload"]["state"], "error");
    assert_eq!(error_frame["payload"]["errorCode"], response_error.code);
    assert_eq!(
        error_frame["payload"]["errorMessage"],
        response_error.message
    );
    assert_eq!(
        error_frame["payload"]["message"]["content"][0]["text"],
        response_error.message
    );
    assert!(rx.try_recv().is_err());
    assert!(state.message_flow.web_sends.lock().await.is_empty());
}

#[tokio::test]
async fn web_chat_send_uses_session_subscription_key_for_sender_conn_id() {
    let state = new_state();

    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _fanout_rx = connection_state.open_protected_queue();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-1",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "group-web-1:abcdef12",
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    let sender_conn_id = connection_state.subscribed_sessions[0].1;

    let send = BcsFrame::Request(RequestFrame::new(
        "send-1",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "sender_id": "11111111",
            "sessionKey": "group-web-1:abcdef12",
            "message": "hello"
        })),
    ));

    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();

    let sent = recv_response(&mut rx).await;
    assert!(sent.ok, "response: {:?}", sent);

    let calls = state.message_flow.web_sends.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].session_id.as_deref(), Some("group-web-1:abcdef12"));
    assert_eq!(calls[0].sender_conn_id, Some(sender_conn_id));
}

#[tokio::test]
async fn web_chat_send_uses_view_bound_to_the_target_session() {
    let state = new_state();
    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState {
        subscribed_sessions: vec![
            (
                "group-web-1:participant-session".to_string(),
                1,
                Some(HumanMessageView {
                    actor_id: "human_100001".to_string(),
                    scope: MessageViewScope::Participant,
                    allow_legacy_unclassified_chat: false,
                }),
            ),
            ("group-web-1:full-session".to_string(), 2, None),
        ],
        ..Default::default()
    };
    let send = BcsFrame::Request(RequestFrame::new(
        "send-participant-session",
        "chat.send",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "bot_uuid": "human_100001",
            "session_id": "group-web-1:participant-session",
            "sessionKey": "group-web-1:participant-session",
            "message": "hello"
        })),
    ));

    // The admission for THIS send resolves to a Participant scope:
    // a FullOnly frame outside that scope is refused at the enqueue
    // boundary — the permit freezes the view and the writer filters
    // nothing (the old final-writer negative is now upstream).
    state
        .delivery_admission
        .set_scope(MessageViewScope::Participant);
    dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&send).unwrap(),
        &tx,
        &mut connection_state,
        &WorkbenchConnectionAuth::UserBound {
            actor_id: Some("human_100001".to_string()),
            caller_ingress: None,
        },
    )
    .await
    .unwrap();

    assert!(recv_response(&mut rx).await.ok);
    assert!(
        !state
            .dispatch_state
            .run_channels
            .send_visible_event(
                "run-web-1",
                "full-only".to_string(),
                MessageVisibilityDomain::ManagerWorker,
                Some(&MessageAudience::FullOnly),
                false,
            )
            .await,
        "a frame outside the frozen participant scope has no permit at enqueue"
    );
}
