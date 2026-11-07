//! Provider terminal-receipt contract (spec "终态仲裁" / caller-terminal
//! entrance). Real Managed rows on the memory repo: the outcome/event_seq/
//! control_revision evidence commits in the same CAS as the ordinary
//! terminal transition, an exact duplicate replays without writing, and a
//! committed terminal is immutable.

use std::sync::Arc;

use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::message_delivery::{
    CallerCancelReason, CallerDispatchCommit, CallerTerminalEventCommand, CallerTerminalObservation,
    CallerTerminalOutcome, CallerTerminalReceipt, CommitCallerDispatchCommand,
    DeliveryTransitionCommand, ManagedMessageDeliveryService,
};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::port::CallerDispatchAssociation;
use serde_json::json;

const DIGEST: &str = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

fn association(row: &PersistedMessageDelivery) -> CallerDispatchAssociation {
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

async fn admit_send(service: &ManagedMessageDelivery, message_id: &str) -> PersistedMessageDelivery {
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

async fn committed_row(service: &ManagedMessageDelivery, message_id: &str) -> PersistedMessageDelivery {
    let started = admit_send(service, message_id).await;
    service
        .commit_caller_dispatch(CommitCallerDispatchCommand {
            delivery_id: started.delivery_id.clone(),
            expected_state_version: started.state.state_version,
            now_ms: now_ms(),
            dispatch: association(&started),
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

fn terminal_for(
    row: &PersistedMessageDelivery,
    outcome: CallerTerminalOutcome,
    event_seq: u64,
) -> CallerTerminalEventCommand {
    CallerTerminalEventCommand {
        provider_id: "provider-1".into(),
        dispatch: association(row),
        observation: CallerTerminalObservation {
            outcome,
            event_seq,
            control_revision: 0,
        },
        now_ms: now_ms(),
    }
}

#[tokio::test]
async fn terminal_evidence_commits_once_then_replays_and_never_mutates() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let committed = committed_row(&service, "caller-ok").await;

    let applied = service
        .handle_caller_terminal(terminal_for(&committed, CallerTerminalOutcome::Completed, 5))
        .await
        .expect("first observation applies");
    let CallerTerminalReceipt::Applied { delivery_id, execution_state, state_version } = &applied else {
        panic!("first observation must apply: {applied:?}")
    };
    assert_eq!(delivery_id, &committed.delivery_id);
    assert_eq!(execution_state, "completed");
    let row = fetch_row(&service, &committed.delivery_id).await;
    assert!(row.state.state_version > state_version - 1);
    let stored = &row.semantic_projection_json["caller_terminal"];
    assert_eq!(stored["version"], 1);
    assert_eq!(stored["outcome"], "completed");
    assert_eq!(stored["event_seq"], 5);
    assert_eq!(stored["caller_control_revision"], 0);

    // Exact duplicate: no write, same receipt facts.
    let duplicate = service
        .handle_caller_terminal(terminal_for(&committed, CallerTerminalOutcome::Completed, 5))
        .await
        .expect("exact duplicate replays");
    match &duplicate {
        CallerTerminalReceipt::Duplicate { state_version, .. } => {
            assert_eq!(state_version, &row.state.state_version);
        }
        other => panic!("expected duplicate receipt, got {other:?}"),
    }

    // Any other observation after the committed terminal conflicts — old
    // seq, same seq with a different outcome, even a higher seq.
    for observation in [
        (CallerTerminalOutcome::Completed, 4u64),
        (CallerTerminalOutcome::Failed, 5),
        (CallerTerminalOutcome::Failed, 6),
    ] {
        let error = service
            .handle_caller_terminal(terminal_for(&committed, observation.0, observation.1))
            .await
            .expect_err("a committed terminal is immutable");
        assert!(matches!(error, bcs_service_api::ManagedDeliveryError::Conflict));
    }
    let after = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(after.semantic_projection_json["caller_terminal"]["outcome"], "completed");
    assert_eq!(after.state.state_version, row.state.state_version, "no write on conflicts");
}

#[tokio::test]
async fn cancel_first_keeps_the_cancelled_state_and_records_the_actual_outcome() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let committed = committed_row(&service, "caller-ok").await;
    let dispatch = &committed.semantic_projection_json["caller_dispatch"];
    service
        .cancel_caller_dispatch(
            bcs_service_api::application::message_delivery::CancelCallerDispatchCommand {
                delivery_id: committed.delivery_id.clone(),
                expected_state_version: committed.state.state_version,
                now_ms: now_ms(),
                dispatch: association(&committed),
                cancel: bcs_service_api::application::message_delivery::CallerCancelIntent {
                    cancel_request_id: "cancel-1".into(),
                    control_revision: dispatch["caller_control_revision"].as_u64().unwrap(),
                    reason: CallerCancelReason::UserRequested,
                    not_after_ms: dispatch["caller_not_after_ms"].as_i64().unwrap(),
                },
            },
        )
        .await
        .expect("cancel intent");
    let cancelling = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(cancelling.state.status, Status::Cancelling);

    // The actual completed outcome lands independently while the logical
    // state stays Cancelled (spec 终态仲裁).
    let applied = service
        .handle_caller_terminal(terminal_for(&committed, CallerTerminalOutcome::Completed, 7))
        .await
        .expect("terminal applies on a cancelling row");
    let CallerTerminalReceipt::Applied { execution_state, .. } = &applied else {
        panic!("expected applied: {applied:?}")
    };
    assert_eq!(execution_state, "cancelled");
    let row = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(row.state.status, Status::Cancelled);
    assert_eq!(row.semantic_projection_json["caller_terminal"]["outcome"], "completed");

    // Late different observations cannot mutate the committed evidence.
    let error = service
        .handle_caller_terminal(terminal_for(&committed, CallerTerminalOutcome::Failed, 8))
        .await
        .expect_err("immutable after commit");
    assert!(matches!(error, bcs_service_api::ManagedDeliveryError::Conflict));
}

#[tokio::test]
async fn association_and_precondition_rejects() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let committed = committed_row(&service, "caller-ok").await;

    // Ghost dispatch ids and mismatched associations are simply absent.
    let mut ghost = terminal_for(&committed, CallerTerminalOutcome::Completed, 3);
    ghost.dispatch.dispatch_id = "ghost".into();
    let error = service.handle_caller_terminal(ghost).await.expect_err("ghost");
    assert!(matches!(error, bcs_service_api::ManagedDeliveryError::NotFound));
    let mut wrong_attempt = terminal_for(&committed, CallerTerminalOutcome::Completed, 3);
    wrong_attempt.dispatch.attempt += 1;
    let error = service
        .handle_caller_terminal(wrong_attempt)
        .await
        .expect_err("old attempt never advances a new task");
    assert!(matches!(error, bcs_service_api::ManagedDeliveryError::NotFound));

    // An uncommitted row has no dispatch facts for a Provider to attest.
    // (Separate service: the committed row above still holds the lane.)
    let plain_service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let uncommitted = admit_send(&plain_service, "uncommitted").await;
    let error = plain_service
        .handle_caller_terminal(terminal_for(&uncommitted, CallerTerminalOutcome::Completed, 3))
        .await
        .expect_err("nothing to attest against");
    assert!(matches!(error, bcs_service_api::ManagedDeliveryError::Conflict));
    let row = fetch_row(&plain_service, &uncommitted.delivery_id).await;
    assert!(row.semantic_projection_json.get("caller_terminal").is_none());
    assert_eq!(row.state.status, Status::Dispatching);
}

#[tokio::test]
async fn uncertain_attempts_converge_via_terminal_evidence() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let committed = committed_row(&service, "caller-ok").await;
    service
        .transition(DeliveryTransitionCommand {
            delivery_id: committed.delivery_id.clone(),
            expected_state_version: committed.state.state_version,
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
    let unknown = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(unknown.state.status, Status::Unknown);

    let applied = service
        .handle_caller_terminal(terminal_for(&unknown, CallerTerminalOutcome::Failed, 2))
        .await
        .expect("terminal resolves the uncertainty");
    let CallerTerminalReceipt::Applied { execution_state, .. } = &applied else {
        panic!("expected applied: {applied:?}")
    };
    assert_eq!(execution_state, "failed");
    let row = fetch_row(&service, &committed.delivery_id).await;
    assert_eq!(row.state.status, Status::Failed);
    assert_eq!(row.semantic_projection_json["caller_terminal"]["outcome"], "failed");
}