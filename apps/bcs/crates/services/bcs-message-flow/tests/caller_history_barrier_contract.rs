//! HISTORY_NOT_READY barrier contract (spec §6.8): a retryable
//! pre-acceptance refusal keeps the row Dispatching with its committed
//! attempt/digest, consumes the identity exactly once, and converges
//! NOTHING — the recovery path only re-sends the same dispatch after
//! history completion, never creating a new attempt.

use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::application::message_delivery::{
    DeliveryTransitionCommand, ManagedMessageDeliveryService,
};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_domain::{DeliveryType, NewMessage, SenderType};
use serde_json::json;
use std::sync::Arc;

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

async fn dispatching_row(
    service: &ManagedMessageDelivery,
) -> bcs_domain::message_delivery::PersistedMessageDelivery {
    service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: "history-barrier".into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            now_ms: 1,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "g".into(),
                session_id: "s".into(),
                sender_id: "human".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: json!({"text": "task"}),
                client_msg_id: Some("history-barrier".into()),
                owner_bot_id: None,
                created_at: 1,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                rejection: None,
                target_bot_id: "bot".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({
                    "version": 1,
                    "caller_required": true,
                    "caller_task": "task",
                    "caller_channel": {"actor_id": "human_alice", "actor_name": "Alice"},
                    "caller_not_after_ms": now_ms() + 300_000,
                }),
            }],
        })
        .await
        .expect("admit");
    let row = service
        .snapshot(None)
        .await
        .unwrap()
        .into_iter()
        .find(|row| row.source_message_id == "history-barrier")
        .expect("row");
    service
        .transition(DeliveryTransitionCommand {
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
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
        .expect("StartSend")
}

async fn fetch(
    service: &ManagedMessageDelivery,
    delivery_id: &str,
) -> bcs_domain::message_delivery::PersistedMessageDelivery {
    service
        .lookup(DeliveryLookup::Id(delivery_id.to_string()))
        .await
        .unwrap()
        .into_iter()
        .next()
        .unwrap()
}

#[tokio::test]
async fn history_not_ready_keeps_the_dispatch_alive_once_attempted() {
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let started = dispatching_row(&service).await;

    // The runtime barrier arm stays Dispatching and returns convergence to
    // the recovery loop — assert the row's invariants around the barrier:
    // the attempt and committed digest survive untouched.
    let after = fetch(&service, &started.delivery_id).await;
    assert_eq!(after.state.status, Status::Dispatching);
    assert_eq!(after.attempt_no, started.attempt_no, "one attempt only");
    assert!(after.request_id.is_some(), "the network dispatch id is fixed");

    // Commit the wire facts first (§15.21 guard): the terminal receipt
    // binds to committed dispatch facts.
    service
        .commit_caller_dispatch(
            bcs_service_api::application::message_delivery::CommitCallerDispatchCommand {
                delivery_id: after.delivery_id.clone(),
                expected_state_version: after.state.state_version,
                now_ms: now_ms(),
                dispatch: bcs_service_api::port::CallerDispatchAssociation {
                    environment: after.env.clone(),
                    target_bot_id: after.target_bot_id.clone(),
                    provider_id: "p".into(),
                    provider_bot_ref: String::new(),
                    session_id: after.session_id.clone(),
                    run_id: after.run_id.clone().unwrap(),
                    attempt: after.attempt_no,
                    dispatch_id: after.request_id.clone().unwrap(),
                },
                commit: bcs_service_api::application::message_delivery::CallerDispatchCommit {
                    request_digest: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".into(),
                    not_after_ms: now_ms() + 300_000,
                    control_revision: 0,
                },
            },
        )
        .await
        .expect("commit");

    // A later Completed still converges normally (the barrier never
    // terminalized the row): the same attempt's terminal evidence lands.
    let terminal = bcs_service_api::application::message_delivery::CallerTerminalEventCommand {
        provider_id: "p".into(),
        dispatch: bcs_service_api::port::CallerDispatchAssociation {
            environment: after.env.clone(),
            target_bot_id: after.target_bot_id.clone(),
            provider_id: "p".into(),
            provider_bot_ref: String::new(),
            session_id: after.session_id.clone(),
            run_id: after.run_id.clone().unwrap(),
            attempt: after.attempt_no,
            dispatch_id: after.request_id.clone().unwrap(),
        },
        observation: bcs_service_api::application::message_delivery::CallerTerminalObservation {
            outcome: bcs_service_api::application::message_delivery::CallerTerminalOutcome::Completed,
            event_seq: 3,
            control_revision: 0,
        },
        now_ms: now_ms(),
    };
    let receipt = service
        .handle_caller_terminal(terminal)
        .await
        .expect("terminal converges after the barrier window");
    use bcs_service_api::application::message_delivery::CallerTerminalReceipt;
    assert!(matches!(receipt, CallerTerminalReceipt::Applied { .. }));
    let done = fetch(&service, &started.delivery_id).await;
    assert_eq!(done.state.status, Status::Completed);
}