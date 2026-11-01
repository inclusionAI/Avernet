use async_trait::async_trait;
use axum::{body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_service_api::{MessageFlowService, ServiceResult, TaskHandleStatus as S, TaskHandleView as View};
use std::sync::Arc;
use tower::ServiceExt;

struct Flow { cancel_storage_failure: bool }
#[async_trait]
impl MessageFlowService for Flow {
    async fn handle_bot_event(&self, _: bcs_service_api::BotEventCommand) -> ServiceResult<bcs_service_api::BotEventOutcome> { unreachable!() }
    async fn handle_group_callback(&self, _: bcs_service_api::GroupCallbackCommand) -> ServiceResult<bcs_service_api::GroupCallbackOutcome> { unreachable!() }
    async fn handle_chat_abort(&self, _: bcs_service_api::ChatAbortCommand) -> ServiceResult<bcs_service_api::ChatAbortOutcome> { unreachable!() }
    async fn register_task_run_alias(&self, _: &str, _: &str, _: &str) -> ServiceResult<bcs_service_api::TaskRunAliasRegistration> { unreachable!() }
    async fn handle_task_dispatch(&self, _: bcs_service_api::TaskDispatchCommand) -> ServiceResult<bcs_service_api::TaskDispatchOutcome> { unreachable!() }
    async fn handle_task_complete(&self, _: bcs_service_api::TaskCompleteCommand) -> ServiceResult<bcs_service_api::TaskCompleteOutcome> { unreachable!() }
    async fn handle_web_send(&self, _: bcs_service_api::WebSendCommand) -> ServiceResult<bcs_service_api::WebSendOutcome> { unreachable!() }
    async fn task_status(&self, refs: Vec<String>) -> ServiceResult<Vec<View>> {
        assert_eq!(refs, vec!["T-1234abcd"]);
        Ok(vec![View::new(refs[0].clone(), S::NotFound)])
    }
    async fn cancel_task(&self, reference: String, reason: String) -> ServiceResult<View> {
        assert_eq!(reason, "please stop");
        if self.cancel_storage_failure {
            return Err(bcs_service_api::ServiceError::InternalError("task cancellation persistence failed".into()));
        }
        Ok(View::new(reference, S::Unknown))
    }
}

#[tokio::test]
async fn task_http_contract_is_minimal_and_does_not_treat_absence_as_stop() {
    for (path, input, expected) in [
        ("/tasks/status", serde_json::json!({"task_refs":["T-1234abcd"]}), serde_json::json!({"tasks":[{"ref":"T-1234abcd","status":"not_found"}]})),
        ("/tasks/cancel", serde_json::json!({"task_ref":"T-1234abcd","reason":"please stop"}), serde_json::json!({"ref":"T-1234abcd","status":"unknown"})),
    ] {
        let mut services = bcs_services_container::Services::noop();
        services.message_flow = Arc::new(Flow { cancel_storage_failure:false });
        let app = bcs_http::router::build_router(bcs_http::state::HttpAppState::new(services));
        let response = app.oneshot(Request::builder().method("POST").uri(path)
            .header("content-type", "application/json").body(Body::from(input.to_string())).unwrap()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), 4096).await.unwrap();
        assert_eq!(serde_json::from_slice::<serde_json::Value>(&body).unwrap(), expected);
    }
}

#[tokio::test]
async fn cancellation_storage_errors_return_service_unavailable() {
    let mut services = bcs_services_container::Services::noop();
    services.message_flow = Arc::new(Flow { cancel_storage_failure:true });
    let app = bcs_http::router::build_router(bcs_http::state::HttpAppState::new(services));
    let response = app.oneshot(Request::builder().method("POST").uri("/tasks/cancel")
        .header("content-type", "application/json")
        .body(Body::from(serde_json::json!({"task_ref":"T-1234abcd","reason":"please stop"}).to_string())).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}
