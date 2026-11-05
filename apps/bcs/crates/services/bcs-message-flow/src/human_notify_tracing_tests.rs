//! Real HTTP ingress -> message flow -> mention spawn -> work-order backend.
use super::*;
use axum::{Router, body::Body, extract::Path, http::{HeaderMap, Request, StatusCode}, middleware, routing::post};
use bcs_tracing_api::{Carrier, Kind, NetworkTracer, Outcome, ParentContext, Record, RequestInfo, StartedTrace, with_provider};
use std::sync::{Mutex, atomic::{AtomicU64, Ordering}};
use tower::ServiceExt;

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

struct Trace { id: String, rpc: String, children: AtomicU64 }
#[derive(Default)]
struct Tracer(Mutex<Vec<(Kind, String, String)>>);
struct Completion;
impl Record for Completion {
    fn finish(self: Box<Self>, _: Outcome, _: Option<u64>) {}
}
impl NetworkTracer for Tracer {
    fn start(&self, info: &RequestInfo<'_>, incoming: &Carrier, parent: ParentContext<'_>) -> StartedTrace {
        let context = if info.kind == Kind::HttpServer {
            Trace {
                id: incoming.iter().find(|(key, _)| key == "x-notify-trace").expect("ingress trace").1.clone(),
                rpc: "0".into(), children: AtomicU64::new(0),
            }
        } else {
            let parent = parent.local.and_then(|context| context.downcast_ref::<Trace>()).expect("spawn retained parent");
            Trace {
                id: parent.id.clone(), rpc: format!("{}.{}", parent.rpc, parent.children.fetch_add(1, Ordering::Relaxed) + 1),
                children: AtomicU64::new(0),
            }
        };
        self.0.lock().expect("starts").push((info.kind, context.id.clone(), context.rpc.clone()));
        StartedTrace {
            remove_headers: vec![],
            headers: vec![("x-notify-trace".into(), context.id.clone()), ("x-notify-rpc".into(), context.rpc.clone())],
            context: Arc::new(context), record: Box::new(Completion),
        }
    }
}

struct WorkOrderPort {
    notifier: Arc<dyn bcs_human_notify_api::HumanMentionNotifier>,
    gate: Arc<tokio::sync::Semaphore>,
}
#[async_trait::async_trait]
impl HumanMentionNotifyPort for WorkOrderPort {
    fn is_available(&self) -> bool { true }
    async fn notify_mentioned_humans(&self, notification: MentionNotification) -> ServiceResult<()> {
        let _permit = self.gate.acquire().await.expect("test gate");
        self.notifier.notify(&bcs_human_notify_api::MentionNotification {
            session_id: notification.session_id, group_id: notification.group_id, group_name: notification.group_name,
            session_name: notification.session_name, sender_actor_id: notification.sender_actor_id, sender_label: notification.sender_label,
            mentioned: notification.mentioned.into_iter().map(|human| bcs_human_notify_api::MentionedHuman {
                actor_id: human.actor_id, display_name: human.display_name,
            }).collect(),
            message_text: notification.message_text, timestamp_ms: notification.timestamp_ms,
        }).await.map_err(|error| ServiceError::InternalError(error.to_string()))
    }
}

#[tokio::test]
async fn mentions_keep_isolated_request_traces_after_response_and_preserve_noop_scope() {
    use bcs_service_api::{BotRunContextPort, MessageFlowService};
    let (sent, mut received) = tokio::sync::mpsc::channel(4);
    let backend = Router::new().route("/notice", post(move |headers: HeaderMap, axum::Json(body): axum::Json<serde_json::Value>| {
        let sent = sent.clone();
        async move { sent.send((headers, body)).await.expect("capture request"); StatusCode::OK }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("loopback");
    let url = format!("http://{}", listener.local_addr().expect("address"));
    let server = tokio::spawn(async move { axum::serve(listener, backend).await.expect("backend"); });
    let notifier = bcs_human_notify_work_order::build_work_order_notifier(bcs_config_api::HumanNotifyProviderConfig {
        name: "work_order".into(), enabled: true,
        options: [("backend_base_url".into(), serde_json::json!(url)), ("event_path".into(), serde_json::json!("/notice")),
            ("signing_key".into(), serde_json::json!("test-key"))].into(),
    }).await.expect("work-order plugin");
    let gate = Arc::new(tokio::sync::Semaphore::new(0));
    let port: Option<Arc<dyn HumanMentionNotifyPort>> = Some(Arc::new(WorkOrderPort { notifier, gate: gate.clone() }));
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.expect("group");
    let mut human = bcs_domain::Participant::human("human_2", bcs_domain::ParticipantRole::Observer);
    human.bot_name = Some("Human Two".into());
    human.mode = Some(bcs_domain::ParticipantMode::Present);
    group.participants.push(human);
    support.group.upsert(group).await.expect("group update");
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let runs = Arc::new(crate::MemoryBotRunContextStore::new());
    runs.put_context(bcs_service_api::BotRunContext {
        run_id: "notify-bot".into(), bot_id: "bot-observer".into(), group_id: "group-1".into(),
        bcs_session_id: Some("group-1:s1".into()), deadline_ms: u64::MAX, terminal: false,
    }).await;
    let flow = Arc::new(crate::BcsMessageFlow::new(support.group, support.routing, support.registry,
        support.bot_delivery, support.frontend_delivery)
        .with_message_repo(repo.clone())
        .with_managed_deliveries(Arc::new(crate::managed_delivery::ManagedMessageDelivery::new(repo)))
        .with_bot_run_context(runs)
        .with_human_mention_notify(port.expect("notify port")));
    flow.retain_terminal_events();
    let web_flow = flow.clone();
    let router = Router::new().route("/groups/{id}/messages", post(move |Path(source): Path<String>| {
        let flow = web_flow.clone();
        async move {
            flow.handle_web_send(bcs_service_api::WebSendCommand {
                caller_execution: None, caller_ingress: None, speaking_lease: None,
                caller: bcs_service_api::CallerContext::Human(bcs_service_api::HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
                group_id: "group-1".into(), session_id: Some("group-1:s1".into()), from_actor_id: "human_1".into(),
                from_name: Some("Human One".into()), message: format!("notify-{source}"), mentions: vec!["human_2".into()],
                attachments: None, thinking: None, idempotency_key: None, source_im_message_id: None,
                channel_sender_identity: None, sender_conn_id: None, provider_bypass_headers: vec![],
            }).await.expect("accepted group message");
            StatusCode::ACCEPTED
        }
    })).route("/bot/events", post(move || {
        let flow = flow.clone();
        async move {
            flow.handle_bot_event(bcs_service_api::BotEventCommand {
                bot_id: "bot-observer".into(), run_id: "notify-bot".into(), group_id: "group-1".into(),
                bcs_session_id: Some("group-1:s1".into()), state: bcs_service_api::ChatEventState::Final, event_type: "chat".into(),
                event_payload: serde_json::json!({"message":{"content":[{"type":"text","text":"@Human Two notify-bot"}]}}),
            }).await.expect("accepted bot final");
            StatusCode::ACCEPTED
        }
    })).layer(middleware::from_fn_with_state(bcs_network_tracing::HttpServerFilter::default(), bcs_network_tracing::http_server));
    let tracer = Arc::new(Tracer::default());
    for (group, enabled) in [("first", true), ("second", true), ("disabled", false)] {
        let provider: Option<Arc<dyn NetworkTracer>> = if enabled { Some(tracer.clone()) } else { None };
        let response = with_provider(provider, router.clone().oneshot(Request::post(format!("/groups/{group}/messages"))
            .header("x-notify-trace", group).body(Body::empty()).expect("request"))).await.expect("response");
        assert_eq!(response.status(), StatusCode::ACCEPTED);
    }
    let response = with_provider(Some(tracer.clone()), router.oneshot(Request::post("/bot/events")
        .header("x-notify-trace", "bot").body(Body::empty()).expect("request"))).await.expect("response");
    assert_eq!(response.status(), StatusCode::ACCEPTED);
    // All HTTP request scopes have ended before any notification may send.
    assert!(bcs_tracing_api::current_context().is_none());
    gate.add_permits(4);
    let mut seen = Vec::new();
    for _ in 0..4 {
        let (headers, body) = tokio::time::timeout(std::time::Duration::from_secs(5), received.recv())
            .await.unwrap_or_else(|_| panic!("notification deadline; received sources: {seen:?}" )).expect("notification");
        assert!(headers.contains_key("x-avernet-principal"));
        assert_eq!(body["biz_data"]["group_id"], "group-1");
        let content = body["content"]["text"].as_str().expect("notification text");
        let group = ["first", "second", "disabled", "bot"].into_iter()
            .find(|source| content.contains(&format!("notify-{source}"))).expect("source message").to_owned();
        if group == "disabled" {
            assert!(!headers.contains_key("x-notify-trace"));
        } else {
            assert_eq!(headers["x-notify-trace"], group);
            assert_eq!(headers["x-notify-rpc"], "0.1");
        }
        seen.push(group);
    }
    seen.sort();
    assert_eq!(seen, ["bot", "disabled", "first", "second"]);
    let starts = tracer.0.lock().expect("starts");
    assert_eq!(starts.len(), 6);
    assert_eq!(starts.iter().filter(|(kind, _, _)| *kind == Kind::HttpClient).count(), 3);
    server.abort();
}
