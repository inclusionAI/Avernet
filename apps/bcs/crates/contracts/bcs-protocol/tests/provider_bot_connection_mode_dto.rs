//! Wire DTO contract for the `connection_mode` field on
//! `POST /providers/{provider_id}/bots` (`RegisterProviderBotRequest`).

use bcs_protocol::http::{ProviderBotConnectionModeDto, RegisterProviderBotRequest};

#[test]
fn all_connection_mode_names_are_the_same_type() {
    use std::any::TypeId;
    use bcs_domain::{bot_provider::BotConnectionMode, provider::ProviderBotConnectionMode,
        provider_registration_token::ProviderRegistrationMode};

    let common = TypeId::of::<ProviderBotConnectionMode>();
    assert_eq!(TypeId::of::<ProviderBotConnectionModeDto>(), common);
    assert_eq!(TypeId::of::<ProviderRegistrationMode>(), common);
    assert_eq!(TypeId::of::<BotConnectionMode>(), common);
}

#[test]
fn registration_and_storage_preserve_the_existing_wire_values() {
    use bcs_domain::{bot_provider::BotConnectionMode,
        provider_registration_token::ProviderRegistrationMode};

    for value in ["plugin", "gateway"] {
        let json = serde_json::json!(value);
        let registration: ProviderRegistrationMode = serde_json::from_value(json.clone()).unwrap();
        let stored: BotConnectionMode = serde_json::from_value(json.clone()).unwrap();
        assert_eq!(serde_json::to_value(registration).unwrap(), json);
        assert_eq!(serde_json::to_value(stored).unwrap(), json);
    }
    for value in ["upstream", "Plugin", "Gateway", "", "bogus"] {
        let json = serde_json::json!(value);
        assert!(serde_json::from_value::<ProviderBotConnectionModeDto>(json.clone()).is_err());
        assert!(serde_json::from_value::<ProviderRegistrationMode>(json.clone()).is_err());
        assert!(serde_json::from_value::<BotConnectionMode>(json).is_err());
    }
}

#[test]
fn absent_connection_mode_parses_as_none_and_defaults_to_gateway() {
    let req: RegisterProviderBotRequest = serde_json::from_str(
        r#"{
            "name": "Bot",
            "provider_bot_ref": "plugin-bot:alice",
            "owners": ["11111111"]
        }"#,
    )
    .expect("absent connection_mode parses");
    assert!(req.connection_mode.is_none(), "absent ⇒ None");
    // the handler maps None ⇒ Gateway via `unwrap_or_default()`.
    assert_eq!(
        ProviderBotConnectionModeDto::default(),
        ProviderBotConnectionModeDto::Gateway
    );
}

#[test]
fn parses_gateway_and_plugin_in_snake_case() {
    let gateway: RegisterProviderBotRequest = serde_json::from_str(
        r#"{
            "name": "Bot",
            "provider_bot_ref": "plugin-bot:alice",
            "owners": ["11111111"],
            "connection_mode": "gateway"
        }"#,
    )
    .expect("gateway parses");
    assert!(matches!(
        gateway.connection_mode,
        Some(ProviderBotConnectionModeDto::Gateway)
    ));

    let plugin: RegisterProviderBotRequest = serde_json::from_str(
        r#"{
            "name": "Bot",
            "provider_bot_ref": "plugin-bot:alice",
            "owners": ["11111111"],
            "connection_mode": "plugin"
        }"#,
    )
    .expect("plugin parses");
    assert!(matches!(
        plugin.connection_mode,
        Some(ProviderBotConnectionModeDto::Plugin)
    ));
}

#[test]
fn rejects_unknown_connection_mode_value() {
    let result = serde_json::from_str::<RegisterProviderBotRequest>(
        r#"{
            "name": "Bot",
            "provider_bot_ref": "plugin-bot:alice",
            "owners": ["11111111"],
            "connection_mode": "bogus"
        }"#,
    );
    assert!(
        result.is_err(),
        "unknown connection_mode must be a serde error (→ 400)"
    );
}
