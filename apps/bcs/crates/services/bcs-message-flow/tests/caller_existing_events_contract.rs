//! Caller-derived runs reuse the ordinary `/bot/events` result path.
use std::sync::Arc;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore, queued_group::QueuedGroupPreparation};
use bcs_service_api::{CallerContext, HumanActor, WebSendCommand, GroupCoreService, ManagedDeliveryPreparationService, ManagedMessageDeliveryService, MessageFlowService};
use serde_json::json;
#[path = "support/session.rs"] mod session;
#[path = "../../../test-support/message_flow_contract_support.rs"] mod support;

#[tokio::test]
async fn ordinary_bot_event_reuses_caller_managed_run() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_delivery_target("bot-driver", support::FakeRegistryService::provider_target("bot-driver")).await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
        .with_message_repo(repo)
        .with_bot_run_context(run_context)
        .with_managed_deliveries(service.clone())
        .with_session_management(Arc::new(session::StaticSessionManagement::new(session::test_session("group-1:caller-events", "group-1", group.participants.clone()))));
    let command = WebSendCommand {
        caller_execution: None,
        caller_ingress: None,
        speaking_lease: None,
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(),
        session_id: Some("group-1:caller-events".into()),
        from_actor_id: "human_1".into(),
        from_name: Some("Human One".into()),
        message: "@bot-driver 请处理这个任务".into(),
        mentions: vec!["bot-driver".into()],
        attachments: None,
        thinking: None,
        idempotency_key: Some("caller-events".into()),
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    };
    let target = bcs_service_api::RoutingTarget {
        bot_uuid: "bot-driver".into(),
        url: String::new(),
        is_driver: true,
        delivery_type: bcs_domain::DeliveryType::Send,
    };
    let decision = bcs_service_api::RoutingDecision {
        targets: vec![target.clone()],
        mentions: vec!["bot-driver".into()],
        cleaned_message: "bot-driver 请处理这个任务".into(),
        hidden_mentions: Vec::new(),
    };
    let projection = bcs_message_flow::queued_group::QueuedGroupProjection::capture(
        &flow,
        &group,
        &command,
        &decision,
        &target,
        "Human One".into(),
        None,
    ).await.unwrap();
    let mut projection = serde_json::to_value(projection).unwrap();
    if let Some(map) = projection.as_object_mut() {
        map.insert("caller_required".into(), serde_json::Value::Bool(true));
        map.insert("caller_task".into(), serde_json::Value::String("处理 caller 任务".into()));
    }
    let admissible = bcs_service_api::port::repo::message_delivery::AdmitMessageDeliveries {
        display_message: None,
        message_id: "caller-existing-events".into(),
        flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
        now_ms: 100,
        expire_at_ms: None,
        event: None,
        targets: vec![bcs_service_api::port::repo::message_delivery::DeliveryAdmissionTarget {
                run_id: None,
            rejection: None,
            target_bot_id: "bot-driver".into(),
            kind: bcs_domain::DeliveryType::Send,
            max_queued: 10,
            semantic_projection_json: projection,
        }],
        message: bcs_domain::NewMessage {
            visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
            audience: None,
            group_id: "group-1".into(),
            session_id: "group-1:caller-events".into(),
            sender_id: "human_1".into(),
            sender_type: bcs_domain::SenderType::Human,
            message_type: "chat".into(),
            content: json!({"text":"@bot-driver 请处理这个任务"}).into(),
            client_msg_id: Some("caller-existing-events".into()),
            owner_bot_id: None,
            created_at: 100,
            run_id: String::new(),
        },
    };
    let row = service.admit(admissible).await.unwrap().deliveries.remove(0);
    let flow_arc = Arc::new(flow);
    let preparer = QueuedGroupPreparation {
        flow: Arc::downgrade(&flow_arc),
        deliveries: service.clone(),
    };
    let mut prepared = preparer.prepare(&row).await.unwrap();
    let started = service.transition(bcs_service_api::DeliveryTransitionCommand {
        caller_dispatch_commit: None,
        caller_cancel: None,
        caller_terminal: None,
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
        now_ms: 101,
        request_id: None,
        actor_id: None,
        reply: None,
        transport_context_json: Some(prepared.transport_context_json.clone()),
        deadline_at_ms: Some(i64::MAX),
    }).await.unwrap();
    if let bcs_protocol::BcsFrame::Request(frame) = &mut prepared.command.frame {
        frame.id = started.request_id.clone().unwrap();
    }
    preparer.before_send(&started, &prepared.command).await.unwrap();
    let run = started.run_id.clone().unwrap();
    let resolved_none = flow_arc.resolve_managed_provider_run(&run, "wrong-provider", "bot-driver").await.unwrap();
    assert!(resolved_none.is_none());
    let resolved = flow_arc.resolve_managed_provider_run(&run, "provider-1", "bot-driver").await.unwrap().unwrap();
    assert_eq!(resolved.run_id, run);
    let before = support.group.message_count("group-1").await.unwrap();
    let event = bcs_service_api::BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: run,
        group_id: "group-1".into(),
        event_type: "chat".into(),
        state: bcs_service_api::ChatEventState::Final,
        bcs_session_id: Some("group-1:caller-events".into()),
        event_payload: json!({"message":{"role":"assistant","content":[{"type":"text","text":"处理完成"}]}}),
    };
    flow_arc.handle_bot_event(event.clone()).await.unwrap();
    let rows = service.snapshot(None).await.unwrap();
    let after = rows.iter().find(|row| row.delivery_id == started.delivery_id).unwrap();
    assert_eq!(after.state.status, bcs_domain::message_delivery::MessageDeliveryStatus::Completed);
    assert_eq!(support.group.message_count("group-1").await.unwrap(), before + 1);
}
