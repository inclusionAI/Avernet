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
use bcs_service_api::application::{
    TaskHandleError, TaskHandleService, TaskHandleStatus as S, TaskHandleView as View,
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
impl TaskHandleService for Flow {
    async fn task_status(&self, refs: Vec<String>) -> Result<Vec<View>, TaskHandleError> {
        if refs.is_empty() {
            return Err(TaskHandleError::InvalidRequest);
        }
        assert_eq!(refs, vec!["T-1234abcd"]);
        Ok(vec![View::new(refs[0].clone(), S::NotFound)])
    }
    async fn cancel_task(
        &self,
        reference: String,
        reason: String,
    ) -> Result<View, TaskHandleError> {
        assert_eq!(reason, "please stop");
        if self.cancel_storage_failure {
            return Err(TaskHandleError::Unavailable);
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
        serde_json::json!({"intent_ids":["T-1234abcd"]})
    } else {
        serde_json::json!({"intent_id":"T-1234abcd","reason":"please stop"})
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

#[tokio::test]
async fn application_validation_errors_return_bad_request() {
    let request = Request::builder()
        .method("POST")
        .uri("/openapi/v1/collaboration/worker-tasks/status")
        .header("x-test-auth", "yes")
        .header("content-type", "application/json")
        .body(Body::from(r#"{"task_refs":[]}"#))
        .unwrap();
    let response = app(false).oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = to_bytes(response.into_body(), 4096).await.unwrap();
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&body).unwrap(),
        serde_json::json!({"error":"task_handle_request_failed"})
    );
}
