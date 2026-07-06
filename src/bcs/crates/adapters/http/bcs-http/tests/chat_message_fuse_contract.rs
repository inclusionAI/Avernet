use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{FusionResponse, GroupFusionCommand, GroupFusionService, ServiceResult};
use bcs_services_container::Services;
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[derive(Default)]
struct RecordingGroupFusion {
    commands: Mutex<Vec<GroupFusionCommand>>,
}

#[async_trait::async_trait]
impl GroupFusionService for RecordingGroupFusion {
    async fn fuse_for_group(&self, cmd: GroupFusionCommand) -> ServiceResult<FusionResponse> {
        self.commands.lock().await.push(cmd);
        Ok(FusionResponse {
            recommendation: Some("merged context".to_string()),
            ..FusionResponse::default()
        })
    }
}

#[tokio::test]
async fn fuse_route_delegates_to_group_fusion_service() {
    let group_fusion = Arc::new(RecordingGroupFusion::default());
    let services = Services::builder()
        .group_fusion(group_fusion.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/fuse")
                .header("content-type", "application/json")
                .body(Body::from(
                    r#"{"question":"what changed?","participants":[]}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["recommendation"], "merged context");

    let commands = group_fusion.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].group_id, "group-1");
    assert_eq!(commands[0].request.question, "what changed?");
    assert!(commands[0].request.participants.is_empty());
}
