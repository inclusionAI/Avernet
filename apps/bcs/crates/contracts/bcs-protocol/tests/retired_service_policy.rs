use bcs_protocol::http::groups::CreateGroupRequest;
use serde_json::json;

#[test]
fn group_creation_accepts_and_discards_retired_service_policy_even_when_null() {
    for policy in [
        json!({"timeout_seconds": 1}),
        json!({"max_concurrency": 1}),
        json!({"callback_config": {"channels": []}}),
        json!({}),
        json!(null),
    ] {
        let result = serde_json::from_value::<CreateGroupRequest>(json!({
            "driver_bot": "bot-a", "service_spec": policy,
        }));
        let request = result.expect("legacy Backend requests remain compatible");
        assert!(serde_json::to_value(request).unwrap().get("service_spec").is_none());
    }
}

#[test]
fn ordinary_group_creation_needs_no_service_policy() {
    let request: CreateGroupRequest =
        serde_json::from_value(json!({"driver_bot": "bot-a"})).unwrap();
    assert_eq!(request.driver_bot.as_deref(), Some("bot-a"));
    assert!(
        serde_json::to_value(request)
            .unwrap()
            .get("service_spec")
            .is_none()
    );
}
