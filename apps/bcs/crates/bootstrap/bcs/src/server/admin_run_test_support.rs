//! Run-registered A2A double for the detached-admin terminal integration test.
use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::A2aChatRunService;

pub(super) struct AdminRunTestA2aRuns(pub(super) Arc<dyn bcs_service_api::BotRunContextPort>);

#[async_trait]
impl A2aChatRunService for AdminRunTestA2aRuns {
    async fn start_async_chat(
        &self,
        cmd: bcs_service_api::AsyncA2aChatCommand,
    ) -> bcs_service_api::ServiceResult<bcs_service_api::AsyncA2aChatAccepted> {
        // Match the real A2A producer: a successful dispatch has already
        // registered its context before a Bot can report a terminal event.
        self.0.put_context(bcs_service_api::BotRunContext {
            run_id: cmd.run_id.clone(), bot_id: cmd.target_bot_id.clone(),
            group_id: String::new(), bcs_session_id: Some(cmd.session_key.clone()),
            deadline_ms: bcs_protocol::now_ms().saturating_add(cmd.timeout_ms), terminal: false,
        }).await;
        Ok(bcs_service_api::AsyncA2aChatAccepted {
            delivery: None,
            run_id: cmd.run_id,
            bot_uuid: cmd.target_bot_id,
            session_id: cmd.session_key,
            status: "dispatched".to_string(),
            expires_at_ms: u64::MAX,
        })
    }

    async fn get_run(
        &self,
        cmd: bcs_service_api::ChatRunQueryCommand,
    ) -> bcs_service_api::ServiceResult<bcs_service_api::A2aRunStatus> {
        Ok(bcs_service_api::A2aRunStatus {
            run_id: cmd.run_id,
            status: "running".to_string(),
            response: None,
        })
    }

    async fn cancel_run(
        &self,
        _cmd: bcs_service_api::ChatRunCancelCommand,
    ) -> bcs_service_api::ServiceResult<bcs_service_api::A2aRunStatus> {
        unreachable!("admin run test does not cancel")
    }
}
