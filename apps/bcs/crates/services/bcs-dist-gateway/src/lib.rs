//! Gateway core service of the distributed executor plane.
//!
//! [`DistGatewayCore`] implements `DistGatewayCoreService` over the repository
//! ports only: it checks the resolved scope, the frozen session and the task's
//! state before an input is admitted, and turns port results into core results.
//! It holds no storage code and no transport types.

mod admission;
mod content;
mod error;

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::core::dist_gateway::*;
use bcs_service_api::port::repo::distributed::common::PageRequest;
use bcs_service_api::port::repo::distributed::inbox::InboxProducerPort;
use bcs_service_api::port::repo::distributed::result::BlobStorePort;
use bcs_service_api::port::repo::distributed::session::SessionStorePort;
use bcs_service_api::port::repo::distributed::task_feed::*;
use error::read_error;
use futures::StreamExt;
use futures::stream::BoxStream;
use std::sync::Arc;

pub struct DistGatewayCore {
    inbox: Arc<dyn InboxProducerPort>,
    tasks: Arc<dyn TaskReaderPort>,
    sessions: Arc<dyn SessionStorePort>,
    blobs: Arc<dyn BlobStorePort>,
}

impl DistGatewayCore {
    pub fn new(
        inbox: Arc<dyn InboxProducerPort>,
        tasks: Arc<dyn TaskReaderPort>,
        sessions: Arc<dyn SessionStorePort>,
        blobs: Arc<dyn BlobStorePort>,
    ) -> Self {
        Self {
            inbox,
            tasks,
            sessions,
            blobs,
        }
    }
}

#[async_trait]
impl DistGatewayCoreService for DistGatewayCore {
    async fn admit(
        &self,
        scope: &TaskScope,
        command: AdmitTaskCommand,
    ) -> Result<CoreAdmission, DistGatewayCoreError> {
        admission::admit(self, scope, command).await
    }

    async fn restore_task(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<RestoredTaskCut, DistGatewayCoreError> {
        let (mut view, cursor) = match self.tasks.restore(scope, tid).await.map_err(read_error)? {
            None => return Err(DistGatewayCoreError::NotFound),
            Some(RestoredTask::Final(view)) => (view, None),
            Some(RestoredTask::Live { view, cursor }) => (view, Some(cursor)),
        };
        view.content.snapshot = content::inline(self.blobs.as_ref(), view.content.snapshot).await?;
        Ok(RestoredTaskCut { view, cursor })
    }

    async fn validate_resume(
        &self,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<CoreResumeCheck, DistGatewayCoreError> {
        Ok(
            match self.tasks.validate_resume(scope, cursor).await.map_err(read_error)? {
                ResumeCheck::Valid => CoreResumeCheck::Valid,
                ResumeCheck::Gap => CoreResumeCheck::Gap,
                ResumeCheck::InvalidScope => CoreResumeCheck::InvalidScope,
            },
        )
    }

    fn follow_task(
        &self,
        scope: TaskScope,
        cursor: TaskCursor,
    ) -> BoxStream<'static, Result<CoreFollowItem, DistGatewayCoreError>> {
        let blobs = self.blobs.clone();
        self.tasks
            .follow(scope, cursor)
            .then(move |item| {
                let blobs = blobs.clone();
                async move {
                    match item.map_err(read_error)? {
                        FollowItem::Gap => Ok(CoreFollowItem::Gap),
                        FollowItem::Events { items, next } => {
                            let mut events = Vec::with_capacity(items.len());
                            for event in items {
                                events.push(content::inline_event(blobs.as_ref(), event).await?);
                            }
                            Ok(CoreFollowItem::Events { events, next })
                        }
                    }
                }
            })
            .boxed()
    }

    async fn list_tasks(
        &self,
        scope: &TaskScope,
        query: CoreTaskQuery,
    ) -> Result<CoreTaskPage, DistGatewayCoreError> {
        if let Some(sid) = &query.sid {
            // The session must exist and belong to the caller before it is listed.
            match self.sessions.get_spec(sid).await.map_err(read_error)? {
                Some(spec) if &spec.scope == scope => {}
                Some(_) => return Err(DistGatewayCoreError::ScopeMismatch),
                None => return Err(DistGatewayCoreError::NotFound),
            }
        }
        let filter = TaskFilter {
            sid: query.sid,
            statuses: query.statuses,
        };
        let page = PageRequest {
            after: query.after,
            limit: query.limit,
        };
        // Listed views keep large content as references: a list shows states,
        // and a caller reads one task to get its content.
        let page = self.tasks.list(scope, filter, page).await.map_err(read_error)?;
        Ok(CoreTaskPage {
            tasks: page.items,
            next: page.next,
        })
    }
}
