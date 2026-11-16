//! V1 group-list sort selector contracts (design D3/D4/D7).
//!
//! The adapter owns only translation: it validates the `sort` vocabulary,
//! forwards the selected variant into `ListGroups`, and serializes whatever
//! activity timestamp the application projected. Ordering itself is asserted
//! at the application layer.

use super::*;

/// Two summaries in the fixture's default (`created_at DESC, group_id ASC`)
/// order, each carrying a projected activity timestamp.
fn activity_group_summaries() -> Vec<GroupSummary> {
    vec![
        GroupSummary::Normal(NormalGroupSummary {
            group_id: "group-normal".into(),
            version: 1,
            name: None,
            context: None,
            status: GroupStatus::Active,
            visibility: GroupVisibility::Private,
            membership: Membership::Direct,
            originator_actor_id: "bot-1".into(),
            participant_count: 2,
            driver_bot_uuid: "bot-1".into(),
            driver_bot_name: Some("Bot 1".into()),
            strategy: GroupStrategy::Chat,
            participants: Vec::new(),
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
            created_at: 2,
            updated_at: 2,
            last_activity_at: Some(1_700_000_000_100),
        }),
        GroupSummary::DirectMessage(DirectMessageGroupSummary {
            group_id: "group-dm".into(),
            version: 1,
            name: None,
            context: None,
            status: GroupStatus::Active,
            visibility: GroupVisibility::Private,
            membership: Membership::Direct,
            originator_actor_id: "bot-1".into(),
            participant_count: 2,
            peer_actor: None,
            participants: Vec::new(),
            human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::DriverBotOnly,
            created_at: 1,
            updated_at: 2,
            last_activity_at: Some(1_700_000_000_200),
        }),
    ]
}

fn app_with_activity_items(service: &Arc<FakeGroupService>) {
    *service
        .populated_list_items
        .lock()
        .expect("populated list lock") = activity_group_summaries();
}

#[tokio::test]
async fn group_list_without_sort_keeps_the_created_at_order() {
    let service = Arc::new(FakeGroupService::default());
    app_with_activity_items(&service);
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups?offset=0&limit=20",
            Value::Null,
        ))
        .await
        .expect("list response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    // The route forwards the page verbatim: unchanged default ordering.
    assert_eq!(body["data"]["items"][0]["group_id"], json!("group-normal"));
    assert_eq!(body["data"]["items"][1]["group_id"], json!("group-dm"));
    let list = service.list.lock().expect("list lock");
    assert_eq!(
        list.as_ref().expect("list command").sort,
        GroupListSort::CreatedAt
    );
}

#[tokio::test]
async fn group_list_accepts_an_explicit_created_at_sort() {
    let service = Arc::new(FakeGroupService::default());
    app_with_activity_items(&service);
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups?sort=created_at",
            Value::Null,
        ))
        .await
        .expect("list response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["items"][0]["group_id"], json!("group-normal"));
    let list = service.list.lock().expect("list lock");
    assert_eq!(
        list.as_ref().expect("list command").sort,
        GroupListSort::CreatedAt
    );
}

#[tokio::test]
async fn group_list_forwards_the_last_activity_sort() {
    let service = Arc::new(FakeGroupService::default());
    app_with_activity_items(&service);
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups?sort=last_activity",
            Value::Null,
        ))
        .await
        .expect("list response");

    assert_eq!(response.status(), StatusCode::OK);
    let list = service.list.lock().expect("list lock");
    assert_eq!(
        list.as_ref().expect("list command").sort,
        GroupListSort::LastActivity
    );
}

#[tokio::test]
async fn group_list_rejects_unknown_sort_variants() {
    for query in ["sort=LAST_ACTIVITY", "sort=last_activity%20", "sort=recent"] {
        let service = Arc::new(FakeGroupService::default());
        let app = test_router(service.clone());

        let response = app
            .oneshot(authenticated_request(
                "GET",
                &format!("/openapi/v1/collaboration/groups?{query}"),
                Value::Null,
            ))
            .await
            .expect("list response");

        assert_eq!(
            response.status(),
            StatusCode::BAD_REQUEST,
            "query `{query}` must be rejected"
        );
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "invalid_request");
        // Prove the rejection came from the sort enum vocabulary rather than
        // an unknown-field check (the route must still expose `sort`).
        let message = body["message"].as_str().expect("error message");
        assert!(
            message.contains("unknown variant") && message.contains("last_activity"),
            "unexpected rejection message for `{query}`: {message}"
        );
        assert!(
            service.list.lock().expect("list lock").is_none(),
            "rejected query `{query}` must not reach the application"
        );
    }
}

#[tokio::test]
async fn group_list_items_carry_the_last_activity_timestamp() {
    let service = Arc::new(FakeGroupService::default());
    app_with_activity_items(&service);
    let app = test_router(service);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups",
            Value::Null,
        ))
        .await
        .expect("list response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["data"]["items"][0]["last_activity_at"],
        json!(1_700_000_000_100_u64)
    );
    assert_eq!(
        body["data"]["items"][1]["last_activity_at"],
        json!(1_700_000_000_200_u64)
    );
}

#[tokio::test]
async fn public_group_items_omit_the_last_activity_timestamp() {
    let service = Arc::new(FakeGroupService::default());
    // The discovery catalog never populates the field (D4), so the wire form
    // is an absent property rather than a null.
    *service
        .populated_list_items
        .lock()
        .expect("populated list lock") = populated_group_summaries();
    let app = test_router(service);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/public-groups",
            Value::Null,
        ))
        .await
        .expect("public list response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    let items = body["data"]["items"].as_array().expect("items");
    assert_eq!(items.len(), 2);
    for item in items {
        assert_eq!(
            item.get("last_activity_at"),
            None,
            "public catalog item must not expose last_activity_at: {item}"
        );
    }
}
