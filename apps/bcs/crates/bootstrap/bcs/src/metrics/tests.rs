use super::*;

#[test]
fn service_mode_label_keeps_unknown_values_low_cardinality() {
    assert_eq!(service_mode_label(None), "none");
    assert_eq!(service_mode_label(Some("master_slave")), "master_slave");
    assert_eq!(service_mode_label(Some("other")), "other");
    assert_eq!(service_mode_label(Some("free-form-mode")), "other");
}

#[test]
fn group_strategy_label_uses_closed_enum_values() {
    assert_eq!(group_strategy_label(GroupStrategy::Chat), "chat");
    assert_eq!(
        group_strategy_label(GroupStrategy::ManagerWorker),
        "manager_worker"
    );
    assert_eq!(
        group_strategy_label(GroupStrategy::StateMachine),
        "state_machine"
    );
}

#[test]
fn bot_labels_use_closed_sets() {
    assert_eq!(actor_kind_label(ActorKind::Bot), "bot");
    assert_eq!(actor_kind_label(ActorKind::Human), "human");
    assert_eq!(actor_status_label(ActorStatus::Online), "online");
    assert_eq!(actor_status_label(ActorStatus::Hidden), "hidden");
    assert_eq!(visibility_label(Some("public")), "public");
    assert_eq!(visibility_label(Some("protected")), "protected");
    assert_eq!(visibility_label(Some("private")), "private");
    assert_eq!(visibility_label(Some("custom")), "other");
    assert_eq!(visibility_label(None), "other");
}

#[test]
fn http_result_label_treats_only_5xx_as_error() {
    assert_eq!(http_status_class(StatusCode::NOT_FOUND), "4xx");
    assert_eq!(http_result_label(StatusCode::NOT_FOUND), "success");
    assert_eq!(http_status_class(StatusCode::INTERNAL_SERVER_ERROR), "5xx");
    assert_eq!(
        http_result_label(StatusCode::INTERNAL_SERVER_ERROR),
        "error"
    );
}

#[test]
fn group_event_labels_use_closed_sets() {
    assert_eq!(group_event_kind_label(Some(GroupKind::Normal)), "normal");
    assert_eq!(group_event_kind_label(Some(GroupKind::Dm)), "dm");
    assert_eq!(group_event_kind_label(None), "unknown");
    assert_eq!(session_status_label(SessionStatus::Running), "running");
    assert_eq!(session_status_label(SessionStatus::Completed), "completed");
    assert_eq!(session_kind_label(SessionKind::Chat), "chat");
    assert_eq!(
        session_kind_label(SessionKind::ServiceInvocation),
        "service_invocation"
    );
    assert_eq!(
        group_session_event_label(group_status_event("completed")),
        "completed"
    );
    assert_eq!(
        group_session_event_label(group_status_event("closed")),
        "closed"
    );
    assert_eq!(
        group_session_event_label(group_status_event("active")),
        "status_updated"
    );
    assert_eq!(
        group_session_event_label(GroupSessionEvent::MemberAdded),
        "member_added"
    );
}

#[test]
fn ws_labels_use_closed_sets() {
    assert_eq!(ws_peer_label(WsPeer::Bot), "bot");
    assert_eq!(
        ws_close_reason_label(WsCloseReason::IdleTimeout),
        "idle_timeout"
    );
    assert_eq!(ws_event_label(WsConnectionEvent::Registered), "registered");
}

#[test]
fn message_flow_labels_use_closed_sets() {
    assert_eq!(
        message_flow_source_label(MessageFlowSource::WebWs),
        "web_ws"
    );
    assert_eq!(
        message_flow_operation_label(MessageFlowOperation::PersistentGroupSend),
        "persistent_group_send"
    );
    assert_eq!(
        message_flow_operation_label(MessageFlowOperation::DirectChat),
        "direct_chat"
    );
}

#[test]
fn direct_chat_client_labels_use_closed_sets() {
    assert_eq!(
        direct_chat_client_kind_label(DirectChatClientKind::None),
        "none"
    );
    assert_eq!(
        direct_chat_client_kind_label(DirectChatClientKind::HttpChat),
        "http_chat"
    );
    assert_eq!(
        direct_chat_client_kind_label(DirectChatClientKind::HttpChatAsync),
        "http_chat_async"
    );
    assert_eq!(
        direct_chat_client_kind_label(DirectChatClientKind::BcsCli),
        "bcs_cli"
    );
    assert_eq!(
        direct_chat_client_kind_label(DirectChatClientKind::Unknown),
        "unknown"
    );
    assert_eq!(
        direct_chat_run_event_label(DirectChatRunEvent::CapacityRejected),
        "capacity_rejected"
    );
    assert_eq!(
        direct_chat_run_reason_label(DirectChatRunReason::StoreCapacity),
        "store_capacity"
    );
}

#[test]
fn delivery_block_labels_use_closed_sets() {
    assert_eq!(
        delivery_metric_target_label(DeliveryMetricTarget::Bot),
        "bot"
    );
    assert_eq!(
        delivery_metric_target_label(DeliveryMetricTarget::Unknown),
        "unknown"
    );
    assert_eq!(delivery_metric_kind_label(DeliveryMetricKind::Send), "send");
    assert_eq!(
        delivery_metric_kind_label(DeliveryMetricKind::TaskDispatch),
        "task_dispatch"
    );
    assert_eq!(
        delivery_error_code_label(delivery_block_reason_code(
            DeliveryBlockReason::PolicyBlocked
        )),
        "policy_blocked"
    );
    assert_eq!(
        delivery_outcome_label(DeliveryOutcome::NoReceivers),
        "no_receivers"
    );
    assert_eq!(
        delivery_error_code_label(DeliveryErrorCode::NotConnected),
        "not_connected"
    );
}

#[test]
fn histogram_buckets_are_seconds_and_monotonic() {
    for buckets in [
        HTTP_DURATION_BUCKETS_SECONDS,
        WS_CONNECTION_DURATION_BUCKETS_SECONDS,
        DELIVERY_DURATION_BUCKETS_SECONDS,
    ] {
        assert!(!buckets.is_empty());
        assert!(buckets.iter().all(|bucket| *bucket > 0.0));
        assert!(buckets.windows(2).all(|pair| pair[0] < pair[1]));
    }
    assert_eq!(HTTP_DURATION_BUCKETS_SECONDS[0], 0.005);
    assert_eq!(DELIVERY_DURATION_BUCKETS_SECONDS[0], 0.001);
    assert_eq!(WS_CONNECTION_DURATION_BUCKETS_SECONDS[0], 1.0);
}

#[test]
fn runtime_info_labels_follow_config_sources() {
    let mut config = BcsConfig::default();
    assert_eq!(cache_label(&config), "memory");
    assert_eq!(storage_label(&config), "sqlite");

    config.cache.cache_type = "redis".to_string();
    assert_eq!(cache_label(&config), "redis");

    config.database.database_type = DatabaseType::Mysql;
    assert_eq!(storage_label(&config), "mysql");

    config.database.database_type = DatabaseType::Sqlite;
    assert_eq!(storage_label(&config), "sqlite");
}
