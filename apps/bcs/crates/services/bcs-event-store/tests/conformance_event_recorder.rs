#![allow(clippy::expect_used, clippy::unwrap_used)]

mod common;

use std::sync::Arc;

use bcs_db_local::LocalSqliteDbPlugin;
use bcs_event_store::{DbEventStore, EventRecorder, MemoryEventStore};
use bcs_service_api::port::repo::EventRepoPort;
use bcs_service_api::port::{EventRecordErrorCategory, EventRecordResult, EventRecorderPort};
use bcs_test_support::contract::port::event_recorder_port_contract_tests;

#[tokio::test]
async fn enabled_recorder_commits_before_returning_recorded() {
    let repo = Arc::new(MemoryEventStore::new());
    let recorder = EventRecorder::new(repo.clone(), true, common::ENV, 30, 262_144);
    let event = common::append("evt-recorder", "recorder", "group.created").event;

    event_recorder_port_contract_tests(&recorder, event.clone()).await;
    assert!(
        repo.get_event(&event.event_id, common::ENV)
            .await
            .expect("read recorded Event")
            .is_some()
    );
}

#[tokio::test]
async fn disabled_recorder_is_explicit_and_does_not_write() {
    let repo = Arc::new(MemoryEventStore::new());
    let recorder = EventRecorder::new(repo.clone(), false, common::ENV, 30, 262_144);
    let event = common::append("evt-disabled", "disabled", "group.created").event;

    assert_eq!(
        recorder.record(event.clone()).await.unwrap(),
        EventRecordResult::Disabled
    );
    assert!(
        repo.get_event(&event.event_id, common::ENV)
            .await
            .expect("read disabled Event")
            .is_none()
    );
}

#[tokio::test]
async fn recorder_propagates_storage_failure_and_payload_limit() {
    let missing_schema = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    let recorder = EventRecorder::new(
        Arc::new(DbEventStore::sqlite(missing_schema)),
        true,
        common::ENV,
        30,
        262_144,
    );
    let storage_error = recorder
        .record(common::append("evt-storage", "storage", "group.created").event)
        .await
        .expect_err("missing Eventing schema must be visible");
    assert_eq!(storage_error.category, EventRecordErrorCategory::Storage);

    let recorder = EventRecorder::new(Arc::new(MemoryEventStore::new()), true, common::ENV, 30, 1);
    let payload_error = recorder
        .record(common::append("evt-large", "large", "group.created").event)
        .await
        .expect_err("oversized Event rejected");
    assert_eq!(
        payload_error.category,
        EventRecordErrorCategory::PayloadTooLarge
    );
}
