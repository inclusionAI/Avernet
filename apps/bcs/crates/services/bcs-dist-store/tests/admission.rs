//! The admission provider against the exported store model.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

mod common;

use bcs_dist_model::*;
use bcs_dist_store::codec::decode_inbox_entry;
use bcs_dist_store::coordination::CoordinationStore;
use bcs_dist_store::error::NEED_CONTRACT;
use bcs_dist_store::inbox::{InboxProducerConfig, StoreInboxProducer, read_listener};
use bcs_dist_store::layout::KeyLayout;
use bcs_service_api::port::repo::distributed::common::StoreError;
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
        input: user_input(input_id, text),
        create_session: Some(spec("grp-research-01")),
        create_task: Some(task()),
    }
}

async fn inbox_len(store: &Recorder) -> usize {
    StreamStorePlugin::range(
        &store.model,
        "bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c",
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
async fn writes_happen_in_the_fixed_order() {
    let store = Arc::new(Recorder::default());
    let outcome = producer(&store)
        .admit(new_task_request("adm-9c41e7", "hello"))
        .await
        .unwrap();
    assert!(matches!(outcome, AdmitOutcome::Accepted(ref r) if r.tid.0 == TID));
    assert_eq!(
        store.calls(),
        vec![
            "kv.get bcn:v1:c01:main:s0042:admit:adm-9c41e7",
            "document.create bcn:v1:c01:main:s0042:spec:s0042.7f3a9c",
            "document.create bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e",
            "zset.add bcn:v1:c01:main:s0042:tasks:s0042.7f3a9c",
            "stream.append bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c",
            "zset.add bcn:v1:c01:main:s0042:active",
            "stream.append bcn:v1:c01:main:s0042:wake",
            "stream.trim bcn:v1:c01:main:s0042:wake",
            "kv.put_if_absent bcn:v1:c01:main:s0042:admit:adm-9c41e7",
        ]
    );
    let created = store
        .model
        .get_json("bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(created.version, Version(1));
    let active = ZSetStorePlugin::range(
        &store.model,
        "bcn:v1:c01:main:s0042:active",
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
}

#[tokio::test]
async fn same_input_twice_is_one_success() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    let first = p.admit(new_task_request("adm-9c41e7", "hello")).await.unwrap();
    store.clear();
    let second = p.admit(new_task_request("adm-9c41e7", "hello")).await.unwrap();
    let AdmitOutcome::Accepted(receipt) = first else {
        panic!("first admission not accepted: {first:?}");
    };
    assert_eq!(second, AdmitOutcome::Accepted(receipt));
    assert_eq!(store.calls().len(), 1, "an admitted input is answered from admit");
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn same_id_with_other_content_is_a_conflict() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("adm-9c41e7", "hello")).await.unwrap();
    let outcome = p.admit(new_task_request("adm-9c41e7", "bye")).await.unwrap();
    assert_eq!(outcome, AdmitOutcome::ContentConflict);
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn a_lost_response_at_any_step_is_unknown_and_a_retry_completes() {
    // Writes of a new-session admission: spec, task, task index, inbox, active,
    // wake, trim, admit.
    for step in 1..=8 {
        let store = Arc::new(Recorder::default());
        let p = producer(&store);
        store.lose_write(step);
        let first = p.admit(new_task_request("adm-9c41e7", "hello")).await;
        if step == 7 {
            // The wake trim is best effort; its loss doesn't fail admission.
            assert!(matches!(first, Ok(AdmitOutcome::Accepted(_))), "step {step}");
            continue;
        }
        assert_eq!(
            first,
            Err(StoreError::OutcomeUnknown {
                operation_id: "adm-9c41e7".into()
            }),
            "step {step}"
        );
        let retry = p.admit(new_task_request("adm-9c41e7", "hello")).await.unwrap();
        assert!(
            matches!(retry, AdmitOutcome::Accepted(ref r) if r.tid.0 == TID),
            "step {step}: {retry:?}"
        );
        assert!(inbox_len(&store).await >= 1, "step {step}");
    }
}

#[tokio::test]
async fn an_existing_task_is_never_replaced() {
    let store = Arc::new(Recorder::default());
    let key = "bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e";
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
    let key = "bcn:v1:c01:main:s0042:spec:s0042.7f3a9c";
    let original = store.model.read_document(key).await.unwrap().unwrap();
    let mut other = new_task_request("adm-2", "again");
    other.create_session = Some(spec("grp-other"));
    assert_eq!(p.admit(other).await.unwrap(), AdmitOutcome::ContentConflict);
    assert_eq!(store.model.read_document(key).await.unwrap().unwrap(), original);
    assert_eq!(inbox_len(&store).await, 1);
}

#[tokio::test]
async fn repeated_creation_preserves_the_first_spec_and_task() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("adm-1", "hello")).await.unwrap();
    let spec_key = "bcn:v1:c01:main:s0042:spec:s0042.7f3a9c";
    let task_key = "bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e";
    let original_spec = store.model.read_document(spec_key).await.unwrap().unwrap();
    let original_task = store.model.read_document(task_key).await.unwrap().unwrap();
    let mut retry = new_task_request("adm-2", "again");
    // Rebuilding a request from newer configuration cannot change a frozen
    // session, nor reset the task under the same identity.
    retry.create_session.as_mut().unwrap().workflow.revision = "4".into();
    retry.create_task.as_mut().unwrap().content.status = TaskStatus::Working;
    assert!(matches!(p.admit(retry).await.unwrap(), AdmitOutcome::Accepted(_)));
    assert_eq!(store.model.read_document(spec_key).await.unwrap().unwrap(), original_spec);
    assert_eq!(store.model.read_document(task_key).await.unwrap().unwrap(), original_task);
    assert_eq!(original_spec.version, Version(1));
    assert_eq!(original_task.version, Version(1));
}

#[tokio::test]
async fn admission_does_not_require_full_atomic_json() {
    let store = Arc::new(Recorder::without_capability(StoreCapability::AtomicJson));
    assert!(matches!(
        producer(&store).admit(new_task_request("adm-1", "hello")).await.unwrap(),
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
        store.clone(), Arc::new(Model::default()), store.clone(),
        KeyLayout::new("c01", "main").unwrap(),
    ).unwrap();
    store.clear();
    assert_eq!(sessions.get_spec(&Sid(SID.into())).await.unwrap(), expected);
    assert_eq!(store.calls(), vec!["document.read bcn:v1:c01:main:s0042:spec:s0042.7f3a9c"]);
}

#[test]
fn admission_requires_document_cas() {
    let store = Arc::new(Recorder::without_capability(StoreCapability::DocumentCas));
    let built = StoreInboxProducer::new(
        store.clone(), store.clone(), store.clone(), store.clone(),
        KeyLayout::new("c01", "main").unwrap(),
        Arc::new(FixedClock(NOW)),
        InboxProducerConfig::new(BackendId("tbase".into())),
    );
    assert!(matches!(built, Err(StoreError::NotApplied { ref code }) if code.contains("DocumentCas")));
    assert!(store.calls().is_empty());
}

#[tokio::test]
async fn the_listener_read_is_a_delayed_contract() {
    let store = Arc::new(Recorder::default());
    let layout = KeyLayout::new("c01", "main").unwrap();
    let read = read_listener(store.as_ref(), &layout, &Sid(SID.into())).await;
    assert_eq!(
        read,
        Err(StoreError::NotApplied {
            code: NEED_CONTRACT.into()
        })
    );
    // Admission still succeeds and writes exactly one wake.
    producer(&store)
        .admit(new_task_request("adm-9c41e7", "hello"))
        .await
        .unwrap();
    let wakes = store.calls().iter().filter(|c| c.starts_with("stream.append") && c.ends_with(":wake")).count();
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
    let pos = p.append_stream(request.clone()).await.unwrap();
    assert_eq!(pos.sid.0, SID);
    assert_eq!(pos.token.backend.0, "tbase");
    assert_eq!(pos.token.generation.0, "1");
    assert_eq!(
        store.calls()[..3],
        [
            "stream.append bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c".to_owned(),
            "zset.add bcn:v1:c01:main:s0042:active".to_owned(),
            "stream.append bcn:v1:c01:main:s0042:wake".to_owned(),
        ]
    );
    let page = StreamStorePlugin::range(
        &store.model,
        "bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c",
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(10).unwrap(),
        false,
    )
    .await
    .unwrap();
    let decoded = decode_inbox_entry(&Sid(SID.into()), &page.entries[0].fields).unwrap();
    assert_eq!(decoded.body, InputBody::Agent { event });
    // A retry of the same event keeps its identity.
    p.append_stream(request).await.unwrap();
    let page = StreamStorePlugin::range(
        &store.model,
        "bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c",
        Bound::Unbounded,
        Bound::Unbounded,
        NonZeroU32::new(10).unwrap(),
        false,
    )
    .await
    .unwrap();
    let again = decode_inbox_entry(&Sid(SID.into()), &page.entries[1].fields).unwrap();
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
            input,
            create_session: None,
            create_task: Some(second),
        })
        .await
        .unwrap();
    assert_eq!(outcome, AdmitOutcome::ContentConflict);
    assert!(
        store.calls().iter().all(|c| c.starts_with("kv.get") || c.starts_with("document.read")),
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
            "bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e",
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
            "bcn:v1:c01:main:s0042:spec:s0042.7f3a9c",
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
    for which in ["spec", "task"] {
        let store = Arc::new(Recorder::default());
        let mut request = new_task_request("adm-9c41e7", "hello");
        match which {
            "spec" => request.create_session.as_mut().unwrap().schema_version = 2,
            _ => request.create_task.as_mut().unwrap().schema_version = 2,
        }
        assert_eq!(
            producer(&store).admit(request).await,
            Err(StoreError::UnsupportedFormat { format: 2 }),
            "{which}"
        );
        assert!(store.calls().is_empty(), "{which}: nothing read or written");
    }
}

async fn task_index(store: &Recorder) -> Vec<bcs_store_api::zset::ZSetEntry> {
    ZSetStorePlugin::range(
        &store.model,
        "bcn:v1:c01:main:s0042:tasks:s0042.7f3a9c",
        &ZSetRange {
            min: Bound::Unbounded,
            max: Bound::Unbounded,
            after: None,
            limit: NonZeroU32::new(10).unwrap(),
        },
    )
    .await
    .unwrap()
}

#[tokio::test]
async fn new_task_is_listed_in_the_session_index_once() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("adm-9c41e7", "hello")).await.unwrap();
    // A second input for the same task (other id) lists nothing new and keeps the score.
    let mut later = new_task_request("adm-51d0b2", "again");
    later.create_task.as_mut().unwrap().created_at = TimestampMillis(1_794_371_299_000);
    p.admit(later).await.unwrap();
    let index = task_index(&store).await;
    assert_eq!(index.len(), 1);
    assert_eq!(index[0].member, TID);
    assert_eq!(index[0].score, 1_794_371_200_000_f64);
}

#[tokio::test]
async fn retry_after_a_failed_index_write_repairs_the_index() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    // Writes: 1 spec, 2 task, 3 task index.
    store.fail_write(3);
    let first = p.admit(new_task_request("adm-9c41e7", "hello")).await;
    assert_eq!(
        first,
        Err(StoreError::OutcomeUnknown {
            operation_id: "adm-9c41e7".into()
        })
    );
    assert!(task_index(&store).await.is_empty());
    let retry = p.admit(new_task_request("adm-9c41e7", "hello")).await.unwrap();
    assert!(matches!(retry, AdmitOutcome::Accepted(_)));
    assert_eq!(task_index(&store).await.len(), 1);
}

#[tokio::test]
async fn an_input_without_a_new_task_does_not_touch_the_index() {
    let store = Arc::new(Recorder::default());
    let p = producer(&store);
    p.admit(new_task_request("adm-9c41e7", "hello")).await.unwrap();
    store.clear();
    let reply = AdmitRequest {
        input: user_input("adm-77d2", "more"),
        create_session: None,
        create_task: None,
    };
    p.admit(reply).await.unwrap();
    assert!(!store.calls().iter().any(|c| c.contains(":tasks:")));
}

/// Spec and task as a first attempt left them, without the index entry.
async fn preload_spec_and_task(store: &Recorder, created_at: i64) {
    let mut first = task();
    first.created_at = TimestampMillis(created_at);
    for (key, doc) in [
        ("bcn:v1:c01:main:s0042:spec:s0042.7f3a9c", bcs_dist_store::codec::encode_json(&spec("grp-research-01")).unwrap()),
        ("bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e", bcs_dist_store::codec::encode_json(&first).unwrap()),
    ] {
        store
            .model
            .mutate_json(key, WriteCondition::Newer(Version(1)), JsonMutation::Replace(doc), Expiry::Persistent)
            .await
            .unwrap();
    }
}

#[tokio::test]
async fn a_repairing_index_insert_counts_as_a_write_of_the_call() {
    let store = Arc::new(Recorder::default());
    preload_spec_and_task(&store, 1_794_371_200_000).await;
    // Writes of this retry: 1 spec (exists), 2 task (exists), 3 index (repairs), 4 inbox.
    store.fail_write(4);
    let outcome = producer(&store).admit(new_task_request("adm-9c41e7", "hello")).await;
    assert_eq!(
        outcome,
        Err(StoreError::OutcomeUnknown {
            operation_id: "adm-9c41e7".into()
        }),
        "something was written, so the failure is not definite"
    );
    assert_eq!(task_index(&store).await.len(), 1);
}

#[tokio::test]
async fn a_repaired_index_entry_is_scored_with_the_stored_creation_time() {
    let store = Arc::new(Recorder::default());
    preload_spec_and_task(&store, 1_794_371_200_000).await;
    // The retry computes a later creation time; the task keeps its first one.
    let mut retry = new_task_request("adm-9c41e7", "hello");
    retry.create_task.as_mut().unwrap().created_at = TimestampMillis(1_794_371_299_000);
    producer(&store).admit(retry).await.unwrap();
    let index = task_index(&store).await;
    assert_eq!(index.len(), 1);
    assert_eq!(index[0].score, 1_794_371_200_000_f64);
}

#[tokio::test]
async fn a_conflicting_task_is_not_listed() {
    let store = Arc::new(Recorder::default());
    // A task of another scope already holds the id.
    let mut foreign = task();
    foreign.scope.principal = PrincipalId("someone-else".into());
    store
        .model
        .mutate_json(
            "bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e",
            WriteCondition::Newer(Version(1)),
            JsonMutation::Replace(bcs_dist_store::codec::encode_json(&foreign).unwrap()),
            Expiry::Persistent,
        )
        .await
        .unwrap();
    preload_only_spec(&store).await;
    let outcome = producer(&store).admit(new_task_request("adm-9c41e7", "hello")).await.unwrap();
    assert_eq!(outcome, AdmitOutcome::ContentConflict);
    assert!(task_index(&store).await.is_empty());
}

async fn preload_only_spec(store: &Recorder) {
    store
        .model
        .mutate_json(
            "bcn:v1:c01:main:s0042:spec:s0042.7f3a9c",
            WriteCondition::Newer(Version(1)),
            JsonMutation::Replace(bcs_dist_store::codec::encode_json(&spec("grp-research-01")).unwrap()),
            Expiry::Persistent,
        )
        .await
        .unwrap();
}

#[tokio::test]
async fn a_new_task_in_an_existing_session_writes_in_the_fixed_order() {
    let store = Arc::new(Recorder::default());
    preload_only_spec(&store).await;
    let request = AdmitRequest {
        input: user_input("adm-9c41e7", "hello"),
        create_session: None,
        create_task: Some(task()),
    };
    producer(&store).admit(request).await.unwrap();
    assert_eq!(
        store.calls(),
        vec![
            "kv.get bcn:v1:c01:main:s0042:admit:adm-9c41e7",
            "json.get bcn:v1:c01:main:s0042:spec:s0042.7f3a9c",
            "json.mutate bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e",
            "zset.add bcn:v1:c01:main:s0042:tasks:s0042.7f3a9c",
            "stream.append bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c",
            "zset.add bcn:v1:c01:main:s0042:active",
            "stream.append bcn:v1:c01:main:s0042:wake",
            "stream.trim bcn:v1:c01:main:s0042:wake",
            "kv.put_if_absent bcn:v1:c01:main:s0042:admit:adm-9c41e7",
        ]
    );
}
