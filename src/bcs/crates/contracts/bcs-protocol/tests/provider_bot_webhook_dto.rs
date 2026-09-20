use bcs_protocol::http::{PatchProviderBotRequest, RegisterProviderBotRequest, RegisterProviderRequest};
use serde_json::{json, Value};

#[test]
fn provider_registration_accepts_an_absent_default_endpoint() {
    for endpoint in [None, Some(Value::Null)] {
        let mut body = json!({"name": "per-bot", "auth": {"mode": "static_bearer"}});
        if let Some(endpoint) = endpoint { body["webhook_url"] = endpoint; }
        let request: RegisterProviderRequest = serde_json::from_value(body)
            .expect("a provider with per-bot endpoints needs no shared URL");
        assert!(serde_json::to_value(request).unwrap()["webhook_url"].is_null());
    }
}

#[test]
fn bot_registration_preserves_its_own_endpoint() {
    let request: RegisterProviderBotRequest = serde_json::from_value(json!({
        "name": "Bot A", "provider_bot_ref": "a", "owners": ["alice"],
        "webhook_url": "https://a.example.com/webhook"
    })).unwrap();
    assert_eq!(serde_json::to_value(request).unwrap()["webhook_url"],
        "https://a.example.com/webhook");
}

#[test]
fn bot_endpoint_patch_preserves_omitted_null_and_value() {
    for body in [json!({}), json!({"webhook_url": null}),
        json!({"webhook_url": "https://b.example.com/webhook"})] {
        let request: PatchProviderBotRequest = serde_json::from_value(body.clone()).unwrap();
        let round_trip = serde_json::to_value(request).unwrap();
        assert_eq!(round_trip.as_object().unwrap().get("webhook_url"),
            body.as_object().unwrap().get("webhook_url"));
    }
}

#[test]
fn bot_endpoint_patch_rejects_non_string_values() {
    for endpoint in [json!(5), json!(true), json!({"url": "https://a.example.com"})] {
        assert!(serde_json::from_value::<PatchProviderBotRequest>(
            json!({"webhook_url": endpoint})).is_err());
    }
}

#[test]
fn legacy_bot_and_binding_json_remain_readable_without_an_override() {
    let bot: RegisterProviderBotRequest = serde_json::from_value(json!({
        "name": "Legacy", "provider_bot_ref": "legacy", "owners": ["alice"]
    })).unwrap();
    assert!(bot.webhook_url.is_none());
    let binding: bcs_domain::ProviderBotBinding = serde_json::from_value(json!({
        "bot_uuid": "legacy", "provider_id": "provider", "provider_bot_ref": "legacy",
        "disabled": false, "created_at": 1, "updated_at": 1
    })).unwrap();
    assert!(binding.webhook_url.is_none());
}
