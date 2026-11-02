use super::*;

#[tokio::test]
async fn session_delete_follows_the_current_control_matrix() {
    // A(owner): alice currently controls the creator AND
    // the driver.
    let fixture = session_fixture("alice").await;
    fixture.authority.seed_owner("driver-bot", "alice");
    fixture.authority.seed_owner("creator-bot", "alice");
    let ok = delete_session(&fixture.app, SID, "human_alice").await;
    assert_eq!(ok.status(), StatusCode::OK);

    // A→B handover: alice's edges are gone; the
    // historical `created_by` rows stay — they must NOT
    // authorize.
    let fixture_revoked = session_fixture("alice").await;
    fixture_revoked.authority.seed_owner("driver-bot", "bob");
    fixture_revoked.authority.seed_owner("creator-bot", "bob");
    let denied = delete_session(&fixture_revoked.app, SID, "human_alice").await;
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);

    // The CURRENT resource administrator deletes the
    // SAME session through the same route.
    let ok_b = delete_session(&fixture_revoked.app, SID, "human_bob").await;
    assert_eq!(ok_b.status(), StatusCode::OK);
}

#[tokio::test]
async fn session_list_visibility_follows_the_current_edge() {
    let fixture = session_fixture("alice").await;
    fixture.authority.seed_owner("driver-bot", "alice");
    let response = list_sessions(&fixture.app, "group-1").await;
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["items"].as_array().map(|a| a.len()), Some(1));

    // X(revoked): the same Human with no edges sees
    // nothing — despite the `created_by` mirror.
    let fixture_revoked = session_fixture("alice").await;
    let response = list_sessions(&fixture_revoked.app, "group-1").await;
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let items = json["items"].as_array().cloned().unwrap_or_default();
    assert!(items.is_empty(), "an edgeless Human sees no session: {items:?}");
}

#[tokio::test]
async fn session_collect_requires_the_current_edge_for_the_target_bot() {
    let fixture = session_fixture("alice").await;
    fixture.authority.seed_owner("managed-bot", "alice");
    let ok = fixture
        .app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{SID}/collect"))
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({"participant": "managed-bot"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(ok.status(), StatusCode::OK);

    let stranger = session_fixture("carol").await;
    stranger.authority.seed_owner("driver-bot", "bob");
    let denied = stranger
        .app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{SID}/collect"))
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({"participant": "managed-bot"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
}
