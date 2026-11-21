use super::*;
#[test]
fn write_dto_presence_and_type_matrix() {
    assert!(serde_json::from_value::<Update>(json!({})).unwrap().catalog_labels.is_none());
    assert!(serde_json::from_value::<Update>(json!({"catalog_labels":{}})).unwrap().catalog_labels.is_some());
    for labels in [json!(null),json!(1),json!("x"),json!([]),json!({"x":1}),json!({"x":{}}),json!({"x":[null]}),json!({"x":[1]})] {
        assert!(serde_json::from_value::<Update>(json!({"catalog_labels":labels})).is_err());
        assert!(serde_json::from_value::<Registration>(json!({"provider_bot_ref":"r","agent_card":{},"catalog_labels":labels})).is_err());
        assert!(serde_json::from_value::<NamedRegistration>(json!({"provider_bot_ref":"r","owners":["owner"],"agent_card":{},"catalog_labels":labels})).is_err());
    }
    for labels in [json!({}),json!({"x":null}),json!({"x":[]}),json!({"x":["a","a"]})] {
        assert!(serde_json::from_value::<Update>(json!({"catalog_labels":labels})).is_ok());
    }
}
#[test]
fn old_closed_request_and_response_shapes_reject_new_labels() {
    // Exact pre-change closed update shape; no capability inference from old acceptance.
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    #[allow(dead_code)]
    struct OldUpdate {visibility:Option<A2aRegistrationVisibility>,agent_card_url:Option<String>,agent_card:Option<Value>}
    assert!(serde_json::from_value::<OldUpdate>(json!({"catalog_labels":{}})).is_err());
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    #[allow(dead_code)]
    struct OldView {provider_bot_ref:String,provider_name:String,agent_name:String,bot_id:String,agent_card_url:Option<String>,agent_card:Value}
    let old=json!({"provider_bot_ref":"r","provider_name":"p","agent_name":"a","bot_id":"b","agent_card_url":null,"agent_card":{}});
    assert!(serde_json::from_value::<OldView>(old.clone()).is_ok());
    let mut new=old;new["catalog_labels"]=json!({});assert!(serde_json::from_value::<OldView>(new).is_err());
}
