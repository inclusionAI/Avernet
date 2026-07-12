use std::sync::Arc;

use bcs_message_flow::a2a_chat::{A2aChat, ChatRunStore, DrainOutcome, drain_chat_event, drain_chat_event_with_mode};
use bcs_protocol::BcsFrame;
use bcs_domain::{Organization, OrganizationMember};
use bcs_service_api::{
    A2aChatCommand, A2aChatRunService, A2aChatService, ActorKind, ActorStatus, AgentCredentials,
    AsyncA2aChatCommand, BlockingA2aChatCommand, BotActor, BotCapabilities, BotDeliveryCommand,
    BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, BotDeliveryTarget, BotDynamicStatus,
    BotRegistryCoreService, CallerContext, ChatResponseMode, ChatRunCancelCommand, ChatRunCleanupPort, ChatRunEventPort, ChatRunQueryCommand,
    AuthorizedOrganizationPair, DirectChatClientKind, DirectChatRunSnapshotPort, FriendCoreService, OrganizationCoreService, RegisteredBot,
    OrganizationCandidateBot, OrganizationCandidateQuery, ServiceError, ServiceResult,
    interceptor::{
        BlockReason, InterceptorChain, InterceptorDecision, MessageInterceptor, OutboundMessage,
    },
};
use std::collections::{HashMap, HashSet};
use std::time::Duration;
use tokio::sync::{Mutex, Notify, RwLock, mpsc};

#[path = "../../../test-support/message_flow_contract_support.rs"]
#[allow(dead_code)]
mod support;

fn chat_command(target_bot_id: &str) -> A2aChatCommand {
    A2aChatCommand {
        caller: CallerContext::Bot(BotActor {
            bot_uuid: "bot-source".to_string(),
        }),
        target_bot_id: target_bot_id.to_string(),
        message: "hello".to_string(),
        from_actor_id: Some("api-user".to_string()),
        run_id: Some("run-1".to_string()),
        async_mode: true,
        session_key: Some("session-1".to_string()),
        timeout_ms: Some(10_000),
        client: Some("contract-test".to_string()),
        authenticated_staff_id: Some("owner-1".to_string()),
        tags: Vec::new(),
        response_mode: ChatResponseMode::Full,
        caller_wait_mode: None,
        organization_code: None,
    }
}

fn scoped_chat_command(target_bot_id: &str) -> A2aChatCommand {
    A2aChatCommand {
        organization_code: Some("promo-2026".to_string()),
        ..chat_command(target_bot_id)
    }
}

async fn build_organization_service(
    bots: Vec<(&str, &str, Option<&str>)>,
    friendships: Vec<(&str, &str)>,
    members: Vec<&str>,
) -> A2aChat {
    let bot_delivery = Arc::new(RecordingDelivery::new(true));
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    let friend = Arc::new(StaticFriendCoreService::new(friendships));
    for (bot_id, visibility, created_by) in bots {
        registry.insert(bot_id, visibility, created_by).await;
    }
    let organization = Arc::new(StaticOrganizationCoreService::new(members));
    A2aChat::new(bot_delivery, run_store, 30_000, registry, friend).with_organization(organization)
}

async fn build_service(
    bots: Vec<(&str, &str, Option<&str>)>,
    friendships: Vec<(&str, &str)>,
) -> (A2aChat, Arc<RecordingDelivery>, Arc<MemoryRegistry>) {
    let bot_delivery = Arc::new(RecordingDelivery::new(true));
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    let friend = Arc::new(StaticFriendCoreService::new(friendships));
    for (bot_id, visibility, created_by) in bots {
        registry.insert(bot_id, visibility, created_by).await;
    }
    (
        A2aChat::new(
            bot_delivery.clone(),
            run_store,
            30_000,
            registry.clone(),
            friend,
        ),
        bot_delivery,
        registry,
    )
}

async fn build_run_service(
    events: Vec<String>,
    keep_open_after_send: bool,
) -> (Arc<A2aChat>, Arc<RecordingRunPort>, Arc<ChatRunStore>) {
    let bot_delivery = Arc::new(support::RecordingBotDelivery::default());
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert_named("bot-source", "Source Bot", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;
    let run_port = Arc::new(RecordingRunPort::new(events, keep_open_after_send));
    let service = Arc::new(A2aChat::new_with_run_ports(
        bot_delivery,
        run_store.clone(),
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
        run_port.clone(),
        run_port.clone(),
    ));
    (service, run_port, run_store)
}

fn chat_event(state: &str, text: &str) -> String {
    serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": state,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}]
            }
        }
    })
    .to_string()
}

fn chat_event_state(state: &str) -> String {
    serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": state
        }
    })
    .to_string()
}

fn tool_call_event(state: &str) -> String {
    serde_json::json!({
        "type": "event",
        "event": "chat.event",
        "payload": {
            "state": state,
            "tool_call_id": "tool-1",
            "tool_name": "lookup"
        }
    })
    .to_string()
}

fn agent_tool_event() -> String {
    serde_json::json!({
        "type": "event",
        "event": "agent",
        "payload": {
            "run_id": "run-1",
            "bcs_group_id": "group-1",
            "stream": "tool",
            "ts": 123,
            "data": {
                "name": "lookup",
                "phase": "result",
                "toolCallId": "tool-1"
            }
        }
    })
    .to_string()
}

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
    let (service, run_port, _run_store) =
        build_run_service(vec![chat_event("final", "pong")], false).await;

    service
        .run_blocking_chat(BlockingA2aChatCommand {
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
            timeout_ms: 1_000,
            client: None,
            response_mode: ChatResponseMode::Full,
            organization_code: None,
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
            timeout_ms: 1_000,
            client: None,
            response_mode: ChatResponseMode::Full,
            caller_wait_mode: None,
            organization_code: None,
        })
        .await
        .unwrap();
    run_port
        .wait_for_event_unregister("http-async-client-kind-run")
        .await;

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
    );

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
    assert_eq!(chat_send["timeout_ms"], serde_json::json!(10_000));
    assert_eq!(chat_send["tags"], serde_json::json!(["tag1", "tag2"]));
    assert_eq!(chat_send["extensions"]["caller_wait_mode"], "detached");
}

#[tokio::test]
async fn blocking_run_service_records_final_event_and_unregisters_run() {
    let (service, run_port, run_store) = build_run_service(
        vec![chat_event("delta", "po"), chat_event("final", "ng")],
        false,
    )
    .await;

    let outcome = service
        .run_blocking_chat(BlockingA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("api-user".to_string()),
            run_channel_from: Some("api-user".to_string()),
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            run_id: "blocking-run".to_string(),
            session_key: "blocking-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::Full,
            organization_code: None,
        })
        .await
        .unwrap();

    assert!(outcome.delivered);
    assert_eq!(outcome.bot_uuid, "bot-target");
    assert_eq!(outcome.session_id, "blocking-session");
    assert_eq!(outcome.content, "pong");
    assert_eq!(
        run_store.get("blocking-run").await.unwrap().state.as_str(),
        "completed"
    );
    assert_eq!(run_port.event_unregistered().await, vec!["blocking-run"]);
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

    run_port
        .send_event("run-detached", chat_event_state("delivered"))
        .await;
    run_port
        .wait_for_event_unregister("run-detached")
        .await;

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
        serde_json::json!("")
    );
    assert_eq!(
        run_store.get("run-detached").await.unwrap().state.as_str(),
        "running"
    );
    run_store.cleanup_expired(u64::MAX, u64::MAX).await;
    assert_eq!(
        run_store.get("run-detached").await.unwrap().state.as_str(),
        "running"
    );
}

#[tokio::test]
async fn blocking_run_service_unregisters_when_recording_event_fails() {
    let (service, run_port, run_store) = build_run_service(Vec::new(), true).await;
    let task = {
        let service = service.clone();
        tokio::spawn(async move {
            service
                .run_blocking_chat(BlockingA2aChatCommand {
                    caller: CallerContext::Bot(BotActor {
                        bot_uuid: "bot-source".to_string(),
                    }),
                    target_bot_id: "bot-target".to_string(),
                    message: "hello".to_string(),
                    from_actor_id: Some("api-user".to_string()),
                    run_channel_from: Some("api-user".to_string()),
                    authenticated_staff_id: Some("owner-1".to_string()),
                    tags: Vec::new(),
                    run_id: "record-error-run".to_string(),
                    session_key: "record-error-session".to_string(),
                    timeout_ms: 1_000,
                    client: None,
                    response_mode: ChatResponseMode::Full,
                    organization_code: None,
                })
                .await
        })
    };

    for _ in 0..50 {
        if run_store.get("record-error-run").await.is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert!(run_store.mark_completed("record-error-run", None).await);
    run_store.cleanup_expired(u64::MAX, 0).await;
    run_port
        .send_event("record-error-run", chat_event("final", "late"))
        .await;

    assert!(matches!(task.await.unwrap(), Err(ServiceError::BotNotFound(_))));
    assert_eq!(run_port.event_unregistered().await, vec!["record-error-run"]);
}

#[tokio::test]
async fn run_service_preserves_omitted_from_as_run_channel_metadata_none() {
    let (service, run_port, _run_store) =
        build_run_service(vec![chat_event("final", "pong")], false).await;

    service
        .run_blocking_chat(BlockingA2aChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            target_bot_id: "bot-target".to_string(),
            message: "hello".to_string(),
            from_actor_id: Some("user".to_string()),
            run_channel_from: None,
            authenticated_staff_id: Some("owner-1".to_string()),
            tags: Vec::new(),
            run_id: "metadata-run".to_string(),
            session_key: "metadata-session".to_string(),
            timeout_ms: 1_000,
            client: None,
            response_mode: ChatResponseMode::Full,
            organization_code: None,
        })
        .await
        .unwrap();

    assert_eq!(
        run_port.registrations().await,
        vec![(
            "metadata-run".to_string(),
            "metadata-session".to_string(),
            Some("http-chat".to_string()),
            None,
        )]
    );
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
async fn async_run_after_last_tool_call_mode_returns_only_followup_text() {
    let (service, run_port, _run_store) = build_run_service(
        vec![
            chat_event("delta", "analysis before tool. "),
            tool_call_event("tool_call_start"),
            chat_event("delta", "tool streaming detail. "),
            tool_call_event("tool_call_end"),
            chat_event("delta", "answer "),
            chat_event("final", "after tool"),
        ],
        false,
    )
    .await;

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
            run_id: "async-after-tool-run".to_string(),
            session_key: "async-after-tool-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::AfterLastToolCall,
            caller_wait_mode: None,
            organization_code: None,
        })
        .await
        .unwrap();
    run_port.wait_for_event_unregister("async-after-tool-run").await;

    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-after-tool-run".to_string(),
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
        Some("answer after tool")
    );
}

#[tokio::test]
async fn async_run_after_last_tool_call_mode_uses_agent_tool_boundary() {
    let (service, run_port, _run_store) = build_run_service(
        vec![
            chat_event("delta", "analysis before tool. "),
            agent_tool_event(),
            chat_event("delta", "answer after tool"),
            chat_event("final", "analysis before tool. answer after tool"),
        ],
        false,
    )
    .await;

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
            run_id: "async-agent-tool-run".to_string(),
            session_key: "async-agent-tool-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::AfterLastToolCall,
            caller_wait_mode: None,
            organization_code: None,
        })
        .await
        .unwrap();
    run_port.wait_for_event_unregister("async-agent-tool-run").await;

    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-agent-tool-run".to_string(),
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
        Some("answer after tool")
    );
}

#[tokio::test]
async fn async_run_after_last_tool_call_mode_uses_final_when_agent_tool_has_no_followup_text() {
    let (service, run_port, _run_store) = build_run_service(
        vec![
            chat_event("delta", "analysis before tool. "),
            agent_tool_event(),
            chat_event("final", "analysis before tool."),
        ],
        false,
    )
    .await;

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
            run_id: "async-agent-tool-final-run".to_string(),
            session_key: "async-agent-tool-final-session".to_string(),
            timeout_ms: 1_000,
            client: Some("contract-test".to_string()),
            response_mode: ChatResponseMode::AfterLastToolCall,
            caller_wait_mode: None,
            organization_code: None,
        })
        .await
        .unwrap();
    run_port.wait_for_event_unregister("async-agent-tool-final-run").await;

    let status = A2aChatRunService::get_run(
        service.as_ref(),
        ChatRunQueryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-source".to_string(),
            }),
            run_id: "async-agent-tool-final-run".to_string(),
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
        Some("analysis before tool.")
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
    let service = Arc::new(A2aChat::new(
        bot_delivery,
        run_store,
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
    ));
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

#[tokio::test]
async fn protected_target_requires_friendship_in_a2a_service() {
    let (service, delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("bot-target", "protected", None),
        ],
        vec![],
    )
    .await;

    let err = service.chat(chat_command("bot-target")).await.unwrap_err();

    assert!(matches!(err, ServiceError::NotFriends(bot_ids) if bot_ids == vec!["bot-target"]));
    assert!(delivery.frames().await.is_empty());
}

#[tokio::test]
async fn protected_friend_and_public_target_are_delivered_by_a2a_service() {
    let (service, delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("protected-target", "protected", None),
            ("public-target", "public", None),
        ],
        vec![("bot-source", "protected-target")],
    )
    .await;

    service
        .chat(chat_command("protected-target"))
        .await
        .unwrap();
    let mut public_cmd = chat_command("public-target");
    public_cmd.run_id = Some("run-2".to_string());
    public_cmd.session_key = Some("session-2".to_string());
    service.chat(public_cmd).await.unwrap();

    assert_eq!(delivery.frames().await.len(), 2);
}

#[tokio::test]
async fn private_or_missing_target_is_not_found_before_delivery() {
    // Friends CAN chat with private bots; strangers and missing bots get 404
    let (service, _delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("private-target", "private", None),
        ],
        vec![("bot-source", "private-target")],
    )
    .await;

    // Friends can reach private bots
    assert!(
        service.chat(chat_command("private-target")).await.is_ok(),
        "friends should be able to chat with private bots"
    );
    // Missing bots still return 404
    assert!(matches!(
        service.chat(chat_command("missing-target")).await.unwrap_err(),
        ServiceError::BotNotFound(id) if id == "missing-target"
    ));
}

#[tokio::test]
async fn private_stranger_is_not_found_before_delivery() {
    // Strangers cannot reach private bots
    let (service, delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("private-target", "private", None),
        ],
        vec![], // no friendship
    )
    .await;
    assert!(matches!(
        service.chat(chat_command("private-target")).await.unwrap_err(),
        ServiceError::BotNotFound(id) if id == "private-target"
    ));
    assert!(delivery.frames().await.is_empty());
}

#[tokio::test]
async fn authenticated_staff_must_own_source_bot_when_present() {
    let (service, delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-2")),
            ("bot-target", "public", None),
        ],
        vec![],
    )
    .await;

    let err = service.chat(chat_command("bot-target")).await.unwrap_err();

    assert!(matches!(err, ServiceError::Unauthorized(_)));
    assert!(delivery.frames().await.is_empty());
}

#[tokio::test]
async fn disconnected_target_maps_to_service_not_connected_error() {
    let bot_delivery = Arc::new(RecordingDelivery::new(false));
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert("bot-source", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;
    let service = A2aChat::new(
        bot_delivery.clone(),
        run_store,
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
    );

    let err = service.chat(chat_command("bot-target")).await.unwrap_err();

    assert!(matches!(
        err,
        ServiceError::BotNotConnected(id) if id == "bot-target"
    ));
    assert!(bot_delivery.frames().await.is_empty());
}

#[test]
fn a2a_event_parser_matches_existing_chat_event_shapes() {
    let raw = serde_json::json!({
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

    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event(&raw, &mut accumulated),
        DrainOutcome::Final
    );
    assert_eq!(accumulated, "done");
}

#[test]
fn a2a_event_parser_treats_final_as_snapshot_for_full_response() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "hello"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "hello"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "hello");
}

#[test]
fn a2a_event_parser_treats_block_joined_final_as_snapshot_for_full_response() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "visible part 1"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "visible part 2"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "visible part 1\n\nvisible part 2"),
            &mut accumulated,
            ChatResponseMode::Full,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "visible part 1\n\nvisible part 2");
}

#[test]
fn a2a_event_parser_keeps_after_tool_window_when_final_snapshot_is_full_response() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "analysis before tool. "),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &tool_call_event("tool_call_start"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "analysis before tool. answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "answer after tool");
}

#[test]
fn a2a_event_parser_clears_after_agent_tool_event() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "analysis before tool. "),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &agent_tool_event(),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "analysis before tool. answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "answer after tool");
}

#[test]
fn a2a_event_parser_deduplicates_repeated_after_tool_delta_before_final_snapshot() {
    let mut accumulated = String::new();
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "analysis before tool. "),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &agent_tool_event(),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("delta", "answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Continue
    );
    assert_eq!(
        drain_chat_event_with_mode(
            &chat_event("final", "analysis before tool. answer after tool"),
            &mut accumulated,
            ChatResponseMode::AfterLastToolCall,
        ),
        DrainOutcome::Final
    );

    assert_eq!(accumulated, "answer after tool");
}

#[tokio::test]
async fn organization_scoped_a2a_allows_public_and_protected_without_friendship() {
    let service = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("public-target", "public", None),
            ("protected-target", "protected", None),
        ],
        vec![],
        vec!["bot-source", "public-target", "protected-target"],
    )
    .await;

    let mut public_cmd = scoped_chat_command("public-target");
    public_cmd.run_id = Some("org-public-run".to_string());
    public_cmd.session_key = Some("org-public-session".to_string());
    assert!(service.chat(public_cmd).await.is_ok());
    let mut protected_cmd = scoped_chat_command("protected-target");
    protected_cmd.run_id = Some("org-protected-run".to_string());
    protected_cmd.session_key = Some("org-protected-session".to_string());
    assert!(service.chat(protected_cmd).await.is_ok());
}

#[tokio::test]
async fn organization_scoped_a2a_allows_private_friends_and_hides_private_strangers() {
    let friend_service = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("private-friend", "private", None),
        ],
        vec![("bot-source", "private-friend")],
        vec!["bot-source", "private-friend"],
    )
    .await;
    assert!(friend_service.chat(scoped_chat_command("private-friend")).await.is_ok());

    let stranger_service = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("private-stranger", "private", None),
        ],
        vec![],
        vec!["bot-source", "private-stranger"],
    )
    .await;
    assert!(matches!(
        stranger_service.chat(scoped_chat_command("private-stranger")).await.unwrap_err(),
        ServiceError::BotNotFound(id) if id == "private-stranger"
    ));
}

#[tokio::test]
async fn organization_scoped_a2a_rejects_sender_or_target_outside_effective_membership() {
    let sender_rejected = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("bot-target", "public", None),
        ],
        vec![],
        vec!["bot-target"],
    )
    .await;
    assert!(matches!(
        sender_rejected.chat(scoped_chat_command("bot-target")).await.unwrap_err(),
        ServiceError::Forbidden(message) if message == "organization_member_required"
    ));

    let target_rejected = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("bot-target", "public", None),
        ],
        vec![("bot-source", "bot-target")],
        vec!["bot-source"],
    )
    .await;
    assert!(matches!(
        target_rejected.chat(scoped_chat_command("bot-target")).await.unwrap_err(),
        ServiceError::Forbidden(message) if message == "organization_member_required"
    ));
}

#[tokio::test]
async fn organization_scoped_a2a_rejects_disabled_organization_before_friendship() {
    let bot_delivery = Arc::new(RecordingDelivery::new(true));
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry.insert("bot-source", "public", Some("owner-1")).await;
    registry.insert("private-friend", "private", None).await;
    let friend = Arc::new(StaticFriendCoreService::new(vec![("bot-source", "private-friend")]));
    let organization = Arc::new(StaticOrganizationCoreService::new(vec!["bot-source", "private-friend"]).disabled());
    let service = A2aChat::new(bot_delivery, run_store, 30_000, registry, friend)
        .with_organization(organization);

    assert!(matches!(
        service.chat(scoped_chat_command("private-friend")).await.unwrap_err(),
        ServiceError::Forbidden(message) if message == "organization_disabled"
    ));
}

#[derive(Default)]
struct MemoryRegistry {
    bots: RwLock<HashMap<String, RegisteredBot>>,
}

impl MemoryRegistry {
    async fn insert(&self, bot_id: &str, visibility: &str, created_by: Option<&str>) {
        self.insert_named(bot_id, bot_id, visibility, created_by).await;
    }

    async fn insert_named(
        &self,
        bot_id: &str,
        name: &str,
        visibility: &str,
        created_by: Option<&str>,
    ) {
        self.bots.write().await.insert(
            bot_id.to_string(),
            RegisteredBot {
                bot_uuid: bot_id.to_string(),
                capabilities: BotCapabilities {
                    name: Some(name.to_string()),
                    visibility: visibility.to_string(),
                    ..BotCapabilities::default()
                },
                dynamic_status: BotDynamicStatus::default(),
                env: None,
                created_by: created_by.map(str::to_string),
                actor_kind: ActorKind::Bot,
                status: ActorStatus::Online,
            },
        );
    }
}

#[async_trait::async_trait]
impl BotRegistryCoreService for MemoryRegistry {
    async fn register(&self, bot_id: String, capabilities: BotCapabilities) -> ServiceResult<()> {
        self.bots.write().await.insert(
            bot_id.clone(),
            RegisteredBot {
                bot_uuid: bot_id,
                capabilities,
                dynamic_status: BotDynamicStatus::default(),
                env: None,
                created_by: None,
                actor_kind: ActorKind::Bot,
                status: ActorStatus::Online,
            },
        );
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.read().await.get(bot_id).cloned()
    }

    async fn get_agent_credentials(&self, bot_id: &str) -> Option<AgentCredentials> {
        // Synthetic credentials for tests so the outbound interceptor chain
        // runs through to BlockingInterceptor / SecurityInterceptor. Mirrors
        // the helper in message_flow_contract_support.rs.
        if self.bots.read().await.contains_key(bot_id) {
            Some(AgentCredentials {
                agent_code: Some(format!("test-agent-{bot_id}")),
                agent_token: Some(format!("test-token-{bot_id}")),
            })
        } else {
            None
        }
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.read().await.values().cloned().collect()
    }

    async fn list_bots_by_creator(&self, created_by: &str) -> Vec<RegisteredBot> {
        self.bots
            .read()
            .await
            .values()
            .filter(|bot| bot.created_by.as_deref() == Some(created_by))
            .cloned()
            .collect()
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, bot_id: &str) -> bool {
        self.bots.write().await.remove(bot_id).is_some()
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, bot_id: &str, visibility: &str) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().await.get_mut(bot_id) {
            bot.capabilities.visibility = visibility.to_string();
        }
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn has_been_onboarded(&self, bot_id: &str) -> bool {
        self.bots.read().await.contains_key(bot_id)
    }

    async fn save_created_by(
        &self,
        bot_id: &str,
        created_by: &str,
        overwrite: bool,
    ) -> ServiceResult<()> {
        if let Some(bot) = self.bots.write().await.get_mut(bot_id) {
            if overwrite || bot.created_by.is_none() {
                bot.created_by = Some(created_by.to_string());
            }
        }
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, bot_id: &str) -> bool {
        self.bots.read().await.contains_key(bot_id)
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Ok(())
    }

    async fn list_connected(&self) -> Vec<String> {
        self.bots.read().await.keys().cloned().collect()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }
}


#[derive(Clone)]
struct StaticOrganizationCoreService {
    members: HashSet<String>,
    disabled: bool,
}

impl StaticOrganizationCoreService {
    fn new(members: Vec<&str>) -> Self {
        Self {
            members: members.into_iter().map(str::to_string).collect(),
            disabled: false,
        }
    }

    fn disabled(mut self) -> Self {
        self.disabled = true;
        self
    }

    fn member(bot_uuid: &str) -> OrganizationMember {
        OrganizationMember {
            env: "test".to_string(),
            organization_code: "promo-2026".to_string(),
            bot_uuid: bot_uuid.to_string(),
            role: None,
            disabled: false,
            created_at: 1,
            updated_at: 1,
        }
    }

    fn organization() -> Organization {
        Organization {
            env: "test".to_string(),
            code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: None,
            managing_provider_id: "provider-a".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        }
    }
}

#[async_trait::async_trait]
impl OrganizationCoreService for StaticOrganizationCoreService {
    async fn create(&self, _managing_provider_id: &str, _code: &str, _name: &str, _description: Option<&str>) -> ServiceResult<Organization> {
        Ok(Self::organization())
    }

    async fn get_for_manager(&self, _managing_provider_id: &str, _code: &str) -> ServiceResult<Organization> {
        Ok(Self::organization())
    }

    async fn list_for_manager(&self, _managing_provider_id: &str, _include_disabled: bool) -> ServiceResult<Vec<Organization>> {
        Ok(vec![Self::organization()])
    }

    async fn update_for_manager(&self, _managing_provider_id: &str, _code: &str, _name: Option<&str>, _description: Option<Option<&str>>, _disabled: Option<bool>) -> ServiceResult<Organization> {
        Ok(Self::organization())
    }

    async fn put_member(&self, _managing_provider_id: &str, _organization_code: &str, bot_uuid: &str, _role: Option<&str>) -> ServiceResult<OrganizationMember> {
        Ok(Self::member(bot_uuid))
    }

    async fn delete_member(&self, _managing_provider_id: &str, _organization_code: &str, _bot_uuid: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_member_for_manager(&self, _managing_provider_id: &str, _organization_code: &str, bot_uuid: &str) -> ServiceResult<Option<OrganizationMember>> {
        Ok(self.members.contains(bot_uuid).then(|| Self::member(bot_uuid)))
    }

    async fn list_members_for_manager(&self, _managing_provider_id: &str, _organization_code: &str, _include_disabled: bool, _role: Option<&str>) -> ServiceResult<Vec<OrganizationMember>> {
        Ok(self.members.iter().map(|bot| Self::member(bot)).collect())
    }

    async fn candidate_bots(&self, _managing_provider_id: &str, _query: OrganizationCandidateQuery) -> ServiceResult<Vec<OrganizationCandidateBot>> {
        Ok(Vec::new())
    }

    async fn require_effective_member(&self, _organization_code: &str, bot_uuid: &str) -> ServiceResult<OrganizationMember> {
        if self.disabled {
            return Err(ServiceError::Forbidden("organization_disabled".to_string()));
        }
        if self.members.contains(bot_uuid) {
            Ok(Self::member(bot_uuid))
        } else {
            Err(ServiceError::Forbidden("organization_member_required".to_string()))
        }
    }

    async fn list_effective_members(&self, _organization_code: &str, _role: Option<&str>) -> ServiceResult<Vec<OrganizationMember>> {
        if self.disabled {
            return Err(ServiceError::Forbidden("organization_disabled".to_string()));
        }
        Ok(self.members.iter().map(|bot| Self::member(bot)).collect())
    }

    async fn authorize_pair(&self, organization_code: &str, sender_bot_uuid: &str, target_bot_uuid: &str) -> ServiceResult<AuthorizedOrganizationPair> {
        let sender = self.require_effective_member(organization_code, sender_bot_uuid).await?;
        let target = self.require_effective_member(organization_code, target_bot_uuid).await?;
        Ok(AuthorizedOrganizationPair {
            organization: Self::organization(),
            sender,
            target,
        })
    }
}

#[derive(Default)]
struct StaticFriendCoreService {
    friendships: HashSet<(String, String)>,
}

impl StaticFriendCoreService {
    fn new(friendships: Vec<(&str, &str)>) -> Self {
        Self {
            friendships: friendships
                .into_iter()
                .map(|(a, b)| normalized_friendship(a, b))
                .collect(),
        }
    }
}

#[async_trait::async_trait]
impl FriendCoreService for StaticFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.friendships
            .iter()
            .filter_map(|(a, b)| {
                if a == bot_id {
                    Some(b.clone())
                } else if b == bot_id {
                    Some(a.clone())
                } else {
                    None
                }
            })
            .collect()
    }

    async fn are_friends(&self, bot_a: &str, bot_b: &str) -> bool {
        self.friendships
            .contains(&normalized_friendship(bot_a, bot_b))
    }

    async fn are_all_friends(&self, bot_id: &str, others: &[String]) -> ServiceResult<()> {
        let missing: Vec<String> = others
            .iter()
            .filter(|other| {
                !self
                    .friendships
                    .contains(&normalized_friendship(bot_id, other))
            })
            .cloned()
            .collect();
        if missing.is_empty() {
            Ok(())
        } else {
            Err(ServiceError::NotFriends(missing))
        }
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

fn normalized_friendship(a: &str, b: &str) -> (String, String) {
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

struct RecordingDelivery {
    connected: bool,
    frames: RwLock<Vec<BcsFrame>>,
}

impl RecordingDelivery {
    fn new(connected: bool) -> Self {
        Self {
            connected,
            frames: RwLock::new(Vec::new()),
        }
    }

    async fn frames(&self) -> Vec<BcsFrame> {
        self.frames.read().await.clone()
    }
}

struct RecordingRunPort {
    events: Vec<String>,
    keep_open_after_send: bool,
    registrations: Mutex<Vec<(String, String, Option<String>, Option<String>)>>,
    event_unregistered: Mutex<Vec<String>>,
    cleanup_unregistered: Mutex<Vec<String>>,
    open_senders: RwLock<HashMap<String, mpsc::Sender<String>>>,
    notify: Notify,
}

impl RecordingRunPort {
    fn new(events: Vec<String>, keep_open_after_send: bool) -> Self {
        Self {
            events,
            keep_open_after_send,
            registrations: Mutex::new(Vec::new()),
            event_unregistered: Mutex::new(Vec::new()),
            cleanup_unregistered: Mutex::new(Vec::new()),
            open_senders: RwLock::new(HashMap::new()),
            notify: Notify::new(),
        }
    }

    async fn event_unregistered(&self) -> Vec<String> {
        self.event_unregistered.lock().await.clone()
    }

    async fn cleanup_unregistered(&self) -> Vec<String> {
        self.cleanup_unregistered.lock().await.clone()
    }

    async fn registrations(&self) -> Vec<(String, String, Option<String>, Option<String>)> {
        self.registrations.lock().await.clone()
    }

    async fn send_event(&self, run_id: &str, event: String) {
        let sender = self
            .open_senders
            .read()
            .await
            .get(run_id)
            .cloned()
            .unwrap_or_else(|| panic!("{run_id} is not registered"));
        sender.send(event).await.unwrap();
    }

    async fn wait_for_event_unregister(&self, run_id: &str) {
        let timeout = tokio::time::sleep(Duration::from_secs(1));
        tokio::pin!(timeout);
        loop {
            if self
                .event_unregistered
                .lock()
                .await
                .iter()
                .any(|recorded| recorded == run_id)
            {
                return;
            }
            tokio::select! {
                _ = self.notify.notified() => {}
                _ = &mut timeout => panic!("timed out waiting for {run_id} to unregister"),
            }
        }
    }
}

#[async_trait::async_trait]
impl ChatRunEventPort for RecordingRunPort {
    async fn register(
        &self,
        run_id: String,
        session_key: String,
        sender: mpsc::Sender<String>,
        source: Option<String>,
        from: Option<String>,
    ) {
        self.registrations
            .lock()
            .await
            .push((run_id.clone(), session_key, source, from));
        for event_json in &self.events {
            sender.send(event_json.clone()).await.unwrap();
        }
        if self.keep_open_after_send {
            self.open_senders.write().await.insert(run_id, sender);
        }
    }

    async fn unregister(&self, run_id: &str) {
        self.open_senders.write().await.remove(run_id);
        self.event_unregistered
            .lock()
            .await
            .push(run_id.to_string());
        self.notify.notify_waiters();
    }
}

#[async_trait::async_trait]
impl ChatRunCleanupPort for RecordingRunPort {
    async fn unregister(&self, run_id: &str) {
        self.open_senders.write().await.remove(run_id);
        self.cleanup_unregistered
            .lock()
            .await
            .push(run_id.to_string());
        self.notify.notify_waiters();
    }
}

#[async_trait::async_trait]
impl BotDeliveryPort for RecordingDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        self.connected
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        self.frames.write().await.push(cmd.frame);
        Ok(BotDeliveryResult {
            target_bot_id,
            delivered: self.connected,
            error: None,
        })
    }
}

struct A2aBlockingInterceptor;

#[async_trait::async_trait]
impl MessageInterceptor for A2aBlockingInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Block(BlockReason {
            interceptor_id: "test-block".to_string(),
            code: "blocked".to_string(),
            message: "a2a chat blocked by test".to_string(),
            user_visible: true,
        })
    }
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
