use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_protocol::BcsFrame;
use bcs_service_api::{
    BotEventCommand, BotRunContextPort, CallerContext, ChatAbortCommand, ChatEventState,
    GroupCoreService, HumanActor, MessageFlowService, WebSendCommand,
};
use serde_json::json;
use std::sync::Arc;

#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
use session_support::{StaticSessionManagement, test_session};

#[tokio::test]
async fn queued_relay_enforces_group_turn_limit() {
    use bcs_service_api::ManagedMessageDeliveryService;
    for all_managed in [false, true] {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.group.reset_message_count("group-1").await.unwrap();
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
    let mut limits = std::collections::BTreeMap::from([("bot-observer".into(), 10)]);
    if all_managed { limits.insert("bot-driver".into(), 10); }
    let flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
        .with_message_repo(repo).with_managed_deliveries(service.clone())
        .with_group_delivery_limits(limits)
        .with_bot_relay_turn_limit(1)
        .with_session_management(Arc::new(StaticSessionManagement::new(test_session("group-1:limit", "group-1", group.participants))));
    let mut event = BotEventCommand {
        bot_id: "bot-driver".into(), run_id: "relay-1".into(), group_id: "group-1".into(),
        event_type: "chat".into(), state: ChatEventState::Final, bcs_session_id: Some("group-1:limit".into()),
        event_payload: json!({"message":{"role":"assistant","content":[{"type":"text","text":"@Observer 请检查"}]}}),
    };
    flow.handle_bot_event(event.clone()).await.unwrap();
    assert_eq!(support.group.message_count("group-1").await.unwrap(), 1);
    let count = service.snapshot(None).await.unwrap().len();
    assert!(count > 0);
    assert_eq!(support.bot_delivery.frames().await.is_empty(), all_managed);
    event.run_id = "relay-2".into();
    flow.handle_bot_event(event).await.unwrap();
    assert_eq!(support.group.get("group-1").await.unwrap().status, bcs_domain::GroupStatus::Inactive);
    assert_eq!(service.snapshot(None).await.unwrap().len(), count, "limit prevents another queued relay");
    assert_eq!(support.group.message_count("group-1").await.unwrap(), 1);
    }
}

#[tokio::test]
async fn queue_status_respects_participant_message_audience() {
    use bcs_domain::{DeliveryType, MessageAudience, MessageVisibilityDomain, MessageViewScope, NewMessage, SenderType};
    use bcs_service_api::{DeliveryStatusQuery, ManagedMessageDeliveryService};
    use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget};
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = bcs_domain::GroupStrategy::ManagerWorker;
    for participant in &mut group.participants {
        if participant.bot_uuid == "human_1" { participant.message_view_scope = MessageViewScope::Participant; }
    }
    support.group.upsert(group.clone()).await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
    let flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
        .with_message_repo(repo).with_managed_deliveries(service.clone())
        .with_session_management(Arc::new(StaticSessionManagement::new(test_session("group-1:visibility", "group-1", group.participants))));
    for (id, audience, allowed) in [
        ("hidden", MessageAudience::FullOnly, false),
        ("public", MessageAudience::Public, true),
        ("directed", MessageAudience::directed(["human_1"]).unwrap(), true),
    ] {
        service.admit(AdmitMessageDeliveries {
            display_message: None, message_id: id.into(), flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            now_ms: 100, expire_at_ms: None, event: None,
            message: NewMessage { group_id: "group-1".into(), session_id: "group-1:visibility".into(),
                sender_id: "bot-driver".into(), sender_type: SenderType::Bot, message_type: "chat".into(),
                content: json!({"text":id}), client_msg_id: Some(id.into()), owner_bot_id: None,
                created_at: 100, run_id: id.into(), visibility_domain: MessageVisibilityDomain::ManagerWorker, audience: Some(audience) },
            targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: "bot-observer".into(), kind: DeliveryType::Inject,
                max_queued: 10, semantic_projection_json: json!({"version":1}) }],
        }).await.unwrap();
        let result = flow.query_message_deliveries(DeliveryStatusQuery {
            caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
            session_id: "group-1:visibility".into(), message_ids: vec![id.into()], client_msg_id: None,
        }).await;
        assert_eq!(result.is_ok(), allowed, "{id}: {result:?}");
    }
}

#[tokio::test]
async fn conformance_live_group_admission_uses_defaults_and_blocks_drain_bypass() {
    use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
    use bcs_message_flow::{delivery_policy::LiveDeliveryPolicy, managed_delivery::ManagedMessageDelivery};
    use bcs_service_api::{HumanActor, ManagedMessageDeliveryService};
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let live = Arc::new(LiveDeliveryPolicy::new(repo.clone(), Default::default()));
    live.scheduler_available.store(true, std::sync::atomic::Ordering::SeqCst);
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()).with_policy(live.clone()));
    let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
        .with_message_repo(repo).with_managed_deliveries(service.clone())
        .with_session_management(Arc::new(StaticSessionManagement::new(test_session("group-1:live", "group-1", group.participants.clone()))));
    flow.delivery_policy = Some(live.clone());
    let admin = || CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() });
    let mut policy = DeliveryPolicy::default();
    policy.flow_enabled.group = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    policy.defaults.max_queued = 1;
    flow.replace_delivery_policy(admin(), 0, policy.clone()).await.unwrap();
    let command = WebSendCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(), session_id: Some("group-1:live".into()), from_actor_id: "human_1".into(),
        from_name: None, message: "live defaults".into(), mentions: vec!["bot-driver".into()], attachments: None,
        thinking: None, idempotency_key: Some("live-1".into()), source_im_message_id: None, channel_sender_identity: None,
        sender_conn_id: None, provider_bypass_headers: vec![],
    };
    let first = flow.handle_web_send(command.clone()).await.unwrap().queue_admission.unwrap();
    assert_eq!(first.deliveries.len(), 2, "defaults cover both unlisted Bots");
    assert!(support.bot_delivery.frames().await.is_empty(), "managed ingress must not send directly");
    policy.flow_enabled.group = false;
    flow.replace_delivery_policy(admin(), 1, policy).await.unwrap();
    let mut next = command;
    next.idempotency_key = Some("live-2".into());
    assert!(flow.handle_web_send(next.clone()).await.is_err_and(|e| e.to_string().contains("queue_draining")));
    assert_eq!(service.snapshot(None).await.unwrap().len(), 2);
    // The unmentioned observer has only an unbound Inject. Once the old Send
    // settles, that context must not permanently block legacy ingress.
    let send = service.snapshot(None).await.unwrap().into_iter()
        .find(|row| row.state.kind == bcs_domain::DeliveryType::Send).unwrap();
    service.transition(bcs_service_api::DeliveryTransitionCommand {
        delivery_id: send.delivery_id, expected_state_version: send.state.state_version,
        event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested,
        now_ms: 100, request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
    }).await.unwrap();
    assert!(flow.handle_web_send(next).await.unwrap().queue_admission.is_none());
    assert!(!support.bot_delivery.frames().await.is_empty());
    assert!(service.snapshot(None).await.unwrap().iter().all(|row|
        row.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Cancelled));
}

#[tokio::test]
async fn conformance_queued_group_preparation_and_ingress() {
    use bcs_domain::{DeliveryType, NewMessage, SenderType};
    use bcs_message_flow::queued_group::{
        QueuedGroupPreparation, QueuedGroupProjection, queued_inbound_content,
    };
    use bcs_service_api::port::repo::message_delivery::*;
    use bcs_service_api::{
        CancelMessageDeliveryCommand, DeliveryStatusQuery, DeliveryTransitionCommand,
        ManagedDeliveryPreparationService, ManagedMessageDeliveryService,
    };
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service =
        Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(repo.clone()));
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let mut flow = Arc::new(
        BcsMessageFlow::new(
            support.group.clone(),
            support.routing.clone(),
            support.registry.clone(),
            support.bot_delivery.clone(),
            support.frontend_delivery.clone(),
        )
        .with_message_repo(repo.clone())
        .with_bot_run_context(run_context.clone())
        .with_managed_deliveries(service.clone())
        .with_group_delivery_limits(std::collections::BTreeMap::from([
            ("bot-driver".into(), 10),
            ("bot-observer".into(), 10),
        ]))
        .with_session_management(Arc::new(StaticSessionManagement::new(test_session(
            "group-1:queued",
            "group-1",
            group.participants.clone(),
        )))),
    );
    Arc::get_mut(&mut flow).unwrap().delivery_queue_ttl_ms = Some(60000);
    let command = WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".into(),
            staff_no: "1".into(),
        }),
        group_id: "group-1".into(),
        session_id: Some("group-1:queued".into()),
        from_actor_id: "human_1".into(),
        from_name: Some("Human One".into()),
        message: "@bot-driver 请查看文件".into(),
        mentions: vec!["bot-driver".into()],
        attachments: Some(
            serde_json::from_value(json!([{"attachment_id":"file-1", "type":"file",
            "file_name":"f.txt", "url":"https://example.invalid/queued-file", "expires_at":1}]))
            .unwrap(),
        ),
        thinking: None,
        idempotency_key: Some("queued-client".into()),
        source_im_message_id: Some("im-original".into()),
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    };
    let target = bcs_service_api::RoutingTarget {
        bot_uuid: "bot-driver".into(),
        url: String::new(),
        is_driver: true,
        delivery_type: DeliveryType::Send,
    };
    let decision = bcs_service_api::RoutingDecision {
        targets: vec![target.clone()],
        mentions: vec!["bot-driver".into()],
        cleaned_message: "bot-driver 请查看文件".into(),
        hidden_mentions: Vec::new(),
    };
    let projection = QueuedGroupProjection::capture(
        &flow,
        &group,
        &command,
        &decision,
        &target,
        "Human One".into(),
        None,
    )
    .await
    .unwrap();
    let projection = serde_json::to_value(projection).unwrap();
    assert!(!projection.to_string().contains("queued-file"));
    assert!(!projection.to_string().contains("请查看文件"));
    let row = service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: "queued-source".into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            now_ms: 1,
            expire_at_ms: None,
            event: None,
            targets: vec![DeliveryAdmissionTarget { rejection: None,
                target_bot_id: "bot-driver".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: projection,
            }],
            message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
                group_id: command.group_id.clone(),
                session_id: command.session_id.clone().unwrap(),
                sender_id: command.from_actor_id.clone(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: queued_inbound_content(&command),
                client_msg_id: command.idempotency_key.clone(),
                owner_bot_id: None,
                created_at: 1,
                run_id: String::new(),
            },
        })
        .await
        .unwrap()
        .deliveries
        .remove(0);
    let mut incoming = command.clone();
    incoming.idempotency_key = Some("real-ingress-client".into());
    let admitted = flow.handle_web_send(incoming.clone()).await.unwrap();
    assert_eq!(admitted.status, "queued");
    assert!(
        admitted.bot_deliveries.is_empty(),
        "managed ingress must not call legacy transport"
    );
    let view = admitted.queue_admission.unwrap();
    assert!(
        service
            .snapshot(None)
            .await
            .unwrap()
            .iter()
            .filter(|row| row.source_message_id == view.message_id)
            .all(|row| row.expire_at_ms == Some(row.created_at_ms + 60000))
    );
    assert_eq!(view.deliveries.len(), 2);
    assert!(!view.duplicate);
    let repeated = flow
        .handle_web_send(incoming)
        .await
        .unwrap()
        .queue_admission
        .unwrap();
    assert!(repeated.duplicate);
    assert_eq!(repeated.message_id, view.message_id);
    assert_eq!(service.snapshot(None).await.unwrap().len(), 3);
    let preparer = QueuedGroupPreparation {
        flow: Arc::downgrade(&flow),
        deliveries: service.clone(),
    };
    bcs_test_support::contract::application::message_delivery::managed_delivery_preparation_service_contract_tests(&preparer, &row).await.unwrap();
    let mut prepared = preparer.prepare(&row).await.unwrap();
    let wire = serde_json::to_value(&prepared.command.frame)
        .unwrap()
        .to_string();
    assert!(
        wire.contains("https://example.invalid/queued-file"),
        "queue must not drop the original URL, even when expired"
    );
    assert!(wire.contains("请查看文件"));
    assert_eq!(
        prepared.transport_context_json["connection_id"],
        "test-connection-bot-driver"
    );
    let started = service
        .transition(DeliveryTransitionCommand {
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
            now_ms: 2,
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: Some(prepared.transport_context_json),
            deadline_at_ms: Some(i64::MAX),
        })
        .await
        .unwrap();
    if let BcsFrame::Request(frame) = &mut prepared.command.frame {
        frame.id = started.request_id.clone().unwrap();
    }
    preparer
        .before_send(&started, &prepared.command)
        .await
        .unwrap();
    let active = run_context
        .find_active_run(started.request_id.as_deref().unwrap())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(active.canonical_run_id, started.run_id.clone().unwrap());
    assert_eq!(
        active.downstream_run_id, active.canonical_run_id,
        "request nonce is not an engine run id"
    );
    let abort = preparer.prepare_abort(&started).await.unwrap();
    assert_eq!(
        Some(abort.session_id.as_str()),
        active.downstream_session_key.as_deref()
    );
    assert_eq!(abort.run_id, started.run_id);
    let final_event = BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: started.run_id.clone().unwrap(),
        group_id: "group-1".into(),
        event_type: "chat".into(),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:queued".into()),
        event_payload: json!({"message":{"role":"assistant","content":[{"type":"text","text":"@Observer 收到，请检查"}]}}),
    };
    let count_before = support.group.message_count("group-1").await.unwrap();
    let terminal = flow.handle_bot_event(final_event.clone()).await.unwrap();
    assert_eq!(support.group.message_count("group-1").await.unwrap(), count_before + 1,
        "queued reply advances the relay limit once");
    assert!(
        terminal.bot_deliveries.is_empty(),
        "reply routing must also avoid legacy sends for queued targets"
    );
    let after_terminal = service.snapshot(None).await.unwrap();
    assert_eq!(
        after_terminal
            .iter()
            .find(|d| d.delivery_id == started.delivery_id)
            .unwrap()
            .state
            .status,
        bcs_domain::message_delivery::MessageDeliveryStatus::Completed
    );
    let reply_delivery = after_terminal
        .iter()
        .find(|d| {
            d.source_message_id != view.message_id
                && d.source_message_id != "queued-source"
                && d.target_bot_id == "bot-observer"
        })
        .unwrap();
    assert_eq!(reply_delivery.state.kind, DeliveryType::Send);
    assert_eq!(
        reply_delivery.state.status,
        bcs_domain::message_delivery::MessageDeliveryStatus::Queued
    );
    let reply_frame = preparer.prepare(reply_delivery).await.unwrap();
    let reply_json = serde_json::to_value(reply_frame.command.frame)
        .unwrap()
        .to_string();
    assert!(reply_json.contains("assistant"));
    assert!(reply_json.contains("请检查"));
    assert!(
        !reply_json.contains("https://example.invalid/queued-file"),
        "inject file permissions must survive context binding"
    );
    flow.handle_bot_event(final_event).await.unwrap();
    assert_eq!(support.group.message_count("group-1").await.unwrap(), count_before + 1,
        "duplicate final must not count the relay twice");
    assert_eq!(
        service.snapshot(None).await.unwrap().len(),
        after_terminal.len(),
        "duplicate final must not readmit reply targets"
    );
    let query = DeliveryStatusQuery {
        caller: command.caller.clone(),
        session_id: "group-1:queued".into(),
        message_ids: Vec::new(),
        client_msg_id: Some("real-ingress-client".into()),
    };
    let statuses = flow.query_message_deliveries(query.clone()).await.unwrap();
    assert_eq!(statuses.len(), 2);
    assert!(
        !serde_json::to_string(&statuses)
            .unwrap()
            .contains("queued-file")
    );
    let mut forbidden = query;
    forbidden.caller = CallerContext::Human(HumanActor {
        actor_id: "outsider".into(),
        staff_no: "outsider".into(),
    });
    assert!(flow.query_message_deliveries(forbidden).await.is_err());
    // Scope abort affects only active work, not the queued user message or
    // its pending/bound context. It uses the durable identity after cache loss.
    let mut reply_frame = preparer.prepare(reply_delivery).await.unwrap();
    let active_reply = service
        .transition(DeliveryTransitionCommand {
            delivery_id: reply_delivery.delivery_id.clone(),
            expected_state_version: reply_delivery.state.state_version,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend,
            now_ms: reply_delivery.available_at_ms,
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: Some(reply_frame.transport_context_json),
            deadline_at_ms: Some(i64::MAX),
        })
        .await
        .unwrap();
    if let BcsFrame::Request(frame) = &mut reply_frame.command.frame {
        frame.id = active_reply.request_id.clone().unwrap();
    }
    preparer
        .before_send(&active_reply, &reply_frame.command)
        .await
        .unwrap();
    let scope_abort = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: command.caller.clone(),
            group_id: "group-1".into(),
            session_id: "group-1:queued".into(),
            bot_id: "bot-observer".into(),
            run_id: None,
        })
        .await
        .unwrap();
    assert_eq!(
        scope_abort.aborted_run_ids,
        vec![active_reply.run_id.clone().unwrap()]
    );
    assert!(scope_abort.failures.is_empty());
    let cancelled = flow
        .cancel_message_deliveries(CancelMessageDeliveryCommand {
            caller: command.caller.clone(),
            session_id: "group-1:queued".into(),
            message_id: view.message_id,
            delivery_id: None,
        })
        .await
        .unwrap();
    assert!(cancelled.iter().any(
        |r| r.delivery.status == bcs_domain::message_delivery::MessageDeliveryStatus::Cancelled
    ));
    assert_eq!(
        support.bot_delivery.aborts().await.len(),
        1,
        "queued cancellation must not issue chat.abort"
    );
}
