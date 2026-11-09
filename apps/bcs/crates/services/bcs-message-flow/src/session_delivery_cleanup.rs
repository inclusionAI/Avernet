//! Settle the unfinished queue of a Session that is being deleted.
//!
//! Called before the Session row is removed, so a failure here fails the
//! delete and the delete can be retried. Never-sent rows are cancelled in
//! place; maybe-sent rows get a durable cancel intent, and the delivery
//! scheduler performs the actual Bot abort I/O asynchronously (control loop:
//! `cancelling` rows without an abort request). The cancel carries the
//! `session_deleting` marker so no task notice is enqueued into the Session;
//! later terminal notices find the Session gone and are dropped.
use crate::BcsMessageFlow;
use bcs_domain::DeliveryType;
use bcs_domain::message_delivery::{MessageDeliveryStatus as Status, PersistedMessageDelivery};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::{DeliveryLookup, MessageDeliveryRepoError};
use bcs_service_api::{
    DeliveryTransitionCommand, ManagedDeliveryError, ManagedMessageDeliveryService, ServiceError,
    ServiceResult,
};

/// Same cancel budget the per-message cancellation API grants.
const CANCEL_DEADLINE_MS: i64 = 30_000;

/// Bounded reload-and-retry per row when a concurrent scheduler commit (e.g.
/// StartSend / Accepted) wins the version race against the cancel.
const CANCEL_ATTEMPTS: usize = 3;

pub(crate) async fn cancel(
    flow: &BcsMessageFlow,
    session_id: &str,
    reason: &str,
) -> ServiceResult<()> {
    match flow.managed_deliveries.as_ref() {
        Some(service) => cancel_session(service.as_ref(), session_id, reason).await,
        None => Ok(()),
    }
}

/// Rows read per Session page (the lookup itself caps at 500).
const PAGE: usize = 200;

/// Active rows this cleanup must still cancel. Anything else is terminal or
/// already carries a durable cancel intent.
fn needs_cancel(row: &PersistedMessageDelivery) -> bool {
    row.state.kind == DeliveryType::Send
        && matches!(row.state.status, Status::Queued | Status::Dispatching | Status::Running | Status::Unknown)
}

/// Every unfinished Send of the Session, whoever the target Bot is now: a Bot
/// removed from the Session while its delivery was running is still here.
/// Pages follow the Session index with a keyset cursor, so the sweep reads
/// the Session's index entries once instead of re-scanning per page.
async fn cancel_session(
    service: &dyn ManagedMessageDeliveryService,
    session_id: &str,
    reason: &str,
) -> ServiceResult<()> {
    let mut after: Option<(String, String)> = None;
    let mut failed = 0usize;
    loop {
        let rows = service
            .lookup(DeliveryLookup::SessionUnfinished { session: session_id.to_owned(), after: after.clone(), limit: PAGE })
            .await
            .map_err(|error| ServiceError::InternalError(format!("session delivery lookup failed: {error}")))?;
        let Some(last) = rows.last() else { break };
        after = Some((last.source_message_id.clone(), last.delivery_id.clone()));
        let full = rows.len() >= PAGE;
        for row in rows.into_iter().filter(|row| row.session_id == session_id) {
            if let Err(error) = cancel_row(service, row, reason).await {
                failed += 1;
                tracing::warn!(%session_id, %error, "session delete: delivery cancellation failed");
            }
        }
        if !full {
            break;
        }
    }
    if failed > 0 {
        return Err(ServiceError::InternalError(format!(
            "session delete: {failed} delivery cancellation(s) failed"
        )));
    }
    Ok(())
}

/// A lost version race is only "done" once the reloaded row no longer needs a
/// cancel; otherwise retry against the fresh version, then fail so the
/// Session is not deleted with a live, un-aborted delivery.
async fn cancel_row(
    service: &dyn ManagedMessageDeliveryService,
    mut row: PersistedMessageDelivery,
    reason: &str,
) -> Result<(), String> {
    for _ in 0..CANCEL_ATTEMPTS {
        if !needs_cancel(&row) {
            return Ok(());
        }
        let now_ms = chrono::Utc::now().timestamp_millis();
        let command = DeliveryTransitionCommand {
            caller_dispatch_commit: None,
            caller_cancel: None,
            caller_terminal: None,
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            event: Event::CancelRequested,
            now_ms,
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: Some(serde_json::json!({
                "cancel_reason": reason,
                crate::managed_delivery::SESSION_DELETING: true,
            })),
            deadline_at_ms: Some(now_ms.saturating_add(CANCEL_DEADLINE_MS)),
        };
        match service.transition(command).await {
            Ok(_) => return Ok(()),
            Err(ManagedDeliveryError::Conflict | ManagedDeliveryError::NotFound)
            | Err(ManagedDeliveryError::Repository(MessageDeliveryRepoError::Conflict))
            | Err(ManagedDeliveryError::Lifecycle(_)) => {
                match service.lookup(DeliveryLookup::Id(row.delivery_id.clone())).await {
                    Ok(mut current) => match current.pop() {
                        Some(current) => row = current,
                        None => return Ok(()),
                    },
                    Err(error) => return Err(format!("{}: reload failed: {error}", row.delivery_id)),
                }
            }
            Err(error) => return Err(format!("{}: {error}", row.delivery_id)),
        }
    }
    if needs_cancel(&row) {
        return Err(format!("{}: still active after {CANCEL_ATTEMPTS} cancel attempts", row.delivery_id));
    }
    Ok(())
}

#[cfg(test)]
#[path = "session_delivery_cleanup_tests.rs"]
mod tests;
