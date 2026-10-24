//! Execution-control contract (spec §7.8 "execution.status / execution.cancel").
//! Credential-free: the only key is the original eight-field association.
//! Real Managed rows on the memory repo; the control service shares the
//! SAME managed instance as scheduler and callbacks.

use std::sync::Arc;

use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_message_flow::CallerExecutionControl;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::message_delivery::{
    CallerCancelReason, CallerDispatchCommit, CommitCallerDispatchCommand, DeliveryTransitionCommand,
    ManagedMessageDeliveryService,
};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::port::CallerDispatchAssociation;
use bcs_service_api::{
    CallerExecutionCancelCommand, CallerExecutionControlService, CallerExecutionQuery,
    CallerExecutionStatus,
};
use serde_json::json;

const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

fn query_for(row: &PersistedMessageDelivery) -> CallerExecutionQuery {
    CallerExecutionQuery {
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

async fn admitted_send(service: &ManagedMessageDelivery, message_id: &str) -> PersistedMessageDelivery {
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
                rejection: None,
                target_bot_id: "bot".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({"version": 1, "caller_required": true}),
            }],
        })
        .await
        .expect("admit row");
    let admitted = service
        .snapshot(None)
        .await
        .unwrap()
        .into_iter()
        .find(|row| row.source_message_id == message_id)
        .expect("row snapshot");
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

async fn committed_row(service: &ManagedMessageDelivery) -> PersistedMessageDelivery {
    let started = admitted_send(service, "caller-ok").await;
    service
        .commit_caller_dispatch(CommitCallerDispatchCommand {
            delivery_id: started.delivery_id.clone(),
            expected_state_version: started.state.state_version,
            now_ms: now_ms(),
            dispatch: CallerDispatchAssociation {
                environment: started.env.clone(),
                target_bot_id: started.target_bot_id.clone(),
                provider_id: String::new(),
                provider_bot_ref: String::new(),
                session_id: started.session_id.clone(),
                run_id: started.run_id.clone().unwrap(),
                attempt: started.attempt_no,
                dispatch_id: started.request_id.clone().unwrap(),
            },
            commit: CallerDispatchCommit {
                request_digest: DIGEST.into(),
                not_after_ms: now_ms() + 300_000,
                control_revision: 0,
            },
        })
        .await
        .expect("commit");
    fetch_row(service, &started.delivery_id).await
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

fn cancel_for(row: &PersistedMessageDelivery, request_id: &str) -> CallerExecutionCancelCommand {
    CallerExecutionCancelCommand {
        query: query_for(row),
        cancel_request_id: request_id.into(),
        reason: CallerCancelReason::UserRequested,
        now_ms: now_ms(),
    }
}

#[tokio::test]
async fn status_reports_accept_unknown_and_not_found_by_association() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let control = CallerExecutionControl::new(service.clone());
    let committed = committed_row(&service).await;

    // Located: accepted, with the frozen control revision in view.
    let view = control.execution_status(query_for(&committed)).await.unwrap();
    assert_eq!(view.status, CallerExecutionStatus::Accepted);
    assert_eq!(view.execution_state, "dispatching");
    assert_eq!(view.control_revision, Some(0));
    assert_eq!(view.cancel_state, None, "no intent yet");

    // A wrong key of any field sees only not-found.
    let mut wrong_attempt = query_for(&committed);
    wrong_attempt.attempt += 1;
    let view = control.execution_status(wrong_attempt).await.unwrap();
    assert_eq!(view.status, CallerExecutionStatus::NotFound);
    assert_eq!(view.execution_state, "not_found");
    let mut wrong_env = query_for(&committed);
    wrong_env.environment = "other-env".into();
    let view = control.execution_status(wrong_env).await.unwrap();
    assert_eq!(view.status, CallerExecutionStatus::NotFound);

    // A dispatch that never existed.
    let mut ghost = query_for(&committed);
    ghost.dispatch_id = "ghost-dispatch".into();
    let view = control.execution_status(ghost).await.unwrap();
    assert_eq!(view.status, CallerExecutionStatus::NotFound);

    // Uncertain attempts surface as unknown, never a fabricated terminal.
    service
        .transition(DeliveryTransitionCommand {
            delivery_id: committed.delivery_id.clone(),
            expected_state_version: fetch_row(&service, &committed.delivery_id).await.state.state_version,
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
    let view = control.execution_status(query_for(&committed)).await.unwrap();
    assert_eq!(view.status, CallerExecutionStatus::Unknown);
    assert_eq!(view.execution_state, "unknown");
}

#[tokio::test]
async fn cancel_lands_the_typed_intent_and_replays_idempotently() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let control = CallerExecutionControl::new(service.clone());
    let committed = committed_row(&service).await;

    let first = control
        .execution_cancel(cancel_for(&committed, "cancel-9"))
        .await
        .expect("cancel lands");
    assert_eq!(first.cancel_state, "requested");
    assert_eq!(first.execution_state, "cancelling");
    assert!(first.last_event_seq.is_some());

    // Status now reflects the durable intent.
    let view = control.execution_status(query_for(&committed)).await.unwrap();
    assert_eq!(view.cancel_state, Some("requested".to_string()));
    assert_eq!(view.execution_state, "cancelling");

    // Replaying the SAME command is a no-write success: same receipt, and
    // the row version did not advance past the first commit.
    let replay = control
        .execution_cancel(cancel_for(&committed, "cancel-9"))
        .await
        .expect("replay succeeds");
    assert_eq!(replay.cancel_state, "requested");
    assert_eq!(replay.execution_state, "cancelling");
    assert_eq!(replay.last_event_seq, first.last_event_seq, "no second write");

    // A different cancel id is a new intent and never overwrites.
    let error = control
        .execution_cancel(cancel_for(&committed, "cancel-10"))
        .await
        .expect_err("different intent conflicts");
    assert!(matches!(error, bcs_service_api::ManagedDeliveryError::Conflict));
    let row = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(
        row.semantic_projection_json["caller_cancel"]["cancel_request_id"], "cancel-9",
        "the committed intent survives"
    );
}

#[tokio::test]
async fn cancel_without_locatable_facts_stays_unconfirmed() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let control = CallerExecutionControl::new(service.clone());

    // A started-but-uncommitted caller row: it has the attempt identity a
    // query can locate, but no frozen dispatch facts to intent against.
    let uncommitted = admitted_send(&service, "uncommitted").await;

    // Nothing behind this dispatch id: not-found is never stop evidence.
    let mut ghost = cancel_for(&uncommitted, "cancel-x");
    ghost.query.dispatch_id = "ghost-dispatch".into();
    let outcome = control.execution_cancel(ghost).await.unwrap();
    assert_eq!(outcome.cancel_state, "unconfirmed");
    assert_eq!(outcome.execution_state, "not_found");
    assert_eq!(outcome.last_event_seq, None);

    // Located but not committed: no frozen facts, no fabricated intent.
    let outcome = control
        .execution_cancel(cancel_for(&uncommitted, "cancel-y"))
        .await
        .unwrap();
    assert_eq!(outcome.cancel_state, "unconfirmed");
    assert_eq!(outcome.execution_state, "dispatching");
    assert_eq!(outcome.last_event_seq, None);
    let row = fetch_row(&service, &uncommitted.delivery_id).await;
    assert!(row.semantic_projection_json.get("caller_cancel").is_none());
}