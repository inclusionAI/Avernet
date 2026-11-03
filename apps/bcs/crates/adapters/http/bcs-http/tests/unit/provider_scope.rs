//! Private HTTP translation contracts, loaded by the Provider route module.
use super::*;
use bcs_service_api::ProviderCollaborationScope;

#[test]
fn collaboration_scope_request_preserves_omission_and_null() {
    for extra in [json!({}), json!({"collaboration_scope": null})] {
        let mut body = json!({"name": "Local", "auth": {"mode": "agentpass"}});
        body.as_object_mut().unwrap().extend(extra.as_object().unwrap().clone());
        let request: RegisterProviderRequest = serde_json::from_value(body).unwrap();
        assert_eq!(parse_collaboration_scope(request.collaboration_scope.as_deref()).unwrap(), None);
        let patch: PatchProviderRequest = serde_json::from_value(extra).unwrap();
        assert_eq!(parse_collaboration_scope(patch.collaboration_scope.as_deref()).unwrap(), None);
    }
}

#[test]
fn collaboration_scope_http_values_are_exact_and_invalid_values_return_400() {
    for (value, expected) in [("standard", ProviderCollaborationScope::Standard),
        ("same_owner", ProviderCollaborationScope::SameOwner)] {
        let patch: PatchProviderRequest = serde_json::from_value(json!({"collaboration_scope": value})).unwrap();
        assert_eq!(parse_collaboration_scope(patch.collaboration_scope.as_deref()).unwrap(), Some(expected));
    }
    for value in ["", "owner", "SAME_OWNER", " same_owner "] {
        let patch: PatchProviderRequest = serde_json::from_value(json!({"collaboration_scope": value})).unwrap();
        let error = parse_collaboration_scope(patch.collaboration_scope.as_deref()).unwrap_err();
        assert_eq!(error.status, StatusCode::BAD_REQUEST);
        assert_eq!(error.message, "invalid collaboration_scope");
    }
    assert!(serde_json::from_value::<PatchProviderRequest>(json!({"collaboration_scope": false})).is_err());
}

#[test]
fn collaboration_scope_provider_info_reports_effective_policy_and_rejects_corruption() {
    let record = |config: &str| ProviderRecord {
        provider_id: "local".into(), slug: None, name: "Local".into(), config: config.into(),
        created_by: "alice".into(), owners: "[]".into(), disabled: false, created_at: 1, updated_at: 1,
    };
    for (config, scope) in [("{}", "standard"),
        (r#"{"collaboration_scope":"standard"}"#, "standard"),
        (r#"{"collaboration_scope":"same_owner"}"#, "same_owner")] {
        let response = provider_to_response(record(config)).unwrap();
        assert_eq!(response.collaboration_scope.as_deref(), Some(scope));
        let wire = serde_json::to_value(response).unwrap();
        assert_eq!(wire["collaboration_scope"], scope);
        assert!(wire.get("config").is_none());
    }
    for config in [r#"{"collaboration_scope":"unknown"}"#, r#"{"collaboration_scope":null}"#, "[]", "invalid"] {
        assert!(provider_to_response(record(config)).is_err());
    }
}
