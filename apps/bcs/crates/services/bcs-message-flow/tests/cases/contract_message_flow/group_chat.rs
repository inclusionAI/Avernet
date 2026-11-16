use super::*;

#[tokio::test]
async fn group_chat_validates_sender_and_returns_legacy_delivery_projection() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let flow = with_hook(
        BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
        ),
        DRIVER_OWNER_1,
    );

    let outcome = flow
        .handle_group_chat(GroupChatCommand {
        caller_ingress: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("bot-driver".to_string()),
            message: "hello as my bot".to_string(),
            session_id: Some("session-1".to_string()),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(outcome.group_id, "group-1");
    assert_eq!(outcome.driver_bot_id, "bot-driver");
    assert_eq!(outcome.delivered_count, 2);
    assert_eq!(outcome.failed_count, 0);
    assert_eq!(
        support.routing.route_calls().await,
        vec![("group-1".to_string(), "hello as my bot".to_string(), None)]
    );
    let frames = support.bot_delivery.frames().await;
    assert!(
        frames
            .iter()
            .any(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
    );
}

#[tokio::test]
async fn group_chat_uses_session_participants_for_human_sender_validation() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:human-chat";
    let session = test_session(
        session_id,
        "group-1",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            Participant::human("human_2", ParticipantRole::Observer),
        ],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let outcome = flow
        .handle_group_chat(GroupChatCommand {
        caller_ingress: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_2".to_string(),
                staff_no: "2".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("human_2".to_string()),
            message: "hello from the session Human".to_string(),
            session_id: Some(session_id.to_string()),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(outcome.delivered_count, 1);
    let frames = support.bot_delivery.frames().await;
    let send_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("chat.send frame");
    let BcsFrame::Request(req) = send_frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["channel"]["actor_id"], "human_2");
    assert_eq!(params["session_context"]["from_bot_id"], "human_2");
}

#[tokio::test]
async fn group_chat_rejects_bot_that_is_not_a_session_participant() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:driver-only";
    let session = test_session(
        session_id,
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let error = flow
        .handle_group_chat(GroupChatCommand {
        caller_ingress: None,
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-observer".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("bot-observer".to_string()),
            message: "should not be delivered".to_string(),
            session_id: Some(session_id.to_string()),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap_err();

    assert!(
        matches!(error, ServiceError::Unauthorized(message) if message.contains("not a participant"))
    );
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn group_chat_rejects_session_from_another_group() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "other-group:mismatched";
    let session = test_session(
        session_id,
        "other-group",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let error = flow
        .handle_group_chat(GroupChatCommand {
        caller_ingress: None,
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-driver".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("bot-driver".to_string()),
            message: "should not be delivered".to_string(),
            session_id: Some(session_id.to_string()),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap_err();

    assert!(matches!(
        error,
        ServiceError::InvalidOperation { message, .. }
            if message == "session 'other-group:mismatched' does not belong to group 'group-1'"
    ));
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn group_chat_rejects_session_without_participants() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:empty";
    let session = test_session(session_id, "group-1", Vec::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let error = flow
        .handle_group_chat(GroupChatCommand {
        caller_ingress: None,
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-driver".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("bot-driver".to_string()),
            message: "should not be delivered".to_string(),
            session_id: Some(session_id.to_string()),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap_err();

    assert!(matches!(
        error,
        ServiceError::InvalidOperation { message, .. }
            if message == "session 'group-1:empty' has no participants"
    ));
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn group_chat_treats_requested_sender_id_literally_without_trimming() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let error = flow
        .handle_group_chat(GroupChatCommand {
        caller_ingress: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some(" bot-driver ".to_string()),
            message: "hello as my bot".to_string(),
            session_id: Some("session-1".to_string()),
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap_err();

    assert!(
        matches!(error, ServiceError::Unauthorized(message) if message.contains(" bot-driver "))
    );
}

#[tokio::test]
async fn group_chat_uses_human_staff_number_as_display_name_when_speaking_as_owned_bot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let flow = with_hook(
        BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
        ),
        DRIVER_OWNER_1,
    );

    flow.handle_group_chat(GroupChatCommand {
        caller_ingress: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        requested_sender_id: Some("bot-driver".to_string()),
        message: "hello as my bot".to_string(),
        session_id: Some("session-1".to_string()),
        provider_bypass_headers: Vec::new(),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let send_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("chat.send frame");
    let BcsFrame::Request(req) = send_frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["channel"]["user_id"], "1");
    assert_eq!(params["channel"]["actor_id"], "bot-driver");
    assert_eq!(params["channel"]["actor_name"], "1");
    assert_eq!(params["session_context"]["from"], "1(bot-driver)");
}
