#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::*;
use bcs_service_api::{
    CollaborationDefinitionValidationDiagnostic, CollaborationDefinitionValidationOutcome,
    CollaborationDefinitionValidationSummary,
};
use serde_json::{Value, json};
use tower::ServiceExt;

struct HeaderVerifier;

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(AuthenticatedCaller {
                tenant: Some("tenant-1".into()),
                user: Some(AuthenticatedUserIdentity {
                    id: "staff-1".to_string(),
                    username: "staff-1".to_string(),
                    display_name: None,
                    full_name: None,
                }),
                bot: None,
                app: None,
                access_key: None,
            })
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

#[derive(Default)]
struct FakeDefinitionService {
    last: Mutex<Option<ValidateCollaborationDefinition>>,
    invalid: bool,
}

#[async_trait]
impl CollaborationDefinitionService for FakeDefinitionService {
    async fn validate_definition_yaml(
        &self,
        command: ValidateCollaborationDefinition,
    ) -> Result<CollaborationDefinitionValidationOutcome, ApplicationError> {
        *self.last.lock().expect("command lock") = Some(command.clone());
        if self.invalid {
            return Ok(CollaborationDefinitionValidationOutcome {
                valid: false,
                errors: vec![CollaborationDefinitionValidationDiagnostic {
                    code: "invalid_schema".to_string(),
                    path: "/participants".to_string(),
                    message: "missing driver slot".to_string(),
                    hint: Some("add a driver participant".to_string()),
                }],
                warnings: Vec::new(),
                summary: CollaborationDefinitionValidationSummary::default(),
                participants: Vec::new(),
                graph: None,
                definition: None,
            });
        }
        Ok(CollaborationDefinitionValidationOutcome {
            valid: true,
            errors: Vec::new(),
            warnings: Vec::new(),
            summary: CollaborationDefinitionValidationSummary {
                participants: 1,
                nodes: 1,
                ..Default::default()
            },
            participants: Vec::new(),
            graph: None,
            definition: None,
        })
    }
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

fn test_router(service: Arc<FakeDefinitionService>) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier),
        )
        .with_collaboration_definition_service(service),
    )
}

fn request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-definition")
        .body(Body::from(body.to_string()))
        .expect("request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

#[tokio::test]
async fn validate_returns_envelope_and_forwards_definition_yaml() {
    let service = Arc::new(FakeDefinitionService::default());
    let app = test_router(service.clone());

    let yaml = "version: 1\nkind: manager_worker\n";
    let response = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/definitions/validate",
            json!({ "definition_yaml": yaml }),
        ))
        .await
        .expect("validate response");
    assert_eq!(response.status(), StatusCode::OK);

    let body = response_json(response).await;
    assert_eq!(body["code"], 20000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"]["valid"], true);
    assert_eq!(body["data"]["summary"]["participants"], 1);
    assert_eq!(body["data"]["summary"]["nodes"], 1);
    assert!(body["data"].get("definition").is_none(), "definition is skip-serialized");

    let recorded = service
        .last
        .lock()
        .expect("command lock")
        .clone()
        .expect("command recorded");
    assert_eq!(recorded.definition_yaml, yaml);
}

#[tokio::test]
async fn validate_returns_200_with_valid_false_for_invalid_yaml() {
    let service = Arc::new(FakeDefinitionService {
        invalid: true,
        ..FakeDefinitionService::default()
    });
    let app = test_router(service);

    // An invalid document is still 200 with valid:false — not an HTTP error.
    let response = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/definitions/validate",
            json!({ "definition_yaml": "broken: [" }),
        ))
        .await
        .expect("invalid response");
    assert_eq!(response.status(), StatusCode::OK);

    let body = response_json(response).await;
    assert_eq!(body["data"]["valid"], false);
    assert_eq!(body["data"]["errors"][0]["code"], "invalid_schema");
    assert_eq!(body["data"]["errors"][0]["path"], "/participants");
    assert_eq!(body["data"]["errors"][0]["hint"], "add a driver participant");
}

#[tokio::test]
async fn validate_missing_body_field_is_400() {
    let service = Arc::new(FakeDefinitionService::default());
    let app = test_router(service);

    let response = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/definitions/validate",
            json!({}),
        ))
        .await
        .expect("empty body response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}

#[tokio::test]
async fn validate_rejects_legacy_yaml_alias_by_design() {
    let service = Arc::new(FakeDefinitionService::default());
    let app = test_router(service);

    // ``{"yaml": ...}`` is the legacy alias; the V1 contract accepts only
    // ``definition_yaml`` and rejects the alias with 400 by design.
    let response = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/definitions/validate",
            json!({ "yaml": "version: 1\n" }),
        ))
        .await
        .expect("alias rejection response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}

#[tokio::test]
async fn validate_empty_string_yaml_is_400() {
    let service = Arc::new(FakeDefinitionService::default());
    let app = test_router(service);
    let response = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/definitions/validate",
            json!({ "definition_yaml": "" }),
        ))
        .await
        .expect("empty string response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}

#[tokio::test]
async fn validate_whitespace_only_yaml_is_400() {
    let service = Arc::new(FakeDefinitionService::default());
    let app = test_router(service);
    let response = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/definitions/validate",
            json!({ "definition_yaml": "   \n\t  " }),
        ))
        .await
        .expect("whitespace response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}

#[tokio::test]
async fn validate_requires_a_gateway_principal() {
    let service = Arc::new(FakeDefinitionService::default());
    let app = test_router(service);

    let request = Request::builder()
        .method("POST")
        .uri("/api/v1/collaboration/definitions/validate")
        .header("content-type", "application/json")
        .body(Body::from(json!({ "definition_yaml": "x" }).to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("unauthenticated response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}
