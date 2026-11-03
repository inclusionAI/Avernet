//! Application boundary for task controls. Core diagnostics never reach adapters.
use async_trait::async_trait;
use bcs_service_api::application::{TaskHandleError, TaskHandleService, TaskHandleView};
use bcs_service_api::{MessageFlowService, ServiceError};
use std::sync::Arc;

pub struct TaskHandleApplicationService {
    flow: Arc<dyn MessageFlowService>,
}
impl TaskHandleApplicationService {
    pub fn new(flow: Arc<dyn MessageFlowService>) -> Self {
        Self { flow }
    }
}
fn translate(error: ServiceError) -> TaskHandleError {
    match error {
        ServiceError::InvalidOperation { .. } => TaskHandleError::InvalidRequest,
        _ => TaskHandleError::Unavailable,
    }
}
#[async_trait]
impl TaskHandleService for TaskHandleApplicationService {
    async fn task_status(&self, refs: Vec<String>) -> Result<Vec<TaskHandleView>, TaskHandleError> {
        self.flow.task_status(refs).await.map_err(translate)
    }
    async fn cancel_task(
        &self,
        reference: String,
        reason: String,
    ) -> Result<TaskHandleView, TaskHandleError> {
        self.flow
            .cancel_task(reference, reason)
            .await
            .map_err(translate)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn core_diagnostics_are_not_part_of_the_application_error() {
        let error = translate(ServiceError::InternalError(
            "private persistence diagnostic".into(),
        ));
        assert_eq!(error, TaskHandleError::Unavailable);
        assert_eq!(error.to_string(), "task-handle service unavailable");
        let error = translate(ServiceError::InvalidOperation {
            message: "core validation detail".into(),
            request_id: None,
        });
        assert_eq!(error, TaskHandleError::InvalidRequest);
        assert_eq!(error.to_string(), "invalid task-handle request");
    }
}
