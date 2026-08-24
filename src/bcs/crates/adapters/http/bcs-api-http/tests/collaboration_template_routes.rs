#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::*;
use bcs_service_api::application::collaboration_template::CollaborationTemplateParticipantSummary;
use bcs_service_api::{
    CollaborationTemplateDetail, CollaborationTemplateFormat, CollaborationTemplateListResponse,
    CollaborationTemplateSummary,
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
struct FakeTemplateService {
    last_list: Mutex<Option<ListCollaborationTemplates>>,
    last_get: Mutex<Option<GetCollaborationTemplate>>,
    missing_id: Option<String>,
}

#[async_trait]
impl CollaborationTemplateService for FakeTemplateService {
    async fn list_templates(
        &self,
        command: ListCollaborationTemplates,
    ) -> Result<CollaborationTemplateListResponse, ApplicationError> {
        *self.last_list.lock().expect("list lock") = Some(command);
        let mut participants = BTreeMap::new();
        participants.insert(
            "driver".to_string(),
            CollaborationTemplateParticipantSummary {
                display_name: Some("Driver".to_string()),
                description: None,
                required: true,
            },
        );
        let summary = CollaborationTemplateSummary {
            id: "judge_review".to_string(),
            name: "Judge Review".to_string(),
            description: "A manager-worker review template".to_string(),
            participants,
            tags: vec!["review".to_string()],
            priority: 0,
            available_languages: vec!["en".to_string(), "zh-CN".to_string()],
        };
        Ok(CollaborationTemplateListResponse {
            templates: vec![summary],
            tag_labels: BTreeMap::new(),
            default_language: "en".to_string(),
            supported_languages: vec!["en".to_string(), "zh-CN".to_string()],
        })
    }

    async fn get_template(
        &self,
        query: GetCollaborationTemplate,
    ) -> Result<CollaborationTemplateDetail, ApplicationError> {
        *self.last_get.lock().expect("get lock") = Some(query.clone());
        if self.missing_id.as_deref() == Some(&query.template_id) {
            return Err(ApplicationError::not_found(
                "template_not_found",
                "Template not found",
            ));
        }
        Ok(CollaborationTemplateDetail {
            id: "judge_review".to_string(),
            lang: query.requested_language.unwrap_or("en".to_string()),
            name: "Judge Review".to_string(),
            yaml: "version: 1\nkind: manager_worker\n".to_string(),
            definition: json!({}),
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

fn test_router(service: Arc<FakeTemplateService>) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier),
        )
        .with_collaboration_template_service(service),
    )
}

#[tokio::test]
async fn list_templates_returns_envelope_and_forwards_filters() {
    let service = Arc::new(FakeTemplateService::default());
    let app = test_router(service.clone());

    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/templates?lang=zh-CN&tags=review%2Cgeneral")
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-template")
        .header("accept-language", "zh-CN")
        .body(Body::from(Value::Null.to_string()))
        .expect("request");
    let response = app.oneshot(request).await.expect("list response");
    assert_eq!(response.status(), StatusCode::OK);

    let body = response_json(response).await;
    assert_eq!(body["code"], 20000);
    assert_eq!(body["message"], "OK");
    assert_eq!(body["data"]["default_language"], "en");
    assert_eq!(body["data"]["templates"][0]["id"], "judge_review");
    assert_eq!(body["data"]["templates"][0]["participants"]["driver"]["required"], true);

    let recorded = service
        .last_list
        .lock()
        .expect("list lock")
        .clone()
        .expect("list command recorded");
    assert_eq!(recorded.requested_language.as_deref(), Some("zh-CN"));
    assert_eq!(recorded.accept_language.as_deref(), Some("zh-CN"));
    assert_eq!(recorded.tags, vec!["review".to_string(), "general".to_string()]);
}

#[tokio::test]
async fn get_template_returns_raw_yaml_by_default() {
    let service = Arc::new(FakeTemplateService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/templates/judge_review",
            Value::Null,
        ))
        .await
        .expect("get response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers().get("content-type").unwrap(),
        "text/yaml; charset=utf-8"
    );
    assert_eq!(response.headers().get("x-template-id").unwrap(), "judge_review");
    assert_eq!(response.headers().get("x-template-lang").unwrap(), "en");

    let body = response_text(response).await;
    assert!(body.starts_with("version: 1"));

    let recorded = service
        .last_get
        .lock()
        .expect("get lock")
        .clone()
        .expect("get command recorded");
    assert_eq!(recorded.format, CollaborationTemplateFormat::Yaml);
}

#[tokio::test]
async fn get_template_returns_json_envelope_when_requested() {
    let service = Arc::new(FakeTemplateService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/templates/judge_review?format=json",
            Value::Null,
        ))
        .await
        .expect("get json response");
    assert_eq!(response.status(), StatusCode::OK);

    let body = response_json(response).await;
    assert_eq!(body["code"], 20000);
    assert_eq!(body["data"]["id"], "judge_review");
    assert_eq!(body["data"]["yaml"], "version: 1\nkind: manager_worker\n");
}

#[tokio::test]
async fn get_template_not_found_maps_to_404() {
    let service = Arc::new(FakeTemplateService {
        missing_id: Some("missing".to_string()),
        ..FakeTemplateService::default()
    });
    let app = test_router(service);

    let response = app
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/templates/missing?format=json",
            Value::Null,
        ))
        .await
        .expect("not found response");
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "template_not_found");
}

#[tokio::test]
async fn template_routes_require_a_gateway_principal() {
    let service = Arc::new(FakeTemplateService::default());
    let app = test_router(service);

    let request = Request::builder()
        .method("GET")
        .uri("/api/v1/collaboration/templates")
        .header("content-type", "application/json")
        .body(Body::empty())
        .expect("request");
    let response = app.oneshot(request).await.expect("unauthenticated response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

fn request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-template")
        .body(Body::from(body.to_string()))
        .expect("request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

async fn response_text(response: axum::response::Response) -> String {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    String::from_utf8(bytes.to_vec()).expect("UTF-8 response")
}