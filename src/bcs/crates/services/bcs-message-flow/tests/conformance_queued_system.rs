use std::sync::Arc;
use bcs_domain::{DeliveryType, GroupStrategy, MessageAudience, MessageVisibilityDomain,
    ParticipantRole, SystemMessageEvent, SystemMessageEventKind, PersistMode, SystemGroupMessage};
use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus as Status};
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore, managed_delivery::ManagedMessageDelivery,
    queued_group::QueuedGroupPreparation, queued_system::QueuedSystemAdmission};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{GroupCoreService, ManagedMessageDeliveryService, ManagedDeliveryPreparationService,
    SystemMessageDispatcherService, BotDeliveryPort, BotRunContextPort, MessageFlowService, DeliveryTransitionCommand,
    CallerContext, HumanActor, WebSendCommand};
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_system_message::{SystemMessageDispatcherImpl, SessionContextMessageProducer};

#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

async fn fixture() -> (support::FlowTestSupport, Arc<BcsMessageFlow>, Arc<ManagedMessageDelivery>, Arc<MemoryMessageRepo>, Arc<MemoryBotRunContextStore>) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let contexts = Arc::new(MemoryBotRunContextStore::default());
    let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone())
        .with_message_repo(repo.clone()).with_managed_deliveries(service.clone())
        .with_bot_run_context(contexts.clone())
        .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 1), ("bot-observer".into(), 1)]))
        .with_session_management(Arc::new(session_support::StaticSessionManagement::new(
            session_support::test_session("group-1:system", "group-1", group.participants))));
    flow.delivery_queue_ttl_ms = Some(60_000);
    let flow = Arc::new(flow);
    flow.retain_terminal_events();
    (support, flow, service, repo, contexts)
}

#[tokio::test]
async fn shared_initialization_source_preserves_send_ttl_and_only_protects_inject() {
    let (support, flow, service, _, _) = fixture().await;
    let group = support.group.get("group-1").await.unwrap();
    let messages = [
        SystemGroupMessage { recipients:vec!["bot-driver".into()], message:"shared context".into(),
            delivery_type:DeliveryType::Send, persist:PersistMode::Public },
        SystemGroupMessage { recipients:vec!["bot-observer".into()], message:"shared context".into(),
            delivery_type:DeliveryType::Inject, persist:PersistMode::Skip },
    ];
    flow.system_queue_port().admit(&group, "group-1:system", &group.participants,
        SystemMessageEventKind::SessionContext, &messages).await.unwrap();
    let rows = service.snapshot(None).await.unwrap();
    assert_eq!(rows[0].source_message_id, rows[1].source_message_id);
    assert!(rows.iter().find(|r| r.state.kind == DeliveryType::Send).unwrap().expire_at_ms.is_some());
    assert!(rows.iter().find(|r| r.state.kind == DeliveryType::Inject).unwrap().expire_at_ms.is_none());
}

#[tokio::test]
async fn manager_worker_initialization_queues_owned_context_with_directed_audience() {
    let (support, flow, service, repo, _) = fixture().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    for participant in &mut group.participants {
        if participant.bot_uuid == "bot-driver" { participant.role = ParticipantRole::Manager; }
        if participant.bot_uuid == "bot-observer" { participant.role = ParticipantRole::Worker; }
    }
    support.group.upsert(group.clone()).await.unwrap();
    let messages = [
        SystemGroupMessage { recipients:vec!["bot-driver".into()], message:"manager context".into(),
            delivery_type:DeliveryType::Send, persist:PersistMode::Public },
        SystemGroupMessage { recipients:vec!["bot-observer".into()], message:"worker context".into(),
            delivery_type:DeliveryType::Inject, persist:PersistMode::PerRecipient },
    ];
    flow.system_queue_port().admit(&group, "group-1:system", &group.participants,
        SystemMessageEventKind::SessionContext, &messages).await.unwrap().unwrap();
    let rows = service.snapshot(None).await.unwrap();
    let manager = rows.iter().find(|row| row.target_bot_id == "bot-driver").unwrap();
    let manager_message = repo.get_message_by_id(&manager.session_id, &manager.source_message_id)
        .await.unwrap().unwrap();
    assert_eq!(manager_message.visibility_domain, Some(MessageVisibilityDomain::ManagerWorker));
    assert_eq!(manager_message.owner_bot_id, None);
    assert_eq!(manager_message.audience, Some(MessageAudience::FullOnly));
    let worker = rows.iter().find(|row| row.target_bot_id == "bot-observer").unwrap();
    assert_eq!(worker.state.status, Status::PendingContext);
    let worker_message = repo.get_message_by_id(&worker.session_id, &worker.source_message_id)
        .await.unwrap().unwrap();
    assert_eq!(worker_message.visibility_domain, Some(MessageVisibilityDomain::ManagerWorker));
    assert_eq!(worker_message.owner_bot_id.as_deref(), Some("bot-observer"));
    assert_eq!(worker_message.audience, Some(MessageAudience::Directed {
        actor_ids: vec!["bot-observer".into()],
    }));
}

fn user_send() -> WebSendCommand {
    WebSendCommand { caller: CallerContext::Human(HumanActor { actor_id:"human_1".into(), staff_no:"1".into() }),
        group_id:"group-1".into(), session_id:Some("group-1:system".into()), from_actor_id:"human_1".into(),
        from_name:None, message:"请检查当前需求".into(), mentions:vec!["bot-observer".into()], attachments:None,
        thinking:None, idempotency_key:Some("user-request".into()), source_im_message_id:None,
        channel_sender_identity:None, sender_conn_id:None, provider_bypass_headers:vec![] }
}

#[tokio::test]
async fn initialization_is_queued_and_first_group_send_carries_required_context_without_native_inject() {
    let (support, flow, service, repo, contexts) = fixture().await;
    let group = support.group.get("group-1").await.unwrap();
    let dispatcher = SystemMessageDispatcherImpl::builder().with_registry(support.registry.clone())
        .with_delivery(support.bot_delivery.clone()).with_frontend_delivery(support.frontend_delivery.clone())
        .with_message_repo(repo.clone()).with_bot_run_context(contexts.clone()).with_queue(flow.system_queue_port())
        .register(SessionContextMessageProducer).build().unwrap();
    let outcome = dispatcher.dispatch(SystemMessageEvent::SessionContext { group_id: group.id.clone(),
        session_id:"group-1:system".into(), reason:"初始化目标".into(), session_input:None, task_ledger:None,
        driver_delivery:None }, &group, "group-1:system", &group.participants).await.unwrap();
    assert_eq!(outcome.recipient_results.len(), 2);
    assert!(outcome.recipient_results.iter().all(|r| r.accepted() && !r.delivered && r.delivery_id.is_some()));
    assert!(support.bot_delivery.frames().await.is_empty());
    let rows = service.snapshot(None).await.unwrap();
    let driver = rows.iter().find(|r| r.state.kind == DeliveryType::Send).unwrap();
    assert_eq!(driver.flow_kind, DeliveryFlowKind::System);
    assert!(contexts.get_context(driver.run_id.as_deref().unwrap()).await.is_none(), "queue wait is not run time");
    let inject = rows.iter().find(|r| r.state.kind == DeliveryType::Inject).unwrap();
    assert_eq!(inject.state.status, Status::PendingContext);
    assert_eq!(inject.expire_at_ms, None);
    let initial = repo.get_message_by_id(&inject.session_id, &inject.source_message_id).await.unwrap().unwrap();
    assert_eq!(initial.owner_bot_id.as_deref(), Some("bot-observer"));
    let preparer = QueuedGroupPreparation { flow:Arc::downgrade(&flow), deliveries:service.clone() };
    let driver_command = preparer.prepare(driver).await.unwrap();
    let driver_wire = serde_json::to_value(driver_command.command.frame).unwrap();
    assert_eq!(driver_wire["method"], "chat.send");
    assert!(driver_wire.to_string().contains("bcs-system-message"));
    flow.handle_web_send(user_send()).await.unwrap();
    let carrier = service.snapshot(None).await.unwrap().into_iter().find(|r| r.flow_kind == DeliveryFlowKind::Group && r.state.kind == DeliveryType::Send).unwrap();
    let mut prepared = preparer.prepare(&carrier).await.unwrap();
    let wire = serde_json::to_value(&prepared.command.frame).unwrap().to_string();
    assert!(wire.contains("初始化目标") && wire.contains("请检查当前需求"));
    let selection = &prepared.transport_context_json["context_selection"];
    assert_eq!(selection["selected"].as_array().unwrap().len(), 1);
    let started = service.transition(DeliveryTransitionCommand { delivery_id:carrier.delivery_id.clone(),
        expected_state_version:carrier.state.state_version, event:Event::StartSend,
        now_ms:chrono::Utc::now().timestamp_millis(), request_id:None, actor_id:None, reply:None,
        transport_context_json:Some(prepared.transport_context_json), deadline_at_ms:Some(i64::MAX) }).await.unwrap();
    if let bcs_protocol::BcsFrame::Request(frame) = &mut prepared.command.frame { frame.id = started.request_id.clone().unwrap(); }
    preparer.before_send(&started, &prepared.command).await.unwrap();
    assert!(contexts.get_context(started.run_id.as_deref().unwrap()).await.is_some());
    support.bot_delivery.deliver(prepared.command).await.unwrap();
    service.transition(DeliveryTransitionCommand { delivery_id:started.delivery_id,
        expected_state_version:started.state.state_version, event:Event::Completed,
        now_ms:chrono::Utc::now().timestamp_millis(), request_id:None, actor_id:None, reply:None,
        transport_context_json:None, deadline_at_ms:None }).await.unwrap();
    let settled = service.snapshot(None).await.unwrap();
    assert_eq!(settled.iter().find(|r| r.delivery_id == inject.delivery_id).unwrap().state.status, Status::Consumed);
    assert!(support.bot_delivery.frames().await.iter().all(|f| matches!(f, bcs_protocol::BcsFrame::Request(r) if r.method == "chat.send")));
}

#[tokio::test]
async fn public_skip_recipients_share_one_canonical_message_and_invalid_skip_commits_nothing() {
    let (support, flow, service, repo, _) = fixture().await;
    let group = support.group.get("group-1").await.unwrap();
    bcs_test_support::contract::application::message_delivery::system_message_queue_service_contract_tests(
        flow.system_queue_port().as_ref(), &group, "group-1:system").await.unwrap();
    let rows = service.snapshot(None).await.unwrap();
    assert_eq!(rows[0].source_message_id, rows[1].source_message_id);
    assert!(repo.get_message_by_id("group-1:system", &rows[0].source_message_id).await.unwrap().unwrap().owner_bot_id.is_none());
    let bad = vec![SystemGroupMessage { recipients:vec!["bot-observer".into()], message:"无来源副本".into(),
        delivery_type:DeliveryType::Inject, persist:PersistMode::Skip }];
    assert!(flow.system_queue_port().admit(&group, "group-1:system", &group.participants,
        SystemMessageEventKind::BotHiddenNotice, &bad).await.is_err());
    assert_eq!(service.snapshot(None).await.unwrap().len(), 2, "invalid Skip commits nothing");
    assert!(support.bot_delivery.frames().await.is_empty());
    use bcs_service_api::SystemMessageQueueService;
    assert!(QueuedSystemAdmission::default().admit(&group, "group-1:system", &group.participants,
        SystemMessageEventKind::SessionContext, &[]).await.is_err(), "unbound composition fails closed");
}

#[tokio::test]
async fn disabling_group_and_system_preserves_initialization_for_the_next_send() {
    use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
    use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(MemoryMessageRepo::new());
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    live.scheduler_available.store(true, std::sync::atomic::Ordering::SeqCst);
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()).with_policy(live.clone()));
    let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone()).with_message_repo(repo)
        .with_managed_deliveries(service.clone())
        .with_session_management(Arc::new(session_support::StaticSessionManagement::new(
            session_support::test_session("group-1:system", "group-1", group.participants.clone()))));
    flow.delivery_policy = Some(live);
    let flow = Arc::new(flow);
    flow.retain_terminal_events();
    let admin = || CallerContext::Human(HumanActor { actor_id:"human_operator".into(), staff_no:"operator".into() });
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true; policy.flow_enabled.system = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    flow.replace_delivery_policy(admin(), 0, policy.clone()).await.unwrap();
    let messages = [SystemGroupMessage { recipients:vec!["bot-observer".into()], message:"REQUIRED_AT_CREATION".into(),
        delivery_type:DeliveryType::Inject, persist:PersistMode::PerRecipient }];
    flow.system_queue_port().admit(&group, "group-1:system", &group.participants,
        SystemMessageEventKind::SessionContext, &messages).await.unwrap().unwrap();
    policy.flow_enabled.group = false; policy.flow_enabled.system = false;
    flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let result = flow.handle_web_send(user_send()).await.unwrap();
    assert!(result.queue_admission.is_some(), "first Send after disable carries persisted initialization");
    let rows = service.snapshot(None).await.unwrap();
    let inject = rows.iter().find(|r| r.state.kind == DeliveryType::Inject).unwrap();
    let carrier = rows.iter().find(|r| r.state.kind == DeliveryType::Send).unwrap();
    assert_eq!(inject.bound_to_delivery_id.as_deref(), Some(carrier.delivery_id.as_str()));
    assert_eq!(inject.state.status, Status::Bound);
    let prepared = QueuedGroupPreparation { flow:Arc::downgrade(&flow), deliveries:service.clone() }.prepare(carrier).await.unwrap();
    assert!(serde_json::to_value(prepared.command.frame).unwrap().to_string().contains("REQUIRED_AT_CREATION"));
    let mut next = user_send(); next.idempotency_key = Some("next".into());
    assert!(flow.handle_web_send(next).await.is_err_and(|e| e.to_string().contains("queue_draining")));
}

#[tokio::test]
async fn failed_system_admission_never_falls_back_to_transport_or_partial_history() {
    let (support, flow, service, repo, contexts) = fixture().await;
    let group = support.group.get("group-1").await.unwrap();
    service.set_admission_available(false);
    let dispatcher = SystemMessageDispatcherImpl::builder().with_registry(support.registry.clone())
        .with_delivery(support.bot_delivery.clone()).with_frontend_delivery(support.frontend_delivery.clone())
        .with_message_repo(repo.clone()).with_bot_run_context(contexts).with_queue(flow.system_queue_port())
        .register(SessionContextMessageProducer).build().unwrap();
    assert!(dispatcher.dispatch(SystemMessageEvent::SessionContext { group_id:group.id.clone(),
        session_id:"group-1:system".into(), reason:"failure test".into(), session_input:None,
        task_ledger:None, driver_delivery:None }, &group, "group-1:system", &group.participants).await.is_err());
    assert!(service.snapshot(None).await.unwrap().is_empty());
    assert_eq!(repo.get_current_seq("group-1:system").await.unwrap(), 0);
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn driver_capacity_rejection_keeps_other_bots_initial_context() {
    let (support, flow, service, repo, contexts) = fixture().await;
    let group = support.group.get("group-1").await.unwrap();
    flow.system_queue_port().admit(&group, "group-1:system", &group.participants, SystemMessageEventKind::GenericNotification,
        &[SystemGroupMessage { recipients:vec!["bot-driver".into()], message:"fill capacity".into(),
            delivery_type:DeliveryType::Send, persist:PersistMode::Public }]).await.unwrap();
    let dispatcher = SystemMessageDispatcherImpl::builder().with_registry(support.registry.clone())
        .with_delivery(support.bot_delivery.clone()).with_frontend_delivery(support.frontend_delivery.clone())
        .with_message_repo(repo).with_bot_run_context(contexts).with_queue(flow.system_queue_port())
        .register(SessionContextMessageProducer).build().unwrap();
    let result = dispatcher.dispatch(SystemMessageEvent::SessionContext { group_id:group.id.clone(),
        session_id:"group-1:system".into(), reason:"capacity test".into(), session_input:None,
        task_ledger:None, driver_delivery:None }, &group, "group-1:system", &group.participants).await.unwrap();
    assert_eq!(result.successful_deliveries, 1);
    assert_eq!(result.failed_deliveries, 1);
    let rows = service.snapshot(None).await.unwrap();
    assert_eq!(rows.iter().filter(|r| r.state.status == Status::RejectedCapacity).count(), 1);
    assert_eq!(rows.iter().filter(|r| r.state.status == Status::PendingContext && r.target_bot_id == "bot-observer").count(), 1);
    assert!(support.bot_delivery.frames().await.is_empty());
}
