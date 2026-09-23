#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

//! V1 `bot_routes` boundary tests. Shared fixtures are loaded from
//! `support/bot_routes_fixtures.rs` to keep this file under 1000 lines
//! (Task 7 split).

#[path = "support/bot_routes_fixtures.rs"]
mod fixtures;
use fixtures::*;

#[test]
fn api_state_builder_methods_attach_optional_services() {
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier),
    )
    .with_auth_service(Arc::new(NoopAuthService), "http://127.0.0.1/openapi/v1/auth".to_string())
    .with_channel_service(Arc::new(NoopChannelService))
    .with_collaboration_runtime_service(Arc::new(NoopCollaborationRuntimeService))
    .with_collaboration_template_service(Arc::new(NoopCollaborationTemplateService))
    .with_manifest_config("test".to_string(), ManifestConfig::default());

    assert!(state.auth_service.is_some());
    assert_eq!(state.auth_public_base_url, "http://127.0.0.1/openapi/v1/auth");
    assert!(state.channel_service.is_some());
    assert!(state.collaboration_runtime_service.is_some());
    assert!(state.collaboration_template_service.is_some());
    assert_eq!(state.manifest_env, "test");
    assert_eq!(state.manifest, ManifestConfig::default());
}

#[tokio::test]
async fn all_seven_bot_routes_forward_verified_human_and_contract_inputs() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    let candidates = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("candidates response");
    let candidates_status = candidates.status();
    if candidates_status != StatusCode::OK {
        let body = to_bytes(candidates.into_body(), usize::MAX)
            .await
            .expect("candidates body");
        panic!(
            "candidates route returned {candidates_status}: {}",
            String::from_utf8_lossy(&body)
        );
    }
    assert_eq!(candidates_status, StatusCode::OK);
    assert_eq!(
        response_json(candidates).await["data"]["items"][0]["bot"]["kind"],
        "bot"
    );

    let eligible_candidates = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/eligible-candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("eligible candidates response");
    assert_eq!(eligible_candidates.status(), StatusCode::OK);

    let searched = app
        .clone()
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/bots/human_staff-1/candidates/search?q=planning%20help&purpose=collaboration",
            Value::Null,
        ))
        .await
        .expect("candidate search response");
    assert_eq!(searched.status(), StatusCode::OK);
    let searched_body = response_json(searched).await;
    assert_eq!(
        searched_body["data"],
        json!({
            "items": [{
                "bot": physical_bot(),
                "is_friend": true,
                "tags": {"specialty": "planning"},
                "score": 0.0,
                "short_profile": "Planning specialist"
            }],
            "search_mode": "semantic"
        })
    );
    assert!(searched_body["data"].get("context").is_none());
    assert!(!searched_body.to_string().contains("recommend_response"));

    let queried = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/bots/query",
            json!({"bot_ids": ["bot-2", "bot-1", "bot-2"]}),
        ))
        .await
        .expect("query response");
    assert_eq!(queried.status(), StatusCode::OK);

    let got = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1",
            Value::Null,
        ))
        .await
        .expect("get response");
    assert_eq!(got.status(), StatusCode::OK);

    let updated = app
        .clone()
        .oneshot(request(
            "PATCH",
            "/openapi/v1/collaboration/bots/bot-1",
            json!({
                "name": "Renamed",
                "visibility": "protected",
                "status": "hidden",
                "descriptor": {"domains": [], "skills": [{"name": "plan"}]},
                "user_visibility": "private",
                "friend_ext": {"no_check_scope_friend_deps": ["dep-1"]},
                "friend_check_in_strategy": "DEPT_FREE"
            }),
        ))
        .await
        .expect("update response");
    assert_eq!(updated.status(), StatusCode::OK);

    let mine = app
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/mine?kind=human&name=vin&status=online&reachability=unreachable&offset=2&limit=3",
            Value::Null,
        ))
        .await
        .expect("mine response");
    assert_eq!(mine.status(), StatusCode::OK);
    let mine_body = response_json(mine).await;
    assert_eq!(mine_body["data"]["items"][0]["kind"], "human");
    assert_eq!(mine_body["data"]["items"][0]["user_visibility"], "protected");
    assert_eq!(mine_body["data"]["items"][0]["friend_ext"], json!({}));
    assert_eq!(
        mine_body["data"]["items"][0]["friend_check_in_strategy"],
        "APPROVAL"
    );

    let candidates = service.candidates.lock().expect("candidates lock");
    let candidates = candidates.as_ref().expect("candidates command");
    assert_eq!(
        candidates.caller.user.as_ref().map(|user| user.id.as_str()),
        Some("staff-1")
    );
    assert_eq!(candidates.bot_id, "human_staff-1");
    assert_eq!(candidates.purpose, BotCandidatePurpose::Collaboration);
    assert_eq!(candidates.name.as_deref(), Some("planner"));
    assert_eq!((candidates.offset, candidates.limit), (5, 10));
    let eligible_candidates = service
        .eligible_candidates
        .lock()
        .expect("eligible candidates lock");
    let eligible_candidates = eligible_candidates
        .as_ref()
        .expect("eligible candidates command");
    assert_eq!(
        eligible_candidates.caller.user.as_ref().map(|user| user.id.as_str()),
        Some("staff-1")
    );
    assert_eq!(eligible_candidates.bot_id, "human_staff-1");
    assert_eq!(eligible_candidates.purpose, BotCandidatePurpose::Collaboration);
    assert_eq!(eligible_candidates.name.as_deref(), Some("planner"));
    assert_eq!((eligible_candidates.offset, eligible_candidates.limit), (5, 10));
    let searches = service
        .candidate_searches
        .lock()
        .expect("candidate searches lock");
    let search = searches.first().expect("candidate search command");
    assert_eq!(
        search.caller.user.as_ref().map(|user| user.id.as_str()),
        Some("staff-1")
    );
    assert_eq!(search.bot_id, "human_staff-1");
    assert_eq!(search.purpose, BotCandidatePurpose::Collaboration);
    assert_eq!(search.query.as_deref(), Some("planning help"));

    assert_eq!(
        service
            .query
            .lock()
            .expect("query lock")
            .as_ref()
            .expect("query command")
            .bot_ids,
        vec!["bot-2", "bot-1", "bot-2"]
    );
    let update = service.update.lock().expect("update lock");
    let update = update.as_ref().expect("update command");
    assert_eq!(update.patch.visibility, Some(BotVisibility::Protected));
    assert_eq!(update.patch.status, Some(BotStatus::Hidden));
    assert_eq!(update.patch.user_visibility, Some(UserVisibility::Private));
    assert_eq!(
        update.patch.friend_check_in_strategy,
        Some(FriendCheckInStrategy::DeptFree)
    );
    assert_eq!(
        update.patch.friend_ext.as_ref().expect("friend ext")["no_check_scope_friend_deps"],
        json!(["dep-1"])
    );
    assert_eq!(
        update
            .patch
            .descriptor
            .as_ref()
            .and_then(|d| d.domains.as_ref()),
        Some(&vec![])
    );
    let mine = service.mine.lock().expect("mine lock");
    let mine = mine.as_ref().expect("mine command");
    assert_eq!(mine.kind, Some(BotKind::Human));
    assert_eq!(mine.reachability, Some(BotReachability::Unreachable));
}

#[tokio::test]
async fn invite_code_routes_forward_through_gate_and_validate_payloads() {
    let bot_service = Arc::new(FakeBotService::default());
    let invite_code_service = Arc::new(FakeInviteCodeService { allow_access: true, ..Default::default() });
    let app = invite_code_test_router(bot_service.clone(), invite_code_service.clone(), true);

    let claim = app
        .clone()
        .oneshot(anonymous_request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/claim",
            Value::Null,
        ))
        .await
        .expect("public claim response");
    assert_eq!(claim.status(), StatusCode::CREATED);
    assert_eq!(claim.headers()["cache-control"], "no-store");
    assert_eq!(response_json(claim).await["data"], json!({"invite_code": "ABC123"}));

    let bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "ABC123"}),
        ))
        .await
        .expect("bind response");
    assert_eq!(bind.status(), StatusCode::OK);
    assert_eq!(response_json(bind).await["data"], json!({"bound": true, "bound_at": 123}));

    let me = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/invite-codes/me",
            Value::Null,
        ))
        .await
        .expect("my binding response");
    assert_eq!(me.status(), StatusCode::OK);
    assert_eq!(response_json(me).await["data"], json!({"bound": true, "bound_at": 456}));

    let init = app
        .clone()
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 3}),
        ))
        .await
        .expect("init response");
    assert_eq!(init.status(), StatusCode::OK);
    assert_eq!(response_json(init).await["data"], json!({"codes": ["ABC123"]}));

    let gated_candidates = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("gated candidates response");
    assert_eq!(gated_candidates.status(), StatusCode::OK);
    assert_eq!(response_json(gated_candidates).await["data"]["items"][0]["bot"]["bot_id"], "bot-1");

    let empty_bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "   "}),
        ))
        .await
        .expect("empty bind response");
    assert_eq!(empty_bind.status(), StatusCode::BAD_REQUEST);
    assert_eq!(response_json(empty_bind).await["data"]["error_code"], "invalid_request");

    let zero_init = app
        .clone()
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 0}),
        ))
        .await
        .expect("zero init response");
    assert_eq!(zero_init.status(), StatusCode::BAD_REQUEST);
    assert_eq!(response_json(zero_init).await["data"]["error_code"], "invalid_request");

    assert_eq!(
        invite_code_service
            .ensure_access_calls
            .lock()
            .expect("ensure access lock")
            .len(),
        1,
        "protected routes should run the invite-code gate exactly once"
    );
    assert_eq!(
        invite_code_service
            .claim_public_invite_code_calls
            .lock()
            .expect("claim public invite code lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .bind_invite_code_calls
            .lock()
            .expect("bind invite code lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .get_my_invite_code_binding_calls
            .lock()
            .expect("get my invite code binding lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .init_invite_codes_calls
            .lock()
            .expect("init invite codes lock")
            .len(),
        1
    );
    assert!(
        bot_service
            .candidates
            .lock()
            .expect("candidates lock")
            .is_some(),
        "protected routes should flow through the invite-code gate when access is granted"
    );
}

#[tokio::test]
async fn public_invite_code_claim_returns_too_many_requests_at_max_count() {
    let bot_service = Arc::new(FakeBotService::default());
    let invite_code_service = Arc::new(FakeInviteCodeService {
        claim_limit_reached: true,
        ..Default::default()
    });
    let response = invite_code_test_router(bot_service, invite_code_service, false)
        .oneshot(anonymous_request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/claim",
            Value::Null,
        ))
        .await
        .expect("claim limit response");

    assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invite_code_claim_limit_reached");
    assert_eq!(
        body["message"],
        "maximum public invite-code claim count has been reached"
    );
}

#[tokio::test]
async fn public_invite_code_claim_returns_not_found_when_disabled() {
    let invite_code_service = Arc::new(FakeInviteCodeService::default());
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier),
    )
    .with_invite_code_service(invite_code_service.clone());
    let response = router(state)
        .oneshot(anonymous_request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/claim",
            Value::Null,
        ))
        .await
        .expect("disabled public claim response");

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(invite_code_service
        .claim_public_invite_code_calls
        .lock()
        .expect("claim public invite code lock")
        .is_empty());
}

#[tokio::test]
async fn invite_code_routes_return_internal_when_the_service_is_missing() {
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier),
    )
    .with_invite_code_gate_enabled(true);

    let app = router(state);

    let bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "ABC123"}),
        ))
        .await
        .expect("bind without invite-code service");
    assert_eq!(bind.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(bind).await["data"]["error_code"], "internal_error");

    let me = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/invite-codes/me",
            Value::Null,
        ))
        .await
        .expect("get binding without invite-code service");
    assert_eq!(me.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(me).await["data"]["error_code"], "internal_error");

    let init = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 3}),
        ))
        .await
        .expect("init without invite-code service");
    assert_eq!(init.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(init).await["data"]["error_code"], "internal_error");
}

#[tokio::test]
async fn invite_code_gate_rejects_protected_routes_when_access_is_not_granted() {
    let bot_service = Arc::new(FakeBotService::default());
    let invite_code_service = Arc::new(FakeInviteCodeService { allow_access: false, ..Default::default() });
    let app = invite_code_test_router(bot_service.clone(), invite_code_service.clone(), true);

    let response = app
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("protected route response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(response_json(response).await["data"]["error_code"], "invite_code_required");
    assert_eq!(
        invite_code_service
            .ensure_access_calls
            .lock()
            .expect("ensure access lock")
            .len(),
        1
    );
    assert!(
        bot_service
            .candidates
            .lock()
            .expect("candidates lock")
            .is_none(),
        "gate rejection must short-circuit the bot service"
    );
}

#[tokio::test]
async fn candidate_search_accepts_omitted_empty_and_whitespace_queries() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    for uri in [
        "/api/v1/collaboration/bots/human_staff-1/candidates/search",
        "/api/v1/collaboration/bots/human_staff-1/candidates/search?q=",
        "/api/v1/collaboration/bots/human_staff-1/candidates/search?q=%20%20%20",
    ] {
        let response = app
            .clone()
            .oneshot(request("GET", uri, Value::Null))
            .await
            .expect("empty candidate search response");
        assert_eq!(response.status(), StatusCode::OK, "{uri}");
        assert_eq!(
            response_json(response).await["data"],
            json!({"items": [], "search_mode": "empty_query"}),
            "{uri}"
        );
    }

    let searches = service
        .candidate_searches
        .lock()
        .expect("candidate searches lock");
    assert_eq!(searches.len(), 3);
    assert_eq!(searches[0].query, None);
    assert_eq!(searches[1].query.as_deref(), Some(""));
    assert_eq!(searches[2].query.as_deref(), Some("   "));
}

#[tokio::test]
async fn candidate_search_serializes_name_fallback_without_a_score() {
    let service = Arc::new(FakeBotService::default());
    let response = test_router(service)
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/bots/human_staff-1/candidates/search?q=fallback",
            Value::Null,
        ))
        .await
        .expect("fallback candidate search response");

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response_json(response).await["data"],
        json!({
            "items": [{
                "bot": physical_bot(),
                "is_friend": true,
                "tags": {"specialty": "planning"}
            }],
            "search_mode": "name_fallback"
        })
    );
}

#[tokio::test]
async fn bot_routes_reject_unknown_request_fields_and_missing_principal() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());
    let unknown = app
        .clone()
        .oneshot(request(
            "PATCH",
            "/openapi/v1/collaboration/bots/bot-1",
            json!({"name": "Bot", "created_by": "forged"}),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(unknown.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response_json(unknown).await["data"]["error_code"],
        "invalid_request"
    );

    let unknown_search_query = app
        .clone()
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/bots/bot-1/candidates/search?q=planning&limit=10",
            Value::Null,
        ))
        .await
        .expect("unknown search query response");
    assert_eq!(unknown_search_query.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response_json(unknown_search_query).await["data"]["error_code"],
        "invalid_request"
    );
    assert!(
        service
            .candidate_searches
            .lock()
            .expect("candidate searches lock")
            .is_empty()
    );

    let missing = app
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/collaboration/bots/bot-1")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("missing principal response");
    assert_eq!(missing.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn bot_routes_reject_invalid_queries_and_missing_service() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    for uri in [
        "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10&unexpected=1",
        "/openapi/v1/collaboration/bots/human_staff-1/eligible-candidates?purpose=collaboration&name=planner&offset=5&limit=10&unexpected=1",
        "/openapi/v1/collaboration/bots/mine?kind=human&name=vin&status=online&reachability=unreachable&offset=2&limit=3&unexpected=1",
    ] {
        let response = app
            .clone()
            .oneshot(request("GET", uri, Value::Null))
            .await
            .expect("invalid query response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{uri}");
        assert_eq!(response_json(response).await["data"]["error_code"], "invalid_request", "{uri}");
    }

    assert!(service.candidates.lock().expect("candidates lock").is_none());
    assert!(service.eligible_candidates.lock().expect("eligible candidates lock").is_none());
    assert!(service.mine.lock().expect("mine lock").is_none());

    let missing_service_app = router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier),
        )
        .with_invite_code_gate_enabled(false),
    );

    let missing_service = missing_service_app
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1",
            Value::Null,
        ))
        .await
        .expect("missing bot service response");
    assert_eq!(missing_service.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(missing_service).await["data"]["error_code"], "internal_error");
}

#[tokio::test]
async fn previous_bcn_path_families_are_not_mounted() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    for uri in [
        "/openapi/v1/bots/bot-1",
        "/openapi/v1/bots/mine",
        "/openapi/v1/bots/collaboration/bot-1",
        "/openapi/v1/groups",
        "/openapi/v1/group-sessions/session-1",
        "/openapi/v1/friend-requests/request-1/accept",
        "/openapi/v1/invitations/token-1/accept",
        "/api/v1/collaboration/bots/bot-1/candidates",
    ] {
        let response = app
            .clone()
            .oneshot(request("GET", uri, Value::Null))
            .await
            .expect("legacy path response");
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{uri}");
    }

    assert!(service.get.lock().expect("get lock").is_none());
    assert!(service.mine.lock().expect("mine lock").is_none());
}
