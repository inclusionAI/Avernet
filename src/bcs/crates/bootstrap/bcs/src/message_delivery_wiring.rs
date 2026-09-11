//! Durable single-instance scheduler composition. Deployment guarantees exclusivity.
use bcs_message_flow::{
    BcsMessageFlow,
    delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig},
    managed_delivery::ManagedMessageDelivery,
    queued_group::QueuedGroupPreparation,
};
use bcs_service_api::application::message_delivery::ManagedMessageDeliveryService;
use std::{collections::BTreeMap, sync::Arc, time::Duration};

#[cfg(test)]
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod wiring_support;
#[cfg(test)]
#[path = "../../../services/bcs-message-flow/tests/support/session.rs"]
mod session_support;

struct SchedulerGuard {
    policy: Arc<bcs_message_flow::delivery_policy::LiveDeliveryPolicy>,
    service: Arc<ManagedMessageDelivery>,
    completion: tokio::sync::watch::Sender<bool>,
}

/// Stop a scheduler if later composition fails, including collaborators with
/// legacy reference cycles. Disarm only once the server owns shutdown.
pub(crate) struct StartupGuard(pub Option<Arc<dyn bcs_service_api::MessageFlowService>>);
impl Drop for StartupGuard {
    fn drop(&mut self) {
        if let Some(flow) = self.0.take() {
            if let Ok(runtime) = tokio::runtime::Handle::try_current() {
                runtime.spawn(async move {
                    if let Err(error) = flow.shutdown_managed_delivery().await {
                        tracing::error!(%error, "failed to stop delivery scheduler after composition failure");
                    }
                });
            }
        }
    }
}
impl Drop for SchedulerGuard {
    fn drop(&mut self) {
        self.service.set_admission_available(false);
        self.policy.scheduler_available.store(false, std::sync::atomic::Ordering::SeqCst);
        let _ = self.completion.send(true);
    }
}

pub async fn wire(
    mut flow: BcsMessageFlow,
    config: &crate::BcsConfig,
) -> crate::Result<Arc<BcsMessageFlow>> {
    use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
    use std::sync::atomic::Ordering;
    let invalid = |message: &str| crate::BcsError::InvalidConfig(message.into());
    if config.message_delivery != Default::default() {
        return Err(invalid("message_delivery business policy moved to DB: remove TOML flow_enabled/bots/TTL/retry/pause values and use the management API"));
    }
    let repository = flow.message_repo.clone().and_then(|repo| repo.delivery_repository())
        .ok_or_else(|| invalid("message store does not support delivery transactions"))?;
    let initial = repository.load_policy().await.map_err(|_| invalid("cannot load durable delivery policy"))?;
    initial.policy.validate().map_err(|e| invalid(&e.to_string()))?;
    let durable = repository.is_durable();
    config.provider_http.validate().map_err(|e| invalid(&e))?;
    flow.queue_persistable_headers = config.provider_http.queue_persistable_headers.clone();
    let live = Arc::new(LiveDeliveryPolicy::new(repository.clone(), initial.clone()));
    let service = ManagedMessageDelivery::new(repository).with_policy(live.clone());
    let metrics = Arc::new(crate::delivery_metrics::DeliveryMetrics::default());
    let service = service.with_instrumentation(metrics.clone());
    let service = Arc::new(service);
    service.set_admission_available(false);
    let rows = service.queue_statistics().await.map_err(|_| invalid("cannot read message delivery recovery state"))?;
    let pending = !rows.is_empty();
    flow.delivery_policy = Some(live.clone());
    flow = flow.with_managed_deliveries(service.clone());
    let flow = Arc::new(flow);
    flow.retain_terminal_events();
    // Durable storage always installs an idle scheduler, even when all policy
    // switches are off. Deployment owns single-instance, non-overlapping runs.
    if !durable {
        if initial.policy.needs_scheduler() || pending {
            return Err(invalid("message delivery scheduler requires SQLite or MySQL/OceanBase"));
        }
        return Ok(flow);
    }
    let (completion_sender, completion_receiver) = tokio::sync::watch::channel(false);
    let guard = SchedulerGuard { service: service.clone(), policy: live.clone(), completion: completion_sender };
    service.recover(chrono::Utc::now().timestamp_millis()).await.map_err(|_| invalid("message delivery startup recovery failed"))?;
    let runtime = DeliveryRuntime {
        policy: Some(live.clone()),
        service: service.clone(),
        preparation: Arc::new(QueuedGroupPreparation {
            flow: Arc::downgrade(&flow), deliveries: service.clone(),
        }),
        transport: flow.bot_delivery.clone(),
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0, bots: BTreeMap::new(), pause_dispatch: false,
            tick: Duration::from_millis(100), io_timeout: Duration::from_secs(30),
            run_timeout: Duration::from_millis(config.provider_chat_run_timeout_ms),
            cancel_timeout: Duration::from_secs(30), max_tasks: 32, max_abort_tasks: 2,
        },
    };
    let (sender, receiver) = tokio::sync::watch::channel(false);
    flow.delivery_shutdown.set((sender, completion_receiver)).map_err(|_| invalid("queue shutdown hook already installed"))?;
    service.set_admission_available(true);
    live.scheduler_available.store(true, Ordering::SeqCst);
    let (metrics_stop, metrics_receiver) = tokio::sync::watch::channel(false);
    let sampler = tokio::spawn(crate::delivery_metrics::run(service.clone(), live.clone(), metrics, metrics_receiver));
    let notifications = service.subscribe();
    tokio::spawn(bcs_message_flow::delivery_notifications::run(Arc::downgrade(&flow), notifications, receiver.clone()));
    tokio::spawn(async move {
        let _guard = guard;
        if let Err(error) = runtime.run(receiver).await {
            tracing::error!(%error, "message delivery scheduler stopped; new admissions are disabled");
        }
        _guard.service.set_admission_available(false);
        _guard.policy.scheduler_available.store(false, Ordering::SeqCst);
        let _ = metrics_stop.send(true);
        if let Err(error) = sampler.await { tracing::error!(%error, "delivery monitor sampler stopped unexpectedly"); }
    });
    Ok(flow)
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn removed_lock_path_is_rejected_by_full_config_schema() {
        let error = toml::from_str::<crate::BcsConfig>("[message_delivery]\nlock_path = '/obsolete/delivery.lock'\n").unwrap_err();
        assert!(error.to_string().contains("unknown field `lock_path`"));
    }

    #[tokio::test]
    async fn database_policy_can_enable_after_disabled_start_and_survive_restart() {
        use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
        use bcs_service_api::{HumanActor, CallerContext, GroupCoreService, MessageFlowService, WebSendCommand};
        let fixture = wiring_support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        crate::migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
        use bcs_db_api::{DbPlugin, DbStatement};
        db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('group-1:live','group-1','dev','[]')")).await.unwrap();
        let repo = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(db, "dev".into()));
        let group = fixture.group.get("group-1").await.unwrap();
        let make_flow = || BcsMessageFlow::new(fixture.group.clone(), fixture.routing.clone(), fixture.registry.clone(), fixture.bot_delivery.clone(), fixture.frontend_delivery.clone())
            .with_message_repo(repo.clone())
            .with_bot_run_context(Arc::new(bcs_message_flow::MemoryBotRunContextStore::new()))
            .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session_support::test_session("group-1:live", "group-1", group.participants.clone()))));
        let admin = || CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() });
        let mut config = crate::BcsConfig::default();
        config.metrics.enabled = false;
        config.provider_http.bypass_headers = vec!["x-routing-zone".into()];
        config.provider_http.queue_persistable_headers = vec!["x-routing-zone".into()];
        let mut policy = DeliveryPolicy::default();
        policy.flow_enabled.group = true;
        policy.defaults.mode = BotDeliveryMode::Enforce;
        let flow = wire(make_flow(), &config).await.unwrap();
        assert!(flow.delivery_shutdown.get().is_some(), "off startup must install the idle scheduler");
        let enabled = flow.replace_delivery_policy(admin(), 0, policy).await.unwrap();
        assert_eq!(enabled.version, 1);
        let admission = flow.handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
            group_id: "group-1".into(), session_id: Some("group-1:live".into()), from_actor_id: "human_1".into(),
            from_name: None, message: "@Driver dynamically enabled".into(), mentions: vec!["bot-driver".into()],
            attachments: None, thinking: None, idempotency_key: Some("dynamic-1".into()), source_im_message_id: None,
            channel_sender_identity: None, sender_conn_id: None, provider_bypass_headers: Vec::new(),
        }).await.unwrap();
        assert!(admission.queue_admission.is_some());
        let dispatched = tokio::time::timeout(Duration::from_secs(3), async {
            while fixture.bot_delivery.frames().await.is_empty() { tokio::time::sleep(Duration::from_millis(10)).await; }
        }).await;
        assert!(dispatched.is_ok(), "idle scheduler did not dispatch: {:?}", flow.managed_deliveries.as_ref().unwrap().snapshot(None).await.unwrap());
        flow.shutdown_managed_delivery().await.unwrap();
        assert!(!flow.delivery_policy.as_ref().unwrap().scheduler_available.load(std::sync::atomic::Ordering::SeqCst));
        assert!(flow.replace_delivery_policy(admin(), 1, enabled.policy.clone()).await.is_err(), "stopped scheduler must remain fail closed");
        let restarted = wire(make_flow(), &config).await.unwrap();
        assert_eq!(restarted.get_delivery_policy(admin()).await.unwrap(), enabled);
        restarted.shutdown_managed_delivery().await.unwrap();
        config.message_delivery.flow_enabled.group = true;
        assert!(wire(make_flow(), &config).await.is_err(), "legacy TOML must not shadow DB policy");
    }

    #[tokio::test]
    async fn memory_storage_cannot_enable_scheduler() {
        use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
        use bcs_service_api::{HumanActor, CallerContext, MessageFlowService};
        let fixture = wiring_support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let flow = BcsMessageFlow::new(fixture.group, fixture.routing, fixture.registry, fixture.bot_delivery, fixture.frontend_delivery)
            .with_message_repo(Arc::new(bcs_message_store::MemoryMessageRepo::new()));
        let flow = wire(flow, &crate::BcsConfig::default()).await.unwrap();
        assert!(flow.delivery_shutdown.get().is_none());
        let mut policy = DeliveryPolicy::default();
        policy.flow_enabled.group = true;
        policy.defaults.mode = BotDeliveryMode::Enforce;
        let caller = CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() });
        assert!(flow.replace_delivery_policy(caller, 0, policy).await.is_err());
    }

    #[tokio::test]
    async fn disabled_config_recovers_sqlite_work_and_rejects_legacy_overtaking() {
        use bcs_db_api::{DbPlugin, DbStatement};
        use bcs_domain::{
            DeliveryType, NewMessage, SenderType,
            message_delivery::{DeliveryFlowKind, MessageDeliveryStatus},
        };
        use bcs_service_api::application::message_delivery::DeliveryTransitionCommand;
        use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent;
        use bcs_service_api::port::repo::message_delivery::{
            AdmitMessageDeliveries, DeliveryAdmissionTarget,
        };
        use bcs_service_api::{CallerContext, HumanActor, MessageFlowService, WebSendCommand};
        let fixture = wiring_support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        crate::migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('group-1:12345678','group-1','dev','[]')")).await.unwrap();
        let repo = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(
            db,
            "dev".into(),
        ));
        let seed = ManagedMessageDelivery::new(repo.clone());
        let row = seed
            .admit(AdmitMessageDeliveries {
                display_message: None,
                message_id: "before-crash".into(),
                flow_kind: DeliveryFlowKind::Group,
                now_ms: 1,
                expire_at_ms: None,
                event: None,
                targets: vec![DeliveryAdmissionTarget { rejection: None,
                    target_bot_id: "bot-driver".into(),
                    kind: DeliveryType::Send,
                    max_queued: 10,
                    semantic_projection_json: serde_json::json!({"version":1}),
                }],
                message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
                    group_id: "group-1".into(),
                    session_id: "group-1:12345678".into(),
                    sender_id: "human_1".into(),
                    sender_type: SenderType::Human,
                    message_type: "chat".into(),
                    content: serde_json::json!({"text":"before crash"}),
                    client_msg_id: None,
                    owner_bot_id: None,
                    created_at: 1,
                    run_id: String::new(),
                },
            })
            .await
            .unwrap()
            .deliveries
            .remove(0);
        seed.transition(DeliveryTransitionCommand { delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
            event: DeliveryLifecycleEvent::StartSend, now_ms: 2, request_id: None, actor_id: None, reply: None,
            transport_context_json: Some(serde_json::json!({"version":1,"owner":{"kind":"web_socket"},"connection_id":"old","downstream_session_key":"old-key"})), deadline_at_ms: Some(i64::MAX),
        }).await.unwrap();
        let config = crate::BcsConfig::default();
        let make_flow = || {
            BcsMessageFlow::new(
                fixture.group.clone(),
                fixture.routing.clone(),
                fixture.registry.clone(),
                fixture.bot_delivery.clone(),
                fixture.frontend_delivery.clone(),
            )
            .with_message_repo(repo.clone())
        };
        let flow = wire(make_flow(), &config).await.unwrap();
        let recovered = flow
            .managed_deliveries
            .as_ref()
            .unwrap()
            .snapshot(None)
            .await
            .unwrap();
        assert_eq!(recovered[0].state.status, MessageDeliveryStatus::Unknown);
        assert_eq!(
            recovered[0].run_id,
            seed.snapshot(None).await.unwrap()[0].run_id
        );
        let rejected = flow
            .handle_web_send(WebSendCommand {
                caller: CallerContext::Human(HumanActor {
                    actor_id: "human_1".into(),
                    staff_no: "1".into(),
                }),
                group_id: "group-1".into(),
                session_id: Some("group-1:12345678".into()),
                from_actor_id: "human_1".into(),
                from_name: None,
                message: "@Driver after crash".into(),
                mentions: vec!["bot-driver".into()],
                attachments: None,
                thinking: None,
                idempotency_key: None,
                source_im_message_id: None,
                channel_sender_identity: None,
                sender_conn_id: None,
                provider_bypass_headers: Vec::new(),
            })
            .await;
        assert!(rejected.is_err_and(|error| error.to_string().contains("queue_draining")));
        flow.shutdown_managed_delivery().await.unwrap();
        let next = wire(make_flow(), &config).await.unwrap();
        next.shutdown_managed_delivery().await.unwrap();
    }
}
