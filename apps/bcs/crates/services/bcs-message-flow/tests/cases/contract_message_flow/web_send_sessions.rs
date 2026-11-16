use super::*;

#[tokio::test]
async fn web_send_with_session_id_routes_v2_by_substituting_wire_group_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:abcdef12";
    let session = test_session(
        session_id,
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver), {
            let mut human = Participant::human("human_1", ParticipantRole::Observer);
            human.mode = Some(ParticipantMode::Present);
            human
        }],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello in session".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(
        frames.len(),
        1,
        "session participants should exclude bot-observer"
    );
    let frontend_commands = support.frontend_delivery.commands().await;
    assert_eq!(
        frontend_commands[0].target,
        FrontendDeliveryTarget::Session {
            session_id: session_id.to_string()
        }
    );
    let BcsFrame::Request(req) = &frames[0] else {
        panic!("expected request frame");
    };
    assert_eq!(req.method, "chat.send");
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], session_id);
    assert_eq!(params["session_key"], "group:group-1:");
    assert!(
        params.get("bcs_session_id").is_none(),
        "legacy protocol clients should not receive bcs_session_id explicitly"
    );
}

#[tokio::test]
async fn web_send_with_legacy_session_id_routes_v2_with_group_wire_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:00000000";
    let session = test_session(
        session_id,
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver), {
            let mut human = Participant::human("human_1", ParticipantRole::Observer);
            human.mode = Some(ParticipantMode::Present);
            human
        }],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello in legacy session".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 1);
    let BcsFrame::Request(req) = &frames[0] else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["session_key"], "group:group-1");
    assert!(params.get("bcs_session_id").is_none());
}

#[tokio::test]
async fn web_send_with_session_id_routes_v3_with_explicit_bcs_session_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:channel_dingtalk_abcdef12";
    support.registry.set_protocol_version("bot-driver", 3).await;
    let session = test_session(
        session_id,
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver), {
            let mut human = Participant::human("human_1", ParticipantRole::Observer);
            human.mode = Some(ParticipantMode::Present);
            human
        }],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello in session".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 1);
    let BcsFrame::Request(req) = &frames[0] else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["bcs_session_id"], session_id);
    assert_eq!(params["session_key"], session_id);
}

#[tokio::test]
async fn web_send_to_provider_with_session_id_uses_explicit_bcs_session_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:channel_dingtalk_abcdef12";
    let mut group = support.group.get("group-1").await.expect("group exists");
    group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "bot-driver")
        .expect("driver participant")
        .tags = vec!["group-tag".to_string()];
    group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "bot-observer")
        .expect("observer participant")
        .tags = vec!["stale-observer-tag".to_string()];
    support
        .group
        .upsert(group)
        .await
        .expect("update group tags");
    support
        .registry
        .set_delivery_target(
            "bot-driver",
            support::FakeRegistryService::provider_target("bot-driver"),
        )
        .await;
    support
        .registry
        .set_delivery_target(
            "bot-observer",
            support::FakeRegistryService::provider_target("bot-observer"),
        )
        .await;
    let mut provider = Participant::bot("bot-driver", ParticipantRole::Driver);
    provider.tags = vec!["session-tag".to_string(), "tenant-a".to_string()];
    let mut observer = Participant::bot("bot-observer", ParticipantRole::Observer);
    observer.tags = vec!["online".to_string(), "tenant-b".to_string()];
    let session = test_session(
        session_id,
        "group-1",
        vec![provider, observer, {
            let mut human = Participant::human("human_1", ParticipantRole::Observer);
            human.mode = Some(ParticipantMode::Present);
            human
        }],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello provider in session".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 2);
    assert!(
        support
            .bot_delivery
            .targets()
            .await
            .iter()
            .all(BotDeliveryTarget::is_http_provider)
    );
    let send = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("provider chat.send frame");
    let send_params = match send {
        BcsFrame::Request(req) => req.params.as_ref().expect("send params"),
        _ => unreachable!(),
    };
    assert_eq!(send_params["bcs_group_id"], "group-1");
    assert_eq!(send_params["bcs_session_id"], session_id);
    assert_eq!(send_params["session_key"], session_id);
    assert_eq!(send_params["tags"], json!(["session-tag", "tenant-a"]));

    let inject = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.inject"))
        .expect("provider chat.inject frame");
    let inject_params = match inject {
        BcsFrame::Request(req) => req.params.as_ref().expect("inject params"),
        _ => unreachable!(),
    };
    assert_eq!(inject_params["bcs_session_id"], session_id);
    assert_eq!(inject_params["tags"], json!(["online", "tenant-b"]));
}
