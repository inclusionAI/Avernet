use super::*;

fn normal_request() -> Value {
    json!({
        "group_kind": "normal",
        "driver_bot_uuid": "bot-1",
        "participants": [{"actor_id": "bot-1", "role": "driver"}],
        "collaboration": {"strategy": "chat"}
    })
}

#[tokio::test]
async fn create_notify_mode_accepts_all_values_and_omission() {
    for mode in [None, Some("all"), Some("driver_bot_only"), Some("none")] {
        let service = Arc::new(FakeGroupService::default());
        let mut request = normal_request();
        if let Some(mode) = mode {
            request["human_mention_notify_mode"] = json!(mode);
        }
        let response = test_router(service.clone())
            .oneshot(authenticated_request("POST", "/openapi/v1/collaboration/groups", request))
            .await.unwrap();
        assert_eq!(response.status(), StatusCode::CREATED, "{mode:?}");
        let command = service.created.lock().unwrap().take().expect("create command");
        let CreateGroupSpec::Collaboration(group) = command.group else {
            panic!("expected normal Group");
        };
        assert_eq!(serde_json::to_value(group.human_mention_notify_mode).unwrap(), mode.unwrap_or("all"));
    }
}

#[tokio::test]
async fn create_notify_mode_rejects_invalid_values_before_dispatch() {
    for mode in [Value::Null, json!("invalid"), json!(1), json!(false), json!([]), json!({})] {
        let service = Arc::new(FakeGroupService::default());
        let mut request = normal_request();
        request["human_mention_notify_mode"] = mode;
        let response = test_router(service.clone())
            .oneshot(authenticated_request("POST", "/openapi/v1/collaboration/groups", request))
            .await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert!(service.created.lock().unwrap().is_none());
    }
}

#[tokio::test]
async fn create_notify_mode_remains_unsupported_for_dm() {
    let service = Arc::new(FakeGroupService::default());
    let response = test_router(service.clone())
        .oneshot(authenticated_request("POST", "/openapi/v1/collaboration/groups", json!({
            "group_kind": "dm", "target_actor_id": "bot-2", "human_mention_notify_mode": "none"
        })))
        .await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(service.created.lock().unwrap().is_none());
}
