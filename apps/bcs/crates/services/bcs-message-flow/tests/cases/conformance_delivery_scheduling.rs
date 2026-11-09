use std::collections::BTreeSet;

use bcs_domain::DeliveryType;
use bcs_domain::message_delivery::{
    DeliveryWaitReason, MessageDeliveryState, MessageDeliveryStatus,
};
use bcs_message_flow::message_delivery::MessageDeliveryCore;
use bcs_service_api::core::message_delivery::{
    DeliveryLifecycleError, DeliveryScheduleAction, DeliveryScheduleEntry,
    DeliveryScheduleSnapshot, MessageDeliverySchedulingCoreService,
};

fn row(id: &str, session: &str, seq: u64, status: MessageDeliveryStatus) -> DeliveryScheduleEntry {
    DeliveryScheduleEntry {
        delivery_id: id.into(),
        session_id: session.into(),
        source_session_seq: seq,
        state: MessageDeliveryState {
            kind: DeliveryType::Send,
            status,
            state_version: 1,
            may_have_been_sent: status != MessageDeliveryStatus::Queued,
        },
        available_at_ms: 100,
        expire_at_ms: None,
    }
}

fn snapshot<'a>(
    rows: &'a [DeliveryScheduleEntry],
    preparing: &'a BTreeSet<String>,
) -> DeliveryScheduleSnapshot<'a> {
    DeliveryScheduleSnapshot {
        entries: rows,
        max_running: 2,
        bot_online: true,
        paused: false,
        now_ms: 100,
        monotonic_now_ms: 100,
        next_send_tick_ms: 100,
        after_session_id: None,
        preparing_delivery_ids: preparing,
    }
}

fn prepare(id: &str) -> Option<DeliveryScheduleAction> {
    Some(DeliveryScheduleAction::Prepare {
        delivery_id: id.into(),
        state_version: 1,
    })
}

#[test]
fn canonical_session_fifo_and_round_robin() -> Result<(), DeliveryLifecycleError> {
    let rows = [
        row("a2", "session-a", 2, MessageDeliveryStatus::Queued),
        row("b1", "session-b", 1, MessageDeliveryStatus::Queued),
        row("a1", "session-a", 1, MessageDeliveryStatus::Queued),
    ];
    let preparing = BTreeSet::new();
    assert_eq!(
        MessageDeliveryCore
            .select(snapshot(&rows, &preparing))?
            .action,
        prepare("a1")
    );
    let mut view = snapshot(&rows, &preparing);
    view.after_session_id = Some("session-a");
    assert_eq!(MessageDeliveryCore.select(view)?.action, prepare("b1"));
    let mut view = snapshot(&rows, &preparing);
    view.after_session_id = Some("session-z");
    assert_eq!(MessageDeliveryCore.select(view)?.action, prepare("a1"));
    Ok(())
}

#[test]
fn retry_backoff_and_preparation_do_not_allow_lane_overtaking() -> Result<(), DeliveryLifecycleError>
{
    let mut rows = [
        row("a1", "a", 1, MessageDeliveryStatus::Queued),
        row("a2", "a", 2, MessageDeliveryStatus::Queued),
        row("b1", "b", 1, MessageDeliveryStatus::Queued),
    ];
    rows[0].available_at_ms = 101;
    let preparing = BTreeSet::new();
    let decision = MessageDeliveryCore.select(snapshot(&rows, &preparing))?;
    assert_eq!(decision.action, prepare("b1"));
    assert_eq!(
        decision.waiting,
        vec![("a1".into(), DeliveryWaitReason::RetryBackoff)]
    );
    rows[0].available_at_ms = 100;
    let preparing = BTreeSet::from(["a1".into()]);
    assert_eq!(
        MessageDeliveryCore
            .select(snapshot(&rows, &preparing))?
            .action,
        prepare("b1")
    );
    Ok(())
}

#[test]
fn all_uncertain_and_cancelling_states_occupy_bot_capacity() -> Result<(), DeliveryLifecycleError> {
    for status in [
        MessageDeliveryStatus::Dispatching,
        MessageDeliveryStatus::Running,
        MessageDeliveryStatus::Unknown,
        MessageDeliveryStatus::Cancelling,
        MessageDeliveryStatus::CancelUnknown,
    ] {
        let rows = [
            row("a1", "a", 1, status),
            row("a2", "a", 2, MessageDeliveryStatus::Queued),
            row("b1", "b", 1, MessageDeliveryStatus::Queued),
        ];
        let preparing = BTreeSet::new();
        assert_eq!(
            MessageDeliveryCore
                .select(snapshot(&rows, &preparing))?
                .action,
            prepare("b1")
        );
        let mut view = snapshot(&rows, &preparing);
        view.max_running = 1;
        let decision = MessageDeliveryCore.select(view)?;
        assert_eq!(decision.action, None);
        assert_eq!(
            decision.waiting,
            vec![("b1".into(), DeliveryWaitReason::BotCapacity)]
        );
    }
    Ok(())
}

#[test]
fn rate_limit_uses_monotonic_time_not_wall_clock() -> Result<(), DeliveryLifecycleError> {
    let rows = [row("a1", "a", 1, MessageDeliveryStatus::Queued)];
    let preparing = BTreeSet::new();
    let mut view = snapshot(&rows, &preparing);
    view.now_ms = i64::MAX;
    view.next_send_tick_ms = 101;
    let decision = MessageDeliveryCore.select(view)?;
    assert_eq!(decision.action, None);
    assert_eq!(
        decision.waiting,
        vec![("a1".into(), DeliveryWaitReason::RateLimited)]
    );
    Ok(())
}

#[test]
fn expire_queued_during_pause_offline_and_full_capacity() -> Result<(), DeliveryLifecycleError> {
    let mut rows = [
        row("active", "a", 1, MessageDeliveryStatus::Running),
        row("expired", "b", 1, MessageDeliveryStatus::Queued),
    ];
    rows[0].expire_at_ms = Some(1);
    rows[1].expire_at_ms = Some(100);
    let preparing = BTreeSet::new();
    let mut view = snapshot(&rows, &preparing);
    view.max_running = 1;
    view.paused = true;
    view.bot_online = false;
    assert_eq!(
        MessageDeliveryCore.select(view)?.action,
        Some(DeliveryScheduleAction::Expire {
            delivery_id: "expired".into(),
            state_version: 1,
        })
    );
    Ok(())
}

#[test]
fn offline_pause_and_recheck_block_prepared_work() -> Result<(), DeliveryLifecycleError> {
    let rows = [row("a1", "a", 1, MessageDeliveryStatus::Queued)];
    let preparing = BTreeSet::new();
    for (paused, online, expected) in [
        (true, true, DeliveryWaitReason::Paused),
        (false, false, DeliveryWaitReason::BotOffline),
    ] {
        let mut view = snapshot(&rows, &preparing);
        view.paused = paused;
        view.bot_online = online;
        let decision = MessageDeliveryCore.select(view)?;
        assert_eq!(decision.action, None);
        assert_eq!(decision.waiting, vec![("a1".into(), expected)]);
    }
    let mut cancelled = rows;
    cancelled[0].state.status = MessageDeliveryStatus::Cancelled;
    assert_eq!(
        MessageDeliveryCore
            .select(snapshot(&cancelled, &preparing))?
            .action,
        None
    );
    Ok(())
}

#[test]
fn invalid_recovery_snapshots_fail_closed() {
    let preparing = BTreeSet::new();
    for rows in [
        vec![
            row("a1", "a", 1, MessageDeliveryStatus::Running),
            row("a2", "a", 2, MessageDeliveryStatus::Unknown),
        ],
        vec![
            row("same", "a", 1, MessageDeliveryStatus::Queued),
            row("same", "b", 1, MessageDeliveryStatus::Queued),
        ],
        vec![
            row("a1", "a", 1, MessageDeliveryStatus::Queued),
            row("a2", "a", 1, MessageDeliveryStatus::Queued),
        ],
        vec![row("a1", "", 1, MessageDeliveryStatus::Queued)],
    ] {
        assert_eq!(
            MessageDeliveryCore.select(snapshot(&rows, &preparing)),
            Err(DeliveryLifecycleError::InvalidState)
        );
    }
}

#[test]
fn queued_successor_expires_behind_unknown_run() -> Result<(), DeliveryLifecycleError> {
    let mut rows = [
        row("unknown", "a", 1, MessageDeliveryStatus::Unknown),
        row("expired", "a", 2, MessageDeliveryStatus::Queued),
    ];
    rows[1].expire_at_ms = Some(100);
    let preparing = BTreeSet::new();
    assert_eq!(
        MessageDeliveryCore
            .select(snapshot(&rows, &preparing))?
            .action,
        Some(DeliveryScheduleAction::Expire {
            delivery_id: "expired".into(),
            state_version: 1
        })
    );
    Ok(())
}
#[test]
fn conformance_managed_delivery_scheduling() -> Result<(), DeliveryLifecycleError> {
    bcs_test_support::contract::core::message_delivery::message_delivery_scheduling_core_service_contract_tests(
        &MessageDeliveryCore,
    )
}
