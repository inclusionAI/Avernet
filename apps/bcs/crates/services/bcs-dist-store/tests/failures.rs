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
async fn admit_reports_a_definite_error_only_before_the_first_write() {
    // New-session writes: 1 spec, 2 task, 3 task index, 4 inbox, 5 active,
    // 6 wake, 7 trim, 8 admit.
    let expected = [
        (1, Err(definite())),
        (2, Err(unknown("adm-1"))),
        (3, Err(unknown("adm-1"))),
        (4, Err(unknown("adm-1"))),
        (5, Err(unknown("adm-1"))),
        (6, Err(unknown("adm-1"))),
        (8, Err(unknown("adm-1"))),
    ];
    for (step, want) in expected {
        let store = Arc::new(Recorder::default());
        store.fail_write(step);
        let got = producer(&store).admit(new_session("adm-1", "hello")).await;
        assert_eq!(got.map(|_| ()), want, "step {step}");
    }
    // The wake trim is best effort.
    let store = Arc::new(Recorder::default());
    store.fail_write(7);
    let got = producer(&store).admit(new_session("adm-1", "hello")).await;
    assert!(matches!(got, Ok(AdmitOutcome::Accepted(_))));
}

#[tokio::test]
async fn admit_without_creation_passes_a_failed_inbox_append_through() {
    // No spec or task to create: the inbox append is the first write.
    let store = Arc::new(Recorder::default());
    store.fail_write(1);
    let request = AdmitRequest {
        input: user_input("adm-2", "reply"),
        create_session: None,
        create_task: None,
    };
    assert_eq!(producer(&store).admit(request).await, Err(definite()));
}

#[tokio::test]
async fn append_stream_reports_a_definite_error_only_before_the_first_write() {
    let event = AgentEvent {
        did: Did("s0042.7f3a9c.b21e.n2.a1".into()),
        holder_epoch: HolderEpoch(1),
        seq: InputSeq(7),
        kind: AgentEventKind::Delta,
        remote_event_id: None,
        payload: Payload::Inline(serde_json::json!({"text": "partial"})),
    };
    let request = AppendAgentEvent {
        sid: Sid(SID.into()),
        tid: Tid(TID.into()),
        event,
    };
    // Writes: 1 inbox, 2 active, 3 wake.
    for step in 1..=3 {
        let store = Arc::new(Recorder::default());
        store.fail_write(step);
        let got = producer(&store).append_stream(request.clone()).await;
        match step {
            1 => assert_eq!(got, Err(definite()), "step {step}"),
            _ => assert!(
                matches!(got, Err(StoreError::OutcomeUnknown { ref operation_id }) if operation_id.starts_with("as-")),
                "step {step}: {got:?}"
            ),
        }
    }
}
