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
fn stored_connection_mode_is_independent_of_the_legacy_admin_plugin_spelling() {
    assert_eq!(serde_json::to_string(&BotConnectionMode::Upstream).unwrap(), "\"upstream\"");
    assert_eq!(serde_json::to_string(&BotConnectionMode::Gateway).unwrap(), "\"gateway\"");
    assert!(serde_json::from_str::<BotConnectionMode>("\"plugin\"").is_err());
}
