use bcs_protocol::{PatchProviderRequest, ProviderInfoResponse, RegisterProviderRequest};
use serde_json::json;

#[test]
fn legacy_provider_info_still_deserializes_without_slug_or_protocol_version() {
    let info: ProviderInfoResponse = serde_json::from_value(json!({
        "provider_id":"legacy", "name":"Legacy", "webhook_url":null,
        "auth_mode":"static_bearer", "disabled":false, "created_at":1, "updated_at":1
    })).unwrap();
    assert_eq!(info.slug, None);
    assert_eq!(info.protocol_version, "1.0");
}

#[test]
fn registration_and_patch_preserve_slug_presence_without_normalization() {
    let request: RegisterProviderRequest = serde_json::from_value(json!({
        "name":"Provider", "slug":"coding-provider", "auth":{"mode":"agentpass"}
    })).unwrap();
    assert_eq!(request.slug.as_deref(), Some("coding-provider"));
    for body in [json!({}), json!({"slug":null})] {
        let patch: PatchProviderRequest = serde_json::from_value(body).unwrap();
        assert_eq!(patch.slug, None);
    }
    let patch: PatchProviderRequest = serde_json::from_value(json!({"slug":"Upper"})).unwrap();
    assert_eq!(patch.slug.as_deref(), Some("Upper"));
    assert!(serde_json::from_value::<PatchProviderRequest>(json!({"slug":42})).is_err());
}
