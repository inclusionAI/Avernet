//! Pure managed-delivery lifecycle policy. This module deliberately has no
//! timer, transport or database access. It is not a process-local send queue.

use bcs_domain::DeliveryType;
use bcs_domain::message_delivery::{MessageDeliveryState, MessageDeliveryStatus};
use bcs_service_api::core::message_delivery::{
    DeliveryContextAction, DeliveryLifecycleError, DeliveryLifecycleEvent, DeliveryStateChange,
    MessageDeliveryCoreService, MessageDeliverySchedulingCoreService,
};

#[derive(Debug, Default)]
pub struct MessageDeliveryCore;

fn active(status: MessageDeliveryStatus) -> bool {
    use MessageDeliveryStatus::*;
    matches!(
        status,
        Dispatching | Running | Unknown | Cancelling | CancelUnknown
    )
}

fn terminal(status: MessageDeliveryStatus) -> bool {
    use MessageDeliveryStatus::*;
    matches!(
        status,
        Completed | Failed | Cancelled | Expired | RejectedCapacity | Consumed | DiscardedContext
    )
}

fn valid(state: MessageDeliveryState) -> bool {
    use MessageDeliveryStatus::*;
    if state.state_version == 0 {
        return false;
    }
    match state.kind {
        DeliveryType::Inject => {
            !state.may_have_been_sent
                && matches!(
                    state.status,
                    PendingContext | Bound | Consumed | DiscardedContext | Cancelled | Expired | Failed
                )
        }
        DeliveryType::Send => match state.status {
            Queued | Expired | RejectedCapacity => !state.may_have_been_sent,
            Dispatching | Running | Unknown | Cancelling | CancelUnknown | Completed => {
                state.may_have_been_sent
            }
            Failed | Cancelled => true,
            PendingContext | Bound | Consumed | DiscardedContext => false,
        },
    }
}

impl MessageDeliveryCoreService for MessageDeliveryCore {
    fn transition(
        &self,
        current: MessageDeliveryState,
        expected_state_version: u64,
        event: DeliveryLifecycleEvent,
    ) -> Result<DeliveryStateChange, DeliveryLifecycleError> {
        use DeliveryLifecycleEvent as Event;
        use MessageDeliveryStatus as Status;

        if !valid(current) {
            return Err(DeliveryLifecycleError::InvalidState);
        }
        if current.state_version != expected_state_version {
            return Err(DeliveryLifecycleError::StaleVersion {
                expected: expected_state_version,
                actual: current.state_version,
            });
        }
        let mut result = DeliveryStateChange {
            state: current,
            changed: false,
            release_active: false,
            request_abort: false,
            context_action: DeliveryContextAction::Keep,
        };
        // Never reopen a terminal delivery, including on a late transport
        // result after a fast final. Duplicate terminal processing has no effects.
        if terminal(current.status) {
            return Ok(result);
        }
        let invalid = || DeliveryLifecycleError::InvalidTransition {
            status: current.status,
            event,
        };
        let next = if current.kind == DeliveryType::Inject {
            match (current.status, event) {
                (Status::PendingContext, Event::BindContext) => Status::Bound,
                (Status::Bound, Event::ReleaseContext) => Status::PendingContext,
                (Status::Bound, Event::ConsumeContext) => Status::Consumed,
                (Status::Bound, Event::WithdrawBoundContext)
                | (Status::PendingContext, Event::CancelRequested) => Status::Cancelled,
                (Status::PendingContext, Event::QueueExpired) => Status::Expired,
                (_, Event::Recover) => current.status,
                _ => return Err(invalid()),
            }
        } else {
            match event {
                Event::StartSend if current.status == Status::Queued => {
                    result.state.may_have_been_sent = true;
                    Status::Dispatching
                }
                Event::Accepted => match current.status {
                    Status::Dispatching | Status::Unknown => Status::Running,
                    // ACK cannot erase a persisted cancellation intent.
                    Status::Running | Status::Cancelling | Status::CancelUnknown => current.status,
                    _ => return Err(invalid()),
                },
                Event::Submitted if active(current.status) => current.status,
                Event::DefinitelyNotSent { retry } => {
                    let status = match current.status {
                        Status::Dispatching => {
                            if retry {
                                Status::Queued
                            } else {
                                Status::Failed
                            }
                        }
                        Status::Cancelling | Status::CancelUnknown => Status::Cancelled,
                        // Unknown is never an automatic retry path.
                        Status::Unknown if !retry => Status::Failed,
                        _ => return Err(invalid()),
                    };
                    result.state.may_have_been_sent = false;
                    status
                }
                Event::TransportUnknown => match current.status {
                    Status::Dispatching | Status::Running | Status::Unknown => Status::Unknown,
                    Status::Cancelling | Status::CancelUnknown => Status::CancelUnknown,
                    _ => return Err(invalid()),
                },
                Event::CancelRequested => match current.status {
                    Status::Queued => Status::Cancelled,
                    Status::Dispatching | Status::Running | Status::Unknown => {
                        result.request_abort = true;
                        Status::Cancelling
                    }
                    Status::Cancelling | Status::CancelUnknown => current.status,
                    _ => return Err(invalid()),
                },
                Event::ScopeAbortRequested if active(current.status) => {
                    result.request_abort = true;
                    Status::Cancelling
                }
                Event::AbortUnconfirmed
                    if matches!(current.status, Status::Cancelling | Status::CancelUnknown) =>
                {
                    Status::CancelUnknown
                }
                Event::StartAbort if current.status == Status::Cancelling => {
                    result.request_abort = true;
                    current.status
                }
                Event::Completed if active(current.status) => Status::Completed,
                Event::Failed if active(current.status) => Status::Failed,
                Event::Aborted if active(current.status) => Status::Cancelled,
                Event::PreparationFailed if current.status == Status::Queued => Status::Failed,
                Event::QueueExpired if current.status == Status::Queued => Status::Expired,
                Event::Recover => match current.status {
                    Status::Dispatching | Status::Running => Status::Unknown,
                    Status::Cancelling => Status::CancelUnknown,
                    _ => current.status,
                },
                _ => return Err(invalid()),
            }
        };
        if next == current.status && result.state.may_have_been_sent == current.may_have_been_sent {
            return Ok(result);
        }
        result.state.status = next;
        result.state.state_version = current
            .state_version
            .checked_add(1)
            .ok_or(DeliveryLifecycleError::VersionExhausted)?;
        result.changed = true;
        result.release_active = active(current.status) && !active(next);
        if current.kind == DeliveryType::Send && terminal(next) {
            result.context_action = if result.state.may_have_been_sent {
                DeliveryContextAction::Consume
            } else {
                DeliveryContextAction::Release
            };
        }
        Ok(result)
    }
}

impl MessageDeliverySchedulingCoreService for MessageDeliveryCore {
    fn select(
        &self,
        snapshot: bcs_service_api::core::message_delivery::DeliveryScheduleSnapshot<'_>,
    ) -> Result<
        bcs_service_api::core::message_delivery::DeliveryScheduleDecision,
        DeliveryLifecycleError,
    > {
        use bcs_domain::message_delivery::DeliveryWaitReason as Reason;
        use bcs_service_api::core::message_delivery::{
            DeliveryScheduleAction as Action, DeliveryScheduleDecision, DeliveryScheduleEntry,
        };
        use std::collections::{BTreeMap, BTreeSet};

        if snapshot.max_running == 0 {
            return Err(DeliveryLifecycleError::InvalidState);
        }
        let mut ids = BTreeSet::new();
        let mut sequences = BTreeSet::new();
        let mut active_sessions = BTreeSet::new();
        let mut heads: BTreeMap<&str, &DeliveryScheduleEntry> = BTreeMap::new();
        for entry in snapshot.entries {
            if entry.state.kind != DeliveryType::Send
                || !valid(entry.state)
                || entry.delivery_id.is_empty()
                || entry.session_id.is_empty()
                || entry.source_session_seq == 0
                || !ids.insert(entry.delivery_id.as_str())
                || !sequences.insert((entry.session_id.as_str(), entry.source_session_seq))
            {
                return Err(DeliveryLifecycleError::InvalidState);
            }
            if active(entry.state.status) && !active_sessions.insert(entry.session_id.as_str()) {
                // Fail closed on a corrupted recovery snapshot; never conceal
                // two active runs by counting their lane as one occupied slot.
                return Err(DeliveryLifecycleError::InvalidState);
            }
            if !terminal(entry.state.status) {
                let head = heads.entry(entry.session_id.as_str()).or_insert(entry);
                if entry.source_session_seq < head.source_session_seq {
                    *head = entry;
                }
            }
        }
        // Expiry is not FIFO dispatch: a queued successor must still be able
        // to expire behind an indefinitely unknown run in its own lane.
        if let Some(entry) = snapshot
            .entries
            .iter()
            .filter(|entry| {
                entry.state.status == MessageDeliveryStatus::Queued
                    && entry
                        .expire_at_ms
                        .is_some_and(|deadline| deadline <= snapshot.now_ms)
            })
            .min_by_key(|entry| {
                (
                    entry.expire_at_ms,
                    entry.session_id.as_str(),
                    entry.source_session_seq,
                )
            })
        {
            return Ok(DeliveryScheduleDecision {
                action: Some(Action::Expire {
                    delivery_id: entry.delivery_id.clone(),
                    state_version: entry.state.state_version,
                }),
                waiting: Vec::new(),
            });
        }
        let mut ordered: Vec<_> = heads.into_iter().collect();
        if let Some(cursor) = snapshot.after_session_id {
            let pivot = ordered.partition_point(|(session, _)| *session <= cursor);
            ordered.rotate_left(pivot);
        }
        let mut decision = DeliveryScheduleDecision {
            action: None,
            waiting: Vec::new(),
        };
        for (session, entry) in ordered {
            if entry.state.status != MessageDeliveryStatus::Queued {
                continue;
            }
            let reason = if snapshot.paused {
                Some(Reason::Paused)
            } else if active_sessions.contains(session) {
                Some(Reason::PriorMessageRunning)
            } else if !snapshot.bot_online {
                Some(Reason::BotOffline)
            } else if active_sessions.len() >= snapshot.max_running {
                Some(Reason::BotCapacity)
            } else if snapshot.monotonic_now_ms < snapshot.next_send_tick_ms {
                Some(Reason::RateLimited)
            } else if snapshot.now_ms < entry.available_at_ms {
                Some(Reason::RetryBackoff)
            } else {
                None
            };
            if let Some(reason) = reason {
                decision.waiting.push((entry.delivery_id.clone(), reason));
            } else if decision.action.is_none()
                && !snapshot.preparing_delivery_ids.contains(&entry.delivery_id)
            {
                decision.action = Some(Action::Prepare {
                    delivery_id: entry.delivery_id.clone(),
                    state_version: entry.state.state_version,
                });
            }
        }
        Ok(decision)
    }
}
