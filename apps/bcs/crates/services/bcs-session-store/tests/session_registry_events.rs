//! Eventful Group creation must claim its Session type atomically with persistence.
use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_event_store::MemoryEventStore;
use bcs_service_api::port::NewEvent;
use bcs_service_api::port::repo::{
    AppendEventRecord, CreateSessionWithEvent, EventRepoPort, NewSessionParams, SessionRepoPort,
};
use bcs_service_api::port::repo::session_registry::SessionType;
use bcs_service_api::types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject};
use bcs_service_api::ServiceError;
use bcs_session_store::MemorySessionRepo;

use bcs_service_api::port::repo::{AddSessionParticipantWithEvent, RemoveSessionParticipantWithEvent,
    UpdateSessionParticipantMessageViewScopeWithEvent, CompleteSessionWithEvent};
use bcs_service_api::{ActorKind, Participant, ParticipantRole, ParticipantMode, SessionKind, SessionStatus};
use bcs_service_api::types::MessageViewScope;

fn creation(session_id: &str) -> CreateSessionWithEvent {
    CreateSessionWithEvent {
        group_id: "registry-group".into(),
        params: NewSessionParams { id: Some(session_id.into()), ..Default::default() },
        event: AppendEventRecord {
            event: NewEvent {
                event_id: format!("created-{session_id}"),
                event_type: "session.created".into(),
                schema_version: EVENT_SCHEMA_VERSION_V1.into(),
                producer: "session-registry-contract".into(),
                producer_key: session_id.into(),
                occurred_at: "2026-10-08T00:00:00.000Z".into(),
                subject: EventSubject { subject_type: "session".into(), id: session_id.into() },
                scope: EventScope {
                    group_id: Some("registry-group".into()),
                    session_id: Some(session_id.into()),
                    ..Default::default()
                },
                stream_key: format!("session:{session_id}"),
                actor: None, correlation_id: None, causation_event_id: None, trace_id: None,
                data: BTreeMap::new(),
            },
            recorded_at: "2026-10-08T00:00:00.001Z".into(),
            retention_until_ms: 2_000_000_000_000,
            env: "dev".into(),
        },
    }
}

fn mutation(id: &str, key: &str) -> AppendEventRecord {
    let mut event = creation(id).event;
    event.event.event_id = format!("{id}-{key}");
    event.event.producer_key = key.into();
    event
}

#[tokio::test]
async fn coverage_eventful_session_rejects_missing_event_store_without_changing_state() {
    let repo = MemorySessionRepo::new();
    let id = "registry-group:1234abcd";
    assert!(matches!(repo.create_with_event(creation(id)).await, Err(ServiceError::InternalError(_))));
    assert!(repo.get(id).await.is_none());
    let initial = repo.create("registry-group", NewSessionParams { id: Some(id.into()), ..Default::default() }).await.unwrap();
    assert!(matches!(repo.add_participant_with_event(AddSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: vec![], participant: Participant::bot("bot", ParticipantRole::Observer),
        event: mutation(id, "unconfigured-add"),
    }).await, Err(ServiceError::InternalError(_))));
    assert!(matches!(repo.remove_participant_with_event(RemoveSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: vec![], bot_uuid: "bot".into(), event: mutation(id, "unconfigured-remove"),
    }).await, Err(ServiceError::InternalError(_))));
    assert!(matches!(repo.update_participant_message_view_scope_with_event(UpdateSessionParticipantMessageViewScopeWithEvent {
        session_id: id.into(), expected_participants: vec![], actor_id: "bot".into(), message_view_scope: MessageViewScope::Full,
        mode: None, event: mutation(id, "unconfigured-scope"),
    }).await, Err(ServiceError::InternalError(_))));
    assert!(matches!(repo.complete_if_running_with_event(CompleteSessionWithEvent {
        session_id: id.into(), expected_activation_count: initial.activation_count, output: None, error: None,
        event: mutation(id, "unconfigured-complete"),
    }).await, Err(ServiceError::InternalError(_))));
    let unchanged = repo.get(id).await.unwrap();
    assert!(unchanged.participants.is_empty()); assert_eq!(unchanged.status, SessionStatus::Running);
    assert_eq!(unchanged.activation_count, initial.activation_count);
}

#[tokio::test]
async fn coverage_eventful_session_missing_members_and_event_conflicts_preserve_business_state() {
    let events = Arc::new(MemoryEventStore::new());
    let repo = MemorySessionRepo::new().with_event_store(events.clone());
    let id = "registry-group:abcd1234";
    let mut command = creation(id);
    command.params.participants = vec![Participant::bot("member", ParticipantRole::Observer)];
    let initial = repo.create_with_event(command).await.unwrap();
    let mut collision = mutation(id, "collision"); collision.event.event_id = format!("created-{id}");
    assert!(matches!(repo.add_participant_with_event(AddSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: initial.participants.clone(), participant: Participant::bot("new", ParticipantRole::Worker),
        event: collision.clone(),
    }).await, Err(ServiceError::InternalError(_))));
    assert!(matches!(repo.remove_participant_with_event(RemoveSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: initial.participants.clone(), bot_uuid: "member".into(), event: collision.clone(),
    }).await, Err(ServiceError::InternalError(_))));
    assert!(matches!(repo.update_participant_message_view_scope_with_event(UpdateSessionParticipantMessageViewScopeWithEvent {
        session_id: id.into(), expected_participants: initial.participants.clone(), actor_id: "member".into(), message_view_scope: MessageViewScope::Full,
        mode: None, event: collision.clone(),
    }).await, Err(ServiceError::InternalError(_))));
    assert!(matches!(repo.complete_if_running_with_event(CompleteSessionWithEvent {
        session_id: id.into(), expected_activation_count: initial.activation_count, output: Some(serde_json::json!("must not commit")), error: None,
        event: collision,
    }).await, Err(ServiceError::InternalError(_))));
    assert!(matches!(repo.remove_participant_with_event(RemoveSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: initial.participants.clone(), bot_uuid: "missing".into(), event: mutation(id, "missing-remove"),
    }).await, Err(ServiceError::SessionInvalidParams(_))));
    assert!(matches!(repo.update_participant_message_view_scope_with_event(UpdateSessionParticipantMessageViewScopeWithEvent {
        session_id: id.into(), expected_participants: initial.participants.clone(), actor_id: "missing".into(), message_view_scope: MessageViewScope::Full,
        mode: None, event: mutation(id, "missing-scope"),
    }).await, Err(ServiceError::SessionInvalidParams(_))));
    let unchanged = repo.get(id).await.unwrap();
    assert_eq!(unchanged.status, SessionStatus::Running); assert!(unchanged.output.is_none());
    assert_eq!(unchanged.participants.len(), 1); assert_eq!(unchanged.participants[0].bot_uuid, "member");
    assert!(events.get_event(&format!("{id}-missing-remove"), "dev").await.unwrap().is_none());
    assert!(events.get_event(&format!("{id}-missing-scope"), "dev").await.unwrap().is_none());
}

#[tokio::test]
async fn coverage_eventful_session_membership_and_completion_are_activation_fenced() {
    let events = Arc::new(MemoryEventStore::new());
    let repo = MemorySessionRepo::new().with_event_store(events.clone());
    let id = "registry-group:abcdef12";
    let mut create = creation(id); create.params.session_kind = SessionKind::ServiceInvocation;
    let initial = repo.create_with_event(create).await.unwrap();
    let mut human = Participant::bot("human_1", ParticipantRole::Observer);
    human.actor_kind = ActorKind::Human;
    human.mode = Some(ParticipantMode::Present);
    let add = AddSessionParticipantWithEvent { session_id: id.into(), expected_participants: initial.participants,
        participant: human, event: mutation(id, "add") };
    let added = repo.add_participant_with_event(add.clone()).await.unwrap();
    assert_eq!(added.participants.len(), 1);
    assert_eq!(added.participant_join_seq.as_ref().unwrap()["human_1"], 0);
    assert_eq!(repo.add_participant_with_event(add).await.unwrap().participants.len(), 1, "duplicate addition is idempotent");
    let updated = repo.update_participant_message_view_scope_with_event(UpdateSessionParticipantMessageViewScopeWithEvent {
        session_id: id.into(), expected_participants: added.participants.clone(), actor_id: "human_1".into(),
        message_view_scope: MessageViewScope::Participant, mode: Some(ParticipantMode::Absent), event: mutation(id, "scope"),
    }).await.unwrap();
    assert_eq!(updated.participants[0].message_view_scope, MessageViewScope::Participant);
    assert_eq!(updated.participants[0].mode, Some(ParticipantMode::Absent));
    repo.collect(id, "human_1").await.unwrap();
    let removed = repo.remove_participant_with_event(RemoveSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: updated.participants, bot_uuid: "human_1".into(), event: mutation(id, "remove"),
    }).await.unwrap();
    assert!(removed.participants.is_empty());
    assert!(repo.collected_at_map(&[id.into()], "human_1").await.is_empty(), "removal also drops the participant's collection");
    let complete = CompleteSessionWithEvent { session_id: id.into(), expected_activation_count: 1,
        output: Some(serde_json::json!("result")), error: Some("previous warning".into()), event: mutation(id, "complete") };
    let completed = repo.complete_if_running_with_event(complete.clone()).await.unwrap().unwrap();
    assert_eq!(completed.status, SessionStatus::Completed);
    assert!(completed.completed_at.is_some());
    assert!(repo.complete_if_running_with_event(complete.clone()).await.unwrap().is_none());
    assert!(repo.reactivate_if_completed_activation(id, 0, None).await.unwrap().is_none());
    let active = repo.reactivate_if_completed_activation(id, 1, Some(serde_json::json!({"next":true}))).await.unwrap().unwrap();
    assert_eq!(active.activation_count, 2);
    assert_eq!(active.status, SessionStatus::Running);
    assert!(active.output.is_none() && active.error_message.is_none() && active.completed_at.is_none());
    assert_eq!(active.input, Some(serde_json::json!({"next":true})));
    assert!(matches!(repo.complete_if_running_with_event(complete).await, Err(ServiceError::Conflict(_))), "old activation cannot complete a reactivated session");
    for key in ["add", "scope", "remove", "complete"] {
        assert!(events.get_event(&format!("{id}-{key}"), "dev").await.unwrap().is_some());
    }
}

#[tokio::test]
async fn coverage_eventful_session_rejects_stale_membership_and_bad_scope_without_writes() {
    let events = Arc::new(MemoryEventStore::new());
    let repo = MemorySessionRepo::new().with_event_store(events.clone());
    let id = "registry-group:abcdef34";
    let initial = repo.create_with_event(creation(id)).await.unwrap();
    let bot = Participant::bot("bot", ParticipantRole::Observer);
    let stale = vec![bot.clone()];
    assert!(matches!(repo.add_participant_with_event(AddSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: stale.clone(), participant: bot.clone(), event: mutation(id, "stale-add"),
    }).await, Err(ServiceError::Conflict(_))));
    assert!(matches!(repo.remove_participant_with_event(RemoveSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: stale.clone(), bot_uuid: "bot".into(), event: mutation(id, "stale-remove"),
    }).await, Err(ServiceError::Conflict(_))));
    assert!(matches!(repo.update_participant_message_view_scope_with_event(UpdateSessionParticipantMessageViewScopeWithEvent {
        session_id: id.into(), expected_participants: stale, actor_id: "bot".into(),
        message_view_scope: MessageViewScope::Participant, mode: None, event: mutation(id, "stale-scope"),
    }).await, Err(ServiceError::Conflict(_))));
    let mut bad_event = mutation(id, "bad-scope"); bad_event.event.scope.session_id = Some("other".into());
    assert!(matches!(repo.add_participant_with_event(AddSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: initial.participants.clone(), participant: bot.clone(), event: bad_event,
    }).await, Err(ServiceError::InvalidOperation { .. })));
    let added = repo.add_participant_with_event(AddSessionParticipantWithEvent {
        session_id: id.into(), expected_participants: initial.participants, participant: bot,
        event: mutation(id, "valid-add"),
    }).await.unwrap();
    for (scope, mode, key) in [
        (MessageViewScope::Participant, None, "invalid-bot-scope"),
        (MessageViewScope::Full, Some(ParticipantMode::Present), "invalid-bot-mode"),
    ] {
        assert!(matches!(repo.update_participant_message_view_scope_with_event(UpdateSessionParticipantMessageViewScopeWithEvent {
            session_id: id.into(), expected_participants: added.participants.clone(), actor_id: "bot".into(),
            message_view_scope: scope, mode, event: mutation(id, key),
        }).await, Err(ServiceError::SessionInvalidParams(_))));
        assert!(events.get_event(&format!("{id}-{key}"), "dev").await.unwrap().is_none());
    }
    assert_eq!(repo.get(id).await.unwrap().participants[0].message_view_scope, MessageViewScope::Full);
    for key in ["stale-add", "stale-remove", "stale-scope", "bad-scope"] {
        assert!(events.get_event(&format!("{id}-{key}"), "dev").await.unwrap().is_none());
    }
}

#[tokio::test]
async fn eventful_group_creation_reserves_type_even_after_deletion() {
    let events = Arc::new(MemoryEventStore::new());
    let repo = MemorySessionRepo::new().with_event_store(events.clone());
    let command = creation("registry-group:12345678");
    let session = repo.create_with_event(command.clone()).await.unwrap();
    let registration = repo.session_registration(&session.id).await.unwrap().unwrap();
    assert_eq!(registration.session_type, SessionType::Group);
    assert_eq!(registration.current_msg_seq, None);
    assert!(events.get_event(&command.event.event.event_id, "dev").await.unwrap().is_some());
    assert!(repo.create_with_event(command).await.is_err());
    repo.delete(&session.id).await.unwrap();
    assert!(repo.get(&session.id).await.is_none());
    assert!(matches!(repo.ensure_direct_session(&session.id).await,
        Err(ServiceError::Conflict(message)) if message == "session_type_conflict"));
    assert_eq!(repo.session_registration(&session.id).await.unwrap(), Some(registration));
    repo.validate_session_registry().await.unwrap();
}

#[tokio::test]
async fn eventful_group_creation_rejects_direct_identity_without_writes() {
    let events = Arc::new(MemoryEventStore::new());
    let repo = MemorySessionRepo::new().with_event_store(events.clone());
    let command = creation("registry-group:23456789");
    let id = command.params.id.as_ref().unwrap();
    let direct = repo.ensure_direct_session(id).await.unwrap();
    assert!(matches!(repo.create_with_event(command.clone()).await,
        Err(ServiceError::Conflict(message)) if message == "session_type_conflict"));
    assert!(repo.get(id).await.is_none());
    assert!(events.get_event(&command.event.event.event_id, "dev").await.unwrap().is_none());
    assert_eq!(repo.session_registration(id).await.unwrap(), Some(direct));
}

#[tokio::test]
async fn failed_event_write_does_not_leave_a_group_identity_claim() {
    let events = Arc::new(MemoryEventStore::new());
    let repo = MemorySessionRepo::new().with_event_store(events.clone());
    let command = creation("registry-group:34567890");
    let id = command.params.id.as_ref().unwrap();
    // A distinct producer already owns this event ID. Event persistence fails
    // after the Group claim has been validated, before it can be committed.
    let mut existing = command.event.clone();
    existing.event.producer_key = "other-producer-key".into();
    events.append_event(existing).await.unwrap();
    assert!(repo.create_with_event(command.clone()).await.is_err());
    assert!(repo.get(id).await.is_none());
    assert!(repo.session_registration(id).await.unwrap().is_none());
    assert_eq!(repo.ensure_direct_session(id).await.unwrap().session_type, SessionType::DirectA2a);
}
