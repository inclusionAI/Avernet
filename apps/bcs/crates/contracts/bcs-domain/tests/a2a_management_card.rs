use bcs_domain::a2a_management::{
    ManagementCode as Code, validate_card_url, validate_management_card, validate_summary,
};
use serde_json::{Value, json};
fn card() -> Value {
    json!({"name":"订单助手","description":"说明","version":"1",
    "supportedInterfaces":[{"url":"https://example.org/rpc","protocolBinding":"JSONRPC","protocolVersion":"1.0"}],
    "capabilities":{},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],
    "skills":[{"id":"query","name":"查询","description":"完整描述","tags":[]}]})
}

#[test]
fn validates_required_fields_and_preserves_optional_extension_data() {
    let mut raw = card();
    raw["capabilities"]["extensions"] =
        json!([{"uri":"urn:example:data:v1","params":{"nested":[1,true,null]}}]);
    let original = raw.clone();
    validate_management_card(&raw).unwrap();
    assert_eq!(raw, original);
    for field in [
        "name",
        "description",
        "version",
        "supportedInterfaces",
        "capabilities",
        "defaultInputModes",
        "defaultOutputModes",
        "skills",
    ] {
        let mut raw = card();
        raw.as_object_mut().unwrap().remove(field);
        assert!(validate_management_card(&raw).is_err(), "{field}");
    }
    let mut raw = card();
    raw["skills"] = json!([]);
    validate_management_card(&raw).unwrap();
    raw["name"] = json!("界".repeat(64));
    validate_management_card(&raw).unwrap();
    raw["name"] = json!("界".repeat(65));
    assert!(validate_management_card(&raw).is_err());
}

#[test]
fn anonymous_security_uses_protojson_or_and_semantics_including_skills() {
    let mut raw = card();
    raw["securitySchemes"] = json!({"bearer":{"httpAuthSecurityScheme":{"scheme":"bearer"}}});
    let required = json!({"schemes":{"bearer":{"list":[]}}});
    raw["securityRequirements"] = json!([required.clone()]);
    assert_eq!(
        validate_management_card(&raw).unwrap_err().code,
        Code::CardAuthUnsupported
    );
    raw["securityRequirements"] = json!([required.clone(),{"schemes":{}}]);
    validate_management_card(&raw).unwrap();
    raw["skills"][0]["securityRequirements"] = json!([required]);
    assert_eq!(
        validate_management_card(&raw).unwrap_err().code,
        Code::CardAuthUnsupported
    );
    raw["skills"][0]["securityRequirements"] = json!([]);
    raw["securityRequirements"] = json!([{"schemes":{"undeclared":{}}},{}]);
    assert_eq!(
        validate_management_card(&raw).unwrap_err().code,
        Code::InvalidCard
    );
    raw["securityRequirements"] = json!([{"bearer":[]}]);
    assert!(validate_management_card(&raw).is_err());
}

#[test]
fn skill_ids_and_card_types_are_validated() {
    let mut raw = card();
    raw["skills"]
        .as_array_mut()
        .unwrap()
        .push(card()["skills"][0].clone());
    assert!(validate_management_card(&raw).is_err());
    for (pointer, value) in [
        ("/skills/0/tags", json!("query")),
        ("/capabilities/streaming", json!("true")),
        ("/supportedInterfaces/0/tenant", json!("")),
        ("/defaultInputModes", json!(["image/png"])),
    ] {
        let mut raw = card();
        if pointer == "/capabilities/streaming" {
            raw["capabilities"]["streaming"] = value;
        } else if pointer == "/supportedInterfaces/0/tenant" {
            raw["supportedInterfaces"][0]["tenant"] = value;
        } else {
            *raw.pointer_mut(pointer).unwrap() = value;
        }
        assert!(validate_management_card(&raw).is_err(), "{pointer}");
    }
}

#[test]
fn summary_override_and_card_url_limits_are_explicit() {
    let mut raw = card();
    raw["description"] = json!("界".repeat(4097));
    assert!(validate_summary(None, &raw).is_err());
    validate_summary(Some(""), &raw).unwrap();
    for url in [
        "relative",
        "https://user:password@example.org/card",
        "https://example.org/card#fragment",
        " https://example.org/card",
    ] {
        assert_eq!(
            validate_card_url(url).unwrap_err().code,
            Code::CardSourceInvalid
        );
    }
    validate_card_url("https://example.org/agents/one/card.json").unwrap();
}
