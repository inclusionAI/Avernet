#![allow(clippy::expect_used, reason = "test assertions intentionally fail fast")]

use std::sync::Arc;

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_config_api::{ManifestBundleConfig, ManifestConfig};
use bcs_service_api::application::v1::*;
use serde_json::Value;
use tower::ServiceExt;

struct AcceptAllVerifier;

#[async_trait]
impl PrincipalVerifier for AcceptAllVerifier {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        // Public routes do not pass the verify_principal boundary; this is
        // only here to satisfy ApiState::new.
        Err(PrincipalVerificationError::Missing)
    }
}

fn file_bundle_config() -> ManifestConfig {
    ManifestConfig {
        schema_version: 1,
        bundles: vec![ManifestBundleConfig {
            name: "bcsPanel".to_string(),
            source_type: None,
            url: None,
            file: Some("assets/panel/dist/index.umd.js".to_string()),
        }],
    }
}

fn test_router(manifest: ManifestConfig) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(NoopFriendshipService),
            Arc::new(AcceptAllVerifier),
        )
        .with_manifest_config("prod".to_string(), manifest),
    )
}

async fn body_bytes(response: axum::http::Response<Body>) -> Vec<u8> {
    to_bytes(response.into_body(), usize::MAX).await.expect("body").to_vec()
}

#[tokio::test]
async fn manifest_returns_enveloped_bundles_with_gateway_asset_url() {
    let app = test_router(file_bundle_config());
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/manifest")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let bytes = body_bytes(response).await;
    let body: Value = serde_json::from_slice(&bytes).expect("json");
    assert_eq!(body["code"], 20000);
    assert_eq!(body["data"]["schema_version"], 1);
    assert_eq!(body["data"]["env"], "prod");
    assert_eq!(body["data"]["bundles"][0]["name"], "bcsPanel");
    assert_eq!(
        body["data"]["bundles"][0]["url"],
        "/api/v1/collaboration/assets/bcsPanel/index.umd.js"
    );
}

#[tokio::test]
async fn assets_serves_raw_file_bytes_not_enveloped() {
    let dir = std::env::temp_dir().join(format!(
        "bcs-api-http-manifest-{}",
        std::process::id()
    ));
    std::fs::create_dir_all(&dir).expect("mkdir");
    let file_path = dir.join("index.umd.js");
    std::fs::write(&file_path, b"console.log('panel');").expect("write");
    let manifest = ManifestConfig {
        schema_version: 1,
        bundles: vec![ManifestBundleConfig {
            name: "bcsPanel".to_string(),
            source_type: None,
            url: None,
            file: Some(file_path.to_string_lossy().to_string()),
        }],
    };
    let app = test_router(manifest);
    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/assets/bcsPanel/index.umd.js")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers().get("content-type").unwrap(),
        "application/javascript; charset=utf-8"
    );
    let bytes = body_bytes(response).await;
    assert_eq!(&bytes[..], b"console.log('panel');");
    let _ = std::fs::remove_dir_all(&dir);
}

// --- Noop dependencies required by ApiState::new ---------------------------

struct NoopGroupService;

#[async_trait]
impl GroupService for NoopGroupService {
    async fn list_groups(&self, _: ListGroups) -> Result<Page<GroupSummary>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create(&self, _: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn get(&self, _: GetGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update(&self, _: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete(&self, _: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn add_participant(
        &self,
        _: AddGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update_participant(
        &self,
        _: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_participant(
        &self,
        _: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopSessionService;

#[async_trait]
impl SessionService for NoopSessionService {
    async fn create(&self, _: CreateSession) -> Result<CreateSessionOutcome, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn list(&self, _: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn get(&self, _: GetSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update(&self, _: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete(&self, _: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn complete(&self, _: CompleteSession) -> Result<SessionCompletionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn collect(&self, _: CollectSession) -> Result<SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn uncollect(
        &self,
        _: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn add_participant(
        &self,
        _: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update_participant(
        &self,
        _: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_participant(
        &self,
        _: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopMessageService;

#[async_trait]
impl SessionMessageService for NoopMessageService {
    async fn list(
        &self,
        _: ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create_session_invitation(
        &self,
        _: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn accept_invitation(
        &self,
        _: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_bot_friendships(
        &self,
        _: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_bot_friendship(
        &self,
        _: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create_bot_friend_request(
        &self,
        _: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn list_bot_friend_requests(
        &self,
        _: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn accept_friend_request(
        &self,
        _: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn reject_friend_request(
        &self,
        _: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }

    async fn register_bot(
        &self,
        _command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }
}