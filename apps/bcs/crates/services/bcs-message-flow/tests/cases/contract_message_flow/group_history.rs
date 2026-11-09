use super::*;

#[derive(Default)]
struct RecordingHistoryBotRequest {
    calls: RwLock<Vec<(String, String, serde_json::Value, u64)>>,
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for RecordingHistoryBotRequest {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let bot_uuid = target.bot_id().to_string();
        self.calls
            .write()
            .await
            .push((bot_uuid.clone(), method.to_string(), params, timeout_ms));
        if bot_uuid == "bot-observer" {
            return Err("request_failed: disconnected".to_string());
        }
        Ok(json!({
            "messages": [
                {
                    "id": "hist-context",
                    "role": "assistant",
                    "content": "[BCS Context]\n- 消息来自: Observer(bot-observer)\n\n[消息内容]\ncontext body",
                    "timestamp": 7
                },
                {
                    "id": "hist-tool",
                    "role": "tool_result",
                    "content": [{"type": "text", "text": "tool output"}],
                    "timestamp": 8,
                    "toolName": "lookup",
                    "toolCallId": "call-1",
                    "isError": false
                },
                {
                    "id": "hist-call",
                    "role": "assistant",
                    "content": [{"type": "toolCall", "name": "search", "id": "call-2", "arguments": {"q": "bcs"}}],
                    "timestamp": 9,
                    "stopReason": "toolUse",
                    "historyMeta": {"source": "openclaw"}
                }
            ]
        }))
    }
}

struct NewerOnlyHistoryBotRequest;

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for NewerOnlyHistoryBotRequest {
    async fn send_history_request(
        &self,
        _target: BotDeliveryTarget,
        _method: &str,
        _params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        Ok(json!({
            "messages": [
                {
                    "id": "hist-newer",
                    "role": "assistant",
                    "content": "newer than cursor",
                    "timestamp": 20
                }
            ]
        }))
    }
}

#[derive(Default)]
struct RecordingTargetHistoryBotRequest {
    calls: RwLock<Vec<(BotDeliveryTarget, String)>>,
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for RecordingTargetHistoryBotRequest {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        _params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        self.calls.write().await.push((target, method.to_string()));
        Ok(json!({
            "messages": [
                {"id": "provider-hist-1", "role": "assistant", "content": "provider history", "timestamp": 1}
            ]
        }))
    }
}

#[tokio::test]
async fn group_history_falls_back_to_driver_and_normalizes_bot_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();
    let history_request = Arc::new(RecordingHistoryBotRequest::default());
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        history_request.clone(),
    )
    .with_bot_authority_hook(Some(staff_authority(&[("bot-driver", "1"), ("bot-observer", "1")])));

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: Some(10),
        })
        .await
        .unwrap();

    assert_eq!(result.group_id, "group-1");
    assert_eq!(result.limit, 500);
    assert_eq!(result.messages.len(), 3);
    assert_eq!(result.messages[0].id, "hist-context");
    assert_eq!(result.messages[0].sender, "bot-observer");
    assert_eq!(result.messages[0].bot_name.as_deref(), Some("Observer"));
    assert_eq!(result.messages[0].content, "context body");
    assert_eq!(result.messages[0].role, MessageRole::User);
    assert_eq!(
        result.messages[1].metadata,
        Some(json!({
            "tool_name": "lookup",
            "tool_call_id": "call-1",
            "is_error": false,
            "result": "tool output"
        }))
    );
    assert_eq!(
        result.messages[2].metadata,
        Some(json!({
            "stop_reason": "toolUse",
            "tool_calls": [{"tool_name": "search", "tool_call_id": "call-2", "arguments": {"q": "bcs"}}]
        }))
    );
    assert_eq!(
        result.messages[2].history_meta,
        Some(json!({"source": "openclaw"}))
    );

    let calls = history_request.calls.read().await.clone();
    assert_eq!(calls.len(), 2);

    let observer_call = calls
        .iter()
        .find(|c| c.0 == "bot-observer")
        .expect("observer call");
    assert_eq!(observer_call.1, "chat.history");
    assert_eq!(
        observer_call.2,
        json!({"session_key": "group-1", "bcs_group_id": "group-1", "limit": 500, "before": 10})
    );
    assert_eq!(observer_call.3, 30_000);

    let driver_call = calls
        .iter()
        .find(|c| c.0 == "bot-driver")
        .expect("driver call");
    assert_eq!(driver_call.1, "chat.history");
    assert_eq!(
        driver_call.2,
        json!({"session_key": "group-1", "bcs_group_id": "group-1", "limit": 500, "before": 10})
    );
    assert_eq!(driver_call.3, 30_000);
}

#[tokio::test]
async fn history_requests_provider_target_without_ws_connection() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .insert_named_actor("bot-provider", "Provider")
        .await;
    support
        .registry
        .save_created_by("bot-provider", "1", true)
        .await
        .unwrap();
    support
        .registry
        .set_delivery_target(
            "bot-provider",
            support::FakeRegistryService::provider_target("bot-provider"),
        )
        .await;
    let mut provider = Participant::bot("bot-provider", ParticipantRole::Driver);
    provider.bot_name = Some("Provider".to_string());
    let group = Group::new(
        "group-provider",
        "bot-provider",
        vec![
            provider,
            Participant {
                bot_uuid: "human_1".to_string(),
                bot_name: Some("Human One".to_string()),
                kind: None,
                role: ParticipantRole::Observer,
                actor_kind: ActorKind::Human,
                mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    },
        ],
    );
    support.group.upsert(group).await.unwrap();

    let history_request = Arc::new(RecordingTargetHistoryBotRequest::default());
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        history_request.clone(),
    )
    .with_bot_authority_hook(Some(staff_authority(&[("bot-driver", "1"), ("bot-provider", "1")])));

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-provider".to_string(),
            view_bot_id: Some("bot-provider".to_string()),
            limit: 20,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages[0].sender, "bot-provider");
    assert_eq!(result.messages[0].content, "provider history");
    let calls = history_request.calls.read().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].1, "chat.history");
    assert!(calls[0].0.is_http_provider());
}

#[tokio::test]
async fn group_history_falls_back_to_store_when_bot_window_has_no_older_messages() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .group
        .add_message(
            "group-1",
            GroupMessage {
                id: "stored-old".to_string(),
                timestamp: 10,
                sender: "bot-driver".to_string(),
                content: "[from:Driver]stored old".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
                attachments: None,
            },
        )
        .await
        .unwrap();
    support
        .group
        .add_message(
            "group-1",
            GroupMessage {
                id: "stored-new".to_string(),
                timestamp: 20,
                sender: "bot-observer".to_string(),
                content: "[from:Observer]stored new".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
                attachments: None,
            },
        )
        .await
        .unwrap();

    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        Arc::new(NewerOnlyHistoryBotRequest),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: 1,
            before: Some(20),
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].id, "stored-old");
    assert_eq!(result.messages[0].content, "stored old");
    assert_eq!(result.messages[0].bot_name.as_deref(), Some("Driver"));
    assert_eq!(result.before, Some(20));
    assert_eq!(result.next_before, Some(10));
}

#[tokio::test]
async fn group_history_expands_legacy_unbounded_limit_for_bot_request() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let history_request = Arc::new(RecordingHistoryBotRequest::default());
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        history_request.clone(),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: u64::MAX,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.limit, u64::MAX);
    let calls = history_request.calls.read().await.clone();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, "bot-driver");
    assert_eq!(
        calls[0].2,
        json!({"session_key": "group-1", "bcs_group_id": "group-1", "limit": 1000})
    );
}

#[tokio::test]
async fn group_history_prefers_high_priority_even_when_low_priority_finishes_first() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let mut delays = HashMap::new();
    delays.insert("bot-observer".to_string(), 300); // high-priority, finishes later
    delays.insert("bot-driver".to_string(), 50); // low-priority, finishes first

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {"id": "obs-1", "role": "assistant", "content": "observer msg", "timestamp": 1}
            ]
        })),
    );
    responses.insert(
        "bot-driver".to_string(),
        Ok(json!({
            "messages": [
                {"id": "drv-1", "role": "assistant", "content": "driver msg", "timestamp": 2}
            ]
        })),
    );

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        delays, responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request.clone(),
    )
    .with_bot_authority_hook(Some(staff_authority(&[("bot-driver", "1"), ("bot-observer", "1")])));

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "observer msg");
}

#[tokio::test]
async fn group_history_falls_back_to_driver_when_view_bot_fails() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let mut delays = HashMap::new();
    delays.insert("bot-observer".to_string(), 50);
    delays.insert("bot-driver".to_string(), 100);

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Err("observer_timeout".to_string()),
    );
    responses.insert(
        "bot-driver".to_string(),
        Ok(json!({
            "messages": [
                {"id": "drv-1", "role": "assistant", "content": "driver fallback", "timestamp": 2}
            ]
        })),
    );

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        delays, responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request.clone(),
    )
    .with_bot_authority_hook(Some(staff_authority(&[("bot-driver", "1"), ("bot-observer", "1")])));

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "driver fallback");
}

#[tokio::test]
async fn group_history_falls_back_to_store_when_all_bots_fail() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let mut delays = HashMap::new();
    delays.insert("bot-observer".to_string(), 50);
    delays.insert("bot-driver".to_string(), 50);

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Err("observer_timeout".to_string()),
    );
    responses.insert("bot-driver".to_string(), Err("driver_timeout".to_string()));

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        delays, responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request.clone(),
    )
    .with_bot_authority_hook(Some(staff_authority(&[("bot-driver", "1"), ("bot-observer", "1")])));

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 0);
}
