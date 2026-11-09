use bcs_domain::a2a_auth::{select_authentication,A2aAuthentication};
use serde_json::json;

#[test]
fn anonymous_requires_explicit_permission_and_does_not_send_available_bearer() {
    assert!(select_authentication(&json!({}),false,true).is_err());
    assert_eq!(select_authentication(&json!({}),true,true),Ok(A2aAuthentication::Anonymous));
}

#[test]
fn required_authentication_never_downgrades_to_anonymous() {
    let mut card=json!({"securitySchemes":{"auth":{"httpAuthSecurityScheme":{"scheme":"Bearer"}}},
        "securityRequirements":[{"schemes":{"auth":{"list":[]}}}]});
    assert!(select_authentication(&card,true,false).is_err());
    assert_eq!(select_authentication(&card,false,true),Ok(A2aAuthentication::ProviderBearer));
    card["securityRequirements"][0]["schemes"]["other"]=json!({"list":[]});
    assert!(select_authentication(&card,true,true).is_err());
    card["securityRequirements"].as_array_mut().unwrap().push(json!({"schemes":{}}));
    assert_eq!(select_authentication(&card,true,false),Ok(A2aAuthentication::Anonymous));
}

#[test]
fn unknown_authentication_and_malformed_declarations_fail_closed() {
    for card in [json!({"securityRequirements":{}}),json!({"securityRequirements":[{"unexpected":{}}]}),
        json!({"securityRequirements":[{"schemes":{"missing":{"list":[]}}}]}),
        json!({"securityRequirements":[{"schemes":{"missing":{}}}]})] {
        assert!(select_authentication(&card,true,true).is_err());
    }
}

#[test]
fn bearer_accepts_protojson_omitted_empty_scope_list() {
    let card=serde_json::json!({"securitySchemes":{"auth":{"httpAuthSecurityScheme":{"scheme":"bearer"}}},
        "securityRequirements":[{"schemes":{"auth":{}}}]});
    assert_eq!(select_authentication(&card,false,true).unwrap(),
        A2aAuthentication::ProviderBearer);
}

#[test]
fn omitted_empty_schemes_map_is_a_protojson_anonymous_alternative() {
    let card=json!({"securityRequirements":[{}]});
    assert_eq!(select_authentication(&card,true,false),Ok(A2aAuthentication::Anonymous));
    assert!(select_authentication(&card,false,true).is_err());
}

#[test]
fn bearer_cannot_satisfy_scopes_or_malformed_security_alternatives() {
    for requirements in [
        json!([{"schemes":{"auth":[]}}]),
        json!([{"schemes":{"auth":{"list":"read"}}}]),
        json!([{"schemes":{"auth":{"list":["read"]}}}]),
        json!([{"schemes":{}}]),
        json!([{}]),
    ] {
        let card = json!({"securitySchemes":{"auth":{"httpAuthSecurityScheme":{"scheme":"bearer"}}},
            "securityRequirements":requirements});
        assert!(select_authentication(&card, false, true).is_err());
    }
    assert!(select_authentication(&json!(null), true, true).is_err());
    assert_eq!(A2aAuthentication::Anonymous.credential_kind(), "a2a_anonymous");
    assert_eq!(A2aAuthentication::ProviderBearer.credential_kind(), "downlink_bcs_to_provider");
}
