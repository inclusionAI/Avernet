use super::*;
use bcs_service_api::{BotEventCommand, ChatEventState, RoutingPolicy, RoutingMode, DefaultDelivery};

#[tokio::test]
async fn legacy_relay_cannot_downgrade_a_caller_target_to_owner() {
    for (enabled, unreadable) in [(false, false), (true, false), (false, true)] {
        let db = sqlite_db().await;
        if enabled { seed_caller_enabled_bearer_bot(db.as_ref(), "bot-driver").await; }
        if unreadable { db.execute(DbStatement::new("DROP TABLE bcs_bots")).await.unwrap(); }
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let mut group = support.group.get("group-1").await.unwrap();
        group.routing_policy = Some(RoutingPolicy {
            mode: RoutingMode::Structured,
            default_bot_final_delivery: DefaultDelivery::SendToDriver,
            ..RoutingPolicy::default()
        });
        support.group.upsert(group).await.unwrap();
        let store = Arc::new(MemoryProviderStore::default());
        let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
        let mut flow = BcsMessageFlow::new(
            support.group.clone(), support.routing.clone(), support.registry.clone(),
            support.bot_delivery.clone(), support.frontend_delivery.clone(),
        );
        flow.caller_admission = Some(Arc::new(CallerAdmissionDeps {
            handoff: Arc::new(HumanCallerDeliveryHandoff::new(16)),
            admission: managed,
            control_plane: Arc::new(BotControlPlaneCore::new(
                Arc::new(PersistentBotRepo::new(db)), store.clone(), store,
            )),
            reserve_ttl_ms: 10_000,
        }));
        let result = flow.handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".into(), run_id: "run-1".into(), group_id: "group-1".into(),
            event_type: "chat".into(), state: ChatEventState::Final, bcs_session_id: None,
            event_payload: json!({"state":"final", "message":{
                "role":"assistant", "content":[{"type":"text", "text":"done"}]
            }}),
        }).await.unwrap();
        assert_eq!(result.bot_deliveries.len(), 1);
        assert_eq!(result.bot_deliveries[0].delivered, !(enabled || unreadable));
        assert_eq!(support.bot_delivery.frames().await.is_empty(), enabled || unreadable);
    }
}
