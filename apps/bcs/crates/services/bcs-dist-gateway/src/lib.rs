//! Gateway core service of the distributed executor plane.
//!
//! [`DistGatewayCore`] implements `DistGatewayCoreService` over injected Worker
//! admission/output facades and the session specification port. It checks the
//! resolved scope and maps results, forwarding already formatted output views.
//! It holds no storage code, output conversion rules or transport types.

mod admission;
mod error;

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::core::dist_gateway::*;
use bcs_service_api::core::dist_admission::TaskAdmissionCoreService;
use bcs_service_api::port::dist_output::{DistTaskOutputReaderPort, FormattedFollowItem};
use bcs_service_api::port::repo::distributed::common::PageRequest;
use bcs_service_api::port::repo::distributed::session::SessionStorePort;
use bcs_service_api::port::repo::distributed::task_feed::*;
use error::read_error;
use futures::StreamExt;
use futures::stream::BoxStream;
use std::sync::Arc;

pub struct DistGatewayCore {
    inbox: Arc<dyn TaskAdmissionCoreService>,
    tasks: Arc<dyn DistTaskOutputReaderPort>,
    sessions: Arc<dyn SessionStorePort>,
}

impl DistGatewayCore {
    pub fn new(
        inbox: Arc<dyn TaskAdmissionCoreService>,
        tasks: Arc<dyn DistTaskOutputReaderPort>,
        sessions: Arc<dyn SessionStorePort>,
    ) -> Self {
        Self {
            inbox,
            tasks,
            sessions,
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

    async fn session_group(
        &self,
        scope: &TaskScope,
        sid: &Sid,
    ) -> Result<GroupId, DistGatewayCoreError> {
        // Another scope's session is reported as absent, like its tasks.
        match self.sessions.get_spec(sid).await.map_err(read_error)? {
            Some(spec) if &spec.scope == scope => Ok(spec.group),
            _ => Err(DistGatewayCoreError::NotFound),
        }
    }

    async fn restore_task(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<RestoredTaskCut, DistGatewayCoreError> {
        let (view, cursor) = match self.tasks.restore(scope, tid).await.map_err(read_error)? {
            None => return Err(DistGatewayCoreError::NotFound),
            Some(RestoredTask::Final(view)) => (view, None),
            Some(RestoredTask::Live { view, cursor }) => (view, Some(cursor)),
        };
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
        self.tasks
            .follow(scope, cursor)
            .map(|item| {
                Ok(match item.map_err(read_error)? {
                    FormattedFollowItem::Gap => CoreFollowItem::Gap,
                    FormattedFollowItem::Events { events, next } => CoreFollowItem::Events { events, next },
                })
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
