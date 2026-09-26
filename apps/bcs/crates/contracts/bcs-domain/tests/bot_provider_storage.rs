use bcs_domain::bot_provider::{BotConnectionMode, DownlinkDetectionSource};

#[test]
fn delivery_source_defaults_to_legacy_binding() {
    assert_eq!(DownlinkDetectionSource::default(), DownlinkDetectionSource::Binding);
    assert_eq!(serde_json::to_string(&DownlinkDetectionSource::default()).unwrap(), "\"binding\"");
}

#[test]
fn delivery_source_accepts_only_explicit_supported_sources() {
    assert_eq!(serde_json::from_str::<DownlinkDetectionSource>("\"bot_connection_mode\"").unwrap(),
        DownlinkDetectionSource::BotConnectionMode);
    for value in ["\"auto\"", "\"gateway\"", "\"bot\"", "null"] {
        assert!(serde_json::from_str::<DownlinkDetectionSource>(value).is_err());
    }
}

#[test]
fn stored_connection_mode_uses_the_same_values_as_provider_administration() {
    for (mode, value) in [(BotConnectionMode::Plugin, "plugin"), (BotConnectionMode::Gateway, "gateway")] {
        assert_eq!(serde_json::to_value(mode).unwrap(), serde_json::json!(value));
        assert_eq!(mode.as_str(), value);
        assert_eq!(value.parse::<BotConnectionMode>().unwrap(), mode);
        assert_eq!(serde_json::from_value::<BotConnectionMode>(serde_json::json!(value)).unwrap(), mode);
    }
    for value in ["upstream", "Plugin", "Gateway", "", "bogus"] {
        assert!(value.parse::<BotConnectionMode>().is_err());
        assert!(serde_json::from_value::<BotConnectionMode>(serde_json::json!(value)).is_err());
    }
}
