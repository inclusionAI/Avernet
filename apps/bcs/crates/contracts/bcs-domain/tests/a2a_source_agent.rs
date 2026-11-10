use bcs_domain::a2a_management::{SOURCE_AGENT_EXTENSION, source_agent, validate_source_transition, validate_provider_bot_ref};
use serde_json::{json, Value};

fn card(party: Option<Value>) -> Value {
    let mut params=json!({});
    if let Some(party)=party {params["party"]=party;}
    json!({"capabilities":{"extensions":[{"uri":SOURCE_AGENT_EXTENSION,"required":false,"params":params}]}})
}

#[test]
fn classification_is_optional_mutable_and_preserves_unknown_data() {
    assert!(source_agent(&json!({})).unwrap().is_none());
    assert_eq!(source_agent(&card(None)).unwrap().unwrap().party,None);
    for party in ["first_party","second_party","third_party"] {
        let mut raw=card(Some(json!(party)));
        raw["capabilities"]["extensions"][0]["params"]["future"]=json!([1,2]);
        let before=raw.clone();
        assert_eq!(source_agent(&raw).unwrap().unwrap().party,Some(party));
        assert!(validate_source_transition(&raw,&json!({})).is_ok());
        assert!(validate_source_transition(&json!({}),&raw).is_ok());
        assert_eq!(before,raw);
    }
}

#[test]
fn rejects_retired_identity_invalid_party_and_duplicate_extensions() {
    for value in [json!(""),json!(null),json!("unknown"),json!(42)] {
        assert!(source_agent(&card(Some(value))).is_err());
    }
    let mut raw=card(None);
    raw["capabilities"]["extensions"][0]["params"]["agentId"]=json!("old-id");
    assert!(source_agent(&raw).is_err());
    raw["capabilities"]["extensions"][0]["uri"]=json!(SOURCE_AGENT_EXTENSION.replace("v2.md","v1.md"));
    assert!(source_agent(&raw).is_err());
    let mut duplicate=card(None);
    let entry=duplicate["capabilities"]["extensions"][0].clone();
    duplicate["capabilities"]["extensions"].as_array_mut().unwrap().push(entry);
    assert!(source_agent(&duplicate).is_err());
    for value in [json!(true),json!(null),json!("false")] {
        let mut raw=card(None);raw["capabilities"]["extensions"][0]["required"]=value;
        assert!(source_agent(&raw).is_err());
    }
}

#[test]
fn provider_ref_uses_existing_ascii_contract_without_normalization() {
    assert!(validate_provider_bot_ref(&"x".repeat(128)).is_ok());
    for key in ["001Ab","abc:001","A-b_c.d"] {assert!(validate_provider_bot_ref(key).is_ok());}
    for key in [""," x","x ","x/y","中文","a\n",&"x".repeat(129)] {assert!(validate_provider_bot_ref(key).is_err());}
}
