use bcs_domain::DeliveryType;
use bcs_domain::message_delivery::{
    DeliveryFlowKind, DeliveryLaneKey, MessageDeliveryState, MessageDeliveryStatus as Status,
};
use bcs_message_flow::message_delivery::MessageDeliveryCore;
use bcs_service_api::core::message_delivery::{
    DeliveryContextAction as Context, DeliveryLifecycleError, DeliveryLifecycleEvent as Event,
    DeliveryStateChange, MessageDeliveryCoreService,
};

fn send(status: Status) -> MessageDeliveryState {
    MessageDeliveryState {
        kind: DeliveryType::Send,
        status,
        state_version: 1,
        may_have_been_sent: matches!(
            status,
            Status::Dispatching
                | Status::Running
                | Status::Unknown
                | Status::Cancelling
                | Status::CancelUnknown
                | Status::Completed
        ),
    }
}

fn inject(status: Status) -> MessageDeliveryState {
    MessageDeliveryState {
        kind: DeliveryType::Inject,
        status,
        state_version: 1,
        may_have_been_sent: false,
    }
}

fn step(
    state: MessageDeliveryState,
    event: Event,
) -> Result<DeliveryStateChange, DeliveryLifecycleError> {
    let core: &dyn MessageDeliveryCoreService = &MessageDeliveryCore;
    core.transition(state, state.state_version, event)
}

#[test]
fn admission_to_fast_final_releases_once() -> Result<(), DeliveryLifecycleError> {
    let started = step(send(Status::Queued), Event::StartSend)?;
    assert_eq!(started.state.status, Status::Dispatching);
    assert!(started.state.may_have_been_sent);
    assert!(!started.release_active);
    let completed = step(started.state, Event::Completed)?;
    assert_eq!(completed.state.status, Status::Completed);
    assert!(completed.release_active);
    assert_eq!(completed.context_action, Context::Consume);
    for late in [
        Event::Completed,
        Event::TransportUnknown,
        Event::Accepted,
        Event::StartSend,
    ] {
        let duplicate = step(completed.state, late)?;
        assert_eq!(duplicate.state, completed.state);
        assert!(!duplicate.changed);
        assert!(!duplicate.release_active);
        assert!(!duplicate.request_abort);
        assert_eq!(duplicate.context_action, Context::Keep);
    }
    Ok(())
}

#[test]
fn queued_cancellation_never_requests_abort() -> Result<(), DeliveryLifecycleError> {
    let cancelled = step(send(Status::Queued), Event::CancelRequested)?;
    assert_eq!(cancelled.state.status, Status::Cancelled);
    assert!(!cancelled.request_abort);
    assert!(!cancelled.release_active);
    assert_eq!(cancelled.context_action, Context::Release);
    Ok(())
}

#[test]
fn uncertain_states_hold_lane_until_proven_terminal() -> Result<(), DeliveryLifecycleError> {
    let unknown = step(send(Status::Dispatching), Event::TransportUnknown)?;
    assert_eq!(unknown.state.status, Status::Unknown);
    assert!(!unknown.release_active);
    assert_eq!(unknown.context_action, Context::Keep);
    assert!(step(unknown.state, Event::StartSend).is_err());
    assert!(step(unknown.state, Event::DefinitelyNotSent { retry: true }).is_err());
    let cancelling = step(unknown.state, Event::CancelRequested)?;
    assert!(cancelling.request_abort);
    assert!(!cancelling.release_active);
    let uncertain = step(cancelling.state, Event::AbortUnconfirmed)?;
    assert_eq!(uncertain.state.status, Status::CancelUnknown);
    assert!(!uncertain.release_active);
    assert!(uncertain.state.may_have_been_sent);
    let stopped = step(uncertain.state, Event::Aborted)?;
    assert!(stopped.release_active);
    assert_eq!(stopped.context_action, Context::Consume);
    Ok(())
}

#[test]
fn safe_retry_retains_binding_but_unsent_terminal_releases_it() -> Result<(), DeliveryLifecycleError>
{
    let retry = step(
        send(Status::Dispatching),
        Event::DefinitelyNotSent { retry: true },
    )?;
    assert_eq!(retry.state.status, Status::Queued);
    assert!(!retry.state.may_have_been_sent);
    assert!(retry.release_active);
    assert_eq!(retry.context_action, Context::Keep);
    let failed = step(
        send(Status::Dispatching),
        Event::DefinitelyNotSent { retry: false },
    )?;
    assert_eq!(failed.state.status, Status::Failed);
    assert_eq!(failed.context_action, Context::Release);
    // Proof of acceptance cannot later be undone by a transport result.
    assert!(
        step(
            send(Status::Running),
            Event::DefinitelyNotSent { retry: true }
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn cancellation_wins_over_unsent_retry_and_late_ack() -> Result<(), DeliveryLifecycleError> {
    for status in [Status::Cancelling, Status::CancelUnknown] {
        let state = send(status);
        let ack = step(state, Event::Accepted)?;
        assert_eq!(ack.state, state);
        assert!(!ack.changed);
        let repeat = step(state, Event::CancelRequested)?;
        assert!(!repeat.request_abort);
        let unsent = step(state, Event::DefinitelyNotSent { retry: true })?;
        assert_eq!(unsent.state.status, Status::Cancelled);
        assert_eq!(unsent.context_action, Context::Release);
        assert!(unsent.release_active);
    }
    Ok(())
}

#[test]
fn trusted_final_can_win_during_cancellation() -> Result<(), DeliveryLifecycleError> {
    for status in [
        Status::Dispatching,
        Status::Running,
        Status::Unknown,
        Status::Cancelling,
        Status::CancelUnknown,
    ] {
        for (event, expected) in [
            (Event::Completed, Status::Completed),
            (Event::Failed, Status::Failed),
            (Event::Aborted, Status::Cancelled),
        ] {
            let ended = step(send(status), event)?;
            assert_eq!(ended.state.status, expected);
            assert!(ended.release_active);
            assert_eq!(ended.context_action, Context::Consume);
        }
        assert!(step(send(status), Event::QueueExpired).is_err());
    }
    Ok(())
}

#[test]
fn restart_does_not_turn_possible_sends_back_into_queued() -> Result<(), DeliveryLifecycleError> {
    for (before, after) in [
        (Status::Queued, Status::Queued),
        (Status::Dispatching, Status::Unknown),
        (Status::Running, Status::Unknown),
        (Status::Unknown, Status::Unknown),
        (Status::Cancelling, Status::CancelUnknown),
        (Status::CancelUnknown, Status::CancelUnknown),
    ] {
        let recovered = step(send(before), Event::Recover)?;
        assert_eq!(recovered.state.status, after);
        assert!(!recovered.release_active);
        assert!(!recovered.request_abort);
        assert_eq!(recovered.context_action, Context::Keep);
        assert_eq!(recovered.changed, before != after);
    }
    Ok(())
}

#[test]
fn inject_has_no_run_and_requires_explicit_carrier_evidence() -> Result<(), DeliveryLifecycleError>
{
    assert!(step(inject(Status::PendingContext), Event::StartSend).is_err());
    let bound = step(inject(Status::PendingContext), Event::BindContext)?;
    assert_eq!(bound.state.status, Status::Bound);
    assert!(!bound.state.may_have_been_sent);
    assert!(step(bound.state, Event::CancelRequested).is_err());
    assert!(step(bound.state, Event::QueueExpired).is_err());
    let released = step(bound.state, Event::ReleaseContext)?;
    assert_eq!(released.state.status, Status::PendingContext);
    let consumed = step(bound.state, Event::ConsumeContext)?;
    assert_eq!(consumed.state.status, Status::Consumed);
    assert!(!consumed.release_active);
    assert!(!consumed.request_abort);
    assert_eq!(
        step(consumed.state, Event::ReleaseContext)?.state.status,
        Status::Consumed
    );
    assert_eq!(
        step(bound.state, Event::WithdrawBoundContext)?.state.status,
        Status::Cancelled
    );
    Ok(())
}

#[test]
fn unsent_expiry_and_preparation_failure_preserve_context() -> Result<(), DeliveryLifecycleError> {
    for (event, expected) in [
        (Event::QueueExpired, Status::Expired),
        (Event::PreparationFailed, Status::Failed),
    ] {
        let change = step(send(Status::Queued), event)?;
        assert_eq!(change.state.status, expected);
        assert_eq!(change.context_action, Context::Release);
        assert!(!change.request_abort);
    }
    Ok(())
}

#[test]
fn stale_and_corrupt_state_cannot_authorize_a_send() {
    assert!(matches!(
        MessageDeliveryCore.transition(send(Status::Queued), 0, Event::StartSend),
        Err(DeliveryLifecycleError::StaleVersion { .. })
    ));
    for state in [
        MessageDeliveryState {
            may_have_been_sent: true,
            ..send(Status::Queued)
        },
        MessageDeliveryState {
            state_version: 0,
            ..send(Status::Queued)
        },
        inject(Status::Queued),
    ] {
        assert_eq!(
            step(state, Event::StartSend),
            Err(DeliveryLifecycleError::InvalidState)
        );
    }
    assert_eq!(
        step(
            MessageDeliveryState {
                state_version: u64::MAX,
                ..send(Status::Queued)
            },
            Event::StartSend
        ),
        Err(DeliveryLifecycleError::VersionExhausted)
    );
}

#[test]
fn all_persisted_status_names_and_flow_names_are_stable() -> Result<(), serde_json::Error> {
    for (status, name) in [
        (Status::Queued, "queued"),
        (Status::Dispatching, "dispatching"),
        (Status::Running, "running"),
        (Status::Unknown, "unknown"),
        (Status::Cancelling, "cancelling"),
        (Status::CancelUnknown, "cancel_unknown"),
        (Status::Completed, "completed"),
        (Status::Failed, "failed"),
        (Status::Cancelled, "cancelled"),
        (Status::Expired, "expired"),
        (Status::RejectedCapacity, "rejected_capacity"),
        (Status::PendingContext, "pending_context"),
        (Status::Bound, "bound"),
        (Status::Consumed, "consumed"),
        (Status::DiscardedContext, "discarded_context"),
    ] {
        assert_eq!(serde_json::to_value(status)?, name);
        assert_eq!(
            serde_json::from_value::<Status>(serde_json::json!(name))?,
            status
        );
    }
    assert_eq!(
        serde_json::to_value(DeliveryFlowKind::DirectA2a)?,
        "direct_a2a"
    );
    assert_eq!(
        serde_json::to_value(DeliveryFlowKind::StateMachine)?,
        "state_machine"
    );
    assert!(serde_json::from_value::<DeliveryFlowKind>(serde_json::json!("inject")).is_err());
    Ok(())
}

#[test]
fn separate_im_message_sessions_do_not_share_a_lane() {
    let first = DeliveryLaneKey {
        env: "test".into(),
        target_bot_id: "bot".into(),
        session_id: "message-session-1".into(),
    };
    let second = DeliveryLaneKey {
        session_id: "message-session-2".into(),
        ..first.clone()
    };
    assert_ne!(first, second);
    assert_ne!(
        first,
        DeliveryLaneKey {
            env: "other-env".into(),
            ..first.clone()
        }
    );
}
#[test]
fn conformance_managed_delivery_lifecycle() -> Result<(), DeliveryLifecycleError> {
    bcs_test_support::contract::core::message_delivery::message_delivery_core_service_contract_tests(
        &MessageDeliveryCore,
    )
}
