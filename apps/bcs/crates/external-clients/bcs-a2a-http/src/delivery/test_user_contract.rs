use super::*;
use bcs_service_api::types::a2a_bot::{A2aInterface, A2aUpdateMode};

fn config() -> A2aTestUserConfig {
    let mut config: A2aTestUserConfig = serde_json::from_value(json!({
        "accept_new": true, "alias": "weather-smoke", "binding_ref": "fixture-v1",
        "caller_bot_id": "caller", "provider_id": "provider", "target_bot_id": "target",
        "interface_url": "https://gateway.example.test/rpc/weather?deployment=pre",
        "header_name": "x-example-agent-user", "user_id_secret": "fixture-ref",
    })).unwrap();
    config.set_user_id("synthetic-user".into()).unwrap();
    config
}

fn target(url: &str, binding: A2aBinding, mode: A2aUpdateMode) -> BotDeliveryTarget {
    BotDeliveryTarget::A2a {
        bot_id: "target".into(), provider_id: "provider".into(), provider_bot_ref: "weather".into(),
        interface: A2aInterface { binding, update_mode: mode, url: url.into(), tenant: None, protocol_version: "1.0".into() },
        credential_kind: "downlink_bcs_to_provider".into(),
        bearer: bcs_service_api::RedactedToken::new("fixture-token"), card_revision: 1, extensions: vec![],
    }
}

#[test]
fn test_user_header_requires_an_exact_https_streaming_route_and_binding() {
    let mut config = config();
    let selection = test_user_selection(&config);
    let route = target(&config.interface_url, A2aBinding::JsonRpc, A2aUpdateMode::Stream);
    let (name, value) = test_user_header(Some(&config), &selection, &route).unwrap();
    assert_eq!(name, "x-example-agent-user");
    assert_eq!(value, "synthetic-user");
    assert!(value.is_sensitive());

    for url in [
        "http://gateway.example.test/rpc/weather?deployment=pre",
        "https://gateway.example.test:8443/rpc/weather?deployment=pre",
        "https://gateway.example.test/rpc/finance?deployment=pre",
        "https://gateway.example.test/rpc/weather?deployment=prod",
        "https://other.example.test/rpc/weather?deployment=pre",
    ] {
        let changed = target(url, A2aBinding::JsonRpc, A2aUpdateMode::Stream);
        assert!(test_user_header(Some(&config), &selection, &changed).is_err(), "{url}");
    }
    let poll = target(&config.interface_url, A2aBinding::JsonRpc, A2aUpdateMode::Poll);
    assert!(test_user_header(Some(&config), &selection, &poll).is_err());
    assert!(test_user_header(None, &selection, &route).is_err());
    let earlier = config.clone();
    config.binding_ref = "fixture-v2".into();
    assert!(test_user_header(Some(&config), &selection, &route).is_err());
    config.binding_ref = earlier.binding_ref;
    config.interface_url = "https://gateway.example.test/rpc/finance".into();
    let new_route = target(&config.interface_url, A2aBinding::JsonRpc, A2aUpdateMode::Stream);
    assert!(test_user_header(Some(&config), &selection, &new_route).is_err());
}

#[test]
fn draining_keeps_existing_selection_and_hides_user_id() {
    let mut config = config();
    let selection = test_user_admission(&config).unwrap().selection;
    config.accept_new = false;
    assert!(test_user_admission(&config).is_none());
    let route = target(&config.interface_url, A2aBinding::JsonRpc, A2aUpdateMode::Stream);
    assert!(test_user_header(Some(&config), &selection, &route).is_ok());
    assert!(!serde_json::to_string(&selection).unwrap().contains("synthetic-user"));
    assert!(!serde_json::to_string(&config).unwrap().contains("synthetic-user"));
    assert!(!format!("{config:?}").contains("synthetic-user"));
}
