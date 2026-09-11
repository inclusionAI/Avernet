//! Managed-delivery contracts executable against any implementation, with no
//! dependency on concrete schedulers, databases or network adapters.

use std::collections::BTreeSet;

use bcs_domain::DeliveryType;
use bcs_domain::message_delivery::{MessageDeliveryState, MessageDeliveryStatus as Status};
use bcs_service_api::core::message_delivery::{
    DeliveryContextAction, DeliveryLifecycleError, DeliveryLifecycleEvent as Event,
    DeliveryScheduleAction, DeliveryScheduleEntry, DeliveryScheduleSnapshot,
    MessageDeliveryCoreService, MessageDeliverySchedulingCoreService,
};

pub fn message_delivery_core_service_contract_tests<T: MessageDeliveryCoreService + ?Sized>(
    service: &T,
) -> Result<(), DeliveryLifecycleError> {
    let queued = MessageDeliveryState {
        kind: DeliveryType::Send,
        status: Status::Queued,
        state_version: 1,
        may_have_been_sent: false,
    };
    let cancelled = service.transition(queued, 1, Event::CancelRequested)?;
    assert_eq!(cancelled.state.status, Status::Cancelled);
    assert!(!cancelled.request_abort);
    assert_eq!(cancelled.context_action, DeliveryContextAction::Release);

    let started = service.transition(queued, 1, Event::StartSend)?;
    assert_eq!(started.state.status, Status::Dispatching);
    assert!(started.state.may_have_been_sent);
    let unknown = service.transition(started.state, 2, Event::Recover)?;
    assert_eq!(unknown.state.status, Status::Unknown);
    assert!(!unknown.release_active);
    assert!(
        service
            .transition(unknown.state, 3, Event::StartSend)
            .is_err()
    );
    assert!(
        service
            .transition(unknown.state, 3, Event::DefinitelyNotSent { retry: true })
            .is_err()
    );
    let cancelling = service.transition(unknown.state, 3, Event::CancelRequested)?;
    assert!(cancelling.request_abort);
    let late_ack = service.transition(cancelling.state, 4, Event::Accepted)?;
    assert_eq!(late_ack.state.status, Status::Cancelling);
    assert!(!late_ack.changed);
    let finished = service.transition(cancelling.state, 4, Event::Completed)?;
    assert_eq!(finished.state.status, Status::Completed);
    assert!(finished.release_active);
    assert_eq!(finished.context_action, DeliveryContextAction::Consume);
    let duplicate = service.transition(finished.state, 5, Event::Aborted)?;
    assert!(!duplicate.changed);
    assert!(!duplicate.release_active);
    assert_eq!(duplicate.context_action, DeliveryContextAction::Keep);
    assert!(service.transition(queued, 2, Event::StartSend).is_err());
    Ok(())
}

pub fn message_delivery_scheduling_core_service_contract_tests<
    T: MessageDeliverySchedulingCoreService + ?Sized,
>(
    service: &T,
) -> Result<(), DeliveryLifecycleError> {
    let mut rows = vec![DeliveryScheduleEntry {
        delivery_id: "first".into(),
        session_id: "canonical-session".into(),
        source_session_seq: 1,
        state: MessageDeliveryState {
            kind: DeliveryType::Send,
            status: Status::Queued,
            state_version: 1,
            may_have_been_sent: false,
        },
        available_at_ms: 0,
        expire_at_ms: None,
    }];
    let mut second = rows[0].clone();
    second.delivery_id = "second".into();
    second.source_session_seq = 2;
    rows.push(second);
    let preparing = BTreeSet::new();
    let select = |entries: &[DeliveryScheduleEntry]| {
        service.select(DeliveryScheduleSnapshot {
            entries,
            max_running: 1,
            bot_online: true,
            paused: false,
            now_ms: 0,
            monotonic_now_ms: 0,
            next_send_tick_ms: 0,
            after_session_id: None,
            preparing_delivery_ids: &preparing,
        })
    };
    assert_eq!(
        select(&rows)?.action,
        Some(DeliveryScheduleAction::Prepare {
            delivery_id: "first".into(),
            state_version: 1,
        })
    );
    for status in [
        Status::Dispatching,
        Status::Running,
        Status::Unknown,
        Status::Cancelling,
        Status::CancelUnknown,
    ] {
        rows[0].state.status = status;
        rows[0].state.may_have_been_sent = true;
        assert_eq!(select(&rows)?.action, None);
    }
    rows[0].state.status = Status::Completed;
    assert_eq!(
        select(&rows)?.action,
        Some(DeliveryScheduleAction::Prepare {
            delivery_id: "second".into(),
            state_version: 1,
        })
    );
    rows[0].state.status = Status::Queued; // Invalid marker: never dispatch it.
    assert!(select(&rows).is_err());
    Ok(())
}
