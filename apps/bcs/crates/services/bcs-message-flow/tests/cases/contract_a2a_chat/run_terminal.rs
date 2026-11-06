use super::*;

#[tokio::test]
async fn detached_run_completes_when_final_is_the_first_event() {
    let (service, run_port, run_store) =
        build_run_service(vec![chat_event("final", "done")], false).await;

    service
        .start_async_chat(detached_async_command("detach-final-first"))
        .await
        .unwrap();
    run_port
        .wait_for_event_unregister("detach-final-first")
        .await;

    let run = run_store.get("detach-final-first").await.unwrap();
    assert_eq!(run.state.as_str(), "completed");
    assert_eq!(run.accumulated_content, "done");
}
#[tokio::test]
async fn detached_run_fails_when_error_is_the_first_event() {
    let (service, run_port, run_store) =
        build_run_service(vec![chat_event("error", "provider failed")], false).await;

    service
        .start_async_chat(detached_async_command("detach-error-first"))
        .await
        .unwrap();
    run_port
        .wait_for_event_unregister("detach-error-first")
        .await;

    let run = run_store.get("detach-error-first").await.unwrap();
    assert_eq!(run.state.as_str(), "failed");
    assert_eq!(run.error_message.as_deref(), Some("provider failed"));
}
#[tokio::test]
async fn async_run_service_accepts_and_drains_events_until_final() {
    let (service, run_port, _run_store) =
        build_run_service(vec![chat_event("final", "async pong")], false).await;

    let accepted = service
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
            run_id: "async-run".to_string(),
            session_key: "async-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    assert_eq!(accepted.run_id, "async-run");
    assert_eq!(accepted.bot_uuid, "bot-target");
    assert_eq!(accepted.session_id, "async-session");
    assert!(accepted.expires_at_ms > 0);
    run_port.wait_for_event_unregister("async-run").await;

    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-run".to_string(),
            wait_ms: 0,
            since_version: 0,
        },
    )
    .await
    .unwrap();
    assert_eq!(status.status, "completed");
    assert_eq!(
        status
            .response
            .unwrap()
            .get("content")
            .and_then(|v| v.as_str()),
        Some("async pong")
    );
}
#[tokio::test]
async fn async_run_service_marks_failed_on_chat_event_error() {
    let (service, run_port, _run_store) =
        build_run_service(vec![chat_event("error", "provider failed")], false).await;

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
            run_id: "async-error-run".to_string(),
            session_key: "async-error-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();
    run_port.wait_for_event_unregister("async-error-run").await;

    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-error-run".to_string(),
            wait_ms: 0,
            since_version: 0,
        },
    )
    .await
    .unwrap();

    assert_eq!(status.status, "failed");
    assert_eq!(
        status
            .response
            .as_ref()
            .and_then(|response| response.get("error_message"))
            .and_then(|value| value.as_str()),
        Some("provider failed")
    );
}
#[tokio::test]
async fn async_run_service_times_out_and_unregisters_when_no_terminal_event_arrives() {
    let (service, run_port, _run_store) = build_run_service(Vec::new(), true).await;

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
            run_id: "async-timeout-run".to_string(),
            session_key: "async-timeout-session".to_string(),
            timeout_ms: 20,
            client: None,
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    run_port.wait_for_event_unregister("async-timeout-run").await;
    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-timeout-run".to_string(),
            wait_ms: 0,
            since_version: 0,
        },
    )
    .await
    .unwrap();

    assert_eq!(status.status, "failed");
    assert_eq!(
        status
            .response
            .as_ref()
            .and_then(|response| response.get("error_message"))
            .and_then(|value| value.as_str()),
        Some("timeout")
    );
}
#[tokio::test]
async fn cancel_run_service_cancels_underlying_run_and_unregisters_channel() {
    let (service, run_port, _run_store) = build_run_service(Vec::new(), true).await;

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
            run_id: "cancel-run".to_string(),
            session_key: "cancel-session".to_string(),
            timeout_ms: 1_000,
            client: None,
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    let status = A2aChatRunService::cancel_run(
        service.as_ref(),
        ChatRunCancelCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "cancel-run".to_string(),
        },
    )
    .await
    .unwrap();

    assert_eq!(status.status, "cancelled");
    assert_eq!(
        status
            .response
            .as_ref()
            .and_then(|response| response.get("cancelled"))
            .and_then(|value| value.as_bool()),
        Some(true)
    );
    assert_eq!(run_port.cleanup_unregistered().await, vec!["cancel-run"]);
}
#[tokio::test]
async fn cancel_run_marks_running_run_cancelled() {
    let bot_delivery = Arc::new(support::RecordingBotDelivery::default());
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert("bot-source", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;
    let service = A2aChat::new(
        bot_delivery,
        run_store,
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
    )
    .with_bot_authority_hook(Some(staff_authority_default()));
    let caller = CallerContext::Bot(BotActor {
        bot_uuid: "bot-source".to_string(),
    });

    service
        .chat(A2aChatCommand {
            caller: caller.clone(),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: None,
            run_id: Some("run-2".to_string()),
            async_mode: true,
            session_key: Some("session-2".to_string()),
            timeout_ms: Some(10_000),
            client: None,
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    let status = A2aChatService::cancel_run(&service, caller, "run-2")
        .await
        .unwrap();
    assert_eq!(status.run_id, "run-2");
    assert_eq!(status.status, "cancelled");
    assert_eq!(
        status
            .response
            .unwrap()
            .get("content")
            .and_then(|v| v.as_str()),
        Some("")
    );
}
#[tokio::test]
async fn run_events_update_status_and_wake_waiters() {
    let bot_delivery = Arc::new(support::RecordingBotDelivery::default());
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert("bot-source", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;
    let service = Arc::new(
        A2aChat::new(
            bot_delivery,
            run_store,
            30_000,
            registry,
            Arc::new(StaticFriendCoreService::default()),
        )
        .with_bot_authority_hook(Some(staff_authority_default())),
    );
    let caller = CallerContext::Bot(BotActor {
        bot_uuid: "bot-source".to_string(),
    });

    service
        .chat(A2aChatCommand {
            caller: caller.clone(),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: None,
            run_id: Some("run-3".to_string()),
            async_mode: true,
            session_key: Some("session-3".to_string()),
            timeout_ms: Some(10_000),
            client: None,
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();

    let baseline = A2aChatService::get_run(service.as_ref(), caller.clone(), "run-3")
        .await
        .unwrap()
        .response
        .unwrap();
    let baseline_version = baseline.get("version").and_then(|v| v.as_u64()).unwrap();
    let waiter = {
        let service = service.clone();
        let caller = caller.clone();
        tokio::spawn(async move {
            service
                .wait_run(caller, "run-3", baseline_version, 500)
                .await
                .unwrap()
        })
    };

    tokio::time::sleep(Duration::from_millis(20)).await;
    let event = serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}]
            }
        }
    })
    .to_string();

    assert!(service.record_run_event("run-3", &event).await.unwrap());

    let status = waiter.await.unwrap();
    assert_eq!(status.status, "completed");
    let response = status.response.unwrap();
    assert_eq!(
        response.get("content").and_then(|v| v.as_str()),
        Some("done")
    );
    assert_eq!(
        response.get("state").and_then(|v| v.as_str()),
        Some("completed")
    );
    assert_eq!(
        response.get("is_terminal").and_then(|v| v.as_bool()),
        Some(true)
    );
}
