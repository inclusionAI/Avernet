//! Settle the unfinished queue of a deleted Session.
//!
//! Called after the Session row is gone. Never-sent rows are cancelled in
//! place; maybe-sent rows get a durable cancel intent, and the delivery
//! scheduler performs the actual Bot abort I/O asynchronously (control loop:
//! `cancelling` rows without an abort request). Task failure notices built by
//! these transitions are dropped by the queue because the Session no longer
//! exists, so nothing new is enqueued into the deleted Session.
use crate::BcsMessageFlow;
use bcs_domain::DeliveryType;
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::{DeliveryLookup, MessageDeliveryRepoError};
use bcs_service_api::{
    DeliveryTransitionCommand, ManagedDeliveryError, ManagedMessageDeliveryService, ServiceError,
    ServiceResult,
};

/// Same cancel budget the per-message cancellation API grants.
const CANCEL_DEADLINE_MS: i64 = 30_000;

pub(crate) async fn cancel(
    flow: &BcsMessageFlow,
    session_id: &str,
    bot_ids: &[String],
    reason: &str,
) -> ServiceResult<()> {
    let Some(service) = flow.managed_deliveries.as_ref() else {
        return Ok(());
    };
    let mut failed = 0usize;
    for bot in bot_ids {
        // Bounded by the per-Bot `max_queued` policy; served by the lane index.
        let rows = service
            .lookup(DeliveryLookup::Lane { bot: bot.clone(), session: session_id.to_owned() })
            .await
            .map_err(|error| ServiceError::InternalError(format!("session delivery lookup failed: {error}")))?;
        for row in rows {
            if row.session_id != session_id
                || row.state.kind != DeliveryType::Send
                || !matches!(row.state.status, Status::Queued | Status::Dispatching | Status::Running | Status::Unknown)
            {
                continue;
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
                transport_context_json: Some(serde_json::json!({ "cancel_reason": reason })),
                deadline_at_ms: Some(now_ms.saturating_add(CANCEL_DEADLINE_MS)),
            };
            match service.transition(command).await {
                Ok(_) => {}
                // Another path converged the row first.
                Err(ManagedDeliveryError::Conflict | ManagedDeliveryError::NotFound)
                | Err(ManagedDeliveryError::Repository(MessageDeliveryRepoError::Conflict))
                | Err(ManagedDeliveryError::Lifecycle(_)) => {}
                Err(error) => {
                    failed += 1;
                    tracing::warn!(%session_id, delivery_id = %row.delivery_id, %error,
                        "session delete: delivery cancellation failed");
                }
            }
        }
    }
    if failed > 0 {
        return Err(ServiceError::InternalError(format!(
            "session delete: {failed} delivery cancellation(s) failed"
        )));
    }
    Ok(())
}
