//! Pure mapping/projection tests for the V1 Session facade (no I/O, no
//! fakes — the parity and store suites live under `tests/`).

use bcs_service_api::application::session::SessionUseCaseError;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::types::MessageViewScope;
use bcs_service_api::{
    CollaborationRuntimeError, GroupUseCaseError, Participant, ParticipantMode,
    ParticipantRole, ServiceError,
};

use crate::{
    map_group_use_case_error, map_runtime_error, map_service_error, map_session_error,
    project_participant,
};

#[test]
fn session_use_case_errors_map_to_stable_v1_codes() {
    assert_eq!(
        map_session_error(SessionUseCaseError::NotFound("s1".into())).code(),
        "session_not_found"
    );
    assert_eq!(
        map_session_error(SessionUseCaseError::InvalidParams("bad".into())).code(),
        "invalid_request"
    );
    assert_eq!(
        map_session_error(SessionUseCaseError::CallbackPending("pending".into())).code(),
        "conflict"
    );
    assert_eq!(
        map_session_error(SessionUseCaseError::Conflict("running".into())).code(),
        "conflict"
    );
    assert_eq!(
        map_session_error(SessionUseCaseError::Internal(ServiceError::SessionNotFound(
            "s2".into()
        )))
        .code(),
        "session_not_found"
    );
}

#[test]
fn service_errors_map_to_stable_v1_codes() {
    assert_eq!(
        map_service_error(ServiceError::GroupNotFound("g1".into())).code(),
        "group_not_found"
    );
    assert_eq!(
        map_service_error(ServiceError::BotNotFound("b1".into())).code(),
        "bot_not_found"
    );
    assert_eq!(
        map_service_error(ServiceError::ParticipantNotFound("b2".into())).code(),
        "participant_not_found"
    );
    assert_eq!(
        map_service_error(ServiceError::Conflict("dup".into())).code(),
        "conflict"
    );
    assert_eq!(
        map_service_error(ServiceError::SessionInvalidParams("x".into())).code(),
        "invalid_request"
    );
}

#[test]
fn session_history_errors_preserve_authentication_and_authorization_categories() {
    assert!(matches!(
        map_group_use_case_error(GroupUseCaseError::Unauthorized("missing".into())),
        ApplicationError::Unauthenticated
    ));
    assert!(matches!(
        map_group_use_case_error(GroupUseCaseError::Forbidden("denied".into())),
        ApplicationError::Forbidden(_)
    ));
    assert_eq!(
        map_group_use_case_error(GroupUseCaseError::InvalidHistoryLimit(0)).code(),
        "invalid_request"
    );
    assert_eq!(
        map_group_use_case_error(GroupUseCaseError::InvalidGroupId("bad".into())).code(),
        "internal_error"
    );
}

#[test]
fn runtime_errors_map_to_stable_v1_codes() {
    assert_eq!(
        map_runtime_error(CollaborationRuntimeError::Unauthenticated).code(),
        "unauthenticated"
    );
    assert_eq!(
        map_runtime_error(CollaborationRuntimeError::Forbidden("denied".into())).code(),
        "forbidden"
    );
    for error in [
        CollaborationRuntimeError::InvalidDefinition("invalid definition".into()),
        CollaborationRuntimeError::InvalidParticipantBinding("invalid binding".into()),
        CollaborationRuntimeError::InvalidRequest("invalid request".into()),
    ] {
        assert_eq!(map_runtime_error(error).code(), "invalid_request");
    }
    assert_eq!(
        map_runtime_error(CollaborationRuntimeError::Conflict("running".into())).code(),
        "conflict"
    );
    assert_eq!(
        map_runtime_error(CollaborationRuntimeError::JudgeUnavailable("offline".into())).code(),
        "internal_error"
    );
}

#[test]
fn project_participant_preserves_human_present_mode() {
    // Vey7i: a legacy invitation-accept inserts a Human participant with
    // `actor_kind: Human, mode: Present`. The V1 projection must NOT map
    // that into the Bot-only `Auto`/`Muted` vocabulary.
    let human = Participant {
        bot_uuid: "human_staff-1".into(),
        bot_name: Some("Alice".into()),
        kind: None,
        role: ParticipantRole::Consultant,
        actor_kind: bcs_service_api::ActorKind::Human,
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
        mode: Some(ParticipantMode::Present),
    };
    let projected = project_participant(&human);
    assert_eq!(projected.actor_id, "human_staff-1");
    assert_eq!(projected.actor_kind, bcs_service_api::ActorKind::Human);
    assert_eq!(projected.role, ParticipantRole::Consultant);
    assert_eq!(projected.mode, ParticipantMode::Present);
    assert_eq!(projected.name.as_deref(), Some("Alice"));

    // `mode: None` falls back to the actor-kind default; for a Human that
    // is `Absent`, surfaced verbatim.
    let absent = Participant {
        mode: None,
        bot_uuid: "human_staff-2".into(),
        bot_name: None,
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: bcs_service_api::ActorKind::Human,
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
    };
    let projected_absent = project_participant(&absent);
    assert_eq!(projected_absent.actor_kind, bcs_service_api::ActorKind::Human);
    assert_eq!(projected_absent.mode, ParticipantMode::Absent);

    // No regression for Bot participants: Auto/Muted pass through as-is.
    let bot = Participant {
        bot_uuid: "bot-1".into(),
        bot_name: None,
        kind: None,
        role: ParticipantRole::Driver,
        actor_kind: bcs_service_api::ActorKind::Bot,
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Full,
        mode: Some(ParticipantMode::Muted),
    };
    let projected_bot = project_participant(&bot);
    assert_eq!(projected_bot.actor_kind, bcs_service_api::ActorKind::Bot);
    assert_eq!(projected_bot.mode, ParticipantMode::Muted);
}