use super::*;

/// 2b. BOLA guard: a bot must not enumerate another bot's collected
///     sessions. `intruder-bot` requests driver-bot's collected list ->
///     403, even when driver-bot has actually collected sessions.
#[tokio::test]
async fn collected_list_rejects_unauthorized_participant() {
    let (app, mock, _temp_dir, _registry) = test_app().await;

    // Seed a session collected by driver-bot.
    let sid = "group-1:cafebabe";
    mock.sessions.lock().await.push(Session {
        id: sid.to_string(),
        group_id: "group-1".to_string(),
        session_title: Some("Collected by driver".to_string()),
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
    mock.collected
        .lock()
        .await
        .insert((sid.to_string(), "driver-bot".to_string()), 1_000);

    // intruder-bot queries driver-bot's collected list -> 403.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(
                    "/groups/group-1/sessions?participant=driver-bot&collected=true",
                )
                .header("authorization", "Bearer intruder-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(
        response.status(),
        StatusCode::FORBIDDEN,
        "a bot must not view another bot's collected sessions"
    );
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert_eq!(body["error"], "forbidden");

    // driver-bot can still view its own collected list -> 200, 1 item.
    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(
                    "/groups/group-1/sessions?participant=driver-bot&collected=true",
                )
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
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["session_id"], sid);
}
/// 5. Human caller without participant → 400; human with a non-owned bot → 403.
#[tokio::test]
async fn human_caller_collect_enforces_participant_and_ownership() {
    let (app, mock, _temp_dir, _registry) = human_app("alice", &["owned-bot"]).await;

    let sid = "group-1:cccccccc";
    mock.sessions.lock().await.push(Session {
        id: sid.to_string(),
        group_id: "group-1".to_string(),
        session_title: Some("Human test session".to_string()),
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![Participant::bot("owned-bot", ParticipantRole::Driver)],
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

    // Human without participant → 400.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{sid}/collect"))
                .header("content-type", "application/json")
                .body(Body::from(json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(
        response.status(),
        StatusCode::BAD_REQUEST,
        "human without participant must get 400"
    );
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert_eq!(body["error"], "invalid_params");
    assert!(
        body["message"]
            .as_str()
            .unwrap_or("")
            .contains("participant"),
        "expected message about missing participant"
    );

    // Human with a non-owned bot → 403.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{sid}/collect"))
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({"participant": "unauthorized-bot"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(
        response.status(),
        StatusCode::FORBIDDEN,
        "human with non-owned bot must get 403"
    );
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert_eq!(body["error"], "forbidden");
    assert!(
        body["message"]
            .as_str()
            .unwrap_or("")
            .contains("does not own"),
        "expected ownership error message, got: {}",
        body
    );
}

/// 6. Human caller uncollects via `?participant=<owned-bot>` query string.
///    The DELETE endpoint reads `participant` from the query (DELETE bodies are
///    unreliable across proxies); the human must own the named bot. Covers the
///    ownership check on the uncollect path, which the bot-token tests above do
///    not exercise.
#[tokio::test]
async fn human_caller_uncollect_via_query_enforces_ownership() {
    let (app, mock, _temp_dir, _registry) = human_app("alice", &["owned-bot"]).await;

    let sid = "group-1:dddddddd";
    mock.sessions.lock().await.push(Session {
        id: sid.to_string(),
        group_id: "group-1".to_string(),
        session_title: Some("Uncollect via query".to_string()),
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![Participant::bot("owned-bot", ParticipantRole::Driver)],
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
    // Seed as collected.
    mock.collected
        .lock()
        .await
        .insert((sid.to_string(), "owned-bot".to_string()), 1_000);

    // Human uncollect naming a bot the human does NOT own -> 403.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/sessions/{sid}/collect?participant=unauthorized-bot"))
                .header("content-type", "application/json")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert_eq!(body["error"], "forbidden");

    // Human uncollect naming an OWNED bot via the query string -> 200.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/sessions/{sid}/collect?participant=owned-bot"))
                .header("content-type", "application/json")
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

    // Idempotent repeat -> 200, and the collected entry is gone.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/sessions/{sid}/collect?participant=owned-bot"))
                .header("content-type", "application/json")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(
        !mock.collected
            .lock()
            .await
            .contains_key(&(sid.to_string(), "owned-bot".to_string())),
        "collected set must be empty after uncollect"
    );
}
/// §3.3 Human self right (GPT-review batch C
/// finding #8): the collect / collect-listing
/// routes pass the caller's OWN `human_{id}`
/// collector through its self identity — NOT
/// through the authority hook (no Human→Human
/// edges exist). A different Human actor stays
/// denied, and the Bot-participant leg keeps its
/// A→B handover matrix.
#[tokio::test]
async fn human_self_collector_is_a_self_right_not_an_authority_call() {
    let (app, mock, _temp_dir, _registry) = human_app("alice", &["owned-bot"]).await;
    let sid = "group-1:abcd1234";
    push_session(&mock, &sid, "owned-bot").await;

    // Self: allowed with NO authority edge (the
    // hook would always deny `can_manage` on a
    // Human actor id).
    let collect = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{sid}/collect"))
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({"participant": "human_alice"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(collect.status(), StatusCode::OK);

    // Self on the collected LISTING too.
    let listing = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!(
                    "/groups/group-1/sessions?collected=true&participant=human_alice"
                ))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(listing.status(), StatusCode::OK);

    // Uncollect self.
    let uncollect = app
        .clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/sessions/{sid}/collect?participant=human_alice"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(uncollect.status(), StatusCode::OK);

    // A DIFFERENT Human actor is denied (the
    // self branch is identity-bound, not a
    // wildcard).
    let other = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{sid}/collect"))
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({"participant": "human_bob"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(other.status(), StatusCode::FORBIDDEN);
}
