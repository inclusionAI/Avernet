use std::sync::Arc;
use bcs_domain::{DeliveryType, NewMessage, SenderType, MessageOwnerFilter, message_delivery::DeliveryFlowKind};
use bcs_message_flow::{BcsMessageFlow, managed_delivery::ManagedMessageDelivery};
use bcs_service_api::{BotEventCommand, ChatEventState, MessageFlowService, ManagedMessageDeliveryService, DeliveryTransitionCommand, GroupCoreService};
use bcs_service_api::port::repo::{MessageRepoPort, message_delivery::*};
use serde_json::json;

#[derive(Default)]
struct RecordingEventFactory(std::sync::Mutex<Vec<bcs_service_api::port::NewEvent>>);
impl bcs_service_api::port::EventRecordFactoryPort for RecordingEventFactory {
    fn prepare(&self, event: bcs_service_api::port::NewEvent) -> Result<Option<bcs_service_api::port::repo::AppendEventRecord>, bcs_service_api::port::EventRecordError> {
        self.0.lock().unwrap().push(event.clone());
        Ok(Some(bcs_service_api::port::repo::AppendEventRecord { event, env: "local".into(), recorded_at: "2026-09-10T00:00:00.000Z".into(), retention_until_ms: i64::MAX as u64 }))
    }
}

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "support/session.rs"]
mod session_support;

#[path = "support/failing_delivery.rs"]
mod failing_delivery;

struct FailingHistory {
    inner: Arc<bcs_message_store::MemoryMessageRepo>,
    failures: std::sync::atomic::AtomicUsize,
}
#[async_trait::async_trait]
impl MessageRepoPort for FailingHistory {
    async fn run_chat_segments(&self, session: &str, sender: &str, run: &str) -> Result<Vec<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError> {
        if self.failures.fetch_update(std::sync::atomic::Ordering::SeqCst, std::sync::atomic::Ordering::SeqCst, |n| n.checked_sub(1)).is_ok() {
            return Err(bcs_service_api::port::repo::MessageRepoError::StorageError("injected history read failure".into()));
        }
        self.inner.run_chat_segments(session, sender, run).await
    }
    async fn append_message(&self, message: NewMessage) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> { self.inner.append_message(message).await }
    async fn append_message_with_event(&self, command: bcs_service_api::port::repo::AppendMessageWithEvent) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> { self.inner.append_message_with_event(command).await }
    async fn query_messages(&self, query: bcs_domain::MessageQuery) -> Result<bcs_domain::MessagePage, bcs_service_api::port::repo::MessageRepoError> { self.inner.query_messages(query).await }
    async fn get_message_by_id(&self, session: &str, id: &str) -> Result<Option<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError> { self.inner.get_message_by_id(session, id).await }
    async fn get_current_seq(&self, session: &str) -> Result<i64, bcs_service_api::port::repo::MessageRepoError> { self.inner.get_current_seq(session).await }
    async fn list_session_history(&self, session: &str, owner: MessageOwnerFilter, visible: Option<i64>, human_view: Option<bcs_domain::HumanMessageView>, before: Option<(u64, i64)>, limit: u32) -> bcs_service_api::ServiceResult<bcs_domain::MessagePage> { self.inner.list_session_history(session, owner, visible, human_view, before, limit).await }
}

#[tokio::test]
async fn terminal_storage_faults_preserve_reply_and_publish_after_commit_even_if_caller_disconnects() {
    use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
    for (state, fault, expected_status, disconnect) in [
        (ChatEventState::Final, "completed", Status::Completed, false),
        (ChatEventState::Final, "completed_after_commit", Status::Completed, false),
        (ChatEventState::Error, "failed", Status::Failed, false),
        (ChatEventState::Aborted, "aborted", Status::Cancelled, false),
        (ChatEventState::Final, "completed", Status::Completed, true),
    ] {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let frontend = support.frontend_delivery.clone();
        let bots = support.bot_delivery.clone();
        let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
        let history = Arc::new(FailingHistory { inner: repo.clone(), failures: Default::default() });
        let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
        let faults = Arc::new(failing_delivery::FailingDelivery::new(service.clone()));
        let group = support.group.get("group-1").await.unwrap();
        let flow = Arc::new(BcsMessageFlow::new(support.group, support.routing, support.registry, support.bot_delivery, support.frontend_delivery)
            .with_message_repo(history.clone()).with_managed_deliveries(faults.clone())
            .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 100), ("bot-observer".into(), 100)]))
            .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session_support::test_session("group-1:retry", "group-1", group.participants)))));
        flow.retain_terminal_events();
        let input = service.admit(AdmitMessageDeliveries { display_message: None, message_id: "input".into(),
            message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None, group_id: "group-1".into(), session_id: "group-1:retry".into(), sender_id: "human".into(),
                sender_type: SenderType::Human, message_type: "chat".into(), content: json!({"text":"question"}),
                client_msg_id: Some("input".into()), owner_bot_id: None, created_at: 1, run_id: String::new() },
            flow_kind: DeliveryFlowKind::Group, targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: "bot-driver".into(), kind: DeliveryType::Send,
                max_queued: 100, semantic_projection_json: json!({"version":1}) }], now_ms: 1, expire_at_ms: None, event: None }).await.unwrap().deliveries.remove(0);
        let started = service.transition(DeliveryTransitionCommand { delivery_id: input.delivery_id.clone(), expected_state_version: 1,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend, now_ms: 2, request_id: None, actor_id: None,
            reply: None, transport_context_json: Some(json!({"version":1,"owner":{"kind":"web_socket"},"connection_id":"test","downstream_session_key":"session"})), deadline_at_ms: Some(i64::MAX) }).await.unwrap();
        let mut terminal = BotEventCommand { bot_id: "bot-driver".into(), run_id: started.run_id.clone().unwrap(), group_id: "group-1".into(),
            bcs_session_id: Some("group-1:retry".into()), state: ChatEventState::Delta, event_type: "chat".into(), event_payload: json!({"delta_text":"保留的正文"}) };
        flow.handle_bot_event(terminal.clone()).await.unwrap();
        terminal.state = state.clone();
        terminal.event_payload = json!({"message":{"content":[{"type":"text","text":"保留的正文"}]}});
        let before = frontend.events().await.len();
        faults.arm("lookup", 1);
        faults.arm(fault, 1);
        if state == ChatEventState::Final { history.failures.store(1, std::sync::atomic::Ordering::SeqCst); }
        let task_flow = flow.clone(); let event = terminal.clone();
        let caller = tokio::spawn(async move { task_flow.handle_bot_event(event).await });
        tokio::time::timeout(std::time::Duration::from_secs(1), faults.failed.notified()).await.unwrap();
        assert!(!caller.is_finished());
        assert_eq!(frontend.events().await.len(), before);
        assert_eq!(service.active_count("bot-driver").await.unwrap(), 1);
        if disconnect { caller.abort(); let _ = caller.await; }
        else { tokio::time::timeout(std::time::Duration::from_secs(3), caller).await.unwrap().unwrap().unwrap(); }
        tokio::time::timeout(std::time::Duration::from_secs(3), async {
            while frontend.events().await.len() == before { tokio::time::sleep(std::time::Duration::from_millis(5)).await; }
        }).await.unwrap();
        let rows = service.snapshot(None).await.unwrap();
        assert_eq!(rows.iter().find(|r| r.delivery_id == input.delivery_id).unwrap().state.status, expected_status);
        assert_eq!(service.active_count("bot-driver").await.unwrap(), 0);
        assert_eq!(faults.remaining(), 0);
        assert_eq!(history.failures.load(std::sync::atomic::Ordering::SeqCst), 0);
        assert!(bots.frames().await.is_empty(), "persistence retries must not invoke Bot transport");
        if state == ChatEventState::Final {
            let reply = rows.iter().find(|r| r.source_message_id != "input").unwrap();
            let message = repo.get_message_by_id("group-1:retry", &reply.source_message_id).await.unwrap().unwrap();
            assert_eq!(message.message_type, "run_reply");
            assert_eq!(message.content["text"], "保留的正文");
        }
        let published = frontend.events().await.len();
        let seq = repo.get_current_seq("group-1:retry").await.unwrap();
        flow.handle_bot_event(terminal).await.unwrap();
        assert_eq!(frontend.events().await.len(), published);
        assert_eq!(repo.get_current_seq("group-1:retry").await.unwrap(), seq);
    }
}

#[tokio::test]
async fn mixed_final_modes_reconstruct_one_reply_and_preserve_visible_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let event_store = Arc::new(bcs_event_store::MemoryEventStore::new());
    let factory = Arc::new(RecordingEventFactory::default());
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new().with_environment("local".into()).with_event_store(event_store.clone()));
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let group = support.group.get("group-1").await.unwrap();
    let mut flow = BcsMessageFlow::new(support.group, support.routing, support.registry, support.bot_delivery, support.frontend_delivery)
        .with_message_repo(repo.clone()).with_managed_deliveries(service.clone())
        .with_event_record_factory(factory.clone())
        .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 100), ("bot-observer".into(), 100)]))
        .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session_support::test_session("group-1:reply", "group-1", group.participants))));
    for (index, final_text, expected, current) in [
        (0, "工具前\n工具后补充", "工具前\n工具后补充", "工具后"),
        (1, "工具后补充", "工具前\n工具后补充", "工具后"),
        (2, "补充", "工具前\n工具后补充", "工具后"),
        (3, "", "工具前\n工具后", "工具后"),
        (4, "", "工具前", ""),
    ] {
        let source = service.admit(AdmitMessageDeliveries { display_message: None,
            message_id: format!("input-{index}"), message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None, group_id: "group-1".into(), session_id: "group-1:reply".into(),
                sender_id: "human".into(), sender_type: SenderType::Human, message_type: "chat".into(), content: json!({"text":"question"}),
                client_msg_id: Some(format!("input-{index}")), owner_bot_id: None, created_at: 1, run_id: String::new() },
            flow_kind: DeliveryFlowKind::Group, targets: vec![DeliveryAdmissionTarget { rejection: None, target_bot_id: "bot-driver".into(), kind: DeliveryType::Send,
                max_queued: 100, semantic_projection_json: json!({"version":1}) }], now_ms: 1, expire_at_ms: None, event: None }).await.unwrap();
        let input = &source.deliveries[0];
        let started = service.transition(DeliveryTransitionCommand { delivery_id: input.delivery_id.clone(), expected_state_version: 1,
            event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::StartSend, now_ms: 2, request_id: None, actor_id: None,
            reply: None, transport_context_json: Some(json!({"version":1,"owner":{"kind":"web_socket"},"connection_id":"test","downstream_session_key":"session"})), deadline_at_ms: Some(i64::MAX) }).await.unwrap();
        let run = started.run_id.as_ref().unwrap();
        let event = |state, event_type: &str, payload| BotEventCommand { bot_id: "bot-driver".into(), run_id: run.clone(), group_id: "group-1".into(),
            bcs_session_id: Some("group-1:reply".into()), state, event_type: event_type.into(), event_payload: payload };
        flow.handle_bot_event(event(ChatEventState::Delta, "chat", json!({"delta_text":"工具前"}))).await.unwrap();
        flow.handle_bot_event(event(ChatEventState::Delta, "agent", json!({"stream":"tool","data":{"phase":"result","name":"search","toolCallId":format!("tool-{index}"),"result":"TOOL BODY MUST NOT ENTER REPLY"}}))).await.unwrap();
        // Interleaved same-run output from another Bot must not pollute C or H.
        let mut other = event(ChatEventState::Delta, "chat", json!({"delta_text":"OTHER BOT"})); other.bot_id = "bot-observer".into();
        flow.handle_bot_event(other).await.unwrap();
        if !current.is_empty() { flow.handle_bot_event(event(ChatEventState::Delta, "chat", json!({"delta_text":current}))).await.unwrap(); }
        if index == 4 {
            // Rebuild the application with empty streaming buffers: the closed
            // prefix survives because reconstruction reads committed chat rows.
            flow = BcsMessageFlow::new(flow.group.clone(), flow.routing.clone(), flow.registry.clone(), flow.bot_delivery.clone(), flow.frontend_delivery.clone())
                .with_message_repo(repo.clone()).with_managed_deliveries(service.clone())
                .with_event_record_factory(factory.clone())
                .with_group_delivery_limits(std::collections::BTreeMap::from([("bot-driver".into(), 100), ("bot-observer".into(), 100)]))
                .with_session_management(flow.session_management.clone().unwrap());
        }
        let payload = if index == 2 { json!({"delta_text":final_text}) } else { json!({"message":{"role":"assistant","content":[{"type":"text","text":final_text}]}}) };
        let terminal = event(ChatEventState::Final, "chat", payload);
        flow.handle_bot_event(terminal.clone()).await.unwrap();
        let deliveries = service.snapshot(Some("group-1:reply")).await.unwrap();
        let mut summaries = Vec::new();
        for delivery in &deliveries {
            let message = repo.get_message_by_id("group-1:reply", &delivery.source_message_id).await.unwrap().unwrap();
            if message.run_id == *run && message.message_type == "run_reply" { summaries.push(message); }
        }
        assert!(!summaries.is_empty());
        assert_eq!(summaries.iter().map(|m| &m.message_id).collect::<std::collections::BTreeSet<_>>().len(), 1, "one logical reply shared by targets");
        let summary = &summaries[0];
        let targets: Vec<_> = deliveries.iter().filter(|d| d.source_message_id == summary.message_id).map(|d| &d.target_bot_id).collect();
        assert_eq!(targets.len(), targets.iter().collect::<std::collections::BTreeSet<_>>().len(), "one delivery per reply × target");
        assert_eq!(summary.content["text"], expected);
        assert_eq!(summary.content["normalization"]["raw_final"], final_text);
        assert!(!summary.content["text"].as_str().unwrap().contains("TOOL BODY"));
        let history = repo.list_session_history("group-1:reply", MessageOwnerFilter::Any, None, None, None, 100).await.unwrap();
        assert!(history.messages.iter().all(|m| m.message_type != "run_reply"));
        let visible = repo.run_chat_segments("group-1:reply", "bot-driver", run).await.unwrap();
        assert_eq!(visible.iter().filter_map(|m| m.content.as_str()).collect::<Vec<_>>().join("\n"), expected);
        flow.handle_bot_event(terminal).await.unwrap();
        assert_eq!(service.snapshot(Some("group-1:reply")).await.unwrap().len(), deliveries.len());
        let events = factory.0.lock().unwrap().clone();
        assert!(events.iter().all(|e| e.data.get("message_type") != Some(&json!("run_reply"))));
        assert!(events.iter().all(|e| !serde_json::to_string(&e.data).unwrap().contains("normalization")));
        for event in events {
            use bcs_service_api::port::repo::EventRepoPort;
            assert!(event_store.get_event(&event.event_id, "local").await.unwrap().is_some());
        }
        // The fixed routing fake may include a Send back to the driver. Cancel
        // these unsent outputs before starting the next independent test run.
        for delivery in deliveries.iter().filter(|d| d.source_message_id == summary.message_id && d.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Queued) {
            service.transition(DeliveryTransitionCommand { delivery_id: delivery.delivery_id.clone(), expected_state_version: delivery.state.state_version,
                event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelRequested, now_ms: delivery.created_at_ms,
                request_id: None, actor_id: Some("test".into()), reply: None, transport_context_json: None, deadline_at_ms: None }).await.unwrap();
        }
    }
}
