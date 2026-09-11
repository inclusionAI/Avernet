use std::sync::Arc;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore, managed_delivery::ManagedMessageDelivery, queued_group::QueuedGroupPreparation};
use bcs_service_api::{CallerContext, GroupCoreService, HumanActor, ManagedDeliveryPreparationService, ManagedMessageDeliveryService, MessageFlowService, WebSendCommand};
use bcs_domain::{DeliveryType, message_delivery::MessageDeliveryStatus as Status};
use serde_json::json;
#[path = "support/session.rs"] mod session;
#[path = "../../../test-support/message_flow_contract_support.rs"] mod support;

fn command(id: &str, name: &str, value: &str) -> WebSendCommand {
    WebSendCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(), session_id: Some("group-1:headers".into()), from_actor_id: "human_1".into(),
        from_name: None, message: "hello".into(), mentions: vec!["bot-driver".into()], attachments: None,
        thinking: None, idempotency_key: Some(id.into()), source_im_message_id: None, channel_sender_identity: None,
        sender_conn_id: None, provider_bypass_headers: vec![(name.into(), value.into())],
    }
}

async fn fixture() -> (support::FlowTestSupport, Arc<BcsMessageFlow>, Arc<ManagedMessageDelivery>) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_delivery_target("bot-driver", support::FakeRegistryService::provider_target("bot-driver")).await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let mut flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(), support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
        .with_message_repo(repo).with_managed_deliveries(service.clone())
        .with_bot_run_context(Arc::new(MemoryBotRunContextStore::new()))
        .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(),10),("bot-observer".into(),10)]))
        .with_session_management(Arc::new(session::StaticSessionManagement::new(session::test_session("group-1:headers","group-1",group.participants))));
    flow.queue_persistable_headers = vec!["x-routing-zone".into()];
    (support, Arc::new(flow), service)
}

#[tokio::test]
async fn rejects_only_provider_target_without_persisting_unsupported_values() {
    let (support, flow, service) = fixture().await;
    let result = flow.handle_web_send(command("reject", "cookie", "do-not-persist")).await.unwrap();
    let view = result.queue_admission.unwrap();
    let provider = view.deliveries.iter().find(|d| d.target_bot_id == "bot-driver").unwrap();
    assert_eq!(provider.status, Status::Failed);
    assert_eq!(provider.admission_error, Some("delivery_provider_headers_unsupported"));
    assert_eq!(view.deliveries.iter().find(|d| d.target_bot_id == "bot-observer").unwrap().status, Status::PendingContext);
    let rows = service.snapshot(None).await.unwrap();
    for row in rows {
        assert!(!row.semantic_projection_json.to_string().contains("do-not-persist"));
    }
    assert!(support.bot_delivery.frames().await.is_empty());
    assert!(!serde_json::to_string(&view).unwrap().contains("do-not-persist"));
}

#[tokio::test]
async fn persists_route_per_send_restores_abort_and_relay_without_model_leakage() {
    use bcs_service_api::{DeliveryTransitionCommand, ChatAbortCommand, BotEventCommand, ChatEventState};
    use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
    for provider in [true, false] {
    let (support, flow, service) = fixture().await;
    if !provider {
        support.registry.set_delivery_target("bot-driver", bcs_service_api::BotDeliveryTarget::WebSocket { bot_id: "bot-driver".into() }).await;
    }
    // A pending Inject may carry another routing snapshot but cannot replace
    // the carrier's route when both are composed into one model request.
    let mut injected = command("inject", "x-routing-zone", "context-zone");
    injected.mentions = vec!["bot-observer".into()];
    flow.handle_web_send(injected).await.unwrap();
    let view = flow.handle_web_send(command("send", "X-Routing-Zone", "carrier-zone")).await.unwrap().queue_admission.unwrap();
    let row = service.snapshot(None).await.unwrap().into_iter().find(|d| d.source_message_id == view.message_id && d.state.kind == DeliveryType::Send).unwrap();
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: service.clone() };
    let mut prepared = preparer.prepare(&row).await.unwrap();
    let expected = vec![("x-routing-zone".into(), "carrier-zone".into())];
    let transport_headers = if provider { expected.clone() } else { Vec::new() };
    assert_eq!(prepared.command.provider_bypass_headers, transport_headers);
    let frame = serde_json::to_string(&prepared.command.frame).unwrap();
    assert!(!frame.contains("carrier-zone") && !frame.contains("context-zone"));
    assert!(!serde_json::to_string(&view).unwrap().contains("carrier-zone"));
    let started = service.transition(DeliveryTransitionCommand {
        delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
        event: Event::StartSend, now_ms: chrono::Utc::now().timestamp_millis(), request_id: None, actor_id: None, reply: None,
        transport_context_json: Some(prepared.transport_context_json.clone()), deadline_at_ms: Some(i64::MAX),
    }).await.unwrap();
    if let bcs_protocol::BcsFrame::Request(frame) = &mut prepared.command.frame { frame.id = started.request_id.clone().unwrap(); }
    preparer.before_send(&started, &prepared.command).await.unwrap();
    let active = flow.bot_run_context.as_ref().unwrap().find_active_run(started.run_id.as_deref().unwrap()).await.unwrap().unwrap();
    assert_eq!(active.provider_bypass_headers, transport_headers);
    let mut mismatched = prepared.command.clone();
    mismatched.provider_bypass_headers = vec![("x-routing-zone".into(), "incorrect-zone".into())];
    assert!(preparer.before_send(&started, &mismatched).await.is_err());
    // Recover abort from durable transport, not an active-run cache or the
    // cancelling caller. Exact Provider abort remains unsupported.
    if provider {
    assert!(preparer.prepare_abort(&started).await.is_err());
    flow.bot_run_context.as_ref().unwrap().remove_active_run(&active.scope, started.run_id.as_deref().unwrap()).await.unwrap();
    flow.handle_chat_abort(ChatAbortCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(), session_id: "group-1:headers".into(), bot_id: "bot-driver".into(), run_id: None,
    }).await.unwrap();
    assert_eq!(support.bot_delivery.aborts().await[0].provider_bypass_headers, expected);
    }
    support.registry.set_delivery_target("bot-observer", support::FakeRegistryService::provider_target("bot-observer")).await;
    flow.handle_bot_event(BotEventCommand { bot_id: "bot-driver".into(), run_id: started.run_id.unwrap(), group_id: "group-1".into(),
        event_type: "chat".into(), state: ChatEventState::Final, bcs_session_id: Some("group-1:headers".into()),
        event_payload: json!({"message":{"role":"assistant","content":[{"type":"text","text":"@Observer check this"}]}}),
    }).await.unwrap();
    let reply = service.snapshot(None).await.unwrap().into_iter().find(|d| d.target_bot_id == "bot-observer" && d.state.kind == DeliveryType::Send && d.source_session_seq > row.source_session_seq).unwrap();
    assert_eq!(reply.semantic_projection_json["provider_route_headers"], json!(expected));
    }
}

#[tokio::test]
async fn configuration_tightening_fails_instead_of_sending_without_route() {
    let (_, mut flow, service) = fixture().await;
    let view = flow.handle_web_send(command("tighten", "x-routing-zone", "original-zone")).await.unwrap().queue_admission.unwrap();
    let row = service.snapshot(None).await.unwrap().into_iter().find(|d| d.source_message_id == view.message_id && d.state.kind == DeliveryType::Send).unwrap();
    Arc::get_mut(&mut flow).unwrap().queue_persistable_headers.clear();
    let preparer = QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: service };
    assert!(preparer.prepare(&row).await.is_err());
    // Old persisted rows without headers remain readable and use default route.
    let mut old = row;
    old.semantic_projection_json.as_object_mut().unwrap().remove("provider_route_headers");
    assert!(preparer.prepare(&old).await.unwrap().command.provider_bypass_headers.is_empty());
}

#[tokio::test]
async fn scope_abort_rejects_default_and_named_route_mix() {
    let (support, flow, _) = fixture().await;
    for (id, headers) in [("default", Vec::new()), ("named", vec![("x-routing-zone".into(), "blue".into())])] {
        flow.bot_run_context.as_ref().unwrap().put_context(bcs_service_api::BotRunContext {
            run_id: id.into(), bot_id: "bot-driver".into(), group_id: "group-1".into(),
            bcs_session_id: Some("group-1:headers".into()), deadline_ms: u64::MAX, terminal: false,
        }).await;
        flow.bot_run_context.as_ref().unwrap().register_active_run(bcs_service_api::ActiveBotRunContext {
            canonical_run_id: id.into(), downstream_run_id: id.into(), downstream_session_key: Some("group-1:headers".into()),
            scope: bcs_service_api::BotRunScope { group_id: "group-1".into(), session_id: "group-1:headers".into(), bot_id: "bot-driver".into() },
            transport_owner: bcs_service_api::BotRunTransportOwner::HttpProvider { provider_id: "provider-1".into(), provider_bot_ref: "bot-driver".into() },
            provider_bypass_headers: headers, deadline_ms: u64::MAX,
        }).await.unwrap();
    }
    let outcome = flow.handle_chat_abort(bcs_service_api::ChatAbortCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
        group_id: "group-1".into(), session_id: "group-1:headers".into(), bot_id: "bot-driver".into(), run_id: None,
    }).await.unwrap();
    assert!(outcome.aborted_run_ids.is_empty());
    assert!(support.bot_delivery.aborts().await.is_empty(), "never choose one route arbitrarily");
}
