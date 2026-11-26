use super::*;
use bcs_service_api::types::a2a_bot::{A2aInterface, A2aUpdateMode};

fn config() -> A2aTestUserConfig {
    serde_json::from_value(json!({
        "caller_bot_id": "caller", "provider_id": "provider", "target_bot_id": "target",
        "interface_url": "https://gateway.example.test/rpc/weather?deployment=pre",
        "header_name": "x-example-agent-user",
    })).unwrap()
}

fn target(url: &str, binding: A2aBinding, mode: A2aUpdateMode) -> BotDeliveryTarget {
    BotDeliveryTarget::A2a {
        bot_id: "target".into(), provider_id: "provider".into(), provider_bot_ref: "weather".into(),
        interface: A2aInterface { binding, update_mode: mode, url: url.into(), tenant: None, protocol_version: "1.0".into() },
        credential_kind: bcs_service_api::types::a2a_auth::ANONYMOUS.into(),
        bearer: bcs_service_api::RedactedToken::new(""), card_revision: 1, extensions: vec![],
    }
}

#[test]
fn test_user_header_requires_an_exact_streaming_route_and_binding() {
    let mut config = config();
    let selection = A2aTestUserSelection::new("caller".into(), "12345678".into()).unwrap();
    let route = target(&config.interface_url, A2aBinding::JsonRpc, A2aUpdateMode::Stream);
    assert!(test_user_header(Some(&config), &selection, &route).is_ok());

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
    let mut bearer_route = route;
    let BotDeliveryTarget::A2a { credential_kind, .. } = &mut bearer_route else { unreachable!() };
    *credential_kind = bcs_service_api::types::a2a_auth::PROVIDER_BEARER.into();
    assert!(test_user_header(Some(&config), &selection, &bearer_route).is_err());

    config.interface_url = "http://gateway.example.test/rpc/weather?deployment=pre".into();
    let http_route = target(&config.interface_url, A2aBinding::JsonRpc, A2aUpdateMode::Stream);
    let (name, value) = test_user_header(Some(&config), &selection, &http_route).unwrap();
    assert_eq!(name, "x-example-agent-user");
    assert_eq!(value, "12345678");
    assert!(value.is_sensitive());
}

#[test]
fn test_user_id_is_decimal_and_redacted() {
    let selection = A2aTestUserSelection::new("caller".into(), "12345678".into()).unwrap();
    assert!(!format!("{selection:?}").contains("12345678"));
    for user_id in ["", "not-an-id", "123\n456"] {
        assert!(A2aTestUserSelection::new("caller".into(), user_id.into()).is_err());
    }
}
