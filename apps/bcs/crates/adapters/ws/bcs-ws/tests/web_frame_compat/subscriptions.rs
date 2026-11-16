use super::*;

#[tokio::test]
async fn web_connect_without_view_actor_preserves_legacy_full_subscription() {
    let state = new_state();
    *state.workbench_sessions.connect_participants.lock().await =
        Some(vec![WorkbenchParticipantView {
            bot_uuid: "owned-bot".to_string(),
            role: "driver".to_string(),
            kind: ParticipantKind::Bot,
            mode: Some(ParticipantMode::Present),
            message_view_scope: MessageViewScope::Full,
        }]);

    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let mut fanout_rx = connection_state.open_protected_queue();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-1",
        "connect",
        Some(serde_json::json!({"group_id": "group-web-1"})),
    ));

    let outcome = dispatch_client_frame(
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
    assert_eq!(
        outcome,
        WebDispatchOutcome::ClientConnect { subscribed: true }
    );

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    assert_eq!(
        state
            .dispatch_state
            .frontend_connections
            .connection_count("group-web-1")
            .await,
        1
    );
    assert_eq!(connection_state.subscribed_sessions.len(), 1);
    assert!(connection_state.subscribed_sessions[0].2.is_none());
    // FANOUT on a bound connection is admitted per EVENT (an
    // independent resource event here): the frame reaches the
    // queue already authorized — no per-frame writer decision
    // exists any more (bot-authority spec §8.2).
    assert_eq!(
        state
            .dispatch_state
            .frontend_connections
            .broadcast_visible_excluding(
                "group-web-1",
                "legacy-full-only",
                MessageVisibilityDomain::ManagerWorker,
                Some(&MessageAudience::FullOnly),
                None,
                None,
                None,
                false,
            )
            .await,
        1,
        "the admitted event is enqueued for the bound connection"
    );
    match timeout(Duration::from_secs(1), fanout_rx.recv()).await {
        Ok(Some(bcs_ws::shared::OutboundFrame::Data { payload })) => {
            assert_eq!(payload, "legacy-full-only");
        }
        other => panic!("expected a fanout data frame, got {other:?}"),
    }
    let connects = state.workbench_sessions.connects.lock().await;
    assert_eq!(connects.len(), 1);
    assert_eq!(connects[0].group_id, "group-web-1");
    assert_eq!(connects[0].session_id, None);
    assert_eq!(connects[0].bound_actor_id.as_deref(), Some("human_100001"));
    assert_eq!(connects[0].view_actor_id, None);
    assert!(
        state
            .group_session_connections
            .authorizations
            .lock()
            .await
            .is_empty()
    );
}

#[tokio::test]
async fn web_connect_with_explicit_human_view_binds_participant_projection() {
    let state = new_state();
    *state.workbench_sessions.connect_scope.lock().await = Some(MessageViewScope::Participant);

    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let mut fanout_rx = connection_state.open_protected_queue();
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-participant",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "view_actor_id": "human_100001"
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
    let payload = connected.payload.expect("connect payload");
    assert_eq!(payload["view_actor_id"], "human_100001");
    assert_eq!(payload["message_view_scope"], "participant");
    assert_eq!(
        connection_state.subscribed_sessions[0].2,
        Some(HumanMessageView {
            actor_id: "human_100001".to_string(),
            scope: MessageViewScope::Participant,
            allow_legacy_unclassified_chat: true,
        })
    );
    // The scope decision now happens at the ENQUEUE boundary:
    // with a Participant-scoped admission the FullOnly event is
    // filtered BEFORE enqueue — nothing reaches the queue, and
    // per-frame writer re-authorization no longer exists (§8.2).
    state
        .delivery_admission
        .set_scope(MessageViewScope::Participant);
    assert_eq!(
        state
            .dispatch_state
            .frontend_connections
            .broadcast_visible_excluding(
                "group-web-1",
                "hidden",
                MessageVisibilityDomain::ManagerWorker,
                Some(&MessageAudience::FullOnly),
                None,
                None,
                None,
                false,
            )
            .await,
        0,
        "a FullOnly event outside the admitted scope is filtered at the enqueue boundary"
    );
    assert!(
        timeout(Duration::from_millis(100), fanout_rx.recv())
            .await
            .is_err(),
        "no frame is enqueued for a filtered event"
    );
}

#[tokio::test]
async fn web_connect_with_session_id_subscribes_session_registry_key() {
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

    let outcome = dispatch_client_frame(
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
    assert_eq!(
        outcome,
        WebDispatchOutcome::ClientConnect { subscribed: true }
    );

    let connected = recv_response(&mut rx).await;
    assert!(connected.ok);
    assert_eq!(
        state
            .dispatch_state
            .frontend_connections
            .connection_count("group-web-1")
            .await,
        0
    );
    assert_eq!(
        state
            .dispatch_state
            .frontend_connections
            .connection_count("group-web-1:abcdef12")
            .await,
        1
    );
    assert_eq!(connection_state.subscribed_sessions.len(), 1);
    assert_eq!(
        connection_state.subscribed_sessions[0].0,
        "group-web-1:abcdef12"
    );

    let connects = state.workbench_sessions.connects.lock().await;
    assert_eq!(connects.len(), 1);
    assert_eq!(connects[0].group_id, "group-web-1");
    assert_eq!(
        connects[0].session_id.as_deref(),
        Some("group-web-1:abcdef12")
    );
}

#[tokio::test]
async fn user_bound_connect_can_subscribe_multiple_sessions() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut connection_state = WebClientConnectionState::default();
    let _protected_rx = connection_state.open_protected_queue();
    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human_100001".to_string()),
        caller_ingress: None,
    };

    for (request_id, session_id) in [
        ("connect-session-1", "session-user-1"),
        ("connect-session-2", "session-user-2"),
    ] {
        let connect = BcsFrame::Request(RequestFrame::new(
            request_id,
            "connect",
            Some(serde_json::json!({
                "group_id": "group-web-1",
                "session_id": session_id
            })),
        ));
        let outcome = dispatch_client_frame(
            &state.dispatch_state,
            &serde_json::to_string(&connect).unwrap(),
            &tx,
            &mut connection_state,
            &auth,
        )
        .await
        .unwrap();

        assert_eq!(outcome, WebDispatchOutcome::ClientConnect { subscribed: true });
        assert!(recv_response(&mut rx).await.ok);
    }

    let connects = state.workbench_sessions.connects.lock().await;
    assert_eq!(connects.len(), 2);
    assert_eq!(connects[0].session_id.as_deref(), Some("session-user-1"));
    assert_eq!(connects[1].session_id.as_deref(), Some("session-user-2"));
    assert_eq!(connection_state.subscribed_sessions.len(), 2);
}
