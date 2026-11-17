//! Waiting for a task to need input or to finish.
//!
//! Used by a blocking send and by cancel. The stored view is returned as is
//! (no folding of later events), so after the awaited event shows up in the
//! event stream the task is re-read until its view covers that event.

use crate::error::core_error;
use crate::identity::session_of_task;
use crate::subscribe::ends_stream;
use crate::{DistGateway, scope_of};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::core::dist_gateway::*;
use futures::StreamExt;
use tokio::time::{Instant, sleep, timeout_at};

fn is_final(status: TaskStatus) -> bool {
    matches!(
        status,
        TaskStatus::Completed | TaskStatus::Failed | TaskStatus::Canceled | TaskStatus::Rejected
    )
}

fn event_matches(event: &OutputEvent, until: WaitUntil) -> bool {
    match (&event.body, until) {
        (OutputBody::Terminal { .. }, _) => true,
        (OutputBody::Status { status, .. }, WaitUntil::Final) => is_final(*status),
        (OutputBody::Status { .. }, WaitUntil::InputOrFinal) => ends_stream(event),
        (OutputBody::Artifact { .. }, _) => false,
    }
}

/// Does the stored view already answer the wait? A finished task always does.
/// A task waiting for input does only if that state is newer than `after`
/// (otherwise it is the question the caller has just answered).
fn view_matches(view: &TaskView, request: &WaitTask) -> bool {
    let status = view.content.status;
    if is_final(status) {
        return true;
    }
    let waiting = matches!(status, TaskStatus::InputRequired | TaskStatus::AuthRequired);
    request.until == WaitUntil::InputOrFinal
        && waiting
        && request.after.is_none_or(|after| view.covered_through > Some(after))
}

pub(crate) async fn wait_task(
    gateway: &DistGateway,
    caller: &GatewayCaller,
    request: WaitTask,
) -> Result<TaskView, GatewayError> {
    let scope = scope_of(caller)?;
    session_of_task(&request.tid)?;
    let deadline = Instant::now() + request.timeout;
    let restore = || async {
        gateway
            .core
            .restore_task(&scope, &request.tid)
            .await
            .map_err(core_error)
    };
    let mut cut = restore().await?;
    // Find the awaited event in the event stream.
    let awaited = loop {
        if view_matches(&cut.view, &request) {
            return Ok(cut.view);
        }
        let Some(cursor) = cut.cursor.clone() else {
            return Ok(cut.view);
        };
        let mut follow = gateway.core.follow_task(scope.clone(), cursor);
        let found = loop {
            match timeout_at(deadline, follow.next()).await {
                // Timeout: the current view is the answer.
                Err(_) => return restore().await.map(|cut| cut.view),
                Ok(None) => return restore().await.map(|cut| cut.view),
                Ok(Some(Err(error))) => return Err(core_error(error)),
                Ok(Some(Ok(CoreFollowItem::Gap))) => break None,
                Ok(Some(Ok(CoreFollowItem::Events { events, .. }))) => {
                    if let Some(event) = events.iter().find(|e| event_matches(e, request.until)) {
                        break Some(event.id);
                    }
                }
            }
        };
        match found {
            Some(id) => break id,
            // The position is gone: look at the stored view again.
            None => cut = restore().await?,
        }
    };
    // The event is committed; wait (bounded) until the stored view covers it.
    let cover_deadline = deadline.min(Instant::now() + gateway.config.snapshot_cover_limit);
    loop {
        cut = restore().await?;
        let covered = cut.cursor.is_none() || cut.view.covered_through >= Some(awaited);
        if covered || Instant::now() + gateway.config.snapshot_cover_retry > cover_deadline {
            return Ok(cut.view);
        }
        sleep(gateway.config.snapshot_cover_retry).await;
    }
}
