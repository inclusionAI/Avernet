use super::*;

/// 1. `GET ...?collected=true` without participant → 400
#[tokio::test]
async fn collected_true_without_participant_returns_400() {
    let (app, _mock, _temp_dir, _registry) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/groups/group-1/sessions?collected=true")
                .header("authorization", "Bearer driver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert_eq!(body["error"], "invalid_params");
    assert!(
        body["message"]
            .as_str()
            .unwrap_or("")
            .contains("participant"),
        "expected message about missing participant, got: {}",
        body
    );
}

/// 2. Collect (bot token) → 200 `{collected:true}`; collected list returns
///    the session; repeat collect is idempotent.
#[tokio::test]
async fn collect_and_list_collected_via_bot_token() {
    let (app, mock, _temp_dir, _registry) = test_app().await;

    // Pre-seed a session.
    let sid = "group-1:aaaaaaaa";
    mock.sessions.lock().await.push(Session {
        id: sid.to_string(),
        group_id: "group-1".to_string(),
        session_title: Some("Test session".to_string()),
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

    // POST /sessions/{sid}/collect with bot token
    let response = app
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

    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert_eq!(body["collected"], true);
    assert_eq!(body["session_id"], sid);

    // GET collected list — must now contain the session.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!(
                    "/groups/group-1/sessions?participant=driver-bot&collected=true"
                ))
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
    assert_eq!(items.len(), 1, "collected list must contain the session");
    assert_eq!(items[0]["session_id"], sid);
    // collected=true branch surfaces collected=true on each item.
    assert_eq!(items[0]["collected"], true);

    // Repeat collect — idempotent (still 200).
    let response = app
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

    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert_eq!(body["collected"], true);
}
/// 2d. Ordinary list (no collected=true) with `participant` surfaces
///     per-session collected state: collected=true + collected_at for the
///     collected session, collected=false for the uncollected one. Without
///     `participant`, neither field is present.
#[tokio::test]
async fn ordinary_list_with_participant_surfaces_collected_fields() {
    let (app, mock, _temp_dir, _registry) = test_app().await;

    let sid_collected = "group-1:cccc1111";
    let sid_uncollected = "group-1:dddd2222";
    for sid in [sid_collected, sid_uncollected] {
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
    // Collect only sid_collected as driver-bot.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{sid_collected}/collect"))
                .header("content-type", "application/json")
                .header("authorization", "Bearer driver-token")
                .body(Body::from(json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    // Ordinary list WITH participant=driver-bot -> each item has a `collected`
    // bool; the collected one also has collected_at.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/groups/group-1/sessions?participant=driver-bot")
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
    let by_id: HashMap<&str, &Value> = items
        .iter()
        .map(|it| (it["id"].as_str().unwrap(), it))
        .collect();
    let collected_item = by_id.get(sid_collected).unwrap();
    assert_eq!(collected_item["collected"], true);
    assert!(collected_item["collected_at"].is_u64(), "collected item has collected_at");
    let uncollected_item = by_id.get(sid_uncollected).unwrap();
    assert_eq!(uncollected_item["collected"], false);
    assert!(
        uncollected_item.get("collected_at").is_none() || uncollected_item["collected_at"].is_null(),
        "uncollected item must not surface collected_at"
    );

    // Ordinary list WITHOUT participant -> no `collected` field on any item.
    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/groups/group-1/sessions")
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
    for it in items {
        assert!(
            it.get("collected").is_none(),
            "without participant, collected must not be present; got {it}"
        );
        assert!(
            it.get("collected_at").is_none(),
            "without participant, collected_at must not be present; got {it}"
        );
    }
}

/// 3. DELETE /sessions/{sid}/collect → 200 `{collected:false}`;
///    collected list is empty afterwards.
#[tokio::test]
async fn uncollect_removes_from_collected_list() {
    let (app, mock, _temp_dir, _registry) = test_app().await;

    let sid = "group-1:bbbbbbbb";
    mock.sessions.lock().await.push(Session {
        id: sid.to_string(),
        group_id: "group-1".to_string(),
        session_title: Some("To be uncollected".to_string()),
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

    // Collect first.
    mock.collected
        .lock()
        .await
        .insert((sid.to_string(), "driver-bot".to_string()), 1_000);

    // DELETE uncollect with bot token (participant via query param for bot is
    // optional, so we omit it — the bot token resolves the collector).
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/sessions/{sid}/collect"))
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
    assert_eq!(body["collected"], false);
    assert_eq!(body["session_id"], sid);

    // GET collected list — must be empty now.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!(
                    "/groups/group-1/sessions?participant=driver-bot&collected=true"
                ))
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
    assert!(
        items.is_empty(),
        "collected list must be empty after uncollect"
    );
}

/// 4. Collect on a non-existent session → 404.
#[tokio::test]
async fn collect_nonexistent_session_returns_404() {
    let (app, _mock, _temp_dir, _registry) = test_app().await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/nonexistent-sid/collect")
                .header("content-type", "application/json")
                .header("authorization", "Bearer driver-token")
                .body(Body::from(json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert!(
        body["error"]
            .as_str()
            .unwrap_or("")
            .contains("nonexistent"),
        "expected error mentioning the session id, got: {}",
        body
    );
}
/// The Bot-participant leg of the same route
/// keeps its A→B handover matrix (the self
/// branch must not widen it).
#[tokio::test]
async fn collect_participant_bot_leg_keeps_the_handover_matrix() {
    // A controls the participant: collect passes.
    let (app_a, mock, _t, _r) = human_app("alice", &["owned-bot"]).await;
    let sid = "group-1:abcd1234";
    push_session(&mock, &sid, "owned-bot").await;
    let collect = app_a
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{sid}/collect"))
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({"participant": "owned-bot"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(collect.status(), StatusCode::OK);

    // A→B handover: B collects; the revoked A
    // (same staff identity) is denied.
    let (app_b, mock_b, _t, _r) = human_app("bob", &["owned-bot"]).await;
    push_session(&mock_b, &sid, "owned-bot").await;
    let collect_b = app_b
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{sid}/collect"))
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({"participant": "owned-bot"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(collect_b.status(), StatusCode::OK);
}
