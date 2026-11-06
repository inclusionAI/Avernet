//! Typed caller cancel intent contract (spec §"取消意图与回执"). Real
//! Managed admission + Started/committed dispatch rows on the memory repo;
//! the intent must become durable in the SAME CAS that moves the row to
//! Cancelling, replay the SAME intent must be a no-write success, and a
//! different intent must never overwrite a committed one.

use std::sync::Arc;

use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::message_delivery::{
    CallerCancelReason, CallerDispatchCommit, CommitCallerDispatchCommand, DeliveryTransitionCommand,
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

async fn committed_row(service: &ManagedMessageDelivery) -> PersistedMessageDelivery {
    let started = started_row(service).await;
    let commit = CommitCallerDispatchCommand {
        delivery_id: started.delivery_id.clone(),
        expected_state_version: started.state.state_version,
        now_ms: now_ms(),
        dispatch: row_association(&started),
        commit: CallerDispatchCommit {
            request_digest: DIGEST.into(),
            not_after_ms: now_ms() + 300_000,
            control_revision: 0,
        },
    };
    service
        .commit_caller_dispatch(commit)
        .await
        .expect("commit");
    fetch_row(service, &started.delivery_id).await
}

async fn started_row(service: &ManagedMessageDelivery) -> PersistedMessageDelivery {
    started_row_named(service, "caller-ok").await
}

async fn started_row_named(
    service: &ManagedMessageDelivery,
    message_id: &str,
) -> PersistedMessageDelivery {
    service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: message_id.into(),
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
                client_msg_id: Some(message_id.into()),
                owner_bot_id: None,
                created_at: 1,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                run_id: None,
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
        .find(|row| row.source_message_id == message_id)
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

async fn admit_plain_send(
    service: &ManagedMessageDelivery,
    message_id: &str,
) -> PersistedMessageDelivery {
    service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: message_id.into(),
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
                client_msg_id: Some(message_id.into()),
                owner_bot_id: None,
                created_at: 1,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                run_id: None,
                rejection: None,
                target_bot_id: "bot".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({"version": 1, "caller_required": true}),
            }],
        })
        .await
        .expect("admit probe row");
    service
        .snapshot(None)
        .await
        .unwrap()
        .into_iter()
        .find(|row| row.source_message_id == message_id)
        .expect("probe row snapshot")
}

async fn fetch_row(
    service: &ManagedMessageDelivery,
    delivery_id: &str,
) -> PersistedMessageDelivery {
    service
        .lookup(DeliveryLookup::Id(delivery_id.to_string()))
        .await
        .unwrap()
        .into_iter()
        .next()
        .expect("row present")
}

fn cancel_for(row: &PersistedMessageDelivery, request_id: &str) -> CancelCallerDispatchCommand {
    let dispatch = &row.semantic_projection_json["caller_dispatch"];
    CancelCallerDispatchCommand {
        delivery_id: row.delivery_id.clone(),
        expected_state_version: row.state.state_version,
        now_ms: now_ms(),
        dispatch: row_association(row),
        cancel: bcs_service_api::application::message_delivery::CallerCancelIntent {
            cancel_request_id: request_id.into(),
            control_revision: dispatch["caller_control_revision"].as_u64().unwrap(),
            reason: CallerCancelReason::UserRequested,
            not_after_ms: dispatch["caller_not_after_ms"].as_i64().unwrap(),
        },
    }
}

use bcs_service_api::application::message_delivery::CancelCallerDispatchCommand;

#[tokio::test]
async fn cancel_intent_is_durable_in_the_cas_that_moves_the_row_and_replays_idempotently() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let committed = committed_row(&service).await;

    let wrote = service
        .cancel_caller_dispatch(cancel_for(&committed, "cancel-1"))
        .await
        .expect("typed cancel commits the intent");
    assert!(wrote);
    let row = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(row.state.status, Status::Cancelling, "intent and state in one CAS");
    assert!(row.state.state_version > committed.state.state_version, "version advanced");
    let stored = &row.semantic_projection_json["caller_cancel"];
    assert_eq!(stored["version"], 1);
    assert_eq!(stored["cancel_request_id"], "cancel-1");
    assert_eq!(stored["caller_control_revision"], 0, "revision frozen at intent commit");
    assert_eq!(stored["reason"], "user_requested");
    assert_eq!(stored["cancel_state"], "requested");
    assert_eq!(
        stored["not_after_ms"],
        row.semantic_projection_json["caller_dispatch"]["caller_not_after_ms"],
        "not_after mirrors the committed dispatch fact"
    );
    assert!(row.cancel_requested_at_ms.is_some());
    // The lane stays held: requested/fenced are not release evidence. A
    // second queued Send on the same session cannot StartSend while the
    // Cancelling row is still an active lane holder.
    let second = admit_plain_send(&service, "lane-probe").await;
    let error = service
        .transition(DeliveryTransitionCommand {
            delivery_id: second.delivery_id.clone(),
            expected_state_version: second.state.state_version,
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
        .expect_err("the cancelling row still holds the lane");
    assert!(matches!(error, bcs_service_api::ManagedDeliveryError::Conflict));

    // Replaying the SAME intent at the committed row's current version is a
    // no-write success; a stale version conflicts via the ordinary CAS.
    let mut replay = cancel_for(&row, "cancel-1");
    replay.expected_state_version = row.state.state_version;
    let wrote_again = service
        .cancel_caller_dispatch(replay)
        .await
        .expect("same-intent replay succeeds");
    assert!(!wrote_again, "same intent writes nothing");
    let after = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(after.state.state_version, row.state.state_version, "no write");
}

#[tokio::test]
async fn a_committed_intent_is_never_overwritten_by_a_different_one() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let committed = committed_row(&service).await;
    service
        .cancel_caller_dispatch(cancel_for(&committed, "cancel-1"))
        .await
        .expect("first intent");
    let row = fetch_row(&service, &committed.delivery_id).await;

    let mut other = cancel_for(&row, "cancel-2");
    other.expected_state_version = row.state.state_version;
    let error = service
        .cancel_caller_dispatch(other)
        .await
        .expect_err("a second, different intent must not overwrite");
    assert!(matches!(error, bcs_service_api::ManagedDeliveryError::Conflict));
    let after = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(
        after.semantic_projection_json["caller_cancel"]["cancel_request_id"], "cancel-1",
        "the original intent survives"
    );
}

#[tokio::test]
async fn cancel_running_and_unknown_rows_fences_each_state() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let committed = committed_row(&service).await;

    // ACK moves the row to Running; cancel still lands the intent.
    let running = service
        .transition(DeliveryTransitionCommand {
            delivery_id: committed.delivery_id.clone(),
            expected_state_version: committed.state.state_version,
            event: Event::Accepted,
            now_ms: now_ms(),
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: None,
            deadline_at_ms: None,
            caller_dispatch_commit: None,
            caller_cancel: None,
            caller_terminal: None,
        })
        .await
        .expect("Accepted");
    assert_eq!(running.state.status, Status::Running);
    service
        .cancel_caller_dispatch(cancel_for(&running, "cancel-run"))
        .await
        .expect("cancel from Running");
    let row = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(row.state.status, Status::Cancelling);

    // TransportUnknown slides the row to CancelUnknown; the lane is still
    // held and the durable intent keeps its identity.
    let unknown = service
        .transition(DeliveryTransitionCommand {
            delivery_id: committed.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            event: Event::TransportUnknown,
            now_ms: now_ms(),
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: None,
            deadline_at_ms: None,
            caller_dispatch_commit: None,
            caller_cancel: None,
            caller_terminal: None,
        })
        .await
        .expect("TransportUnknown");
    assert_eq!(unknown.state.status, Status::CancelUnknown);
    // CancelUnknown stays an active lane holder: confirmation, not the
    // intent itself, is the release evidence.
    assert_eq!(
        unknown.semantic_projection_json["caller_cancel"]["cancel_request_id"],
        "cancel-run"
    );
}

#[tokio::test]
async fn structural_and_association_rejects_never_leave_an_intent_behind() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let committed = committed_row(&service).await;

    // Frozen-fact drift: revision and deadline must mirror the commit.
    let mut drifted = cancel_for(&committed, "cancel-1");
    drifted.cancel.control_revision = 1;
    assert!(service.cancel_caller_dispatch(drifted).await.is_err());
    let mut drifted = cancel_for(&committed, "cancel-1");
    drifted.cancel.not_after_ms += 1;
    assert!(service.cancel_caller_dispatch(drifted).await.is_err());

    // Empty request id, association mismatch, wrong row.
    let mut no_id = cancel_for(&committed, "  ");
    no_id.expected_state_version = committed.state.state_version;
    assert!(service.cancel_caller_dispatch(no_id).await.is_err());
    let mut mismatch = cancel_for(&committed, "cancel-1");
    mismatch.dispatch.dispatch_id = "other-dispatch".into();
    assert!(service.cancel_caller_dispatch(mismatch).await.is_err());

    // Non-caller rows refuse the typed cancel entirely.
    let ordinary_row = admit_plain_send(&service, "ordinary").await;
    let mut stranger = cancel_for(&committed, "cancel-1");
    stranger.delivery_id = ordinary_row.delivery_id.clone();
    assert!(service.cancel_caller_dispatch(stranger).await.is_err());

    // Cancel before commit: there are no frozen dispatch facts to extend,
    // so the typed path refuses rather than synthesizing them. The row does
    // not even need a StartSend — an admitted-but-undispatched caller row
    // has no attempt identity for the intent to freeze.
    let admitted = admit_plain_send(&service, "late-starter").await;
    assert!(
        service
            .cancel_caller_dispatch(CancelCallerDispatchCommand {
                delivery_id: admitted.delivery_id.clone(),
                expected_state_version: admitted.state.state_version,
                now_ms: now_ms(),
                dispatch: CallerDispatchAssociation {
                    environment: admitted.env.clone(),
                    target_bot_id: admitted.target_bot_id.clone(),
                    provider_id: String::new(),
                    provider_bot_ref: String::new(),
                    session_id: admitted.session_id.clone(),
                    run_id: "run-early".into(),
                    attempt: 0,
                    dispatch_id: "dispatch-early".into(),
                },
                cancel: bcs_service_api::application::message_delivery::CallerCancelIntent {
                    cancel_request_id: "early".into(),
                    control_revision: 0,
                    reason: CallerCancelReason::ScopeRevoked,
                    not_after_ms: now_ms() + 300_000,
                },
            })
            .await
            .is_err(),
        "cancel-before-commit has no frozen facts and must not fabricate them"
    );
    let untouched = fetch_row(&service, &admitted.delivery_id).await;
    assert!(untouched.semantic_projection_json.get("caller_cancel").is_none());
}

#[tokio::test]
async fn reason_wire_strings_stay_a_closed_set() {
    assert_eq!(CallerCancelReason::UserRequested.as_str(), "user_requested");
    assert_eq!(CallerCancelReason::ScopeRevoked.as_str(), "scope_revoked");
    assert_eq!(CallerCancelReason::from_wire("user_requested"), Some(CallerCancelReason::UserRequested));
    assert_eq!(CallerCancelReason::from_wire("scope_revoked"), Some(CallerCancelReason::ScopeRevoked));
    assert_eq!(CallerCancelReason::from_wire("operator_whim"), None);
    let projection = json!({});
    let _: Value = projection;
}