//! The admission provider against the exported store model.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

mod common;

use bcs_dist_model::*;
use bcs_dist_store::codec::decode_inbox_entry;
use bcs_dist_store::coordination::CoordinationStore;
use bcs_dist_store::inbox::{
    InboxProducerConfig, ListenerState, StoreInboxProducer, read_listener,
};
use bcs_dist_store::layout::KeyLayout;
use bcs_service_api::port::repo::distributed::common::{PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::inbox::*;
use bcs_service_api::port::repo::distributed::session::SessionStorePort;
use bcs_store_api::document::DocumentStorePlugin;
use bcs_store_api::json::{JsonMutation, JsonStorePlugin};
use bcs_store_api::stream::StreamStorePlugin;
use bcs_store_api::zset::{ZSetRange, ZSetStorePlugin};
use bcs_store_api::{Expiry, StoreCapability, Version, WriteCondition};
use bcs_test_support::store_model::Model;
use common::*;
use std::num::NonZeroU32;
use std::ops::Bound;
use std::sync::Arc;

const NOW: i64 = 1_794_371_200_123;

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
        Arc::new(FixedClock(NOW)),
        InboxProducerConfig::new(BackendId("tbase".into())),
    )
    .unwrap()
}

fn new_task_request(input_id: &str, text: &str) -> AdmitRequest {
    AdmitRequest {
        scope: scope(),
        input: user_input(input_id, text),
        create_session: Some(spec("grp-research-01")),
        create_task: Some(task()),
    }
}

async fn inbox_len(store: &Recorder) -> usize {
    StreamStorePlugin::range(
        &store.model,
        "bcn:v2:c01:main:s0042:inbox:s0042.7f3a9c",
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(100).unwrap(),
        false,
    )
    .await
    .unwrap()
    .entries
    .len()
}

#[tokio::test]
async fn writes_happen_in_the_fixed_order_without_a_journal_root() {
    let store = Arc::new(Recorder::default());
    let outcome = producer(&store)
        .admit(new_task_request("adm-9c41e7", "hello"))
        .await
        .unwrap();
    assert!(matches!(outcome, AdmitOutcome::Accepted(ref r) if r.tid.0 == TID));
    let calls = store.calls();
    assert!(calls.iter().all(|c| !c.contains(":admission-root")));
    assert!(calls.iter().all(|c| !c.contains(":admission-intent:")));
    assert!(calls.iter().all(|c| !c.contains(":canonical:")));
    let admission = calls
        .iter()
        .position(|c| c.starts_with("document.create") && c.contains(":admission:"))
        .unwrap();
    let slot = calls
        .iter()
        .position(|c| c.starts_with("document.create") && c.contains(":schedule:"))
        .unwrap();
    let task_create = calls
        .iter()
        .position(|c| c.starts_with("document.create") && c.contains(":task:"))
        .unwrap();
    let inbox = calls
        .iter()
        .position(|c| c.starts_with("stream.append") && c.contains(":inbox:"))
        .unwrap();
    assert!(admission < slot && slot < task_create && task_create < inbox);
    assert!(
        calls.len() <= 48,
        "bounded normal-path provider calls: {}",
        calls.len()
    );
    let created = store
        .model
        .get_json("bcn:v2:c01:main:s0042:task:s0042.7f3a9c.b21e")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(created.version, Version(1));
    let active = ZSetStorePlugin::range(
        &store.model,
        "bcn:v2:c01:main:s0042:active",
        &ZSetRange {
            min: Bound::Unbounded,
            max: Bound::Unbounded,
            after: None,
            limit: NonZeroU32::new(10).unwrap(),
        },
    )
    .await
    .unwrap();
    assert_eq!(active.len(), 1);
    assert_eq!(active[0].member, SID);
    assert_eq!(active[0].score, NOW as f64);
    let admission_key = KeyLayout::new("c01", "main")
        .unwrap()
        .admission(&scope(), &InputId("adm-9c41e7".into()))
        .unwrap();
    let admission = store.model.read_document(&admission_key).await.unwrap().unwrap();
    assert!(admission.value.get("request").is_some());
    assert!(admission.value.get("intent").is_none());
    assert!(admission.value.get("sequence").is_none());
}

#[tokio::test]
async fn same_input_twice_is_one_success() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    let first = p
        .admit(new_task_request("adm-9c41e7", "hello"))
        .await
        .unwrap();
    store.clear();
    let second = p
        .admit(new_task_request("adm-9c41e7", "hello"))
        .await
        .unwrap();
    let AdmitOutcome::Accepted(receipt) = first else {
        panic!("first admission not accepted: {first:?}");
    };
    assert_eq!(second, AdmitOutcome::Duplicate(receipt));
    assert!(store.calls().len() <= 6);
    assert!(store.calls().iter().all(|c| c.starts_with("document.read")));
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn same_id_with_other_content_is_a_conflict() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("adm-9c41e7", "hello"))
        .await
        .unwrap();
    let outcome = p
        .admit(new_task_request("adm-9c41e7", "bye"))
        .await
        .unwrap();
    assert_eq!(outcome, AdmitOutcome::ContentConflict);
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn an_existing_task_is_never_replaced() {
    let store = Arc::new(Recorder::default());
    let key = "bcn:v2:c01:main:s0042:task:s0042.7f3a9c.b21e";
    let mut advanced = task();
    advanced.content.status = TaskStatus::Working;
    store
        .model
        .mutate_json(
            key,
            WriteCondition::Newer(Version(14_000_000_002)),
            JsonMutation::Replace(serde_json::to_value(&advanced).unwrap()),
            Expiry::Persistent,
        )
        .await
        .unwrap();
    producer(&store)
        .admit(new_task_request("adm-9c41e7", "hello"))
        .await
        .unwrap();
    let stored = store.model.get_json(key).await.unwrap().unwrap();
    assert_eq!(stored.version, Version(14_000_000_002));
    assert_eq!(stored.value["content"]["status"], "working");
}

#[tokio::test]
async fn a_spec_of_another_group_is_a_conflict() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("adm-1", "hello")).await.unwrap();
    let key = "bcn:v2:c01:main:s0042:spec:s0042.7f3a9c";
    let original = store.model.read_document(key).await.unwrap().unwrap();
    let mut other = new_task_request("adm-2", "again");
    other.create_session = Some(spec("grp-other"));
    assert_eq!(p.admit(other).await.unwrap(), AdmitOutcome::ContentConflict);
    assert_eq!(
        store.model.read_document(key).await.unwrap().unwrap(),
        original
    );
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn repeated_creation_preserves_the_first_spec_and_task() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("adm-1", "hello")).await.unwrap();
    let spec_key = "bcn:v2:c01:main:s0042:spec:s0042.7f3a9c";
    let task_key = "bcn:v2:c01:main:s0042:task:s0042.7f3a9c.b21e";
    let original_spec = store.model.read_document(spec_key).await.unwrap().unwrap();
    let original_task = store.model.read_document(task_key).await.unwrap().unwrap();
    let mut retry = new_task_request("adm-2", "again");
    // Rebuilding a request from newer configuration cannot change a frozen
    // session, nor reset the task under the same identity.
    retry.create_session.as_mut().unwrap().mode_envelope.revision = "4".into();
    assert!(matches!(
        p.admit(retry).await.unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    assert_eq!(
        store.model.read_document(spec_key).await.unwrap().unwrap(),
        original_spec
    );
    assert_eq!(
        store.model.read_document(task_key).await.unwrap().unwrap(),
        original_task
    );
    assert_eq!(original_spec.version, Version(1));
    assert_eq!(original_task.version, Version(1));
}

#[tokio::test]
async fn admission_does_not_require_full_atomic_json() {
    let store = Arc::new(Recorder::without_capability(StoreCapability::AtomicJson));
    assert!(matches!(
        producer(&store)
            .admit(new_task_request("adm-1", "hello"))
            .await
            .unwrap(),
        AdmitOutcome::Accepted(_)
    ));
    assert!(store.calls().iter().all(|call| !call.starts_with("json.")));
}

#[tokio::test]
async fn worker_reads_the_admitted_spec_through_the_same_document_api() {
    let store = Arc::new(Recorder::without_capability(StoreCapability::AtomicJson));
    let request = new_task_request("adm-1", "hello");
    let expected = request.create_session.clone();
    producer(&store).admit(request).await.unwrap();
    let sessions = CoordinationStore::new(
        store.clone(),
        Arc::new(Model::default()),
        store.clone(),
        KeyLayout::new("c01", "main").unwrap(),
    )
    .unwrap();
    store.clear();
    assert_eq!(sessions.get_spec(&Sid(SID.into())).await.unwrap(), expected);
    assert_eq!(
        store.calls(),
        vec!["document.read bcn:v2:c01:main:s0042:spec:s0042.7f3a9c"]
    );
}

#[test]
fn admission_requires_document_cas() {
    let store = Arc::new(Recorder::without_capability(StoreCapability::DocumentCas));
    let built = StoreInboxProducer::new(
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
        Arc::new(FixedClock(NOW)),
        InboxProducerConfig::new(BackendId("tbase".into())),
    );
    assert!(
        matches!(built, Err(StoreError::NotApplied { ref code }) if code.contains("DocumentCas"))
    );
    assert!(store.calls().is_empty());
}

#[tokio::test]
async fn listener_reads_durable_schedule_state() {
    let store = Arc::new(Recorder::default());
    let layout = KeyLayout::new("c01", "main").unwrap();
    let read = read_listener(store.as_ref(), &layout, &Sid(SID.into())).await;
    assert_eq!(read, Ok(ListenerState::NotListening));
    // Admission still succeeds and writes exactly one wake.
    producer(&store)
        .admit(new_task_request("adm-9c41e7", "hello"))
        .await
        .unwrap();
    let wakes = store
        .calls()
        .iter()
        .filter(|c| c.starts_with("stream.append") && c.ends_with(":wake"))
        .count();
    assert_eq!(wakes, 1);
}

#[tokio::test]
async fn ids_that_disagree_are_rejected_before_any_read() {
    let store = Arc::new(Recorder::default());
    let mut request = new_task_request("adm-9c41e7", "hello");
    request.input.tid = Tid("s0099.other.b21e".into());
    request.create_task = None;
    assert!(matches!(
        producer(&store).admit(request).await,
        Err(StoreError::Invalid { .. })
    ));
    assert!(store.calls().is_empty());
}

#[tokio::test]
async fn a_missing_capability_stops_construction() {
    let mut model = Model::default();
    model.ordinary_disabled = true;
    let store = Arc::new(Recorder::with_model(model));
    let built = StoreInboxProducer::new(
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
        Arc::new(FixedClock(NOW)),
        InboxProducerConfig::new(BackendId("tbase".into())),
    );
    assert!(matches!(built, Err(StoreError::NotApplied { ref code }) if code.contains("Kv")));
}

#[tokio::test]
async fn an_intermediate_agent_event_is_appended_and_activates_the_session() {
    let store = Arc::new(Recorder::default());
    let event = AgentEvent {
        did: Did("s0042.7f3a9c.b21e.n2.a1".into()),
        holder_epoch: HolderEpoch(1),
        seq: InputSeq(42),
        kind: AgentEventKind::Delta,
        remote_event_id: None,
        payload: Payload::Inline(serde_json::json!({"text": "partial"})),
    };
    let request = AppendAgentEvent {
        sid: Sid(SID.into()),
        tid: Tid(TID.into()),
        event: event.clone(),
    };
    let p = producer(&store);
    p.admit(new_task_request("initial", "hello")).await.unwrap();
    store.clear();
    let pos = p.append_stream(request.clone()).await.unwrap();
    assert_eq!(pos.sid.0, SID);
    assert_eq!(pos.token.backend.0, "tbase");
    assert_eq!(pos.token.generation.0, "1");
    assert!(
        store
            .calls()
            .iter()
            .any(|c| c.starts_with("document.replace") && c.contains(":schedule:"))
    );
    let page = StreamStorePlugin::range(
        &store.model,
        "bcn:v2:c01:main:s0042:inbox:s0042.7f3a9c",
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(10).unwrap(),
        false,
    )
    .await
    .unwrap();
    let decoded = decode_inbox_entry(&Sid(SID.into()), &page.entries[1].fields).unwrap();
    assert_eq!(decoded.body, InputBody::Agent { event });
    // A retry of the same event keeps its identity.
    p.append_stream(request).await.unwrap();
    let page = StreamStorePlugin::range(
        &store.model,
        "bcn:v2:c01:main:s0042:inbox:s0042.7f3a9c",
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(10).unwrap(),
        false,
    )
    .await
    .unwrap();
    let again = decode_inbox_entry(&Sid(SID.into()), &page.entries[2].fields).unwrap();
    assert_eq!(again.input_id, decoded.input_id);
    assert_eq!(again.business_digest, decoded.business_digest);
}

#[tokio::test]
async fn a_bad_task_key_is_rejected_before_the_spec_is_written() {
    let store = Arc::new(Recorder::default());
    let mut request = new_task_request("adm-9c41e7", "hello");
    let bad = Tid(format!("{TID}{{x}}"));
    request.input.tid = bad.clone();
    request.create_task.as_mut().unwrap().tid = bad;
    assert!(matches!(
        producer(&store).admit(request).await,
        Err(StoreError::Invalid { .. })
    ));
    assert!(store.calls().is_empty(), "nothing read or written");
}

#[tokio::test]
async fn task_and_spec_of_different_scopes_are_rejected_before_any_write() {
    let store = Arc::new(Recorder::default());
    let mut request = new_task_request("adm-9c41e7", "hello");
    request.create_task.as_mut().unwrap().scope.principal = PrincipalId("someone-else".into());
    assert!(matches!(
        producer(&store).admit(request).await,
        Err(StoreError::Invalid { ref code }) if code == "task_scope_mismatch"
    ));
    assert!(store.calls().is_empty());
}

#[tokio::test]
async fn a_new_task_must_match_the_frozen_session_scope() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("adm-1", "hello")).await.unwrap();

    let mut second = task();
    second.tid = Tid(format!("{SID}.c3d4"));
    second.scope.principal = PrincipalId("someone-else".into());
    let mut input = user_input("adm-2", "second task");
    input.tid = second.tid.clone();
    store.clear();
    let outcome = p
        .admit(AdmitRequest {
            scope: second.scope.clone(),
            input,
            create_session: None,
            create_task: Some(second),
        })
        .await
        .unwrap();
    assert_eq!(outcome, AdmitOutcome::ContentConflict);
    assert!(
        store
            .calls()
            .iter()
            .all(|c| c.starts_with("kv.get") || c.starts_with("document.read")),
        "no writes: {:?}",
        store.calls()
    );
}

#[tokio::test]
async fn a_new_task_needs_an_existing_session() {
    let store = Arc::new(Recorder::default());
    let mut request = new_task_request("adm-1", "hello");
    request.create_session = None;
    assert_eq!(
        producer(&store).admit(request).await,
        Err(StoreError::NotApplied {
            code: "session_not_found".into()
        })
    );
}

#[tokio::test]
async fn an_existing_task_of_another_scope_is_a_conflict() {
    let store = Arc::new(Recorder::default());
    let mut foreign = task();
    foreign.scope.principal = PrincipalId("someone-else".into());
    store
        .model
        .mutate_json(
            "bcn:v2:c01:main:s0042:task:s0042.7f3a9c.b21e",
            WriteCondition::Newer(Version(1)),
            JsonMutation::Replace(serde_json::to_value(&foreign).unwrap()),
            Expiry::Persistent,
        )
        .await
        .unwrap();
    let mut request = new_task_request("adm-1", "hello");
    request.create_session = None;
    // The session exists with the request's scope.
    store
        .model
        .mutate_json(
            "bcn:v2:c01:main:s0042:spec:s0042.7f3a9c",
            WriteCondition::Newer(Version(1)),
            JsonMutation::Replace(serde_json::to_value(spec("grp-research-01")).unwrap()),
            Expiry::Persistent,
        )
        .await
        .unwrap();
    assert_eq!(
        producer(&store).admit(request).await.unwrap(),
        AdmitOutcome::ContentConflict
    );
    assert_eq!(inbox_len(&store).await, 0);
}

#[tokio::test]
async fn documents_of_an_unsupported_version_are_never_written() {
    for (which, format) in [("spec", 3), ("task", 2)] {
        let store = Arc::new(Recorder::default());
        let mut request = new_task_request("adm-9c41e7", "hello");
        match which {
            "spec" => request.create_session.as_mut().unwrap().schema_version = format,
            _ => request.create_task.as_mut().unwrap().schema_version = format,
        }
        assert_eq!(
            producer(&store).admit(request).await,
            Err(StoreError::UnsupportedFormat { format }),
            "{which}"
        );
        assert!(store.calls().is_empty(), "{which}: nothing read or written");
    }
}

#[tokio::test]
async fn takeover_enumerates_active_sessions_without_an_admission_journal() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("enumerate-active", "hello"))
        .await
        .unwrap();
    let page = p
        .enumerate_pending(
            ShardId(42),
            PageRequest {
                after: None,
                limit: NonZeroU32::new(16).unwrap(),
            },
        )
        .await
        .unwrap();
    assert_eq!(page.items, vec![Sid(SID.into())]);
    assert!(store.calls().iter().all(|c| !c.contains(":admission-root")));
}
