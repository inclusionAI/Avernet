use super::*;

/// 2c. Collected list is ordered by collect-event time (newest first) and
///     each item surfaces `collected_at`. Collect A then B; list must be
///     [B, A] with B.collected_at > A.collected_at.
#[tokio::test]
async fn collected_list_ordered_by_collect_event_desc() {
    let (app, mock, _temp_dir, _registry) = test_app().await;

    let sid_a = "group-1:aaaa1111";
    let sid_b = "group-1:bbbb2222";
    for sid in [sid_a, sid_b] {
        mock.sessions.lock().await.push(Session {
            id: sid.to_string(),
            group_id: "group-1".to_string(),
            session_title: Some(sid.to_string()),
            env: None,
            status: SessionStatus::Running,
            session_kind: SessionKind::Chat,
            participants: vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
            group_version: Some(1),
            caller_id: None,
            input: None,
            output: None,
            error_message: None,
            activation_count: 1,
            message_visibility_version: 1,
            caller_principal: None,
            created_by: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
            meta: None,
            current_msg_seq: 0,
            participant_join_seq: None,
            collected_at: None,
        });
    }

    // Collect A first, then B. Mock assigns monotonic collected_at per collect.
    for sid in [sid_a, sid_b] {
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(format!("/sessions/{sid}/collect"))
                    .header("content-type", "application/json")
                    .header("authorization", "Bearer driver-token")
                    .body(Body::from(json!({}).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/groups/group-1/sessions?participant=driver-bot&collected=true")
                .header("authorization", "Bearer driver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    let items = body["items"].as_array().expect("items array");
    assert_eq!(items.len(), 2, "both collected sessions must be listed");
    // Newest collect (B) first, then A.
    assert_eq!(items[0]["session_id"], sid_b);
    assert_eq!(items[1]["session_id"], sid_a);
    // collected_at is surfaced and ordered B > A.
    let ts_b = items[0]["collected_at"].as_u64().expect("B collected_at");
    let ts_a = items[1]["collected_at"].as_u64().expect("A collected_at");
    assert!(ts_b > ts_a, "B (collected later) must outrank A: {ts_b} > {ts_a}");
}
