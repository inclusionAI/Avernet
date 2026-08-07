use bcs_protocol::{
    A2aAuthzContext, A2aAuthzGrantKind, A2aAuthzGrantRef, A2aAuthzGrantSource,
    A2aAuthzRuntimeContext,
};
use serde_json::json;

#[test]
fn a2a_authz_context_contract_uses_unified_grants_only() {
    let context = A2aAuthzContext {
        task_id: Some("task-1".to_string()),
        run_id: Some("run-1".to_string()),
        from_id: "bot-a".to_string(),
        to_id: "bot-b".to_string(),
        env: "dev".to_string(),
        originator: Some("human-1".to_string()),
        context: A2aAuthzRuntimeContext::Direct,
        grants: vec![
            A2aAuthzGrantRef {
                kind: A2aAuthzGrantKind::PermissionProfile,
                ref_id: "profile-default".to_string(),
                revision: 7,
                digest: "sha256:profile".to_string(),
                source: A2aAuthzGrantSource::EdgeGrant,
            },
            A2aAuthzGrantRef {
                kind: A2aAuthzGrantKind::Rules,
                ref_id: "rules-grant-1".to_string(),
                revision: 3,
                digest: "sha256:rules".to_string(),
                source: A2aAuthzGrantSource::EdgeGrant,
            },
        ],
        issued_at: 1_786_080_000_000,
        expires_at: 1_786_080_060_000,
        signature: Some("sig".to_string()),
    };

    let encoded = serde_json::to_value(&context).expect("authz context should serialize");

    assert!(encoded.get("authz_context").is_none());
    assert!(encoded.get("grants").is_some());
    assert!(encoded.get("permission_profiles").is_none());
    assert!(encoded.get("rules_grants").is_none());
    assert!(encoded.get("edge_id").is_none());
    assert_eq!(encoded["context"]["type"], json!("direct"));

    assert_eq!(encoded["grants"].as_array().expect("grants array").len(), 2);
    assert_eq!(
        encoded["grants"][0],
        json!({
            "kind": "permission_profile",
            "ref_id": "profile-default",
            "revision": 7,
            "digest": "sha256:profile",
            "source": "edge_grant"
        })
    );
    assert_eq!(encoded["grants"][1]["kind"], "rules");
    assert_eq!(encoded["grants"][1]["source"], "edge_grant");

    let decoded: A2aAuthzContext =
        serde_json::from_value(encoded).expect("authz context should deserialize");
    assert_eq!(decoded.grants.len(), 2);
    assert!(matches!(
        decoded.grants[0].kind,
        A2aAuthzGrantKind::PermissionProfile
    ));
    assert!(matches!(decoded.grants[1].kind, A2aAuthzGrantKind::Rules));
}
