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
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::{Value, json};
use std::sync::Arc;
use tower::ServiceExt;

const ID: &str = "750c52c6-60e2-4249-8ee5-1fb8cb9402ab";

// Keep the deployed CLI's strict four-field decoder and base64 checks as a wire contract.
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PreviousCliReference {
    schema_version: u8,
    draft_id: String,
    revision: u64,
    read_token: String,
}

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
    body: Option<Value>,
) -> (StatusCode, Value) {
    let mut request = Request::builder()
        .method(method)
        .uri(format!("/collaboration/drafts/{ID}{suffix}"));
    if let Some(bot) = bot {
        request = request.header("authorization", format!("Bearer {bot}"));
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
        let (status, body) = call(&app, method, suffix, None, None).await;
        assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(body["error"], "DRAFT_SERVICE_UNAVAILABLE");
    }
}

#[tokio::test]
async fn complete_fixed_version_http_contract_without_read_credentials() {
    let (app, _dir) = app().await;
    let fixture: Value = serde_json::from_str(include_str!(
        "../../../../../tests/fixtures/collaboration-definition-draft/indentation-regression.json"
    ))
    .unwrap();
    let body = json!({"definition_yaml":fixture["revision"]["definition_yaml"],"title":"现场回归"});
    assert_eq!(
        call(&app, "PUT", "", None, Some(body.clone()))
            .await
            .0,
        StatusCode::UNAUTHORIZED
    );
    let (status, saved) = call(
        &app,
        "PUT",
        "",
        Some("owner-token"),
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
    )
    .await;
    let previous: PreviousCliReference = serde_json::from_value(reference.clone()).unwrap();
    assert_eq!((previous.schema_version, previous.draft_id.as_str(), previous.revision), (2, ID, 1));
    let decoded = URL_SAFE_NO_PAD.decode(&previous.read_token).unwrap();
    assert_eq!(decoded, [0; 32]);
    assert_eq!(URL_SAFE_NO_PAD.encode(decoded), previous.read_token);
    let (normalized, message) = bcs_cli::collaboration_draft::reference_message(reference, ID, 1).unwrap();
    assert_eq!(serde_json::to_value(normalized).unwrap(), json!({"schemaVersion":2,"draftId":ID,"revision":1}));
    assert!(!message.contains("readToken"));
    let (status, read) = call(&app, "GET", "/revisions/1", None, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(read, saved);
    assert!(read.get("read_token").is_none());
    let (status, validation) = call(
        &app,
        "POST",
        "/revisions/1/validate",
        None,
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
            Some(json!({"definition_yaml":"substitute"}))
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    for (method, suffix) in [
        ("GET", ""),
        ("GET", "/revisions/1/reference"),
        ("DELETE", ""),
        ("DELETE", "/revisions/1/share"),
    ] {
        assert_eq!(
            call(&app, method, suffix, None, None).await.0,
            StatusCode::UNAUTHORIZED
        );
        assert_eq!(
            call(&app, method, suffix, Some("other-token"), None)
                .await
                .0,
            StatusCode::FORBIDDEN
        );
    }
    let mut forged = body.clone();
    forged["owner_bot_id"] = json!("credential-that-must-not-be-echoed");
    let (status, error) = call(&app, "PUT", "", Some("owner-token"), Some(forged)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(error, json!({"error":"DRAFT_INPUT"}));
    let update = json!({"definition_yaml":"invalid: [","expected_revision":1});
    assert_eq!(
        call(
            &app,
            "POST",
            "/revisions",
            Some("owner-token"),
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
            Some(update)
        )
        .await
        .0,
        StatusCode::CONFLICT
    );
    assert_eq!(
        call(&app, "GET", "/revisions/2", None, None).await.1["definition_yaml"],
        "invalid: ["
    );
    // Updating the head never replaces the original version.
    assert_eq!(call(&app, "GET", "/revisions/1", None, None).await.1, saved);
    assert_eq!(call(&app, "GET", "/revisions/3", None, None).await.0, StatusCode::NOT_FOUND);
    assert_eq!(
        call(
            &app,
            "DELETE",
            "/revisions/1/share",
            Some("owner-token"),
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
            None
        )
        .await
        .0,
        StatusCode::NO_CONTENT
    );
    assert_eq!(
        call(&app, "GET", "/revisions/1", None, None)
            .await
            .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(&app, "GET", "/revisions/1", Some("owner-token"), None)
            .await
            .0,
        StatusCode::NOT_FOUND
    );
    for bot in [None, Some("owner-token")] {
        assert_eq!(call(&app, "POST", "/revisions/1/validate", bot, None).await.0, StatusCode::NOT_FOUND);
    }
    assert_eq!(call(&app, "GET", "/revisions/2", None, None).await.0, StatusCode::OK);
    assert_eq!(
        call(
            &app,
            "GET",
            "/revisions/1/reference",
            Some("owner-token"),
            None
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        call(&app, "DELETE", "", Some("owner-token"), None)
            .await
            .0,
        StatusCode::NO_CONTENT
    );
    assert_eq!(call(&app, "GET", "/revisions/2", None, None).await.0, StatusCode::NOT_FOUND);
    assert_eq!(
        call(&app, "PUT", "", Some("owner-token"), Some(body))
            .await
            .0,
        StatusCode::NOT_FOUND
    );
}
