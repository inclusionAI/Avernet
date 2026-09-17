use bcs_config_api::FixedLoopLimits;
use serde_json::json;

#[test]
fn limits_default_and_override_without_accepting_unknown_fields() {
    let defaults = FixedLoopLimits::default();
    assert!(defaults.validate().is_ok());
    assert_eq!(serde_json::from_value::<FixedLoopLimits>(json!({})).unwrap(), defaults);
    let configured: FixedLoopLimits = serde_json::from_value(json!({"max_fixed_loop_iterations": 4})).unwrap();
    assert_eq!(configured.max_fixed_loop_iterations, 4);
    assert_eq!(configured.max_compiled_state_machine_bytes, defaults.max_compiled_state_machine_bytes);
    assert!(serde_json::from_value::<FixedLoopLimits>(json!({"max_fixed_loop_iteration": 4})).is_err());
}

#[test]
fn limits_reject_zero_negative_and_out_of_range_values() {
    for field in ["max_fixed_loop_iterations", "max_fixed_loop_body_nodes", "max_compiled_state_machine_nodes", "max_compiled_state_machine_bytes"] {
        let invalid: FixedLoopLimits = serde_json::from_value(json!({field: 0})).unwrap();
        assert!(invalid.validate().is_err());
        assert!(serde_json::from_value::<FixedLoopLimits>(json!({field: -1})).is_err());
    }
    assert!(serde_json::from_value::<FixedLoopLimits>(json!({"max_fixed_loop_iterations": 4294967296u64})).is_err());
}
