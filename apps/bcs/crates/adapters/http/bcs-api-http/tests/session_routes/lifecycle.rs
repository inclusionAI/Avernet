use super::*;

#[tokio::test]
async fn create_session_returns_created_and_forwards_principal() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({
                "title": "Planning",
                "kind": "service_invocation",
                "acting_bot_id": "bot-owned",
                "creator_role": "manager",
                "message_view_scope": "participant",
                "input": {"query": "how to coordinate?", "custom": {"n": 1}},
                "meta": {
                    "callback_target": {"baas_session_id": "baas-1"},
                    "channel": {"source": "dingtalk", "binding_id": "binding-1"}
                },
                "context_delivery": "inject"
            }),
        ))
        .await
        .expect("create response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_100);
    assert_eq!(body["message"], "Created");
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["session_id"], "session-1");
    assert_eq!(body["data"]["group_id"], "group-1");
    {
        // Protocol adaptation only: the adapter carries
        // the raw authenticated caller; the application
        // resolves the effective SessionCaller (live
        // hybrid rule).
        let created = session.created.lock().expect("create lock");
        let created = created.as_ref().expect("create command");
        assert_eq!(created.caller, caller());
        assert_eq!(created.group_id, "group-1");
        assert_eq!(created.title.as_deref(), Some("Planning"));
        assert_eq!(
            created.input,
            Some(json!({"query": "how to coordinate?", "custom": {"n": 1}}))
        );
        assert_eq!(created.kind, Some(SessionKind::ServiceInvocation));
        assert_eq!(created.acting_bot_id.as_deref(), Some("bot-owned"));
        assert_eq!(created.creator_role, Some(ParticipantRole::Manager));
        assert_eq!(
            created.message_view_scope,
            Some(bcs_domain::MessageViewScope::Participant)
        );
        assert_eq!(created.context_delivery, Some(DeliveryType::Inject));
        assert_eq!(
            created.meta,
            Some(json!({
                "callback_target": {"baas_session_id": "baas-1"},
                "channel": {"source": "dingtalk", "binding_id": "binding-1"}
            }))
        );
    }
}
#[tokio::test]
async fn create_session_preserves_fixed_loop_run_execution_metadata() {
    let fixture: Value = serde_json::from_str(include_str!("../../../../../../tests/fixtures/fixed_loop_api.json")).unwrap();
    let session = Arc::new(FakeSessionService::default());
    *session.state_machine_run.lock().unwrap() = Some(serde_json::from_value(fixture["run"].clone()).unwrap());
    let app = test_session_router(session, Arc::new(FakeSessionMessageService::default()));
    let response = app.oneshot(authenticated_request("POST", "/openapi/v1/collaboration/groups/group-1/sessions",
        json!({"kind": "service_invocation", "input": {"query": "iterate"}}))).await.unwrap();
    assert_eq!(response.status(), StatusCode::CREATED);
    let body = response_json(response).await;
    assert_eq!(body["data"]["state_machine_run_id"], "run-1");
    assert_eq!(body["data"]["state_machine_run"]["node_execution_metadata"], fixture["run"]["node_execution_metadata"]);
    assert_eq!(body["data"]["state_machine_run"]["nodes"].as_array().unwrap().len(), 4);
}
#[tokio::test]
async fn create_session_accepts_explicit_authenticated_human_actor() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({
                "acting_bot_id": "human_staff-1",
                "creator_role": "observer"
            }),
        ))
        .await
        .expect("explicit Human create response");
    assert_eq!(response.status(), StatusCode::CREATED);

    let created = session.created.lock().expect("create lock");
    let created = created.as_ref().expect("create command");
    assert_eq!(created.caller, caller());
    assert_eq!(created.acting_bot_id.as_deref(), Some("human_staff-1"));
    assert_eq!(created.creator_role, Some(ParticipantRole::Observer));
}
#[tokio::test]
async fn create_session_accepts_bot_identity_and_raw_string_input() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router_for_caller(
        session.clone(),
        message,
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: Some(AuthenticatedBotIdentity {
                bot_uuid: "bot-1".into(),
                owner_id: "staff-1".into(),
                app_id: 1,
                agent_code: "agent-1".into(),
            }),
            app: None,
            access_key: None,
        },
    );

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({"input": "run this task"}),
        ))
        .await
        .expect("Bot create response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let created = session.created.lock().expect("create lock");
    let created = created.as_ref().expect("create command");
    // The raw Bot credential is carried to the application
    // (the effective Bot-caller choice is its live rule).
    assert!(created.caller.bot.is_some());
    assert_eq!(created.caller.bot.as_ref().expect("bot").bot_uuid, "bot-1");
    assert!(created.caller.user.is_none());
    assert_eq!(created.input, Some(json!("run this task")));
}
#[tokio::test]
async fn create_session_accepts_any_json_object_input() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/groups/group-1/sessions",
            json!({"input": {"query": 42, "custom": [true, null]}}),
        ))
        .await
        .expect("open object create response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let created = session.created.lock().expect("create lock");
    assert_eq!(
        created.as_ref().expect("create command").input,
        Some(json!({"query": 42, "custom": [true, null]}))
    );
}
#[tokio::test]
async fn list_sessions_returns_page_and_forwards_filters() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/groups/group-1/sessions?view_bot_id=bot-1&offset=5&limit=10&status=running",
            Value::Null,
        ))
        .await
        .expect("list response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"]["items"][0]["session_id"], "session-1");
    assert_eq!(body["data"]["total"], 1);
    assert_eq!(body["data"]["offset"], 5);
    assert_eq!(body["data"]["limit"], 10);
    // `collected` is surfaced for an explicitly named view actor.
    assert_eq!(body["data"]["items"][0]["collected"], true);
    {
        let listed = session.listed.lock().expect("list lock");
        let listed = listed.as_ref().expect("list command");
        assert_eq!(caller_user_id(&listed.caller), "staff-1");
        assert_eq!(listed.view_bot_id.as_deref(), Some("bot-1"));
        assert_eq!(listed.group_id, "group-1");
        assert_eq!(listed.offset, 5);
        assert_eq!(listed.limit, 10);
        assert_eq!(listed.status, Some(SessionStatus::Running));
    }
}
#[tokio::test]
async fn get_session_returns_detail() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/collaboration/sessions/session-1",
            Value::Null,
        ))
        .await
        .expect("get response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["session_id"], "session-1");
    {
        let got = session.got.lock().expect("get lock");
        let got = got.as_ref().expect("get command");
        assert_eq!(caller_user_id(&got.caller), "staff-1");
        assert_eq!(got.session_id, "session-1");
    }
}
#[tokio::test]
async fn update_session_returns_updated_detail() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/collaboration/sessions/session-1",
            json!({"title": "Renamed"}),
        ))
        .await
        .expect("update response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["session_id"], "session-1");
    {
        let updated = session.updated.lock().expect("update lock");
        let updated = updated.as_ref().expect("update command");
        assert_eq!(caller_user_id(&updated.caller), "staff-1");
        assert_eq!(updated.session_id, "session-1");
        assert_eq!(updated.title.as_deref(), Some("Renamed"));
    }
}
#[tokio::test]
async fn delete_session_returns_deleted() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1",
            Value::Null,
        ))
        .await
        .expect("delete response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["deleted"], true);
    {
        let deleted = session.deleted.lock().expect("delete lock");
        let deleted = deleted.as_ref().expect("delete command");
        assert_eq!(caller_user_id(&deleted.caller), "staff-1");
        assert_eq!(deleted.session_id, "session-1");
    }
}
#[tokio::test]
async fn complete_session_route_is_not_mounted() {
    let session = Arc::new(FakeSessionService::default());
    let message = Arc::new(FakeSessionMessageService::default());
    let app = test_session_router(session.clone(), message);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/completion",
            Value::Null,
        ))
        .await
        .expect("complete response");
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(session.completed.lock().expect("complete lock").is_none());
}
#[tokio::test]
async fn collect_session_returns_v1_envelope_and_forwards_command() {
    let session = Arc::new(FakeSessionService::default());
    let app = test_session_router(
        session.clone(),
        Arc::new(FakeSessionMessageService::default()),
    );

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            json!({"participant": "bot-1"}),
        ))
        .await
        .expect("collect response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["request_id"], "request-123");
    assert_eq!(body["data"]["session_id"], "session-1");
    assert_eq!(body["data"]["participant"], "bot-1");
    assert_eq!(body["data"]["collected"], true);
    let command = session.collected.lock().expect("collect lock");
    let command = command.as_ref().expect("collect command");
    assert_eq!(caller_user_id(&command.caller), "staff-1");
    assert_eq!(command.session_id, "session-1");
    assert_eq!(command.participant, "bot-1");
}
#[tokio::test]
async fn uncollect_session_returns_v1_envelope_and_forwards_command() {
    let session = Arc::new(FakeSessionService::default());
    let app = test_session_router(
        session.clone(),
        Arc::new(FakeSessionMessageService::default()),
    );

    let response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1/collect?participant=bot-1",
            Value::Null,
        ))
        .await
        .expect("uncollect response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["session_id"], "session-1");
    assert_eq!(body["data"]["participant"], "bot-1");
    assert_eq!(body["data"]["collected"], false);
    let command = session.uncollected.lock().expect("uncollect lock");
    let command = command.as_ref().expect("uncollect command");
    assert_eq!(caller_user_id(&command.caller), "staff-1");
    assert_eq!(command.session_id, "session-1");
    assert_eq!(command.participant, "bot-1");
}
#[tokio::test]
async fn collection_requests_reject_invalid_participant_input_before_service_call() {
    for (method, uri, body) in [
        (
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            Value::Null,
        ),
        (
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            json!({"participant": ""}),
        ),
        (
            "POST",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            json!({"participant": "bot-1", "unknown": true}),
        ),
        (
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1/collect",
            Value::Null,
        ),
        (
            "DELETE",
            "/openapi/v1/collaboration/sessions/session-1/collect?participant=",
            Value::Null,
        ),
    ] {
        let session = Arc::new(FakeSessionService::default());
        let app = test_session_router(
            session.clone(),
            Arc::new(FakeSessionMessageService::default()),
        );
        let response = app
            .oneshot(authenticated_request(method, uri, body))
            .await
            .expect("invalid collection response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{method} {uri}");
        let response_body = response_json(response).await;
        assert_eq!(response_body["data"]["error_code"], "invalid_request");
        assert!(session.collected.lock().expect("collect lock").is_none());
        assert!(
            session
                .uncollected
                .lock()
                .expect("uncollect lock")
                .is_none()
        );
    }
}
#[tokio::test]
async fn collection_application_errors_use_declared_v1_envelopes() {
    for (forbidden, expected_status, expected_code) in [
        (true, StatusCode::FORBIDDEN, "forbidden"),
        (false, StatusCode::NOT_FOUND, "session_not_found"),
    ] {
        let session = Arc::new(FakeSessionService::default());
        session
            .collection_forbidden
            .store(forbidden, Ordering::Relaxed);
        session
            .collection_not_found
            .store(!forbidden, Ordering::Relaxed);
        let app = test_session_router(session, Arc::new(FakeSessionMessageService::default()));
        let response = app
            .oneshot(authenticated_request(
                "POST",
                "/openapi/v1/collaboration/sessions/session-1/collect",
                json!({"participant": "bot-1"}),
            ))
            .await
            .expect("collection error response");
        assert_eq!(response.status(), expected_status);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], expected_code);
        assert_eq!(body["request_id"], "request-123");
    }
}
