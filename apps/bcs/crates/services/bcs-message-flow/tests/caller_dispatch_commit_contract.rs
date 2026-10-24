//! Typed caller dispatch commit contract (wire contract §7.5.1
//! "commit_caller_dispatch"). Real Managed admission + real transition/CAS
//! on the memory repo; every rule is locked against the committed StartSend
//! facts. Pure functions verified with the independent v1 digest vector.

use std::sync::Arc;

use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_message_flow::caller_dispatch_pipeline::canonical_request_digest;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::message_delivery::{
    CallerDispatchCommit, CommitCallerDispatchCommand, DeliveryTransitionCommand,
    ManagedMessageDeliveryService,
};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::port::CallerDispatchAssociation;
use serde_json::{Value, json};

const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

fn row_association(row: &PersistedMessageDelivery) -> CallerDispatchAssociation {
    CallerDispatchAssociation {
        environment: row.env.clone(),
        target_bot_id: row.target_bot_id.clone(),
        provider_id: String::new(),
        provider_bot_ref: String::new(),
        session_id: row.session_id.clone(),
        run_id: row.run_id.clone().unwrap(),
        attempt: row.attempt_no,
        dispatch_id: row.request_id.clone().unwrap(),
    }
}

async fn started_row(service: &ManagedMessageDelivery) -> PersistedMessageDelivery {
    service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: "caller-ok".into(),
            flow_kind: DeliveryFlowKind::Group,
            now_ms: 1,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "g".into(),
                session_id: "session-1".into(),
                sender_id: "human".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: json!({"text": "task"}),
                client_msg_id: Some("caller-ok".into()),
                owner_bot_id: None,
                created_at: 1,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                rejection: None,
                target_bot_id: "bot".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({"version": 1, "caller_required": true}),
            }],
        })
        .await
        .expect("admit caller row");
    let admitted = service
        .snapshot(None)
        .await
        .unwrap()
        .into_iter()
        .find(|row| row.source_message_id == "caller-ok")
        .expect("admitted row snapshot");
    let started = service
        .transition(DeliveryTransitionCommand {
            delivery_id: admitted.delivery_id.clone(),
            expected_state_version: 1,
            event: Event::StartSend,
            now_ms: now_ms(),
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: Some(json!({})),
            deadline_at_ms: Some(now_ms() + 600_000),
            caller_dispatch_commit: None,
            caller_cancel: None,
            caller_terminal: None,
        })
        .await
        .expect("StartSend");
    assert_eq!(started.state.status, Status::Dispatching);
    started
}

fn commit_for(row: &PersistedMessageDelivery) -> CommitCallerDispatchCommand {
    CommitCallerDispatchCommand {
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        now_ms: now_ms(),
        dispatch: row_association(row),
        commit: CallerDispatchCommit {
            request_digest: DIGEST.into(),
            not_after_ms: now_ms() + 300_000,
            control_revision: 0,
        },
    }
}

#[tokio::test]
async fn commit_cas_writes_the_projection_then_resubmits_idempotently() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let started = started_row(&service).await;
    let command = commit_for(&started);

    let wrote = service
        .commit_caller_dispatch(command.clone())
        .await
        .expect("first commit writes");
    assert!(wrote);
    let row = service
        .lookup(DeliveryLookup::Id(started.delivery_id.clone()))
        .await
        .unwrap()
        .into_iter()
        .next()
        .unwrap();
    let stored = &row.semantic_projection_json["caller_dispatch"];
    assert_eq!(stored["version"], 1);
    assert_eq!(stored["caller_request_digest"], DIGEST);
    assert_eq!(stored["caller_control_revision"], 0);
    assert_eq!(row.state.status, Status::Dispatching, "commit keeps Dispatching");
    assert!(row.state.state_version > started.state.state_version, "version advanced");
    assert!(row.submitted_at_ms.is_none() && row.accepted_at_ms.is_none(), "no transport stamps");

    // Same payload at the NEW version: idempotent no-write.
    let mut resubmit = command.clone();
    resubmit.expected_state_version = row.state.state_version;
    let wrote_again = service
        .commit_caller_dispatch(resubmit)
        .await
        .expect("identical resubmission is a success");
    assert!(!wrote_again, "identical payload writes nothing");
}

#[tokio::test]
async fn drift_conflicts_at_any_version() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let started = started_row(&service).await;
    service
        .commit_caller_dispatch(commit_for(&started))
        .await
        .expect("first commit");
    let row = service
        .lookup(DeliveryLookup::Id(started.delivery_id.clone()))
        .await
        .unwrap()
        .into_iter()
        .next()
        .unwrap();
    // Same version, different digest: conflict.
    let mut drift = commit_for(&started);
    drift.expected_state_version = row.state.state_version;
    drift.commit.request_digest = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff".into();
    assert!(
        service.commit_caller_dispatch(drift).await.is_err(),
        "digest drift must conflict"
    );
    // Old version: conflict via the ordinary CAS.
    let mut stale = commit_for(&started);
    stale.expected_state_version = started.state.state_version;
    assert!(service.commit_caller_dispatch(stale).await.is_err());
}

#[tokio::test]
async fn non_caller_rows_and_structural_rejects_never_commit() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let started = started_row(&service).await;

    // A row-scoped mismatch in the two association fields.
    let mut wrong_session = commit_for(&started);
    wrong_session.dispatch.session_id = "session-OTHER".into();
    assert!(service.commit_caller_dispatch(wrong_session).await.is_err());

    // Captured digest/revision gates.
    let mut bad_digest = commit_for(&started);
    bad_digest.commit.request_digest = "short".into();
    assert!(service.commit_caller_dispatch(bad_digest).await.is_err());

    let mut bad_revision = commit_for(&started);
    bad_revision.commit.control_revision = 3;
    assert!(service.commit_caller_dispatch(bad_revision).await.is_err());

    let mut past_deadline = commit_for(&started);
    past_deadline.commit.not_after_ms = 0;
    assert!(service.commit_caller_dispatch(past_deadline).await.is_err());

    // Ordinary (non-caller) rows: control keys never appear.
    service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: "plain-row".into(),
            flow_kind: DeliveryFlowKind::Group,
            now_ms: 1,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "g".into(),
                session_id: "session-1".into(),
                sender_id: "human".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: json!({"text": "plain"}),
                client_msg_id: Some("plain-row".into()),
                owner_bot_id: None,
                created_at: 1,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                rejection: None,
                target_bot_id: "bot".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({"version": 1}),
            }],
        })
        .await
        .expect("admit plain");
    let plain = service
        .snapshot(None)
        .await
        .unwrap()
        .into_iter()
        .find(|row| row.source_message_id == "plain-row")
        .expect("plain row");
    let mut ordinary = commit_for(&started);
    ordinary.delivery_id = plain.delivery_id.clone();
    ordinary.dispatch = CallerDispatchAssociation {
        environment: plain.env.clone(),
        target_bot_id: plain.target_bot_id.clone(),
        provider_id: String::new(),
        provider_bot_ref: String::new(),
        session_id: plain.session_id.clone(),
        run_id: "run".into(),
        attempt: 1,
        dispatch_id: "dispatch".into(),
    };
    assert!(service.commit_caller_dispatch(ordinary).await.is_err());
    // The attempt-0 row never got the keys either.
    let check: Value = service
        .lookup(DeliveryLookup::Id(plain.delivery_id.clone()))
        .await
        .unwrap()
        .into_iter()
        .next()
        .unwrap()
        .semantic_projection_json;
    assert!(check.get("caller_dispatch").is_none());
}

#[test]
fn canonical_digest_matches_the_independent_v1_vector() {
    let digest = canonical_request_digest(
        "test",
        "provider-a",
        "runtime-a",
        "bot-a",
        "session-a",
        "run-a",
        "dispatch-a",
        Some("dm-a"),
        "message-a",
        "处理这条消息",
        1,
        0,
        1_800_000_030_000,
    );
    assert_eq!(digest, "sha256:4f09da419e71169e7364f4e4dbd94946f26e1750fd60e86aadcdc054be723ede");
}
