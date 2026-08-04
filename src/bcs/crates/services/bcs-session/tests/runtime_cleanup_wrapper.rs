use std::sync::Arc;

use bcs_service_api::{
    CreateOrReactivateCommand, NewSessionParams, Participant, ParticipantMode, ParticipantRole,
    SessionManagementService, SessionStatus,
};
use bcs_session::{NoopSessionManagementService, SessionManagementWithRuntimeCleanup};
use bcs_test_support::NoopCollaborationRuntimeService;

fn service() -> SessionManagementWithRuntimeCleanup {
    SessionManagementWithRuntimeCleanup::new(
        Arc::new(NoopSessionManagementService),
        Arc::new(NoopCollaborationRuntimeService),
    )
}

#[tokio::test]
async fn cleanup_wrapper_forwards_non_delete_operations() {
    let service = service();

    assert!(
        service
            .create_or_reactivate(CreateOrReactivateCommand {
                group_id: "group-1".to_string(),
                session_id: None,
                params: NewSessionParams::default(),
            })
            .await
            .is_err()
    );
    assert!(service.get("session-1").await.unwrap().is_none());
    assert!(
        !service
            .belongs_to_group("session-1", "group-1")
            .await
            .unwrap()
    );
    assert!(
        service
            .list_by_group("group-1", Some(SessionStatus::Running), 0, 20, None, None)
            .await
            .unwrap()
            .is_empty()
    );
    assert_eq!(service.count_running_service("group-1").await.unwrap(), 0);
    assert!(
        service
            .list_running_service(0, 20)
            .await
            .unwrap()
            .is_empty()
    );
    service
        .update_callback_status("session-1", "completed")
        .await
        .unwrap();
    assert!(
        service
            .complete_if_running("session-1", None, None)
            .await
            .unwrap()
            .is_none()
    );

    let participant = Participant::bot("bot-1", ParticipantRole::Worker);
    assert!(
        service
            .add_participant("session-1", participant)
            .await
            .is_err()
    );
    assert!(
        service
            .remove_participant("session-1", "bot-1")
            .await
            .is_err()
    );
    assert!(
        service
            .update_participant_mode("session-1", "bot-1", ParticipantMode::Muted)
            .await
            .is_err()
    );
    assert!(
        service
            .update_title("session-1", Some("Renamed".to_string()))
            .await
            .is_err()
    );
    assert!(
        service
            .list_group_ids_by_session_participant("bot-1")
            .await
            .unwrap()
            .is_empty()
    );
    assert!(service.collect("session-1", "bot-1").await.is_err());
    assert!(service.uncollect("session-1", "bot-1").await.is_err());
    assert!(
        service
            .list_collected_by_group("group-1", "bot-1", None, None, 0, 20)
            .await
            .unwrap()
            .is_empty()
    );
    assert!(
        service
            .collected_at_map(&["session-1"], "bot-1")
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn delete_reports_run_cancellation_failure() {
    let error = service()
        .delete("session-1")
        .await
        .expect_err("delete must stop when active runs cannot be cancelled");

    assert!(
        error
            .to_string()
            .contains("Failed to cancel active state-machine runs for deleted session 'session-1'")
    );
}
