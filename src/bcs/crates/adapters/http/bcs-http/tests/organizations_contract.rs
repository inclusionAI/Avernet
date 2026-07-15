use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use async_trait::async_trait;
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_domain::{Organization, OrganizationMember};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    CreateOrganizationCommand, OrganizationAuth, OrganizationCandidateBot,
    OrganizationCandidateQuery, OrganizationManagementService, OrganizationMemberPage,
    OrganizationMemberAuth, OrganizationMemberBotDetail, OrganizationMemberDetail,
    OrganizationMemberPageQuery, OrganizationMemberProfile,
    PutOrganizationMemberCommand, ServiceError, ServiceResult, UpdateOrganizationCommand,
    UpdateOrganizationMemberProfileCommand,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tokio::sync::Mutex;
use tower::ServiceExt;

struct TestApp {
    app: Router,
    recording: Arc<RecordingOrganizationManagement>,
}

fn test_app() -> TestApp {
    let recording = Arc::new(RecordingOrganizationManagement::default());
    let services = Services::builder()
        .organization_management(recording.clone())
        .build_for_test();
    TestApp {
        app: build_router(HttpAppState::new(services)),
        recording,
    }
}

#[derive(Default)]
struct RecordingOrganizationManagement {
    calls: Mutex<Vec<String>>,
    next_error: Mutex<Option<ServiceError>>,
    member_bot_missing: AtomicBool,
    member_missing: AtomicBool,
}

impl RecordingOrganizationManagement {
    async fn fail_next(&self, error: ServiceError) {
        *self.next_error.lock().await = Some(error);
    }

    async fn maybe_fail(&self) -> ServiceResult<()> {
        if let Some(error) = self.next_error.lock().await.take() {
            Err(error)
        } else {
            Ok(())
        }
    }

    async fn record(&self, call: impl Into<String>) -> ServiceResult<()> {
        self.calls.lock().await.push(call.into());
        self.maybe_fail().await
    }
}

#[async_trait]
impl OrganizationManagementService for RecordingOrganizationManagement {
    async fn create(&self, command: CreateOrganizationCommand) -> ServiceResult<Organization> {
        self.record(format!("create:{}:{}", command.auth.provider_id, command.organization_code)).await?;
        Ok(sample_org(command.auth.provider_id, command.organization_code))
    }

    async fn get(&self, auth: OrganizationAuth, code: &str) -> ServiceResult<Organization> {
        self.record(format!("get:{}:{code}", auth.provider_id)).await?;
        Ok(sample_org(auth.provider_id, code.to_string()))
    }

    async fn list(
        &self,
        auth: OrganizationAuth,
        include_disabled: bool,
    ) -> ServiceResult<Vec<Organization>> {
        self.record(format!("list:{}:{include_disabled}", auth.provider_id)).await?;
        Ok(vec![sample_org(auth.provider_id, "promo-2026".to_string())])
    }

    async fn update(&self, command: UpdateOrganizationCommand) -> ServiceResult<Organization> {
        self.record(format!("update:{}:{}", command.auth.provider_id, command.organization_code)).await?;
        Ok(sample_org(command.auth.provider_id, command.organization_code))
    }

    async fn put_member(
        &self,
        command: PutOrganizationMemberCommand,
    ) -> ServiceResult<OrganizationMember> {
        self.record(format!("put_member:{}:{}:{}", command.auth.provider_admin_token, command.organization_code, command.bot_uuid)).await?;
        Ok(sample_member(command.organization_code, command.bot_uuid))
    }

    async fn delete_member(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()> {
        self.record(format!("delete_member:{}:{organization_code}:{bot_uuid}", auth.provider_admin_token)).await
    }

    async fn get_member(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>> {
        self.record(format!("get_member:{}:{organization_code}:{bot_uuid}", auth.provider_admin_token)).await?;
        Ok(Some(sample_member(organization_code.to_string(), bot_uuid.to_string())))
    }

    async fn get_member_detail(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMemberDetail>> {
        self.record(format!("get_member_detail:{}:{organization_code}:{bot_uuid}", auth.provider_admin_token)).await?;
        if self.member_missing.load(Ordering::Relaxed) {
            return Ok(None);
        }
        Ok(Some(OrganizationMemberDetail {
            member: sample_member(organization_code.to_string(), bot_uuid.to_string()),
            bot: (!self.member_bot_missing.load(Ordering::Relaxed)).then(sample_member_bot_detail),
        }))
    }

    async fn require_invocable_member(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember> {
        self.record(format!(
            "require_invocable_member:{}:{organization_code}:{bot_uuid}",
            auth.provider_id
        ))
        .await?;
        Ok(sample_member(
            organization_code.to_string(),
            bot_uuid.to_string(),
        ))
    }

    async fn list_members(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        include_disabled: bool,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        self.record(format!("list_members:{}:{organization_code}:{include_disabled}:{:?}", auth.provider_admin_token, role)).await?;
        Ok(vec![sample_member(organization_code.to_string(), "bot-b".to_string())])
    }

    async fn list_members_page(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        query: OrganizationMemberPageQuery,
    ) -> ServiceResult<OrganizationMemberPage> {
        self.record(format!(
            "list_members_page:{}:{organization_code}:{}:{:?}:{}:{}",
            auth.provider_admin_token,
            query.include_disabled,
            query.role,
            query.offset,
            query.limit,
        ))
        .await?;
        Ok(OrganizationMemberPage {
            members: if query.offset == 0 {
                vec![sample_member(organization_code.to_string(), "bot-b".to_string())]
            } else {
                Vec::new()
            },
            total: 1,
            offset: query.offset,
            limit: query.limit,
        })
    }

    async fn update_member_profile(
        &self,
        command: UpdateOrganizationMemberProfileCommand,
    ) -> ServiceResult<OrganizationMemberProfile> {
        self.record(format!(
            "update_member_profile:{}:{}:{}",
            command.auth.provider_admin_token,
            command.organization_code,
            command.bot_uuid,
        ))
        .await?;
        Ok(OrganizationMemberProfile {
            organization_code: command.organization_code,
            bot_uuid: command.bot_uuid,
            provider_id: "provider-b".to_string(),
            capabilities: bcs_service_api::BotCapabilities {
                name: command.patch.name,
                summary: command.patch.summary,
                domains: command.patch.domains.unwrap_or_default(),
                skills: command.patch.skills.unwrap_or_default(),
                scopes: command.patch.scopes.unwrap_or_default(),
                ..bcs_service_api::BotCapabilities::default()
            },
        })
    }

    async fn candidate_bots(
        &self,
        auth: OrganizationAuth,
        query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>> {
        self.record(format!("candidate_bots:{}:{:?}", auth.provider_id, query.q)).await?;
        Ok(vec![OrganizationCandidateBot {
            bot_uuid: "bot-b".to_string(),
            provider_id: "provider-b".to_string(),
            capabilities: bcs_service_api::BotCapabilities::default(),
        }])
    }
}

fn sample_org(provider_id: String, code: String) -> Organization {
    Organization {
        env: "local".to_string(),
        code,
        name: "Promo 2026".to_string(),
        description: Some("campaign".to_string()),
        managing_provider_id: provider_id,
        disabled: false,
        created_at: 1,
        updated_at: 2,
    }
}

fn sample_member(organization_code: String, bot_uuid: String) -> OrganizationMember {
    OrganizationMember {
        env: "local".to_string(),
        organization_code,
        bot_uuid,
        role: Some("traffic".to_string()),
        disabled: false,
        created_at: 1,
        updated_at: 2,
    }
}

fn sample_member_bot_detail() -> OrganizationMemberBotDetail {
    OrganizationMemberBotDetail {
        provider_id: "provider-b".to_string(),
        provider_bot_ref: "provider-b-ref".to_string(),
        agent_code: Some("agent-code-b".to_string()),
        capabilities: bcs_service_api::BotCapabilities {
            name: Some("Bot B".to_string()),
            summary: Some("Reviews code".to_string()),
            domains: vec!["engineering".to_string()],
            skills: vec![bcs_service_api::Skill::with_description(
                "code_review",
                "Review changes",
            )],
            scopes: vec!["source_code".to_string()],
            visibility: "protected".to_string(),
            agent_code: Some("must-not-leak-from-capabilities".to_string()),
            agent_token: Some("must-not-leak".to_string()),
            ..bcs_service_api::BotCapabilities::default()
        },
        created_by: Some("yuange".to_string()),
        actor_kind: bcs_service_api::ActorKind::Bot,
        env: Some("prod".to_string()),
    }
}

#[tokio::test]
async fn get_member_returns_flat_bot_detail_without_credentials_or_status() {
    let app = test_app();
    let response = request(
        &app.app,
        "GET",
        "/organizations/promo-2026/members/bot-b",
        Some("provider-token"),
        None,
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response_json(response).await,
        json!({
            "organization_code": "promo-2026",
            "bot_uuid": "bot-b",
            "role": "traffic",
            "disabled": false,
            "bot": {
                "provider_id": "provider-b",
                "provider_bot_ref": "provider-b-ref",
                "agent_code": "agent-code-b",
                "name": "Bot B",
                "summary": "Reviews code",
                "domains": ["engineering"],
                "skills": [{"name": "code_review", "description": "Review changes"}],
                "scopes": ["source_code"],
                "visibility": "protected",
                "created_by": "yuange",
                "actor_kind": "bot",
                "env": "prod"
            }
        })
    );
}

#[tokio::test]
async fn get_member_returns_null_bot_when_registered_bot_is_missing() {
    let app = test_app();
    app.recording.member_bot_missing.store(true, Ordering::Relaxed);

    let response = request(
        &app.app,
        "GET",
        "/organizations/promo-2026/members/bot-b",
        Some("provider-token"),
        None,
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let json = response_json(response).await;
    assert!(json.get("bot").is_some(), "response must include the bot field");
    assert_eq!(json["bot"], Value::Null);
}

#[tokio::test]
async fn get_member_returns_not_found_when_member_is_missing() {
    let app = test_app();
    app.recording.member_missing.store(true, Ordering::Relaxed);

    let response = request(
        &app.app,
        "GET",
        "/organizations/promo-2026/members/missing",
        Some("provider-token"),
        None,
    )
    .await;

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn provider_scoped_organization_routes_call_application_service() {
    let app = test_app();
    let cases = [
        ("POST", "/providers/provider-a/organizations", Some(json!({"organization_code":"promo-2026","name":"Promo 2026","description":"campaign"})), StatusCode::OK),
        ("GET", "/providers/provider-a/organizations/promo-2026", None, StatusCode::OK),
        ("GET", "/providers/provider-a/organizations?include_disabled=true", None, StatusCode::OK),
        ("PATCH", "/providers/provider-a/organizations/promo-2026", Some(json!({"name":"Promo 2026 updated","description":null,"disabled":false})), StatusCode::OK),
        ("PUT", "/organizations/promo-2026/members/bot-b", Some(json!({"role":"traffic"})), StatusCode::OK),
        ("DELETE", "/organizations/promo-2026/members/bot-b", None, StatusCode::NO_CONTENT),
        ("GET", "/organizations/promo-2026/members", None, StatusCode::OK),
        ("GET", "/organizations/promo-2026/members/bot-b", None, StatusCode::OK),
        ("GET", "/providers/provider-a/organization-candidate-bots?q=traffic", None, StatusCode::OK),
    ];

    for (method, uri, body, expected_status) in cases {
        let response = request(&app.app, method, uri, Some("provider-token"), body).await;
        assert_eq!(response.status(), expected_status, "{method} {uri}");
    }
}

#[tokio::test]
async fn provider_prefixed_member_routes_are_removed() {
    let app = test_app();
    for (method, uri, body) in [
        (
            "PUT",
            "/providers/provider-a/organizations/promo-2026/members/bot-b",
            Some(json!({"role": "traffic"})),
        ),
        (
            "DELETE",
            "/providers/provider-a/organizations/promo-2026/members/bot-b",
            None,
        ),
        (
            "GET",
            "/providers/provider-a/organizations/promo-2026/members",
            None,
        ),
        (
            "GET",
            "/providers/provider-a/organizations/promo-2026/members/bot-b",
            None,
        ),
    ] {
        let response = request(&app.app, method, uri, Some("provider-token"), body).await;
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{method} {uri}");
    }
}

#[tokio::test]
async fn patch_member_profile_accepts_supported_fields() {
    let app = test_app();
    let response = request(
        &app.app,
        "PATCH",
        "/organizations/promo-2026/members/bot-b/profile",
        Some("provider-token"),
        Some(json!({
            "name": "Updated Bot",
            "summary": "Updated summary",
            "domains": ["engineering"],
            "skills": [
                {"name": "code_review", "description": "Reviews code"},
                {"name": "sql_analysis", "description": "Analyzes SQL"}
            ],
            "scopes": ["production"]
        })),
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["organization_code"], "promo-2026");
    assert_eq!(body["bot_uuid"], "bot-b");
    assert_eq!(body["provider_id"], "provider-b");
    assert_eq!(body["profile"]["name"], "Updated Bot");
    assert_eq!(body["profile"]["skills"][1]["name"], "sql_analysis");
    assert!(body.get("capabilities").is_none());
}

#[tokio::test]
async fn patch_member_profile_rejects_empty_unknown_and_legacy_skill_shapes() {
    let app = test_app();
    for body in [
        json!({}),
        json!({"visibility": "public"}),
        json!({"skills": ["code_review"]}),
    ] {
        let response = request(
            &app.app,
            "PATCH",
            "/organizations/promo-2026/members/bot-b/profile",
            Some("provider-token"),
            Some(body),
        )
        .await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
}

#[tokio::test]
async fn patch_member_profile_requires_admin_token_and_maps_service_errors() {
    let app = test_app();
    let uri = "/organizations/promo-2026/members/bot-b/profile";
    let body = Some(json!({"name": "Updated Bot"}));

    let missing_token = request(&app.app, "PATCH", uri, None, body.clone()).await;
    assert_eq!(missing_token.status(), StatusCode::UNAUTHORIZED);

    for (error, expected_status) in [
        (
            ServiceError::Forbidden("organization_member_disabled".to_string()),
            StatusCode::FORBIDDEN,
        ),
        (
            ServiceError::BotNotFound("bot-b".to_string()),
            StatusCode::NOT_FOUND,
        ),
        (
            ServiceError::InternalError("database write failed".to_string()),
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    ] {
        app.recording.fail_next(error).await;
        let response = request(
            &app.app,
            "PATCH",
            uri,
            Some("provider-token"),
            body.clone(),
        )
        .await;
        assert_eq!(response.status(), expected_status);
    }
}

#[tokio::test]
async fn candidate_bots_returns_requested_page_metadata() {
    let app = test_app();
    let response = request(
        &app.app,
        "GET",
        "/providers/provider-a/organization-candidate-bots?q=traffic&offset=10&limit=25",
        Some("provider-token"),
        None,
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    let json = response_json(response).await;
    assert_eq!(json["offset"], 10);
    assert_eq!(json["limit"], 25);
    assert_eq!(json["total"], 1);
    assert_eq!(json["bots"], json!([]));
}

#[tokio::test]
async fn pagination_returns_requested_page_metadata() {
    let app = test_app();
    let response = request(
        &app.app,
        "GET",
        "/organizations/promo-2026/members?offset=10&limit=25",
        Some("provider-token"),
        None,
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response_json(response).await,
        json!({
            "members": [],
            "offset": 10,
            "limit": 25,
            "total": 1
        })
    );
}

#[tokio::test]
async fn pagination_defaults_to_first_page_of_fifty() {
    let app = test_app();
    let response = request(
        &app.app,
        "GET",
        "/organizations/promo-2026/members",
        Some("provider-token"),
        None,
    )
    .await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response_json(response).await,
        json!({
            "members": [{
                "organization_code": "promo-2026",
                "bot_uuid": "bot-b",
                "role": "traffic",
                "disabled": false
            }],
            "offset": 0,
            "limit": 50,
            "total": 1
        })
    );
    assert_eq!(
        app.recording.calls.lock().await.as_slice(),
        ["list_members_page:provider-token:promo-2026:false:None:0:50"]
    );
}

#[tokio::test]
async fn pagination_rejects_invalid_limit() {
    let app = test_app();
    for uri in [
        "/organizations/promo-2026/members?limit=0",
        "/organizations/promo-2026/members?limit=201",
    ] {
        let response = request(&app.app, "GET", uri, Some("provider-token"), None).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{uri}");
    }
    assert!(app.recording.calls.lock().await.is_empty());
}

#[tokio::test]
async fn member_page_service_contract_applies_page_query() {
    let app = test_app();
    let page = app
        .recording
        .list_members_page(
            OrganizationMemberAuth {
                provider_admin_token: "provider-token".to_string(),
            },
            "promo-2026",
            OrganizationMemberPageQuery {
                include_disabled: false,
                role: Some("traffic".to_string()),
                offset: 10,
                limit: 25,
            },
        )
        .await
        .unwrap();

    assert!(page.members.is_empty());
    assert_eq!(page.offset, 10);
    assert_eq!(page.limit, 25);
    assert_eq!(page.total, 1);
    assert_eq!(
        app.recording.calls.lock().await.as_slice(),
        ["list_members_page:provider-token:promo-2026:false:Some(\"traffic\"):10:25"]
    );
}

#[tokio::test]
async fn provider_scoped_organization_routes_reject_missing_bearer_token() {
    let app = test_app();
    let response = request(
        &app.app,
        "GET",
        "/providers/provider-a/organizations/promo-2026",
        None,
        None,
    )
    .await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn provider_scoped_organization_routes_map_service_errors() {
    let app = test_app();
    let cases = [
        (ServiceError::Unauthorized("bad token".to_string()), StatusCode::UNAUTHORIZED),
        (ServiceError::Forbidden("wrong provider".to_string()), StatusCode::FORBIDDEN),
        (ServiceError::InvalidOperation { message: "invalid code".to_string(), request_id: None }, StatusCode::BAD_REQUEST),
        (ServiceError::BotNotFound("bot-b".to_string()), StatusCode::NOT_FOUND),
        (ServiceError::ProviderNotFound("provider-a".to_string()), StatusCode::NOT_FOUND),
        (ServiceError::Conflict("duplicate".to_string()), StatusCode::CONFLICT),
        (ServiceError::InternalError("db failed".to_string()), StatusCode::INTERNAL_SERVER_ERROR),
    ];

    for (error, expected_status) in cases {
        app.recording.fail_next(error).await;
        let response = request(
            &app.app,
            "GET",
            "/providers/provider-a/organizations/promo-2026",
            Some("provider-token"),
            None,
        )
        .await;
        assert_eq!(response.status(), expected_status);
    }
}

async fn request(
    app: &Router,
    method: &str,
    uri: &str,
    bearer: Option<&str>,
    body: Option<Value>,
) -> axum::response::Response {
    let mut builder = Request::builder().method(method).uri(uri);
    if let Some(token) = bearer {
        builder = builder.header("authorization", format!("Bearer {token}"));
    }
    let body = match body {
        Some(value) => {
            builder = builder.header("content-type", "application/json");
            Body::from(value.to_string())
        }
        None => Body::empty(),
    };
    app.clone().oneshot(builder.body(body).unwrap()).await.unwrap()
}

#[allow(dead_code)]
async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    serde_json::from_slice(&bytes).unwrap()
}
