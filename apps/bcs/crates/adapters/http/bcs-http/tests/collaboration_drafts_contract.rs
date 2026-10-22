use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_app_collaboration_draft::CollaborationDraftApplication;
use bcs_bot::BotCore;
use bcs_collaboration_draft::CollaborationDraftCore;
use bcs_collaboration_store::{MemoryCollaborationStore, draft::DbCollaborationDraftStore};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, JudgeDecision, JudgeEvaluatorPort, JudgeRequest,
    ServiceResult,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use std::sync::Arc;
use tower::ServiceExt;

const ID: &str = "750c52c6-60e2-4249-8ee5-1fb8cb9402ab";

#[test]
fn published_draft_contract_resolves_schemas_and_declares_all_operations() {
    let contract: Value = serde_yaml::from_str(include_str!(
        "../../../../../api-contracts/collaboration-drafts/v1.yaml"
    ))
    .unwrap();
    let shared: Value = serde_yaml::from_str(include_str!(
        "../../../../../api-contracts/v1/openapi/collaboration-definitions.yaml"
    ))
    .unwrap();
    fn references(value: &Value, contract: &Value, shared: &Value) {
        if let Some(reference) = value.get("$ref").and_then(Value::as_str) {
            let (file, pointer) = reference.split_once('#').unwrap();
            let target = match file {
                "" => contract,
                "../v1/openapi/collaboration-definitions.yaml" => shared,
                _ => panic!("unexpected contract dependency"),
            };
            assert!(target.pointer(pointer).is_some(), "unresolved {reference}");
        }
        match value {
            Value::Object(map) => map.values().for_each(|v| references(v, contract, shared)),
            Value::Array(values) => values.iter().for_each(|v| references(v, contract, shared)),
            _ => {}
        }
    }
    references(&contract, &contract, &shared);
    let count = contract["paths"]
        .as_object()
        .unwrap()
        .values()
        .flat_map(|path| path.as_object().unwrap().iter())
        .filter(|(method, _)| ["get", "put", "post", "delete"].contains(&method.as_str()))
        .count();
    assert_eq!(count, 8);
    assert_eq!(
        contract["components"]["schemas"]["Reference"]["required"],
        json!(["schemaVersion", "draftId", "revision", "readToken"])
    );
}

struct UnusedJudge;
#[async_trait::async_trait]
impl JudgeEvaluatorPort for UnusedJudge {
    async fn judge(&self, _: JudgeRequest) -> ServiceResult<JudgeDecision> {
        panic!("preview must not execute");
    }
}
async fn app() -> (Router, tempfile::TempDir) {
    let dir = tempfile::tempdir().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(dir.path().into()));
    for (id, token) in [("owner", "owner-token"), ("other", "other-token")] {
        registry
            .register_with_owner_and_token(id.into(), BotCapabilities::default(), "human", token)
            .await
            .unwrap();
    }
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    for sql in include_str!("../../../../../migrations/sqlite/036_collaboration_drafts.sql")
        .split(';')
        .filter(|s| !s.trim().is_empty())
    {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    let mut services = Services::builder().registry(registry).build_for_test();
    let memory = Arc::new(MemoryCollaborationStore::new());
    let validator = Arc::new(bcs_collaboration_runtime::CollaborationRuntime::new(
        memory.clone(),
        memory.clone(),
        memory.clone(),
        memory,
        services.group.clone(),
        services.session_management.clone(),
        services.bot_delivery.clone(),
        Arc::new(UnusedJudge),
    ));
    let core = Arc::new(CollaborationDraftCore::new(
        Arc::new(DbCollaborationDraftStore::new(db)),
    ));
    services.collaboration_drafts = Some(Arc::new(CollaborationDraftApplication::new(
        core, validator, true,
    )));
    (build_router(HttpAppState::new(services)), dir)
}

async fn call(
    app: &Router,
    method: &str,
    suffix: &str,
    bot: Option<&str>,
    grant: Option<&str>,
    body: Option<Value>,
) -> (StatusCode, Value) {
    let mut request = Request::builder()
        .method(method)
        .uri(format!("/collaboration/drafts/{ID}{suffix}"));
    if let Some(bot) = bot {
        request = request.header("authorization", format!("Bearer {bot}"));
    }
    if let Some(grant) = grant {
        request = request.header("x-bcs-draft-read-token", grant);
    }
    let body = match body {
        Some(v) => {
            request = request.header("content-type", "application/json");
            Body::from(v.to_string())
        }
        None => Body::empty(),
    };
    let response = app
        .clone()
        .oneshot(request.body(body).unwrap())
        .await
        .unwrap();
    assert_eq!(response.headers()["cache-control"], "no-store");
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 1_000_000).await.unwrap();
    let value = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap()
    };
    (status, value)
}

#[tokio::test]
async fn disabled_draft_service_reports_unavailable_without_affecting_router() {
    let app = build_router(HttpAppState::new(Services::builder().build_for_test()));
    for (method, suffix) in [("GET", "/revisions/1"), ("POST", "/revisions/1/validate")] {
        let (status, body) = call(&app, method, suffix, None, Some("test-capability"), None).await;
        assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(body["error"], "DRAFT_SERVICE_UNAVAILABLE");
    }
}

#[tokio::test]
async fn complete_fixed_version_http_contract_and_read_capability_precedence() {
    let (app, _dir) = app().await;
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../../../tests/fixtures/collaboration-definition-draft/indentation-regression.json"
    ))
    .unwrap();
    let body = json!({"definition_yaml":fixture["revision"]["definition_yaml"],"title":"现场回归"});
    assert_eq!(
        call(&app, "PUT", "", None, None, Some(body.clone()))
            .await
            .0,
        StatusCode::UNAUTHORIZED
    );
    let (status, saved) = call(
        &app,
        "PUT",
        "",
        Some("owner-token"),
        None,
        Some(body.clone()),
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(saved["size_bytes"], 2174);
    assert_eq!(saved["created_by"], "owner");
    assert_eq!(
        call(
            &app,
            "PUT",
            "",
            Some("owner-token"),
            None,
            Some(body.clone())
        )
        .await
        .0,
        StatusCode::OK
    );
    assert_eq!(
        call(
            &app,
            "PUT",
            "",
            Some("other-token"),
            None,
            Some(body.clone())
        )
        .await
        .0,
        StatusCode::FORBIDDEN
    );
    let (_, reference) = call(
        &app,
        "GET",
        "/revisions/1/reference",
        Some("owner-token"),
        None,
        None,
    )
    .await;
    assert_eq!(reference.as_object().unwrap().len(), 4);
    let token = reference["readToken"].as_str().unwrap();
    assert_eq!(token.len(), 43);
    let (status, read) = call(&app, "GET", "/revisions/1", None, Some(token), None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(read, saved);
    assert!(read.get("share_nonce").is_none());
    assert!(read.get("read_token").is_none());
    let (status, validation) = call(
        &app,
        "POST",
        "/revisions/1/validate",
        None,
        Some(token),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(validation["validation"]["valid"], true);
    assert_eq!(validation["content_hash"], saved["content_hash"]);
    assert_eq!(
        call(
            &app,
            "POST",
            "/revisions/1/validate",
            None,
            Some(token),
            Some(json!({"definition_yaml":"substitute"}))
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    // Bad read token cannot fall back to a valid owner credential.
    assert_eq!(
        call(
            &app,
            "GET",
            "/revisions/1",
            Some("owner-token"),
            Some("bad"),
            None
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    for (method, suffix) in [
        ("GET", ""),
        ("GET", "/revisions/1/reference"),
        ("DELETE", ""),
        ("DELETE", "/revisions/1/share"),
    ] {
        assert_eq!(
            call(&app, method, suffix, Some("owner-token"), Some(token), None)
                .await
                .0,
            StatusCode::FORBIDDEN
        );
    }
    let mut forged = body.clone();
    forged["owner_bot_id"] = json!("credential-that-must-not-be-echoed");
    let (status, error) = call(&app, "PUT", "", Some("owner-token"), None, Some(forged)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(error, json!({"error":"DRAFT_INPUT"}));
    let update = json!({"definition_yaml":"invalid: [","expected_revision":1});
    assert_eq!(
        call(
            &app,
            "POST",
            "/revisions",
            Some("owner-token"),
            None,
            Some(update.clone())
        )
        .await
        .0,
        StatusCode::OK
    );
    assert_eq!(
        call(
            &app,
            "POST",
            "/revisions",
            Some("owner-token"),
            None,
            Some(update)
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    assert_eq!(
        call(&app, "GET", "/revisions/2", None, Some(token), None)
            .await
            .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(
            &app,
            "DELETE",
            "/revisions/1/share",
            Some("owner-token"),
            None,
            None
        )
        .await
        .0,
        StatusCode::NO_CONTENT
    );
    assert_eq!(
        call(
            &app,
            "DELETE",
            "/revisions/1/share",
            Some("owner-token"),
            None,
            None
        )
        .await
        .0,
        StatusCode::NO_CONTENT
    );
    assert_eq!(
        call(&app, "GET", "/revisions/1", None, Some(token), None)
            .await
            .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(&app, "GET", "/revisions/1", Some("owner-token"), None, None)
            .await
            .0,
        StatusCode::OK
    );
    assert_eq!(
        call(
            &app,
            "GET",
            "/revisions/1/reference",
            Some("owner-token"),
            None,
            None
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(&app, "DELETE", "", Some("owner-token"), None, None)
            .await
            .0,
        StatusCode::NO_CONTENT
    );
    assert_eq!(
        call(&app, "PUT", "", Some("owner-token"), None, Some(body))
            .await
            .0,
        StatusCode::NOT_FOUND
    );
}
