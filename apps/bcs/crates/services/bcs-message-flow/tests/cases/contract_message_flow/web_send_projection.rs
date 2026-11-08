use super::*;

#[tokio::test]
async fn web_send_persists_human_only_audience_for_manager_worker() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
        Participant::human("human_1", ParticipantRole::Observer),
    ];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "start service work".to_string(),
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

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].sender_id, "human_1");
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].owner_bot_id, None);
    assert_eq!(
        appended[0].audience,
        Some(bcs_domain::MessageAudience::directed(["human_1"]).unwrap())
    );

    let chat_support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let chat_repo = Arc::new(RecordingMessageRepo::default());
    let chat_flow = BcsMessageFlow::new(
        chat_support.group.clone(),
        chat_support.routing.clone(),
        chat_support.registry.clone(),
        chat_support.bot_delivery.clone(),
        chat_support.frontend_delivery.clone(),
    )
    .with_message_repo(chat_repo.clone());

    chat_flow
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "regular chat".to_string(),
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

    let chat_appended = chat_repo.appended().await;
    assert_eq!(chat_appended.len(), 1);
    assert_eq!(chat_appended[0].owner_bot_id, None);
}

#[tokio::test]
async fn accepted_chat_send_records_run_context_for_callback() {
    const CONFIGURED_TIMEOUT_MS: u64 = 7_200_000;

    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_chat_run_timeout_ms(CONFIGURED_TIMEOUT_MS)
    .with_bot_run_context(run_context.clone());

    let before_send_ms = bcs_protocol::now_ms();
    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller_execution: None,
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: Some("group-1:abcdef12".to_string()),
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "hello".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: Some("idempotency-1".to_string()),
            source_im_message_id: Some("source-msg-1".to_string()),
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();
    let after_send_ms = bcs_protocol::now_ms();

    let context = run_context
        .get_context(&outcome.primary_run_id)
        .await
        .expect("run context");
    assert_eq!(context.bot_id, "bot-driver");
    assert_eq!(context.group_id, "group-1");
    assert_eq!(context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    let delivered_session_key = support
        .bot_delivery
        .frames()
        .await
        .into_iter()
        .find_map(|frame| match frame {
            BcsFrame::Request(request) if request.method == "chat.send" => request
                .params
                .and_then(|params| params.get("session_key").and_then(|value| value.as_str()).map(str::to_string)),
            _ => None,
        })
        .expect("chat.send session_key");
    let active = run_context
        .find_active_run(&outcome.primary_run_id)
        .await
        .unwrap()
        .expect("active run context");
    assert_eq!(
        active.downstream_session_key.as_deref(),
        Some(delivered_session_key.as_str())
    );
    assert!(!context.terminal);
    assert_eq!(
        flow.message_tracker
            .channel_source_message_id(&outcome.primary_run_id)
            .await
            .as_deref(),
        Some("source-msg-1")
    );
    assert!(context.deadline_ms >= before_send_ms.saturating_add(CONFIGURED_TIMEOUT_MS));
    assert!(context.deadline_ms <= after_send_ms.saturating_add(CONFIGURED_TIMEOUT_MS));
}

#[tokio::test]
async fn web_send_in_human_bot_dm_omits_group_context_by_default() -> ServiceResult<()> {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    make_human_bot_dm(&support).await;
    support.registry.set_protocol_version("bot-driver", 3).await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello direct".to_string(),
        mentions: vec!["bot-driver".to_string()],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    })
    .await?;

    let frames = support.bot_delivery.frames().await;
    let Some(BcsFrame::Request(req)) = frames.first() else {
        return Err(ServiceError::InternalError(
            "expected request frame".to_string(),
        ));
    };
    let Some(params) = req.params.as_ref() else {
        return Err(ServiceError::InternalError("expected params".to_string()));
    };
    let Some(text) = params["message"]["content"][0]["text"].as_str() else {
        return Err(ServiceError::InternalError("expected text".to_string()));
    };
    assert_eq!(text, "hello direct");
    let Some(participants) = params["session_context"]["participants"].as_array() else {
        return Err(ServiceError::InternalError(
            "expected participants".to_string(),
        ));
    };
    assert_eq!(participants.len(), 0);
    assert!(params["session_context"].get("group_type").is_none());
    assert!(params["session_context"].get("routing_mode").is_none());
    assert!(params["session_context"].get("recipient_role").is_none());
    Ok(())
}

#[tokio::test]
async fn opted_in_channel_sender_identity_is_projected_to_bot_frame() -> ServiceResult<()> {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_protocol_version("bot-driver", 3).await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_410025".to_string(),
            staff_no: "410025".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_410025".to_string(),
        from_name: Some("张三".to_string()),
        message: "hello direct".to_string(),
        mentions: Vec::new(),
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: Some(ChannelSenderIdentity {
            channel_type: "dingtalk".to_string(),
            user_id: "410025".to_string(),
            actor_id: "human_410025".to_string(),
            display_name: "张三".to_string(),
        }),
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    })
    .await?;

    let frames = support.bot_delivery.frames().await;
    assert!(
        frames.iter().any(
            |frame| matches!(frame, BcsFrame::Request(request) if request.method == "chat.send")
        )
    );
    assert!(frames.iter().any(
        |frame| matches!(frame, BcsFrame::Request(request) if request.method == "chat.inject")
    ));
    for frame in frames {
        let BcsFrame::Request(request) = frame else {
            continue;
        };
        let channel = &request.params.as_ref().unwrap()["channel"];
        assert_eq!(channel["source"], "dingtalk");
        assert_eq!(channel["user_id"], "410025");
        assert_eq!(channel["actor_id"], "human_410025");
        assert_eq!(channel["actor_name"], "张三");
        assert_eq!(channel["identity_forwarding"], true);
    }
    Ok(())
}

#[tokio::test]
async fn web_send_delivery_frame_contains_recipient_group_context() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "@Driver hello".to_string(),
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
    let driver_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("driver send frame");
    let BcsFrame::Request(req) = driver_frame else {
        panic!("expected request frame");
    };
    let context = req
        .params
        .as_ref()
        .and_then(|params| params.get("session_context"))
        .expect("session_context");
    assert_eq!(
        req.params
            .as_ref()
            .and_then(|params| params.get("timeout_ms")),
        None
    );
    assert_eq!(context["session_id"], "group-1");
    assert_eq!(context["recipient"], "bot-driver");
    assert_eq!(context["recipient_role"], "driver");
    assert_eq!(context["from_bot_id"], "human_1");
}

#[tokio::test]
async fn web_send_direct_bot_projection_hides_bcs_group_context() -> ServiceResult<()> {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:abcdef12";
    support.registry.set_protocol_version("bot-driver", 3).await;
    let mut session = test_session(
        session_id,
        "group-1",
        vec![Participant::bot("bot-driver", ParticipantRole::Driver), {
            let mut human = Participant::human("human_1", ParticipantRole::Observer);
            human.mode = Some(ParticipantMode::Present);
            human.bot_name = Some("张三".to_string());
            human
        }],
    );
    session.meta = Some(json!({
        "channel": {
            "source": "dingtalk",
            "context_projection": "direct_bot"
        }
    }));
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
        from_name: Some("张三".to_string()),
        message: "帮我查一下".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    })
    .await?;

    let frames = support.bot_delivery.frames().await;
    let Some(BcsFrame::Request(req)) = frames.first() else {
        return Err(ServiceError::InternalError(
            "expected request frame".to_string(),
        ));
    };
    let Some(params) = req.params.as_ref() else {
        return Err(ServiceError::InternalError("expected params".to_string()));
    };
    let Some(text) = params["message"]["content"][0]["text"].as_str() else {
        return Err(ServiceError::InternalError("expected text".to_string()));
    };
    assert_eq!(text, "帮我查一下");
    assert_eq!(params["session_context"]["session_id"], session_id);
    let Some(participants) = params["session_context"]["participants"].as_array() else {
        return Err(ServiceError::InternalError(
            "expected participants".to_string(),
        ));
    };
    assert_eq!(participants.len(), 0);
    assert!(params["session_context"].get("group_type").is_none());
    assert!(params["session_context"].get("routing_mode").is_none());
    assert!(params["session_context"].get("recipient_role").is_none());
    Ok(())
}

#[tokio::test]
async fn web_send_prefers_human_from_name_in_delivered_frame() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .group
        .add_participant(
            "group-1",
            Participant::human("human_2", ParticipantRole::Observer),
        )
        .await
        .unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_2".to_string(),
            staff_no: "2".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_2".to_string(),
        from_name: Some("Alice Human".to_string()),
        message: "hello from a person".to_string(),
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
    let driver_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("driver send frame");
    let BcsFrame::Request(req) = driver_frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["channel"]["user_id"], "Alice Human");
    assert_eq!(params["channel"]["actor_id"], "human_2");
    assert_eq!(params["channel"]["actor_name"], "Alice Human");
    assert_eq!(params["session_context"]["from"], "Alice Human(human_2)");
    assert!(
        params["message"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("[from:Alice Human]hello from a person")
    );
}

#[tokio::test]
async fn web_send_persists_raw_mention_text_while_bots_receive_cleaned_text() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    flow.handle_web_send(WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "@Driver please review".to_string(),
        mentions: vec!["bot-driver".to_string()],
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

    // Human-facing history keeps the sender's text verbatim and stores the
    // structured mention targets for rendering.
    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].content["text"], "@Driver please review");
    assert_eq!(appended[0].content["mentions"][0], "bot-driver");
    assert!(appended[0].content.get("attachments").is_none());

    // Bot-bound deliveries still get the @-stripped routing text.
    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 2);
    for frame in frames {
        let BcsFrame::Request(req) = frame else {
            panic!("expected request frame");
        };
        let params = req.params.as_ref().expect("params");
        let text = params["message"]["content"][0]["text"]
            .as_str()
            .expect("text");
        assert!(text.contains("Driver please review"), "text: {text}");
        assert!(!text.contains("@Driver"), "text: {text}");
    }

    // The realtime workbench event already echoes the raw text and mentions.
    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    let event: serde_json::Value = serde_json::from_str(&events[0]).unwrap();
    assert_eq!(
        event["payload"]["message"]["content"][0]["text"],
        "@Driver please review"
    );
    assert_eq!(event["payload"]["message"]["mentions"][0], "bot-driver");
}
