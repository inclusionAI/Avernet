use super::*;
use bcs_service_api::ActorKind;

async fn json(response: Response) -> Value {
    serde_json::from_slice(&axum::body::to_bytes(response.into_body(), 8192).await.unwrap()).unwrap()
}

#[tokio::test]
async fn coverage_group_error_translation_preserves_status_details_and_opening_message_code() {
    use GroupUseCaseError as E;
    let cases: Vec<(fn() -> E, StatusCode, &str)> = vec![
        (|| E::Unauthorized("authenticate".into()), StatusCode::UNAUTHORIZED, "authenticate"),
        (|| E::Forbidden("no authority".into()), StatusCode::FORBIDDEN, "no authority"),
        (|| E::InvalidGroupId("bad group".into()), StatusCode::BAD_REQUEST, "bad group"),
        (|| E::InvalidGroupStatus("bad status".into()), StatusCode::BAD_REQUEST, "bad status"),
        (|| E::InvalidProposal("bad proposal".into()), StatusCode::BAD_REQUEST, "bad proposal"),
        (|| E::InvalidHistoryLimit(0), StatusCode::BAD_REQUEST, "Invalid history limit: 0"),
        (|| E::ActorNotFound("missing".into()), StatusCode::NOT_FOUND, "Actor 'missing' not found"),
        (|| E::InvalidParticipantMode { mode: ParticipantMode::Present, actor_kind: ActorKind::Bot }, StatusCode::BAD_REQUEST, "not valid for actor_kind"),
        (|| E::Conflict("stale version".into()), StatusCode::CONFLICT, "stale version"),
    ];
    for (error, status, expected) in cases {
        let response = group_use_case_error_to_http(error()).into_response();
        assert_eq!(response.status(), status);
        assert!(json(response).await["error"].as_str().unwrap().contains(expected));
        let (actual, Json(body)) = group_use_case_error_to_mode_response(error());
        assert_eq!(actual, status); assert_eq!(body["success"], false);
        assert!(body["error"].as_str().unwrap().contains(expected));
    }
    let response = group_use_case_error_to_http(E::InvalidProposal("invalid_opening_message:  empty content ".into())).into_response();
    let body = json(response).await;
    assert_eq!(body["code"], "invalid_opening_message"); assert_eq!(body["message"], "empty content");
    let response = group_use_case_error_to_http(E::Service(ServiceError::ExistNonPublicBots {
        bots: vec![("unnamed".into(), None), ("named".into(), Some("Display name".into()))],
    })).into_response();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = json(response).await;
    assert_eq!(body["params"]["code"], "exist_none_public_bots");
    assert_eq!(body["params"]["bots"][0]["bot_name"], "unnamed");
    assert_eq!(body["params"]["bots"][1]["bot_name"], "Display name");
}

#[test]
fn coverage_group_mode_service_errors_keep_business_failures_distinct() {
    use ServiceError as E;
    for (error, expected) in [
        (E::CannotAddSelf, StatusCode::BAD_REQUEST),
        (E::BotNotFound("missing".into()), StatusCode::NOT_FOUND),
        (E::BotNotRegistered("missing".into()), StatusCode::NOT_FOUND),
        (E::FriendRequestNotFound("missing".into()), StatusCode::NOT_FOUND),
        (E::NotFriends(vec!["not connected".into()]), StatusCode::FORBIDDEN),
        (E::Conflict("stale".into()), StatusCode::CONFLICT),
        (E::CannotAcceptRejected, StatusCode::CONFLICT),
        (E::CannotRejectAccepted, StatusCode::CONFLICT),
        (E::PrivateBotCannotCollaborate, StatusCode::FORBIDDEN),
        (E::InternalError("dependency failed".into()), StatusCode::INTERNAL_SERVER_ERROR),
    ] {
        let expected_message = error.to_string();
        let (status, Json(body)) = group_use_case_error_to_mode_response(GroupUseCaseError::Service(error));
        assert_eq!(status, expected); assert_eq!(body["success"], false); assert_eq!(body["error"], expected_message);
    }
}

#[tokio::test]
async fn coverage_collaboration_runtime_errors_map_to_retryable_and_client_statuses() {
    use CollaborationRuntimeError as E;
    for (error, status, expected) in [
        (E::InvalidDefinition("bad definition".into()), StatusCode::BAD_REQUEST, "bad definition"),
        (E::InvalidParticipantBinding("bad binding".into()), StatusCode::BAD_REQUEST, "bad binding"),
        (E::InvalidRequest("bad request".into()), StatusCode::BAD_REQUEST, "bad request"),
        (E::DefinitionNotFound("definition".into(), 2), StatusCode::NOT_FOUND, "definition@2"),
        (E::RunNotFound("run".into()), StatusCode::NOT_FOUND, "StateMachineRun 'run'"),
        (E::NodeNotFound { run_id: "run".into(), node_id: "node".into() }, StatusCode::NOT_FOUND, "run/node"),
        (E::Unauthenticated, StatusCode::UNAUTHORIZED, "authentication is required"),
        (E::Forbidden("no authority".into()), StatusCode::FORBIDDEN, "no authority"),
        (E::JudgeUnavailable("judge offline".into()), StatusCode::INTERNAL_SERVER_ERROR, "judge offline"),
        (E::Conflict("stale run".into()), StatusCode::CONFLICT, "stale run"),
        (E::Internal(ServiceError::InternalError("dependency failed".into())), StatusCode::INTERNAL_SERVER_ERROR, "dependency failed"),
    ] {
        let response = collaboration_runtime_error_to_http(error).into_response();
        assert_eq!(response.status(), status);
        assert!(json(response).await["error"].as_str().unwrap().contains(expected));
    }
    assert!(routing_policy_from_protocol_value(None).unwrap().is_none());
    assert!(matches!(routing_policy_from_protocol_value(Some(serde_json::json!("invalid policy"))), Err(HttpAdapterError::BadRequest(message)) if message.starts_with("invalid routing_policy:")));
    assert_eq!(group_kind_from_str("normal").unwrap(), GroupKind::Normal);
    assert_eq!(group_kind_from_str("dm").unwrap(), GroupKind::Dm);
    assert!(matches!(group_kind_from_str("unknown"), Err(HttpAdapterError::BadRequest(message)) if message.contains("unknown")));
}
