use bcs_domain::a2a_management::{
    ManagementCode, SOURCE_AGENT_EXTENSION, source_agent, validate_source_transition,
};
use serde_json::{Value, json};

fn card(id: Value, party: Option<Value>) -> Value {
    let mut params = json!({"agentId": id});
    if let Some(party) = party {
        params["party"] = party;
    }
    json!({"capabilities": {"extensions": [{
        "uri": SOURCE_AGENT_EXTENSION, "required": false, "params": params
    }]}})
}

#[test]
fn source_uri_matches_published_contract() {
    let spec = include_str!("../../../../../../docs/specs/a2a/extensions/source-agent/v1.md");
    assert!(spec.contains(&format!("URI: `{SOURCE_AGENT_EXTENSION}`")));
    assert_eq!(
        SOURCE_AGENT_EXTENSION,
        "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v1.md"
    );
}

#[test]
fn optional_source_preserves_exact_id_and_unknown_data() {
    assert!(source_agent(&json!({})).unwrap().is_none());
    for id in ["000123", "Agent-X", "agent-x", "订单助手"] {
        let mut raw = card(json!(id), None);
        raw["capabilities"]["extensions"][0]["params"]["custom"] = json!({"nested": [1, 2]});
        let before = raw.clone();
        let source = source_agent(&raw).unwrap().unwrap();
        assert_eq!(source.agent_id, id);
        assert_eq!(source.party, None);
        assert_eq!(raw, before);
    }
}

#[test]
fn source_fields_are_validated_without_coercion() {
    for id in [
        json!(null),
        json!(123),
        json!(""),
        json!("  "),
        json!(" id"),
        json!("id "),
        json!("id\nnext"),
        json!("界".repeat(257)),
    ] {
        assert_eq!(
            source_agent(&card(id, None)).unwrap_err().code,
            ManagementCode::InvalidCard
        );
    }
    assert!(source_agent(&card(json!("界".repeat(256)), None)).is_ok());
    for party in ["first_party", "second_party", "third_party"] {
        let raw = card(json!("id"), Some(json!(party)));
        assert_eq!(source_agent(&raw).unwrap().unwrap().party, Some(party));
    }
    for party in [json!(null), json!(""), json!("FIRST_PARTY"), json!(false)] {
        assert!(source_agent(&card(json!("id"), Some(party))).is_err());
    }
}

#[test]
fn source_requires_one_explicit_optional_declaration() {
    let raw = card(json!("id"), None);
    let mut duplicate = raw.clone();
    duplicate["capabilities"]["extensions"]
        .as_array_mut()
        .unwrap()
        .push(raw["capabilities"]["extensions"][0].clone());
    assert!(source_agent(&duplicate).is_err());
    for required in [json!(true), json!(null), json!("false")] {
        let mut invalid = raw.clone();
        invalid["capabilities"]["extensions"][0]["required"] = required;
        assert!(source_agent(&invalid).is_err());
    }
    let mut missing = raw;
    missing["capabilities"]["extensions"][0]
        .as_object_mut()
        .unwrap()
        .remove("required");
    assert!(source_agent(&missing).is_err());
}

#[test]
fn old_placeholder_is_not_a_source_alias() {
    let mut raw = card(json!("id"), None);
    raw["capabilities"]["extensions"][0]["uri"] =
        json!("https://example.org/a2a/extensions/source-agent/v1");
    assert!(source_agent(&raw).unwrap().is_none());
}

#[test]
fn id_is_immutable_but_first_assignment_and_party_updates_are_allowed() {
    let initial = card(json!("001Ab"), None);
    assert!(validate_source_transition(&json!({}), &initial).is_ok());
    let classified = card(json!("001Ab"), Some(json!("first_party")));
    assert!(validate_source_transition(&initial, &classified).is_ok());
    assert!(validate_source_transition(&classified, &initial).is_ok());
    for next in [
        json!({}),
        card(json!("1Ab"), None),
        card(json!("001ab"), None),
    ] {
        assert_eq!(
            validate_source_transition(&initial, &next)
                .unwrap_err()
                .code,
            ManagementCode::SourceAgentChangeNotAllowed
        );
    }
}
