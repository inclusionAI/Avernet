//! Port contract harnesses.

pub mod bot_terminal_observer;
pub mod human_notify;
pub mod metrics;

use bcs_domain::HumanInputNotificationMode;
use bcs_service_api::port::{
    EventDeliveryDisposition, EventDeliveryPort, EventDeliveryRequest, EventRecordResult,
    EventRecorderPort, EventingInstrumentationPort, NewEvent,
};
use bcs_service_api::{
    ActiveBotRunContext, BotDeliveryPort, BotRunContext, BotRunContextPort, BotRunScope,
    BotRunTransportOwner, CanResolveInteractionPort, ChatRunCleanupPort, ChatRunEventPort,
    FrontendDeliveryPort, GroupHistoryBotRequestPort, HumanInputReadyEvent,
    InteractionFrontendPort, InteractionProviderPort, InteractionStorePort, LeaderElectionPort,
    LeaderStatus, SessionChannelDeliveryOutcome, SessionChannelOutboundPort,
    StateMachineResultPublishCommand, StateMachineResultPublisherPort,
};

pub use bot_terminal_observer::bot_terminal_observer_port_contract_tests;
pub use human_notify::{human_mention_notifier_contract_tests, HumanNotifyContractHarness};
pub use metrics::{
    bot_metrics_snapshot_port_contract_tests,
    delivery_policy_block_instrumentation_hook_contract_tests,
    direct_chat_run_lifecycle_hook_contract_tests, direct_chat_run_snapshot_port_contract_tests,
    group_metrics_snapshot_port_contract_tests, group_session_metrics_snapshot_port_contract_tests,
    ws_lifecycle_instrumentation_hook_contract_tests,
};

pub async fn bot_delivery_port_contract_tests<T: BotDeliveryPort + ?Sized>(_port: &T) {}

pub async fn bot_run_context_port_contract_tests<T: BotRunContextPort + ?Sized>(
    port: &T,
    namespace: &str,
) {
    let canonical_run_id = format!("{namespace}-canonical");
    let initial_downstream_run_id = format!("{namespace}-downstream-initial");
    let bound_downstream_run_id = format!("{namespace}-downstream-bound");
    let scope = BotRunScope {
        group_id: format!("{namespace}-group"),
        session_id: format!("{namespace}-session"),
        bot_id: format!("{namespace}-bot"),
    };
    port.put_context(BotRunContext {
        run_id: canonical_run_id.clone(),
        bot_id: scope.bot_id.clone(),
        group_id: scope.group_id.clone(),
        bcs_session_id: Some(scope.session_id.clone()),
        deadline_ms: u64::MAX,
        terminal: false,
    })
    .await;
    port.register_active_run(ActiveBotRunContext {
        canonical_run_id: canonical_run_id.clone(),
        downstream_run_id: initial_downstream_run_id.clone(),
        downstream_session_key: Some(format!("{namespace}-wire-session")),
        scope: scope.clone(),
        transport_owner: BotRunTransportOwner::WebSocket,
        deadline_ms: u64::MAX,
    })
    .await
    .expect("register active run");

    let active = port
        .list_active_runs(&scope)
        .await
        .expect("list active runs");
    assert_eq!(active.len(), 1, "scope contains exactly the registered run");
    assert_eq!(active[0].canonical_run_id, canonical_run_id);
    assert_eq!(
        port.find_active_run(&initial_downstream_run_id)
            .await
            .expect("resolve initial alias")
            .expect("initial alias exists")
            .scope,
        scope
    );

    assert!(
        port.bind_downstream_run_id(&canonical_run_id, &bound_downstream_run_id)
            .await
            .expect("bind downstream run id")
    );
    assert!(
        port.find_active_run(&initial_downstream_run_id)
            .await
            .expect("query replaced alias")
            .is_none(),
        "replaced downstream alias must no longer resolve"
    );
    assert_eq!(
        port.find_active_run(&bound_downstream_run_id)
            .await
            .expect("resolve bound alias")
            .expect("bound alias exists")
            .canonical_run_id,
        canonical_run_id
    );

    let wrong_scope = BotRunScope {
        session_id: format!("{namespace}-other-session"),
        ..scope.clone()
    };
    assert!(
        !port
            .remove_active_run(&wrong_scope, &canonical_run_id)
            .await
            .expect("reject cross-scope removal")
    );
    assert!(
        port.remove_active_run(&scope, &canonical_run_id)
            .await
            .expect("remove active run")
    );
    assert!(
        port.list_active_runs(&scope)
            .await
            .expect("list after removal")
            .is_empty()
    );
    assert!(
        port.find_active_run(&bound_downstream_run_id)
            .await
            .expect("query removed alias")
            .is_none()
    );
}

pub async fn event_recorder_port_contract_tests<T: EventRecorderPort + ?Sized>(
    port: &T,
    event: NewEvent,
) {
    match port.record(event).await.expect("record canonical Event") {
        EventRecordResult::Recorded {
            event_id,
            stream_sequence,
            ..
        } => {
            assert!(!event_id.is_empty());
            assert!(stream_sequence > 0, "stream sequence starts at one");
        }
        EventRecordResult::Disabled => {}
    }
}

pub async fn event_delivery_port_contract_tests<T: EventDeliveryPort + ?Sized>(
    port: &T,
    request: EventDeliveryRequest,
    expected: EventDeliveryDisposition,
) {
    let response = port
        .deliver(request)
        .await
        .expect("Event Delivery adapter classifies the outcome");
    assert_eq!(response.disposition, expected);
    if response.disposition == EventDeliveryDisposition::Succeeded {
        assert!(
            response
                .http_status
                .is_some_and(|status| (200..300).contains(&status)),
            "successful delivery must carry a 2xx status"
        );
    }
}

pub async fn eventing_instrumentation_port_contract_tests<
    T: EventingInstrumentationPort + ?Sized,
>(
    port: &T,
) {
    use bcs_service_api::port::{
        EventDeliveryAttemptMetric, EventDeliveryMetricResult, EventErrorCategory,
        EventHttpStatusClass, EventMetricFamily, EventProductionMetric,
        EventProductionMetricResult, WebhookGuardBlockReason,
    };

    port.event_produced(EventProductionMetric {
        family: EventMetricFamily::StateMachine,
        result: EventProductionMetricResult::Recorded,
        error_category: None,
    })
    .await;
    port.fanout_failed(EventErrorCategory::Projection).await;
    port.delivery_attempted(EventDeliveryAttemptMetric {
        family: EventMetricFamily::StateMachine,
        result: EventDeliveryMetricResult::Retryable,
        status_class: EventHttpStatusClass::ServerError,
        error_category: Some(EventErrorCategory::Http),
    })
    .await;
    port.webhook_guard_blocked(WebhookGuardBlockReason::PrivateAddress)
        .await;
}

pub async fn chat_run_cleanup_port_contract_tests<T: ChatRunCleanupPort + ?Sized>(_port: &T) {}

pub async fn chat_run_event_port_contract_tests<T: ChatRunEventPort + ?Sized>(_port: &T) {}

pub async fn frontend_delivery_port_contract_tests<T: FrontendDeliveryPort + ?Sized>(_port: &T) {}

pub async fn can_resolve_interaction_port_contract_tests<T: CanResolveInteractionPort + ?Sized>(
    _port: &T,
) {
}

pub async fn interaction_frontend_port_contract_tests<T: InteractionFrontendPort + ?Sized>(
    _port: &T,
) {
}

pub async fn interaction_provider_port_contract_tests<T: InteractionProviderPort + ?Sized>(
    _port: &T,
) {
}

pub async fn interaction_store_port_contract_tests<T: InteractionStorePort + ?Sized>(_port: &T) {}

pub async fn group_history_bot_request_port_contract_tests<
    T: GroupHistoryBotRequestPort + ?Sized,
>(
    _port: &T,
) {
}

pub async fn leader_election_port_contract_tests<T: LeaderElectionPort + ?Sized>(port: &T) {
    let status = port.campaign().await.expect("campaign");
    let is_leader = port.is_leader().await.expect("is_leader");
    match status {
        LeaderStatus::Leader => assert!(is_leader, "leader status must report is_leader"),
        LeaderStatus::Follower | LeaderStatus::Unknown => {
            assert!(!is_leader, "non-leader status must not report is_leader")
        }
    }

    let current = port.current_leader().await.expect("current_leader");
    if is_leader {
        assert!(
            current.is_some(),
            "leader implementations must expose leader info"
        );
    }
}

pub async fn session_channel_outbound_port_contract_tests<
    T: SessionChannelOutboundPort + ?Sized,
>(
    port: &T,
) {
    let result = port
        .publish_human_input_ready(HumanInputReadyEvent {
            event_id: "contract-event".to_string(),
            group_id: "contract-group".to_string(),
            session_id: "contract-group:00000001".to_string(),
            run_id: "contract-run".to_string(),
            node_id: "human-review".to_string(),
            display_name: "Human review".to_string(),
            instruction: "Review the upstream result".to_string(),
            assignee_actor_id: "contract-human".to_string(),
            channel_type: "contract-channel".to_string(),
            notification_mode: HumanInputNotificationMode::DirectAssignee,
            fixed_group_conversation_id: None,
            response_ref: "contract-run:human-review".to_string(),
            upstream_artifacts: Vec::new(),
            judge_outcomes: Vec::new(),
            timeout_deadline_ms: None,
        })
        .await;

    match result {
        Ok(SessionChannelDeliveryOutcome::NotApplicable) => {}
        Err(bcs_service_api::ServiceError::InvalidOperation { .. }) => {}
        other => panic!(
            "an unconfigured HumanInput channel must be not-applicable or explicitly rejected, got {other:?}"
        ),
    }
}

pub async fn state_machine_result_publisher_port_contract_tests<
    T: StateMachineResultPublisherPort + ?Sized,
>(
    port: &T,
) {
    port.publish_state_machine_result(StateMachineResultPublishCommand {
        run_id: "contract-run".to_string(),
        group_id: "contract-group".to_string(),
        session_id: "contract-group:00000001".to_string(),
        sender_bot_id: "contract-initiator".to_string(),
        content: "contract final result".to_string(),
    })
    .await
    .expect("publish state-machine result under the initiating Bot identity");
}
