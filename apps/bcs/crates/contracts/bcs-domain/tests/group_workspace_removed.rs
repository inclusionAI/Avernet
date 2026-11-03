use bcs_domain::Group;
use serde_json::json;

fn group() -> Group {
    Group::new("group-1", "driver", vec![])
}

#[test]
fn group_serialization_omits_deprecated_workspace() {
    let value = serde_json::to_value(group()).unwrap();
    assert_eq!(value["id"], "group-1");
    assert!(value.get("workspace").is_none());
}

#[test]
fn legacy_group_workspace_is_ignored_on_read() {
    let mut value = serde_json::to_value(group()).unwrap();
    value["workspace"] = json!({
        "decisions": ["legacy decision"],
        "tasks": [],
        "notes": ["legacy note"],
        "audit_log": []
    });
    let restored: Group = serde_json::from_value(value).unwrap();
    assert_eq!(restored.id, "group-1");
    assert!(serde_json::to_value(restored).unwrap().get("workspace").is_none());
}
