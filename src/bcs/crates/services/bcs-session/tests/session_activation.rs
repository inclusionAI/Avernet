use std::sync::{Arc, Mutex};
use bcs_event_store::{EventRecorder, MemoryEventStore};
use bcs_group_store::MemoryGroupRepo;
use bcs_service_api::{NewSessionParams, SessionKind, SessionManagementService, SessionStatus};
use bcs_service_api::port::{EventRecordError, EventRecordFactoryPort, NewEvent};
use bcs_service_api::port::repo::{AppendEventRecord, EventRepoPort, SessionRepoPort};
use bcs_session::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::NoopCollaborationRuntimeService;
use serde_json::json;

struct RecordingFactory {
    inner: EventRecorder,
    ids: Mutex<Vec<String>>,
}
impl EventRecordFactoryPort for RecordingFactory {
    fn prepare(&self, event: NewEvent) -> Result<Option<AppendEventRecord>, EventRecordError> {
        self.ids.lock().unwrap().push(event.event_id.clone());
        self.inner.prepare(event)
    }
}

#[tokio::test]
async fn activation_completion_preserves_events_and_wrapper_delegation() {
    for eventful in [false, true] {
        let events = Arc::new(MemoryEventStore::new());
        let repo = Arc::new(MemorySessionRepo::new().with_event_store(events.clone()));
        let factory = Arc::new(RecordingFactory {
            inner: EventRecorder::new(events.clone(), eventful, "test", 7, 65536),
            ids: Mutex::new(Vec::new()),
        });
        let inner = Arc::new(SessionManagementServiceImpl::new(repo.clone(), Arc::new(MemoryGroupRepo::new()))
            .with_event_record_factory(factory.clone()));
        let service = SessionManagementWithRuntimeCleanup::new(inner, Arc::new(NoopCollaborationRuntimeService));
        let session = repo.create("group-1", NewSessionParams { session_kind: SessionKind::ServiceInvocation, ..Default::default() }).await.unwrap();
        assert_eq!(service.list_running_service_after(None, 1).await.unwrap()[0].id, session.id);
        assert!(service.complete_running_service_activation(&session.id, session.activation_count + 1, None, None).await.unwrap().is_none());
        assert!(factory.ids.lock().unwrap().is_empty());
        let (one, two) = tokio::join!(
            service.complete_running_service_activation(&session.id, session.activation_count, Some(json!("result")), None),
            service.complete_running_service_activation(&session.id, session.activation_count, Some(json!("result")), None),
        );
        let winners = [one.unwrap(), two.unwrap()].into_iter().flatten().count();
        assert_eq!(winners, 1);
        let ids = factory.ids.lock().unwrap().clone();
        let mut stored = 0;
        for id in ids {
            if let Some(record) = events.get_event(&id, "test").await.unwrap() {
                assert_eq!(record.envelope.event_type, "session.completed");
                stored += 1;
            }
        }
        assert_eq!(stored, usize::from(eventful));
        repo.update_callback_status(&session.id, "not_applicable").await.unwrap();
        let next = repo.reactivate(&session.id, None).await.unwrap();
        assert!(service.complete_running_service_activation(&session.id, session.activation_count, None, Some("old failure".into())).await.unwrap().is_none());
        let current = service.get(&session.id).await.unwrap().unwrap();
        assert_eq!(current.status, SessionStatus::Running);
        assert_eq!(current.activation_count, next.activation_count);
        assert!(current.error_message.is_none());
    }
}

#[tokio::test]
async fn activation_completion_does_not_fall_back_when_event_preparation_fails() {
    let events = Arc::new(MemoryEventStore::new());
    let repo = Arc::new(MemorySessionRepo::new().with_event_store(events.clone()));
    let service = SessionManagementServiceImpl::new(repo.clone(), Arc::new(MemoryGroupRepo::new()))
        .with_event_record_factory(Arc::new(EventRecorder::new(events, true, "test", 7, 1)));
    let session = repo.create("group-1", NewSessionParams { session_kind: SessionKind::ServiceInvocation, ..Default::default() }).await.unwrap();
    assert!(service.complete_running_service_activation(&session.id, session.activation_count, None, None).await.is_err());
    assert_eq!(repo.try_get(&session.id).await.unwrap().unwrap().status, SessionStatus::Running);
}
