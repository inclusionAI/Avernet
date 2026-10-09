use std::sync::Arc;
use bcs_domain::{DeliveryType, GroupStrategy, MessageAudience, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_domain::message_delivery::DeliveryFlowKind;
use bcs_message_flow::{BcsMessageFlow, managed_delivery::ManagedMessageDelivery};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{BotDeliveryPort, BotDeliveryTarget, BotDeliveryCommand, BotDeliveryResult, ServiceResult, ServiceError, DeliveryTransitionCommand, FrontendDeliveryTarget, GroupCoreService, ManagedMessageDeliveryService};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
use serde_json::json;

#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

#[tokio::test]
async fn queued_group_and_system_failures_use_legacy_chat_notice() {
    for flow_kind in [DeliveryFlowKind::System, DeliveryFlowKind::Group] {
        for strategy in [GroupStrategy::Chat, GroupStrategy::ManagerWorker, GroupStrategy::StateMachine] {
            for private in [false, true] {
                check_notice(flow_kind, strategy, private, DeliveryType::Send, Event::TransportRejected, true, NoticeMode::Online).await;
            }
        }
    }
}

#[tokio::test]
async fn bot_errors_success_and_inject_do_not_add_delivery_failure_notice() {
    for (kind, event) in [(DeliveryType::Send, Event::Failed), (DeliveryType::Send, Event::Completed), (DeliveryType::Inject, Event::CancelRequested)] {
        check_notice(DeliveryFlowKind::System, GroupStrategy::Chat, false, kind, event, false, NoticeMode::Online).await;
    }
}

#[tokio::test]
async fn queued_group_failure_preserves_offline_notice() {
    check_notice(DeliveryFlowKind::Group, GroupStrategy::Chat, false, DeliveryType::Send, Event::TransportRejected, true, NoticeMode::Offline).await;
}

#[tokio::test]
async fn failures_across_ticks_preserve_later_offline_and_retryable_notices() {
    for mode in [NoticeMode::OfflineFirst, NoticeMode::RetryableFirst] {
        check_notice(DeliveryFlowKind::Group, GroupStrategy::Chat, false, DeliveryType::Send, Event::TransportRejected, true, mode).await;
    }
}

#[derive(Clone, Copy)]
enum NoticeMode { Online, Offline, OfflineFirst, RetryableFirst }

async fn check_notice(flow_kind: DeliveryFlowKind, strategy: GroupStrategy, private: bool, kind: DeliveryType, terminal_event: Event, expected: bool, mode: NoticeMode) {
    let fixture = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let offline = matches!(mode, NoticeMode::Offline);
    let staggered = matches!(mode, NoticeMode::OfflineFirst | NoticeMode::RetryableFirst);
    let delivery: Arc<dyn BotDeliveryPort> = if offline { Arc::new(OfflineDelivery) }
        else if staggered { Arc::new(SelectiveOfflineDelivery) } else { fixture.bot_delivery.clone() };
    let mut group = fixture.group.get("group-1").await.unwrap();
    group.group_strategy = strategy;
    fixture.group.upsert(group.clone()).await.unwrap();
    let session = session_support::test_session("group-1:failure", "group-1", group.participants.clone());
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let frontend = fixture.frontend_delivery.clone();
    let dispatcher = bcs_system_message::SystemMessageDispatcherImpl::builder()
        .with_registry(fixture.registry.clone()).with_delivery(delivery.clone())
        .with_frontend_delivery(frontend.clone()).with_message_repo(repo.clone())
        .register(bcs_system_message::producers::generic::GenericNotificationMessageProducer).build().unwrap();
    let system = Arc::new(bcs_system_message::SystemMessageServiceImpl::new(Arc::new(dispatcher), fixture.group.clone()));
    let flow = Arc::new(BcsMessageFlow::new(fixture.group, fixture.routing, fixture.registry, delivery, frontend.clone())
        .with_message_repo(repo.clone()).with_system_message(system)
        .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session)))
        .with_managed_deliveries(service.clone()));
    let (shutdown, receiver) = tokio::sync::watch::channel(false);
    let notifications = tokio::spawn(bcs_message_flow::delivery_notifications::run(Arc::downgrade(&flow), service.subscribe(), receiver));
    let admitted = service.admit(AdmitMessageDeliveries {
        display_message: None, message_id: "initial-context".into(), flow_kind,
        now_ms: 100, expire_at_ms: None, event: None,
        targets: ["bot-driver", "bot-observer"].into_iter().map(|target| DeliveryAdmissionTarget {
            rejection: None, target_bot_id: target.into(), kind, max_queued: 10,
            semantic_projection_json: json!({"version":1}),
        }).collect(),
        message: NewMessage {
            group_id: "group-1".into(), session_id: "group-1:failure".into(), sender_id: "system".into(),
            sender_type: SenderType::System, message_type: "system".into(), content: json!({"text":"initial context"}),
            client_msg_id: None, owner_bot_id: private.then(|| "bot-driver".into()), created_at: 100, run_id: String::new(),
            visibility_domain: MessageVisibilityDomain::ManagerWorker,
            audience: Some(if private { MessageAudience::Directed { actor_ids: vec!["bot-driver".into()] } } else { MessageAudience::FullOnly }),
        },
    }).await.unwrap();
    let command = |row: &bcs_domain::message_delivery::PersistedMessageDelivery, event| DeliveryTransitionCommand {
        delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version, event,
        now_ms: 200, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
    };
    let mut deliveries = admitted.deliveries.iter().collect::<Vec<_>>();
    deliveries.sort_by_key(|row| &row.target_bot_id);
    if matches!(mode, NoticeMode::RetryableFirst) { deliveries.reverse(); }
    for (index, queued) in deliveries.into_iter().enumerate() {
        let active = if kind == DeliveryType::Send { service.transition(command(queued, Event::StartSend)).await.unwrap() } else { queued.clone() };
        service.transition(command(&active, terminal_event)).await.unwrap();
        if staggered { wait_for_notices(&frontend, index + 1).await; }
    }
    if staggered {
        let texts = frontend.events().await.into_iter().filter_map(|frame| {
            let event: serde_json::Value = serde_json::from_str(&frame).unwrap();
            (event["event"] == "chat").then(|| event["payload"]["message"]["content"][0]["text"].as_str().unwrap().to_string())
        }).collect::<Vec<_>>();
        let mut expected = vec!["Bot Driver 已离线", "消息投递失败，请稍后重试。"];
        if matches!(mode, NoticeMode::RetryableFirst) { expected.reverse(); }
        assert_eq!(texts, expected);
        assert_eq!(bcs_service_api::port::repo::MessageRepoPort::get_current_seq(repo.as_ref(), "group-1:failure").await.unwrap(), 3);
        shutdown.send(true).unwrap();
        notifications.await.unwrap();
        return;
    }
    if !expected {
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
        assert!(frontend.events().await.iter().all(|event| serde_json::from_str::<serde_json::Value>(event).unwrap()["event"] != "chat"));
        shutdown.send(true).unwrap();
        notifications.await.unwrap();
        return;
    }
    wait_for_notices(&frontend, 1).await;
    let frames = frontend.commands().await;
    let failure = frames.iter().find(|cmd| serde_json::from_str::<serde_json::Value>(&cmd.event_json).unwrap()["event"] == "chat").unwrap();
    let event: serde_json::Value = serde_json::from_str(&failure.event_json).unwrap();
    assert_eq!(event["bot_uuid"], bcs_service_api::BCS_SYSTEM_MESSAGE);
    assert_eq!(event["payload"]["state"], "final");
    assert_eq!(event["payload"]["bcs_session_id"], "group-1:failure");
    assert_eq!(event["payload"]["message"]["role"], "system");
    if offline {
        let text = event["payload"]["message"]["content"][0]["text"].as_str().unwrap();
        assert!(matches!(text, "Bot Driver、Observer 已离线" | "Bot Observer、Driver 已离线"));
    } else {
        assert_eq!(event["payload"]["message"]["content"], json!([{ "type": "text", "text": "消息投递失败，请稍后重试。" }]));
    }
    assert_eq!(failure.target, FrontendDeliveryTarget::Session { session_id: "group-1:failure".into() });
    assert_eq!(failure.audience, if strategy == GroupStrategy::Chat { None } else { Some(MessageAudience::Public) });
    assert_eq!(failure.visibility_domain, match strategy {
        GroupStrategy::Chat => MessageVisibilityDomain::Chat,
        GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
        GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
    });
    assert!(!failure.event_json.contains("initial context"));
    assert!(failure.run_fallback.is_none());
    assert_eq!(bcs_service_api::port::repo::MessageRepoPort::get_current_seq(repo.as_ref(), "group-1:failure").await.unwrap(),
        if flow_kind == DeliveryFlowKind::Group { 2 } else { 1 });
    assert_eq!(frames.iter().filter(|cmd| serde_json::from_str::<serde_json::Value>(&cmd.event_json).unwrap()["event"] == "chat").count(), 1);

    shutdown.send(true).unwrap();
    notifications.await.unwrap();
}

struct OfflineDelivery;
#[async_trait::async_trait]
impl BotDeliveryPort for OfflineDelivery {
    async fn is_available(&self, _target: &BotDeliveryTarget) -> bool { false }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        Err(ServiceError::BotNotConnected(cmd.target_bot_id().to_string()))
    }
}

async fn wait_for_notices(frontend: &support::RecordingFrontendDelivery, count: usize) {
    tokio::time::timeout(std::time::Duration::from_secs(2), async {
        loop {
            if frontend.events().await.iter().filter(|frame|
                serde_json::from_str::<serde_json::Value>(frame).unwrap()["event"] == "chat"
            ).count() >= count { break; }
            tokio::task::yield_now().await;
        }
    }).await.unwrap();
}

struct SelectiveOfflineDelivery;
#[async_trait::async_trait]
impl BotDeliveryPort for SelectiveOfflineDelivery {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool { target.bot_id() != "bot-driver" }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        Err(ServiceError::BotNotConnected(cmd.target_bot_id().to_string()))
    }
}
