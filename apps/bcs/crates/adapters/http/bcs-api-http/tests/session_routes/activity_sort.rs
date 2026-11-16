//! V1 session-list sort selector contracts (design D3/D4/D7).
//!
//! The adapter owns only translation: it validates the `sort` vocabulary,
//! forwards the selected variant into `ListSessions`, and serializes whatever
//! activity timestamp the application projected. Ordering itself is asserted
//! at the application layer.

use super::*;

#[tokio::test]
async fn session_list_without_sort_keeps_the_created_at_order() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            Value::Null,
        ))
        .await
        .expect("list response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["items"][0]["session_id"], json!("session-1"));
    let listed = session.listed.lock().expect("list lock");
    assert_eq!(
        listed.as_ref().expect("list command").sort,
        SessionListSort::CreatedAt
    );
}

#[tokio::test]
async fn session_list_accepts_an_explicit_created_at_sort() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups/group-1/sessions?sort=created_at",
            Value::Null,
        ))
        .await
        .expect("list response");

    assert_eq!(response.status(), StatusCode::OK);
    let listed = session.listed.lock().expect("list lock");
    assert_eq!(
        listed.as_ref().expect("list command").sort,
        SessionListSort::CreatedAt
    );
}

#[tokio::test]
async fn session_list_forwards_the_last_activity_sort() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups/group-1/sessions?sort=last_activity",
            Value::Null,
        ))
        .await
        .expect("list response");

    assert_eq!(response.status(), StatusCode::OK);
    let listed = session.listed.lock().expect("list lock");
    assert_eq!(
        listed.as_ref().expect("list command").sort,
        SessionListSort::LastActivity
    );
}

#[tokio::test]
async fn session_list_rejects_unknown_sort_variants() {
    for query in ["sort=LAST_ACTIVITY", "sort=last_activity%20", "sort=recent"] {
        let session = Arc::new(FakeSessionService::default());
        let message = Arc::new(FakeSessionMessageService::default());
        let app = test_session_router(session.clone(), message);

        let response = app
            .oneshot(authenticated_request(
                "GET",
                &format!("/openapi/v1/collaboration/groups/group-1/sessions?{query}"),
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
            session.listed.lock().expect("list lock").is_none(),
            "rejected query `{query}` must not reach the application"
        );
    }
}

#[tokio::test]
async fn session_list_items_carry_the_last_activity_timestamp() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session, message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            Value::Null,
        ))
        .await
        .expect("list response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(
        body["data"]["items"][0]["last_activity_at"],
        json!(1_700_000_000_300_u64)
    );
}