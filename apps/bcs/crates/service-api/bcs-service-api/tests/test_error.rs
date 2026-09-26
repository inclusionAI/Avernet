use bcs_service_api::ServiceError;
use serde_json::Value;

#[test]
fn test_bot_not_found_code() {
    let err = ServiceError::BotNotFound("test-bot".into());
    assert_eq!(err.as_ref(), "bot_not_found");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "bot_id": "test-bot" })
    );
}

#[test]
fn test_bot_not_registered_code() {
    let err = ServiceError::BotNotRegistered("test-bot".into());
    assert_eq!(err.as_ref(), "bot_not_registered");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "bot_id": "test-bot" })
    );
}

#[test]
fn test_bot_not_connected_code() {
    let err = ServiceError::BotNotConnected("test-bot".into());
    assert_eq!(err.as_ref(), "bot_not_connected");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "bot_id": "test-bot" })
    );
}

#[test]
fn test_bot_hidden_code() {
    let err = ServiceError::BotHidden("test-bot".into());
    assert_eq!(err.as_ref(), "bot_hidden");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "bot_id": "test-bot" })
    );
}

#[test]
fn test_group_not_found_code() {
    let err = ServiceError::GroupNotFound("group-1".into());
    assert_eq!(err.as_ref(), "group_not_found");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "group_id": "group-1" })
    );
}

#[test]
fn test_invalid_operation_code() {
    let err = ServiceError::InvalidOperation {
        message: "bad thing".into(),
        request_id: Some("req-1".into()),
    };
    assert_eq!(err.as_ref(), "invalid_operation");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "message": "bad thing", "request_id": "req-1" })
    );
}

#[test]
fn test_internal_error_code() {
    let err = ServiceError::InternalError("something went wrong".into());
    assert_eq!(err.as_ref(), "internal_error");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "reason": "something went wrong" })
    );
}

#[test]
fn test_io_error_maps_to_internal_error_and_null_params() {
    let io_err = std::io::Error::new(std::io::ErrorKind::NotFound, "file not found: /secret/path");
    let err = ServiceError::from(io_err);
    assert_eq!(err.as_ref(), "internal_error");
    assert_eq!(err.error_params(), Value::Null);
}

#[test]
fn test_json_error_maps_to_internal_error_and_null_params() {
    let json_err = serde_json::from_str::<Value>("not json").unwrap_err();
    let err = ServiceError::from(json_err);
    assert_eq!(err.as_ref(), "internal_error");
    assert_eq!(err.error_params(), Value::Null);
}

#[test]
fn test_cannot_add_self_code() {
    let err = ServiceError::CannotAddSelf;
    assert_eq!(err.as_ref(), "cannot_add_self");
    assert_eq!(err.error_params(), Value::Null);
}

#[test]
fn test_not_friends_code() {
    let err = ServiceError::NotFriends(vec!["bot-a".into(), "bot-b".into()]);
    assert_eq!(err.as_ref(), "not_friends");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "bot_ids": ["bot-a", "bot-b"] })
    );
}

#[test]
fn test_pending_request_exists_code() {
    let err = ServiceError::PendingRequestExists {
        request_id: "req-42".into(),
        from_bot: Some("bot-a".into()),
        to_bot: Some("bot-b".into()),
    };
    assert_eq!(err.as_ref(), "pending_request_exists");
    assert_eq!(
        err.error_params(),
        serde_json::json!({
            "request_id": "req-42",
            "from_bot": "bot-a",
            "to_bot": "bot-b"
        })
    );
}

#[test]
fn test_unauthorized_code() {
    let err = ServiceError::Unauthorized("no valid token".into());
    assert_eq!(err.as_ref(), "unauthorized");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "reason": "no valid token" })
    );
}

#[test]
fn test_forbidden_code() {
    let err = ServiceError::Forbidden("access denied".into());
    assert_eq!(err.as_ref(), "forbidden");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "reason": "access denied" })
    );
}

#[test]
fn test_private_bot_cannot_collaborate_code() {
    let err = ServiceError::PrivateBotCannotCollaborate;
    assert_eq!(err.as_ref(), "private_bot_cannot_collaborate");
    assert_eq!(err.error_params(), Value::Null);
}

#[test]
fn test_proposal_not_found_code() {
    let err = ServiceError::ProposalNotFound("prop-99".into());
    assert_eq!(err.as_ref(), "proposal_not_found");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "proposal_id": "prop-99" })
    );
}

#[test]
fn test_provider_not_found_code() {
    let err = ServiceError::ProviderNotFound("prov-1".into());
    assert_eq!(err.as_ref(), "provider_not_found");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "provider_id": "prov-1" })
    );
}

#[test]
fn test_provider_not_ready_for_downlink_code() {
    let err = ServiceError::ProviderNotReadyForDownlink {
        provider_id: "prov-1".into(),
        reason: "downlink disabled".into(),
    };
    assert_eq!(err.as_ref(), "provider_not_ready_for_downlink");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "provider_id": "prov-1", "reason": "downlink disabled" })
    );
}

#[test]
fn test_bot_already_bound_code() {
    let err = ServiceError::BotAlreadyBound {
        bot_id: "bot-1".into(),
        existing_provider_id: "prov-2".into(),
        existing_provider_bot_ref: "ref-3".into(),
    };
    assert_eq!(err.as_ref(), "bot_already_bound");
    assert_eq!(
        err.error_params(),
        serde_json::json!({
            "bot_id": "bot-1",
            "existing_provider_id": "prov-2",
            "existing_provider_bot_ref": "ref-3"
        })
    );
}

#[test]
fn test_friend_request_not_found_code() {
    let err = ServiceError::FriendRequestNotFound("fr-1".into());
    assert_eq!(err.as_ref(), "friend_request_not_found");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "request_id": "fr-1" })
    );
}

#[test]
fn test_cannot_accept_rejected_code() {
    let err = ServiceError::CannotAcceptRejected;
    assert_eq!(err.as_ref(), "cannot_accept_rejected");
    assert_eq!(err.error_params(), Value::Null);
}

#[test]
fn test_cannot_reject_accepted_code() {
    let err = ServiceError::CannotRejectAccepted;
    assert_eq!(err.as_ref(), "cannot_reject_accepted");
    assert_eq!(err.error_params(), Value::Null);
}

#[test]
fn test_participant_not_found_code() {
    let err = ServiceError::ParticipantNotFound("part-1".into());
    assert_eq!(err.as_ref(), "participant_not_found");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "participant_id": "part-1" })
    );
}

#[test]
fn test_session_not_found_code() {
    let err = ServiceError::SessionNotFound("sess-1".into());
    assert_eq!(err.as_ref(), "session_not_found");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "session_id": "sess-1" })
    );
}

#[test]
fn test_session_invalid_params_code() {
    let err = ServiceError::SessionInvalidParams("bad mode".into());
    assert_eq!(err.as_ref(), "session_invalid_params");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "reason": "bad mode" })
    );
}

#[test]
fn test_session_callback_pending_code() {
    let err = ServiceError::SessionCallbackPending("sess-1".into());
    assert_eq!(err.as_ref(), "session_callback_pending");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "session_id": "sess-1" })
    );
}

#[test]
fn test_message_limit_reached_code() {
    let err = ServiceError::MessageLimitReached("group msg limit exceeded".into());
    assert_eq!(err.as_ref(), "message_limit_reached");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "reason": "group msg limit exceeded" })
    );
}

#[test]
fn test_conflict_code() {
    let err = ServiceError::Conflict("already exists".into());
    assert_eq!(err.as_ref(), "conflict");
    assert_eq!(
        err.error_params(),
        serde_json::json!({ "reason": "already exists" })
    );
}

#[test]
fn test_exist_non_public_bots_code() {
    let err = ServiceError::ExistNonPublicBots {
        bots: vec![
            ("bot-1".into(), Some("Bot One".into())),
            ("bot-2".into(), None),
        ],
    };
    assert_eq!(err.as_ref(), "exist_non_public_bots");
    assert_eq!(
        err.error_params(),
        serde_json::json!({
            "code": "exist_none_public_bots",
            "bots": [
                { "bot_uuid": "bot-1", "bot_name": "Bot One" },
                { "bot_uuid": "bot-2", "bot_name": "bot-2" },
            ]
        })
    );
}