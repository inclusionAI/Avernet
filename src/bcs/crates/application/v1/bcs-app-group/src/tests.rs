    use super::*;

    fn assert_code(error: ApplicationError, expected: &str) {
        assert_eq!(error.code(), expected);
    }

    #[test]
    fn group_use_case_errors_map_to_stable_v1_codes() {
        assert_code(
            map_group_error(GroupUseCaseError::Unauthorized("missing principal".into())),
            "unauthenticated",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidGroupId("bad id".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidGroupStatus("closed".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidProposal("bad proposal".into())),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::ProposalNotFound("proposal-1".into())),
            "not_found",
        );
        assert_code(
            map_group_error(GroupUseCaseError::ProposalExpired("proposal-2".into())),
            "not_found",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidHistoryLimit(0)),
            "invalid_request",
        );
        assert_code(
            map_group_error(GroupUseCaseError::InvalidParticipantMode {
                mode: ParticipantMode::Auto,
                actor_kind: ActorKind::Human,
            }),
            "invalid_participant",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Conflict("version mismatch".into())),
            "conflict",
        );
        assert_code(
            map_group_error(GroupUseCaseError::Service(ServiceError::BotNotFound(
                "bot-1".into(),
            ))),
            "bot_not_found",
        );
    }

    #[test]
    fn service_errors_map_to_stable_v1_codes() {
        assert_code(
            map_service_error(ServiceError::GroupNotFound("group-1".into())),
            "group_not_found",
        );
        assert_code(
            map_service_error(ServiceError::BotNotFound("bot-1".into())),
            "bot_not_found",
        );
        assert_code(
            map_service_error(ServiceError::BotNotRegistered("bot-2".into())),
            "bot_not_found",
        );
        assert_code(
            map_service_error(ServiceError::ParticipantNotFound("bot-3".into())),
            "participant_not_found",
        );
        assert_code(
            map_service_error(ServiceError::Unauthorized("missing principal".into())),
            "unauthenticated",
        );
        assert_code(
            map_service_error(ServiceError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::Conflict("version mismatch".into())),
            "conflict",
        );
        assert_code(
            map_service_error(ServiceError::InvalidOperation {
                message: "bad patch".into(),
                request_id: Some("request-1".into()),
            }),
            "invalid_request",
        );
        assert_code(
            map_service_error(ServiceError::SessionInvalidParams("bad session".into())),
            "invalid_request",
        );
        assert_code(
            map_service_error(ServiceError::ExistNonPublicBots { bots: Vec::new() }),
            "non_public_participant",
        );
        assert_code(
            map_service_error(ServiceError::BotHidden("bot-4".into())),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::PrivateBotCannotCollaborate),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::NotFriends(vec!["bot-5".into()])),
            "forbidden",
        );
        assert_code(
            map_service_error(ServiceError::InternalError("database unavailable".into())),
            "internal_error",
        );
    }

    #[test]
    fn collaboration_runtime_errors_map_to_stable_v1_codes() {
        assert_code(
            map_runtime_error(CollaborationRuntimeError::DefinitionNotFound(
                "definition-1".into(),
                1,
            )),
            "collaboration_definition_not_found",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidDefinition(
                "bad definition".into(),
            )),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidParticipantBinding(
                "bad binding".into(),
            )),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::InvalidRequest("bad request".into())),
            "invalid_request",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Unauthenticated),
            "unauthenticated",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Forbidden("denied".into())),
            "forbidden",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::Conflict("run active".into())),
            "conflict",
        );
        assert_code(
            map_runtime_error(CollaborationRuntimeError::RunNotFound("run-1".into())),
            "internal_error",
        );
    }

    #[test]
    fn runtime_rollback_failure_preserves_primary_and_cleanup_errors() {
        let error = map_runtime_and_rollback_error(
            CollaborationRuntimeError::InvalidRequest("bad definition".to_string()),
            Some("session store unavailable".to_string()),
            Some("group store unavailable".to_string()),
        );

        assert!(matches!(
            error,
            ApplicationError::Internal(message)
                if message.contains("bad definition")
                    && message.contains("session store unavailable")
                    && message.contains("group store unavailable")
        ));
    }

    #[test]
    fn successful_runtime_rollback_keeps_client_error_classification() {
        let error = map_runtime_and_rollback_error(
            CollaborationRuntimeError::InvalidParticipantBinding("unknown actor".to_string()),
            None,
            None,
        );

        assert!(matches!(
            error,
            ApplicationError::InvalidInput { code, message }
                if code == "invalid_request" && message == "unknown actor"
        ));
    }
