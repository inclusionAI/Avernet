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
