use super::*;

#[tokio::test]
async fn direct_chat_run_snapshot_port_contract() {
    let (service, _delivery, _registry) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("bot-target", "public", None),
        ],
        vec![],
    )
    .await;

    service.chat(chat_command("bot-target")).await.unwrap();

    bcs_test_support::contract::port::direct_chat_run_snapshot_port_contract_tests(&service).await;
}
#[tokio::test]
async fn direct_chat_run_snapshot_maps_http_client_kinds() {
    // The snapshot gauge counts active (non-terminal) runs only — terminal
    // totals come from the lifecycle counter. Use two active async runs (no
    // `final` event, so they stay Running) with different HTTP client kinds.
    let (service, _run_port, _run_store) = build_run_service(vec![], false).await;

    service
        .start_async_chat(AsyncA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_channel_from: Some("api-user".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            run_id: "http-client-kind-run".to_string(),
            session_key: "http-client-kind-session".to_string(),
            timeout_ms: 30_000,
            client: Some("http-chat".to_string()),
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();
    service
        .start_async_chat(AsyncA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_channel_from: Some("api-user".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            run_id: "http-async-client-kind-run".to_string(),
            session_key: "http-async-client-kind-session".to_string(),
            timeout_ms: 30_000,
            client: None,
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    let counts = DirectChatRunSnapshotPort::direct_chat_run_counts(service.as_ref())
        .await
        .unwrap();
    assert!(counts.iter().any(|count| {
        count.client_kind == DirectChatClientKind::HttpChat && count.count > 0
    }));
    assert!(counts.iter().any(|count| {
        count.client_kind == DirectChatClientKind::HttpChatAsync && count.count > 0
    }));
}
#[tokio::test]
async fn async_chat_creates_run_and_delivers_chat_send_frame() {
    let bot_delivery = Arc::new(support::RecordingBotDelivery::default());
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert_named("bot-source", "Source Bot", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;
    let service = A2aChat::new(
        bot_delivery.clone(),
        run_store.clone(),
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
    )
    .with_bot_authority_hook(Some(staff_authority_default()));

    let outcome = service
        .chat(A2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_id: Some("run-1".to_string()),
            async_mode: true,
            session_key: Some("session-1".to_string()),
            timeout_ms: Some(10_000),
            client: Some("contract-test".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: vec!["tag1".to_string(), "tag2".to_string()],
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: Some("detached".to_string()),
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(outcome.run_id, "run-1");
    assert_eq!(outcome.status, "running");
    let run = run_store.get("run-1").await.unwrap();
    assert_eq!(run.bot_uuid, "bot-target");
    assert_eq!(run.from_bot_id, "bot-source");
    assert_eq!(run.session_key, "session-1");
    assert_eq!(
        A2aChatService::get_run(
            &service,
            CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string()
            }),
            "run-1"
        )
        .await
        .unwrap()
        .status,
        "running"
    );
    assert_eq!(bot_delivery.kinds().await, vec![BotDeliveryKind::Send]);
    assert_eq!(
        bot_delivery.provider_transports().await,
        vec![ProviderTransportPreference::SseFirst]
    );
    let chat_send = bot_delivery
        .frames()
        .await
        .into_iter()
        .find_map(|frame| match frame {
            BcsFrame::Request(req) if req.method == "chat.send" && req.id == "run-1" => req.params,
            _ => None,
        })
        .unwrap();
    assert_eq!(chat_send["channel"]["user_id"], "Source Bot");
    assert_eq!(chat_send["channel"]["actor_id"], "bot-source");
    assert_eq!(chat_send["channel"]["actor_name"], "Source Bot");
    assert_eq!(chat_send["session_context"]["from"], "api-user");
    assert_eq!(chat_send["session_context"]["from_bot_id"], "bot-source");
    assert_eq!(chat_send["bcs_session_id"], "session-1");
    assert_eq!(chat_send["timeout_ms"], serde_json::json!(7_200_000));
    assert_eq!(chat_send["tags"], serde_json::json!(["tag1", "tag2"]));
    assert_eq!(chat_send["extensions"]["caller_wait_mode"], "detached");
}
#[tokio::test]
async fn chat_records_original_request_as_wrapped_chat_send_frame() {
    // At run creation the exact chat.send frame delivered to the target bot is
    // captured (write-once) into the run record's audit field
    // `original_request` as {"method":"chat.send","params":{...}}, so the
    // original request can be read back from the run table later. The audit
    // params must be byte-identical to the frame actually sent to the bot.
    let bot_delivery = Arc::new(support::RecordingBotDelivery::default());
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert_named("bot-source", "Source Bot", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;
    let service = A2aChat::new(
        bot_delivery.clone(),
        run_store.clone(),
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
    )
    .with_bot_authority_hook(Some(staff_authority_default()));

    service
        .chat(A2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_id: Some("run-audit".to_string()),
            async_mode: true,
            session_key: Some("session-audit".to_string()),
            timeout_ms: Some(10_000),
            client: Some("contract-test".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: vec!["tag1".to_string(), "tag2".to_string()],
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: Some("detached".to_string()),
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    let run = run_store.get("run-audit").await.unwrap();
    assert!(!run.original_request.is_empty(), "audit field must be set");
    let wrapped: serde_json::Value =
        serde_json::from_str(&run.original_request).expect("audit value is JSON");
    assert_eq!(wrapped["method"], "chat.send");

    // The audit params ARE exactly the frame params delivered to the bot.
    let delivered = bot_delivery
        .frames()
        .await
        .into_iter()
        .find_map(|frame| match frame {
            BcsFrame::Request(req) if req.method == "chat.send" && req.id == "run-audit" => req.params,
            _ => None,
        })
        .unwrap();
    assert_eq!(wrapped["params"], delivered);

    // Spot-check that request fields round-trip through the audit blob.
    assert_eq!(wrapped["params"]["session_context"]["from"], "api-user");
    assert_eq!(wrapped["params"]["tags"], serde_json::json!(["tag1", "tag2"]));
    assert_eq!(wrapped["params"]["extensions"]["caller_wait_mode"], "detached");
}
#[tokio::test]
async fn bcs_cli_a2a_chat_requests_callback_provider_transport() {
    let (service, bot_delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("bot-target", "public", None),
        ],
        Vec::new(),
    )
    .await;
    let mut command = chat_command("bot-target");
    command.client = Some("bcs-cli/0.3.0".to_string());

    service.chat(command).await.unwrap();

    assert_eq!(
        bot_delivery.provider_transports().await,
        vec![ProviderTransportPreference::Callback]
    );
}
#[tokio::test]
async fn detached_provider_async_run_submits_after_downlink_ack_then_runs_on_callback() {
    let bot_delivery = Arc::new(support::RecordingBotDelivery::default());
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(support::FakeRegistryService::default());
    registry.insert_named_actor("bot-source", "Source Bot").await;
    registry.insert_named_actor("bot-target", "Target Bot").await;
    registry.set_visibility("bot-source", "public").await;
    registry.set_visibility("bot-target", "public").await;
    registry
        .set_delivery_target(
            "bot-target",
            support::FakeRegistryService::provider_target("bot-target"),
        )
        .await;
    let run_port = Arc::new(RecordingRunPort::new(Vec::new(), true));
    let service = A2aChat::new_with_run_ports(
        bot_delivery,
        run_store.clone(),
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
        run_port.clone(),
        run_port.clone(),
    );

    let accepted = service
        .start_async_chat(AsyncA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_channel_from: Some("api-user".to_string()),
            authenticated_staff_id: None,
            run_id: "run-detached".to_string(),
            session_key: "session-detached".to_string(),
            timeout_ms: 10_000,
            client: Some("http-chat-async".to_string()),
            tags: Vec::new(),
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: Some("detached".to_string()),
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(accepted.run_id, "run-detached");
    let status = A2aChatRunService::get_run(
        &service,
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "run-detached".to_string(),
            wait_ms: 0,
            since_version: 0,
        },
    )
    .await
    .unwrap();
    assert_eq!(status.status, "submitted");
    assert_eq!(
        run_store.get("run-detached").await.unwrap().state.as_str(),
        "submitted"
    );

    run_port.send_event("run-detached", ping_event()).await;
    tokio::task::yield_now().await;
    assert_eq!(
        run_store.get("run-detached").await.unwrap().state.as_str(),
        "submitted",
        "ping must not acknowledge a detached run"
    );

    run_port
        .send_event("run-detached", chat_delta_text("detached "))
        .await;
    tokio::time::timeout(Duration::from_secs(1), async {
        loop {
            if run_store
                .get("run-detached")
                .await
                .is_some_and(|run| run.state.as_str() == "running")
            {
                break;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("detach acknowledgement should mark the run running");

    let status = A2aChatRunService::get_run(
        &service,
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "run-detached".to_string(),
            wait_ms: 0,
            since_version: 0,
        },
    )
    .await
    .unwrap();
    assert_eq!(status.status, "running");
    assert_eq!(
        status.response.as_ref().unwrap()["content"],
        serde_json::json!("detached ")
    );
    assert_eq!(
        run_store.get("run-detached").await.unwrap().state.as_str(),
        "running"
    );

    run_port
        .send_event("run-detached", chat_event("final", "detached result"))
        .await;
    run_port
        .wait_for_event_unregister("run-detached")
        .await;
    assert_eq!(
        run_store.get("run-detached").await.unwrap().state.as_str(),
        "completed"
    );
    assert_eq!(
        run_store.get("run-detached").await.unwrap().accumulated_content,
        "detached result"
    );
}
#[tokio::test]
async fn a2a_chat_blocking_interceptor_prevents_bot_delivery() {
    let bot_delivery = Arc::new(support::RecordingBotDelivery::default());
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert("bot-source", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;

    let mut chain = InterceptorChain::new();
    chain.push(A2aBlockingInterceptor);
    let service = A2aChat::new(
        bot_delivery.clone(),
        run_store.clone(),
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
    )
    .with_bot_authority_hook(Some(staff_authority_default()))
    .with_interceptors(Arc::new(chain));

    let result = service
        .chat(A2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_id: Some("run-blocked".to_string()),
            async_mode: true,
            session_key: Some("session-blocked".to_string()),
            timeout_ms: Some(10_000),
            client: Some("contract-test".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await;

    match result {
        Err(ServiceError::Forbidden(message)) => {
            assert_eq!(message, "a2a chat blocked by test");
        }
        other => panic!("expected blocked Forbidden, got {other:?}"),
    }
    // Bot must NOT receive any chat.send frame.
    assert_eq!(bot_delivery.frames().await.len(), 0);
    // Run must be marked failed.
    let run = run_store.get("run-blocked").await.expect("run record");
    assert!(
        run.state == bcs_message_flow::a2a_chat::run_store::ChatRunState::Failed,
        "expected run to be marked failed after Block, got {:?}",
        run.state
    );
}
