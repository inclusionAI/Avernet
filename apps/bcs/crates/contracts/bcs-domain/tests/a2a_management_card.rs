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

#[test]
fn optional_provider_requires_complete_valid_identity_when_present() {
    for provider in [json!(null), json!([]), json!({}),
        json!({"organization":"", "url":"https://example.org"}),
        json!({"organization":42, "url":"https://example.org"}),
        json!({"organization":"Example", "url":"relative"}),
        json!({"organization":"Example", "url":"https://"}),
        json!({"organization":"Example", "url":" https://example.org"}),
    ] {
        let mut raw = card();
        raw["provider"] = provider;
        let error = validate_management_card(&raw).unwrap_err();
        assert_eq!(error.code, Code::InvalidCard);
        assert!(error.field.starts_with("agent_card.provider"));
    }
    let mut raw = card();
    raw["provider"] = json!({"organization":"Example", "url":"https://example.org", "future":true});
    let original = raw.clone();
    validate_management_card(&raw).unwrap();
    assert_eq!(raw, original);
}

#[test]
fn optional_card_declarations_reject_malformed_nested_values() {
    for (field, value) in [
        ("capabilities", json!({"extensions":{}})),
        ("capabilities", json!({"extensions":[{"uri":"urn:example:test","description":42}]})),
        ("capabilities", json!({"extensions":[{"uri":"urn:example:test","required":"yes"}]})),
        ("capabilities", json!({"extensions":[{"uri":"urn:example:test","params":[]}]})),
        ("capabilities", json!({"extensions":[{"uri":"urn:example:test"},{"uri":"urn:example:test"}]})),
        ("signatures", json!({})), ("signatures", json!([42])),
        ("documentationUrl", json!("relative")), ("iconUrl", json!(7)),
        ("securitySchemes", json!([])), ("securityRequirements", json!({})),
        ("securityRequirements", json!([42])),
        ("securityRequirements", json!([{"schemes":[]}])),
    ] {
        let mut raw = card();
        raw[field] = value;
        assert_eq!(validate_management_card(&raw).unwrap_err().code, Code::InvalidCard, "{field}: {raw}");
    }
    for scopes in [json!([]), json!({"list":42}), json!({"list":[42]})] {
        let mut raw = card();
        raw["securitySchemes"] = json!({"auth":{"httpAuthSecurityScheme":{"scheme":"bearer"}}});
        raw["securityRequirements"] = json!([{"schemes":{"auth":scopes}},{}]);
        assert_eq!(validate_management_card(&raw).unwrap_err().code, Code::InvalidCard);
    }
    let mut raw = card();
    raw["signatures"] = json!([{"signature":"opaque"}]);
    raw["skills"][0]["examples"] = json!(["query an order"]);
    raw["skills"][0]["inputModes"] = json!(["text/plain"]);
    raw["skills"][0]["outputModes"] = json!(["text/plain"]);
    validate_management_card(&raw).unwrap();
}

#[test]
fn raw_card_parser_preserves_extension_values_and_rejects_ambiguous_json() {
    use bcs_domain::a2a_management::parse_card_json;
    let raw = br#"{"future":{"values":[-7,18446744073709551615,1.25,null,true,"text"]}}"#;
    assert_eq!(parse_card_json(raw).unwrap(), serde_json::from_slice::<Value>(raw).unwrap());
    for raw in [
        br#"{"name":"first","name":"second"}"#.as_slice(),
        br#"{"extensions":[{"params":{"key":1,"key":2}}]}"#.as_slice(),
        br#"{"name":"one"} {"name":"two"}"#.as_slice(),
        br#"{"future":1e999}"#.as_slice(),
    ] {
        assert!(parse_card_json(raw).is_err());
    }
}

#[test]
fn card_byte_limit_counts_preserved_extension_data_at_the_boundary() {
    use bcs_domain::a2a_management::MAX_CARD_BYTES;
    let mut raw = card();
    raw["futureData"] = json!("");
    let padding = MAX_CARD_BYTES - serde_json::to_vec(&raw).unwrap().len();
    raw["futureData"] = json!("a".repeat(padding));
    assert_eq!(serde_json::to_vec(&raw).unwrap().len(), MAX_CARD_BYTES);
    validate_management_card(&raw).unwrap();
    // Two UTF-8 bytes replacing one ASCII byte exceed the limit by one byte.
    raw["futureData"] = json!(format!("{}é", "a".repeat(padding - 1)));
    assert_eq!(serde_json::to_vec(&raw).unwrap().len(), MAX_CARD_BYTES + 1);
    let error = validate_management_card(&raw).unwrap_err();
    assert_eq!(error.code, Code::CardTooLarge);
    assert_eq!(error.field, "agent_card");
    assert_eq!(error.reason, "SIZE_LIMIT_EXCEEDED");
}

#[test]
fn card_root_must_be_an_object_and_interface_tenant_is_preserved() {
    for raw in [json!(null), json!([]), json!("agent"), json!(42)] {
        let error = validate_management_card(&raw).unwrap_err();
        assert_eq!(error.code, Code::InvalidCard);
        assert_eq!(error.field, "agent_card");
        assert_eq!(error.reason, "OBJECT_REQUIRED");
    }
    let mut raw = card();
    raw["supportedInterfaces"][0]["tenant"] = json!("tenant-one");
    let original = raw.clone();
    validate_management_card(&raw).unwrap();
    assert_eq!(raw, original);
}

#[test]
fn standard_bindings_still_require_url_and_custom_addresses_are_deferred_to_admission() {
    for binding in ["HTTP+JSON", "JSONRPC", "GRPC"] {
        let mut raw = card();
        raw["supportedInterfaces"] = json!([{"protocolBinding":binding,"protocolVersion":"1.0"}]);
        assert!(validate_management_card(&raw).is_err());
    }
    let mut raw = card();
    raw["supportedInterfaces"] = json!([{"protocolBinding":"EXAMPLE_INTERNAL","protocolVersion":"1.0","target":"service"}]);
    validate_management_card(&raw).unwrap();
}
