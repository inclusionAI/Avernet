use super::*;

#[path = "../../../../../test-support/legacy_log_capture.rs"]
mod legacy;

#[test]
fn coverage_legacy_message_flow_reports_transport_failures_without_losing_routing() {
    legacy::isolated(|| {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            for failure in ["none", "rejected", "transport"] {
                let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
                match failure {
                    "rejected" => support.bot_delivery.not_delivered_for("bot-driver").await,
                    "transport" => support.bot_delivery.fail_for("bot-driver").await,
                    _ => {}
                }
                let contexts = Arc::new(MemoryBotRunContextStore::new());
                let flow = BcsMessageFlow::new(support.group.clone(), support.routing.clone(),
                    support.registry.clone(), support.bot_delivery.clone(), support.frontend_delivery.clone())
                    .with_bot_run_context(contexts.clone());
                let sent = flow.handle_web_send(WebSendCommand {
                    caller_execution: None, caller_ingress: None, speaking_lease: None,
                    caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
                    group_id: "group-1".into(), session_id: None, from_actor_id: "human_1".into(),
                    from_name: Some("Human One".into()), message: format!("legacy {failure}"),
                    mentions: Vec::new(), attachments: None, thinking: None, idempotency_key: None,
                    source_im_message_id: None, channel_sender_identity: None, sender_conn_id: None,
                    provider_bypass_headers: Vec::new(),
                }).await.unwrap();
                let driver = sent.bot_deliveries.iter().find(|delivery| delivery.target_bot_id == "bot-driver").unwrap();
                assert_eq!(driver.delivered, failure == "none", "{failure}: delivery outcome must retain rejection/error");
                assert_eq!(support.routing.route_calls().await.len(), 1);
                assert_eq!(sent.frontend_deliveries.len(), 1, "humans still see their submitted message");

                register_active_run(&contexts, "legacy-reply", "group-1", "group-1", "bot-observer", BotRunTransportOwner::WebSocket).await;
                let reply = flow.handle_bot_event(BotEventCommand {
                    bot_id: "bot-observer".into(), run_id: "legacy-reply".into(), group_id: "group-1".into(),
                    event_type: "chat.event".into(), event_payload: json!({"state":"final", "message":{
                        "role":"assistant", "content":[{"type":"text", "text":"legacy result"}]
                    }}), bcs_session_id: None, state: ChatEventState::Final,
                }).await.unwrap();
                assert_eq!(reply.frontend_deliveries.len(), 1);
                let relay = reply.bot_deliveries.iter().find(|delivery| delivery.target_bot_id == "bot-driver").unwrap();
                assert_eq!(relay.delivered, failure == "none");
                assert_eq!(support.routing.route_calls().await.len(), 2);
                assert!(contexts.list_active_runs(&BotRunScope {
                    group_id: "group-1".into(), session_id: "group-1".into(), bot_id: "bot-observer".into(),
                }).await.unwrap().is_empty(), "terminal input releases its source run even when relay fails");
            }
            let rows = legacy::rows("bcs_message");
            let delivery_logs: Vec<_> = rows.iter().filter(|row| row.message.contains("bot_deliver_result")).collect();
            assert!(!delivery_logs.is_empty());
            assert!(delivery_logs.iter().any(|row| row.message.contains("delivered=true")));
            let failures: Vec<_> = delivery_logs.iter().filter(|row| row.message.contains("delivered=false")).collect();
            assert!(failures.len() >= 4, "both rejected results and transport errors are observable on send and relay");
            for row in failures {
                row.assert_field("log_class", "business_key");
                assert_eq!(row.level, tracing::log::Level::Warn);
                assert!(row.message.contains("bot-driver"));
            }
        });
    });
}
