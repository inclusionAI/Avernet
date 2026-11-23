//! Definite store failures: reported as is before the first write, and as an
//! unknown outcome once something was written. Plus the merged port harness.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

mod common;

use bcs_dist_model::*;
use bcs_dist_store::inbox::{InboxProducerConfig, StoreInboxProducer};
use bcs_dist_store::layout::KeyLayout;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::inbox::*;
use bcs_test_support::contract::distributed::inbox::inbox_producer_port_contract_tests;
use common::*;
use std::sync::Arc;

fn producer(store: &Arc<Recorder>) -> StoreInboxProducer {
    StoreInboxProducer::new(
        Arc::new(
            bcs_dist_store::budget::SharedScanBudget::new(
                bcs_dist_store::budget::ScanBudgetConfig::default(),
            )
            .unwrap(),
        ),
        Arc::new(bcs_dist_projection::OutputProjectionService),
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        KeyLayout::new("c01", "main").unwrap(),
        Arc::new(FixedClock(1_794_371_200_123)),
        InboxProducerConfig::new(BackendId("tbase".into())),
    )
    .unwrap()
}

fn new_session(input_id: &str, text: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(input_id, text),
        create_session: Some(spec("grp-research-01")),
        create_task: Some(task()),
    }
}

fn unknown(op: &str) -> StoreError {
    StoreError::OutcomeUnknown {
        operation_id: op.into(),
    }
}

fn definite() -> StoreError {
    StoreError::NotApplied {
        code: "store_not_applied".into(),
    }
}

#[tokio::test]
async fn the_merged_port_harness_passes() {
    let store = Arc::new(Recorder::default());
    inbox_producer_port_contract_tests(
        &producer(&store),
        new_session("adm-9c41e7", "hello"),
        new_session("adm-9c41e7", "bye"),
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn admit_reports_a_definite_error_only_before_admission_creation() {
    let baseline = Arc::new(Recorder::default());
    producer(&baseline)
        .admit(new_session("count", "hello"))
        .await
        .unwrap();
    let writes: Vec<_> = baseline
        .calls()
        .into_iter()
        .filter(|c| !c.starts_with("document.read"))
        .collect();
    for (index, call) in writes.iter().enumerate() {
        let store = Arc::new(Recorder::default());
        store.fail_write(index + 1);
        let result = producer(&store).admit(new_session("failed", "hello")).await;
        if index == 0 {
            assert_eq!(result, Err(definite()));
        } else if call.ends_with(":active") || call.ends_with(":wake") {
            assert!(
                matches!(result, Ok(AdmitOutcome::Accepted(_))),
                "{call}: {result:?}"
            );
        } else {
            assert_eq!(result, Err(unknown("failed")), "{call}");
        }
    }
}

#[tokio::test]
async fn existing_task_control_is_persisted_before_delivery() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_session("start", "hello")).await.unwrap();
    store.fail_write(1);
    let mut request = new_session("control", "reply");
    request.create_session = None;
    request.create_task = None;
    request.input.body = InputBody::Cancel { reason: None };
    assert_eq!(p.admit(request).await, Err(definite()));
}

#[tokio::test]
async fn intermediate_activation_hint_loss_does_not_fail_durable_append() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_session("start", "hello")).await.unwrap();
    let request = AppendAgentEvent {
        sid: Sid(SID.into()),
        tid: Tid(TID.into()),
        event: AgentEvent {
            did: Did(format!("{TID}.d")),
            holder_epoch: HolderEpoch(1),
            seq: InputSeq(1),
            kind: AgentEventKind::Delta,
            remote_event_id: None,
            payload: Payload::Inline(serde_json::json!("delta")),
        },
    };
    store.clear();
    store.fail_write(3); // Inbox append, schedule activation, then active hint.
    assert!(p.append_stream(request).await.is_ok());
    use bcs_service_api::port::repo::distributed::admission::AdmissionMaintenancePort;
    use bcs_store_api::document::DocumentStorePlugin;
    let schedule_key = KeyLayout::new("c01", "main")
        .unwrap()
        .schedule(&Sid(SID.into()))
        .unwrap();
    let schedule = store.model.read_document(&schedule_key).await.unwrap().unwrap();
    assert!(schedule.value["activation"].as_u64().unwrap() > 0);
    let maintenance = p.discover(ShardId(42)).await.unwrap();
    assert!(maintenance.round_complete);
    assert_eq!(maintenance.activated, 0);
}
