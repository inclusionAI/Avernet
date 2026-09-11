use bcs_config_api::message_delivery::DeliveryFlowKey as DeliveryFlowKind;
use bcs_config_api::message_delivery::{
    BotDeliveryConfig, BotDeliveryMode, MessageDeliveryConfig, MessageDeliveryConfigError,
};

#[test]
fn durable_defaults_cover_future_bots_and_partial_overrides_inherit() {
    use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryOverride};
    let mut policy = DeliveryPolicy::default();
    assert!(!policy.manages_group("new-bot"));
    policy.flow_enabled.group = true;
    policy.defaults.mode = BotDeliveryMode::Enforce;
    policy.bots.insert("special".into(), BotDeliveryOverride { max_running: Some(3), ..Default::default() });
    policy.bots.insert("excluded".into(), BotDeliveryOverride { mode: Some(BotDeliveryMode::Off), ..Default::default() });
    policy.validate().unwrap();
    assert!(policy.manages_group("new-bot"));
    assert!(!policy.manages_group("excluded"));
    assert_eq!(policy.bot("special").max_running, 3);
    assert_eq!(policy.bot("special").max_queued, policy.defaults.max_queued);
    policy.flow_enabled.direct_a2a = true;
    assert!(policy.validate().is_err());
}

#[test]
fn queue_expiry_and_retry_policy_are_optional_and_bounded() {
    let defaults = MessageDeliveryConfig::default();
    assert!(defaults.queue_ttl_ms.is_none());
    assert!(defaults.safe_retry.is_none());
    for value in [
        serde_json::json!({"queue_ttl_ms": 0}),
        serde_json::json!({"safe_retry": {"max_retries": 11, "backoff_ms": 1000}}),
        serde_json::json!({"safe_retry": {"max_retries": 2, "backoff_ms": 0}}),
    ] {
        let config: MessageDeliveryConfig = serde_json::from_value(value).unwrap();
        assert!(config.validate_ready_flows(&[]).is_err());
    }
    let config: MessageDeliveryConfig = serde_json::from_value(serde_json::json!({
        "queue_ttl_ms": 60000, "safe_retry": {"max_retries": 2, "backoff_ms": 1000}
    }))
    .unwrap();
    config.validate_ready_flows(&[]).unwrap();
}

#[test]
fn omitted_configuration_keeps_all_flows_off() -> Result<(), Box<dyn std::error::Error>> {
    let config: MessageDeliveryConfig = serde_json::from_str("{}")?;
    config.validate_ready_flows(&[])?;
    for flow in [
        DeliveryFlowKind::Group,
        DeliveryFlowKind::DirectA2a,
        DeliveryFlowKind::Task,
        DeliveryFlowKind::System,
        DeliveryFlowKind::StateMachine,
    ] {
        assert!(!config.flow_enabled.enabled(flow));
        assert!(!config.manages_new_delivery("bot", flow));
    }
    Ok(())
}

#[test]
fn only_enforced_bot_and_enabled_type_are_managed() -> Result<(), Box<dyn std::error::Error>> {
    let config: MessageDeliveryConfig = toml::from_str(
        r#"
        [flow_enabled]
        group = true
        [bots.enabled]
        mode = "enforce"
        max_running = 2
        max_queued = 10
        min_send_interval_ms = 100
        [bots.disabled]
        max_running = 1
        max_queued = 10
        min_send_interval_ms = 0
    "#,
    )?;
    config.validate_ready_flows(&[DeliveryFlowKind::Group])?;
    assert!(config.manages_new_delivery("enabled", DeliveryFlowKind::Group));
    assert!(!config.manages_new_delivery("enabled", DeliveryFlowKind::DirectA2a));
    assert!(!config.manages_new_delivery("disabled", DeliveryFlowKind::Group));
    assert!(!config.manages_new_delivery("missing", DeliveryFlowKind::Group));
    Ok(())
}

#[test]
fn each_unready_type_is_rejected_even_without_enforced_bots()
-> Result<(), Box<dyn std::error::Error>> {
    for (name, flow) in [
        ("group", DeliveryFlowKind::Group),
        ("direct_a2a", DeliveryFlowKind::DirectA2a),
        ("task", DeliveryFlowKind::Task),
        ("system", DeliveryFlowKind::System),
        ("state_machine", DeliveryFlowKind::StateMachine),
    ] {
        let config: MessageDeliveryConfig =
            serde_json::from_value(serde_json::json!({"flow_enabled": {name: true}}))?;
        assert_eq!(
            config.validate_ready_flows(&[]),
            Err(MessageDeliveryConfigError::FlowNotReady(flow))
        );
        config.validate_ready_flows(&[flow])?;
    }
    Ok(())
}

#[test]
fn malformed_or_speculative_configuration_is_rejected() {
    for value in [
        serde_json::json!({"flow_enabled": {"direct_a2aa": true}}),
        serde_json::json!({"flow_enabled": {"group": null}}),
        serde_json::json!({"flow_enabled": {"group": "true"}}),
        serde_json::json!({"ready": ["direct_a2a"]}),
        serde_json::json!({"max_context_bytes": 1024}),
        serde_json::json!({"lock_path": "/obsolete/delivery.lock"}),
        serde_json::json!({"bots": {"bot": {"mode": "enforce"}}}),
    ] {
        assert!(serde_json::from_value::<MessageDeliveryConfig>(value).is_err());
    }
}

#[test]
fn bot_limits_must_be_valid_even_when_disabled() {
    let mut config = MessageDeliveryConfig::default();
    for (running, queued) in [(0, 1), (1, 0)] {
        config.bots.insert(
            "bot".into(),
            BotDeliveryConfig {
                mode: BotDeliveryMode::Off,
                max_running: running,
                max_queued: queued,
                min_send_interval_ms: 0,
            },
        );
        assert_eq!(
            config.validate_ready_flows(&[]),
            Err(MessageDeliveryConfigError::InvalidBotPolicy)
        );
    }
}
#[test]
fn context_limits_default_for_old_policy_and_reject_invalid_bounds() {
    use bcs_config_api::message_delivery::DeliveryPolicy;
    let policy = DeliveryPolicy::default();
    assert_eq!(policy.max_context_messages, 24);
    assert_eq!(policy.max_context_bytes, 131072);
    let mut old = serde_json::to_value(&policy).unwrap();
    old.as_object_mut().unwrap().remove("max_context_messages");
    old.as_object_mut().unwrap().remove("max_context_bytes");
    assert_eq!(serde_json::from_value::<DeliveryPolicy>(old).unwrap(), policy);
    for count in [0, 1025, u32::MAX] { let mut invalid = policy.clone(); invalid.max_context_messages = count; assert!(invalid.validate().is_err()); }
    for bytes in [0, 511, 16_777_217, u64::MAX] { let mut invalid = policy.clone(); invalid.max_context_bytes = bytes; assert!(invalid.validate().is_err()); }
    for bytes in [512, 131072, 16_777_216] { let mut valid = policy.clone(); valid.max_context_bytes = bytes; assert!(valid.validate().is_ok()); }
}
