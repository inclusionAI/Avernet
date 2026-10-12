//! Shared V1 Group driver display metadata on the HTTP boundary.

use super::*;

#[tokio::test]
async fn both_list_routes_preserve_driver_name_and_dm_shape() {
    for route in ["groups", "public-groups"] {
        for name in [Some("Bot 1".to_string()), None] {
            let service = Arc::new(FakeGroupService::default());
            let mut items = populated_group_summaries();
            if let GroupSummary::Normal(group) = &mut items[0] {
                group.driver_bot_name = name.clone();
            }
            *service.populated_list_items.lock().expect("list lock") = items;
            let response = test_router(service)
                .oneshot(authenticated_request(
                    "GET",
                    &format!("/openapi/v1/collaboration/{route}"),
                    Value::Null,
                ))
                .await
                .expect("list response");
            assert_eq!(response.status(), StatusCode::OK);
            let body = response_json(response).await;
            assert_eq!(
                body["data"]["items"][0].get("driver_bot_name"),
                Some(&json!(name))
            );
            assert_eq!(body["data"]["items"][0]["driver_bot_uuid"], "bot-1");
            assert!(body["data"]["items"][1].get("driver_bot_name").is_none());
        }
    }
}

#[tokio::test]
async fn create_get_and_update_preserve_driver_owner_fields() {
    for (owner, name) in [
        (
            Some("human_owner".to_string()),
            Some("Owner Name".to_string()),
        ),
        (Some("human_owner".to_string()), None),
        (None, None),
    ] {
        let service = Arc::new(FakeGroupService::default());
        let mut detail = group_detail();
        if let GroupDetail::Collaboration(group) = &mut detail {
            group.driver_bot_owner = owner.clone();
            group.driver_bot_owner_name = name.clone();
        }
        *service.detail_override.lock().expect("detail lock") = Some(detail);
        let app = test_router(service);
        for (method, path, input, status) in [
            ("GET", "/groups/group-1", Value::Null, StatusCode::OK),
            (
                "PATCH",
                "/groups/group-1",
                json!({"name": "Updated"}),
                StatusCode::OK,
            ),
            (
                "POST",
                "/groups",
                json!({
                    "group_kind": "normal",
                    "driver_bot_uuid": "bot-1",
                    "participants": [{"actor_id": "bot-1", "role": "driver"}],
                    "collaboration": {
                        "strategy": "chat",
                        "delivery_policy": {"bot_final_delivery": "send_to_driver"}
                    }
                }),
                StatusCode::CREATED,
            ),
        ] {
            let response = app
                .clone()
                .oneshot(authenticated_request(
                    method,
                    &format!("/openapi/v1/collaboration{path}"),
                    input,
                ))
                .await
                .expect("detail response");
            assert_eq!(response.status(), status, "{method} {path}");
            let body = response_json(response).await;
            assert_eq!(body["data"].get("driver_bot_owner"), Some(&json!(owner)));
            assert_eq!(
                body["data"].get("driver_bot_owner_name"),
                Some(&json!(name))
            );
        }
    }
}
