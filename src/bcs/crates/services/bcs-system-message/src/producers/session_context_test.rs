//! Unit tests for `SessionContextMessageProducer`.
//! CONFORMANCE_WAIVED: NamedRegistry is a test double, not a production impl.

use std::collections::HashMap;

use async_trait::async_trait;
use bcs_domain::{
    ActorKind, ActorStatus, BotCapabilities, BotDynamicStatus, DeliveryType, Group,
    GroupStrategy, LedgerSummary, Participant, ParticipantRole, RegisteredBot, SystemGroupMessage,
    SystemMessageEvent, CoordinationMode, CoordinationSurface,
};
use bcs_service_api::{
    AgentCredentials, BotDeliveryTarget, BotRegistryCoreService, RedactedToken, ServiceResult,
    SystemMessageProducerService,
};

use super::session_context::SessionContextMessageProducer;

struct NamedRegistry {
    bots: HashMap<String, RegisteredBot>,
    surfaces: HashMap<String, CoordinationSurface>,
    http_providers: std::collections::HashSet<String>,
}

impl NamedRegistry {
    fn new(entries: &[(&str, &str, Option<&str>)]) -> Self {
        let bots = entries
            .iter()
            .map(|(bot_id, name, summary)| {
                (
                    (*bot_id).to_string(),
                    RegisteredBot {
                        bot_uuid: (*bot_id).to_string(),
                        capabilities: BotCapabilities {
                            name: Some((*name).to_string()),
                            summary: summary.map(str::to_string),
                            visibility: "protected".to_string(),
                            ..Default::default()
                        },
                        dynamic_status: BotDynamicStatus::default(),
                        env: None,
                        created_by: None,
                        actor_kind: ActorKind::Bot,
                        status: ActorStatus::Online,
                    },
                )
            })
            .collect();
        Self {
            bots,
            surfaces: HashMap::new(),
            http_providers: std::collections::HashSet::new(),
        }
    }

    fn with_surface(mut self, bot_id: &str, surface: CoordinationSurface) -> Self {
        self.surfaces.insert(bot_id.to_string(), surface);
        self
    }

    fn with_http_provider(mut self, bot_id: &str) -> Self {
        self.http_providers.insert(bot_id.to_string());
        self
    }
}

#[async_trait]
impl BotRegistryCoreService for NamedRegistry {
    async fn register(
        &self,
        _bot_id: String,
        _capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.get(bot_id).cloned()
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn resolve_coordination_surface(
        &self,
        bot_id: &str,
    ) -> ServiceResult<CoordinationSurface> {
        Ok(self
            .surfaces
            .get(bot_id)
            .cloned()
            .unwrap_or_else(CoordinationSurface::legacy_upstream))
    }

    async fn resolve_delivery_target(
        &self,
        bot_id: &str,
    ) -> ServiceResult<BotDeliveryTarget> {
        if self.http_providers.contains(bot_id) {
            return Ok(BotDeliveryTarget::HttpProvider {
                bot_id: bot_id.to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "ref".to_string(),
                webhook_url: "https://provider.example.com/bcs/webhook".to_string(),
                bcs_to_provider_token: RedactedToken::new("secret"),
                protocol_version: "2.0".to_string(),
            });
        }
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.values().cloned().collect()
    }

    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        Vec::new()
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

    async fn unregister(&self, _bot_id: &str) -> bool {
        false
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(
        &self,
        _bot_id: &str,
        _caps: &BotCapabilities,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(
        &self,
        _bot_id: &str,
        _visibility: &str,
    ) -> ServiceResult<()> {
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        false
    }

    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> ServiceResult<()> {
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

    async fn reconnect_streaming(
        &self,
        _existing_token: String,
    ) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, _token: String) -> String {
        String::new()
    }
}

async fn manager_worker_session_context_messages() -> (String, String, Vec<SystemGroupMessage>) {
    manager_worker_session_context_messages_with_ledger(None).await
}

async fn manager_worker_session_context_messages_with_ledger(
    task_ledger: Option<LedgerSummary>,
) -> (String, String, Vec<SystemGroupMessage>) {
    let manager_id = "20260416_a5clr6ig:12345678";
    let worker_id = "20260528_vobmrqo6:12345678";

    let mut manager = Participant::bot(manager_id, ParticipantRole::Manager);
    manager.bot_name = Some(manager_id.to_string());
    let mut worker = Participant::bot(worker_id, ParticipantRole::Worker);
    worker.bot_name = Some(worker_id.to_string());

    let mut group = Group::new(
        "851c7a6a-42bc-4be2-8785-1106ef4393a0",
        manager_id,
        vec![manager.clone(), worker.clone()],
    );
    group.group_strategy = GroupStrategy::ManagerWorker;

    let participants = vec![manager, worker];
    let registry = NamedRegistry::new(&[
        (manager_id, "Demo Worker的分身", Some("Demo Worker的分身")),
        (worker_id, "Demo Worker测试0528", None),
    ]);
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: format!("{}:7c18e4be", group.id),
        reason: "协作任务".to_string(),
        session_input: None,
        task_ledger,
        driver_delivery: None,
    };

    let (messages, _) = SessionContextMessageProducer
        .produce(&event, &group, &registry, &participants)
        .await;

    (manager_id.to_string(), worker_id.to_string(), messages)
}

#[tokio::test]
async fn manager_worker_session_context_backfills_placeholder_names() {
    let (manager_id, _worker_id, messages) = manager_worker_session_context_messages().await;

    assert_eq!(messages.len(), 2);
    let manager_message = messages
        .iter()
        .find(|message| message.recipients == vec![manager_id.clone()])
        .expect("manager receives context");
    assert_eq!(manager_message.delivery_type, DeliveryType::Send);
    assert!(manager_message.message.contains(
        "- 名称: Demo Worker的分身 | ID: 20260416_a5clr6ig:12345678 | 角色: manager — Demo Worker的分身"
    ));
    assert!(manager_message.message.contains(
        "- 名称: Demo Worker测试0528 | ID: 20260528_vobmrqo6:12345678 | 角色: worker"
    ));
    assert!(!manager_message.message.contains(
        "- 名称: 20260528_vobmrqo6:12345678 | ID: 20260528_vobmrqo6:12345678"
    ));
}

#[tokio::test]
async fn manager_worker_manager_reminder_lists_only_manager_tools() {
    let (manager_id, _worker_id, messages) = manager_worker_session_context_messages().await;

    let manager_message = messages
        .iter()
        .find(|message| message.recipients == vec![manager_id.clone()])
        .expect("manager receives context");

    assert!(manager_message.message.contains("bcs_assign_task"));
    assert!(manager_message.message.contains("bcs_task_complete"));
    assert!(manager_message.message.contains(
        "[协同提醒] 本群为任务群，你是主 Bot。派发子任务用 bcs_assign_task(target_bot, message)"
    ));
    assert!(manager_message.message.contains("不要用引擎自带的发送工具向群里发消息。"));
    assert!(!manager_message.message.contains("bcs_send_task_message"));
}

#[tokio::test]
async fn manager_worker_context_uses_recipient_coordination_surface() {
    let manager_id = "manager-bot";
    let native_mcp_worker_id = "native-mcp-worker";
    let native_tool_worker_id = "native-tool-worker";
    let legacy_worker_id = "legacy-worker";

    let mut manager = Participant::bot(manager_id, ParticipantRole::Manager);
    manager.bot_name = Some("Manager".to_string());
    let mut native_mcp_worker = Participant::bot(native_mcp_worker_id, ParticipantRole::Worker);
    native_mcp_worker.bot_name = Some("Native MCP Worker".to_string());
    let mut native_tool_worker = Participant::bot(native_tool_worker_id, ParticipantRole::Worker);
    native_tool_worker.bot_name = Some("Native Tool Worker".to_string());
    let mut legacy_worker = Participant::bot(legacy_worker_id, ParticipantRole::Worker);
    legacy_worker.bot_name = Some("Legacy Worker".to_string());

    let mut group = Group::new(
        "group-platform-surface",
        manager_id,
        vec![
            manager.clone(),
            native_mcp_worker.clone(),
            native_tool_worker.clone(),
            legacy_worker.clone(),
        ],
    );
    group.group_strategy = GroupStrategy::ManagerWorker;
    let participants = group.participants.clone();
    let registry = NamedRegistry::new(&[
        (manager_id, "Manager", Some("manager")),
        (native_mcp_worker_id, "Native MCP Worker", None),
        (native_tool_worker_id, "Native Tool Worker", None),
        (legacy_worker_id, "Legacy Worker", None),
    ])
    .with_surface(
        manager_id,
        CoordinationSurface {
            mode: CoordinationMode::McporterMcp,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: Some("mcporter".to_string()),
        },
    )
    .with_surface(
        native_mcp_worker_id,
        CoordinationSurface {
            mode: CoordinationMode::NativeMcp,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: None,
        },
    )
    .with_surface(native_tool_worker_id, CoordinationSurface::native_tool());
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: "group-platform-surface:7c18e4be".to_string(),
        reason: "协作任务".to_string(),
        session_input: None,
        task_ledger: None,
        driver_delivery: None,
    };

    let (messages, _) = SessionContextMessageProducer
        .produce(&event, &group, &registry, &participants)
        .await;

    let message_for = |bot_id: &str| {
        messages
            .iter()
            .find(|message| message.recipients == vec![bot_id.to_string()])
            .expect("recipient message")
            .message
            .as_str()
    };
    assert!(message_for(manager_id).contains("mcporter call bcs.bcs_assign_task"));
    assert!(message_for(native_mcp_worker_id).contains("MCP server `bcs`"));
    assert!(message_for(native_mcp_worker_id).contains("`bcs_send_task_message`"));
    assert!(message_for(native_tool_worker_id).contains("原生工具 `bcs_send_task_message`"));
    assert!(!message_for(native_tool_worker_id).contains("MCP server `bcs`"));
    assert!(!message_for(legacy_worker_id).contains("mcporter call"));
    assert!(!message_for(legacy_worker_id).contains("MCP server `bcs`"));
}

#[tokio::test]
async fn manager_worker_worker_reminder_lists_only_worker_tool() {
    let (_manager_id, worker_id, messages) = manager_worker_session_context_messages().await;

    let worker_message = messages
        .iter()
        .find(|message| message.recipients == vec![worker_id.clone()])
        .expect("worker receives context");

    assert!(worker_message.message.contains("bcs_send_task_message"));
    assert!(worker_message.message.contains(
        "[协同提醒] 本群为任务群，你是子 Bot。收到主 Bot 派发的任务后直接处理并回复"
    ));
    assert!(worker_message.message.contains("不要用引擎自带的发送工具向群里发消息。"));
    assert!(!worker_message.message.contains("bcs_assign_task"));
    assert!(!worker_message.message.contains("bcs_task_complete"));
}

#[tokio::test]
async fn manager_worker_manager_reminder_includes_task_ledger_status() {
    let (manager_id, worker_id, messages) = manager_worker_session_context_messages_with_ledger(
        Some(LedgerSummary {
            pending: vec!["B".to_string(), "C".to_string()],
            replied: vec!["A".to_string()],
            failed: Vec::new(),
            timed_out: Vec::new(),
        }),
    )
    .await;

    let manager_message = messages
        .iter()
        .find(|message| message.recipients == vec![manager_id.clone()])
        .expect("manager receives context");
    assert!(manager_message.message.contains(
        "[任务状态] 待回复: B, C | 已回复: A | 失败: - | 超时: -"
    ));

    let worker_message = messages
        .iter()
        .find(|message| message.recipients == vec![worker_id.clone()])
        .expect("worker receives context");
    assert!(!worker_message.message.contains("[任务状态]"));
}

#[tokio::test]
async fn session_context_produces_no_user_message() {
    // SessionContext intentionally emits no user-facing WS message: the bot
    // context messages are delivered per-recipient and persisted with
    // owner=recipient, but no session-level frontend broadcast is produced.
    let mut driver = Participant::bot("bot-driver", ParticipantRole::Driver);
    driver.bot_name = Some("Driver".to_string());
    let mut peer = Participant::bot("bot-peer", ParticipantRole::Consultant);
    peer.bot_name = Some("Peer".to_string());

    // Chat strategy.
    let mut chat_group = Group::new("group-chat", "bot-driver", vec![driver.clone(), peer.clone()]);
    chat_group.group_strategy = GroupStrategy::Chat;
    let chat_event = SystemMessageEvent::SessionContext {
        group_id: chat_group.id.clone(),
        session_id: format!("{}:7c18e4be", chat_group.id),
        reason: "普通协作".to_string(),
        session_input: Some(serde_json::json!("任务X")),
        task_ledger: None,
        driver_delivery: None,
    };
    let (chat_messages, chat_user_message) = SessionContextMessageProducer
        .produce(&chat_event, &chat_group, &NamedRegistry::new(&[]), &chat_group.participants)
        .await;
    assert!(!chat_messages.is_empty(), "Chat bot context messages still produced");
    assert!(chat_user_message.is_none(), "Chat SessionContext user_message must be None");

    // ManagerWorker strategy.
    let manager_id = "20260416_a5clr6ig:12345678";
    let worker_id = "20260528_vobmrqo6:12345678";
    let mut manager = Participant::bot(manager_id, ParticipantRole::Manager);
    manager.bot_name = Some(manager_id.to_string());
    let mut worker = Participant::bot(worker_id, ParticipantRole::Worker);
    worker.bot_name = Some(worker_id.to_string());
    let mut mw_group = Group::new("851c7a6a-42bc-4be2-8785-1106ef4393a0", manager_id, vec![manager, worker]);
    mw_group.group_strategy = GroupStrategy::ManagerWorker;
    let mw_event = SystemMessageEvent::SessionContext {
        group_id: mw_group.id.clone(),
        session_id: format!("{}:7c18e4be", mw_group.id),
        reason: "协作任务".to_string(),
        session_input: Some(serde_json::json!("执行慢查询审计")),
        task_ledger: Some(LedgerSummary {
            pending: vec!["B".to_string()],
            replied: vec!["A".to_string()],
            failed: Vec::new(),
            timed_out: Vec::new(),
        }),
        driver_delivery: None,
    };
    let (mw_messages, mw_user_message) = SessionContextMessageProducer
        .produce(&mw_event, &mw_group, &NamedRegistry::new(&[]), &mw_group.participants)
        .await;
    assert!(!mw_messages.is_empty(), "MW bot context messages still produced");
    assert!(mw_user_message.is_none(), "MW SessionContext user_message must be None");
}

#[tokio::test]
async fn driver_delivery_defaults_to_send_for_driver() {
    let mut driver = Participant::bot("bot-driver", ParticipantRole::Driver);
    driver.bot_name = Some("Driver".to_string());
    let mut peer = Participant::bot("bot-peer", ParticipantRole::Consultant);
    peer.bot_name = Some("Peer".to_string());
    let mut group = Group::new("group-chat-default", "bot-driver", vec![driver, peer]);
    group.group_strategy = GroupStrategy::Chat;
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: format!("{}:7c18e4be", group.id),
        reason: "普通协作".to_string(),
        session_input: None,
        task_ledger: None,
        driver_delivery: None,
    };
    let (messages, _) = SessionContextMessageProducer
        .produce(&event, &group, &NamedRegistry::new(&[]), &group.participants)
        .await;

    let driver_message = messages
        .iter()
        .find(|m| m.recipients == vec!["bot-driver".to_string()])
        .expect("driver receives context");
    assert_eq!(driver_message.delivery_type, DeliveryType::Send);

    let peer_message = messages
        .iter()
        .find(|m| m.recipients == vec!["bot-peer".to_string()])
        .expect("peer receives context");
    assert_eq!(peer_message.delivery_type, DeliveryType::Inject);

    // Personalized per-bot context is persisted per recipient; it must NOT
    // produce a public (owner = None) record readable by human viewers.
    assert!(messages
        .iter()
        .all(|m| m.persist == bcs_domain::PersistMode::PerRecipient));
}

#[tokio::test]
async fn provider_downlink_group_initializes_driver_with_inject() {
    let mut driver = Participant::bot("bot-driver", ParticipantRole::Driver);
    driver.bot_name = Some("Driver".to_string());
    let mut provider_peer = Participant::bot("bot-provider", ParticipantRole::Consultant);
    provider_peer.bot_name = Some("Provider peer".to_string());
    let mut group = Group::new(
        "group-provider-downlink",
        "bot-driver",
        vec![driver, provider_peer],
    );
    group.group_strategy = GroupStrategy::Chat;
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: format!("{}:7c18e4be", group.id),
        reason: "普通协作".to_string(),
        session_input: None,
        task_ledger: None,
        driver_delivery: None,
    };
    let registry = NamedRegistry::new(&[]).with_http_provider("bot-provider");

    let (messages, _) = SessionContextMessageProducer
        .produce(&event, &group, &registry, &group.participants)
        .await;

    assert!(messages
        .iter()
        .all(|message| message.delivery_type == DeliveryType::Inject));
    let driver_message = messages
        .iter()
        .find(|message| message.recipients == vec!["bot-driver".to_string()])
        .expect("driver receives context");
    assert!(driver_message.message.contains("静默观察"));
}

#[tokio::test]
async fn provider_downlink_respects_explicit_send_driver_override() {
    let mut driver = Participant::bot("bot-driver", ParticipantRole::Driver);
    driver.bot_name = Some("Driver".to_string());
    let mut provider_peer = Participant::bot("bot-provider", ParticipantRole::Consultant);
    provider_peer.bot_name = Some("Provider peer".to_string());
    let mut group = Group::new(
        "group-provider-explicit-send",
        "bot-driver",
        vec![driver, provider_peer],
    );
    group.group_strategy = GroupStrategy::Chat;
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: format!("{}:7c18e4be", group.id),
        reason: "普通协作".to_string(),
        session_input: None,
        task_ledger: None,
        driver_delivery: Some(DeliveryType::Send),
    };
    let registry = NamedRegistry::new(&[]).with_http_provider("bot-provider");

    let (messages, _) = SessionContextMessageProducer
        .produce(&event, &group, &registry, &group.participants)
        .await;

    let driver_message = messages
        .iter()
        .find(|message| message.recipients == vec!["bot-driver".to_string()])
        .expect("driver receives context");
    assert_eq!(driver_message.delivery_type, DeliveryType::Send);
}

#[tokio::test]
async fn driver_delivery_override_injects_to_driver_in_chat_group() {
    let mut driver = Participant::bot("bot-driver", ParticipantRole::Driver);
    driver.bot_name = Some("Driver".to_string());
    let mut peer = Participant::bot("bot-peer", ParticipantRole::Consultant);
    peer.bot_name = Some("Peer".to_string());
    let mut group = Group::new("group-chat-override", "bot-driver", vec![driver, peer]);
    group.group_strategy = GroupStrategy::Chat;
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: format!("{}:7c18e4be", group.id),
        reason: "普通协作".to_string(),
        session_input: None,
        task_ledger: None,
        driver_delivery: Some(DeliveryType::Inject),
    };
    let (messages, _) = SessionContextMessageProducer
        .produce(&event, &group, &NamedRegistry::new(&[]), &group.participants)
        .await;

    let driver_message = messages
        .iter()
        .find(|m| m.recipients == vec!["bot-driver".to_string()])
        .expect("driver receives context");
    assert_eq!(
        driver_message.delivery_type,
        DeliveryType::Inject,
        "driver_delivery=inject must override the driver's default chat.send"
    );

    let peer_message = messages
        .iter()
        .find(|m| m.recipients == vec!["bot-peer".to_string()])
        .expect("peer receives context");
    assert_eq!(peer_message.delivery_type, DeliveryType::Inject);
}

#[tokio::test]
async fn manager_worker_ignores_driver_delivery_override() {
    // ManagerWorker groups intentionally ignore driver_delivery: the manager
    // must actively pick up and dispatch the task, so its context is always
    // delivered via chat.send even when the override asks for inject.
    let manager_id = "20260416_a5clr6ig:12345678";
    let worker_id = "20260528_vobmrqo6:12345678";
    let mut manager = Participant::bot(manager_id, ParticipantRole::Manager);
    manager.bot_name = Some(manager_id.to_string());
    let mut worker = Participant::bot(worker_id, ParticipantRole::Worker);
    worker.bot_name = Some(worker_id.to_string());
    let mut group = Group::new("group-mw-override", manager_id, vec![manager, worker]);
    group.group_strategy = GroupStrategy::ManagerWorker;
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: format!("{}:7c18e4be", group.id),
        reason: "协作任务".to_string(),
        session_input: Some(serde_json::json!("执行慢查询审计")),
        task_ledger: None,
        driver_delivery: Some(DeliveryType::Inject),
    };
    let registry = NamedRegistry::new(&[]).with_http_provider(worker_id);
    let (messages, _) = SessionContextMessageProducer
        .produce(&event, &group, &registry, &group.participants)
        .await;

    let manager_message = messages
        .iter()
        .find(|m| m.recipients == vec![manager_id.to_string()])
        .expect("manager receives context");
    assert_eq!(
        manager_message.delivery_type,
        DeliveryType::Send,
        "ManagerWorker manager always receives chat.send regardless of driver_delivery"
    );

    let worker_message = messages
        .iter()
        .find(|m| m.recipients == vec![worker_id.to_string()])
        .expect("worker receives context");
    assert_eq!(worker_message.delivery_type, DeliveryType::Inject);
}
