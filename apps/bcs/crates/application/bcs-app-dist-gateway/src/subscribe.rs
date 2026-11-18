//! Task subscriptions.
//!
//! ```text
//! Snapshot:  restore → Snapshot{view, cursor}; finished → end; else follow
//! Resume(c): valid → ResumeAccepted{c}, follow(c); gap → as Snapshot
//! follow:    Events per item; end after input-required, auth-required or a
//!            final event; a gap → restore again, emit Snapshot, go on
//! ```
//!
//! Dropping the stream ends the subscription; the task is unaffected.

use crate::error::core_error;
use crate::identity::session_of_task;
use crate::{DistGateway, scope_of};
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::core::dist_gateway::*;
use futures::StreamExt;
use futures::stream::BoxStream;
use std::sync::Arc;

type CoreFollow = BoxStream<'static, Result<CoreFollowItem, DistGatewayCoreError>>;

struct Subscription {
    core: Arc<dyn DistGatewayCoreService>,
    scope: TaskScope,
    tid: Tid,
    /// Items ready before the first poll (the snapshot or the resume ack).
    first: Option<TaskStreamItem>,
    follow: Option<CoreFollow>,
    /// `true` right after a snapshot that was sent because of a gap: another
    /// gap before any event then means the stored position cannot be followed
    /// at all. A gap after the first snapshot is ordinary (the task advanced
    /// or finished since it was restored) and gets a fresh snapshot.
    fresh_snapshot: bool,
}

pub(crate) async fn subscribe(
    gateway: &DistGateway,
    caller: &GatewayCaller,
    request: SubscribeTask,
) -> Result<TaskSubscription, GatewayError> {
    let scope = scope_of(caller)?;
    let sid = session_of_task(&request.tid)?;
    gateway.check_group(&scope, &sid, &request.group).await?;
    let core = gateway.core.clone();
    let resume = match request.mode {
        SubscribeMode::Snapshot => None,
        SubscribeMode::Resume(cursor) => {
            if cursor.tid != request.tid {
                return Err(GatewayError::AccessDenied);
            }
            match core.validate_resume(&scope, &cursor).await.map_err(core_error)? {
                CoreResumeCheck::Valid => Some(cursor),
                // Not usable any more: answer snapshot first.
                CoreResumeCheck::Gap => None,
                CoreResumeCheck::InvalidScope => return Err(GatewayError::AccessDenied),
            }
        }
    };
    let (first, follow) = match resume {
        Some(cursor) => (
            TaskStreamItem::ResumeAccepted {
                cursor: cursor.clone(),
            },
            Some(core.follow_task(scope.clone(), cursor)),
        ),
        None => snapshot(&core, &scope, &request.tid).await?,
    };
    let subscription = Subscription {
        core,
        scope,
        tid: request.tid,
        fresh_snapshot: false,
        first: Some(first),
        follow,
    };
    Ok(futures::stream::unfold(subscription, Subscription::step).boxed())
}

/// The stored view as the first item, and the follow stream after it unless
/// the task is finished.
async fn snapshot(
    core: &Arc<dyn DistGatewayCoreService>,
    scope: &TaskScope,
    tid: &Tid,
) -> Result<(TaskStreamItem, Option<CoreFollow>), GatewayError> {
    let cut = core.restore_task(scope, tid).await.map_err(core_error)?;
    let follow = cut
        .cursor
        .clone()
        .map(|cursor| core.follow_task(scope.clone(), cursor));
    let item = TaskStreamItem::Snapshot {
        view: cut.view,
        cursor: cut.cursor,
    };
    Ok((item, follow))
}

/// `true` for an event after which the stream ends: the task asks for input
/// or authorization, or is finished.
pub(crate) fn ends_stream(event: &OutputEvent) -> bool {
    match &event.body {
        OutputBody::Terminal { .. } => true,
        OutputBody::Status { status, .. } => !matches!(status, TaskStatus::Submitted | TaskStatus::Working),
        OutputBody::Artifact { .. } => false,
    }
}

type Step = Option<(Result<TaskStreamItem, GatewayError>, Subscription)>;

impl Subscription {
    async fn step(mut self) -> Step {
        if let Some(first) = self.first.take() {
            return Some((Ok(first), self));
        }
        let mut follow = self.follow.take()?;
        match follow.next().await {
            None => None,
            Some(Err(error)) => Some((Err(core_error(error)), self)),
            Some(Ok(CoreFollowItem::Events { events, next })) => {
                self.fresh_snapshot = false;
                if !events.iter().any(ends_stream) {
                    self.follow = Some(follow);
                }
                Some((Ok(TaskStreamItem::Events { events, cursor: next }), self))
            }
            // Two gaps in a row: starting over would only repeat. End the
            // stream with a retryable error instead of sending snapshots forever.
            Some(Ok(CoreFollowItem::Gap)) if self.fresh_snapshot => {
                let error = GatewayError::Unavailable {
                    code: "task_position_unavailable".to_owned(),
                };
                Some((Err(error), self))
            }
            // The position is gone: start over from the stored view.
            Some(Ok(CoreFollowItem::Gap)) => match snapshot(&self.core, &self.scope, &self.tid).await {
                Ok((item, follow)) => {
                    self.follow = follow;
                    self.fresh_snapshot = true;
                    Some((Ok(item), self))
                }
                Err(error) => Some((Err(error), self)),
            },
        }
    }
}
