mod register_support;
use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_service_api::application::v1::{
    ApplicationError, BotRegistration, IssueRegisterToken, RegisterBot, RegisterService,
    RegisterTokenView,
};
use bcs_service_api::{
    MessageFlowService, ServiceResult, TaskHandleStatus as S, TaskHandleView as View,
};
use std::sync::Arc;
use tower::ServiceExt;

struct Register;
#[async_trait]
impl RegisterService for Register {
    async fn issue_register_token(
        &self,
        _: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        unreachable!()
    }
    async fn register_bot(&self, _: RegisterBot) -> Result<BotRegistration, ApplicationError> {
        unreachable!()
    }
}
struct Flow {
    cancel_storage_failure: bool,
}
#[async_trait]
impl MessageFlowService for Flow {
    async fn handle_bot_event(
        &self,
        _: bcs_service_api::BotEventCommand,
    ) -> ServiceResult<bcs_service_api::BotEventOutcome> {
        unreachable!()
    }
    async fn handle_group_callback(
        &self,
        _: bcs_service_api::GroupCallbackCommand,
    ) -> ServiceResult<bcs_service_api::GroupCallbackOutcome> {
        unreachable!()
    }
    async fn handle_chat_abort(
        &self,
        _: bcs_service_api::ChatAbortCommand,
    ) -> ServiceResult<bcs_service_api::ChatAbortOutcome> {
        unreachable!()
    }
    async fn register_task_run_alias(
        &self,
        _: &str,
        _: &str,
        _: &str,
    ) -> ServiceResult<bcs_service_api::TaskRunAliasRegistration> {
        unreachable!()
    }
    async fn handle_task_dispatch(
        &self,
        _: bcs_service_api::TaskDispatchCommand,
    ) -> ServiceResult<bcs_service_api::TaskDispatchOutcome> {
        unreachable!()
    }
    async fn handle_task_complete(
        &self,
        _: bcs_service_api::TaskCompleteCommand,
    ) -> ServiceResult<bcs_service_api::TaskCompleteOutcome> {
        unreachable!()
    }
    async fn handle_web_send(
        &self,
        _: bcs_service_api::WebSendCommand,
    ) -> ServiceResult<bcs_service_api::WebSendOutcome> {
        unreachable!()
    }
    async fn task_status(&self, refs: Vec<String>) -> ServiceResult<Vec<View>> {
        assert_eq!(refs, vec!["T-1234abcd"]);
        Ok(vec![View::new(refs[0].clone(), S::NotFound)])
    }
    async fn cancel_task(&self, reference: String, reason: String) -> ServiceResult<View> {
        assert_eq!(reason, "please stop");
        if self.cancel_storage_failure {
            return Err(bcs_service_api::ServiceError::InternalError(
                "task cancellation persistence failed".into(),
            ));
        }
        Ok(View::new(reference, S::Unknown))
    }
}

fn app(storage_failure: bool) -> axum::Router {
    let mut state = register_support::test_state(Arc::new(Register));
    state.worker_task_service = Some(Arc::new(Flow {
        cancel_storage_failure: storage_failure,
    }));
    bcs_api_http::router(state)
}
fn request(action: &str, auth: bool) -> Request<Body> {
    let input = if action == "status" {
        serde_json::json!({"task_refs":["T-1234abcd"]})
    } else {
        serde_json::json!({"task_ref":"T-1234abcd","reason":"please stop"})
    };
    let mut builder = Request::builder()
        .method("POST")
        .uri(format!("/openapi/v1/collaboration/worker-tasks/{action}"))
        .header("content-type", "application/json");
    if auth {
        builder = builder.header("x-test-auth", "yes");
    }
    builder.body(Body::from(input.to_string())).unwrap()
}
#[tokio::test]
async fn worker_task_controls_require_principal() {
    for action in ["status", "cancel"] {
        let response = app(false).oneshot(request(action, false)).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }
}
#[tokio::test]
async fn status_returns_bare_task_list_through_the_application_boundary() {
    let response = app(false).oneshot(request("status", true)).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), 4096).await.unwrap();
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&body).unwrap(),
        serde_json::json!({"tasks":[{"ref":"T-1234abcd","status":"not_found"}]})
    );
}
#[tokio::test]
async fn cancel_keeps_uncertainty_and_storage_failures_distinct() {
    let response = app(false).oneshot(request("cancel", true)).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), 4096).await.unwrap();
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&body).unwrap(),
        serde_json::json!({"ref":"T-1234abcd","status":"unknown"})
    );
    let response = app(true).oneshot(request("cancel", true)).await.unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}
