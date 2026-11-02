use bcs_a2a_http::card::{select_card, CardError};
use serde_json::{json, Value};

fn card() -> Value {
    json!({"name":"demo", "description":"test", "version":"1",
        "supportedInterfaces":[{"url":"http://127.0.0.1:9000/a2a/bots/demo",
            "protocolBinding":"HTTP+JSON", "protocolVersion":"1.0"}],
        "capabilities":{"streaming":true},
        "defaultInputModes":["text/plain"], "defaultOutputModes":["text/plain"],
        "skills":[{"id":"query", "name":"Query", "description":"Full description",
            "tags":["test"], "examples":["query order"], "futureField":{"keep":true}}],
        "futureTopLevel":{"keep":true}})
}

#[test]
fn original_card_is_preserved_including_unknown_skill_fields() {
    let raw = card();
    let selected = select_card(raw.clone(), &[], false, |_| true).unwrap();
    assert_eq!(selected.raw, raw);
    assert_eq!(selected.interface.url, "http://127.0.0.1:9000/a2a/bots/demo");
}

#[test]
fn mandatory_extensions_and_addresses_are_checked_before_use() {
    let mut raw = card();
    raw["capabilities"]["extensions"] = json!([{"uri":"urn:test:tool:v2","required":true}]);
    assert_eq!(select_card(raw.clone(), &[], false, |_| true).unwrap_err(),
        CardError::RequiredExtensionUnsupported("urn:test:tool:v2".into()));
    assert_eq!(select_card(raw, &["urn:test:tool:v2"], false, |_| false).unwrap_err(),
        CardError::AddressRejected);
}

#[test]
fn bearer_requires_a_configured_credential_and_matching_declaration() {
    let mut raw = card();
    raw["securitySchemes"] = json!({"auth":{"httpAuthSecurityScheme":{"scheme":"Bearer"}}});
    raw["securityRequirements"] = json!([{"schemes":{"auth":{"list":[]}}}]);
    assert_eq!(select_card(raw.clone(), &[], false, |_| true).unwrap_err(),
        CardError::AuthenticationUnsupported);
    assert!(select_card(raw, &[], true, |_| true).is_ok());
}

#[test]
fn protocol_binding_and_streaming_are_not_inferred_from_url() {
    let mut raw = card();
    raw["supportedInterfaces"][0]["protocolBinding"] = json!("JSONRPC");
    assert_eq!(select_card(raw.clone(), &[], false, |_| true).unwrap_err(), CardError::InterfaceUnsupported);
    raw["capabilities"]["streaming"] = json!(false);
    assert_eq!(select_card(raw, &[], false, |_| true).unwrap_err(), CardError::StreamingRequired);
}

#[test]
fn structured_input_requires_compatible_declared_draft_and_business_profile() {
    use bcs_a2a_http::structured_exec::{EXTENSION,PROFILE};
    let mut raw=card();
    raw["capabilities"]["extensions"]=json!([{"uri":EXTENSION,"required":true,
        "params":{"jsonschema_draft":"2020-12","bcs_exec_profile":PROFILE}}]);
    assert!(select_card(raw.clone(),&[EXTENSION],false,|_|true).is_ok());
    for params in [json!({}),json!({"jsonschema_draft":"draft-07","bcs_exec_profile":PROFILE}),
        json!({"jsonschema_draft":"2020-12","bcs_exec_profile":"unknown"})] {
        raw["capabilities"]["extensions"][0]["params"]=params;
        assert!(matches!(select_card(raw.clone(),&[EXTENSION],false,|_|true),Err(CardError::RequiredExtensionUnsupported(_))));
    }
    raw["capabilities"]["extensions"][0]["required"]=json!(false);
    assert!(select_card(raw,&[],false,|_|true).is_ok(),"optional unknown profile remains unnegotiated");
}

#[test]
fn sdk_validated_declarations_keep_optional_required_and_profile_parameters() {
    use bcs_a2a_http::structured_exec::{EXTENSION,PROFILE};
    use bcs_service_api::types::a2a_bot::A2aExtensionDeclaration;
    for required in [None,Some(Value::Null),Some(json!(false)),Some(json!(true))] {
        let mut raw=card();
        let mut declaration=json!({"uri":EXTENSION,"params":{"jsonschema_draft":"2020-12","bcs_exec_profile":PROFILE,
            "future":{"retained":true}}});
        if let Some(required)=required {declaration["required"]=required;}
        raw["capabilities"]["extensions"]=json!([declaration]);
        let selected=select_card(raw,&[EXTENSION],false,|_|true).unwrap();
        let projected:A2aExtensionDeclaration=serde_json::from_value(selected.raw["capabilities"]["extensions"][0].clone()).unwrap();
        assert_eq!(projected.params["bcs_exec_profile"],PROFILE);
        assert_eq!(projected.params["future"],json!({"retained":true}));
    }
}
