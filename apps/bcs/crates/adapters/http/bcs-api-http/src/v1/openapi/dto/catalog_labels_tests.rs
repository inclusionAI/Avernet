use super::*;
#[test]
fn group_patch_labels_preserve_presence_and_reject_invalid_types(){
    assert!(serde_json::from_value::<UpdateGroupRequest>(serde_json::json!({"name":"old"})).unwrap().catalog_labels.is_none());
    let cleared:GroupPatch=serde_json::from_value::<UpdateGroupRequest>(serde_json::json!({"catalog_labels":{}})).unwrap().into();
    assert!(cleared.catalog_labels.is_some());assert!(!cleared.is_empty());
    for value in [serde_json::json!(null),serde_json::json!([]),serde_json::json!(1),serde_json::json!({"x":[null]}),serde_json::json!({"x":"a"})]{
        assert!(serde_json::from_value::<UpdateGroupRequest>(serde_json::json!({"name":"must not save","catalog_labels":value})).is_err());
    }
}
#[test]
fn normal_and_dm_creation_use_the_same_strict_label_shape(){
    for kind in ["normal","dm"] {
        let mut value=if kind=="normal"{serde_json::json!({"group_kind":"normal","driver_bot_uuid":"driver","participants":[],"collaboration":{"strategy":"manager_worker"}})}else{serde_json::json!({"group_kind":"dm","target_actor_id":"target"})};
        value["catalog_labels"]=serde_json::json!({});assert!(serde_json::from_value::<CreateGroupRequest>(value.clone()).is_ok());
        value["catalog_labels"]=serde_json::Value::Null;assert!(serde_json::from_value::<CreateGroupRequest>(value).is_err());
    }
}
