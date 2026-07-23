//! Integration tests for `SystemMessageDispatcherImpl::dispatch`.
//! CONFORMANCE_WAIVED: MockDeliveryPort is a test double, not a production impl.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_domain::{
    ActorKind, DeliveryType, Group, GroupKind, GroupStatus, GroupStrategy, Participant,
    ParticipantMode, ParticipantRole, RedactedToken, SystemGroupMessage, SystemMessageEvent,
    SystemMessageEventKind,
};
use bcs_service_api::{
    ActorStatus, AgentCredentials, BotCapabilities, BotDeliveryCommand, BotDeliveryPort,
    BotDeliveryResult, BotDeliveryTarget, BotDynamicStatus, BotRegistryCoreService,
    BotRunContext, BotRunContextPort, DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
    EnsureHumanResult, ProviderStreamGrayList, ProviderTransportPreference, RegisteredBot,
    ServiceError, ServiceResult, SystemMessageDispatcherService, SystemMessageProducerService,
};
use bcs_test_support::NoopFrontendDeliveryPort;
use tokio::sync::RwLock;

use crate::{
    producers::{bot_joined::BotJoinedMessageProducer, session_context::SessionContextMessageProducer},
    SystemMessageDispatcherImpl,
};

/// Mock delivery port that records every command it receives.
struct MockDeliveryPort {
    calls: Mutex<Vec<BotDeliveryCommand>>,
    delivered: bool,
    fail: bool,
}

impl Default for MockDeliveryPort {
    fn default() -> Self {
        Self {
            calls: Mutex::new(Vec::new()),
            delivered: true,
            fail: false,
        }
    }
}

#[async_trait]
impl BotDeliveryPort for MockDeliveryPort {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool {
        true
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let delivered = self.delivered;
        if self.fail {
            self.calls.lock().unwrap().push(cmd);
            return Err(ServiceError::InternalError("delivery failed".to_string()));
        }
        self.calls.lock().unwrap().push(cmd.clone());
        Ok(BotDeliveryResult {
            target_bot_id: cmd.target_bot_id().to_string(),
            delivered,
            error: None,
        })
    }
}

#[derive(Default)]
struct RecordingRunContext {
    contexts: Mutex<HashMap<String, BotRunContext>>,
}

impl RecordingRunContext {
    fn get(&self, run_id: &str) -> Option<BotRunContext> {
        self.contexts.lock().unwrap().get(run_id).cloned()
    }

    fn len(&self) -> usize {
        self.contexts.lock().unwrap().len()
    }
}

#[async_trait]
impl BotRunContextPort for RecordingRunContext {
    async fn put_context(&self, context: BotRunContext) {
        self.contexts
            .lock()
            .unwrap()
            .insert(context.run_id.clone(), context);
    }

    async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
        self.get(run_id)
    }

    async fn try_begin_terminal(&self, _run_id: &str) -> bool {
        true
    }

    async fn mark_terminal(&self, _run_id: &str) -> bool {
        true
    }

    async fn release_terminal(&self, _run_id: &str) {}
}

#[derive(Default)]
struct RecordingMessageRepo {
    appended: RwLock<Vec<bcs_domain::NewMessage>>,
}

impl RecordingMessageRepo {
    async fn appended(&self) -> Vec<bcs_domain::NewMessage> {
        self.appended.read().await.clone()
    }
}

#[async_trait]
impl bcs_service_api::port::repo::MessageRepoPort for RecordingMessageRepo {
    async fn append_message(
        &self,
        msg: bcs_domain::NewMessage,
    ) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> {
        let seq = self.appended.read().await.len() as i64 + 1;
        let persisted = bcs_domain::PersistedMessage {
            message_id: format!("msg-{seq}"),
            group_id: msg.group_id.clone(),
            session_id: msg.session_id.clone(),
            session_seq: seq,
            sender_id: msg.sender_id.clone(),
            sender_type: msg.sender_type,
            message_type: msg.message_type.clone(),
            content: msg.content.clone(),
            client_msg_id: msg.client_msg_id.clone(),
            owner_bot_id: msg.owner_bot_id.clone(),
            status: bcs_domain::PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id.clone(),
        };
        self.appended.write().await.push(msg);
        Ok(persisted)
    }

    async fn query_messages(
        &self,
        _query: bcs_domain::MessageQuery,
    ) -> Result<bcs_domain::MessagePage, bcs_service_api::port::repo::MessageRepoError> {
        Ok(bcs_domain::MessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }

    async fn get_message_by_id(
        &self,
        _session_id: &str,
        _message_id: &str,
    ) -> Result<Option<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError>
    {
        Ok(None)
    }

    async fn get_current_seq(
        &self,
        _session_id: &str,
    ) -> Result<i64, bcs_service_api::port::repo::MessageRepoError> {
        Ok(self.appended.read().await.len() as i64)
    }
}

#[tokio::test]
async fn dispatch_bot_joined_delivers_to_all_participants() {
    let new_bot_id = "new-bot-001".to_string();
    let existing_bot_id = "existing-bot-001".to_string();

    let group = Group {
        id: "group-001".into(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: existing_bot_id.clone(),
        originator: Some(existing_bot_id.clone()),
        routing_policy: None,
        context: None,
        participants: vec![
            Participant {
                bot_uuid: existing_bot_id.clone(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
            },
            Participant {
                bot_uuid: new_bot_id.clone(),
                bot_name: None,
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
            },
        ],
        messages: vec![],
        workspace: Default::default(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 0,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };

    let event = SystemMessageEvent::BotJoined {
        group_id: group.id.clone(),
        actor: Participant {
            bot_uuid: new_bot_id.clone(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        },
    };

    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .register(BotJoinedMessageProducer::new(Arc::new(
            bcs_test_support::NoopGroupMessageHistoryService,
        )))
        .build()
        .expect("build dispatcher");

    let outcome = dispatcher
        .dispatch(event, &group, "session-test", &group.participants)
        .await
        .expect("dispatch succeeded");

    assert_eq!(outcome.total_recipients, 2);
    assert_eq!(outcome.successful_deliveries, 2);
    assert_eq!(outcome.failed_deliveries, 0);

    let calls = delivery.calls.lock().unwrap();
    assert_eq!(calls.len(), 2);

    let target_ids: Vec<String> = calls.iter().map(|c| c.target_bot_id().to_string()).collect();
    assert!(target_ids.contains(&new_bot_id));
    assert!(target_ids.contains(&existing_bot_id));

    for cmd in calls.iter() {
        assert_eq!(cmd.delivery_kind, bcs_protocol::BotDeliveryKind::Inject);
    }
}

#[tokio::test]
async fn dispatch_session_context_preserves_manager_worker_group_type() {
    let mut manager = Participant::bot("bot-manager", ParticipantRole::Manager);
    manager.bot_name = Some("Manager".to_string());
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.bot_name = Some("Worker".to_string());
    let mut group = Group::new("group-manager-worker", "control-plane-owner", vec![manager, worker]);
    group.originator = Some("bot-manager".to_string());
    group.group_strategy = GroupStrategy::ManagerWorker;

    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: "group-manager-worker:abcdef12".to_string(),
        reason: "性能审计".to_string(),
        session_input: Some(serde_json::json!("执行数据库慢查询审计")),
        task_ledger: None,
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .register(SessionContextMessageProducer)
        .build()
        .expect("build dispatcher");

    dispatcher
        .dispatch(event, &group, "group-manager-worker:abcdef12", &group.participants)
        .await
        .expect("dispatch succeeded");

    let calls = delivery.calls.lock().unwrap();
    let manager = calls
        .iter()
        .find(|cmd| cmd.target_bot_id() == "bot-manager")
        .expect("manager delivery");
    let params = match &manager.frame {
        bcs_protocol::BcsFrame::Request(frame) => frame.params.as_ref().expect("request params"),
        other => panic!("expected request frame, got {other:?}"),
    };

    assert_eq!(manager.delivery_kind, bcs_protocol::BotDeliveryKind::Send);
    assert_eq!(params["session_context"]["group_type"], "manager_worker");
    assert_eq!(params["session_context"]["recipient_role"], "manager");
}

#[tokio::test]
async fn dispatch_manager_worker_session_context_persists_worker_private_context() {
    let mut manager = Participant::bot("bot-manager", ParticipantRole::Manager);
    manager.bot_name = Some("Manager".to_string());
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.bot_name = Some("Worker".to_string());
    let mut group = Group::new("group-manager-worker", "control-plane-owner", vec![manager, worker]);
    group.originator = Some("bot-manager".to_string());
    group.group_strategy = GroupStrategy::ManagerWorker;

    let session_id = "group-manager-worker:abcdef12";
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: session_id.to_string(),
        reason: "性能审计".to_string(),
        session_input: Some(serde_json::json!("执行数据库慢查询审计")),
        task_ledger: None,
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let message_repo = Arc::new(RecordingMessageRepo::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery)
        .with_frontend_delivery(frontend_delivery)
        .with_message_repo(message_repo.clone())
        .register(SessionContextMessageProducer)
        .build()
        .expect("build dispatcher");

    dispatcher
        .dispatch(event, &group, session_id, &group.participants)
        .await
        .expect("dispatch succeeded");

    let appended = message_repo.appended().await;
    assert_eq!(appended.len(), 2);

    let global_context = appended
        .iter()
        .find(|msg| msg.owner_bot_id.is_none())
        .expect("global manager context");
    assert_eq!(global_context.group_id, group.id);
    assert_eq!(global_context.session_id, session_id);
    assert_eq!(global_context.sender_id, "system");
    assert_eq!(global_context.message_type, "system");
    assert!(content_text(global_context).contains("你的角色: manager"));

    let worker_context = appended
        .iter()
        .find(|msg| msg.owner_bot_id.as_deref() == Some("bot-worker"))
        .expect("worker private context");
    assert_eq!(worker_context.group_id, group.id);
    assert_eq!(worker_context.session_id, session_id);
    assert_eq!(worker_context.sender_id, "system");
    assert_eq!(worker_context.message_type, "system");
    assert!(content_text(worker_context).contains("你的角色: worker"));
}

#[tokio::test]
async fn dispatch_manager_worker_session_context_persists_each_worker_private_context() {
    let mut manager = Participant::bot("bot-manager", ParticipantRole::Manager);
    manager.bot_name = Some("Manager".to_string());
    let mut worker_a = Participant::bot("bot-worker-a", ParticipantRole::Worker);
    worker_a.bot_name = Some("Worker A".to_string());
    let mut worker_b = Participant::bot("bot-worker-b", ParticipantRole::Worker);
    worker_b.bot_name = Some("Worker B".to_string());
    let mut group = Group::new(
        "group-manager-worker",
        "control-plane-owner",
        vec![manager, worker_a, worker_b],
    );
    group.originator = Some("bot-manager".to_string());
    group.group_strategy = GroupStrategy::ManagerWorker;

    let session_id = "group-manager-worker:abcdef12";
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: session_id.to_string(),
        reason: "性能审计".to_string(),
        session_input: Some(serde_json::json!("执行数据库慢查询审计")),
        task_ledger: None,
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let message_repo = Arc::new(RecordingMessageRepo::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery)
        .with_frontend_delivery(frontend_delivery)
        .with_message_repo(message_repo.clone())
        .register(SessionContextMessageProducer)
        .build()
        .expect("build dispatcher");

    dispatcher
        .dispatch(event, &group, session_id, &group.participants)
        .await
        .expect("dispatch succeeded");

    let appended = message_repo.appended().await;
    assert_eq!(appended.len(), 3);
    assert_eq!(appended.iter().filter(|msg| msg.owner_bot_id.is_none()).count(), 1);
    for worker_id in ["bot-worker-a", "bot-worker-b"] {
        let worker_context = appended
            .iter()
            .find(|msg| msg.owner_bot_id.as_deref() == Some(worker_id))
            .unwrap_or_else(|| panic!("worker private context for {worker_id}"));
        assert!(content_text(worker_context).contains("你的角色: worker"));
    }
}

#[tokio::test]
async fn dispatch_non_manager_worker_session_context_persists_single_global_record() {
    let mut driver = Participant::bot("bot-driver", ParticipantRole::Driver);
    driver.bot_name = Some("Driver".to_string());
    let mut consultant = Participant::bot("bot-consultant", ParticipantRole::Consultant);
    consultant.bot_name = Some("Consultant".to_string());
    let group = Group::new(
        "group-chat",
        "bot-driver",
        vec![driver, consultant],
    );

    let session_id = "group-chat:abcdef12";
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: session_id.to_string(),
        reason: "普通协作".to_string(),
        session_input: None,
        task_ledger: None,
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let message_repo = Arc::new(RecordingMessageRepo::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery)
        .with_frontend_delivery(frontend_delivery)
        .with_message_repo(message_repo.clone())
        .register(SessionContextMessageProducer)
        .build()
        .expect("build dispatcher");

    dispatcher
        .dispatch(event, &group, session_id, &group.participants)
        .await
        .expect("dispatch succeeded");

    let appended = message_repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].owner_bot_id, None);
    assert!(content_text(&appended[0]).contains("[GROUP CONTEXT]"));
}

#[tokio::test]
async fn dispatch_manager_worker_session_context_does_not_make_worker_context_public() {
    let mut manager = Participant::bot("bot-manager", ParticipantRole::Manager);
    manager.bot_name = Some("Manager".to_string());
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.bot_name = Some("Worker".to_string());
    let mut group = Group::new("group-manager-worker", "control-plane-owner", vec![manager, worker]);
    group.originator = Some("bot-manager".to_string());
    group.group_strategy = GroupStrategy::ManagerWorker;

    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: "group-manager-worker:abcdef12".to_string(),
        reason: "性能审计".to_string(),
        session_input: None,
        task_ledger: None,
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let message_repo = Arc::new(RecordingMessageRepo::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery)
        .with_frontend_delivery(frontend_delivery)
        .with_message_repo(message_repo.clone())
        .register(WorkerOnlySessionContextProducer)
        .build()
        .expect("build dispatcher");

    dispatcher
        .dispatch(event, &group, "group-manager-worker:abcdef12", &group.participants)
        .await
        .expect("dispatch succeeded");

    let appended = message_repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].owner_bot_id.as_deref(), Some("bot-worker"));
    assert_eq!(content_text(&appended[0]), "worker-only context");
}

#[tokio::test]
async fn dispatch_manager_worker_generic_system_message_persists_single_global_record() {
    let mut manager = Participant::bot("bot-manager", ParticipantRole::Manager);
    manager.bot_name = Some("Manager".to_string());
    let mut worker = Participant::bot("bot-worker", ParticipantRole::Worker);
    worker.bot_name = Some("Worker".to_string());
    let mut group = Group::new("group-manager-worker", "control-plane-owner", vec![manager, worker]);
    group.originator = Some("bot-manager".to_string());
    group.group_strategy = GroupStrategy::ManagerWorker;

    let event = SystemMessageEvent::GenericNotification {
        group_id: group.id.clone(),
        message: "member changed".to_string(),
        receivers: vec![],
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let message_repo = Arc::new(RecordingMessageRepo::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery)
        .with_frontend_delivery(frontend_delivery)
        .with_message_repo(message_repo.clone())
        .register(FixedProducer)
        .build()
        .expect("build dispatcher");

    dispatcher
        .dispatch(event, &group, "group-manager-worker:abcdef12", &group.participants)
        .await
        .expect("dispatch succeeded");

    let appended = message_repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].owner_bot_id, None);
    assert_eq!(content_text(&appended[0]), "member changed");
}

#[tokio::test]
async fn dispatch_session_context_uses_bcs_route_when_group_has_no_provider_downlink_bot() {
    let mut driver = Participant::bot("bot-ws", ParticipantRole::Driver);
    driver.bot_name = Some("Driver".to_string());
    let mut consultant = Participant::bot("bot-peer", ParticipantRole::Consultant);
    consultant.bot_name = Some("Peer".to_string());
    let group = Group::new("group-ws", "bot-ws", vec![driver, consultant]);

    let calls = dispatch_session_context_with_provider_registry(&group, "普通协作").await;
    let text = delivered_text_for(&calls, "bot-ws");

    assert!(text.contains("路由工具 (bcs_route)"));
    assert!(text.contains("使用 bcs_route 工具指定下一个响应者"));
    assert!(!text.contains("路由工具 (@mention)"));
    assert!(!text.contains("可@:"));
}

#[tokio::test]
async fn dispatch_session_context_uses_at_mention_when_group_has_provider_downlink_bot() {
    let mut driver = Participant::bot("bot-ws", ParticipantRole::Driver);
    driver.bot_name = Some("Driver".to_string());
    let mut provider = Participant::bot("bot-provider", ParticipantRole::Consultant);
    provider.bot_name = Some("Reviewer".to_string());
    let mut duplicate = Participant::bot("bot-peer", ParticipantRole::Consultant);
    duplicate.bot_name = Some("Reviewer".to_string());
    let group = Group::new(
        "group-provider-member",
        "bot-ws",
        vec![driver, provider, duplicate],
    );

    let calls = dispatch_session_context_with_provider_registry(&group, "普通协作").await;

    for recipient in ["bot-ws", "bot-provider"] {
        let text = delivered_text_for(&calls, recipient);
        assert!(text.contains("路由工具 (@mention)"));
        assert!(text.contains("消息中任何 @ 标识都会触发路由，让被 @ 的 Bot 收到消息并被要求响应。"));
        assert!(text.contains("只有希望某个 Bot 响应时才使用 @"));
        assert!(text.contains("不要用 @ 表示引用、收到或转述某个 Bot 的消息"));
        assert!(text.contains("优先使用名称；名称为空、重复或不确定时，使用 Bot ID。"));
        assert!(text.contains(
            "- 名称: Driver | ID: bot-ws | 角色: driver | 可@: @Driver / @bot-ws"
        ));
        assert!(text.contains(
            "- 名称: Reviewer | ID: bot-provider | 角色: consultant | 可@: @bot-provider"
        ));
        assert!(text.contains(
            "- 名称: Reviewer | ID: bot-peer | 角色: consultant | 可@: @bot-peer"
        ));
        assert!(!text.contains("路由工具 (bcs_route)"));
        assert!(!text.contains("等待 @mention、bcs_route 或任务点名后再响应。"));
    }
}

#[tokio::test]
async fn dispatch_send_system_message_records_run_context_for_provider_callback() {
    let group = Group {
        id: "group-provider".into(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "bot-provider".to_string(),
        originator: Some("bot-provider".to_string()),
        routing_policy: None,
        context: None,
        participants: vec![Participant {
            bot_uuid: "bot-provider".to_string(),
            bot_name: Some("Provider".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        }],
        messages: vec![],
        workspace: Default::default(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 0,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };
    let event = SystemMessageEvent::GenericNotification {
        group_id: group.id.clone(),
        message: "member changed".to_string(),
        receivers: vec![],
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let run_context = Arc::new(RecordingRunContext::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .with_bot_run_context(run_context.clone())
        .register(FixedSendProducer)
        .build()
        .expect("build dispatcher");

    let before_dispatch_ms = bcs_protocol::now_ms();
    let outcome = dispatcher
        .dispatch(event, &group, "session-provider", &group.participants)
        .await
        .expect("dispatch succeeded");
    let after_dispatch_ms = bcs_protocol::now_ms();

    assert_eq!(outcome.successful_deliveries, 1);
    let calls = delivery.calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert!(calls[0].target.is_http_provider());
    let context = run_context
        .get(&calls[0].run_id)
        .expect("system chat.send run context");
    assert_eq!(context.run_id, calls[0].run_id);
    assert_eq!(context.bot_id, "bot-provider");
    assert_eq!(context.group_id, "group-provider");
    assert_eq!(context.bcs_session_id.as_deref(), Some("session-provider"));
    assert!(!context.terminal);
    assert!(
        context.deadline_ms
            >= before_dispatch_ms.saturating_add(DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS)
    );
    assert!(
        context.deadline_ms
            <= after_dispatch_ms.saturating_add(DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS)
    );
}

#[tokio::test]
async fn dispatch_send_system_message_uses_sse_when_stream_gray_mode_disabled() {
    let group = Group {
        id: "group-provider".into(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "bot-provider".to_string(),
        originator: Some("bot-provider".to_string()),
        routing_policy: None,
        context: None,
        participants: vec![Participant {
            bot_uuid: "bot-provider".to_string(),
            bot_name: Some("Provider".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        }],
        messages: vec![],
        workspace: Default::default(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 0,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };
    let event = SystemMessageEvent::GenericNotification {
        group_id: group.id.clone(),
        message: "member changed".to_string(),
        receivers: vec![],
    };
    let registry = Arc::new(ProviderTargetRegistry::v2_gray());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new_disabled(vec![
            "gray-user".to_string(),
        ])))
        .register(FixedSendProducer)
        .build()
        .expect("build dispatcher");

    let outcome = dispatcher
        .dispatch(event, &group, "session-provider", &group.participants)
        .await
        .expect("dispatch succeeded");

    assert_eq!(outcome.successful_deliveries, 1);
    let calls = delivery.calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert!(calls[0].target.is_http_provider());
    assert_eq!(calls[0].provider_transport, ProviderTransportPreference::CallbackSse);
}

#[tokio::test]
async fn dispatch_send_system_message_to_websocket_does_not_record_run_context() {
    let group = Group {
        id: "group-ws".into(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "bot-ws".to_string(),
        originator: Some("bot-ws".to_string()),
        routing_policy: None,
        context: None,
        participants: vec![Participant {
            bot_uuid: "bot-ws".to_string(),
            bot_name: Some("WebSocket".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        }],
        messages: vec![],
        workspace: Default::default(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 0,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };
    let event = SystemMessageEvent::GenericNotification {
        group_id: group.id.clone(),
        message: "system notice".to_string(),
        receivers: vec![],
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let run_context = Arc::new(RecordingRunContext::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .with_bot_run_context(run_context.clone())
        .register(FixedWebSocketSendProducer)
        .build()
        .expect("build dispatcher");

    let outcome = dispatcher
        .dispatch(event, &group, "session-ws", &group.participants)
        .await
        .expect("dispatch succeeded");

    assert_eq!(outcome.successful_deliveries, 1);
    let calls = delivery.calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert!(!calls[0].target.is_http_provider());
    assert_eq!(run_context.len(), 0);
}

#[tokio::test]
async fn dispatch_failed_send_system_message_does_not_record_run_context() {
    let group = Group {
        id: "group-provider".into(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "bot-provider".to_string(),
        originator: Some("bot-provider".to_string()),
        routing_policy: None,
        context: None,
        participants: vec![Participant {
            bot_uuid: "bot-provider".to_string(),
            bot_name: Some("Provider".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        }],
        messages: vec![],
        workspace: Default::default(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 0,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };
    let event = SystemMessageEvent::GenericNotification {
        group_id: group.id.clone(),
        message: "member changed".to_string(),
        receivers: vec![],
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort {
        calls: Mutex::new(Vec::new()),
        delivered: false,
        fail: false,
    });
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let run_context = Arc::new(RecordingRunContext::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .with_bot_run_context(run_context.clone())
        .register(FixedSendProducer)
        .build()
        .expect("build dispatcher");

    let outcome = dispatcher
        .dispatch(event, &group, "session-provider", &group.participants)
        .await
        .expect("dispatch succeeded");

    assert_eq!(outcome.successful_deliveries, 0);
    assert_eq!(outcome.failed_deliveries, 1);
    let calls = delivery.calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert!(calls[0].target.is_http_provider());
    let params = match &calls[0].frame {
        bcs_protocol::BcsFrame::Request(frame) => frame.params.as_ref().expect("request params"),
        other => panic!("expected request frame, got {other:?}"),
    };
    assert_eq!(params["bcs_group_id"], "group-provider");
    assert_eq!(params["bcs_session_id"], "session-provider");
    assert_eq!(run_context.len(), 0);
}

#[tokio::test]
async fn dispatch_errored_send_system_message_does_not_record_run_context() {
    let group = Group {
        id: "group-provider".into(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "bot-provider".to_string(),
        originator: Some("bot-provider".to_string()),
        routing_policy: None,
        context: None,
        participants: vec![Participant {
            bot_uuid: "bot-provider".to_string(),
            bot_name: Some("Provider".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        }],
        messages: vec![],
        workspace: Default::default(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 0,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };
    let event = SystemMessageEvent::GenericNotification {
        group_id: group.id.clone(),
        message: "member changed".to_string(),
        receivers: vec![],
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort {
        calls: Mutex::new(Vec::new()),
        delivered: true,
        fail: true,
    });
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let run_context = Arc::new(RecordingRunContext::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .with_bot_run_context(run_context.clone())
        .register(FixedSendProducer)
        .build()
        .expect("build dispatcher");

    let outcome = dispatcher
        .dispatch(event, &group, "session-provider", &group.participants)
        .await
        .expect("dispatch succeeded");

    assert_eq!(outcome.successful_deliveries, 0);
    assert_eq!(outcome.failed_deliveries, 1);
    let calls = delivery.calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert!(calls[0].target.is_http_provider());
    let params = match &calls[0].frame {
        bcs_protocol::BcsFrame::Request(frame) => frame.params.as_ref().expect("request params"),
        other => panic!("expected request frame, got {other:?}"),
    };
    assert_eq!(params["bcs_group_id"], "group-provider");
    assert_eq!(params["bcs_session_id"], "session-provider");
    assert_eq!(run_context.len(), 0);
}

#[tokio::test]
async fn dispatch_inject_system_message_does_not_record_run_context() {
    let group = Group {
        id: "group-provider".into(),
        label: None,
        status: GroupStatus::Active,
        driver_bot: "bot-provider".to_string(),
        originator: Some("bot-provider".to_string()),
        routing_policy: None,
        context: None,
        participants: vec![Participant {
            bot_uuid: "bot-provider".to_string(),
            bot_name: Some("Provider".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        }],
        messages: vec![],
        workspace: Default::default(),
        service_group_uuid: None,
        service_mode: None,
        created_at: 0,
        updated_at: 0,
        group_kind: GroupKind::Normal,
        dm_pair_key: None,
        group_strategy: GroupStrategy::Chat,
        service_spec: None,
        version: 0,
        record_status: "active".to_string(),
        visibility: "private".to_string(),
    };
    let event = SystemMessageEvent::GenericNotification {
        group_id: group.id.clone(),
        message: "member changed".to_string(),
        receivers: vec![],
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);
    let run_context = Arc::new(RecordingRunContext::default());

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .with_bot_run_context(run_context.clone())
        .register(FixedProducer)
        .build()
        .expect("build dispatcher");

    let session_id = "group-provider:abcdef12";
    let outcome = dispatcher
        .dispatch(event, &group, session_id, &group.participants)
        .await
        .expect("dispatch succeeded");

    assert_eq!(outcome.successful_deliveries, 1);
    let calls = delivery.calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert!(calls[0].target.is_http_provider());
    let params = match &calls[0].frame {
        bcs_protocol::BcsFrame::Request(frame) => frame.params.as_ref().expect("request params"),
        other => panic!("expected request frame, got {other:?}"),
    };
    assert_eq!(params["bcs_group_id"], "group-provider");
    assert_eq!(params["bcs_session_id"], session_id);
    assert_eq!(run_context.len(), 0);
}

async fn dispatch_session_context_with_provider_registry(
    group: &Group,
    reason: &str,
) -> Vec<BotDeliveryCommand> {
    let session_id = format!("{}:abcdef12", group.id);
    let event = SystemMessageEvent::SessionContext {
        group_id: group.id.clone(),
        session_id: session_id.clone(),
        reason: reason.to_string(),
        session_input: None,
        task_ledger: None,
    };
    let registry = Arc::new(ProviderTargetRegistry::default());
    let delivery = Arc::new(MockDeliveryPort::default());
    let frontend_delivery = Arc::new(NoopFrontendDeliveryPort);

    let dispatcher = SystemMessageDispatcherImpl::builder()
        .with_registry(registry)
        .with_delivery(delivery.clone())
        .with_frontend_delivery(frontend_delivery)
        .register(SessionContextMessageProducer)
        .build()
        .expect("build dispatcher");

    dispatcher
        .dispatch(event, group, &session_id, &group.participants)
        .await
        .expect("dispatch succeeded");

    delivery.calls.lock().unwrap().clone()
}

fn delivered_text_for(calls: &[BotDeliveryCommand], recipient: &str) -> String {
    let command = calls
        .iter()
        .find(|cmd| cmd.target_bot_id() == recipient)
        .unwrap_or_else(|| panic!("delivery to {recipient}"));
    let params = match &command.frame {
        bcs_protocol::BcsFrame::Request(frame) => frame.params.as_ref().expect("request params"),
        other => panic!("expected request frame, got {other:?}"),
    };
    params["message"]["content"][0]["text"]
        .as_str()
        .expect("message text")
        .to_string()
}

fn content_text(msg: &bcs_domain::NewMessage) -> &str {
    msg.content.as_str().expect("string content")
}

struct WorkerOnlySessionContextProducer;

#[async_trait]
impl SystemMessageProducerService for WorkerOnlySessionContextProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::SessionContext
    }

    async fn produce(
        &self,
        _event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        _participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        vec![SystemGroupMessage {
            recipients: vec!["bot-worker".to_string()],
            message: "worker-only context".to_string(),
            delivery_type: DeliveryType::Inject,
        }]
    }
}

struct FixedProducer;

#[async_trait]
impl SystemMessageProducerService for FixedProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::GenericNotification
    }

    async fn produce(
        &self,
        _event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        _participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        vec![SystemGroupMessage {
            recipients: vec!["bot-provider".to_string()],
            message: "member changed".to_string(),
            delivery_type: DeliveryType::Inject,
        }]
    }
}

struct FixedSendProducer;

#[async_trait]
impl SystemMessageProducerService for FixedSendProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::GenericNotification
    }

    async fn produce(
        &self,
        _event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        _participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        vec![SystemGroupMessage {
            recipients: vec!["bot-provider".to_string()],
            message: "member changed".to_string(),
            delivery_type: DeliveryType::Send,
        }]
    }
}

struct FixedWebSocketSendProducer;

#[async_trait]
impl SystemMessageProducerService for FixedWebSocketSendProducer {
    fn kind(&self) -> SystemMessageEventKind {
        SystemMessageEventKind::GenericNotification
    }

    async fn produce(
        &self,
        _event: &SystemMessageEvent,
        _group: &Group,
        _registry: &dyn BotRegistryCoreService,
        _participants: &[Participant],
    ) -> Vec<SystemGroupMessage> {
        vec![SystemGroupMessage {
            recipients: vec!["bot-ws".to_string()],
            message: "member changed".to_string(),
            delivery_type: DeliveryType::Send,
        }]
    }
}

#[derive(Clone, Copy)]
struct ProviderTargetRegistry {
    protocol_version: &'static str,
    created_by: Option<&'static str>,
}

impl Default for ProviderTargetRegistry {
    fn default() -> Self {
        Self {
            protocol_version: "1.0",
            created_by: None,
        }
    }
}

impl ProviderTargetRegistry {
    fn v2_gray() -> Self {
        Self {
            protocol_version: "2.0",
            created_by: Some("gray-user"),
        }
    }
}

#[async_trait]
impl BotRegistryCoreService for ProviderTargetRegistry {
    async fn register(&self, _bot_id: String, _capabilities: BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        Some(RegisteredBot {
            bot_uuid: bot_id.to_string(),
            capabilities: BotCapabilities {
                name: Some(bot_id.to_string()),
                visibility: "protected".to_string(),
                ..BotCapabilities::default()
            },
            dynamic_status: BotDynamicStatus::default(),
            env: None,
            created_by: self.created_by.map(str::to_string),
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
        })
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        if bot_id == "bot-provider" {
            return Ok(BotDeliveryTarget::HttpProvider {
                bot_id: bot_id.to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "reviewer-v2".to_string(),
                webhook_url: "https://provider.example.com/bcs/webhook".to_string(),
                bcs_to_provider_token: RedactedToken::new("secret-b2p"),
                protocol_version: self.protocol_version.to_string(),
            });
        }
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        Vec::new()
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

    async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_actor_status(&self, _bot_id: &str, _status: ActorStatus) -> ServiceResult<()> {
        Ok(())
    }

    async fn ensure_human_actor(
        &self,
        _staff_no: &str,
        _nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        Ok(EnsureHumanResult { created: false })
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

    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        true
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Ok(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }
}
