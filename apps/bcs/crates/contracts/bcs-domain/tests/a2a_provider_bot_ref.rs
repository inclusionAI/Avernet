use bcs_domain::a2a_management::{
    GENERATED_PROVIDER_BOT_REF_PREFIX, MAX_PROVIDER_BOT_REF_BYTES, ManagementCode,
    is_generated_provider_bot_ref, valid_provider_bot_ref, validate_provider_bot_ref,
};

#[test]
fn call_identifiers_allow_unicode_and_reject_only_unsafe_or_reserved_text() {
    for id in ["order-agent-001", "000123", "Agent-X", "订单助手", "a/b c", "x"] {
        assert!(valid_provider_bot_ref(id), "{id}");
        validate_provider_bot_ref(id).unwrap();
    }
    let generated = format!("{GENERATED_PROVIDER_BOT_REF_PREFIX}abc");
    let too_long = "x".repeat(MAX_PROVIDER_BOT_REF_BYTES + 1);
    for id in ["", " a", "a ", "a\nb", "a\u{0}", generated.as_str(), too_long.as_str()] {
        assert!(!valid_provider_bot_ref(id), "{id:?}");
        let error = validate_provider_bot_ref(id).unwrap_err();
        assert_eq!((error.code, error.field.as_str(), error.reason),
            (ManagementCode::InvalidRequest, "provider_bot_ref", "INVALID_PROVIDER_BOT_REF"));
    }
    // The cap is in bytes, matching shared Provider identity normalization.
    assert!(valid_provider_bot_ref(&"界".repeat(MAX_PROVIDER_BOT_REF_BYTES / 3)));
    assert!(!valid_provider_bot_ref(&"界".repeat(MAX_PROVIDER_BOT_REF_BYTES / 3 + 1)));
    assert!(is_generated_provider_bot_ref(&generated));
    assert!(!is_generated_provider_bot_ref("order-agent-001"));
}
