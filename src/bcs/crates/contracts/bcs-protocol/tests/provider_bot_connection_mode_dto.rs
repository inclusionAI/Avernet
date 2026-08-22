//! Wire DTO contract for the `connection_mode` field on
//! `POST /providers/{provider_id}/bots` (`RegisterProviderBotRequest`).

use bcs_protocol::http::{ProviderBotConnectionModeDto, RegisterProviderBotRequest};

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
